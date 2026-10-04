#!/usr/bin/env bash
# phase1/04-system.sh — Phase 1 step 4 (Sections 3.3, 3.6, 12.5, 17; Appendix B): allowlist proxy (squid) and the
# proxy environment, the allowlisting DNS forwarder (dnsmasq) the host resolves through, ufw default-deny in AND out,
# full system update through the proxy, Canonical's beacons off, GRUB kernel parameters (V3), SSH hardening, Cockpit,
# then the reboot marker and the reboot (unless --no-reboot).
# Order matters: the proxy, the forwarder and the firewall come first so that even the system update obeys rule §7.1.
#
# THE ONE UNAVOIDABLE PRE-PROXY INSTALL: squid itself and dnsmasq (with jq and gettext-base for rendering their
# configs) have to be fetched from the Ubuntu archive before the proxy exists. Nothing else in steps 1-4 installs a
# package before _squid_render/_ufw_rules have run (steps 1-3 only verify that the ISO-seeded tools are present).
#
# DNS (fix round, Section 12.5 "everything else denied and logged"): the home router's recursive resolver answers ANY
# name, so plain "DNS to the pinned LAN resolvers" was a data channel around the allowlist (<chunk>.exfil.example
# queries carry data out, answers carry instructions in). Now: dnsmasq listens on 127.0.0.1:53 and forwards ONLY the
# allowlist.txt names (plus the node's own DOMAIN, ntp.ubuntu.com and the Windows share host) to the LAN resolvers,
# answering NXDOMAIN for everything else (dnsmasq `address=/#/`, VERIFIED on dnsmasq 2.91 during the fix round);
# systemd-resolved is pointed at it (global DNS=127.0.0.1, the LAN link's DHCP DNS switched off); ufw lets only the
# dnsmasq user open port 53 to those resolvers (owner match in before.rules); containers get no port 53 at all (the
# proxy receives the hostname in CONNECT/absolute-URI form, so nothing behind squid needs DNS; Docker's embedded DNS
# still resolves compose service names locally). `--reload-allowlist` re-renders squid and dnsmasq together.
#
# Facts typed literally from the platform research items 2, 5, 6 (VERIFIED unless marked). Defines step_04 and the
# helpers shared with later steps (Phase 1 internal contract; nothing in Phase 2 depends on them):
#   phase1_write_file MODE OWNER DST   write stdin to DST atomically (never `install /dev/stdin`, see below)
#   phase1_lan_resolvers               the LAN's IPv4 resolvers (ufw owner rules, dnsmasq upstreams, WG INIT_DNS)
#   phase1_listen_addrs ADDR...        bind SSH and Cockpit to exactly these addresses (step 7 adds the WG bridge)
#   phase1_reload_allowlist [FILE]     re-render squid + dnsmasq from the allowlist and reload both (driver option)
#   _squid_render                      also called by step 6 once the docker0 gateway is known (second http_port)
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
# Section 3.3 / Appendix B (v0.3.1): the three kernel parameters, all gated by V3a. amdgpu.gttsize is deprecated but
# honoured (one drm warning, accepted by V3a); ttm.pages_limit is the parameter of record; amdgpu.lockup_timeout is
# the kernel 7.x GPU watchdog S8 added so that V22 (DeepSeek V4 DeviceLost, llama.cpp issue #25664) cannot fail for a
# kernel reason, which is exactly why a cmdline without it must fail V3a rather than pass.
export ATLAS_GRUB_PARAMS="amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000"
# Hosts rule §7.1 forbids outright, enforced at render time (Section 16.3 item 6: a one-line edit must not open them).
ATLAS_ALLOWLIST_NEVER=(api.openai.com api.anthropic.com generativelanguage.googleapis.com aiplatform.googleapis.com
                       api.openwebui.com motd.ubuntu.com daisy.ubuntu.com errors.ubuntu.com)
ATLAS_ALLOWLIST_NEVER_WILDCARDS='^\.(googleapis\.com|google\.com|ubuntu\.com|amazonaws\.com|azure\.com|openai\.com|anthropic\.com)$'

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

# phase1_lan_resolvers — the LAN's IPv4 DNS servers, one per line. Order of trust: LAN_DNS_SERVERS persisted in
# atlas.env by an earlier run (after this step points resolved at 127.0.0.1 the live resolvectl view no longer shows
# the router), then resolvectl's per-link servers for LAN_IFACE, then the DHCP lease file, then the global resolvectl
# list, then the default gateway. Loopback addresses are never a resolver (that would be dnsmasq itself: a loop).
# Everything that forwards names (dnsmasq upstreams, the ufw owner rules, the WireGuard clients' INIT_DNS) is pinned
# to exactly this list.
phase1_lan_resolvers() {
  local list=() gw r
  for r in ${LAN_DNS_SERVERS:-}; do [[ "$r" =~ ^[0-9]+(\.[0-9]+){3}$ && "$r" != 127.* ]] && list+=("$r"); done
  if (( ${#list[@]} == 0 )); then
    mapfile -t list < <(resolvectl dns "$LAN_IFACE" 2>/dev/null | awk -F': ' 'NF>1 {print $2}' | tr ' ' '\n' \
                          | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' || true)
  fi
  if (( ${#list[@]} == 0 )); then
    local idx lease; idx="$(cat "/sys/class/net/$LAN_IFACE/ifindex" 2>/dev/null || echo 0)"
    lease="/run/systemd/netif/leases/$idx"
    [[ -f "$lease" ]] && mapfile -t list < <(awk -F= '$1=="DNS" {print $2}' "$lease" | tr ' ' '\n' \
                                              | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' || true)
  fi
  if (( ${#list[@]} == 0 )); then
    mapfile -t list < <(resolvectl dns 2>/dev/null | awk -F': ' 'NF>1 {print $2}' | tr ' ' '\n' \
                          | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' | sort -u || true)
  fi
  if (( ${#list[@]} == 0 )); then
    gw="$(ip -o route show default 2>/dev/null | awk '{print $3; exit}')"
    [[ -n "$gw" ]] && list=("$gw")
  fi
  (( ${#list[@]} > 0 )) || die "phase1_lan_resolvers: no IPv4 resolver for $LAN_IFACE (LAN_DNS_SERVERS in atlas.env, resolvectl dns, the DHCP lease) and no default gateway"
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

# _allowlist_never_check FILE — die when the rendered allowlist names a host rule §7.1 forbids or a wildcard that
# would re-admit one (the "NEVER listed" group in config/allowlist.txt; Section 16.3 item 6).
_allowlist_never_check() {
  local f="$1" hit
  hit="$(grep -nxFf <(printf '%s\n' "${ATLAS_ALLOWLIST_NEVER[@]}") "$f" || true)"
  [[ -z "$hit" ]] || die "allowlist names a forbidden host (rule §7.1 'nothing cloud', config/allowlist.txt header): $hit"
  hit="$(grep -nE "$ATLAS_ALLOWLIST_NEVER_WILDCARDS" "$f" || true)"
  [[ -z "$hit" ]] || die "allowlist carries a wildcard that would re-admit a forbidden host (rule §7.1): $hit"
}

# _squid_render — render /etc/squid/allowlist.txt and /etc/squid/squid.conf. Called by step 4, by step 6 once the
# docker0 gateway is known (adds its http_port) and by --reload-allowlist.
_squid_render() {
  # Strip comments and blanks from config/allowlist.txt; squid reads one dstdomain per line.
  local src="$ATLAS_DAY1_DIR/config/allowlist.txt"
  [[ -s "$src" ]] || die "config/allowlist.txt is missing"
  sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' -e 's/^[[:space:]]*//; s/[[:space:]]*$//' "$src" \
    | phase1_write_file 644 '' /etc/squid/allowlist.txt
  local n; n="$(wc -l </etc/squid/allowlist.txt)"
  (( n > 10 )) || die "allowlist rendered only $n entries; refusing to lock the node out"
  _allowlist_never_check /etc/squid/allowlist.txt
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
  # Binding (CONVENTIONS §8 "loopback only" + rule §7.1 for containers): 127.0.0.1 always; the docker0 gateway as the
  # one extra listener once step 6 has recorded it in docker.env (DOCKER_GW). The LAN address and the WireGuard bridge
  # are never bound: VPN clients must never use the node as an outbound proxy, and wg-easy itself needs no egress.
  # Clients acl: loopback plus the Docker bridge ranges (where every container's gateway address sits).
  local gw="" extra=""
  if [[ -r "$ATLAS_ETC/docker.env" ]]; then gw="$(awk -F= '$1=="DOCKER_GW" {print $2; exit}' "$ATLAS_ETC/docker.env")"; fi
  if [[ "$gw" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
    extra="http_port $gw:3128"
    # ip_nonlocal_bind: squid starts before docker0 exists at boot and must still bind the gateway address.
    printf '# ATLAS Phase 1 step 6: squid binds the docker0 gateway (%s) before the bridge exists at boot.\nnet.ipv4.ip_nonlocal_bind = 1\n' "$gw" \
      >/etc/sysctl.d/90-atlas-squid.conf
    sysctl -q -w net.ipv4.ip_nonlocal_bind=1 || die "sysctl net.ipv4.ip_nonlocal_bind=1 failed"
  fi
  export SQUID_ALLOWLIST=/etc/squid/allowlist.txt SQUID_CLIENT_NETS="172.16.0.0/12" SQUID_EXTRA_PORTS="$extra"
  render_template "$ATLAS_DAY1_DIR/config/squid.conf.tmpl" /etc/squid/squid.conf SQUID_ALLOWLIST SQUID_CLIENT_NETS SQUID_EXTRA_PORTS
  squid -k parse >/dev/null 2>&1 || die "squid -k parse rejects /etc/squid/squid.conf: $(squid -k parse 2>&1 | tail -n5)"
  # The hash makes any later change to the enforced list visible in the phase log (Section 16.3 item 6).
  log "squid: $n allowlisted domains rendered to /etc/squid/allowlist.txt (sha256 $(sha256sum /etc/squid/allowlist.txt | cut -c1-16)...; listeners: 127.0.0.1${gw:+, $gw})"
}

# _dns_render — /etc/dnsmasq.d/90-atlas.conf from the rendered allowlist: every entry (leading dot stripped; dnsmasq's
# server=/domain/ covers the domain and its subdomains, which is also what squid's dotted form means), the node's own
# DOMAIN (V5 resolves VPN_HOST), ntp.ubuntu.com (timesyncd re-resolution) and the Windows share host when it is a
# name; forwarded to every LAN resolver; everything else NXDOMAIN. Requires /etc/squid/allowlist.txt (_squid_render).
_dns_render() {
  local resolvers=() names=() e r
  mapfile -t resolvers < <(phase1_lan_resolvers)
  # Persist and export the list now: once resolved points at 127.0.0.1 the live resolvectl view no longer shows the
  # router, so every later caller (ufw owner rules, DOCKER-USER, WG INIT_DNS, re-runs) reads LAN_DNS_SERVERS instead.
  ensure_kv "$ATLAS_ETC/atlas.env" LAN_DNS_SERVERS "\"${resolvers[*]}\""
  export LAN_DNS_SERVERS="${resolvers[*]}"
  mapfile -t names < <(sed -e 's/^\.//' /etc/squid/allowlist.txt | grep -E '^[A-Za-z0-9.-]+$' | sort -u)
  (( ${#names[@]} > 10 )) || die "_dns_render: the rendered allowlist has only ${#names[@]} usable names"
  names+=("$DOMAIN" ntp.ubuntu.com)
  local share="${WINDOWS_SHARE:-}"; share="${share#//}"; share="${share%%/*}"
  [[ -n "$share" && ! "$share" =~ ^[0-9]+(\.[0-9]+){3}$ ]] && names+=("$share")
  install -d -m 755 /etc/dnsmasq.d
  {
    echo "# Written by ATLAS Phase 1 step 4 (and --reload-allowlist): the host resolves ONLY the allowlisted names"
    echo "# (Section 12.5, rule §7.1). Forwarded to the LAN resolvers ${resolvers[*]}; everything else is NXDOMAIN."
    echo "port=53"
    echo "listen-address=127.0.0.1"
    echo "bind-interfaces"
    echo "no-resolv"
    echo "no-poll"
    echo "domain-needed"
    echo "bogus-priv"
    echo "no-negcache"
    echo "cache-size=2000"
    echo "log-queries"
    echo "log-facility=DAEMON"
    while read -r e; do
      for r in "${resolvers[@]}"; do echo "server=/$e/$r"; done
    done < <(printf '%s\n' "${names[@]}" | sort -u)
    echo "address=/#/"
  } | phase1_write_file 644 '' /etc/dnsmasq.d/90-atlas.conf
  log "dnsmasq: $(grep -c '^server=' /etc/dnsmasq.d/90-atlas.conf) forwarding rules for $(printf '%s\n' "${names[@]}" | sort -u | wc -l) names -> ${resolvers[*]}; all else NXDOMAIN (sha256 $(sha256sum /etc/dnsmasq.d/90-atlas.conf | cut -c1-16)...)"
}

# _dns_selftest — through resolved: an allowlisted name resolves, a forbidden one is NXDOMAIN, no stray servers.
_dns_selftest() {
  resolvectl flush-caches 2>/dev/null || true
  local ok; ok="$(resolvectl query --legend=no -4 archive.ubuntu.com 2>&1 || true)"
  grep -qE '\b[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\b' <<<"$ok" \
    || die "DNS self-test: archive.ubuntu.com does not resolve through dnsmasq ($ok). Check: systemctl status dnsmasq; resolvectl status; journalctl -u dnsmasq -n 20"
  local bad; bad="$(resolvectl query --legend=no -4 api.openai.com 2>&1 || true)"
  if grep -qE '\b[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\b' <<<"$bad"; then
    die "DNS self-test: api.openai.com RESOLVED ($bad): the host is not using the allowlisting forwarder (resolvectl status; is another DNS server configured on $LAN_IFACE?)"
  fi
  local glob; glob="$(resolvectl dns 2>/dev/null | tr '\n' ' ')"
  grep -q '127.0.0.1' <<<"$glob" || die "DNS self-test: resolved's global DNS is not 127.0.0.1: $glob"
  local link; link="$(resolvectl dns "$LAN_IFACE" 2>/dev/null | awk -F': ' 'NF>1 {print $2}')"
  [[ -z "${link// /}" ]] || warn "DNS self-test: $LAN_IFACE still lists DNS servers ($link); ufw drops resolved's queries to them (only dnsmasq may reach port 53) and the networkd drop-in removes them at the reboot"
  log "DNS self-test: allowlisted name resolves, forbidden name is NXDOMAIN, resolved -> 127.0.0.1 (dnsmasq)"
}

# _dns_forwarder_install — dnsmasq as the host's allowlisting resolver; systemd-resolved pointed at it; the LAN link's
# DHCP-supplied DNS switched off (drop-in for the next boot/renewal, resolvectl for right now).
_dns_forwarder_install() {
  _dns_render
  if [[ "$(dpkg-query -W -f='${Status}' dnsmasq 2>/dev/null || true)" != "install ok installed" ]]; then
    # The package's postinst starts the service with Debian's defaults (port 53 on every address), which collides with
    # systemd-resolved's stub and would fail the install; policy-rc.d blocks that start and our config is in place
    # before the first real start. Pre-proxy install from the archive (header), like squid itself.
    printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d; chmod 755 /usr/sbin/policy-rc.d
    # apt_install dies on failure: the EXIT trap makes sure the install-time policy never outlives this call (a
    # leftover policy-rc.d would silently stop every later service start on the node).
    trap 'rm -f /usr/sbin/policy-rc.d' EXIT
    apt_install dnsmasq
    rm -f /usr/sbin/policy-rc.d
    trap - EXIT
  fi
  id -u dnsmasq >/dev/null 2>&1 || die "the dnsmasq package did not create the 'dnsmasq' user (the ufw owner rule needs it)"
  # /etc/default/dnsmasq: never read a resolvconf-provided upstream list (we forward by name only; VERIFIED
  # init-system-common: IGNORE_RESOLVCONF=yes leaves RESOLV_CONF unset). CONFIG_DIR (the package default) is what
  # makes /etc/dnsmasq.d/*.conf effective, so it must stay.
  [[ -f /etc/default/dnsmasq ]] || : >/etc/default/dnsmasq
  ensure_kv /etc/default/dnsmasq IGNORE_RESOLVCONF yes
  grep -q '^CONFIG_DIR=' /etc/default/dnsmasq || ensure_kv /etc/default/dnsmasq CONFIG_DIR "/etc/dnsmasq.d,.dpkg-dist,.dpkg-old,.dpkg-new"
  dnsmasq --test -C /etc/dnsmasq.conf -7 /etc/dnsmasq.d,.dpkg-dist,.dpkg-old,.dpkg-new >/dev/null 2>&1 \
    || die "dnsmasq --test rejects the configuration: $(dnsmasq --test -C /etc/dnsmasq.conf -7 /etc/dnsmasq.d 2>&1 | tail -n3)"
  systemctl enable dnsmasq >/dev/null
  systemctl restart dnsmasq || die "dnsmasq failed to start (bound to 127.0.0.1:53; resolved's stub is 127.0.0.53): $(journalctl -u dnsmasq -n 10 --no-pager)"
  # systemd-resolved: global upstream 127.0.0.1 for every name; the compiled-in FallbackDNS (Cloudflare, Google,
  # Quad9) switched off so no query can ever go there; no DoT/LLMNR/mDNS surprises.
  install -d -m 755 /etc/systemd/resolved.conf.d
  cat >/etc/systemd/resolved.conf.d/90-atlas.conf <<'CONF'
# ATLAS Phase 1 step 4 (Section 12.5): every host lookup goes to the allowlisting dnsmasq forwarder on 127.0.0.1.
[Resolve]
DNS=127.0.0.1
Domains=~.
FallbackDNS=
DNSOverTLS=no
DNSSEC=no
LLMNR=no
MulticastDNS=no
CONF
  # The LAN link must stop handing resolved the router's address. networkd (netplan on Ubuntu Server): a drop-in next
  # to the .network file that manages LAN_IFACE (drop-ins in /etc apply to a /run file too), effective at the reboot
  # this step ends with, without a `networkctl reconfigure` over the Wi-Fi session the step runs on (R9).
  local netfile; netfile="$(networkctl status "$LAN_IFACE" 2>/dev/null | awk -F': ' '/Network File:/ {print $2; exit}' | tr -d ' ')"
  if [[ -n "$netfile" && "$netfile" != n/a ]]; then
    local dd; dd="/etc/systemd/network/$(basename "$netfile").d"
    install -d -m 755 "$dd"
    printf '# ATLAS Phase 1 step 4: the DHCP/RA-supplied DNS servers are never used directly (Section 12.5); dnsmasq forwards.\n[Network]\nDNS=127.0.0.1\nDomains=~.\n[DHCPv4]\nUseDNS=false\n[DHCPv6]\nUseDNS=false\n[IPv6AcceptRA]\nUseDNS=false\n' \
      >"$dd/90-atlas-dns.conf"
    log "networkd drop-in $dd/90-atlas-dns.conf (UseDNS=false; effective at the reboot)"
  elif systemctl is-active --quiet NetworkManager 2>/dev/null; then
    # UNVERIFIED (NetworkManager is not expected on this node; step 4 refuses to let Cockpit pull it in): main.dns=none
    # plus systemd-resolved=false stops NM from pushing the DHCP DNS to resolved.
    install -d -m 755 /etc/NetworkManager/conf.d
    printf '[main]\ndns=none\nsystemd-resolved=false\n' >/etc/NetworkManager/conf.d/90-atlas-dns.conf
    warn "NetworkManager manages $LAN_IFACE: wrote /etc/NetworkManager/conf.d/90-atlas-dns.conf (UNVERIFIED) so it stops pushing DHCP DNS to resolved"
  else
    warn "could not find the networkd .network file for $LAN_IFACE (networkctl status); the DHCP DNS is cleared at run time only and may return at the next lease renewal (ufw drops it either way)"
  fi
  systemctl restart systemd-resolved || die "systemd-resolved failed to restart: $(journalctl -u systemd-resolved -n 10 --no-pager)"
  # Right now (VERIFIED resolvectl(1): dns/domain take a single empty string to clear the per-link list).
  resolvectl dns "$LAN_IFACE" '' 2>/dev/null || warn "resolvectl dns $LAN_IFACE '' refused (per-link DNS not cleared until the reboot)"
  resolvectl domain "$LAN_IFACE" '' 2>/dev/null || true
  _dns_selftest
}

# phase1_reload_allowlist [FILE] — the lightweight path after an allowlist edit (config/allowlist.txt comment):
# copy FILE over $ATLAS_DAY1_DIR/config/allowlist.txt when given, re-render squid and dnsmasq, reload both. No ufw
# reset, no dist-upgrade, no reboot (all of which a `--force 04` would do). Runs without load_env: DOMAIN and
# WINDOWS_SHARE are read from atlas.env here, LAN_DNS_SERVERS/LAN_IFACE likewise.
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
  if [[ -z "${DOMAIN:-}" && -r "$ATLAS_ETC/atlas.env" ]]; then
    # shellcheck disable=SC1091  # /etc/atlas/atlas.env, KEY=VALUE lines only (CONVENTIONS.md §3)
    source "$ATLAS_ETC/atlas.env"
  fi
  [[ -n "${DOMAIN:-}" && -n "${LAN_IFACE:-}" ]] || die "DOMAIN/LAN_IFACE are not set ($ATLAS_ETC/atlas.env unreadable?)"
  _squid_render
  systemctl reload squid || systemctl restart squid || die "squid failed to reload: systemctl status squid"
  if [[ -f /etc/dnsmasq.d/90-atlas.conf ]]; then
    _dns_render
    systemctl restart dnsmasq || die "dnsmasq failed to restart after the re-render: journalctl -u dnsmasq -n 20"
    _dns_selftest
  fi
  log "allowlist reloaded into squid and dnsmasq ($(wc -l </etc/squid/allowlist.txt) entries); no firewall or reboot involved"
}

_proxy_environment() {
  # $ATLAS_ETC/proxy.env is the contract lib/common.sh's proxy_env reads (KEY=VALUE, sourceable, EnvironmentFile-able).
  # The *_TELEMETRY / DO_NOT_TRACK / PIP / NPM / DOCKER keys keep every host-side tool from phoning home to allowlisted
  # hosts (huggingface.co/api/telemetry, pypi.org version checks, npm update-notifier, docker CLI hints): rule §7.1
  # forbids telemetry even to hosts the proxy admits. HF_HUB_DISABLE_IMPLICIT_TOKEN stops huggingface_hub attaching a
  # cached login token to every request on its own (rule §7.2: a secret travels only where a script sends it).
  # lib/common.sh's proxy_env exports only the proxy keys from this file (its contract); the same keys are therefore
  # also placed in environment.d, profile.d and systemd's DefaultEnvironment.
  local telemetry_off=(
    "HF_HUB_ENABLE_HF_TRANSFER=0" "HF_HUB_DISABLE_TELEMETRY=1" "HF_HUB_DISABLE_IMPLICIT_TOKEN=1" "DO_NOT_TRACK=1"
    "PIP_DISABLE_PIP_VERSION_CHECK=1" "NPM_CONFIG_UPDATE_NOTIFIER=false" "DOCKER_CLI_HINTS=false"
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

# _ufw_docker_user_base FILE V6 — pre-create the DOCKER-USER chain with its terminal DROPs in ufw's after(6).rules
# so the chain exists from `ufw enable` at boot, BEFORE dockerd starts restart:unless-stopped containers.
# atlas-docker-egress.service (step 6) then flushes and rebuilds the full rule set (DNAT accept for published ports,
# logging) after dockerd is up; if that unit ever fails, this base still drops everything leaving a bridge for the
# LAN interface. Containers get NO port 53 (header: nothing behind the proxy needs DNS); the WireGuard bridge may
# reach the LAN subnet and the LAN resolvers because that is the Principal's phone's traffic (INIT_DNS), not a
# container's.
# REVERSE INTERACTION (fix round): `ufw reload`, `ufw enable` and `ufw --force reset` run iptables-restore over
# after.rules, which flushes the live DOCKER-USER chain back to this baseline (the DNAT accepts and the logging are
# gone until docker restarts: VPN clients lose ntfy and Open WebUI, availability only, the fallback is stricter).
# So every `ufw reload|enable|reset` must be followed by `systemctl restart atlas-docker-egress.service`: _ufw_rules
# does it itself, and a ufw.service drop-in (below) does it at boot and on `systemctl restart ufw`.
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
        echo "-A DOCKER-USER -i $ATLAS_WG_BRIDGE -o $lan -d $r -p udp --dport 53 -j RETURN"
        echo "-A DOCKER-USER -i $ATLAS_WG_BRIDGE -o $lan -d $r -p tcp --dport 53 -j RETURN"
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
  # Persist the resolver list for the standalone DOCKER-USER script and re-runs (atlas.env key LAN_DNS_SERVERS; derived, not a secret).
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
  # Containers reach the allowlist proxy at the docker0 gateway (INPUT; squid binds 127.0.0.1 and that gateway only,
  # config/squid.conf.tmpl). The LAN interface is denied FIRST so that a LAN that itself sits in 172.16.0.0/12 can
  # never reach squid; the WireGuard bridge is not a squid client at all.
  ufw deny  in on "$lan" to any port 3128 proto tcp comment 'squid never from the LAN' >/dev/null
  ufw allow in on docker0 to any port 3128 proto tcp comment 'squid from docker0' >/dev/null
  ufw allow in from 172.16.0.0/12 to any port 3128 proto tcp comment 'squid from compose bridges' >/dev/null
  # Outbound: NTP to the configured servers ONLY, DHCP, the LAN itself, the Docker bridges. DNS is NOT opened here:
  # only the dnsmasq user may reach the resolvers' port 53 (owner match in before.rules, below), so no process on the
  # host, resolved included, can ask the router about a name the allowlist does not carry (Section 12.5). HTTP(S)
  # only for the squid user (before.rules). No outbound rule for UDP $WG_PORT: wg0 lives inside the wg-easy
  # container, whose replies traverse FORWARD/DOCKER-USER under conntrack, never the host OUTPUT chain.
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
  # Owner-matched egress (before.rules, chain ufw-before-output): only the 'proxy' user (squid) may open 80/443, only
  # the 'dnsmasq' user may open 53 to the LAN resolvers. ICMP: echo-request and the error types to the LAN only (V1
  # pings the gateway); never "any ICMP anywhere".
  local uid dns_uid; uid="$(id -u proxy)" || die "user 'proxy' (squid) does not exist"
  dns_uid="$(id -u dnsmasq)" || die "user 'dnsmasq' does not exist (the DNS forwarder must be installed before the firewall)"
  local f chain tmp
  for f in /etc/ufw/before.rules /etc/ufw/before6.rules; do
    [[ -f "$f" ]] || continue
    # The IPv6 file uses ufw6-* chain names and ipv6-icmp (VERIFIED ufw layout).
    if [[ "$f" == *before6* ]]; then chain=ufw6-before-output; else chain=ufw-before-output; fi
    sed -i '/^# ATLAS:/,/ATLAS-END$/d' "$f"                                  # remove an earlier insertion (ufw reset already restored the default file)
    grep -q "^-A $chain -o lo -j ACCEPT" "$f" || die "$f has no '-A $chain -o lo -j ACCEPT' anchor line; the ufw layout changed"
    tmp="$(mktemp)"
    {
      echo "# ATLAS: only the squid proxy user may open outbound HTTP/HTTPS and only dnsmasq may reach the LAN resolvers (Section 12.5); ICMP to the LAN only (V1)"
      echo "-A $chain -p tcp -m multiport --dports 80,443 -m owner --uid-owner $uid -j ACCEPT"
      if [[ "$chain" == ufw6-before-output ]]; then
        local t
        for t in 133 134 135 136 137; do echo "-A $chain -p ipv6-icmp --icmpv6-type $t -j ACCEPT"; done
        echo "-A $chain -p ipv6-icmp --icmpv6-type 128 -d fe80::/10 -j ACCEPT -m comment --comment ATLAS-END"
      else
        for r in "${ATLAS_LAN_RESOLVERS[@]}"; do
          echo "-A $chain -o $lan -d $r -p udp --dport 53 -m owner --uid-owner $dns_uid -j ACCEPT"
          echo "-A $chain -o $lan -d $r -p tcp --dport 53 -m owner --uid-owner $dns_uid -j ACCEPT"
        done
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
  # ufw.service drop-in: at boot and on `systemctl restart ufw`, rebuild the full DOCKER-USER set after the baseline
  # (see _ufw_docker_user_base). `-` keeps a missing script (before step 6) or a failure from failing ufw itself.
  install -d -m 755 /etc/systemd/system/ufw.service.d
  printf '# ATLAS Phase 1 step 4: the ufw after.rules reset DOCKER-USER to the baseline; re-apply the full set (phase1/06-docker.sh).\n[Service]\nExecStartPost=-/usr/local/sbin/atlas-docker-egress\n' \
    >/etc/systemd/system/ufw.service.d/atlas.conf
  systemctl daemon-reload
  ufw --force enable >/dev/null
  if [[ -x /usr/local/sbin/atlas-docker-egress ]] && systemctl is-active --quiet docker 2>/dev/null; then
    /usr/local/sbin/atlas-docker-egress >/dev/null || warn "atlas-docker-egress failed after ufw enable; run: systemctl restart atlas-docker-egress.service"
  fi
  log "ufw enabled: default deny in/out/routed; LAN=$lan $net; WG bridge=$wgbr $wgnet; DNS ${ATLAS_LAN_RESOLVERS[*]} (uid dnsmasq only); NTP ${ntp[*]}"
  ufw status verbose | sed 's/^/    /'
}

_proxy_selftest() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --proxy "$ATLAS_PROXY_URL" https://archive.ubuntu.com/ || true)"
  [[ "$code" == 200 ]] || die "the allowlist proxy cannot reach https://archive.ubuntu.com/ (HTTP '$code'). Check: systemctl status squid; tail /var/log/squid/cache.log; resolvectl query archive.ubuntu.com; ufw status"
  if curl -sS -o /dev/null --noproxy '*' --max-time 6 https://archive.ubuntu.com/ 2>/dev/null; then
    die "direct egress (bypassing the proxy) still works; ufw is not enforcing default deny outgoing"
  fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --proxy "$ATLAS_PROXY_URL" https://example.com/ || true)"
  [[ "$code" == 403 || "$code" == 000 ]] || die "a non-allowlisted host answered through the proxy (HTTP $code); squid's allowlist is not applied"
  # IP-literal destinations must be refused by squid itself (fix round blocker: without `dstdomain -n` squid would
  # match the PTR name of the address against the allowlist). %{http_connect} is the proxy's answer to CONNECT.
  code="$(curl -sS -o /dev/null -w '%{http_connect}' --max-time 20 --proxy "$ATLAS_PROXY_URL" https://1.1.1.1/ 2>/dev/null || true)"
  [[ "$code" == 403 ]] || die "squid answered CONNECT 1.1.1.1:443 with '$code', expected 403: the ip_literal acl / dstdomain -n of config/squid.conf.tmpl is not in effect (squid -k parse; grep ip_literal /etc/squid/squid.conf)"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --proxy "$ATLAS_PROXY_URL" http://1.1.1.1/ || true)"
  [[ "$code" == 403 ]] || die "squid answered GET http://1.1.1.1/ with '$code', expected 403 (ip_literal acl)"
  log "proxy self-test: allowlisted 200, direct blocked, non-allowlisted denied, IP-literal CONNECT and GET denied (403)"
}

# _disable_beacons — Ubuntu Server's own phone-home paths (rule §7.1 "no telemetry from any installed component"):
# motd-news and apt-news (motd.ubuntu.com), the Ubuntu Pro timers (contracts.canonical.com, esm.ubuntu.com),
# apport/whoopsie crash uploads (daisy.ubuntu.com), fwupd's daily LVFS metadata refresh (cdn.fwupd.org), the
# release-upgrade check (changelogs.ubuntu.com meta-release, Prompt=never), popularity-contest, ubuntu-report and
# snapd's refresh loop (Section 3.1 takes Firefox as Mozilla's .deb, so nothing here needs snap).
# Deliberately LEFT at the distro default (fix round): unattended-upgrades and the apt-daily timers. The baseline
# never asks for OS security updates to be switched off on a node with an internet-facing UDP port, and they run
# through the proxy (apt.conf.d/90atlas-proxy; security.ubuntu.com is allowlisted). An earlier run's
# 90atlas-no-auto override is removed so the default applies again.
_disable_beacons() {
  if [[ -f /etc/default/motd-news ]]; then sed -i 's/^ENABLED=.*/ENABLED=0/' /etc/default/motd-news
  else printf 'ENABLED=0\n' >/etc/default/motd-news; fi
  if command -v pro >/dev/null 2>&1; then pro config set apt_news=false >/dev/null 2>&1 || true; fi
  local u
  for u in motd-news.timer motd-news.service apt-news.service esm-cache.service ua-timer.timer ua-timer.service \
           apport.service apport-autoreport.timer apport-autoreport.service whoopsie.service whoopsie.path \
           fwupd-refresh.timer fwupd-refresh.service; do
    systemctl disable --now "$u" >/dev/null 2>&1 || true
  done
  systemctl mask apport.service whoopsie.service >/dev/null 2>&1 || true
  rm -f /etc/apt/apt.conf.d/90atlas-no-auto
  for u in apt-daily.timer apt-daily-upgrade.timer; do
    if systemctl list-unit-files "$u" 2>/dev/null | grep -q "^$u"; then systemctl enable "$u" >/dev/null 2>&1 || true; fi
  done
  # Release-upgrade beacon (/etc/update-motd.d/91-release-upgrade -> check-new-release fetches meta-release-lts).
  if [[ -f /etc/update-manager/release-upgrades ]]; then
    if grep -q '^Prompt=' /etc/update-manager/release-upgrades; then sed -i 's/^Prompt=.*/Prompt=never/' /etc/update-manager/release-upgrades
    else printf 'Prompt=never\n' >>/etc/update-manager/release-upgrades; fi
  else
    install -d -m 755 /etc/update-manager
    printf '[DEFAULT]\nPrompt=never\n' >/etc/update-manager/release-upgrades
  fi
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
  log "beacons off (rule §7.1): motd-news, apt-news, Pro timers, apport/whoopsie (masked), fwupd-refresh, release-upgrade prompt; purged/masked: ${purge[*]:-none}; unattended security updates left at the distro default (through the proxy)"
}

_grub_params() {
  # Section 3.3 / Appendix B: the three parameters in ATLAS_GRUB_PARAMS, all gated by V3a (header).
  # A drop-in under /etc/default/grub.d/ is sourced by grub-mkconfig after /etc/default/grub (VERIFIED), so the
  # variable is extended without editing the distro file, and rewriting the whole drop-in makes it idempotent.
  local all="$ATLAS_GRUB_PARAMS"
  install -d -m 755 /etc/default/grub.d
  {
    echo "# A.T.L.A.S. Section 3.3 / Appendix B (v0.3.1): GTT sized for 192 GB unified memory and the kernel 7.x GPU watchdog."
    echo "# amdgpu.gttsize is deprecated but honoured (expect one drm deprecation warning); ttm.pages_limit is the parameter of record;"
    echo "# amdgpu.lockup_timeout keeps DeepSeek V4 from a Vulkan DeviceLost (S8, llama.cpp issue #25664). All three are gated by V3a."
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
    # `|| true` inside the pipeline: with pipefail a zero-match grep -o would otherwise abort the step through the
    # generic ERR trap instead of reaching the explicit message below.
    local c
    c="$({ grep -m1 -E '^[[:space:]]*linux[[:space:]]' /boot/grub/grub.cfg || true; } | { grep -o -F -- "$p" || true; } | wc -l)"
    (( c == 1 )) || die "$p appears $c times on the first kernel line of grub.cfg (expected exactly once)"
  done
  log "GRUB: $all (applies at the reboot; V3a checks /proc/cmdline and the live module parameters in step 5)"
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
  # Minutes after a fresh install boots, apt-daily/unattended-upgrades commonly hold the dpkg lock; apt-get gives up
  # at once by default (DPkg::Lock::Timeout 0). This config applies to every apt-get in every phase, lib/common.sh's
  # apt_install included, without editing it.
  install -d -m 755 /etc/apt/apt.conf.d
  printf '// ATLAS Phase 1 step 4: wait for a held dpkg lock (apt-daily on a fresh boot) instead of failing at once.\nDPkg::Lock::Timeout "300";\n' \
    >/etc/apt/apt.conf.d/90atlas-lock-timeout
  # The one unavoidable pre-proxy install (header): squid, dnsmasq and their rendering tools. ufw, curl and
  # ca-certificates are on the Server ISO and cost nothing to list; apt_install skips what is present.
  apt_install ufw squid jq curl ca-certificates gettext-base

  # 1. Proxy and its environment (rule §7.1 from here on), then the allowlisting DNS forwarder (header).
  _squid_render
  systemctl enable --now squid >/dev/null
  systemctl reload squid || systemctl restart squid
  _proxy_environment
  _dns_forwarder_install
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
