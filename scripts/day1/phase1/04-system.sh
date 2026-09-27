#!/usr/bin/env bash
# phase1/04-system.sh — Phase 1 step 4 (Sections 3.3, 3.6, 12.5, 17; Appendix B): allowlist proxy (squid) and the
# proxy environment, ufw default-deny in AND out, full system update through the proxy, Canonical's beacons and
# self-updaters off, GRUB kernel parameters (V3), SSH hardening, Cockpit, then the reboot marker and the reboot
# (unless --no-reboot).
# Order matters: the proxy and firewall come first so that even the system update obeys rule §7.1.
#
# THE ONE UNAVOIDABLE PRE-PROXY INSTALL: squid itself (with jq and gettext-base for rendering its config) has to be
# fetched from the Ubuntu archive before the proxy exists. Nothing else in steps 1-4 installs a package before
# _squid_render/_ufw_rules have run (steps 1-3 only verify that the ISO-seeded tools are present).
#
# Facts typed literally from the platform research items 2, 5, 6 (VERIFIED unless marked). Defines step_04 and the
# helpers shared with later steps (Phase 1 internal contract; nothing in Phase 2 depends on them):
#   phase1_write_file MODE OWNER DST   write stdin to DST atomically (never `install /dev/stdin`, see below)
#   phase1_lan_resolvers               the LAN's IPv4 resolvers (ufw, DOCKER-USER, daemon.json dns, WG INIT_DNS)
#   phase1_listen_addrs ADDR...        bind SSH and Cockpit to exactly these addresses (step 7 adds the WG bridge)
#   phase1_reload_allowlist [FILE]     re-render the squid allowlist and reload squid (driver --reload-allowlist)
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
# Section 3.3 / Appendix B: the two parameters V3a fails on.
export ATLAS_GRUB_PARAMS="amdgpu.gttsize=196608 ttm.pages_limit=50331648"
# Adjudicated conflict 8 (binding on every writer; not in Appendix B): the kernel 7.x GPU watchdog for DeepSeek V4
# DeviceLost, llama.cpp issue #25664. Applied here, REPORTED by V3a in its evidence line, never a V3a fail condition
# (Section 21 defines V3 as the Appendix B parameters plus the GTT pool).
export ATLAS_GRUB_EXTRA="amdgpu.lockup_timeout=10000,60000,10000,10000"

# phase1_write_file MODE OWNER DST — write stdin to DST with MODE (and OWNER "user" or "user:group" when non-empty).
# Why not `... | install -m MODE /dev/stdin DST`: Ubuntu 26.04's /usr/bin/install is rust-coreutils 0.8.0 (VERIFIED
# packages.ubuntu.com/resolute), whose install canonicalizes the SOURCE path whenever DEST already exists; for a pipe
# that fails with "No such file or directory", so every re-run of a step (rule §7.3) would die. A regular temp file
# works with both GNU and uutils install. mktemp creates the file 0600, so a secret never sits world-readable.
phase1_write_file() {
  local mode="$1" owner="$2" dst="$3" tmp
  tmp="$(mktemp)" || die "phase1_write_file: mktemp failed"
  cat >"$tmp"
  if [[ -n "$owner" ]]; then
    install -m "$mode" -o "${owner%%:*}" -g "${owner#*:}" "$tmp" "$dst" || { rm -f "$tmp"; die "phase1_write_file: install $dst failed"; }
  else
    install -m "$mode" "$tmp" "$dst" || { rm -f "$tmp"; die "phase1_write_file: install $dst failed"; }
  fi
  rm -f "$tmp"
}

# phase1_lan_resolvers — the IPv4 DNS servers systemd-resolved uses on LAN_IFACE (one per line), else the global ones,
# else the default gateway. Everything that resolves names (host ufw rules, DOCKER-USER, daemon.json "dns", the
# WireGuard clients' INIT_DNS) is pinned to exactly this list, so "DNS to any host" never becomes a channel around
# the allowlist (Section 12.5).
phase1_lan_resolvers() {
  local list=() gw
  mapfile -t list < <(resolvectl dns "$LAN_IFACE" 2>/dev/null | awk -F': ' 'NF>1 {print $2}' | tr ' ' '\n' \
                        | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)
  if (( ${#list[@]} == 0 )); then
    mapfile -t list < <(resolvectl dns 2>/dev/null | awk -F': ' 'NF>1 {print $2}' | tr ' ' '\n' \
                          | grep -E '^[0-9]+(\.[0-9]+){3}$' | sort -u || true)
  fi
  if (( ${#list[@]} == 0 )); then
    gw="$(ip -o route show default 2>/dev/null | awk '{print $3; exit}')"
    [[ -n "$gw" ]] && list=("$gw")
  fi
  (( ${#list[@]} > 0 )) || die "phase1_lan_resolvers: no IPv4 resolver on $LAN_IFACE and no default gateway (resolvectl dns; ip route)"
  printf '%s\n' "${list[@]}"
}

# phase1_listen_addrs ADDR... — bind SSH and Cockpit to exactly these addresses (Section 3.6 "listening on LAN and
# WireGuard interfaces only"). FreeBind lets both sockets bind before Wi-Fi has its address or before the Docker
# bridge exists, so boot order can never leave the node without SSH. Step 4 calls it with the LAN address, step 7
# adds the WireGuard bridge gateway. The ufw per-interface rules (below) are the second, interface-level fence.
# Step 1 warns when LAN_IP is a DHCP lease rather than a reserved/static address, because these sockets stay on it.
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

# _allowlist_covers HOST — does the rendered allowlist admit HOST (exact entry, or a leading-dot entry that is a
# suffix of it)? Mirrors squid's dstdomain semantics.
_allowlist_covers() {
  local host="$1" e
  while read -r e; do
    [[ -n "$e" ]] || continue
    if [[ "$e" == .* ]]; then
      [[ "$host" == "${e#.}" || "$host" == *"$e" ]] && return 0
    else
      [[ "$host" == "$e" ]] && return 0
    fi
  done </etc/squid/allowlist.txt
  return 1
}

_squid_render() {
  # Strip comments and blanks from config/allowlist.txt; squid reads one dstdomain per line.
  local src="$ATLAS_DAY1_DIR/config/allowlist.txt"
  [[ -s "$src" ]] || die "config/allowlist.txt is missing"
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$src" | phase1_write_file 644 '' /etc/squid/allowlist.txt
  local n; n="$(wc -l </etc/squid/allowlist.txt)"
  (( n > 10 )) || die "allowlist rendered only $n entries; refusing to lock the node out"
  # config/allowlist.txt is the ONLY allowlist (rule §7.1), so the Sentinel feed hosts in config/sentinel-feeds.json
  # are not merged in; instead the render dies when the two files disagree, which keeps them in sync loudly.
  local feeds="$ATLAS_DAY1_DIR/config/sentinel-feeds.json" h missing=()
  if [[ -s "$feeds" ]]; then
    while read -r h; do
      [[ -n "$h" ]] || continue
      h="${h%%:*}"
      [[ "$h" =~ ^[0-9]+(\.[0-9]+){3}$ || "$h" == localhost ]] && continue
      _allowlist_covers "$h" || missing+=("$h")
    done < <(grep -oE 'https?://[^/"[:space:]]+' "$feeds" | sed -E 's#^https?://##' | sort -u)
    (( ${#missing[@]} == 0 )) || die "config/sentinel-feeds.json names hosts that config/allowlist.txt does not admit: ${missing[*]} (add them to the Sentinel block of the allowlist)"
  fi
  [[ -f /etc/squid/squid.conf.dist ]] || cp -n /etc/squid/squid.conf /etc/squid/squid.conf.dist
  # Clients: loopback (squid.conf.tmpl) plus the Docker bridge ranges, which is where every container's gateway
  # address sits. The WireGuard bridge is NOT a client: VPN clients must never use the node as an outbound proxy,
  # and wg-easy itself needs no egress (DISABLE_VERSION_CHECK=true). CONVENTIONS §8 "squid 3128 (loopback only)"
  # therefore reads "loopback plus the Docker bridge gateways", which rule §7.1 requires for containers.
  export SQUID_ALLOWLIST=/etc/squid/allowlist.txt SQUID_CLIENT_NETS="172.16.0.0/12"
  render_template "$ATLAS_DAY1_DIR/config/squid.conf.tmpl" /etc/squid/squid.conf SQUID_ALLOWLIST SQUID_CLIENT_NETS
  squid -k parse >/dev/null 2>&1 || die "squid -k parse rejects /etc/squid/squid.conf: $(squid -k parse 2>&1 | tail -n5)"
  log "squid: $n allowlisted domains rendered to /etc/squid/allowlist.txt"
}

# phase1_reload_allowlist [FILE] — the lightweight path after an allowlist edit (config/allowlist.txt comment):
# copy FILE over $ATLAS_DAY1_DIR/config/allowlist.txt when given, re-render, reload squid. No ufw reset, no
# dist-upgrade, no reboot (all of which a `--force 04` would do).
phase1_reload_allowlist() {
  local src="${1:-}" dst="$ATLAS_DAY1_DIR/config/allowlist.txt"
  [[ -e "$ATLAS_ETC/proxy.env" ]] || die "the proxy has not been set up yet (step 4 has not run); nothing to reload"
  if [[ -n "$src" ]]; then
    [[ -s "$src" ]] || die "allowlist file $src is missing or empty"
    if [[ "$(readlink -f "$src")" != "$(readlink -f "$dst")" ]]; then
      install -m 644 "$src" "$dst"
      log "copied $src -> $dst"
    fi
  fi
  _squid_render
  systemctl reload squid || systemctl restart squid || die "squid failed to reload: systemctl status squid"
  log "allowlist reloaded into squid ($(wc -l </etc/squid/allowlist.txt) entries); no firewall or reboot involved"
}

_proxy_environment() {
  # $ATLAS_ETC/proxy.env is the contract lib/common.sh's proxy_env reads (KEY=VALUE, sourceable, EnvironmentFile-able).
  # The *_TELEMETRY / DO_NOT_TRACK / PIP / NPM keys keep every host-side tool from phoning home to allowlisted hosts
  # (huggingface.co/api/telemetry, pypi.org version checks, npm update-notifier): rule §7.1 forbids telemetry even
  # to hosts the proxy admits. lib/common.sh's proxy_env exports only the proxy keys from this file (its contract);
  # the same keys are therefore also placed in environment.d, profile.d and systemd's DefaultEnvironment.
  local telemetry_off=(
    "HF_HUB_ENABLE_HF_TRANSFER=0" "HF_HUB_DISABLE_TELEMETRY=1" "DO_NOT_TRACK=1"
    "PIP_DISABLE_PIP_VERSION_CHECK=1" "NPM_CONFIG_UPDATE_NOTIFIER=false" "PLAYWRIGHT_SKIP_BROWSER_GC=1"
  )
  {
    echo "# Written by ATLAS Phase 1 step 4. Every outbound request goes through the squid allowlist proxy (§7.1)."
    echo "HTTP_PROXY=$ATLAS_PROXY_URL"
    echo "HTTPS_PROXY=$ATLAS_PROXY_URL"
    echo "NO_PROXY=$ATLAS_NO_PROXY"
    printf '%s\n' "${telemetry_off[@]}"
  } | phase1_write_file 644 '' "$ATLAS_ETC/proxy.env"
  install -d -m 755 /etc/environment.d /etc/profile.d /etc/apt/apt.conf.d /etc/systemd/system.conf.d
  {
    echo "HTTP_PROXY=$ATLAS_PROXY_URL"; echo "HTTPS_PROXY=$ATLAS_PROXY_URL"; echo "NO_PROXY=$ATLAS_NO_PROXY"
    echo "http_proxy=$ATLAS_PROXY_URL"; echo "https_proxy=$ATLAS_PROXY_URL"; echo "no_proxy=$ATLAS_NO_PROXY"
    printf '%s\n' "${telemetry_off[@]}"
  } | phase1_write_file 644 '' /etc/environment.d/90-atlas-proxy.conf
  sed 's/^/export /' /etc/environment.d/90-atlas-proxy.conf | phase1_write_file 644 '' /etc/profile.d/90-atlas-proxy.sh
  printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$ATLAS_PROXY_URL" "$ATLAS_PROXY_URL" \
    | phase1_write_file 644 '' /etc/apt/apt.conf.d/90atlas-proxy
  # Drop-in for every system service (the research's recommendation instead of a separate env-install unit).
  printf '[Manager]\nDefaultEnvironment=HTTP_PROXY=%s HTTPS_PROXY=%s NO_PROXY=%s %s\n' \
    "$ATLAS_PROXY_URL" "$ATLAS_PROXY_URL" "$ATLAS_NO_PROXY" "${telemetry_off[*]}" \
    | phase1_write_file 644 '' /etc/systemd/system.conf.d/90-atlas-proxy.conf
  systemctl daemon-reexec
  proxy_env
  log "proxy environment installed: $ATLAS_ETC/proxy.env, environment.d, profile.d, apt.conf.d, systemd DefaultEnvironment (telemetry keys off)"
}

# _ntp_servers — the IPv4 addresses systemd-timesyncd will use, one per line: its current ServerAddress plus every
# A record of its configured server names (ntp.ubuntu.com by default). timesyncd is then pinned to exactly these
# addresses (timesyncd.conf.d drop-in) so the ufw NTP rules and the client agree. UNVERIFIED: Canonical's
# ntp.ubuntu.com addresses are treated as long-lived; if they ever change, `timedatectl timesync-status` shows the
# failure and `--force 04` re-pins them.
_ntp_servers() {
  local out=() a names n
  a="$(timedatectl show-timesync -p ServerAddress --value 2>/dev/null || true)"
  [[ "$a" =~ ^[0-9]+(\.[0-9]+){3}$ ]] && out+=("$a")
  names="$(timedatectl show-timesync -p SystemNTPServers --value 2>/dev/null || true) $(timedatectl show-timesync -p FallbackNTPServers --value 2>/dev/null || true) $(timedatectl show-timesync -p ServerName --value 2>/dev/null || true)"
  [[ "$names" =~ [A-Za-z0-9] ]] || names="ntp.ubuntu.com"
  for n in $names; do
    if [[ "$n" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then out+=("$n"); continue; fi
    mapfile -t -O "${#out[@]}" out < <(getent ahostsv4 "$n" 2>/dev/null | awk '{print $1}' | sort -u | head -n 8 || true)
  done
  (( ${#out[@]} > 0 )) || return 1
  printf '%s\n' "${out[@]}" | sort -u
}

# _ufw_docker_user_base FILE CHAIN_SUFFIX — pre-create the DOCKER-USER chain with its terminal DROPs in ufw's
# after(6).rules so the chain exists from `ufw enable` at boot, BEFORE dockerd starts restart:unless-stopped
# containers. atlas-docker-egress.service (step 6) then flushes and rebuilds the full rule set (LAN-specific
# RETURNs, DNAT accept for published ports, logging) after dockerd is up; if that unit ever fails, this base still
# drops everything leaving a bridge for the LAN interface.
_ufw_docker_user_base() {
  local f="$1" v6="$2" lan="$LAN_IFACE" tmp r
  [[ -f "$f" ]] || return 0
  sed -i '/^# ATLAS-DOCKER-USER-BEGIN/,/^# ATLAS-DOCKER-USER-END/d' "$f"
  sed -i '/^:DOCKER-USER - \[0:0\]$/d' "$f"
  grep -q '^# End required lines' "$f" || die "$f has no '# End required lines' anchor; the ufw layout changed"
  grep -q '^COMMIT' "$f" || die "$f has no COMMIT line; the ufw layout changed"
  sed -i '0,/^# End required lines/ s//:DOCKER-USER - [0:0]\n# End required lines/' "$f"
  tmp="$(mktemp)"
  {
    echo "# ATLAS-DOCKER-USER-BEGIN: container egress baseline (Section 12.5, adjudicated conflict 4); full set by atlas-docker-egress.service"
    echo "-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN"
    if [[ "$v6" == 0 ]]; then
      echo "-A DOCKER-USER -i $ATLAS_WG_BRIDGE -o $lan -d $LAN_CIDR -j RETURN"
      for r in "${ATLAS_LAN_RESOLVERS[@]}"; do
        echo "-A DOCKER-USER -o $lan -d $r -p udp --dport 53 -j RETURN"
        echo "-A DOCKER-USER -o $lan -d $r -p tcp --dport 53 -j RETURN"
      done
    fi
    echo "-A DOCKER-USER -i br-+ -o $lan -j DROP"
    echo "-A DOCKER-USER -i docker0 -o $lan -j DROP"
    echo "# ATLAS-DOCKER-USER-END"
  } >"$tmp"
  awk -v blk="$tmp" 'BEGIN{done=0} /^COMMIT/ && !done { while ((getline l < blk) > 0) print l; done=1 } {print}' "$f" >"$f.atlas.tmp"
  cat "$f.atlas.tmp" >"$f"; rm -f "$f.atlas.tmp" "$tmp"
}

_ufw_rules() {
  local lan="$LAN_IFACE" net="$LAN_CIDR" wgbr="$ATLAS_WG_BRIDGE" wgnet="$ATLAS_WG_BRIDGE_NET" p r
  # Resolvers and NTP servers are collected BEFORE the firewall closes (name resolution is still open here).
  mapfile -t ATLAS_LAN_RESOLVERS < <(phase1_lan_resolvers)
  local ntp=()
  if ! mapfile -t ntp < <(_ntp_servers); then ntp=(); fi
  if (( ${#ntp[@]} == 0 )); then
    local gw; gw="$(ip -o route show default | awk '{print $3; exit}')"
    warn "no NTP server address could be resolved; allowing NTP to the default gateway $gw only (UNVERIFIED that the router serves NTP)"
    ntp=("$gw")
  fi
  # Persist the resolver list for the standalone DOCKER-USER script (atlas.env key LAN_DNS_SERVERS; derived, not a secret).
  ensure_kv "$ATLAS_ETC/atlas.env" LAN_DNS_SERVERS "\"${ATLAS_LAN_RESOLVERS[*]}\""

  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default deny outgoing >/dev/null
  ufw default deny routed >/dev/null
  ufw logging low >/dev/null                                   # denied packets logged (Section 12.5)
  # Inbound: LAN and the WireGuard bridge only (VPN clients appear as the container's 10.42.42.42). Exactly the
  # Section 3.6 / 12.5 set: SSH, Cockpit, Open WebUI, ntfy, xrdp. The orchestrator ($ORCH_PORT) is deliberately
  # absent: Open WebUI's Filter reaches it on loopback (Phase 2 binds it to 127.0.0.1).
  for p in "22:SSH" "9090:Cockpit" "$OPENWEBUI_PORT:Open WebUI" "8090:ntfy" "3389:xrdp"; do
    ufw allow in on "$lan"  from "$net"   to any port "${p%%:*}" proto tcp comment "${p#*:} LAN" >/dev/null
    ufw allow in on "$wgbr" from "$wgnet" to any port "${p%%:*}" proto tcp comment "${p#*:} WireGuard" >/dev/null
  done
  ufw allow in on "$lan" to any port "$WG_PORT" proto udp comment 'WireGuard from anywhere' >/dev/null
  # Containers reach the allowlist proxy at their bridge gateway (INPUT). The LAN interface is denied FIRST so that a
  # LAN that itself sits in 172.16.0.0/12 can never reach squid; the WireGuard bridge is not a squid client at all.
  ufw deny  in on "$lan" to any port 3128 proto tcp comment 'squid never from the LAN' >/dev/null
  ufw allow in on docker0 to any port 3128 proto tcp comment 'squid from docker0' >/dev/null
  ufw allow in from 172.16.0.0/12 to any port 3128 proto tcp comment 'squid from compose bridges' >/dev/null
  # Outbound: DNS and NTP to the configured servers ONLY (never "to any": DNS to arbitrary hosts is the classic
  # tunnel around an HTTP allowlist), DHCP, the LAN itself, the Docker bridges. HTTP(S) only for the squid user
  # (before.rules, below). No outbound rule for UDP $WG_PORT: wg0 lives inside the wg-easy container, whose replies
  # traverse FORWARD/DOCKER-USER under conntrack, never the host OUTPUT chain.
  for r in "${ATLAS_LAN_RESOLVERS[@]}"; do
    ufw allow out on "$lan" to "$r" port 53 proto udp comment "DNS $r" >/dev/null
    ufw allow out on "$lan" to "$r" port 53 proto tcp comment "DNS $r" >/dev/null
  done
  for r in "${ntp[@]}"; do
    ufw allow out on "$lan" to "$r" port 123 proto udp comment "NTP $r" >/dev/null
  done
  ufw allow out on "$lan" to any port 67 proto udp comment 'DHCP' >/dev/null
  ufw allow out on "$lan" to "$net" comment 'LAN: router, Windows share, phone on LAN' >/dev/null
  ufw allow out to 172.16.0.0/12 comment 'Docker bridges (published services)' >/dev/null
  ufw allow out on "$wgbr" to "$wgnet" comment 'to the wg-easy container' >/dev/null
  # Pin timesyncd to the addresses the rules allow.
  install -d -m 755 /etc/systemd/timesyncd.conf.d
  printf '# ATLAS Phase 1 step 4: NTP pinned to the addresses ufw allows outbound on UDP 123.\n[Time]\nNTP=%s\nFallbackNTP=%s\n' \
    "${ntp[*]}" "${ntp[*]}" >/etc/systemd/timesyncd.conf.d/90-atlas.conf
  systemctl try-restart systemd-timesyncd.service >/dev/null 2>&1 || true
  # Owner-matched egress for squid: only the 'proxy' user may open 80/443 (before.rules, chain ufw-before-output).
  # ICMP: echo-request and the error types to the LAN only (V1 pings the gateway); never "any ICMP anywhere".
  local uid; uid="$(id -u proxy)" || die "user 'proxy' (squid) does not exist"
  local f chain tmp
  for f in /etc/ufw/before.rules /etc/ufw/before6.rules; do
    [[ -f "$f" ]] || continue
    # The IPv6 file uses ufw6-* chain names and ipv6-icmp (VERIFIED ufw layout).
    if [[ "$f" == *before6* ]]; then chain=ufw6-before-output; else chain=ufw-before-output; fi
    sed -i '/^# ATLAS:/,/ATLAS-END$/d' "$f"                                  # remove an earlier insertion (ufw reset already restored the default file)
    grep -q "^-A $chain -o lo -j ACCEPT" "$f" || die "$f has no '-A $chain -o lo -j ACCEPT' anchor line; the ufw layout changed"
    tmp="$(mktemp)"
    {
      echo "# ATLAS: only the squid proxy user may open outbound HTTP/HTTPS (Section 12.5); ICMP to the LAN only (V1)"
      echo "-A $chain -p tcp -m multiport --dports 80,443 -m owner --uid-owner $uid -j ACCEPT"
      if [[ "$chain" == ufw6-before-output ]]; then
        local t
        for t in 133 134 135 136 137; do echo "-A $chain -p ipv6-icmp --icmpv6-type $t -j ACCEPT"; done
        echo "-A $chain -p ipv6-icmp --icmpv6-type 128 -d fe80::/10 -j ACCEPT -m comment --comment ATLAS-END"
      else
        local t
        for t in destination-unreachable time-exceeded parameter-problem; do echo "-A $chain -p icmp --icmp-type $t -d $net -j ACCEPT"; done
        echo "-A $chain -p icmp --icmp-type echo-request -d $net -j ACCEPT -m comment --comment ATLAS-END"
      fi
    } >"$tmp"
    sed -i "/^-A $chain -o lo -j ACCEPT/r $tmp" "$f"
    rm -f "$tmp"
  done
  _ufw_docker_user_base /etc/ufw/after.rules 0
  _ufw_docker_user_base /etc/ufw/after6.rules 1
  ufw --force enable >/dev/null
  log "ufw enabled: default deny in/out/routed; LAN=$lan $net; WG bridge=$wgbr $wgnet; DNS ${ATLAS_LAN_RESOLVERS[*]}; NTP ${ntp[*]}"
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

# _disable_beacons — Ubuntu Server's own phone-home and self-update paths (rule §7.1 "no telemetry from any
# installed component"; Section 16.3 item 6 "never modify its own configuration" also covers unattended upgrades
# replacing the validated 7.0 kernel or docker-ce): motd-news and apt-news (motd.ubuntu.com), the Ubuntu Pro
# timers (contracts.canonical.com, esm.ubuntu.com), apport/whoopsie crash uploads (daisy.ubuntu.com), the
# unattended-upgrades/apt-daily timers, popularity-contest, ubuntu-report and snapd's refresh loop (Section 3.1
# takes Firefox as Mozilla's .deb, so nothing here needs snap).
_disable_beacons() {
  if [[ -f /etc/default/motd-news ]]; then sed -i 's/^ENABLED=.*/ENABLED=0/' /etc/default/motd-news
  else printf 'ENABLED=0\n' >/etc/default/motd-news; fi
  if command -v pro >/dev/null 2>&1; then pro config set apt_news=false >/dev/null 2>&1 || true; fi
  local u
  for u in motd-news.timer motd-news.service apt-news.service esm-cache.service ua-timer.timer ua-timer.service \
           apport.service apport-autoreport.timer apport-autoreport.service whoopsie.service whoopsie.path \
           apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service update-notifier-download.timer \
           update-notifier-motd.timer; do
    systemctl disable --now "$u" >/dev/null 2>&1 || true
  done
  systemctl mask apport.service whoopsie.service >/dev/null 2>&1 || true
  cat >/etc/apt/apt.conf.d/90atlas-no-auto <<'CONF'
// ATLAS Phase 1 step 4: no unattended package activity (Section 16.3 item 6). Updates are the Principal's decision.
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::Unattended-Upgrade "0";
APT::Periodic::AutocleanInterval "0";
CONF
  local purge=() p
  for p in popularity-contest ubuntu-report; do
    [[ "$(dpkg-query -W -f='${Status}' "$p" 2>/dev/null || true)" == "install ok installed" ]] && purge+=("$p")
  done
  if [[ "$(dpkg-query -W -f='${Status}' snapd 2>/dev/null || true)" == "install ok installed" ]]; then
    if apt-get -s purge snapd 2>/dev/null | grep -qE '^Remv (ubuntu-server|ubuntu-minimal|ubuntu-standard)'; then
      warn "snapd is a hard dependency of the ubuntu-server metapackage on this image; masking its units instead of purging"
      for u in snapd.service snapd.socket snapd.seeded.service snapd.autoimport.service snapd.apparmor.service \
               snapd.recovery-chooser-trigger.service snapd.system-shutdown.service snapd.snap-repair.timer; do
        systemctl disable --now "$u" >/dev/null 2>&1 || true
      done
      systemctl mask snapd.service snapd.socket >/dev/null 2>&1 || true
    else
      purge+=(snapd)
    fi
  fi
  if (( ${#purge[@]} > 0 )); then
    log "purging ${purge[*]}"
    apt-get -y -q purge "${purge[@]}" >/dev/null || warn "apt-get purge ${purge[*]} failed; check 'apt-get purge ${purge[*]}' by hand"
  fi
  log "beacons off: motd-news, apt-news, Pro timers, apport/whoopsie (masked), apt-daily/unattended-upgrades timers; purged/masked: ${purge[*]:-none}"
}

_grub_params() {
  # Section 3.3/Appendix B plus the adjudicated lockup_timeout (see the ATLAS_GRUB_EXTRA comment at the top).
  # A drop-in under /etc/default/grub.d/ is sourced by grub-mkconfig after /etc/default/grub (VERIFIED), so the
  # variable is extended without editing the distro file, and rewriting the whole drop-in makes it idempotent.
  local all="$ATLAS_GRUB_PARAMS $ATLAS_GRUB_EXTRA"
  install -d -m 755 /etc/default/grub.d
  {
    echo "# A.T.L.A.S. Section 3.3 / Appendix B: GTT sized for 192 GB unified memory (V3a) and the kernel 7.x GPU watchdog."
    echo "# amdgpu.gttsize is deprecated but honoured (expect one drm deprecation warning); ttm.pages_limit is the parameter of record."
    echo "# amdgpu.lockup_timeout: adjudicated conflict 8 (llama.cpp issue #25664), reported but not gated by V3a."
    echo "GRUB_CMDLINE_LINUX_DEFAULT=\"\$GRUB_CMDLINE_LINUX_DEFAULT $all\""
  } >/etc/default/grub.d/90-atlas.cfg
  # Never duplicate: strip our parameters from the distro line if a hand edit put them there.
  local p
  for p in $all; do
    sed -i -E "/^GRUB_CMDLINE_LINUX_DEFAULT=/ s/[[:space:]]*$(printf '%s' "$p" | sed 's/[.]/\\./g')//g" /etc/default/grub
  done
  update-grub >/dev/null 2>&1 || die "update-grub failed"
  for p in $all; do
    grep -qF -- "$p" /boot/grub/grub.cfg || die "/boot/grub/grub.cfg does not carry $p after update-grub"
    local c; c="$(grep -m1 -E '^[[:space:]]*linux[[:space:]]' /boot/grub/grub.cfg | grep -o -F -- "$p" | wc -l)"
    (( c == 1 )) || die "$p appears $c times on the first kernel line of grub.cfg (expected exactly once)"
  done
  log "GRUB: $all (applies at the reboot; V3a checks /proc/cmdline in step 5)"
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

_cockpit_install() {
  # Without recommends: on resolute `cockpit` Recommends cockpit-networkmanager, which Depends on network-manager
  # (both VERIFIED on packages.ubuntu.com/resolute). NetworkManager would take over the Wi-Fi interface from
  # netplan/systemd-networkd in the middle of this step, over the SSH session it runs on (R9). Package set mirrors 05b.
  local nm_before nm_after
  nm_before="$(dpkg-query -W -f='${Status}' network-manager 2>/dev/null || true)"
  export DEBIAN_FRONTEND=noninteractive
  if [[ "$(dpkg-query -W -f='${Status}' cockpit-ws 2>/dev/null || true)" != "install ok installed" ]]; then
    proxy_env
    if [[ "$_ATLAS_APT_UPDATED" != "1" ]]; then retry 3 apt-get -q update || die "apt-get update failed"; _ATLAS_APT_UPDATED=1; fi
    retry 3 apt-get install -y -q --no-install-recommends -o Dpkg::Options::=--force-confold cockpit-ws cockpit-system cockpit-bridge \
      || die "installing Cockpit (cockpit-ws cockpit-system cockpit-bridge, no recommends) failed"
  fi
  nm_after="$(dpkg-query -W -f='${Status}' network-manager 2>/dev/null || true)"
  if [[ "$nm_after" == "install ok installed" && "$nm_before" != "install ok installed" ]]; then
    die "network-manager was pulled in by the Cockpit install; it would fight systemd-networkd for $LAN_IFACE. Remove it (apt-get purge network-manager) and re-run: sudo $ATLAS_ENTRY phase1"
  elif [[ "$nm_after" == "install ok installed" ]]; then
    warn "network-manager is installed on this node (installer choice); netplan must keep $LAN_IFACE under systemd-networkd"
  fi
}

step_04() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP could not be derived from $LAN_IFACE (no IPv4 address?)"
  # The one unavoidable pre-proxy install (header): squid and its rendering tools. ufw, curl and ca-certificates are
  # on the Server ISO and cost nothing to list; apt_install skips what is present.
  apt_install ufw squid jq curl ca-certificates gettext-base

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
  _ATLAS_APT_UPDATED=1
  log "apt dist-upgrade (this can take several minutes)"
  retry 2 apt-get -y -q -o Dpkg::Options::=--force-confold dist-upgrade || die "apt-get dist-upgrade failed"
  apt-get -y -q autoremove >/dev/null || true
  _disable_beacons
  # 4. Kernel parameters (V3).
  _grub_params
  # 5. SSH and Cockpit (Section 3.6).
  _cockpit_install
  systemctl enable cockpit.socket >/dev/null
  _ssh_harden
  systemctl start cockpit.socket || die "cockpit.socket failed to start: $(systemctl status cockpit.socket --no-pager | tail -n5)"
  log "Cockpit: https://$LAN_IP:9090 (LAN and WireGuard only)"
  # 6. Reboot marker; the reboot itself is the driver's (phase1_request_reboot writes this step's done marker first).
  declare -F phase1_request_reboot >/dev/null || die "phase1_request_reboot is not defined: step 4 must be run by phase1-platform.sh"
  phase1_request_reboot
}
