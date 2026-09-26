#!/usr/bin/env bash
# phase1/docker-egress-rules.sh — installed as /usr/local/sbin/atlas-docker-egress by phase1/06-docker.sh and run by
# systemd/atlas-docker-egress.service after dockerd starts (and again whenever docker.service restarts, PartOf=).
#
# Why: Docker's published ports and container forwarding bypass ufw; only the DOCKER-USER chain, evaluated before
# Docker's own FORWARD rules, can enforce Section 12.5 for containers (adjudicated conflict 4). Containers get no
# direct internet: they reach the allowlist proxy at the bridge gateway (172.17.0.1:3128 on docker0, or the compose
# bridge gateway), which is INPUT traffic and is admitted by ufw. Everything else leaving a bridge for the LAN
# interface is logged and dropped.
#
# Allowed through FORWARD:
#   * replies (RELATED,ESTABLISHED), so inbound WireGuard handshakes to the wg-easy container get answered
#   * br-atlas-wg -> the LAN subnet: VPN clients (masqueraded as 10.42.42.42) reach the node and LAN like a LAN device
#   * any bridge -> LAN DNS (udp/tcp 53): containers on the default bridge resolve through the LAN resolver
#   * the wg-easy container's own UDP 51820 replies (belt and braces beside conntrack)
# Reads LAN_IFACE and LAN_CIDR from /etc/atlas/atlas.env (written by load_env). Idempotent: flushes and rebuilds.
# Standalone by design (no lib/common.sh): it must work with nothing but iptables and the env file.
set -euo pipefail

ENV_FILE="${ATLAS_ENV_FILE:-/etc/atlas/atlas.env}"
[[ -r "$ENV_FILE" ]] || { echo "atlas-docker-egress: $ENV_FILE is missing" >&2; exit 1; }
# shellcheck disable=SC1090  # /etc/atlas/atlas.env, KEY=VALUE lines only (CONVENTIONS.md §3)
source "$ENV_FILE"
LAN_IFACE="${LAN_IFACE:-$(ip -o route show default | awk '{print $5; exit}')}"
[[ -n "$LAN_IFACE" ]] || { echo "atlas-docker-egress: cannot determine LAN_IFACE" >&2; exit 1; }
LAN_CIDR="${LAN_CIDR:-$(ip -4 -o route show dev "$LAN_IFACE" proto kernel | awk '{print $1; exit}')}"
[[ -n "$LAN_CIDR" ]] || { echo "atlas-docker-egress: cannot determine LAN_CIDR" >&2; exit 1; }
WG_BRIDGE="${WG_BRIDGE:-br-atlas-wg}"
WG_PORT="${WG_PORT:-51820}"

iptables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -F DOCKER-USER
iptables -w -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -d "$LAN_CIDR" -j RETURN
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -p udp --sport "$WG_PORT" -j RETURN
for br in 'br-+' docker0; do
  iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -d "$LAN_CIDR" -p udp --dport 53 -j RETURN
  iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -d "$LAN_CIDR" -p tcp --dport 53 -j RETURN
  iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -m limit --limit 5/min -j LOG --log-prefix "[ATLAS docker egress denied] "
  iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -j DROP
done
iptables -w -A DOCKER-USER -j RETURN

# IPv6: containers get no IPv6 egress at all (the compose networks are IPv4-only; DISABLE_IPV6 on wg-easy).
if command -v ip6tables >/dev/null; then
  ip6tables -w -N DOCKER-USER 2>/dev/null || true
  ip6tables -w -F DOCKER-USER
  ip6tables -w -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  ip6tables -w -A DOCKER-USER -i 'br-+' -o "$LAN_IFACE" -j DROP
  ip6tables -w -A DOCKER-USER -i docker0 -o "$LAN_IFACE" -j DROP
  ip6tables -w -A DOCKER-USER -j RETURN
fi
echo "atlas-docker-egress: DOCKER-USER rules installed (lan=$LAN_IFACE $LAN_CIDR, wg bridge=$WG_BRIDGE)"
