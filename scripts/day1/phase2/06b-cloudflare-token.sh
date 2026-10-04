#!/usr/bin/env bash
# phase2/06b-cloudflare-token.sh — Section 17 Phase 2 step 6b: the Cloudflare token relocation (Section 12.3, R7, V23).
# Sourced by phase2-services.sh through run_phase_steps; defines step_06b only. Runs unattended (CONVENTIONS.md §7.6
# names the only three interactive pauses; this step is not one of them).
#
# Phase 1 step 7 (phase1/07-remote.sh) already READ the token from $CLOUDFLARE_TXT and wrote
# $ATLAS_ETC/secrets/cloudflare.env (CF_API_TOKEN, CF_ZONE_NAME, CF_ZONE_ID, CF_RECORD_NAME) for the ddns updater,
# leaving CLOUDFLARE.txt in place. This step finalises R7, in the order rule §7.2 fixes (delete only after the new
# file is written AND a read-back test of the API succeeds):
#   1. the env file: mode 600, owner atlas-ddns:atlas-ddns, outside $ATLAS_SRV, outside any git worktree, outside
#      restic's include set ($ATLAS_ETC/restic-include.txt minus $ATLAS_ETC/restic-exclude.txt, both written by step 7
#      as phase2/07-restic.sh's RESTIC_INCLUDE/RESTIC_EXCLUDE: asserted when present, re-checked by V23 at the gate,
#      where a missing include file after step 7 is a fail);
#   2. the token proves itself with read-only calls: GET /user/tokens/verify (status active: a scoped API token,
#      never the Global API Key, which is not a Bearer credential at all) and GET the zone (falls back to the
#      DNS-record read when the token lacks Zone:Zone:Read, adjudicated conflict 3);
#   3. CF_ZONE_ID resolved once with GET /zones when the token allows; otherwise, exactly as phase1/07-remote.sh does,
#      a 32-hex id written beside the token in $CLOUDFLARE_TXT is used; otherwise the step STOPS with the one-line
#      manual fix (set CF_ZONE_ID in the env file, re-run). No prompt (adjudicated conflict 3 says "stores it", §7.6
#      says everything but the three pauses runs unattended);
#   4. the FINALISED file is read back from disk and GET /user/tokens/verify is repeated with the token read from it
#      (fix round 2: §7.2 words the order as "after the new file is written and a read-back test of the API succeeds";
#      the first verification read the pre-finalisation file, so it alone did not meet the literal order);
#   5. `shred -u $CLOUDFLARE_TXT`;
#   6. V23 recorded through verify/v23-cloudflare-token.sh, which re-checks all of the above and that CLOUDFLARE.txt
#      no longer exists anywhere under the Principal's home.
#
# Facts from platform.md item 10 (VERIFIED from the OpenAPI schema: Bearer auth, GET /zones needs Zone Zone Read,
# dns_records read is part of DNS edit); /user/tokens/verify is from memory (UNVERIFIED) and treated as such: a
# non-JSON answer fails loudly instead of being ignored, because this step is the read-back test rule §7.2 requires.
# RULE for every caller of the Cloudflare API (this step, verify/v23, phase1/cloudflare-ddns.sh, Phase 1): the bearer
# token is never an argv element (`-H "Authorization: Bearer ..."` is readable in /proc/<pid>/cmdline by every local
# account, and the atlas account runs model-driven code); curl reads it from a config on stdin (`-K -`) or from a
# mode-600 file (`-H @file`). Zone ids and token values are never echoed into log lines or die messages either
# (the phase log is root 755 and restic backs it up).
# Contract this file relies on: $ATLAS_ETC/restic-include.txt and restic-exclude.txt are plain lists of paths, one per
# line, '#' comments allowed (phase2/07-restic.sh).

[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

CF_API="https://api.cloudflare.com/client/v4"
CF_ENVF=""
CF_TOKEN=""
CF_ZONE_ID=""
CF_ZONE_NAME=""

_cf_read_env() {
  CF_ENVF="$ATLAS_ETC/secrets/cloudflare.env"
  [[ -s "$CF_ENVF" ]] || die "$CF_ENVF is missing: Phase 1 step 7 writes it from $CLOUDFLARE_TXT (re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1 --force 07)"
  CF_TOKEN="$(awk -F= '$1=="CF_API_TOKEN" {print $2; exit}' "$CF_ENVF")"
  CF_ZONE_ID="$(awk -F= '$1=="CF_ZONE_ID" {print $2; exit}' "$CF_ENVF")"
  CF_ZONE_NAME="$(awk -F= '$1=="CF_ZONE_NAME" {print $2; exit}' "$CF_ENVF")"
  [[ -n "$CF_TOKEN" ]] || die "$CF_ENVF has no CF_API_TOKEN"
  [[ -n "$CF_ZONE_NAME" ]] || CF_ZONE_NAME="$DOMAIN"
  # Section 12.3: a scoped API token (40 chars of [A-Za-z0-9_-]), never the Global API Key (37 hex chars).
  if [[ "$CF_TOKEN" =~ ^[0-9a-f]{37}$ ]]; then
    die "CF_API_TOKEN in $CF_ENVF looks like the Global API Key (37 hex characters); Section 12.3 requires a scoped API token with Zone:DNS:Edit on $CF_ZONE_NAME. Create one, put it in $CLOUDFLARE_TXT, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1 --force 07"
  fi
  [[ "$CF_TOKEN" =~ ^[A-Za-z0-9_-]{40}$ ]] || die "CF_API_TOKEN in $CF_ENVF is not a 40-character Cloudflare API token"
}

# _cf_path_listed FILE PATH — 0 when a non-comment line of FILE equals PATH or is one of its parent directories.
_cf_path_listed() {
  local file="$1" path="$2" line
  [[ -s "$file" ]] || return 1
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(tr -d '[:space:]' <<<"$line")"
    [[ -n "$line" ]] || continue
    case "$path" in
      "$line"|"$line"/*) CF_MATCHED_LINE="$line"; return 0 ;;
    esac
  done <"$file"
  return 1
}
CF_MATCHED_LINE=""

_cf_check_file() {
  id -u atlas-ddns >/dev/null 2>&1 || die "service account atlas-ddns does not exist (Phase 1 step 7)"
  local mode owner group real
  mode="$(stat -c '%a' "$CF_ENVF")"; owner="$(stat -c '%U' "$CF_ENVF")"; group="$(stat -c '%G' "$CF_ENVF")"
  if [[ "$mode" != 600 || "$owner" != atlas-ddns || "$group" != atlas-ddns ]]; then
    warn "$CF_ENVF is $mode $owner:$group; correcting to 600 atlas-ddns:atlas-ddns (CONVENTIONS.md §2)"
    chown atlas-ddns:atlas-ddns "$CF_ENVF"; chmod 600 "$CF_ENVF"
  fi
  real="$(readlink -f "$CF_ENVF")"
  [[ "$real" != "$ATLAS_SRV"/* ]] || die "$CF_ENVF resolves inside $ATLAS_SRV ($real); secrets never live on the data volume (Section 12.3)"
  if command -v git >/dev/null 2>&1 && git -C "$(dirname "$real")" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "$real is inside a git worktree; secrets are never in a git-tracked path (Section 12.3)"
  fi
  # restic (phase2/07-restic.sh): --files-from restic-include.txt, --exclude-file restic-exclude.txt. A path under an
  # include line is acceptable ONLY when an exclude line covers it too ($ATLAS_ETC/secrets is on the exclude list).
  local inc="$ATLAS_ETC/restic-include.txt" exc="$ATLAS_ETC/restic-exclude.txt"
  if [[ -s "$inc" ]]; then
    if _cf_path_listed "$inc" "$real"; then
      local via="$CF_MATCHED_LINE"
      if _cf_path_listed "$exc" "$real"; then
        log "restic: $real is under include line '$via' but excluded by '$CF_MATCHED_LINE' ($exc): outside the backup set"
      else
        die "restic's include set ($inc) covers $real via '$via' and $exc does not exclude it; the token must stay outside every backup (Section 12.3)"
      fi
    else
      log "restic include set ($inc) does not cover $real"
    fi
  else
    log "$inc not written yet (step 7 runs after this step); V23 re-checks it at the Phase 2 gate and fails there if it is still absent"
  fi
}

# _cf_get PATH -> body in CF_BODY, HTTP code in CF_HTTP (always three digits); never dies (callers decide). Results
# travel through globals, not stdout: a `$(...)` caller would run the function in a subshell and lose CF_HTTP.
CF_HTTP=""
CF_BODY=""
_cf_get() {
  local body
  body="$(mktemp)"
  # The header travels in a curl config read from stdin: never on argv (rule in the header).
  CF_HTTP="$(curl -sS --max-time 30 -K - -o "$body" -w '%{http_code}' "$CF_API$1" 2>/dev/null <<<"header = \"Authorization: Bearer $CF_TOKEN\"")" \
    || CF_HTTP="${CF_HTTP:-000}"
  [[ "$CF_HTTP" =~ ^[0-9]{3}$ ]] || CF_HTTP=000
  CF_BODY="$(cat "$body")"; rm -f "$body"
}

_cf_verify_token() {
  proxy_env
  local ans
  _cf_get /user/tokens/verify; ans="$CF_BODY"
  # UNVERIFIED endpoint name (platform.md item 10): a JSON envelope is required; anything else is a loud stop.
  jq -e '.success == true and .result.status == "active"' <<<"$ans" >/dev/null 2>&1 \
    || die "GET /user/tokens/verify did not report an active token (HTTP $CF_HTTP): ${ans:0:200}. The Global API Key is not a Bearer credential and answers here with an error; a revoked or expired scoped token does too."
  local tid; tid="$(jq -r '.result.id // empty' <<<"$ans")"
  log "cloudflare: scoped API token verified active (token id ${tid:0:8}…)"
}

_cf_zone_id() {
  if [[ -z "$CF_ZONE_ID" ]]; then
    local ans
    _cf_get "/zones?name=$CF_ZONE_NAME&status=active"; ans="$CF_BODY"
    CF_ZONE_ID="$(jq -r '.result[0].id // empty' <<<"$ans" 2>/dev/null || true)"
    if [[ -n "$CF_ZONE_ID" ]]; then
      log "zone id for $CF_ZONE_NAME resolved with GET /zones (the token carries Zone:Zone:Read)"
    else
      # Adjudicated conflict 3: a Zone:DNS:Edit-only token cannot list zones (HTTP $CF_HTTP). Same lookup as
      # phase1/07-remote.sh: a "Zone ID: <32 hex>" line beside the token in CLOUDFLARE.txt. No prompt (§7.6).
      local http="$CF_HTTP"
      if [[ -s "$CLOUDFLARE_TXT" ]]; then
        CF_ZONE_ID="$(grep -oiE 'zone[ _-]?id[^0-9a-f]*[0-9a-f]{32}' "$CLOUDFLARE_TXT" 2>/dev/null | grep -oE '[0-9a-f]{32}' | head -n1 || true)"
        [[ -n "$CF_ZONE_ID" ]] && log "zone id found beside the token in $CLOUDFLARE_TXT (GET /zones answered HTTP $http: Zone:DNS:Edit only)"
      fi
      [[ -n "$CF_ZONE_ID" ]] || die "CF_ZONE_ID is blank: the token cannot list zones (GET /zones answered HTTP $http; Zone:DNS:Edit only, adjudicated conflict 3) and no 'Zone ID: <32 hex>' line sits beside the token in $CLOUDFLARE_TXT. Manual fix (once): set CF_ZONE_ID=<32-hex id> in $CF_ENVF (Cloudflare dashboard -> $CF_ZONE_NAME -> Overview -> API -> Zone ID), then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06b"
    fi
  fi
  # The value itself is never printed: a slip (the token pasted as the zone id) must not reach the log or the backup.
  [[ "$CF_ZONE_ID" =~ ^[0-9a-f]{32}$ ]] || die "CF_ZONE_ID is not a 32-hex Cloudflare zone id (value not shown); fix CF_ZONE_ID in $CF_ENVF and re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06b"
}

_cf_read_back() {
  # Read-only proof against the zone itself (rule §7.2's read-back test before the plain-text file is deleted).
  local ans name
  _cf_get "/zones/$CF_ZONE_ID"; ans="$CF_BODY"
  if jq -e '.success == true' <<<"$ans" >/dev/null 2>&1; then
    name="$(jq -r '.result.name // empty' <<<"$ans")"
    [[ "$name" == "$CF_ZONE_NAME" ]] || die "GET /zones/<id> is zone '$name', not $CF_ZONE_NAME: CF_ZONE_ID in $CF_ENVF is wrong"
    log "cloudflare: GET /zones/<id> -> $name (read-only call ok)"
    return 0
  fi
  case "$CF_HTTP" in
    403)
      # Expected for a Zone:DNS:Edit-only token (no Zone:Zone:Read): the DNS-record read is in the DNS scope.
      _cf_get "/zones/$CF_ZONE_ID/dns_records?type=A&name=$VPN_HOST"; ans="$CF_BODY"
      jq -e '.success == true' <<<"$ans" >/dev/null 2>&1 \
        || die "GET /zones/<id> (403, no Zone:Read) and GET dns_records both failed (HTTP $CF_HTTP): ${ans:0:200}. The token does not carry Zone:DNS:Edit on $CF_ZONE_NAME or the zone id is wrong."
      local n; n="$(jq -r '.result | length' <<<"$ans")"
      log "cloudflare: token has no Zone:Zone:Read (GET /zones/{id} 403, fine for Zone:DNS:Edit); dns_records read ok ($n A record(s) named $VPN_HOST)"
      [[ "$n" != 0 ]] || warn "no A record named $VPN_HOST exists yet: the ddns updater creates it (DNS only, grey cloud) on its next run; check journalctl -u atlas-ddns"
      ;;
    *) die "GET /zones/<id> failed (HTTP $CF_HTTP): ${ans:0:200}" ;;
  esac
}

_cf_write_env() {
  local record="$VPN_HOST" tmp
  # Through a 0600 temp file, never `... | install /dev/stdin DEST` (fix round 3, blocker): Ubuntu 26.04's /usr/bin/install
  # is rust-coreutils 0.8.0, which canonicalises the SOURCE whenever DEST already exists (phase1/04-system.sh,
  # phase1_write_file: VERIFIED packages.ubuntu.com/resolute), and $CF_ENVF always exists here (Phase 1 step 7 wrote it),
  # so the pipe form died with ENOENT on every run before the second read-back, the shred and V23. mktemp creates the
  # file 0600, so the token never sits readable by anyone else, and install(1) copies a regular file with both GNU and
  # uutils coreutils.
  tmp="$(mktemp)" || die "_cf_write_env: mktemp failed"
  {
    echo "# Cloudflare dynamic DNS (Section 12.3; V23). Written by Phase 1 step 7, verified and finalised by Phase 2 step 6b."
    echo "CF_API_TOKEN=$CF_TOKEN"
    echo "CF_ZONE_NAME=$CF_ZONE_NAME"
    echo "CF_ZONE_ID=$CF_ZONE_ID"
    echo "CF_RECORD_NAME=$record"
  } >"$tmp" || { rm -f "$tmp"; die "_cf_write_env: could not write the temp copy of $CF_ENVF"; }
  install -m 600 -o atlas-ddns -g atlas-ddns "$tmp" "$CF_ENVF" || { rm -f "$tmp"; die "_cf_write_env: install $CF_ENVF failed"; }
  rm -f "$tmp"
  [[ "$(stat -c '%a %U:%G' "$CF_ENVF")" == "600 atlas-ddns:atlas-ddns" ]] || die "$CF_ENVF is $(stat -c '%a %U:%G' "$CF_ENVF") after the write, not 600 atlas-ddns:atlas-ddns"
  log "finalised $CF_ENVF (600 atlas-ddns:atlas-ddns, zone id set)"
  # The updater must work from the finalised file: one forced run through its own unit (its failure is not fatal
  # here, V5/Phase 1 already judged the DNS side; the journal says why).
  if systemctl cat atlas-ddns.service >/dev/null 2>&1; then
    if systemctl start atlas-ddns.service; then
      log "atlas-ddns.service ran with the finalised token file"
    else
      warn "atlas-ddns.service failed with the finalised file: journalctl -u atlas-ddns -n 20"
    fi
  fi
}

_cf_shred() {
  if [[ -e "$CLOUDFLARE_TXT" ]]; then
    shred -u -z -n 3 "$CLOUDFLARE_TXT" || die "shred -u $CLOUDFLARE_TXT failed"
    log "shredded $CLOUDFLARE_TXT (R7)"
  else
    log "$CLOUDFLARE_TXT already absent"
  fi
}

step_06b() {
  apt_install jq curl
  _cf_read_env
  _cf_check_file
  _cf_verify_token
  _cf_zone_id
  _cf_read_back
  _cf_write_env
  # §7.2 order, literally: the read-back test of the API runs against the token read from the file just written, and
  # only then does the plain-text source go.
  _cf_read_env
  _cf_verify_token
  _cf_shred
  run_verify V23 v23-cloudflare-token.sh "$PRINCIPAL_USER" "$CLOUDFLARE_TXT" \
    || die "V23 failed after the relocation (see the verify table)"
  log "step 06b done: Cloudflare token relocated, $CLOUDFLARE_TXT shredded, V23 recorded"
}
