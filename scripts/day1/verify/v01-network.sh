#!/usr/bin/env bash
# verify/v01-network.sh — V1: network link up and stable. Informational only (Section 21: the node runs on Wi-Fi, R9),
# so this always exits 3 (info) and never blocks the gate; the evidence line says what was seen.
# Usage: v01-network.sh [LAN_IFACE]     (defaults to the interface of the default route)
export ATLAS_LOG_TO_STDERR=1   # §5: stdout carries exactly one line of evidence
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

iface="${1:-$(ip -o route show default 2>/dev/null | awk '{print $5; exit}')}"
if [[ -z "$iface" ]]; then
  echo "no default route: the node has no network path (Wi-Fi not connected?)"
  exit 3
fi
state="$(cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo unknown)"
addr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null | awk '{print $4; exit}')"
gw="$(ip -o route show default 2>/dev/null | awk '$5=="'"$iface"'" {print $3; exit}')"
kind="wired"
[[ -d "/sys/class/net/$iface/wireless" ]] && kind="wifi"
ssid=""
if [[ "$kind" == wifi ]] && command -v iw >/dev/null; then
  ssid="$(iw dev "$iface" link 2>/dev/null | awk -F'SSID: ' '/SSID/ {print $2; exit}')"
fi
# Reachability: ICMP to the gateway (always allowed on the LAN) and to 1.1.1.1 (outbound ICMP is allowed by ufw).
gw_ok="no"; inet_ok="no"
[[ -n "$gw" ]] && ping -c 2 -W 2 "$gw" >/dev/null 2>&1 && gw_ok="yes"
ping -c 2 -W 3 1.1.1.1 >/dev/null 2>&1 && inet_ok="yes"
# Stability: a short loss sample against the gateway (10 pings, ~10 s); informational.
loss="n/a"
if [[ -n "$gw" ]]; then
  loss="$(ping -c 10 -i 0.5 -W 2 "$gw" 2>/dev/null | awk -F', ' '/packet loss/ {for (i=1;i<=NF;i++) if ($i ~ /packet loss/) print $i}' || true)"
  [[ -n "$loss" ]] || loss="n/a"
fi
echo "iface=$iface ($kind${ssid:+, ssid=$ssid}) operstate=$state addr=${addr:-none} gw=${gw:-none} gw_reachable=$gw_ok internet_icmp=$inet_ok gateway_${loss// /_}"
exit 3
