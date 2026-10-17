#!/usr/bin/env bash
# verify/v01-network.sh — V1: network link up and stable. Informational only (Section 21, R9), so this always exits 3
# (info) and never blocks the gate; the evidence line says what was seen.
# B-27 (approved by the Principal 2026-10-17): the node runs on a cable from its 10 GbE port NOT marked DASH (Realtek
# RTL8127, driver r8169) to a router port of 2.5 GbE or faster. A wired link records its driver and speed: ethtool's
# "Speed:" line ("%uMb/s" or "Unknown!", VERIFIED ethtool v6.19 netlink/settings.c:538-542; ethtool 1:6.19-1 is in the
# 26.04.1 live-server manifest, VERIFIED releases.ubuntu.com 2026-10-17), else /sys/class/net/IFACE/speed (Mbit/s, VERIFIED Linux v7.0
# Documentation/ABI/testing/sysfs-class-net:272-278). A speed below 2,500 Mb/s is warned about on stderr and marked in
# the evidence (the router port or the cable caps the pull); it is never a failure. Wi-Fi is recorded as before.
# Usage: v01-network.sh [LAN_IFACE]     (defaults to the interface of the default route)
export ATLAS_LOG_TO_STDERR=1   # §5: stdout carries exactly one line of evidence
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
net_class=/sys/class/net       # node path (phase1/lan_link_test.sh moves it into a temp tree)
link_floor=2500                # Mb/s: a router port of 2.5 GbE or faster (B-27)

iface="${1:-$(ip -o route show default 2>/dev/null | awk '{print $5; exit}')}"
if [[ -z "$iface" ]]; then
  echo "no default route: the node has no network path (cable unplugged or Wi-Fi not connected?)"
  exit 3
fi
state="$(cat "$net_class/$iface/operstate" 2>/dev/null || echo unknown)"
addr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null | awk '{print $4; exit}')"
gw="$(ip -o route show default 2>/dev/null | awk '$5=="'"$iface"'" {print $3; exit}')"
kind="wired"
[[ -d "$net_class/$iface/wireless" ]] && kind="wifi"
ssid=""
if [[ "$kind" == wifi ]] && command -v iw >/dev/null; then
  ssid="$(iw dev "$iface" link 2>/dev/null | awk -F'SSID: ' '/SSID/ {print $2; exit}')"
fi
# Wired: driver and link speed (B-27). Down or unknown reads as link=unknown (sysfs gives -1 or an error then).
link=""
if [[ "$kind" == wired ]]; then
  driver="" speed="" duplex="" src=""
  if [[ -L "$net_class/$iface/device/driver" ]]; then driver="$(basename "$(readlink "$net_class/$iface/device/driver")")"; fi
  if command -v ethtool >/dev/null 2>&1; then
    eth="$(ethtool "$iface" 2>/dev/null || true)"
    speed="$(awk -F': *' '/^[[:space:]]*Speed:/ {print $2; exit}' <<<"$eth")"; speed="${speed%Mb/s}"
    duplex="$(awk -F': *' '/^[[:space:]]*Duplex:/ {print $2; exit}' <<<"$eth")"
    src=ethtool
  fi
  if ! [[ "$speed" =~ ^[0-9]+$ ]]; then
    speed="$(cat "$net_class/$iface/speed" 2>/dev/null || true)"; src=sysfs
    [[ -n "$duplex" && "$duplex" != Unknown* ]] || duplex="$(cat "$net_class/$iface/duplex" 2>/dev/null || true)"
  fi
  link="driver=${driver:-unknown}"
  if [[ "$speed" =~ ^[0-9]+$ ]] && (( speed > 0 )); then
    link+=" link=${speed}Mb/s${duplex:+/${duplex,,}}($src)"
    if (( speed < link_floor )); then
      link+=" link_below_${link_floor}Mb/s"
      warn "V1: $iface links at $speed Mb/s, below $link_floor Mb/s: the router port or the cable caps the model pull (B-27 wants a router port of 2.5 GbE or faster; a 1 GbE port gives about 941 Mbit/s). Informational, not a failure."
    fi
  else
    link+=" link=unknown"
  fi
fi
# Reachability: ICMP echo to the gateway only. ufw (phase1/04-system.sh) allows outbound echo-request to the LAN
# subnet and nothing beyond it, so an internet ping would be dropped by design; the internet path is proven by the
# proxy self-test in step 4 (allowlisted host 200 through squid), not by ICMP.
gw_ok="no"
[[ -n "$gw" ]] && ping -c 2 -W 2 "$gw" >/dev/null 2>&1 && gw_ok="yes"
# Stability: a short loss sample against the gateway (10 pings, ~10 s); informational.
loss="n/a"
if [[ -n "$gw" ]]; then
  loss="$(ping -c 10 -i 0.5 -W 2 "$gw" 2>/dev/null | awk -F', ' '/packet loss/ {for (i=1;i<=NF;i++) if ($i ~ /packet loss/) print $i}' || true)"
  [[ -n "$loss" ]] || loss="n/a"
fi
echo "iface=$iface ($kind${ssid:+, ssid=$ssid}) operstate=$state${link:+ $link} addr=${addr:-none} gw=${gw:-none} gw_reachable=$gw_ok gateway_${loss// /_}"
exit 3
