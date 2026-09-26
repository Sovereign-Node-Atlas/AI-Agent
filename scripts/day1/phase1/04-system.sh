#!/usr/bin/env bash
# phase1/04-system.sh — Phase 1 step 4 (Sections 3.3, 3.6, 12.5, 17; Appendix B): allowlist proxy (squid) and the
# proxy environment, ufw default-deny in AND out, full system update through the proxy, GRUB kernel parameters
# (V3), SSH hardening, Cockpit, then the reboot marker and the reboot (unless --no-reboot).
# Order matters: the proxy and firewall come first so that even the system update obeys rule §7.1.
# Facts typed literally from the platform research items 2, 5, 6 (VERIFIED unless marked). Defines step_04 and the
# helper phase1_listen_addrs (re-used by step 7 to add the WireGuard bridge address).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# Contract for every Phase 1 step and for Phase 2 (adjudicated conflict 4): VPN clients arrive from this bridge.
export ATLAS_WG_BRIDGE="br-atlas-wg"
export ATLAS_WG_BRIDGE_NET="10.42.42.0/24"
export ATLAS_WG_BRIDGE_GW="10.42.42.1"          # used by step 7 (SSH/Cockpit/xrdp bind, ntfy phone URL)
export ATLAS_PROXY_URL="http://127.0.0.1:3128"
export ATLAS_NO_PROXY="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local"
export ATLAS_GRUB_PARAMS="amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000"

# phase1_listen_addrs ADDR... — bind SSH and Cockpit to exactly these addresses (Section 3.6 "listening on LAN and
# WireGuard interfaces only"). FreeBind lets both sockets bind before Wi-Fi has its address or before the Docker
# bridge exists, so boot order can never leave the node without SSH. Step 4 calls it with the LAN address, step 7
# adds the WireGuard bridge gateway. The ufw per-interface rules (below) are the second, interface-level fence.
phase1_listen_addrs() {
  local addrs=("$@") a
  (( ${#addrs[@]} > 0 )) || die "phase1_listen_addrs: no addresses"
  {
    echo "# ATLAS Phase 1: SSH listens only on LAN and WireGuard addresses (Section 3.6). Rewritten by steps 4 and 7."
    for a in "${addrs[@]}"; do echo "ListenAddress $a"; done
  } >/etc/ssh/sshd_config.d/01-atlas-listen.conf
  install -d -m 755 /etc/systemd/system/ssh.socket.d
  printf '[Socket]\n# ATLAS: the LAN/WG addresses may not exist yet at boot; IP_FREEBIND lets the socket bind anyway.\nFreeBind=true\n' \
    >/etc/systemd/system/ssh.socket.d/atlas-freebind.conf
  install -d -m 755 /etc/systemd/system/ssh.service.d
  printf '[Unit]\nAfter=network-online.target\nWants=network-online.target\n' >/etc/systemd/system/ssh.service.d/atlas-online.conf
  install -d -m 755 /etc/systemd/system/cockpit.socket.d
  {
    echo "[Socket]"; echo "FreeBind=true"; echo "ListenStream="
    for a in "${addrs[@]}"; do echo "ListenStream=$a:9090"; done
  } >/etc/systemd/system/cockpit.socket.d/atlas-listen.conf
  sshd -t || die "sshd -t rejects the configuration (see /etc/ssh/sshd_config.d/)"
  systemctl daemon-reload
  # Ubuntu 24.04+ runs sshd socket-activated; the generator rebuilds ssh.socket from sshd_config on daemon-reload.
  if systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    systemctl restart ssh.socket || die "ssh.socket failed to restart: $(systemctl status ssh.socket --no-pager | tail -n5)"
  else
    systemctl restart ssh.service || die "ssh.service failed to restart"
  fi
  if systemctl list-unit-files cockpit.socket >/dev/null 2>&1 && systemctl is-enabled cockpit.socket >/dev/null 2>&1; then
    systemctl restart cockpit.socket || die "cockpit.socket failed to restart"
  fi
  local listening; listening="$(ss -ltnH 'sport = :22' | awk '{print $4}' | tr '\n' ' ')"
  log "sshd listening on: $listening"
  for a in "${addrs[@]}"; do
    grep -q "$a:22" <<<"$listening" || die "sshd is not listening on $a:22 after the restart (got: $listening)"
  done
}

_squid_render() {
  # Strip comments and blanks from config/allowlist.txt; squid reads one dstdomain per line.
  local src="$ATLAS_DAY1_DIR/config/allowlist.txt"
  [[ -s "$src" ]] || die "config/allowlist.txt is missing"
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$src" | install -m 644 /dev/stdin /etc/squid/allowlist.txt
  local n; n="$(wc -l </etc/squid/allowlist.txt)"
  (( n > 10 )) || die "allowlist rendered only $n entries; refusing to lock the node out"
  [[ -f /etc/squid/squid.conf.dist ]] || cp -n /etc/squid/squid.conf /etc/squid/squid.conf.dist
  export SQUID_ALLOWLIST=/etc/squid/allowlist.txt SQUID_CLIENT_NETS="172.16.0.0/12 $ATLAS_WG_BRIDGE_NET"
  render_template "$ATLAS_DAY1_DIR/config/squid.conf.tmpl" /etc/squid/squid.conf SQUID_ALLOWLIST SQUID_CLIENT_NETS
  squid -k parse >/dev/null 2>&1 || die "squid -k parse rejects /etc/squid/squid.conf: $(squid -k parse 2>&1 | tail -n5)"
  log "squid: $n allowlisted domains rendered to /etc/squid/allowlist.txt"
}

_proxy_environment() {
  # $ATLAS_ETC/proxy.env is the contract lib/common.sh's proxy_env reads (KEY=VALUE, sourceable, EnvironmentFile-able).
  {
    echo "# Written by ATLAS Phase 1 step 4. Every outbound request goes through the squid allowlist proxy (§7.1)."
    echo "HTTP_PROXY=$ATLAS_PROXY_URL"
    echo "HTTPS_PROXY=$ATLAS_PROXY_URL"
    echo "NO_PROXY=$ATLAS_NO_PROXY"
  } | install -m 644 /dev/stdin "$ATLAS_ETC/proxy.env"
  install -d -m 755 /etc/environment.d /etc/profile.d /etc/apt/apt.conf.d /etc/systemd/system.conf.d
  {
    echo "HTTP_PROXY=$ATLAS_PROXY_URL"; echo "HTTPS_PROXY=$ATLAS_PROXY_URL"; echo "NO_PROXY=$ATLAS_NO_PROXY"
    echo "http_proxy=$ATLAS_PROXY_URL"; echo "https_proxy=$ATLAS_PROXY_URL"; echo "no_proxy=$ATLAS_NO_PROXY"
    echo "HF_HUB_ENABLE_HF_TRANSFER=0"
  } | install -m 644 /dev/stdin /etc/environment.d/90-atlas-proxy.conf
  sed 's/^/export /' /etc/environment.d/90-atlas-proxy.conf | install -m 644 /dev/stdin /etc/profile.d/90-atlas-proxy.sh
  printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$ATLAS_PROXY_URL" "$ATLAS_PROXY_URL" \
    | install -m 644 /dev/stdin /etc/apt/apt.conf.d/90atlas-proxy
  # Drop-in for every system service (the research's recommendation instead of a separate env-install unit).
  printf '[Manager]\nDefaultEnvironment=HTTP_PROXY=%s HTTPS_PROXY=%s NO_PROXY=%s HF_HUB_ENABLE_HF_TRANSFER=0\n' \
    "$ATLAS_PROXY_URL" "$ATLAS_PROXY_URL" "$ATLAS_NO_PROXY" | install -m 644 /dev/stdin /etc/systemd/system.conf.d/90-atlas-proxy.conf
  systemctl daemon-reexec
  proxy_env
  log "proxy environment installed: $ATLAS_ETC/proxy.env, environment.d, profile.d, apt.conf.d, systemd DefaultEnvironment"
}

_ufw_rules() {
  local lan="$LAN_IFACE" net="$LAN_CIDR" wgbr="$ATLAS_WG_BRIDGE" wgnet="$ATLAS_WG_BRIDGE_NET" p
  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default deny outgoing >/dev/null
  ufw default deny routed >/dev/null
  ufw logging low >/dev/null                                   # denied packets logged (Section 12.5)
  # Inbound: LAN and the WireGuard bridge only (VPN clients appear as the container's 10.42.42.42).
  for p in "22:SSH" "9090:Cockpit" "$OPENWEBUI_PORT:Open WebUI" "8090:ntfy" "3389:xrdp" "$ORCH_PORT:orchestrator"; do
    ufw allow in on "$lan"  from "$net"   to any port "${p%%:*}" proto tcp comment "${p#*:} LAN" >/dev/null
    ufw allow in on "$wgbr" from "$wgnet" to any port "${p%%:*}" proto tcp comment "${p#*:} WireGuard" >/dev/null
  done
  ufw allow in on "$lan" to any port "$WG_PORT" proto udp comment 'WireGuard from anywhere' >/dev/null
  # Containers reach the allowlist proxy at their bridge gateway (INPUT); nothing on the LAN may.
  ufw allow in on docker0 to any port 3128 proto tcp comment 'squid from docker0' >/dev/null
  ufw allow in on "$wgbr" from "$wgnet" to any port 3128 proto tcp comment 'squid from wg bridge' >/dev/null
  ufw allow in from 172.16.0.0/12 to any port 3128 proto tcp comment 'squid from compose bridges' >/dev/null
  # Outbound: DNS, NTP, DHCP, WireGuard, the LAN itself, the Docker bridges. HTTP(S) only for the squid user (below).
  ufw allow out on "$lan" to any port 53 proto udp comment 'DNS' >/dev/null
  ufw allow out on "$lan" to any port 53 proto tcp comment 'DNS' >/dev/null
  ufw allow out on "$lan" to any port 123 proto udp comment 'NTP' >/dev/null
  ufw allow out on "$lan" to any port 67 proto udp comment 'DHCP' >/dev/null
  ufw allow out on "$lan" to any port "$WG_PORT" proto udp comment 'WireGuard replies' >/dev/null
  ufw allow out on "$lan" to "$net" comment 'LAN: router, Windows share, phone on LAN' >/dev/null
  ufw allow out to 172.16.0.0/12 comment 'Docker bridges (published services)' >/dev/null
  ufw allow out on "$wgbr" to "$wgnet" comment 'to the wg-easy container' >/dev/null
  # Owner-matched egress for squid: only the 'proxy' user may open 80/443 (before.rules, chain ufw-before-output).
  local uid; uid="$(id -u proxy)" || die "user 'proxy' (squid) does not exist"
  local f chain icmp
  for f in /etc/ufw/before.rules /etc/ufw/before6.rules; do
    [[ -f "$f" ]] || continue
    # The IPv6 file uses ufw6-* chain names and ipv6-icmp (VERIFIED ufw layout).
    if [[ "$f" == *before6* ]]; then chain=ufw6-before-output; icmp=ipv6-icmp; else chain=ufw-before-output; icmp=icmp; fi
    sed -i '/^# ATLAS:/,/ATLAS-END$/d' "$f"                                  # remove an earlier insertion (ufw reset already restored the default file)
    grep -q "^-A $chain -o lo -j ACCEPT" "$f" || die "$f has no '-A $chain -o lo -j ACCEPT' anchor line; the ufw layout changed"
    sed -i "/^-A $chain -o lo -j ACCEPT/a\\
# ATLAS: only the squid proxy user may open outbound HTTP/HTTPS (Section 12.5); ICMP echo for diagnostics (V1)\\
-A $chain -p tcp -m multiport --dports 80,443 -m owner --uid-owner $uid -j ACCEPT\\
-A $chain -p $icmp -j ACCEPT -m comment --comment ATLAS-END" "$f"
  done
  ufw --force enable >/dev/null
  log "ufw enabled: default deny in/out/routed; LAN=$lan $net; WG bridge=$wgbr $wgnet"
  ufw status verbose | sed 's/^/    /'
}

_proxy_selftest() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --proxy "$ATLAS_PROXY_URL" https://archive.ubuntu.com/ || true)"
  [[ "$code" == 200 ]] || die "the allowlist proxy cannot reach https://archive.ubuntu.com/ (HTTP '$code'). Check: systemctl status squid; tail /var/log/squid/cache.log; ufw status"
  if curl -sS -o /dev/null --noproxy '*' --max-time 6 https://archive.ubuntu.com/ 2>/dev/null; then
    die "direct egress (bypassing the proxy) still works; ufw is not enforcing default deny outgoing"
  fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --proxy "$ATLAS_PROXY_URL" https://example.com/ || true)"
  [[ "$code" == 403 || "$code" == 000 ]] || die "a non-allowlisted host answered through the proxy (HTTP $code); squid's allowlist is not applied"
  log "proxy self-test: allowlisted 200, direct blocked, non-allowlisted denied ($code)"
}

_grub_params() {
  # Section 3.3/Appendix B plus amdgpu.lockup_timeout (adjudicated conflict 8; llama.cpp #25664 on kernel 7.x).
  # A drop-in under /etc/default/grub.d/ is sourced by grub-mkconfig after /etc/default/grub (VERIFIED), so the
  # variable is extended without editing the distro file, and rewriting the whole drop-in makes it idempotent.
  install -d -m 755 /etc/default/grub.d
  {
    echo "# A.T.L.A.S. Section 3.3 / Appendix B: GTT sized for 192 GB unified memory (V3a) and the kernel 7.x GPU watchdog."
    echo "# amdgpu.gttsize is deprecated but honoured (expect one drm deprecation warning); ttm.pages_limit is the parameter of record."
    echo "GRUB_CMDLINE_LINUX_DEFAULT=\"\$GRUB_CMDLINE_LINUX_DEFAULT $ATLAS_GRUB_PARAMS\""
  } >/etc/default/grub.d/90-atlas.cfg
  # Never duplicate: strip our parameters from the distro line if a hand edit put them there.
  local p
  for p in $ATLAS_GRUB_PARAMS; do
    sed -i -E "/^GRUB_CMDLINE_LINUX_DEFAULT=/ s/[[:space:]]*$(printf '%s' "$p" | sed 's/[.]/\\./g')//g" /etc/default/grub
  done
  update-grub >/dev/null 2>&1 || die "update-grub failed"
  for p in $ATLAS_GRUB_PARAMS; do
    grep -qF -- "$p" /boot/grub/grub.cfg || die "/boot/grub/grub.cfg does not carry $p after update-grub"
    local c; c="$(grep -m1 -E '^[[:space:]]*linux[[:space:]]' /boot/grub/grub.cfg | grep -o -F -- "$p" | wc -l)"
    (( c == 1 )) || die "$p appears $c times on the first kernel line of grub.cfg (expected exactly once)"
  done
  log "GRUB: $ATLAS_GRUB_PARAMS (applies at the reboot; V3a checks /proc/cmdline in step 5)"
}

_ssh_harden() {
  local ak="/home/$PRINCIPAL_USER/.ssh/authorized_keys"
  [[ -s "$ak" ]] || die "SSH is about to become key-only but $ak is missing or empty. Add the Principal's public key (from the console: mkdir -p ~/.ssh && nano ~/.ssh/authorized_keys), then re-run: sudo $ATLAS_ENTRY phase1"
  # 00- so it sorts before Ubuntu's 50-cloud-init.conf: sshd keeps the FIRST value it reads for each keyword.
  cat >/etc/ssh/sshd_config.d/00-atlas.conf <<'CONF'
# ATLAS Phase 1 step 4 (Section 3.6): keys only, no passwords, no root.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
X11Forwarding no
AllowTcpForwarding yes
ClientAliveInterval 300
ClientAliveCountMax 2
MaxAuthTries 3
CONF
  phase1_listen_addrs "$LAN_IP" 127.0.0.1
  local eff; eff="$(sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication) ' | tr '\n' ' ')"
  grep -q 'passwordauthentication no' <<<"$eff" || die "sshd -T still reports password auth on: $eff"
  log "sshd hardened: $eff"
}

step_04() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP could not be derived from $LAN_IFACE (no IPv4 address?)"
  apt_install ufw squid jq curl ca-certificates gettext-base bind9-dnsutils iptables

  # 1. Proxy and its environment (rule §7.1 from here on).
  _squid_render
  systemctl enable --now squid >/dev/null
  systemctl reload squid || systemctl restart squid
  _proxy_environment
  # 2. Firewall (Section 3.6, 12.5).
  _ufw_rules
  _proxy_selftest
  # 3. Full system update, through the proxy, unattended (needrestart would otherwise prompt on Server).
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1
  retry 3 apt-get -q update || die "apt-get update failed through the proxy"
  log "apt dist-upgrade (this can take several minutes)"
  retry 2 apt-get -y -q -o Dpkg::Options::=--force-confold dist-upgrade || die "apt-get dist-upgrade failed"
  apt-get -y -q autoremove >/dev/null || true
  # 4. Kernel parameters (V3).
  _grub_params
  # 5. SSH and Cockpit (Section 3.6).
  apt_install cockpit
  systemctl enable cockpit.socket >/dev/null
  _ssh_harden
  systemctl start cockpit.socket || die "cockpit.socket failed to start: $(systemctl status cockpit.socket --no-pager | tail -n5)"
  log "Cockpit: https://$LAN_IP:9090 (LAN and WireGuard only)"
  # 6. Reboot marker; the reboot itself is the driver's (phase1_request_reboot writes this step's done marker first).
  declare -F phase1_request_reboot >/dev/null || die "phase1_request_reboot is not defined: step 4 must be run by phase1-platform.sh"
  phase1_request_reboot
}
