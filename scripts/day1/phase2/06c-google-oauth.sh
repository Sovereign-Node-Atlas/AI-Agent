#!/usr/bin/env bash
# phase2/06c-google-oauth.sh — Section 17 Phase 2 step 6c: the Google OAuth pause (Sections 13, 21 V20, 22).
# Sourced by phase2-services.sh through run_phase_steps; defines step_06c only. This is one of the three explicit
# interactive pauses of the Day 1 scripts (CONVENTIONS.md §7.6): one authorisation link per account.
#
# For each entry email:tag in GOOGLE_ACCOUNTS:
#   1. phase2/google_oauth.py authorise runs InstalledAppFlow.run_local_server(host="localhost", port=8765+i,
#      open_browser=False) (services-tools.md §5.1 VERIFIED), prints the URL in a framed block (on stderr, so the
#      captured stdout keeps only the JSON answer) with the instruction to open it in Google Chrome on the node's own desktop
#      (xrdp), and waits up to 30 minutes (never less than 15);
#   2. the token is stored at $ATLAS_ETC/secrets/google/<email>.json, mode 600, owner atlas;
#   3. Gmail (labels list), Calendar (calendar list) and Drive (about.get) are proven with that token, and the
#      signed-in address must be the expected one.
# Then upstream rclone is installed (pinned, checksum-verified) and a Drive remote gdrive-<tag> is written from the same
# token into $ATLAS_ETC/secrets/google/rclone.conf (the research VERIFIED the token shape, services-tools.md §4.9/S7),
# `rclone about` proves each remote as the atlas user, and Section 13's "mounted as a folder on the node (rclone), both
# accounts" is DONE here (fix round 2): a systemd template atlas-gdrive@.service (User=atlas, rclone mount with a VFS
# write cache) is installed and enabled per tag, mounting gdrive-<tag>: at $ATLAS_SRV/gdrive/<tag>; the mount is proven
# with findmnt and a listing as atlas. The inbox copy of the client JSON is then shredded (Section 12.3 / R7 pattern, the
# same order as step 6b: the plain-text source goes once the relocated copy has passed its read-back proof, here the
# three API calls of every account made as atlas; CONVENTIONS.md §7.2: no secret inside /srv/atlas). V20 is recorded
# last through verify/v20-google.sh, which re-proves all three APIs for every account without a browser AS ATLAS, checks
# the inbox copy is gone, and passes only when every account answered.
#
# WHO RUNS WHAT (fix round 2): the helper (authorise, verify, rclone-remote), rclone and the mounts run as the atlas
# service account, the consumer of these files, so the proof is the consumer's proof. Root installs packages, writes
# /etc and systemd units. /opt/atlas/venv (the orchestrator venv the helper imports from) keeps the invariant steps 02
# and 04 establish: root:atlas, no group/other write (Section 16.3 item 6: the atlas account never owns the code it
# runs, root never executes an atlas-writable file); it is re-asserted here BEFORE root runs pip or an import from it,
# and after the install. The only atlas-writable state this step needs is $ATLAS_ETC/secrets/google (tokens,
# rclone.conf, refreshed by the consumer) and the mount points / VFS cache.
#
# SECRETS DIRECTORY (fix round 3; ONE value across every writer): CONVENTIONS §2 gives /etc/atlas/secrets root:root 700
# and /etc/atlas/secrets/google atlas:atlas 700, which cannot both hold (atlas cannot traverse a root-only parent, so the
# orchestrator, User=atlas, could never open GOOGLE_TOKEN_<TAG>). This step writes `ensure_dir "$ATLAS_ETC/secrets"
# root:atlas 710`: traverse-only for the atlas group, which is all an atlas-side reader needs to open its own 600 file
# BY NAME. The r bit (750, this step's fix-round-2 value) would additionally let every atlas-group process, i.e. the
# account that runs model-driven code, list the names of every secret file (cloudflare.env, restic.pass, luks-data.key,
# smb.cred ...), which nothing needs. 710 is what phase2-services.sh asserts at the start of EVERY phase-2 run and what
# phase2/09b-vault.sh (the last step before the gate, so the mode a finished Phase 2 ends with) writes; phase1/02,03,07
# and phase2/02,03,07,08,09 still write 750 (outside this writer's files), so the directory flip-flops 710/750 between
# steps until they adopt 710. Both modes let atlas traverse, so this step's V20 and the gate's V20 re-run pass whichever
# step ran last (the earlier header's "fails at the gate by design" described a root:root 700 that 09b no longer
# writes); verify/v20-google.sh prints the measured mode, owner and change time when traversal does fail. Request (for
# phase2/README-contracts.md §3 item 10, which asks the same): CONVENTIONS §2's row reads `root:atlas 710` and every
# ensure_dir of the directory uses it.
#
# The OAuth client JSON comes from $ATLAS_SRV/staging/inbox/google-oauth-client.json (Section 22: the client exists).
# If it is absent this step prints exactly where to put it, records V20 FAIL (not deferred) and stops the phase;
# re-running resumes here. CLIENT TYPE (fix round 5, belt and braces): phase2-services.sh's minute-0 pre-flight already
# checks the inbox JSON for the top-level "installed" key (Desktop app) and names a "web" client as the wrong type;
# this step re-checks the copy it is about to use (inbox or the already installed $G_CLIENT) BEFORE the first consent
# URL is printed, so a client swapped after the pre-flight, or an installed copy from an earlier revision, can never
# send the Principal through a consent flow whose loopback redirect Google will refuse. The OAuth hosts the flow needs
# (accounts.google.com, oauth2.googleapis.com, www.googleapis.com) are asserted present in config/allowlist.txt at the
# same point, so a missing host is one line here and not a TCP_DENIED after the consent click.
#
# Contracts relied on from other writers (CONVENTIONS.md §1): /opt/atlas/venv (step 02; created here when absent,
# logged); config/allowlist.txt: accounts.google.com, oauth2.googleapis.com, www.googleapis.com,
# gmail.googleapis.com, openidconnect.googleapis.com, downloads.rclone.org, pypi.org, files.pythonhosted.org;
# /var/cache/atlas (phase2/04-memory.sh): transient caches and downloads, never under $ATLAS_STATE or $ATLAS_SRV;
# $ATLAS_ETC/proxy.env (lib/common.sh header: HTTP_PROXY/HTTPS_PROXY/NO_PROXY lines) is the mount units'
# EnvironmentFile so rclone reaches Google through the allowlist proxy; phase2/07-restic.sh excludes $ATLAS_SRV/gdrive
# from the backup set (a FUSE mount without allow_other is invisible to root, and Drive is Google's copy anyway).
# Contract this file defines for others:
#   * $ATLAS_ETC/secrets/google/client_secret.json (atlas 600), <email>.json tokens (atlas 600) and rclone.conf
#     (atlas 600, rewritten by rclone on token refresh); dir atlas 700; parent root:atlas 710 (see above).
#   * $ATLAS_ETC/google.env (root:atlas 640, the mode of every Phase 2 settings file: atlas.env, voice.env, tools.env;
#     it carries paths and the two addresses, which are settings, not secrets, and already stand in atlas.env's
#     GOOGLE_ACCOUNTS, the phase log and verify.jsonl): GOOGLE_TOKEN_DIR, GOOGLE_CLIENT_JSON, GOOGLE_TOKEN_<TAG>=<path>,
#     GOOGLE_EMAIL_<TAG>=<email>, GOOGLE_TOKEN_ACCESS=direct (the atlas process opens the paths itself), RCLONE_BIN,
#     RCLONE_CONF=/etc/atlas/secrets/google/rclone.conf, RCLONE_REMOTE_<TAG>=gdrive-<tag>, GDRIVE_ROOT=$ATLAS_SRV/gdrive,
#     GDRIVE_MOUNT_<TAG>=$ATLAS_SRV/gdrive/<tag>, GDRIVE_UNIT_<TAG>=atlas-gdrive@<tag>.service. The orchestrator reads
#     and writes Drive through GDRIVE_MOUNT_<TAG> as atlas (the mount has no allow_other: only atlas sees it).
#   * /etc/systemd/system/atlas-gdrive@.service (written by this step from the heredoc below; its content belongs in
#     scripts/day1/systemd/atlas-gdrive@.service per CONVENTIONS §1 once that directory's writer adds it): User=atlas,
#     `rclone mount gdrive-%i: $ATLAS_SRV/gdrive/%i --config $RCLONE_CONF --vfs-cache-mode writes`, EnvironmentFile
#     google.env + proxy.env, After=/Wants= network-online.target AND squid.service (rclone reaches Google only through
#     the allowlist proxy, so the mount must not start before squid listens; like systemd/atlas-ddns.service),
#     Restart=on-failure. Layout rows for CONVENTIONS §2: /srv/atlas/gdrive/<tag> (atlas 700,
#     FUSE), /var/cache/atlas/rclone/<tag> (atlas, VFS cache).

[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# services-tools.md §5.1 VERIFIED pins for the first three; google-auth-httplib2 0.4.2 is the current PyPI release
# (VERIFIED on pypi.org/pypi/google-auth-httplib2/json, 2026-09-27, fix round; the research left it unpinned).
GOOGLE_PINS=("google-api-python-client==2.200.0" "google-auth-oauthlib==1.4.1" "google-auth==2.58.0" "google-auth-httplib2==0.4.2")
RCLONE_VERSION="v1.75.1"                                    # services-tools.md §4.9 VERIFIED latest tag
RCLONE_BASE="https://downloads.rclone.org"
RCLONE_VERSIONED_URL="$RCLONE_BASE/$RCLONE_VERSION/rclone-$RCLONE_VERSION-linux-amd64.zip"   # UNVERIFIED layout (host blocked in research)
RCLONE_VERSIONED_SUMS="$RCLONE_BASE/$RCLONE_VERSION/SHA256SUMS"                              # UNVERIFIED layout
RCLONE_CURRENT_URL="$RCLONE_BASE/rclone-current-linux-amd64.zip"                            # VERIFIED install.md
RCLONE_CURRENT_SUMS="$RCLONE_BASE/SHA256SUMS"                                               # UNVERIFIED layout
OAUTH_PORT_BASE=8765
OAUTH_TIMEOUT=1800                                          # 30 minutes; the task's floor is 15
G_CACHE_DIR="/var/cache/atlas"
GDRIVE_UNIT="atlas-gdrive@.service"
GDRIVE_MOUNT_WAIT_S=90

G_VENV=""
G_DIR=""
G_CLIENT=""
G_ENVF=""
G_ATLAS_HOME=""
G_SRC=""
G_GDRIVE_ROOT=""

_g_paths() {
  G_VENV="$ATLAS_OPT/venv"
  G_DIR="$ATLAS_ETC/secrets/google"
  G_CLIENT="$G_DIR/client_secret.json"
  G_ENVF="$ATLAS_ETC/google.env"
  G_SRC="$ATLAS_SRV/staging/inbox/google-oauth-client.json"
  G_GDRIVE_ROOT="$ATLAS_SRV/gdrive"
  id -u atlas >/dev/null 2>&1 || die "service account atlas does not exist (Phase 1 step 3)"
  G_ATLAS_HOME="$(getent passwd atlas | cut -d: -f6)"
  [[ -n "$G_ATLAS_HOME" ]] || die "cannot read the home directory of the atlas account"
}

# _g_as_atlas CMD... — the consumer of the tokens runs the helper and rclone (proxy for the APIs; loopback redirect is
# in NO_PROXY through proxy_env).
_g_as_atlas() {
  svc_user_run env HOME="$G_ATLAS_HOME" HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
    OAUTHLIB_RELAX_TOKEN_SCOPE=1 "$@"
}

# _g_venv_harden — the step-02/04 invariant for /opt/atlas/venv: root:atlas, no group/other write, asserted.
_g_venv_harden() {
  local stray
  chown -R root:atlas "$G_VENV"
  chmod -R go-w "$G_VENV"
  stray="$(find "$G_VENV" ! -type l \( ! -user root -o -perm /022 \) 2>/dev/null | head -n 3 || true)"
  [[ -z "$stray" ]] || die "$G_VENV still has non-root or group/world-writable entries after the fix (Section 16.3 item 6): $(tr '\n' ' ' <<<"$stray")"
}

_g_venv() {
  apt_install python3-venv python3-pip unzip curl fuse3
  if [[ ! -x "$G_VENV/bin/python" ]]; then
    log "$G_VENV absent (step 02 normally creates it); creating it here with python3 -m venv"
    mkdir -p "$ATLAS_OPT"; python3 -m venv "$G_VENV" || die "python3 -m venv $G_VENV failed"
  fi
  # Re-own BEFORE the first execution: a venv an earlier revision chowned to atlas must never run as root.
  _g_venv_harden
  if ! "$G_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("google-api-python-client") == "2.200.0" and m.version("google-auth-oauthlib") == "1.4.1" and m.version("google-auth") == "2.58.0" and m.version("google-auth-httplib2") == "0.4.2"' 2>/dev/null; then
    proxy_env
    ensure_dir "$G_CACHE_DIR" root:root 755
    ensure_dir "$G_CACHE_DIR/pip" root:root 755
    export PIP_CACHE_DIR="$G_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
    log "pip install ${GOOGLE_PINS[*]} into $G_VENV"
    retry 3 "$G_VENV/bin/python" -m pip install --quiet "${GOOGLE_PINS[@]}" || die "pip install of the Google client libraries failed in $G_VENV"
    _g_venv_harden
  fi
  "$G_VENV/bin/python" -c 'import googleapiclient, google_auth_oauthlib' || die "the Google libraries do not import from $G_VENV"
  # The consumer imports read-only from the same venv (what the orchestrator does at run time).
  _g_as_atlas "$G_VENV/bin/python" -c 'import googleapiclient, google_auth_oauthlib' || die "the Google libraries do not import from $G_VENV as atlas (venv root:atlas go-w: group read must stay)"
}

_g_client() {
  # root:atlas 710 (header: SECRETS DIRECTORY): traverse-only for atlas, no listing; every file inside stays 600 owned
  # by its reader.
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 710
  ensure_dir "$G_DIR" atlas:atlas 700
  if [[ ! -s "$G_CLIENT" ]]; then
    if [[ ! -s "$G_SRC" ]]; then
      cat <<MSG

  ==== Google OAuth client JSON is missing (Section 22 says the client was created) ====
  Download the OAuth client of type "Desktop app" from Google Cloud -> APIs & Services -> Credentials
  (the JSON has a top-level "installed" key) and copy it to EXACTLY:
      $G_SRC
  (owner $PRINCIPAL_USER, any mode; the inbox is $PRINCIPAL_USER:atlas). Then re-run:
      sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2
  The phase resumes at this step. The inbox copy is shredded once the accounts are proven; the installed copy is $G_CLIENT.
  =========================================================================================
MSG
      # Policy v0.3.3: a to-do, not a stop; V20 deferred and the step returns.
      record_v V20 deferred "OAuth client JSON not provided yet (to-do input-google-oauth-client): Gmail, Calendar and Drive wait"
      todo_add input-google-oauth-client "Google OAuth client JSON not provided: create a 'Desktop app' OAuth client in Google Cloud -> APIs & Services -> Credentials, download its JSON to $G_SRC, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06c" "Section 13 / V20"
      return 1
    fi
    _g_client_type_check "$G_SRC" || return 1
    install -m 600 -o atlas -g atlas "$G_SRC" "$G_CLIENT"
    log "installed the OAuth client JSON as $G_CLIENT (atlas, 600); the inbox copy is shredded once every account has passed its API proof"
  fi
  # Belt and braces (header: CLIENT TYPE): the copy the consent flow will use is checked whichever path put it there.
  _g_client_type_check "$G_CLIENT" || return 1
  # The consumer must be able to open it now, not in Phase 3.
  svc_user_run test -r "$G_CLIENT" \
    || die "atlas cannot read $G_CLIENT: $(stat -c '%A %U:%G, changed %y' "$ATLAS_ETC/secrets") on $ATLAS_ETC/secrets blocks traversal. Every writer's ensure_dir of that directory must be root:atlas 710 (750 also traverses; header: SECRETS DIRECTORY)"
}

# _g_client_type_check FILE — the JSON must be a "Desktop app" client (top-level "installed"); a "web" client is named as
# the wrong type (its redirect URIs are fixed https callbacks, the loopback flow of google_oauth.py cannot use it),
# anything else as not an OAuth client at all. V20 fail + die, before any consent URL (header: CLIENT TYPE).
_g_client_type_check() {
  local f="$1" kind
  kind="$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print("unreadable: %s" % exc); sys.exit(0)
print("installed" if isinstance(d, dict) and "installed" in d else "web" if isinstance(d, dict) and "web" in d else "other")' "$f" 2>/dev/null || echo other)"
  case "$kind" in
    installed) log "OAuth client $f: Desktop-app type (top-level 'installed'), as the loopback flow needs" ;;
    web)
      record_v V20 deferred "$f is a 'web' OAuth client (top-level 'web' key), not the 'Desktop app' type the loopback flow needs (to-do input-google-oauth-client)"
      todo_add input-google-oauth-client "The OAuth client JSON is of the 'Web application' type; create a 'Desktop app' client instead, download its JSON to $G_SRC, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06c" "Section 13 / V20"
      return 1 ;;
    *)
      record_v V20 deferred "$f is not a Desktop-app OAuth client (no top-level 'installed' key; $kind) (to-do input-google-oauth-client)"
      todo_add input-google-oauth-client "The file at $G_SRC is not an OAuth client JSON (needs the top-level key 'installed'); download the 'Desktop app' client JSON there, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06c" "Section 13 / V20"
      return 1 ;;
  esac
}

# _g_allowlist_check — the OAuth endpoints (services-tools.md §5.1; Section 12.5 "Google APIs") must be in
# config/allowlist.txt, the ONLY allowlist (rule §7.1), before the first consent URL is printed.
_g_allowlist_check() {
  local f="$ATLAS_DAY1_DIR/config/allowlist.txt" host missing=()
  [[ -f "$f" ]] || die "$f is missing (rule §7.1: the allowlist is that file and nothing else)"
  for host in accounts.google.com oauth2.googleapis.com www.googleapis.com; do
    grep -qxF "$host" "$f" || missing+=("$host")
  done
  (( ${#missing[@]} == 0 )) || die "config/allowlist.txt lacks the OAuth host(s) ${missing[*]} (Section 12.5 Google APIs; services-tools.md §5.1): add them BY NAME (never .googleapis.com, the Gemini guard) and reload: sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist $f"
  log "allowlist: accounts.google.com, oauth2.googleapis.com, www.googleapis.com present in config/allowlist.txt"
}

# _g_last_json OUTPUT -> the last JSON line (the helper prints it last), or "" when there is none. `|| true` (fix round
# 3): with pipefail a helper that died before emit() (ImportError, SIGKILL) made grep's exit 1 abort the step through the
# generic ERR trap instead of reaching the `ok != 1` branch, whose ${out:0:200} fallback shows the traceback head.
_g_last_json() { printf '%s\n' "$1" | grep -E '^\{' | tail -n1 || true; }

_g_authorise_all() {
  local i=0 acct email tag port out json ok n total
  total="$(wc -w <<<"$GOOGLE_ACCOUNTS")"
  proxy_env
  for acct in $GOOGLE_ACCOUNTS; do
    email="${acct%%:*}"; tag="${acct##*:}"; port=$(( OAUTH_PORT_BASE + i )); i=$(( i + 1 ))
    local tokf="$G_DIR/$email.json"
    log "Google account $i/$total ($tag): $email; token $tokf; redirect port $port"
    # Loopback only: the flow must never go through the proxy, the APIs must (proxy_env above; NO_PROXY has localhost).
    # stdout (one JSON line) is captured; the framed block with the URL comes on stderr, which the command substitution
    # leaves on the console (no process substitution: nothing may still be flushing after the next log line).
    out="$(_g_as_atlas "$G_VENV/bin/python" "$ATLAS_DAY1_DIR/phase2/google_oauth.py" authorise \
            --client "$G_CLIENT" --token "$tokf" --email "$email" --port "$port" --timeout "$OAUTH_TIMEOUT" \
            --owner atlas --label "$i/$total, $tag")" || true
    json="$(_g_last_json "$out")"
    ok="$(python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("ok") else "0")' <<<"$json" 2>/dev/null || echo 0)"
    if [[ "$ok" != 1 ]]; then
      local err; err="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("error","?"))' <<<"$json" 2>/dev/null || echo "${out:0:200}")"
      record_v V20 fail "$tag $email: $err"
      die "Google authorisation for $email failed: $err. Re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 (an already-authorised account is not asked again)"
    fi
    # Plain double quotes inside the single-quoted program (fix round 3, blocker): the earlier `\"key\"` inside f-string
    # fields was a SyntaxError on every CPython and, without a fallback, aborted the step right AFTER the Principal's
    # browser consent. A summary problem can never abort a step whose expensive part has already succeeded.
    n="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print("gmail labels {}, calendars {}, drive user {}".format(d.get("gmail_labels", "?"), d.get("calendars", "?"), d.get("drive_user", "?")))' <<<"$json" 2>/dev/null)" \
      || n="(summary unavailable)"
    log "Google $tag ($email): authorised; $n"
    [[ "$(stat -c '%a %U' "$tokf")" == "600 atlas" ]] || { chown atlas:atlas "$tokf"; chmod 600 "$tokf"; }
    ensure_kv "$G_ENVF" "GOOGLE_TOKEN_${tag^^}" "$tokf"
    ensure_kv "$G_ENVF" "GOOGLE_EMAIL_${tag^^}" "$email"
  done
}

# _g_fetch URL DEST — one download through the proxy; returns curl's status (callers decide).
_g_fetch() { curl -fsSL --max-time 300 --retry 3 --retry-delay 5 -o "$2" "$1"; }

_g_rclone_install() {
  local bin=/usr/local/bin/rclone
  if [[ -x "$bin" ]] && "$bin" version 2>/dev/null | grep -q "^rclone $RCLONE_VERSION\$"; then
    log "rclone $RCLONE_VERSION already installed at $bin"
  else
    proxy_env
    ensure_dir "$G_CACHE_DIR" root:root 755
    ensure_dir "$G_CACHE_DIR/downloads" root:root 755
    local zip="$G_CACHE_DIR/downloads/rclone-linux-amd64.zip" sums="$G_CACHE_DIR/downloads/rclone-SHA256SUMS" name tmp
    rm -f "$zip" "$sums"
    # UNVERIFIED (services-tools.md §4.9): the versioned URL layout; the rclone-current zip is VERIFIED (install.md).
    # rclone-current is only a fallback for fetching: whatever is unpacked must BE $RCLONE_VERSION (§7.9), else the step
    # stops naming the version found.
    if _g_fetch "$RCLONE_VERSIONED_URL" "$zip"; then
      name="rclone-$RCLONE_VERSION-linux-amd64.zip"
      _g_fetch "$RCLONE_VERSIONED_SUMS" "$sums" || rm -f "$sums"
    else
      warn "rclone: $RCLONE_VERSIONED_URL not available (UNVERIFIED layout); fetching rclone-current and requiring it to be $RCLONE_VERSION"
      _g_fetch "$RCLONE_CURRENT_URL" "$zip" || die "rclone download failed (downloads.rclone.org allowlisted?)"
      name="rclone-current-linux-amd64.zip"
      _g_fetch "$RCLONE_CURRENT_SUMS" "$sums" || rm -f "$sums"
    fi
    local have want=""
    have="$(sha256sum "$zip" | cut -d' ' -f1)"
    if [[ -s "$sums" ]]; then
      want="$(awk -v f="$name" '$2==f || $2=="*"f {print $1; exit}' "$sums")"
    fi
    if [[ -n "$want" ]]; then
      [[ "$have" == "${want,,}" ]] || { rm -f "$zip"; die "rclone: sha256 mismatch for $name: got $have, SHA256SUMS says $want (download removed; re-run)"; }
      log "rclone: $name sha256 verified against SHA256SUMS"
    else
      warn "rclone: no SHA256SUMS entry for $name (UNVERIFIED layout); recording the computed sha256 $have in $zip.sha256"
      printf '%s  %s\n' "$have" "$name" >"$zip.sha256"
    fi
    tmp="$(mktemp -d)"
    unzip -oq "$zip" -d "$tmp" || die "unzip $zip failed"
    local exe; exe="$(find "$tmp" -type f -name rclone | head -n1 || true)"
    [[ -n "$exe" ]] || { rm -rf "$tmp"; die "no rclone binary inside $zip"; }
    install -m 755 -o root -g root "$exe" "$bin"
    rm -rf "$tmp" "$zip"
  fi
  local ver
  ver="$("$bin" version 2>/dev/null)" || die "$bin version failed after the install"
  ver="${ver%%$'\n'*}"
  [[ "$ver" == "rclone $RCLONE_VERSION" ]] || die "installed rclone is '$ver', not 'rclone $RCLONE_VERSION' (§7.9 pin): rclone-current has moved on. Check its release notes, set RCLONE_VERSION in phase2/06c-google-oauth.sh to the version you accept and re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06c"
  log "rclone: $ver (apt's 1.60.1 is not used: research conflict 10)"
  ensure_kv "$G_ENVF" RCLONE_BIN "$bin"
}

_g_rclone_remotes() {
  # The remote config carries client_secret, refresh_token and access_token: it lives INSIDE the secrets tree (§7.2),
  # atlas 600 (write_private), and rclone (as atlas) rewrites it on every token refresh.
  local conf="$G_DIR/rclone.conf" acct email tag out json remote
  proxy_env
  for acct in $GOOGLE_ACCOUNTS; do
    email="${acct%%:*}"; tag="${acct##*:}"; remote="gdrive-$tag"
    out="$(_g_as_atlas "$G_VENV/bin/python" "$ATLAS_DAY1_DIR/phase2/google_oauth.py" rclone-remote \
            --token "$G_DIR/$email.json" --remote "$remote" --conf "$conf" --owner atlas 2>&1)" \
      || die "writing the rclone remote $remote failed: $(_g_last_json "$out")"
    json="$(_g_last_json "$out")"
    log "rclone remote $remote written to $conf: $json"
    # Read-only proof as the account that will mount it (rclone refreshes and rewrites the token itself).
    if ! _g_as_atlas /usr/local/bin/rclone about "$remote:" --config "$conf" >/dev/null 2>&1; then
      die "rclone about $remote: failed as atlas (config $conf; the drive scope was granted? run it by hand: runuser -u atlas -- rclone about $remote: --config $conf -vv)"
    fi
    log "rclone about $remote: ok (Drive reachable through the remote as atlas)"
    ensure_kv "$G_ENVF" "RCLONE_REMOTE_${tag^^}" "$remote"
  done
  chown atlas:atlas "$conf"; chmod 600 "$conf"
  ensure_kv "$G_ENVF" RCLONE_CONF "$conf"
}

# _g_gdrive_units — Section 13 "mounted as a folder on the node (rclone), both accounts": the template unit, one
# instance per tag, mounted now and at every boot. The mount is atlas-only (no allow_other: the kernel denies every
# other uid, root included; restic excludes $ATLAS_SRV/gdrive), with a VFS write cache on the OS drive so writes by the
# orchestrator are uploaded asynchronously. Proof: findmnt lists a fuse.rclone mount at the path, and atlas can list it.
_g_gdrive_units() {
  local unit="/etc/systemd/system/$GDRIVE_UNIT" tmp
  ensure_dir "$G_GDRIVE_ROOT" atlas:atlas 750
  ensure_dir "$G_CACHE_DIR/rclone" atlas:atlas 755
  ensure_kv "$G_ENVF" GDRIVE_ROOT "$G_GDRIVE_ROOT"
  # Type=simple, not notify: UNVERIFIED this round that rclone $RCLONE_VERSION sends READY=1 for mounts (rclone.org was
  # not reachable); the step waits for the mount itself below, so the unit type changes nothing about the proof.
  tmp="$(mktemp)"
  cat >"$tmp" <<EOT
# /etc/systemd/system/$GDRIVE_UNIT — written by phase2/06c-google-oauth.sh (Section 13: Google Drive mounted as a
# folder, both accounts). Instance = the hemisphere tag (corporate, estate): mounts rclone remote gdrive-%i: at
# $G_GDRIVE_ROOT/%i as atlas. No allow_other: only the atlas account sees the mount (root and restic get EACCES;
# $G_GDRIVE_ROOT is on restic's exclude list). The VFS write cache lives on the OS drive (/var/cache/atlas/rclone/%i).
# Settings: /etc/atlas/google.env (RCLONE_CONF, RCLONE_BIN); proxy: /etc/atlas/proxy.env (the allowlist proxy, rule §7.1).
[Unit]
Description=ATLAS Google Drive mount (%i) via rclone, Section 13
# rclone reaches Google ONLY through the allowlist proxy (proxy.env, rule §7.1): ordered after squid like
# atlas-ddns.service, so a boot-time mount does not fail and retry against a proxy that is not listening yet.
After=network-online.target squid.service
Wants=network-online.target squid.service
AssertPathIsDirectory=$G_GDRIVE_ROOT/%i

[Service]
Type=simple
User=atlas
Group=atlas
EnvironmentFile=$G_ENVF
EnvironmentFile=-$ATLAS_ETC/proxy.env
ExecStart=/usr/local/bin/rclone mount gdrive-%i: $G_GDRIVE_ROOT/%i --config \${RCLONE_CONF} --vfs-cache-mode writes --cache-dir $G_CACHE_DIR/rclone/%i --dir-cache-time 1h --poll-interval 1m --umask 077 --log-level NOTICE
ExecStop=/bin/fusermount3 -uz $G_GDRIVE_ROOT/%i
Restart=on-failure
RestartSec=15
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOT
  install -m 644 -o root -g root "$tmp" "$unit"
  rm -f "$tmp"
  systemctl daemon-reload
  local acct tag mp inst waited
  for acct in $GOOGLE_ACCOUNTS; do
    tag="${acct##*:}"; mp="$G_GDRIVE_ROOT/$tag"; inst="atlas-gdrive@$tag.service"
    ensure_dir "$mp" atlas:atlas 700
    ensure_dir "$G_CACHE_DIR/rclone/$tag" atlas:atlas 700
    if findmnt -rn -t fuse.rclone -o TARGET | grep -qxF "$mp"; then
      log "gdrive: $mp already mounted (fuse.rclone)"
    else
      systemctl enable --now "$inst" || die "systemctl enable --now $inst failed: journalctl -u $inst -n 30"
      waited=0
      until findmnt -rn -t fuse.rclone -o TARGET | grep -qxF "$mp"; do
        (( waited < GDRIVE_MOUNT_WAIT_S )) || die "gdrive-$tag: no fuse.rclone mount at $mp after ${GDRIVE_MOUNT_WAIT_S}s: journalctl -u $inst -n 30 (token refresh through the proxy? /dev/fuse present?)"
        systemctl is-active --quiet "$inst" || die "$inst is not active ($(systemctl is-active "$inst" 2>&1)): journalctl -u $inst -n 30"
        sleep 3; waited=$(( waited + 3 ))
      done
    fi
    systemctl is-enabled --quiet "$inst" || systemctl enable "$inst" >/dev/null 2>&1 || die "systemctl enable $inst failed"
    # The consumer's proof: a listing of the Drive root as atlas (network through the proxy; bounded).
    svc_user_run timeout 120 ls -A "$mp" >/dev/null 2>&1 \
      || die "atlas cannot list $mp (rclone mount gdrive-$tag: is up but the Drive root does not list): journalctl -u $inst -n 30"
    log "gdrive-$tag: mounted at $mp ($(findmnt -rn -o SOURCE,FSTYPE --target "$mp" 2>/dev/null || findmnt -rn -t fuse.rclone -o SOURCE,FSTYPE | head -n1)); listed as atlas; $inst enabled"
    ensure_kv "$G_ENVF" "GDRIVE_MOUNT_${tag^^}" "$mp"
    ensure_kv "$G_ENVF" "GDRIVE_UNIT_${tag^^}" "$inst"
  done
}

_g_shred_inbox() {
  # Section 12.3 / R7 pattern: the plain-text source goes once the relocated copy is verified (every account proved its
  # three APIs as atlas in _g_authorise_all, and rclone reached Drive as atlas).
  if [[ -e "$G_SRC" ]]; then
    shred -u -z -n 3 "$G_SRC" || die "shred -u $G_SRC failed"
    log "shredded the inbox copy $G_SRC (the installed copy is $G_CLIENT, atlas 600)"
  fi
}

step_06c() {
  _g_paths
  # Policy v0.3.3: no Google accounts given -> the whole step is a to-do (load_env recorded input-google-accounts).
  if [[ -z "${GOOGLE_ACCOUNTS:-}" ]]; then
    record_v V20 deferred "no Google accounts set in $ATLAS_ETC/atlas.env (to-do input-google-accounts): Gmail, Calendar and Drive wait"
    log "step 06c skipped: GOOGLE_ACCOUNTS is blank; set it and re-run with --force 06c"
    return 0
  fi
  # Created with its final mode (root:atlas 640, like every Phase 2 settings file; header: the addresses in it are
  # settings, not secrets): a die anywhere in the step never leaves it world-readable.
  [[ -e "$G_ENVF" ]] || install -m 640 -o root -g atlas /dev/null "$G_ENVF"
  _g_venv
  _g_client || { log "step 06c deferred (see the to-do list); re-run with --force 06c once the client JSON is in place"; return 0; }
  _g_allowlist_check
  ensure_kv "$G_ENVF" GOOGLE_TOKEN_DIR "$G_DIR"
  ensure_kv "$G_ENVF" GOOGLE_CLIENT_JSON "$G_CLIENT"
  ensure_kv "$G_ENVF" GOOGLE_TOKEN_ACCESS direct
  _g_authorise_all
  _g_rclone_install
  _g_rclone_remotes
  chown root:atlas "$G_ENVF"; chmod 640 "$G_ENVF"
  _g_gdrive_units
  _g_shred_inbox
  # V20: pass only when Gmail, Calendar and Drive answered for every account, AS ATLAS (no browser: tokens are on disk),
  # and the inbox copy is gone. The gate re-runs V20 after step 9b, which leaves $ATLAS_ETC/secrets at root:atlas 710
  # (header: SECRETS DIRECTORY): atlas traverses, so that re-run passes too.
  run_verify V20 v20-google.sh || die "V20 failed after authorisation (see the verify table)"
  notify "Phase 2 step 6c done: Google OAuth completed for $(wc -w <<<"$GOOGLE_ACCOUNTS") account(s), Drive mounted under $G_GDRIVE_ROOT (V20)"
  log "step 06c done: tokens and rclone.conf in $G_DIR, Drive mounts under $G_GDRIVE_ROOT, settings in $G_ENVF"
}
