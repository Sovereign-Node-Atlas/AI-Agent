#!/usr/bin/env bash
# phase1/docker-egress-rules.sh — installed as /usr/local/sbin/atlas-docker-egress by phase1/06-docker.sh and run
# (a) as ExecStartPre of docker.service (drop-in written by step 6), so the rules exist before any container starts,
# and (b) by systemd/atlas-docker-egress.service after dockerd is up (and again whenever docker restarts, PartOf=).
# ufw's after.rules (phase1/04-system.sh) pre-creates the DOCKER-USER chain with the same terminal DROPs at boot, so
# there is no window in which a container has unrestricted egress even if this script never ran.
#
# Why: Docker's published ports and container forwarding bypass ufw; only the DOCKER-USER chain, evaluated before
# Docker's own FORWARD rules, can enforce Section 12.5 for containers (adjudicated conflict 4). Containers get no
# direct internet: they reach the allowlist proxy at the bridge gateway (172.17.0.1:3128 on docker0, or the compose
# bridge gateway), which is INPUT traffic and is admitted by ufw. Everything else leaving a bridge for the LAN
# interface is logged and dropped.
#
# Allowed through FORWARD:
#   * replies (RELATED,ESTABLISHED), so inbound WireGuard handshakes to the wg-easy container get answered; roaming
#     peers create a fresh conntrack entry with their first packet, so no "UDP sport 51820 to anywhere" rule is needed
#   * br-atlas-wg -> published ports (conntrack state DNAT): VPN clients reach ntfy, Open WebUI and every other
#     service published on the LAN address exactly as a LAN device does; Docker's inter-bridge isolation would
#     otherwise drop the DNATed hop from the WireGuard bridge onto the service's own bridge (research item 9)
#   * br-atlas-wg -> the LAN subnet: VPN clients (masqueraded as 10.42.42.42) reach the node and LAN like a LAN device
#   * any bridge -> the pinned LAN resolvers (udp/tcp 53) ONLY: never DNS to arbitrary hosts (a tunnel around the
#     allowlist). daemon.json "dns" (step 6) hands containers the same list, so nothing is silently dropped.
# Reads LAN_IFACE, LAN_CIDR and LAN_DNS_SERVERS from /etc/atlas/atlas.env (LAN_DNS_SERVERS written by step 4;
# fallbacks: resolvectl, then the default gateway). Idempotent: flushes and rebuilds.
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

resolvers=()
for r in ${LAN_DNS_SERVERS:-}; do [[ "$r" =~ ^[0-9]+(\.[0-9]+){3}$ ]] && resolvers+=("$r"); done
if (( ${#resolvers[@]} == 0 )) && command -v resolvectl >/dev/null; then
  while read -r r; do resolvers+=("$r"); done < <(resolvectl dns "$LAN_IFACE" 2>/dev/null | awk -F': ' 'NF>1 {print $2}' | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)
fi
if (( ${#resolvers[@]} == 0 )); then
  gw="$(ip -o route show default | awk '{print $3; exit}')"
  [[ -n "$gw" ]] && resolvers+=("$gw")
fi
(( ${#resolvers[@]} > 0 )) || { echo "atlas-docker-egress: no LAN resolver (LAN_DNS_SERVERS, resolvectl, default route)" >&2; exit 1; }

iptables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -F DOCKER-USER
iptables -w -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o 'br-+'  -m conntrack --ctstate DNAT -j ACCEPT
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o docker0 -m conntrack --ctstate DNAT -j ACCEPT
iptables -w -A DOCKER-USER -i "$WG_BRIDGE" -o "$LAN_IFACE" -d "$LAN_CIDR" -j RETURN
for br in 'br-+' docker0; do
  for r in "${resolvers[@]}"; do
    iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -d "$r" -p udp --dport 53 -j RETURN
    iptables -w -A DOCKER-USER -i "$br" -o "$LAN_IFACE" -d "$r" -p tcp --dport 53 -j RETURN
  done
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
echo "atlas-docker-egress: DOCKER-USER rules installed (lan=$LAN_IFACE $LAN_CIDR, dns=${resolvers[*]}, wg bridge=$WG_BRIDGE)"
