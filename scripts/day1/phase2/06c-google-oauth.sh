#!/usr/bin/env bash
# phase2/06c-google-oauth.sh — Section 17 Phase 2 step 6c: the Google OAuth pause (Sections 13, 21 V20, 22).
# Sourced by phase2-services.sh through run_phase_steps; defines step_06c only. This is one of the three explicit
# interactive pauses of the Day 1 scripts (CONVENTIONS.md §7.6): one authorisation link per account.
#
# For each entry email:tag in GOOGLE_ACCOUNTS:
#   1. phase2/google_oauth.py authorise runs InstalledAppFlow.run_local_server(host="localhost", port=8765+i,
#      open_browser=False) (services-tools.md §5.1 VERIFIED), prints the URL in a framed block (on stderr, so the
#      captured stdout keeps only the JSON answer) with the instruction to open it in Firefox on the node's own desktop
#      (xrdp), and waits up to 30 minutes (never less than 15);
#   2. the token is stored at $ATLAS_ETC/secrets/google/<email>.json, mode 600, owner atlas;
#   3. Gmail (labels list), Calendar (calendar list) and Drive (about.get) are proven with that token, and the
#      signed-in address must be the expected one.
# Then upstream rclone is installed (pinned, checksum-verified) and a Drive remote gdrive-<tag> is written from the same
# token into $ATLAS_ETC/secrets/google/rclone.conf (the research VERIFIED the token shape, services-tools.md §4.9/S7),
# and `rclone about` proves each remote as the atlas user. The inbox copy of the client JSON is then shredded (Section
# 12.3 / R7 pattern, the same order as step 6b: the plain-text source goes once the relocated copy has passed its
# read-back proof, here the three API calls of every account made as atlas; CONVENTIONS.md §7.2: no secret inside
# /srv/atlas). V20 is recorded last through verify/v20-google.sh, which re-proves all three APIs for every account
# without a browser AS ATLAS, checks the inbox copy is gone, and passes only when every account answered.
#
# WHO RUNS WHAT (fix round): the helper (authorise, verify, rclone-remote) and rclone run as the atlas service account,
# the consumer of these files, so the proof is the consumer's proof. Root installs packages and writes /etc.
#
# SECRETS DIRECTORY TRAVERSAL (fix round; contract every writer that touches $ATLAS_ETC/secrets must honour):
#   CONVENTIONS §2 gives /etc/atlas/secrets root:root 700 and /etc/atlas/secrets/google atlas:atlas 700. The second is
#   unreachable under the first: atlas cannot traverse a root-only parent, so the orchestrator (User=atlas) could never
#   open GOOGLE_TOKEN_<TAG>. phase2-services.sh records the agreed remedy ("an atlas-side reader ... the agreed change is
#   root:atlas on the directory in every writer"). This step sets $ATLAS_ETC/secrets to root:atlas 710 (traverse only:
#   no listing, no world access; every file stays 600 owned by its one reader) and proves as atlas that the client JSON
#   is readable. Every other writer's `ensure_dir "$ATLAS_ETC/secrets" root:root 700` (phase1/02,03,07; phase2-services,
#   phase2/03,07,09,09b) must become root:atlas 710, else steps 7/9/9b undo this and V20 (which runs the proof as atlas
#   at the gate) fails with the exact message; CONVENTIONS §2 should read root:atlas 710 for that row.
#
# The OAuth client JSON comes from $ATLAS_SRV/staging/inbox/google-oauth-client.json (Section 22: the client exists).
# If it is absent this step prints exactly where to put it, records V20 FAIL (not deferred) and stops the phase;
# re-running resumes here.
#
# Contracts relied on from other writers (CONVENTIONS.md §1): /opt/atlas/venv (step 02; created here when absent,
# logged); config/allowlist.txt: accounts.google.com, oauth2.googleapis.com, www.googleapis.com,
# gmail.googleapis.com, openidconnect.googleapis.com, downloads.rclone.org, pypi.org, files.pythonhosted.org;
# /var/cache/atlas (phase2/04-memory.sh): transient caches and downloads, never under $ATLAS_STATE or $ATLAS_SRV.
# Contract this file defines for others:
#   * $ATLAS_ETC/secrets/google/client_secret.json (atlas 600), <email>.json tokens (atlas 600) and rclone.conf
#     (atlas 600, rewritten by rclone on token refresh); dir atlas 700; parent root:atlas 710 (see above).
#   * $ATLAS_ETC/google.env (root:atlas 640): GOOGLE_TOKEN_DIR, GOOGLE_CLIENT_JSON, GOOGLE_TOKEN_<TAG>=<path>,
#     GOOGLE_EMAIL_<TAG>=<email>, GOOGLE_TOKEN_ACCESS=direct (the atlas process opens the paths itself), RCLONE_BIN,
#     RCLONE_CONF=/etc/atlas/secrets/google/rclone.conf, RCLONE_REMOTE_<TAG>=gdrive-<tag>. The Drive mount units
#     (Section 13, "mounted as a folder") are the orchestrator/integration writer's: they run as atlas with
#     RCLONE_CONFIG=$RCLONE_CONF (or `--config`); the remotes are ready for them.

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

G_VENV=""
G_DIR=""
G_CLIENT=""
G_ENVF=""
G_ATLAS_HOME=""
G_SRC=""

_g_paths() {
  G_VENV="$ATLAS_OPT/venv"
  G_DIR="$ATLAS_ETC/secrets/google"
  G_CLIENT="$G_DIR/client_secret.json"
  G_ENVF="$ATLAS_ETC/google.env"
  G_SRC="$ATLAS_SRV/staging/inbox/google-oauth-client.json"
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

_g_venv() {
  apt_install python3-venv python3-pip unzip curl
  if [[ ! -x "$G_VENV/bin/python" ]]; then
    log "$G_VENV absent (step 02 normally creates it); creating it here with python3 -m venv"
    mkdir -p "$ATLAS_OPT"; python3 -m venv "$G_VENV" || die "python3 -m venv $G_VENV failed"
  fi
  if ! "$G_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("google-api-python-client") == "2.200.0" and m.version("google-auth-oauthlib") == "1.4.1" and m.version("google-auth") == "2.58.0" and m.version("google-auth-httplib2") == "0.4.2"' 2>/dev/null; then
    proxy_env
    ensure_dir "$G_CACHE_DIR" root:root 755
    ensure_dir "$G_CACHE_DIR/pip" root:root 755
    export PIP_CACHE_DIR="$G_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
    log "pip install ${GOOGLE_PINS[*]} into $G_VENV"
    retry 3 "$G_VENV/bin/python" -m pip install --quiet "${GOOGLE_PINS[@]}" || die "pip install of the Google client libraries failed in $G_VENV"
  fi
  chown -R atlas:atlas "$G_VENV" 2>/dev/null || true
  "$G_VENV/bin/python" -c 'import googleapiclient, google_auth_oauthlib' || die "the Google libraries do not import from $G_VENV"
}

_g_client() {
  # root:atlas 710: atlas traverses, lists nothing (header: SECRETS DIRECTORY TRAVERSAL).
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
      record_v V20 fail "OAuth client JSON absent: put the Desktop-app client JSON at $G_SRC and re-run phase2"
      die "Google OAuth client JSON absent at $G_SRC (V20 recorded as fail)"
    fi
    if ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if "installed" in d else 1)' "$G_SRC"; then
      record_v V20 fail "$G_SRC is not a Desktop-app OAuth client (no top-level 'installed' key)"
      die "$G_SRC is not a 'Desktop app' OAuth client JSON (needs the top-level key 'installed'; 'web' clients cannot use the loopback redirect)"
    fi
    install -m 600 -o atlas -g atlas "$G_SRC" "$G_CLIENT"
    log "installed the OAuth client JSON as $G_CLIENT (atlas, 600); the inbox copy is shredded once every account has passed its API proof"
  fi
  # The consumer must be able to open it now, not in Phase 3.
  svc_user_run test -r "$G_CLIENT" \
    || die "atlas cannot read $G_CLIENT: $(stat -c '%A %U:%G' "$ATLAS_ETC/secrets") on $ATLAS_ETC/secrets blocks traversal. Every writer's ensure_dir of that directory must be root:atlas 710 (header: SECRETS DIRECTORY TRAVERSAL)"
}

# _g_last_json OUTPUT -> the last line (the JSON the helper prints last).
_g_last_json() { printf '%s\n' "$1" | grep -E '^\{' | tail -n1; }

_g_authorise_all() {
  local i=0 acct email tag port out json ok n total
  total="$(wc -w <<<"$GOOGLE_ACCOUNTS")"
  proxy_env
  for acct in $GOOGLE_ACCOUNTS; do
    email="${acct%%:*}"; tag="${acct##*:}"; port=$(( OAUTH_PORT_BASE + i )); i=$(( i + 1 ))
    local tokf="$G_DIR/$email.json"
    log "Google account $i/$total ($tag): $email; token $tokf; redirect port $port"
    # Loopback only: the flow must never go through the proxy, the APIs must (proxy_env above; NO_PROXY has localhost).
    # stdout (one JSON line) is captured; the framed block with the URL comes on stderr and reaches the console.
    out="$(_g_as_atlas "$G_VENV/bin/python" "$ATLAS_DAY1_DIR/phase2/google_oauth.py" authorise \
            --client "$G_CLIENT" --token "$tokf" --email "$email" --port "$port" --timeout "$OAUTH_TIMEOUT" \
            --owner atlas --label "$i/$total, $tag" 2> >(cat >&2))" || true
    json="$(_g_last_json "$out")"
    ok="$(python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("ok") else "0")' <<<"$json" 2>/dev/null || echo 0)"
    if [[ "$ok" != 1 ]]; then
      local err; err="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("error","?"))' <<<"$json" 2>/dev/null || echo "${out:0:200}")"
      record_v V20 fail "$tag $email: $err"
      die "Google authorisation for $email failed: $err. Re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 (an already-authorised account is not asked again)"
    fi
    n="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"gmail labels {d[\"gmail_labels\"]}, calendars {d[\"calendars\"]}, drive user {d[\"drive_user\"]}")' <<<"$json")"
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
  [[ -e "$G_ENVF" ]] || : >"$G_ENVF"
  _g_venv
  _g_client
  ensure_kv "$G_ENVF" GOOGLE_TOKEN_DIR "$G_DIR"
  ensure_kv "$G_ENVF" GOOGLE_CLIENT_JSON "$G_CLIENT"
  ensure_kv "$G_ENVF" GOOGLE_TOKEN_ACCESS direct
  _g_authorise_all
  _g_rclone_install
  _g_rclone_remotes
  chown root:atlas "$G_ENVF"; chmod 640 "$G_ENVF"
  _g_shred_inbox
  # V20: pass only when Gmail, Calendar and Drive answered for every account, AS ATLAS (no browser: tokens are on disk),
  # and the inbox copy is gone.
  run_verify V20 v20-google.sh || die "V20 failed after authorisation (see the verify table)"
  notify "Phase 2 step 6c done: Google OAuth completed for $(wc -w <<<"$GOOGLE_ACCOUNTS") account(s) (V20)"
  log "step 06c done: tokens and rclone.conf in $G_DIR, settings in $G_ENVF"
}
