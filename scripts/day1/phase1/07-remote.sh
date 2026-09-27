#!/usr/bin/env bash
# phase1/07-remote.sh — Phase 1 step 7 (Sections 9.3, 12.2, 12.3, 17, 21 V5; R7, R11): WG-Easy from
# docker/wg-easy/compose.yml (admin UI published on the LAN address only), the Cloudflare dynamic-DNS updater under
# atlas-ddns, ntfy with default-deny auth and a node token, the WireGuard bridge address added to the SSH/Cockpit/
# xrdp bindings, and V5 (DNS matches the public IP; a handshake from mobile data within 10 minutes passes, else
# deferred with the exact re-run command). No interactive prompt anywhere in this step (rule §7.6).
#
# CLOUDFLARE TOKEN HAND-OFF (Section 12.3, R7, V23): Phase 2 step 6b is the relocation step, but this step needs a
# working updater NOW. So it READS the token from $CLOUDFLARE_TXT and writes $ATLAS_ETC/secrets/cloudflare.env
# (mode 600, atlas-ddns), tightens CLOUDFLARE.txt to 600 owned by the Principal, leaves it IN PLACE, and never
# deletes it. Step 6b verifies the env file against the API, stores CF_ZONE_ID if still blank (asking once if it
# must), and only then shreds CLOUDFLARE.txt (V23).
#
# SECRETS ON DISK: WireGuard's server and peer private keys (/etc/wireguard in the container) live under
# $ATLAS_ETC/secrets/wg-easy (root 700); ntfy's user.db (password hashes, the node token) lives under
# /var/lib/atlas-ntfy (atlas 700). Neither is under /srv/atlas nor in restic's include set (CONVENTIONS §7.2);
# client configs are regenerable from the admin UI and need no backup. Secrets never travel on a command line:
# curl reads the bearer header from a file descriptor, docker exec takes NTFY_PASSWORD from --env-file, and every
# error message is redacted before it reaches the log.
# Facts from the platform research items 8, 9, 10, 11 (VERIFIED unless marked). Defines step_07 only.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

_rand_pw() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-24}"; }

# _redact TEXT — strip anything token-shaped before it can reach a log line (rule §7.2: secrets never echoed).
_redact() { sed -E 's/tk_[A-Za-z0-9]+/tk_<redacted>/g; s/[A-Za-z0-9_-]{40}/<40-char-token-redacted>/g' <<<"$*"; }

# _cf_curl TOKEN URL... — Cloudflare API call with the bearer header fed from a file descriptor, never argv.
_cf_curl() {
  local token="$1"; shift
  curl -fsS --max-time 20 -H @<(printf 'Authorization: Bearer %s\n' "$token") "$@"
}

# _migrate_dir OLD NEW — move an earlier run's data into the new location once (idempotent, refuses to clobber).
_migrate_dir() {
  local old="$1" new="$2"
  [[ -d "$old" ]] || return 0
  if [[ -n "$(ls -A "$old" 2>/dev/null)" ]]; then
    [[ -z "$(ls -A "$new" 2>/dev/null)" ]] || die "both $old (old layout) and $new hold data; merge them by hand, then re-run"
    cp -a "$old"/. "$new"/ && rm -rf "$old"
    log "moved $old -> $new (secrets leave /srv/atlas, CONVENTIONS §7.2)"
  else
    rmdir "$old" 2>/dev/null || true
  fi
}

_wg_kernel() {
  # WG-Easy needs the host's WireGuard support. Kernel 7.x carries it built in or as a module; loading it here means
  # the container needs no SYS_MODULE capability and no /lib/modules mount (compose.yml keeps NET_ADMIN only).
  modprobe wireguard 2>/dev/null || true
  install -d -m 755 /etc/modules-load.d
  printf 'wireguard\n' >/etc/modules-load.d/atlas-wireguard.conf
  # The functional proof (works for built-in and modular kernels alike): a throw-away interface of type wireguard.
  ip link del atlas-wgtest 2>/dev/null || true
  ip link add atlas-wgtest type wireguard 2>/dev/null \
    || die "the kernel offers no WireGuard support ('modprobe wireguard' and 'ip link add type wireguard' both refused): uname -r; modinfo wireguard"
  ip link del atlas-wgtest
  log "kernel WireGuard support confirmed ($(uname -r)); wg-easy runs with NET_ADMIN only"
}

_wg_easy_up() {
  local envf="$ATLAS_ETC/secrets/wg-easy.env" datadir="$ATLAS_ETC/secrets/wg-easy"
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  ensure_dir "$datadir" root:root 700
  _migrate_dir "$ATLAS_SRV/data/wg-easy" "$datadir"
  if [[ ! -s "$envf" ]]; then
    local pw; pw="$(_rand_pw 24)"
    {
      echo "# WG-Easy compose interpolation + admin credentials. WG_ADMIN_PASS is the admin password for the Principal;"
      echo "# WG_INIT_PASS/WG_INIT_ENABLED feed the container's one-time INIT_* setup and are blanked once setup ran."
      echo "WG_ADMIN_USER=$PRINCIPAL_USER"
      echo "WG_ADMIN_PASS=$pw"
      echo "WG_INIT_ENABLED=true"
      echo "WG_INIT_PASS=$pw"
    } | phase1_write_file 600 root:root "$envf"
    log "generated the WG-Easy admin password into $envf (root, 600)"
  fi
  # Non-secret interpolation values are rewritten every run so a changed LAN address is picked up.
  ensure_kv "$envf" LAN_IP "$LAN_IP"
  ensure_kv "$envf" LAN_CIDR "$LAN_CIDR"
  ensure_kv "$envf" LAN_DNS "$(phase1_lan_resolvers | head -n1)"
  ensure_kv "$envf" VPN_HOST "$VPN_HOST"
  ensure_kv "$envf" WG_PORT "$WG_PORT"
  ensure_kv "$envf" WG_CIDR "$WG_CIDR"
  ensure_kv "$envf" WG_DATA_DIR "$datadir"
  ensure_kv "$envf" TZ "$TZ"
  grep -q '^WG_INIT_ENABLED=' "$envf" || ensure_kv "$envf" WG_INIT_ENABLED true
  grep -q '^WG_INIT_PASS=' "$envf" || ensure_kv "$envf" WG_INIT_PASS "$(awk -F= '$1=="WG_ADMIN_PASS" {print $2; exit}' "$envf")"
  local dir="$ATLAS_DAY1_DIR/docker/wg-easy"
  [[ -f "$dir/compose.yml" ]] || die "$dir/compose.yml is missing"
  _wg_kernel
  local compose=(docker compose --project-name atlas-wg-easy --project-directory "$dir" --env-file "$envf")
  log "docker compose up wg-easy (ghcr.io/wg-easy/wg-easy:15; pull goes through the proxy)"
  retry 3 "${compose[@]}" up -d --quiet-pull \
    || die "docker compose up for wg-easy failed (ghcr.io and pkg-containers.githubusercontent.com must be allowlisted): $(_redact "$("${compose[@]}" logs --tail 20 2>&1 | tail -n 20)")"
  ip link show "$ATLAS_WG_BRIDGE" >/dev/null 2>&1 || die "the compose bridge $ATLAS_WG_BRIDGE does not exist after compose up (driver_opts com.docker.network.bridge.name UNVERIFIED; docker network ls)"
  _wg_easy_check
  # Setup has run: recreate the container WITHOUT the admin password in its environment (INIT_* are consumed on the
  # first start only; docker inspect and config.v2.json would otherwise expose it to every docker-group member).
  if [[ "$(awk -F= '$1=="WG_INIT_ENABLED" {print $2; exit}' "$envf")" != "false" ]]; then
    ensure_kv "$envf" WG_INIT_ENABLED false
    ensure_kv "$envf" WG_INIT_PASS ""
    "${compose[@]}" up -d --quiet-pull || die "docker compose up (recreate without INIT_PASSWORD) failed: $(_redact "$("${compose[@]}" logs --tail 20 2>&1 | tail -n 20)")"
    _wg_easy_check
    log "wg-easy recreated without INIT_PASSWORD in its environment (the admin credential stays only in $envf)"
  fi
  ss -lunH "sport = :$WG_PORT" | grep -q ":$WG_PORT" || die "UDP $WG_PORT is not bound on the host (docker port wg-easy)"
  log "WG-Easy up: admin http://$LAN_IP:51821/ (LAN and WireGuard only), UDP $WG_PORT published"
}

# _wg_easy_check — the admin UI answers and $WG_IFACE is up inside the container.
_wg_easy_check() {
  local code=""
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 5 "http://$LAN_IP:51821/" || true)"
    [[ "$code" =~ ^(200|30[1-8])$ ]] && break
    sleep 2
  done
  [[ "$code" =~ ^(200|30[1-8])$ ]] || die "WG-Easy admin UI at http://$LAN_IP:51821/ did not answer (HTTP '$code'): docker logs wg-easy"
  local wgs
  wgs="$(docker exec wg-easy wg show "$WG_IFACE" 2>&1 | head -n 3 || true)"
  grep -q "interface: $WG_IFACE" <<<"$wgs" \
    || die "$WG_IFACE is not up inside wg-easy: the INIT_* unattended setup did not run (WG-Easy v15 UNVERIFIED detail). Open http://$LAN_IP:51821/ and finish the wizard with host $VPN_HOST port $WG_PORT, then re-run: sudo $ATLAS_ENTRY phase1 --force 07. Output: $(_redact "$wgs")"
  log "wg-easy: $(head -n1 <<<"$wgs")"
}

_cloudflare_env() {
  local envf="$ATLAS_ETC/secrets/cloudflare.env" token="" zone_id=""
  if [[ -s "$envf" ]]; then
    # Existing file from an earlier run: keep its values (KEY=VALUE lines only, CONVENTIONS.md §2).
    token="$(awk -F= '$1=="CF_API_TOKEN" {print $2; exit}' "$envf")"
    zone_id="$(awk -F= '$1=="CF_ZONE_ID" {print $2; exit}' "$envf")"
  fi
  if [[ -z "$token" ]]; then
    [[ -s "$CLOUDFLARE_TXT" ]] || die "$CLOUDFLARE_TXT is missing or empty: the scoped Cloudflare token (Zone:DNS:Edit on $DOMAIN) must be there for the ddns updater (Section 12.3)"
    # Accept "token", "KEY=token", "KEY: token" or a line of prose containing it: the first 40-char token-shaped word.
    token="$(grep -oE '[A-Za-z0-9_-]{40}' "$CLOUDFLARE_TXT" | head -n1 || true)"
    [[ -n "$token" ]] || die "no Cloudflare API token (40 characters of [A-Za-z0-9_-]) found in $CLOUDFLARE_TXT"
    # The plain-text file stays for Phase 2 step 6b's read-back-then-shred, but readable by the Principal and root only.
    chown "$PRINCIPAL_USER:$PRINCIPAL_USER" "$CLOUDFLARE_TXT"; chmod 600 "$CLOUDFLARE_TXT"
    log "read the Cloudflare token from $CLOUDFLARE_TXT (now 600 $PRINCIPAL_USER; left in place for Phase 2 step 6b to verify and shred)"
  fi
  proxy_env
  local api="https://api.cloudflare.com/client/v4" verify
  verify="$(_cf_curl "$token" "$api/user/tokens/verify" 2>/dev/null || true)"
  # UNVERIFIED: /user/tokens/verify endpoint name is from memory (research item 10 note); a non-JSON answer is treated
  # as "could not verify" rather than as a failure, and the record lookup below is the real test.
  if jq -e '.result.status == "active"' <<<"$verify" >/dev/null 2>&1; then
    log "Cloudflare token verified: active"
  else
    warn "Cloudflare token could not be verified via /user/tokens/verify (answer: $(_redact "${verify:0:120}")); continuing with the DNS record lookup"
  fi
  if [[ -z "$zone_id" ]]; then
    # Adjudicated conflict 3, without any prompt here: the API when the token allows, else the id written beside the
    # token in CLOUDFLARE.txt, else a non-secret CF_ZONE_ID= key in atlas.env, else blank (the updater fails visibly,
    # V5 reports the DNS mismatch, and Phase 2 step 6b asks the Principal once).
    zone_id="$(_cf_curl "$token" "$api/zones?name=$DOMAIN&status=active" 2>/dev/null | jq -r '.result[0].id // empty' || true)"
    if [[ -n "$zone_id" ]]; then
      log "zone id for $DOMAIN resolved with GET /zones (the token carries Zone:Zone:Read)"
    else
      zone_id="$(grep -oiE 'zone[ _-]?id[^0-9a-f]*[0-9a-f]{32}' "$CLOUDFLARE_TXT" 2>/dev/null | grep -oE '[0-9a-f]{32}' | head -n1 || true)"
      [[ -n "$zone_id" ]] && log "zone id found beside the token in $CLOUDFLARE_TXT"
    fi
    if [[ -z "$zone_id" && -n "${CF_ZONE_ID:-}" ]]; then
      zone_id="$(tr -d '[:space:]' <<<"$CF_ZONE_ID")"
      log "zone id taken from CF_ZONE_ID in $ATLAS_ETC/atlas.env"
    fi
    if [[ -n "$zone_id" && ! "$zone_id" =~ ^[0-9a-f]{32}$ ]]; then
      warn "'$zone_id' is not a 32-hex zone id; leaving CF_ZONE_ID blank"
      zone_id=""
    fi
    [[ -n "$zone_id" ]] || warn "CF_ZONE_ID left blank (the token cannot list zones and no id was found): the ddns updater will fail until Phase 2 step 6b stores it; V5 will report the DNS mismatch. To fix it now: CF_ZONE_ID=<32-hex id from the dashboard Overview page> in $ATLAS_ETC/atlas.env, then --force 07."
  fi
  {
    echo "# Cloudflare dynamic DNS (Section 12.3; V23). Written by Phase 1 step 7, verified and finalised by Phase 2 step 6b."
    echo "CF_API_TOKEN=$token"
    echo "CF_ZONE_NAME=$DOMAIN"
    echo "CF_ZONE_ID=$zone_id"
    echo "CF_RECORD_NAME=$VPN_HOST"
  } | phase1_write_file 600 atlas-ddns:atlas-ddns "$envf"
  log "wrote $envf (600 atlas-ddns:atlas-ddns; zone id ${zone_id:-blank})"
}

_ddns_install() {
  apt_install jq curl
  _cloudflare_env
  install -m 755 "$ATLAS_DAY1_DIR/phase1/cloudflare-ddns.sh" /usr/local/sbin/atlas-ddns
  export VPN_HOST ATLAS_ETC
  render_template "$ATLAS_DAY1_DIR/systemd/atlas-ddns.service" /etc/systemd/system/atlas-ddns.service VPN_HOST ATLAS_ETC
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-ddns.timer" /etc/systemd/system/atlas-ddns.timer
  systemctl daemon-reload
  systemctl enable --now atlas-ddns.timer >/dev/null
  if systemctl start atlas-ddns.service; then
    log "ddns: first run ok, public IP $(cat /var/lib/atlas-ddns/last-ip 2>/dev/null || echo '?') -> $VPN_HOST"
  else
    warn "ddns: first run failed (journalctl -u atlas-ddns -n 20); the timer retries every 5 minutes. V5 will show whether DNS matches."
  fi
}

# _ntfy_cli ENVFILE_CONTENT ARGS... — `ntfy` inside the container with NTFY_PASSWORD delivered via --env-file (a
# process-substituted file, never `-e NTFY_PASSWORD=...` on a world-readable command line). Empty content = no env.
_ntfy_cli() {
  local envc="$1"; shift
  if [[ -n "$envc" ]]; then
    docker exec --env-file <(printf '%s\n' "$envc") atlas-ntfy ntfy "$@"
  else
    docker exec atlas-ntfy ntfy "$@"
  fi
}

_ntfy_up() {
  local base="$ATLAS_SRV/data/ntfy" auth=/var/lib/atlas-ntfy d
  for d in etc cache; do ensure_dir "$base/$d" atlas:atlas 750; done
  ensure_dir "$base" atlas:atlas 750
  ensure_dir "$auth" atlas:atlas 700
  _migrate_dir "$base/lib" "$auth"
  # server.yml: auth on, default deny (VERIFIED keys: auth-file, auth-default-access, base-url, listen-http, cache-file).
  cat >"$base/etc/server.yml" <<YML
base-url: "http://$LAN_IP:8090"
listen-http: ":80"
cache-file: "/var/cache/ntfy/cache.db"
attachment-cache-dir: "/var/cache/ntfy/attachments"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
behind-proxy: false
enable-signup: false
enable-login: true
YML
  chown atlas:atlas "$base/etc/server.yml"; chmod 640 "$base/etc/server.yml"
  grep -q '^NTFY_AUTH_DIR=' "$ATLAS_ETC/docker.env" || die "$ATLAS_ETC/docker.env has no NTFY_AUTH_DIR (step 6 writes it; --force 06)"
  local dir="$ATLAS_DAY1_DIR/docker/ntfy"
  log "docker compose up ntfy (binwiederhier/ntfy:v2.28.0)"
  retry 3 docker compose --project-name atlas-ntfy --project-directory "$dir" --env-file "$ATLAS_ETC/docker.env" up -d --quiet-pull \
    || die "docker compose up for ntfy failed: $(_redact "$(docker compose --project-name atlas-ntfy --project-directory "$dir" --env-file "$ATLAS_ETC/docker.env" logs --tail 20 2>&1 | tail -n 20)")"
  wait_http "http://127.0.0.1:8090/v1/health" 90 || die "ntfy did not become healthy on http://127.0.0.1:8090/v1/health (docker logs atlas-ntfy)"

  # Users and the node token via the CLI inside the container (NTFY_PASSWORD makes `user add` non-interactive;
  # UNVERIFIED output format of `token add`, so the token is parsed and the step dies if nothing token-shaped appears).
  local tokf="$ATLAS_ETC/secrets/ntfy.env" prinf="$ATLAS_ETC/secrets/ntfy-principal.env" out
  # UNVERIFIED: NTFY_PASSWORD is ntfy's documented non-interactive password source for `user add`; "already exists"
  # on a re-run is tolerated, any other failure stops the step (output redacted before it is logged).
  out="$(_ntfy_cli "NTFY_PASSWORD=$(_rand_pw 32)" user add --role=admin atlas 2>&1)" \
    || grep -qi 'exists' <<<"$out" || die "ntfy user add atlas failed: $(_redact "$out")"
  if [[ ! -s "$prinf" ]]; then
    local ppw; ppw="$(_rand_pw 20)"
    out="$(_ntfy_cli "NTFY_PASSWORD=$ppw" user add --role=user principal 2>&1)" \
      || { grep -qi 'exists' <<<"$out" && _ntfy_cli "NTFY_PASSWORD=$ppw" user change-pass principal >/dev/null; } \
      || die "ntfy user add/change-pass principal failed: $(_redact "$out")"
    printf 'NTFY_PRINCIPAL_USER=principal\nNTFY_PRINCIPAL_PASS=%s\n' "$ppw" | phase1_write_file 600 root:root "$prinf"
    log "ntfy phone login for the Principal written to $prinf (root, 600)"
  fi
  _ntfy_cli "" access principal "$NTFY_TOPIC" read-only >/dev/null || die "ntfy access principal $NTFY_TOPIC failed"
  _ntfy_cli "" access principal "${NTFY_TOPIC}-*" read-only >/dev/null || true
  if [[ ! -s "$tokf" ]] || ! grep -qE '^NTFY_TOKEN=tk_' "$tokf"; then
    local tok
    out="$(_ntfy_cli "" token add --label node atlas 2>&1)" || die "ntfy token add failed: $(_redact "$out")"
    tok="$(grep -oE 'tk_[A-Za-z0-9]{29}' <<<"$out" | head -n1 || true)"
    [[ -n "$tok" ]] || die "could not parse a tk_ token from 'ntfy token add' (UNVERIFIED output format; ${#out} bytes): $(_redact "$out")"
    printf 'NTFY_TOKEN=%s\n' "$tok" | phase1_write_file 600 atlas:atlas "$tokf"
    log "ntfy node token written to $tokf (600 atlas:atlas)"
  fi
  # Push test through lib/common.sh's notify (loopback, bearer token).
  notify "Phase 1 step 7: ntfy is up at http://$LAN_IP:8090 (topic $NTFY_TOPIC)"
  local NTFY_TOKEN=""
  # shellcheck disable=SC1090  # secret file, NTFY_TOKEN=... (CONVENTIONS.md §2)
  source "$tokf"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 10 -H @<(printf 'Authorization: Bearer %s\n' "$NTFY_TOKEN") -d "ntfy auth test" "http://127.0.0.1:8090/$NTFY_TOPIC" || true)"
  [[ "$code" == 200 ]] || die "publishing to ntfy with the node token returned HTTP $code (expected 200)"
  code="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 10 -d "anonymous" "http://127.0.0.1:8090/$NTFY_TOPIC" || true)"
  [[ "$code" == 403 ]] || die "anonymous publish to ntfy returned HTTP $code, expected 403 (auth-default-access deny-all not in effect)"
  log "ntfy: auth default-deny confirmed (token 200, anonymous 403)"
}

# _ntfy_path_from_vpn — prove the path a VPN client takes to ntfy: from the wg-easy container (the phone's packets
# are masqueraded to its address) to the published http://$LAN_IP:8090, a DNAT hop onto ntfy's own bridge that
# Docker's inter-bridge isolation would drop without the DOCKER-USER DNAT accept (docker-egress-rules.sh; research
# item 9 marked this UNVERIFIED). A phone that handshakes but cannot open ntfy must not pass V5 silently.
_ntfy_path_from_vpn() {
  local out rc=0
  out="$(docker exec wg-easy sh -c "wget -qO- -T 5 http://$LAN_IP:8090/v1/health" 2>&1)" || rc=$?
  if (( rc == 127 )) || grep -qi 'not found' <<<"$out"; then
    warn "cannot probe ntfy from inside wg-easy (no wget in the image; UNVERIFIED). Check by hand from the phone: http://$LAN_IP:8090/v1/health must answer over the tunnel"
    return 0
  fi
  grep -q '"healthy"' <<<"$out" \
    || die "ntfy is NOT reachable from the WireGuard side (wg-easy -> http://$LAN_IP:8090/v1/health, rc $rc: $(_redact "$out")). Check 'iptables -S DOCKER-USER' for the '--ctstate DNAT -j ACCEPT' rules from $ATLAS_WG_BRIDGE and 'iptables -S DOCKER-ISOLATION-STAGE-2'"
  log "ntfy reachable from the WireGuard bridge (DNAT path through DOCKER-USER): ok"
}

step_07() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP is empty"
  [[ -s "$ATLAS_ETC/docker.env" ]] || die "$ATLAS_ETC/docker.env missing (step 6)"
  declare -F phase1_write_file >/dev/null || die "phase1_write_file is not defined: step 7 must run under phase1-platform.sh (04-system.sh defines it)"
  _wg_easy_up
  # The WireGuard bridge now exists: add its gateway to the SSH/Cockpit and xrdp bindings (Section 3.6).
  phase1_listen_addrs "$LAN_IP" 127.0.0.1 "$ATLAS_WG_BRIDGE_GW"
  phase1_xrdp_bind "$LAN_IP" "$ATLAS_WG_BRIDGE_GW"
  _ddns_install
  _ntfy_up
  _ntfy_path_from_vpn

  local home_ip
  home_ip="$(cat /var/lib/atlas-ddns/last-ip 2>/dev/null || echo unknown)"
  cat <<MSG

  ==== V5: WireGuard from mobile data (up to 10 minutes; the Principal's phone) ====
  Node public IP (per the ddns updater): $home_ip     DNS record: $VPN_HOST
  If your router's WAN address is in 100.64.0.0/10 you are behind CGNAT and this cannot pass (R11).
  1. On this LAN, open http://$LAN_IP:51821/ and log in as "$PRINCIPAL_USER" with the password in
     $ATLAS_ETC/secrets/wg-easy.env (sudo cat it). Create a client named "phone" and show its QR code.
  2. On the phone: install the WireGuard app, scan the QR code, then TURN WI-FI OFF (mobile data only)
     and switch the tunnel on.
  3. In the phone's browser open http://$LAN_IP:8090/ (ntfy) and log in as "principal" with the
     password in $ATLAS_ETC/secrets/ntfy-principal.env; subscribe to topic "$NTFY_TOPIC".
  This step passes as soon as a handshake from a mobile-data address is seen. If you cannot do it now,
  V5 is recorded as deferred (non-blocking) and you re-run it later with:
      sudo $ATLAS_ENTRY phase1 --force 07
  =================================================================================
MSG
  run_verify V5 v05-wireguard.sh "$VPN_HOST" wg-easy "$WG_IFACE" 540 "sudo $ATLAS_ENTRY phase1 --force 07" \
    || die "V5 failed (wg-easy container or $WG_IFACE missing; see the verify table)"
}
