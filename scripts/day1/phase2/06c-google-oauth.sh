#!/usr/bin/env bash
# phase2/06c-google-oauth.sh — Section 17 Phase 2 step 6c: the Google OAuth pause (Sections 13, 21 V20, 22).
# Sourced by phase2-services.sh through run_phase_steps; defines step_06c only. This is one of the three explicit
# interactive pauses of the Day 1 scripts (CONVENTIONS.md §7.6): one authorisation link per account.
#
# For each entry email:tag in GOOGLE_ACCOUNTS:
#   1. phase2/google_oauth.py authorise runs InstalledAppFlow.run_local_server(host="localhost", port=8765+i,
#      open_browser=False) (services-tools.md §5.1 VERIFIED), prints the URL in a framed block with the instruction
#      to open it in Firefox on the node's own desktop (xrdp), and waits up to 30 minutes (never less than 15);
#   2. the token is stored at $ATLAS_ETC/secrets/google/<email>.json, mode 600, owner atlas;
#   3. Gmail (labels list), Calendar (calendar list) and Drive (about.get) are proven with that token, and the
#      signed-in address must be the expected one.
# Then upstream rclone is installed and a Drive remote gdrive-<tag> is written from the same token (the research
# VERIFIED the token shape, services-tools.md §4.9/S7), and `rclone about` proves each remote as the atlas user.
# V20 is recorded through verify/v20-google.sh, which re-proves all three APIs for every account without a browser,
# and passes only when every account answered.
#
# The OAuth client JSON comes from $ATLAS_SRV/staging/inbox/google-oauth-client.json (Section 22: the client exists).
# If it is absent this step prints exactly where to put it, records V20 FAIL (not deferred) and stops the phase;
# re-running resumes here.
#
# Contracts relied on from other writers (CONVENTIONS.md §1): /opt/atlas/venv (step 02; created here when absent,
# logged); config/allowlist.txt: accounts.google.com, oauth2.googleapis.com, www.googleapis.com,
# gmail.googleapis.com, openidconnect.googleapis.com, downloads.rclone.org, pypi.org, files.pythonhosted.org.
# Contract this file defines for others:
#   * $ATLAS_ETC/secrets/google/client_secret.json (atlas 600) and <email>.json tokens (atlas 600), dir atlas 700.
#   * $ATLAS_ETC/google.env (root:atlas 640): GOOGLE_TOKEN_DIR, GOOGLE_CLIENT_JSON, GOOGLE_TOKEN_<TAG>=<path>,
#     GOOGLE_EMAIL_<TAG>=<email>, RCLONE_BIN, RCLONE_CONF, RCLONE_REMOTE_<TAG>=gdrive-<tag>. The Drive mount units
#     (Section 13, "mounted as a folder") are the orchestrator/integration writer's; the remotes are ready for them.

GOOGLE_PINS=("google-api-python-client==2.200.0" "google-auth-oauthlib==1.4.1" "google-auth==2.58.0" "google-auth-httplib2")
RCLONE_VERSION="v1.75.1"                                    # services-tools.md §4.9 VERIFIED latest tag
RCLONE_VERSIONED_URL="https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-linux-amd64.zip"   # UNVERIFIED layout
RCLONE_CURRENT_URL="https://downloads.rclone.org/rclone-current-linux-amd64.zip"                           # VERIFIED install.md
OAUTH_PORT_BASE=8765
OAUTH_TIMEOUT=1800                                          # 30 minutes; the task's floor is 15

G_VENV=""
G_DIR=""
G_CLIENT=""
G_ENVF=""
G_ATLAS_HOME=""

_g_paths() {
  G_VENV="$ATLAS_OPT/venv"
  G_DIR="$ATLAS_ETC/secrets/google"
  G_CLIENT="$G_DIR/client_secret.json"
  G_ENVF="$ATLAS_ETC/google.env"
  G_ATLAS_HOME="$(getent passwd atlas | cut -d: -f6)"
  [[ -n "$G_ATLAS_HOME" ]] || die "cannot read the home directory of the atlas account"
}

_g_venv() {
  apt_install python3-venv python3-pip unzip curl
  if [[ ! -x "$G_VENV/bin/python" ]]; then
    log "$G_VENV absent (step 02 normally creates it); creating it here with python3 -m venv"
    mkdir -p "$ATLAS_OPT"; python3 -m venv "$G_VENV" || die "python3 -m venv $G_VENV failed"
  fi
  if ! "$G_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("google-api-python-client") == "2.200.0" and m.version("google-auth-oauthlib") == "1.4.1" and m.version("google-auth") == "2.58.0"' 2>/dev/null; then
    proxy_env
    export PIP_CACHE_DIR="$ATLAS_STATE/pip-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
    mkdir -p "$PIP_CACHE_DIR"
    log "pip install ${GOOGLE_PINS[*]} into $G_VENV"
    retry 3 "$G_VENV/bin/python" -m pip install --quiet "${GOOGLE_PINS[@]}" || die "pip install of the Google client libraries failed in $G_VENV"
  fi
  "$G_VENV/bin/python" -c 'import googleapiclient, google_auth_oauthlib' || die "the Google libraries do not import from $G_VENV"
}

_g_client() {
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  ensure_dir "$G_DIR" atlas:atlas 700
  local src="$ATLAS_SRV/staging/inbox/google-oauth-client.json"
  if [[ ! -s "$G_CLIENT" ]]; then
    if [[ ! -s "$src" ]]; then
      cat <<MSG

  ==== Google OAuth client JSON is missing (Section 22 says the client was created) ====
  Download the OAuth client of type "Desktop app" from Google Cloud -> APIs & Services -> Credentials
  (the JSON has a top-level "installed" key) and copy it to EXACTLY:
      $src
  (owner $PRINCIPAL_USER, any mode; the inbox is $PRINCIPAL_USER:atlas). Then re-run:
      sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2
  The phase resumes at this step.
  =========================================================================================
MSG
      record_v V20 fail "OAuth client JSON absent: put the Desktop-app client JSON at $src and re-run phase2"
      die "Google OAuth client JSON absent at $src (V20 recorded as fail)"
    fi
    if ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if "installed" in d else 1)' "$src"; then
      record_v V20 fail "$src is not a Desktop-app OAuth client (no top-level 'installed' key)"
      die "$src is not a 'Desktop app' OAuth client JSON (needs the top-level key 'installed'; 'web' clients cannot use the loopback redirect)"
    fi
    install -m 600 -o atlas -g atlas "$src" "$G_CLIENT"
    log "installed the OAuth client JSON as $G_CLIENT (atlas, 600); the inbox copy stays until the Principal removes it"
  fi
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
    out="$("$G_VENV/bin/python" "$ATLAS_DAY1_DIR/phase2/google_oauth.py" authorise \
            --client "$G_CLIENT" --token "$tokf" --email "$email" --port "$port" --timeout "$OAUTH_TIMEOUT" \
            --owner atlas --label "$i/$total, $tag" 2> >(cat >&2))" || true
    json="$(_g_last_json "$out")"
    ok="$(python3 -c 'import json,sys; print("1" if json.loads(sys.argv[1]).get("ok") else "0")' "$json" 2>/dev/null || echo 0)"
    if [[ "$ok" != 1 ]]; then
      local err; err="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("error","?"))' "$json" 2>/dev/null || echo "${out:0:200}")"
      record_v V20 fail "$tag $email: $err"
      die "Google authorisation for $email failed: $err. Re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 (an already-authorised account is not asked again)"
    fi
    n="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(f"gmail labels {d[\"gmail_labels\"]}, calendars {d[\"calendars\"]}, drive user {d[\"drive_user\"]}")' "$json")"
    log "Google $tag ($email): authorised; $n"
    [[ "$(stat -c '%a %U' "$tokf")" == "600 atlas" ]] || { chown atlas:atlas "$tokf"; chmod 600 "$tokf"; }
    ensure_kv "$G_ENVF" "GOOGLE_TOKEN_${tag^^}" "$tokf"
    ensure_kv "$G_ENVF" "GOOGLE_EMAIL_${tag^^}" "$email"
  done
}

_g_rclone_install() {
  local bin=/usr/local/bin/rclone
  if [[ -x "$bin" ]] && "$bin" version 2>/dev/null | grep -q "^rclone $RCLONE_VERSION"; then
    log "rclone $RCLONE_VERSION already installed at $bin"
  else
    proxy_env
    local zip="$ATLAS_SRV/staging/tools/rclone-linux-amd64.zip" tmp
    mkdir -p "$(dirname "$zip")"
    # UNVERIFIED (services-tools.md §4.9): the versioned URL layout; the rclone-current zip is VERIFIED (install.md).
    if ! curl -fsSL --max-time 300 -o "$zip" "$RCLONE_VERSIONED_URL"; then
      warn "rclone: $RCLONE_VERSIONED_URL not available; installing rclone-current instead (the pin $RCLONE_VERSION could not be honoured; the installed version is logged)"
      retry 3 curl -fsSL --max-time 300 -o "$zip" "$RCLONE_CURRENT_URL" || die "rclone download failed (downloads.rclone.org allowlisted?)"
    fi
    tmp="$(mktemp -d)"
    unzip -oq "$zip" -d "$tmp" || die "unzip $zip failed"
    local exe; exe="$(find "$tmp" -type f -name rclone | head -n1 || true)"
    [[ -n "$exe" ]] || { rm -rf "$tmp"; die "no rclone binary inside $zip"; }
    install -m 755 "$exe" "$bin"
    rm -rf "$tmp"
  fi
  log "rclone: $("$bin" version 2>/dev/null | head -n1) (apt's 1.60.1 is not used: research conflict 10)"
  ensure_kv "$G_ENVF" RCLONE_BIN "$bin"
}

_g_rclone_remotes() {
  local conf="$G_ATLAS_HOME/.config/rclone/rclone.conf" acct email tag out json remote
  ensure_dir "$G_ATLAS_HOME/.config" atlas:atlas 700
  ensure_dir "$G_ATLAS_HOME/.config/rclone" atlas:atlas 700
  proxy_env
  for acct in $GOOGLE_ACCOUNTS; do
    email="${acct%%:*}"; tag="${acct##*:}"; remote="gdrive-$tag"
    out="$("$G_VENV/bin/python" "$ATLAS_DAY1_DIR/phase2/google_oauth.py" rclone-remote \
            --token "$G_DIR/$email.json" --remote "$remote" --conf "$conf" --owner atlas 2>&1)" \
      || die "writing the rclone remote $remote failed: $(_g_last_json "$out")"
    json="$(_g_last_json "$out")"
    log "rclone remote $remote written to $conf: $json"
    # Read-only proof as the account that will mount it (rclone refreshes and rewrites the token itself).
    if ! runuser -u atlas -- env HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
           /usr/local/bin/rclone about "$remote:" --config "$conf" >/dev/null 2>&1; then
      die "rclone about $remote: failed as atlas (config $conf; the drive scope was granted? run it by hand: runuser -u atlas -- rclone about $remote: -vv)"
    fi
    log "rclone about $remote: ok (Drive reachable through the remote as atlas)"
    ensure_kv "$G_ENVF" "RCLONE_REMOTE_${tag^^}" "$remote"
  done
  ensure_kv "$G_ENVF" RCLONE_CONF "$conf"
}

step_06c() {
  _g_paths
  [[ -e "$G_ENVF" ]] || : >"$G_ENVF"
  _g_venv
  _g_client
  ensure_kv "$G_ENVF" GOOGLE_TOKEN_DIR "$G_DIR"
  ensure_kv "$G_ENVF" GOOGLE_CLIENT_JSON "$G_CLIENT"
  _g_authorise_all
  _g_rclone_install
  _g_rclone_remotes
  chown root:atlas "$G_ENVF"; chmod 640 "$G_ENVF"
  # V20: pass only when Gmail, Calendar and Drive answered for every account (no browser: tokens are on disk now).
  run_verify V20 v20-google.sh || die "V20 failed after authorisation (see the verify table)"
  notify "Phase 2 step 6c done: Google OAuth completed for $(wc -w <<<"$GOOGLE_ACCOUNTS") account(s) (V20)"
  log "step 06c done: tokens in $G_DIR, remotes in $G_ATLAS_HOME/.config/rclone/rclone.conf, settings in $G_ENVF"
}
