#!/usr/bin/env bash
# phase1/lan_link_test.sh — regression test (B-27, approved by the Principal 2026-10-17: the node on a cable from its
# 10 GbE port not marked DASH) for phase1/01-preflight.sh's phase1_lan_link_note and for verify/v01-network.sh (V1).
# The pre-flight note: a wired RTL8127 (r8169) LAN_IFACE gets the note (port, router reservation and UDP forward on its
# MAC, Wi-Fi off the subnet) and no warning; the controller r8169 treats as DASH (PCI revision 0x04) is warned about;
# a Wi-Fi LAN_IFACE is warned about and never stops the step; a second interface on the LAN subnet is warned about, one
# on another subnet is not. V1: a wired link records driver and speed (ethtool, else sysfs), warns below 2,500 Mb/s and
# never fails; Wi-Fi is recorded as before; stdout stays one line and the exit stays 3 (info). /sys/class/net is moved
# into a temp tree; ip, ethtool, iw and ping are stubs. Run: bash phase1/lan_link_test.sh   (exit 0 when all pass)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/net"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
has() { if grep -qF -- "$3" <<<"$2"; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: '$3' not in: $2"; fail=$((fail+1)); fi; }
hasnt() { if grep -qF -- "$3" <<<"$2"; then echo "FAIL $1: '$3' found in: $2"; fail=$((fail+1)); else echo "PASS $1"; pass=$((pass+1)); fi; }
extract() { awk -v n="$2" '$0 ~ "^"n"\\(\\) \\{" {on=1} on{print} on && /^\}/{exit}' "$1"; }

# Stubs. ip: the default route from $T/route, the IPv4 addresses ("N: IFACE inet ADDR/LEN ...") from $T/addrs.
cat >"$T/bin/ip" <<'SH'
#!/bin/bash
case " $* " in
  *" route show default "*) cat "$T/route" ;;
  *" addr show dev "*) d=""; while (( $# )); do [[ "$1" == dev ]] && { d="$2"; break; }; shift; done
                      awk -v d="$d" '$2 == d' "$T/addrs" ;;
  *" addr show "*) cat "$T/addrs" ;;
esac
SH
# ethtool IFACE: $T/ethtool.out when present, else fail as on an interface without link settings.
cat >"$T/bin/ethtool" <<'SH'
#!/bin/bash
[[ -f "$T/ethtool.out" ]] || { echo "netlink error: Operation not supported" >&2; exit 75; }
cat "$T/ethtool.out"
SH
cat >"$T/bin/iw" <<'SH'
#!/bin/bash
printf 'Connected to 00:11:22:33:44:55 (on %s)\n\tSSID: home-6g\n\tfreq: 5955\n' "$3"
SH
cat >"$T/bin/ping" <<'SH'
#!/bin/bash
printf '10 packets transmitted, 10 received, 0%% packet loss, time 4512ms\n'
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" T

# mknic NAME KIND [DRIVER VENDOR DEVICE REVISION SPEED DUPLEX] — a /sys/class/net entry under $T/net.
mknic() {
  local d="$T/net/$1"
  rm -rf "$d"; mkdir -p "$d/device"
  echo up >"$d/operstate"; echo "aa:bb:cc:00:00:0${#1}" >"$d/address"
  if [[ "$2" == wifi ]]; then mkdir -p "$d/wireless"; mkdir -p "$T/drv/mt7925e"; ln -sfn "$T/drv/mt7925e" "$d/device/driver"; return; fi
  mkdir -p "$T/drv/$3"; ln -sfn "$T/drv/$3" "$d/device/driver"
  echo "$4" >"$d/device/vendor"; echo "$5" >"$d/device/device"; echo "$6" >"$d/device/revision"
  echo "$7" >"$d/speed"; echo "$8" >"$d/duplex"
}
LAN_ETH=enp191s0 LAN_WIFI=wlp192s0

# --- the pre-flight note ---------------------------------------------------------------------------------------------
fn="$(extract "$REPO/phase1/01-preflight.sh" phase1_lan_link_note)"
[[ -n "$fn" ]] || { echo "FAIL phase1/01-preflight.sh defines no phase1_lan_link_note"; exit 1; }
grep -q 'local net=/sys/class/net$' <<<"$fn" || { echo "FAIL could not find the /sys/class/net node path to relocate"; exit 1; }
eval "${fn//local net=\/sys\/class\/net/local net=$T/net}"
log() { echo "LOG: $*"; }
warn() { echo "WARN: $*"; }
note() {  # IFACE — the note for LAN_IFACE=IFACE, as step_01 calls it
  # shellcheck disable=SC2034  # read by phase1_lan_link_note (eval'd above)
  ( LAN_IFACE="$1" LAN_IP=192.168.1.10 LAN_CIDR=192.168.1.0/24 WG_PORT=51820 ATLAS_ETC=/etc/atlas
    phase1_lan_link_note; echo "rc=$?" ) 2>&1
}
addrs() { printf '%s\n' "$@" >"$T/addrs"; }

# 1. Wired RTL8127 (r8169), the non-DASH controller: the note, no warning.
mknic "$LAN_ETH" wired r8169 0x10ec 0x8127 0x05 10000 full
addrs "2: $LAN_ETH    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_ETH" \
      "4: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0"
out="$(note "$LAN_ETH")"
has "wired: returns 0" "$out" "rc=0"
has "wired: logged as wired with driver, PCI ids and MAC" "$out" "LOG: network: $LAN_ETH is wired (driver r8169, PCI 0x10ec:0x8127 rev 0x05, MAC aa:bb:cc:00:00:08)"
has "wired: names the port not marked DASH" "$out" "the cable must be in the 10 GbE port NOT marked DASH (the one away from DC IN)"
has "wired: reservation and UDP forward on this MAC" "$out" "the router's DHCP reservation for 192.168.1.10 and the UDP 51820 forward must point at MAC aa:bb:cc:00:00:08"
has "wired: Wi-Fi off the subnet" "$out" "Wi-Fi must be off the 192.168.1.0/24 subnet"
hasnt "wired: no warning (docker0 is on another subnet)" "$out" "WARN:"

# 2. The controller r8169 treats as DASH (RTL8127A, PCI revision 0x04): warned, not stopped.
mknic "$LAN_ETH" wired r8169 0x10ec 0x8127 0x04 10000 full
out="$(note "$LAN_ETH")"
has "DASH controller: warned" "$out" "WARN: pre-flight: the r8169 driver treats $LAN_ETH as the DASH controller (RTL8127A, PCI revision 0x04)"
has "DASH controller: not a stop" "$out" "rc=0"
# Revision 0x04 of another vendor's part is not DASH.
mknic "$LAN_ETH" wired igc 0x8086 0x125c 0x04 2500 full
out="$(note "$LAN_ETH")"
hasnt "revision 0x04 of an Intel igc part: no DASH warning" "$out" "DASH controller"

# 3. Wi-Fi as LAN_IFACE: warned (the decision is the cable), never a stop.
mknic "$LAN_WIFI" wifi
addrs "3: $LAN_WIFI    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_WIFI"
out="$(note "$LAN_WIFI")"
has "Wi-Fi: warned with the decision" "$out" "WARN: pre-flight: LAN_IFACE=$LAN_WIFI is Wi-Fi (MAC aa:bb:cc:00:00:08). The Principal decided on 2026-10-17 (B-27)"
has "Wi-Fi: says how to switch" "$out" "reserve 192.168.1.10 for that port's MAC on the router and move the UDP 51820 forward to it"
has "Wi-Fi: not a stop" "$out" "this is not a stop."
has "Wi-Fi: returns 0" "$out" "rc=0"
hasnt "Wi-Fi: no wired note" "$out" "is wired"

# 4. Wired, with Wi-Fi still on the same subnet: warned by name; on another subnet: nothing.
mknic "$LAN_ETH" wired r8169 0x10ec 0x8127 0x05 10000 full
addrs "2: $LAN_ETH    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_ETH" \
      "3: $LAN_WIFI    inet 192.168.1.23/24 brd 192.168.1.255 scope global dynamic $LAN_WIFI"
out="$(note "$LAN_ETH")"
has "Wi-Fi on the LAN subnet: warned by name" "$out" "WARN: pre-flight: $LAN_WIFI (192.168.1.23/24) also on the LAN subnet 192.168.1.0/24 beside $LAN_ETH"
has "Wi-Fi on the LAN subnet: not a stop" "$out" "rc=0"
addrs "2: $LAN_ETH    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_ETH" \
      "3: $LAN_WIFI    inet 10.20.0.5/24 brd 10.20.0.255 scope global dynamic $LAN_WIFI"
out="$(note "$LAN_ETH")"
hasnt "Wi-Fi on another subnet: no warning" "$out" "WARN:"
extract "$REPO/phase1/01-preflight.sh" step_01 | grep -qx '  phase1_lan_link_note' && called=yes || called=no
ok "step_01 calls the note" "$called" "yes"

# --- V1 (verify/v01-network.sh) ------------------------------------------------------------------------------------------
sed -e "s#^net_class=/sys/class/net #net_class=$T/net #" \
    -e "s#^source \"\$(dirname \"\$(readlink -f \"\$0\")\")/../lib/common.sh\"#source \"$REPO/lib/common.sh\"#" \
    "$REPO/verify/v01-network.sh" >"$T/v01.sh"
grep -q "^net_class=$T/net " "$T/v01.sh" || { echo "FAIL could not redirect net_class (path changed in v01-network.sh?)"; exit 1; }
grep -qF "source \"$REPO/lib/common.sh\"" "$T/v01.sh" || { echo "FAIL could not redirect the common.sh source line"; exit 1; }
export ATLAS_STATE="$T/state"
v1() {  # run V1 as step 1 does without an argument; sets out (stdout), err (stderr), rc
  rc=0; out="$(bash "$T/v01.sh" 2>"$T/err")" || rc=$?; err="$(cat "$T/err")"
}
printf 'default via 192.168.1.1 dev %s proto dhcp src 192.168.1.10 metric 100\n' "$LAN_ETH" >"$T/route"
addrs "2: $LAN_ETH    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_ETH"
ethout() { printf 'Settings for %s:\n\tSupported ports: [ TP ]\n\tSpeed: %s\n\tDuplex: %s\n\tAuto-negotiation: on\n\tLink detected: yes\n' "$LAN_ETH" "$1" "$2" >"$T/ethtool.out"; }

# 5. Wired at 10 Gb/s (ethtool): recorded, no warning, info.
mknic "$LAN_ETH" wired r8169 0x10ec 0x8127 0x05 10000 full
ethout 10000Mb/s Full
v1
ok "V1 wired 10G: exit 3 (info)" "$rc" "3"
ok "V1 wired 10G: one line of evidence" "$(grep -c . <<<"$out")" "1"
ok "V1 wired 10G: evidence" "$out" "iface=$LAN_ETH (wired) operstate=up driver=r8169 link=10000Mb/s/full(ethtool) addr=192.168.1.10/24 gw=192.168.1.1 gw_reachable=yes gateway_0%_packet_loss"
hasnt "V1 wired 10G: no warning" "$err" "WARN"

# 6. Wired at 1 Gb/s (a 1 GbE router port): warned on stderr and marked, still info.
ethout 1000Mb/s Full
v1
ok "V1 wired 1G: exit 3 (info, never a failure)" "$rc" "3"
has "V1 wired 1G: marked in the evidence" "$out" "driver=r8169 link=1000Mb/s/full(ethtool) link_below_2500Mb/s addr="
has "V1 wired 1G: warned" "$err" "WARN V1: $LAN_ETH links at 1000 Mb/s, below 2500 Mb/s: the router port or the cable caps the model pull"
ok "V1 wired 1G: stdout still one line" "$(grep -c . <<<"$out")" "1"

# 7. Exactly 2.5 Gb/s: no warning.
ethout 2500Mb/s Full
v1
hasnt "V1 wired 2.5G: not marked" "$out" "link_below"
hasnt "V1 wired 2.5G: no warning" "$err" "WARN"

# 8. ethtool gives nothing: sysfs speed and duplex.
rm -f "$T/ethtool.out"
v1
has "V1 sysfs fallback: speed from sysfs" "$out" "driver=r8169 link=10000Mb/s/full(sysfs) addr="
# ethtool "Unknown!" and sysfs -1 (link down): unknown, no warning.
ethout 'Unknown!' 'Unknown! (255)'
echo -1 >"$T/net/$LAN_ETH/speed"; echo unknown >"$T/net/$LAN_ETH/duplex"; echo down >"$T/net/$LAN_ETH/operstate"
v1
has "V1 link down: speed unknown" "$out" "operstate=down driver=r8169 link=unknown addr="
hasnt "V1 link down: no speed warning" "$err" "WARN"
ok "V1 link down: exit 3" "$rc" "3"
rm -f "$T/ethtool.out"

# 9. Wi-Fi: recorded as before (SSID), no driver or link fields, no warning, info.
mknic "$LAN_WIFI" wifi
printf 'default via 192.168.1.1 dev %s proto dhcp metric 600\n' "$LAN_WIFI" >"$T/route"
addrs "3: $LAN_WIFI    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_WIFI"
v1
ok "V1 Wi-Fi: evidence as before" "$out" "iface=$LAN_WIFI (wifi, ssid=home-6g) operstate=up addr=192.168.1.10/24 gw=192.168.1.1 gw_reachable=yes gateway_0%_packet_loss"
ok "V1 Wi-Fi: exit 3 (info, never a failure)" "$rc" "3"
hasnt "V1 Wi-Fi: no warning" "$err" "WARN"

# 10. No default route and no argument.
: >"$T/route"
v1
ok "V1 no route: message" "$out" "no default route: the node has no network path (cable unplugged or Wi-Fi not connected?)"
ok "V1 no route: exit 3" "$rc" "3"

# 11. LAN_IFACE given (step 1 passes it): the wired interface is read even with no default route on it.
mknic "$LAN_ETH" wired r8169 0x10ec 0x8127 0x05 10000 full
addrs "2: $LAN_ETH    inet 192.168.1.10/24 brd 192.168.1.255 scope global dynamic $LAN_ETH"
rc=0; out="$(bash "$T/v01.sh" "$LAN_ETH" 2>/dev/null)" || rc=$?
ok "V1 given LAN_IFACE: evidence for it" "$out" "iface=$LAN_ETH (wired) operstate=up driver=r8169 link=10000Mb/s/full(sysfs) addr=192.168.1.10/24 gw=none gw_reachable=no gateway_n/a"
ok "V1 given LAN_IFACE: exit 3" "$rc" "3"

echo "lan_link_test: $pass passed, $fail failed"
(( fail == 0 ))
