#!/usr/bin/env bash
# phase1/05b-desktop.sh — Phase 1 step 5b (Sections 3.1, 3.6, 17, 21 V19; R21; Appendix B): XFCE and xrdp bound to
# the LAN address now (the WireGuard bridge address is added by step 7), no display manager and no autologin,
# Firefox as a .deb from Mozilla's apt repository (adjudicated conflict 5), then V19: (a) xrdp listens only on the
# bound addresses, (b) wait up to 10 minutes for the Principal's RDP session; no session -> deferred, not failed.
# Facts from the platform research item 4 (package names VERIFIED; the Mozilla repo recipe is UNVERIFIED and fails
# loudly if it does not hold). Defines step_05b and the helper phase1_xrdp_bind (re-used by step 7).
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

_firefox_deb() {
  # Mozilla's apt repository (UNVERIFIED recipe: the KB page was unreachable during research; the key URL and suite
  # are the widely published ones). Every failure stops the step with the fix instead of silently keeping the stub.
  install -d -m 0755 /etc/apt/keyrings
  if [[ ! -s /etc/apt/keyrings/packages.mozilla.org.asc ]]; then
    proxy_env
    curl -fsSL --max-time 60 https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc \
      || die "could not fetch Mozilla's repo signing key via the proxy (is packages.mozilla.org in config/allowlist.txt? see /var/log/squid/access.log)"
    grep -q 'BEGIN PGP PUBLIC KEY BLOCK' /etc/apt/keyrings/packages.mozilla.org.asc \
      || die "the Mozilla key file is not an ASCII-armoured key (UNVERIFIED recipe; check https://packages.mozilla.org/apt/)"
  fi
  cat >/etc/apt/sources.list.d/mozilla.sources <<'SRC'
Types: deb
URIs: https://packages.mozilla.org/apt
Suites: mozilla
Components: main
Signed-By: /etc/apt/keyrings/packages.mozilla.org.asc
SRC
  printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' >/etc/apt/preferences.d/mozilla
  _ATLAS_APT_UPDATED=0
  export DEBIAN_FRONTEND=noninteractive
  retry 3 apt-get -q update || die "apt-get update failed after adding the Mozilla repository"
  apt-cache policy firefox | grep -q 'packages.mozilla.org' \
    || die "apt does not offer firefox from packages.mozilla.org (apt-cache policy firefox); the archive package is a snap stub and is not acceptable (conflict 5)"
  # The archive stub may already be installed; the pin makes Mozilla's build the candidate.
  retry 3 apt-get install -y -q -o Dpkg::Options::=--force-confold firefox || die "apt-get install firefox (Mozilla repo) failed"
  local ver; ver="$(dpkg-query -W -f='${Version}' firefox 2>/dev/null || true)"
  [[ -n "$ver" && "$ver" != *snap* ]] || die "installed firefox is '$ver' (the snap stub); the Mozilla pin did not take"
  log "Firefox $ver installed from packages.mozilla.org (.deb, not snap)"
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
  install -m 644 -o "$PRINCIPAL_USER" -g "$PRINCIPAL_USER" /dev/stdin "/home/$PRINCIPAL_USER/.xsession" <<<'xfce4-session'
  systemctl get-default | grep -qx multi-user.target || systemctl set-default multi-user.target >/dev/null
  local dm
  for dm in lightdm gdm3 sddm; do
    if systemctl list-unit-files "$dm.service" 2>/dev/null | grep -q "^$dm.service"; then
      systemctl disable --now "$dm.service" >/dev/null 2>&1 || true
      warn "display manager $dm was present and has been disabled (no autologin, R21)"
    fi
  done
  phase1_xrdp_bind "$LAN_IP"
  _firefox_deb

  # V19: print the instructions here (the verify script's stdout is the one-line evidence), then wait.
  cat <<MSG

  ==== V19: test the desktop from the Principal's Windows PC (up to 10 minutes) ====
  1. On the Windows PC open "Remote Desktop Connection" (mstsc) and connect to:  $LAN_IP
  2. Accept the certificate warning (self-signed), pick session "Xorg", log in as user "$PRINCIPAL_USER"
     with your Ubuntu password. An XFCE desktop with Firefox should appear.
  Nothing else to do here; this step passes as soon as the session shows up. If you cannot test now, wait it out:
  V19 is recorded as deferred (non-blocking) and you can re-run it later with:
      sudo $ATLAS_ENTRY phase1 --force 05b
  ===============================================================================
MSG
  run_verify V19 v19-xrdp.sh "$PRINCIPAL_USER" 570 "sudo $ATLAS_ENTRY phase1 --force 05b" "$LAN_IP" \
    || die "V19 failed: xrdp is not listening only on the bound addresses (see the verify table)"
}
