#!/usr/bin/env bash
# phase1/docker-egress-rules.sh — installed as /usr/local/sbin/atlas-docker-egress by phase1/06-docker.sh and run
# (a) as ExecStartPre of docker.service (drop-in written by step 6), so the rules exist before any container starts,
# (b) by systemd/atlas-docker-egress.service after dockerd is up (and again whenever docker restarts, PartOf=), and
# (c) as ExecStartPost of ufw.service (drop-in written by step 4) and by step 4's _ufw_rules after `ufw enable`.
# ufw's after.rules (phase1/04-system.sh) pre-creates the DOCKER-USER chain with the same terminal DROPs at boot, so
# there is no window in which a container has unrestricted egress even if this script never ran.
# INTERACTION WITH ufw reload (fix round 3, corrected): ufw restores after.rules with `iptables-restore -n` (VERIFIED in
# ufw 0.36.2, usr/lib/ufw/ufw-init-functions). On a cold boot this creates DOCKER-USER with the baseline before dockerd
# exists; on a later `ufw reload|enable|reset` the existing chain is neither flushed nor recreated, so the baseline is
# appended after this script's final `-j RETURN` (harmless, unreachable duplicates) and the full set stays in force.
# Restart atlas-docker-egress.service afterwards anyway so the chain is rebuilt cleanly: (c) covers boot and
# `systemctl restart ufw`, _ufw_rules covers step 4; a bare `ufw reload` from a shell is followed by hand.
#
# Why: Docker's published ports and container forwarding bypass ufw; only the DOCKER-USER chain, evaluated before
# Docker's own FORWARD rules, can enforce Section 12.5 for containers (adjudicated conflict 4). Containers get no
# direct internet: they reach the allowlist proxy at the docker0 gateway (172.17.0.1:3128, bound by squid; a local
# host address reachable from every compose bridge too), which is INPUT traffic and is admitted by ufw. Everything
# else leaving a bridge for ANY non-bridge interface is logged and dropped (fix round 3: interface-agnostic, so a
# second uplink such as Ethernet beside Wi-Fi, a USB tether or a host wg/tun gives containers no unlogged way out;
# Docker's own `-i docker0 ! -o docker0 -j ACCEPT` sits behind DOCKER-USER and never sees the packet), DNS INCLUDED
# (fix round): nothing behind the
# proxy needs to resolve a name (apt, pip, curl, huggingface_hub and dockerd send the hostname to squid; Docker's
# embedded 127.0.0.11 resolves compose service names locally), and the router's recursive resolver would otherwise
# answer any name, i.e. be a data channel around the allowlist (Section 12.5 "everything else denied and logged").
#
# Allowed through FORWARD:
#   * replies (RELATED,ESTABLISHED), so inbound WireGuard handshakes to the wg-easy container get answered; roaming
#     peers create a fresh conntrack entry with their first packet, so no "UDP sport 51820 to anywhere" rule is needed
#   * br-atlas-wg -> published ports (conntrack state DNAT): VPN clients reach ntfy, Open WebUI and every other
#     service published on the LAN address exactly as a LAN device does; Docker's inter-bridge isolation would
#     otherwise drop the DNATed hop from the WireGuard bridge onto the service's own bridge (research item 9)
#   * br-atlas-wg -> the LAN subnet: VPN clients (masqueraded as 10.42.42.42) reach the node and LAN like a LAN device
#   * br-atlas-wg -> the pinned LAN resolvers (udp/tcp 53) ONLY: that is the Principal's phone's DNS (WG-Easy INIT_DNS
#     hands clients the LAN resolver), not a container's; no other bridge gets port 53 anywhere.
# Reads LAN_IFACE and LAN_CIDR from /etc/atlas/atlas.env and LAN_DNS_SERVERS from /etc/atlas/network.env (the
# Phase-1-owned derived-settings file step 4 writes; fallbacks: the DHCP lease, then the default gateway; never
# resolvectl, which shows 127.0.0.1 once step 4 has pointed resolved at dnsmasq). Idempotent: flushes and rebuilds.
# Standalone by design (no lib/common.sh): it must work with nothing but iptables and the env file.
set -euo pipefail

ENV_FILE="${ATLAS_ENV_FILE:-/etc/atlas/atlas.env}"
[[ -r "$ENV_FILE" ]] || { echo "atlas-docker-egress: $ENV_FILE is missing" >&2; exit 1; }
# shellcheck disable=SC1090  # /etc/atlas/atlas.env, KEY=VALUE lines only (CONVENTIONS.md §3)
source "$ENV_FILE"
NET_FILE="${ATLAS_NETWORK_ENV_FILE:-/etc/atlas/network.env}"
if [[ -r "$NET_FILE" ]]; then
  # shellcheck disable=SC1090  # /etc/atlas/network.env, KEY=VALUE lines written by phase1/04-system.sh
  source "$NET_FILE"
fi
LAN_IFACE="${LAN_IFACE:-$(ip -o route show default | awk '{print $5; exit}')}"
[[ -n "$LAN_IFACE" ]] || { echo "atlas-docker-egress: cannot determine LAN_IFACE" >&2; exit 1; }
LAN_CIDR="${LAN_CIDR:-$(ip -4 -o route show dev "$LAN_IFACE" proto kernel | awk '{print $1; exit}')}"
[[ -n "$LAN_CIDR" ]] || { echo "atlas-docker-egress: cannot determine LAN_CIDR" >&2; exit 1; }
WG_BRIDGE="${WG_BRIDGE:-br-atlas-wg}"

resolvers=()
for r in ${LAN_DNS_SERVERS:-}; do [[ "$r" =~ ^[0-9]+(\.[0-9]+){3}$ && "$r" != 127.* ]] && resolvers+=("$r"); done
if (( ${#resolvers[@]} == 0 )); then
  idx="$(cat "/sys/class/net/$LAN_IFACE/ifindex" 2>/dev/null || echo 0)"
  if [[ -f "/run/systemd/netif/leases/$idx" ]]; then
    while read -r r; do [[ "$r" =~ ^[0-9]+(\.[0-9]+){3}$ && "$r" != 127.* ]] && resolvers+=("$r"); done \
      < <(awk -F= '$1=="DNS" {print $2}' "/run/systemd/netif/leases/$idx" | tr ' ' '\n')
  fi
fi
if (( ${#resolvers[@]} == 0 )); then
  gw="$(ip -o route show default | awk '{print $3; exit}')"
  [[ -n "$gw" ]] && resolvers+=("$gw")
fi
(( ${#resolvers[@]} > 0 )) || { echo "atlas-docker-egress: no LAN resolver (LAN_DNS_SERVERS, the DHCP lease, default route)" >&2; exit 1; }

iptables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -F DOCKER-USER
iptables -w -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o 'br-+'  -m conntrack --ctstate DNAT -j ACCEPT
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o docker0 -m conntrack --ctstate DNAT -j ACCEPT
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -d "$LAN_CIDR" -j RETURN
for r in "${resolvers[@]}"; do
  iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -d "$r" -p udp --dport 53 -j RETURN
  iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -d "$r" -p tcp --dport 53 -j RETURN
done
# Terminal rules, interface-agnostic (header): bridge-to-bridge traffic is handed back to Docker's own isolation
# chains (RETURN); anything else leaving a bridge, whatever the egress interface, is logged and dropped. iptables takes
# one -o per rule, hence the two RETURNs before the catch-all.
for br in 'br-+' docker0; do
  iptables -w -A DOCKER-USER -i "$br" -o 'br-+'  -j RETURN
  iptables -w -A DOCKER-USER -i "$br" -o docker0 -j RETURN
  iptables -w -A DOCKER-USER -i "$br" -m limit --limit 5/min -j LOG --log-prefix "[ATLAS docker egress denied] "
  iptables -w -A DOCKER-USER -i "$br" -j DROP
done
iptables -w -A DOCKER-USER -j RETURN

# IPv6: containers get no IPv6 egress at all, on any interface (the compose networks are IPv4-only; DISABLE_IPV6 on wg-easy).
if command -v ip6tables >/dev/null; then
  ip6tables -w -N DOCKER-USER 2>/dev/null || true
  ip6tables -w -F DOCKER-USER
  ip6tables -w -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  ip6tables -w -A DOCKER-USER -i 'br-+' -j DROP
  ip6tables -w -A DOCKER-USER -i docker0 -j DROP
  ip6tables -w -A DOCKER-USER -j RETURN
fi
echo "atlas-docker-egress: DOCKER-USER rules installed (lan=$LAN_IFACE $LAN_CIDR, wg bridge=$WG_BRIDGE, phone dns=${resolvers[*]}; containers: proxy only, no DNS, no egress on any interface)"
