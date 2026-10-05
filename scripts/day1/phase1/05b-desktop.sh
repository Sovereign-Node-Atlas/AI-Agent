#!/usr/bin/env bash
# phase1/05b-desktop.sh — Phase 1 step 5b (Sections 3.1, 3.6, 17, 21 V19; R21; Appendix B): XFCE and xrdp bound to
# the LAN address now (the WireGuard bridge address is added by step 7), no display manager and no autologin,
# Google Chrome from Google's apt repository (the Principal's choice, 2026-10-05), then V19: (a) xrdp listens only on the
# bound addresses, (b) wait up to 10 minutes for the Principal's RDP session; no session -> V19 recorded as FAIL
# recorded as DEFERRED with a to-do (policy v0.3.3: the Principal's actions never fail a gate); the step completes.
# Facts from the platform research item 4 (XFCE/xrdp package names VERIFIED; the Google repo recipe is UNVERIFIED and
# fails loudly if it does not hold). Defines step_05b and the helper phase1_xrdp_bind (re-used by step 7).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# phase1_xrdp_bind ADDR... — xrdp.ini's [Globals] port= line carries the bind addresses (there is no address= key;
# VERIFIED xrdp.ini(5)). xrdp refuses to start if any listed address is not UP, so a service drop-in orders it after
# network-online and docker (the bridge) and restarts it until the addresses exist.
phase1_xrdp_bind() {
  local addrs=("$@") a spec=""
  (( ${#addrs[@]} > 0 )) || die "phase1_xrdp_bind: no addresses"
  for a in "${addrs[@]}"; do spec+="tcp://$a:3389 "; done
  spec="${spec% }"
  [[ -f /etc/xrdp/xrdp.ini ]] || die "/etc/xrdp/xrdp.ini is missing"
  grep -qE '^port=' /etc/xrdp/xrdp.ini || die "/etc/xrdp/xrdp.ini has no port= line in [Globals]"
  sed -i -E "0,/^port=.*/ s|^port=.*|port=$spec|" /etc/xrdp/xrdp.ini
  install -d -m 755 /etc/systemd/system/xrdp.service.d
  cat >/etc/systemd/system/xrdp.service.d/atlas.conf <<'CONF'
# ATLAS Phase 1 step 5b: xrdp binds to the LAN and WireGuard-bridge addresses (Section 3.6), which may appear late.
[Unit]
After=network-online.target docker.service
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Restart=on-failure
RestartSec=10s
CONF
  systemctl daemon-reload
  systemctl enable xrdp >/dev/null
  systemctl restart xrdp || die "xrdp failed to start with 'port=$spec' (are all addresses UP? ip -4 addr): $(journalctl -u xrdp -n 5 --no-pager)"
  sleep 2
  local l; l="$(ss -ltnH 'sport = :3389' | awk '{print $4}' | tr '\n' ' ')"
  log "xrdp listening on: $l"
  for a in "${addrs[@]}"; do grep -q "$a:3389" <<<"$l" || die "xrdp is not listening on $a:3389 (got: $l)"; done
}

# _chrome_policies — Google Chrome managed policy, written BEFORE the install so the first launch already runs with
# metrics, background mode and promotions off (the Principal asked for Chrome, 2026-10-05; it is the Principal's own
# browser for the desktop session, Section 3.6, not part of ATLAS, so the zero-cloud rule is about telemetry hygiene
# here, not isolation). /etc/opt/chrome/policies/managed/ is Chrome's documented Linux policy path (VERIFIED, Chrome
# Enterprise "Set Chrome policies for users or browsers on Linux"). Policy names VERIFIED against the Chrome Enterprise
# policy list: MetricsReportingEnabled, BackgroundModeEnabled, PromotionsEnabled, DefaultBrowserSettingEnabled,
# PasswordManagerEnabled, BrowserSignin (0 = disabled), SafeBrowsingProtectionLevel (1 = standard; left on: it is the
# Principal's protection, UNVERIFIED whether the standard level phones home beyond hash prefixes), UrlKeyedAnonymizedDataCollectionEnabled.
_chrome_policies() {
  install -d -m 755 /etc/opt/chrome/policies/managed /etc/opt/chrome/policies/recommended
  cat >/etc/opt/chrome/policies/managed/atlas.json <<'JSON'
{
  "MetricsReportingEnabled": false,
  "UrlKeyedAnonymizedDataCollectionEnabled": false,
  "BackgroundModeEnabled": false,
  "PromotionsEnabled": false,
  "DefaultBrowserSettingEnabled": false,
  "BrowserSignin": 0,
  "SyncDisabled": true,
  "PasswordManagerEnabled": false,
  "SafeBrowsingProtectionLevel": 1,
  "ProxyMode": "system"
}
JSON
  chmod 644 /etc/opt/chrome/policies/managed/atlas.json
  log "Google Chrome managed policy written (metrics, URL-keyed data collection, background mode, promotions, sign-in and sync off; proxy = system)"
}

_chrome_deb() {
  # Google's apt repository for Chrome (recipe as published by Google: the signing key at dl.google.com/linux/linux_signing_key.pub,
  # suite "stable", component "main"; UNVERIFIED from the build sandbox beyond that page's wording). Every failure stops
  # the step with the fix. dl.google.com must be in config/allowlist.txt (it is, under "package mirrors during builds").
  _chrome_policies
  install -d -m 0755 /etc/apt/keyrings
  local keyf=/etc/apt/keyrings/google-chrome.gpg
  if [[ ! -s "$keyf" ]]; then
    proxy_env
    command -v gpg >/dev/null || apt_install gnupg
    curl -fsSL --max-time 60 https://dl.google.com/linux/linux_signing_key.pub -o "$keyf.asc" \
      || die "could not fetch Google's Linux package signing key via the proxy (is dl.google.com in config/allowlist.txt? see /var/log/squid/access.log)"
    grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$keyf.asc" \
      || { rm -f "$keyf.asc"; die "the Google key file is not an ASCII-armoured key (UNVERIFIED recipe; check https://www.google.com/linuxrepositories/)"; }
    gpg --dearmor -o "$keyf" "$keyf.asc" || die "gpg --dearmor of Google's signing key failed"
    rm -f "$keyf.asc"; chmod 644 "$keyf"
  fi
  # Google rotates subkeys inside one published key set; the trust anchor is the TLS fetch from dl.google.com plus the
  # published key page (no fingerprint pin here, UNVERIFIED which fingerprint is current; the key set is logged).
  local got; got="$(gpg --show-keys --with-fingerprint --with-colons "$keyf" 2>/dev/null | awk -F: '$1=="fpr" {print $10}' | tr '\n' ' ')"
  log "Google apt signing key fingerprints: ${got:-none}"
  [[ -n "$got" ]] || die "Google's signing key could not be read back (gpg --show-keys $keyf)"
  cat >/etc/apt/sources.list.d/google-chrome.sources <<SRC
Types: deb
URIs: https://dl.google.com/linux/chrome/deb/
Suites: stable
Components: main
Architectures: amd64
Signed-By: $keyf
SRC
  # Chrome's own postinst would add a second (duplicate) source list; disable that so the one above stays the only one.
  install -d -m 755 /etc/default
  grep -q '^repo_add_once=' /etc/default/google-chrome 2>/dev/null && sed -i 's/^repo_add_once=.*/repo_add_once="false"/' /etc/default/google-chrome \
    || echo 'repo_add_once="false"' >>/etc/default/google-chrome
  _ATLAS_APT_UPDATED=0
  export DEBIAN_FRONTEND=noninteractive
  retry 3 apt-get -q update || die "apt-get update failed after adding the Google Chrome repository"
  apt-cache policy google-chrome-stable | grep -q 'dl.google.com' \
    || die "apt does not offer google-chrome-stable from dl.google.com (apt-cache policy google-chrome-stable); check the repository recipe in phase1/05b-desktop.sh"
  retry 3 apt-get install -y -q -o Dpkg::Options::=--force-confold google-chrome-stable || die "apt-get install google-chrome-stable failed"
  local ver; ver="$(dpkg-query -W -f='${Version}' google-chrome-stable 2>/dev/null || true)"
  [[ -n "$ver" ]] || die "google-chrome-stable is not installed after apt-get install"
  log "Google Chrome $ver installed from dl.google.com"
}

step_05b() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP is empty"
  export DEBIAN_FRONTEND=noninteractive
  # --no-install-recommends keeps display managers out; package set VERIFIED on packages.ubuntu.com/resolute.
  proxy_env
  if [[ "$(dpkg-query -W -f='${Status}' xfce4 2>/dev/null || true)" != "install ok installed" ]]; then
    if [[ "$_ATLAS_APT_UPDATED" != "1" ]]; then retry 3 apt-get -q update || die "apt-get update failed"; _ATLAS_APT_UPDATED=1; fi
    retry 3 apt-get install -y -q --no-install-recommends -o Dpkg::Options::=--force-confold \
      xfce4 xfce4-goodies xfce4-terminal dbus-x11 xorg xrdp xorgxrdp \
      || die "installing XFCE/xrdp failed"
  fi
  apt_install xrdp xorgxrdp
  adduser --quiet xrdp ssl-cert >/dev/null 2>&1 || true        # harmless; needed only if runtime_user=xrdp is enabled
  sed -i -E 's/^AllowRootLogin=.*/AllowRootLogin=false/' /etc/xrdp/sesman.ini
  sed -i -E 's/^security_layer=.*/security_layer=tls/' /etc/xrdp/xrdp.ini   # mstsc speaks TLS; drop plain RDP crypto
  # Per-user session for the Principal; no display manager means no autologin (Appendix B "no autologin").
  # phase1_write_file (04-system.sh): `install /dev/stdin` fails on re-runs with resolute's rust-coreutils install.
  # The account's real primary group, not a same-named group (fix round 3: an LDAP/`users` layout has none).
  printf 'xfce4-session\n' | phase1_write_file 644 "$PRINCIPAL_USER:$(id -gn "$PRINCIPAL_USER")" "/home/$PRINCIPAL_USER/.xsession"
  systemctl get-default | grep -qx multi-user.target || systemctl set-default multi-user.target >/dev/null
  local dm
  for dm in lightdm gdm3 sddm; do
    if systemctl list-unit-files "$dm.service" 2>/dev/null | grep -q "^$dm.service"; then
      systemctl disable --now "$dm.service" >/dev/null 2>&1 || true
      warn "display manager $dm was present and has been disabled (no autologin, R21)"
    fi
  done
  # Bind to the LAN address now; step 7 adds the WireGuard bridge gateway. On a re-run after step 7 (--force 05b)
  # the bridge already exists, so keep its address rather than dropping the VPN path until step 7 runs again.
  local addrs=("$LAN_IP")
  if [[ -n "${ATLAS_WG_BRIDGE:-}" ]] && ip link show "$ATLAS_WG_BRIDGE" >/dev/null 2>&1; then addrs+=("$ATLAS_WG_BRIDGE_GW"); fi
  phase1_xrdp_bind "${addrs[@]}"
  _chrome_deb

  # V19: print the instructions here (the verify script's stdout is the one-line evidence), then wait.
  cat <<MSG

  ==== V19: test the desktop from the Principal's Windows PC (up to 10 minutes) ====
  1. On the Windows PC open "Remote Desktop Connection" (mstsc) and connect to:  $LAN_IP
  2. Accept the certificate warning (self-signed), pick session "Xorg", log in as user "$PRINCIPAL_USER"
     with your Ubuntu password. An XFCE desktop with Google Chrome should appear.
  Nothing else to do here; V19 passes as soon as the session shows up. If no session appears in 10 minutes,
  V19 is recorded as DEFERRED (policy v0.3.3: the Principal's input never stops a phase), this step completes,
  the item goes on the to-do list and Phase 1 continues. Record it later (idempotent, waits again) with:
      sudo $ATLAS_ENTRY phase1 --force 05b
  ===============================================================================
MSG
  # phase1_xrdp_bind already died if xrdp is down or not bound as intended, so a fail here is the timeout. Policy
  # v0.3.3: the Principal's RDP click is an input, so the timeout is recorded as deferred (gate: never blocks) plus a
  # to-do; the step completes and Phase 1 goes on.
  if ! run_verify V19 v19-xrdp.sh "$PRINCIPAL_USER" 570 "sudo $ATLAS_ENTRY phase1 --force 05b" "${addrs[@]}"; then
    record_v V19 deferred "no RDP session seen in time (to-do rdp-test); xrdp is up and bound on ${addrs[*]}"
    todo_add rdp-test "Connect once from the Windows PC with Remote Desktop (mstsc) to $LAN_IP as user $PRINCIPAL_USER (session Xorg), then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1 --force 05b to record V19" \
      "Section 17 step 5b / V19. Chrome's policies and the xrdp binding are already in place; only the test is outstanding."
  fi
}
