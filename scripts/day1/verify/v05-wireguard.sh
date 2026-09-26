#!/usr/bin/env bash
# verify/v05-wireguard.sh — V5: WireGuard reachable from mobile data via vpn.<domain> (Sections 12.2, 21; R11 CGNAT).
# What a script can prove unattended (platform research item 11): the DNS record equals the node's public IP, the
# UDP socket is published, and, once the Principal connects the phone over mobile data, a recent handshake whose
# endpoint is neither the home public IP nor an RFC1918 address. The phone-side action is the Principal's; this
# script only waits for its evidence.
# Usage: v05-wireguard.sh VPN_HOST [CONTAINER=wg-easy] [WAIT_S=540] [RERUN_HINT]
# Exit 0 pass (carrier handshake seen), 2 deferred (no carrier handshake within WAIT_S: the Principal can retry with
# RERUN_HINT), 1 fail (container/interface missing). Never prompts. Runs under run_verify's 660 s cap.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

host="${1:-}"; ctr="${2:-wg-easy}"; wait_s="${3:-540}"; hint="${4:-re-run: sudo ./atlas-day1.sh phase1 --force 07}"
[[ -n "$host" ]] || { echo "usage: v05-wireguard.sh VPN_HOST [CONTAINER] [WAIT_S] [RERUN_HINT]"; exit 1; }

docker inspect -f '{{.State.Running}}' "$ctr" 2>/dev/null | grep -qx true \
  || { echo "V5 fail: container $ctr is not running"; exit 1; }
docker exec "$ctr" wg show wg0 >/dev/null 2>&1 \
  || { echo "V5 fail: wg0 is not up inside $ctr (first-run setup incomplete? open the WG-Easy admin page)"; exit 1; }
published="$(docker port "$ctr" 2>/dev/null | grep -m1 'udp' || true)"

home_ip="$(cat /var/lib/atlas-ddns/last-ip 2>/dev/null || true)"
dns_ip=""
if command -v dig >/dev/null; then
  dns_ip="$(dig +short +time=3 +tries=1 A "$host" @1.1.1.1 2>/dev/null | grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)"
fi
[[ -n "$dns_ip" ]] || dns_ip="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1; exit}' || true)"
dns_state="dns=$host->${dns_ip:-unresolved} public=${home_ip:-unknown}"
if [[ -n "$dns_ip" && -n "$home_ip" && "$dns_ip" == "$home_ip" ]]; then dns_state+=" (match)"; else dns_state+=" (MISMATCH: ddns not yet applied or DNS cached)"; fi

is_private() { # RFC1918 / loopback / CGNAT
  case "$1" in
    10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|127.*) return 0 ;;
    100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
  esac
  return 1
}

deadline=$(( SECONDS + wait_s )); last="no handshake seen"
while (( SECONDS < deadline )); do
  now="$(date +%s)"
  # wg show wg0 latest-handshakes: "<peer-pubkey>\t<epoch>"; endpoints: "<peer-pubkey>\t<ip>:<port>"
  while IFS=$'\t' read -r peer ts; do
    [[ -n "$peer" && "$ts" =~ ^[0-9]+$ && "$ts" -gt 0 ]] || continue
    (( now - ts < 180 )) || { last="stale handshake ($(( now - ts )) s ago) for ${peer:0:8}…"; continue; }
    ep="$(docker exec "$ctr" wg show wg0 endpoints 2>/dev/null | awk -v k="$peer" -F'\t' '$1==k {print $2}' | sed -E 's/^\[?([0-9a-fA-F.:]+)\]?:[0-9]+$/\1/')"
    if [[ -z "$ep" ]]; then last="handshake without endpoint for ${peer:0:8}…"; continue; fi
    if [[ "$ep" == "$home_ip" ]] || is_private "$ep"; then
      last="handshake from $ep (home/LAN address: the phone is still on Wi-Fi, turn Wi-Fi off)"
      continue
    fi
    echo "carrier handshake from $ep for peer ${peer:0:8}… $(( now - ts )) s ago; $dns_state; published=${published:-?}"
    exit 0
  done < <(docker exec "$ctr" wg show wg0 latest-handshakes 2>/dev/null || true)
  sleep 10
done
echo "deferred: no WireGuard handshake from a mobile-data address within ${wait_s}s (last: $last); $dns_state; published=${published:-?}; $hint"
exit 2
