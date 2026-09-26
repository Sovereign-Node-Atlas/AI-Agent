#!/usr/bin/env bash
# phase2/06b-cloudflare-token.sh — Section 17 Phase 2 step 6b: the Cloudflare token relocation (Section 12.3, R7, V23).
# Sourced by phase2-services.sh through run_phase_steps; defines step_06b only.
#
# Phase 1 step 7 (phase1/07-remote.sh) already READ the token from $CLOUDFLARE_TXT and wrote
# $ATLAS_ETC/secrets/cloudflare.env (CF_API_TOKEN, CF_ZONE_NAME, CF_ZONE_ID, CF_RECORD_NAME) for the ddns updater,
# leaving CLOUDFLARE.txt in place. This step finalises R7, in the order rule §7.2 fixes (delete only after the new
# file is written AND a read-back test of the API succeeds):
#   1. the env file: mode 600, owner atlas-ddns:atlas-ddns, outside $ATLAS_SRV, outside any git worktree, outside
#      restic's include set ($ATLAS_ETC/restic.include, written by step 7: asserted when present, re-checked by V23);
#   2. the token proves itself with read-only calls: GET /user/tokens/verify (status active: a scoped API token,
#      never the Global API Key, which is not a Bearer credential at all) and GET the zone (falls back to the
#      DNS-record read when the token lacks Zone:Zone:Read, adjudicated conflict 3);
#   3. CF_ZONE_ID resolved once with GET /zones when the token allows, otherwise the Principal is asked once and the
#      id is stored (adjudicated conflict 3);
#   4. `shred -u $CLOUDFLARE_TXT`;
#   5. V23 recorded through verify/v23-cloudflare-token.sh, which re-checks all of the above and that CLOUDFLARE.txt
#      no longer exists anywhere under the Principal's home.
#
# Facts from platform.md item 10 (VERIFIED from the OpenAPI schema: Bearer auth, GET /zones needs Zone Zone Read,
# dns_records read is part of DNS edit); /user/tokens/verify is from memory (UNVERIFIED) and treated as such: a
# non-JSON answer fails loudly instead of being ignored, because this step is the read-back test rule §7.2 requires.
# Contract this file relies on: $ATLAS_ETC/restic.include is a plain list of paths, one per line, written by step 7.

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
  local inc="$ATLAS_ETC/restic.include"
  if [[ -s "$inc" ]]; then
    local line
    while IFS= read -r line; do
      line="${line%%#*}"; line="$(tr -d '[:space:]' <<<"$line")"
      [[ -n "$line" ]] || continue
      case "$real" in
        "$line"|"$line"/*) die "restic's include set ($inc) covers $real via '$line'; the token must stay outside every backup (Section 12.3)" ;;
      esac
    done <"$inc"
    log "restic include set ($inc) does not cover $real"
  else
    log "$inc not written yet (step 7 runs after this step); V23 re-checks it at the Phase 2 gate"
  fi
}

# _cf_get PATH -> body on stdout, HTTP code in CF_HTTP; never dies (callers decide).
CF_HTTP=""
_cf_get() {
  local body
  body="$(mktemp)"
  CF_HTTP="$(curl -sS --max-time 30 -o "$body" -w '%{http_code}' -H "Authorization: Bearer $CF_TOKEN" "$CF_API$1" 2>/dev/null || echo 000)"
  cat "$body"; rm -f "$body"
}

_cf_verify_token() {
  proxy_env
  local ans
  ans="$(_cf_get /user/tokens/verify)"
  # UNVERIFIED endpoint name (platform.md item 10): a JSON envelope is required; anything else is a loud stop.
  jq -e '.success == true and .result.status == "active"' <<<"$ans" >/dev/null 2>&1 \
    || die "GET /user/tokens/verify did not report an active token (HTTP $CF_HTTP): ${ans:0:200}. The Global API Key is not a Bearer credential and answers here with an error; a revoked or expired scoped token does too."
  local tid; tid="$(jq -r '.result.id // empty' <<<"$ans")"
  log "cloudflare: scoped API token verified active (token id ${tid:0:8}…)"
}

_cf_zone_id() {
  if [[ -z "$CF_ZONE_ID" ]]; then
    local ans
    ans="$(_cf_get "/zones?name=$CF_ZONE_NAME&status=active")"
    CF_ZONE_ID="$(jq -r '.result[0].id // empty' <<<"$ans" 2>/dev/null || true)"
    if [[ -n "$CF_ZONE_ID" ]]; then
      log "zone id for $CF_ZONE_NAME resolved with GET /zones (the token carries Zone:Zone:Read)"
    else
      # Adjudicated conflict 3: a Zone:DNS:Edit-only token cannot list zones (HTTP $CF_HTTP). Ask once, store it.
      echo
      echo "  The Cloudflare token cannot list zones (Zone:DNS:Edit only; GET /zones answered HTTP $CF_HTTP)."
      echo "  Paste the Zone ID of $CF_ZONE_NAME: Cloudflare dashboard -> $CF_ZONE_NAME -> Overview -> API -> Zone ID (32 hex characters)."
      [[ -r /dev/tty ]] || die "CF_ZONE_ID is blank and no terminal is attached; set CF_ZONE_ID=<zone id> in $CF_ENVF and re-run this step"
      read -r -t 900 -p "  Zone ID: " CF_ZONE_ID </dev/tty || die "no Zone ID entered within 15 minutes; set CF_ZONE_ID in $CF_ENVF and re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06b"
      CF_ZONE_ID="$(tr -d '[:space:]' <<<"$CF_ZONE_ID")"
    fi
  fi
  [[ "$CF_ZONE_ID" =~ ^[0-9a-f]{32}$ ]] || die "'$CF_ZONE_ID' is not a 32-hex Cloudflare zone id; fix CF_ZONE_ID in $CF_ENVF and re-run this step"
}

_cf_read_back() {
  # Read-only proof against the zone itself (rule §7.2's read-back test before the plain-text file is deleted).
  local ans name
  ans="$(_cf_get "/zones/$CF_ZONE_ID")"
  if jq -e '.success == true' <<<"$ans" >/dev/null 2>&1; then
    name="$(jq -r '.result.name // empty' <<<"$ans")"
    [[ "$name" == "$CF_ZONE_NAME" ]] || die "GET /zones/$CF_ZONE_ID is zone '$name', not $CF_ZONE_NAME: CF_ZONE_ID in $CF_ENVF is wrong"
    log "cloudflare: GET /zones/$CF_ZONE_ID -> $name (read-only call ok)"
    return 0
  fi
  case "$CF_HTTP" in
    403)
      # Expected for a Zone:DNS:Edit-only token (no Zone:Zone:Read): the DNS-record read is in the DNS scope.
      ans="$(_cf_get "/zones/$CF_ZONE_ID/dns_records?type=A&name=$VPN_HOST")"
      jq -e '.success == true' <<<"$ans" >/dev/null 2>&1 \
        || die "GET /zones/$CF_ZONE_ID (403, no Zone:Read) and GET dns_records both failed (HTTP $CF_HTTP): ${ans:0:200}. The token does not carry Zone:DNS:Edit on $CF_ZONE_NAME or the zone id is wrong."
      local n; n="$(jq -r '.result | length' <<<"$ans")"
      log "cloudflare: token has no Zone:Zone:Read (GET /zones/{id} 403, fine for Zone:DNS:Edit); dns_records read ok ($n A record(s) named $VPN_HOST)"
      [[ "$n" != 0 ]] || warn "no A record named $VPN_HOST exists yet: create it once in the dashboard (DNS only, grey cloud); the ddns updater needs it"
      ;;
    *) die "GET /zones/$CF_ZONE_ID failed (HTTP $CF_HTTP): ${ans:0:200}" ;;
  esac
}

_cf_write_env() {
  local record="$VPN_HOST"
  {
    echo "# Cloudflare dynamic DNS (Section 12.3; V23). Written by Phase 1 step 7, verified and finalised by Phase 2 step 6b."
    echo "CF_API_TOKEN=$CF_TOKEN"
    echo "CF_ZONE_NAME=$CF_ZONE_NAME"
    echo "CF_ZONE_ID=$CF_ZONE_ID"
    echo "CF_RECORD_NAME=$record"
  } | install -m 600 -o atlas-ddns -g atlas-ddns /dev/stdin "$CF_ENVF"
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
  _cf_shred
  run_verify V23 v23-cloudflare-token.sh "$PRINCIPAL_USER" "$CLOUDFLARE_TXT" \
    || die "V23 failed after the relocation (see the verify table)"
  log "step 06b done: Cloudflare token relocated, $CLOUDFLARE_TXT shredded, V23 recorded"
}
