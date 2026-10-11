#!/usr/bin/env bash
# verify/v24_watchdog_test.sh — regression test (S50, the hardware watchdog of 2026-10-11) for verify/v24-watchdog.sh.
# Runs a copy of the script with its node paths (the drop-in, the unit file, /sys/class/watchdog, /proc) moved into a
# fresh temp tree, and stub systemctl and journalctl that print the manager's properties, the driver unit's state and
# this boot's journal from files. Each case starts from an armed hardware watchdog (sp5100_tco, the expected driver) and
# changes one thing: pass, the softdog fallback (deferred), and every failure V24 names. Nothing opens a real
# /dev/watchdog*: the fake /proc/1/fd entry is a dangling symlink whose text is "/dev/watchdog0".
# Run: bash verify/v24_watchdog_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

# systemctl show: the driver unit's UnitFileState/ActiveState from $R/ufs and $R/act; the manager's properties from
# $R/props (one KEY=VALUE per line, as systemctl show prints them); $R/show_fails makes the manager query fail.
cat >"$T/bin/systemctl" <<'SH'
#!/bin/bash
[[ "$1" == show ]] || exit 0
if [[ " $* " == *" atlas-watchdog-driver.service "* ]]; then
  printf 'UnitFileState=%s\nActiveState=%s\n' "$(cat "$R/ufs")" "$(cat "$R/act")"; exit 0
fi
[[ -e "$R/show_fails" ]] && { echo "Failed to connect to bus: No such file or directory" >&2; exit 1; }
cat "$R/props"
SH
# journalctl: -k prints this boot's kernel log ($R/kj), anything else PID 1's lines of this boot ($R/jl).
cat >"$T/bin/journalctl" <<'SH'
#!/bin/bash
if [[ " $* " == *" -k "* ]]; then cat "$R/kj"; else cat "$R/jl"; fi
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"

R="$T/root"; export R
sed -e "s#^dropin=/etc/#dropin=$R/etc/#" -e "s#^unit_file=/etc/#unit_file=$R/etc/#" \
    -e "s#^wd_class=/sys/#wd_class=$R/sys/#" -e "s#^proc=/proc\$#proc=$R/proc#" \
    -e "s#^source \"\$(dirname \"\$(readlink -f \"\$0\")\")/../lib/common.sh\"#source \"$REPO/lib/common.sh\"#" \
    "$REPO/verify/v24-watchdog.sh" >"$T/v24.sh"
for v in dropin unit_file wd_class proc; do
  grep -qE "^$v=$R/" "$T/v24.sh" || { echo "FAIL could not redirect $v (path changed in v24-watchdog.sh?)"; exit 1; }
done
grep -qF "source \"$REPO/lib/common.sh\"" "$T/v24.sh" || { echo "FAIL could not redirect the common.sh source line"; exit 1; }
export ATLAS_STATE="$T/state"

USE_SP="Using hardware watchdog /dev/watchdog0: 'SP5100 TCO timer', version 0."
USE_SD="Using hardware watchdog /dev/watchdog0: 'Software Watchdog', version 0."
RESET='x86/amd: Previous system reset reason [0x00080800]: software wrote 0x6 to reset control register 0xCF9'
# mkdev N IDENTITY [PLATFORM_DRIVER] — /sys/class/watchdog/watchdogN; with a driver, a device link to a platform device
# bound to it (as sp5100_tco and wdat_wdt have), without one none (as softdog has).
mkdev() {
  local d="$R/sys/class/watchdog/watchdog$1"
  mkdir -p "$d"
  printf '%s\n' "$2" >"$d/identity"; echo active >"$d/state"; echo 120 >"$d/timeout"; echo 0 >"$d/nowayout"
  echo 97 >"$d/timeleft"; echo 0 >"$d/bootstatus"
  if [[ -n "${3:-}" ]]; then
    mkdir -p "$R/sys/bus/platform/drivers/$3" "$R/sys/devices/platform/$3"
    ln -sfn "$R/sys/bus/platform/drivers/$3" "$R/sys/devices/platform/$3/driver"
    ln -sfn "$R/sys/devices/platform/$3" "$d/device"
  fi
}
props() {  # props RUNTIME REBOOT KEXEC PRE DEVICE MONO
  printf 'RuntimeWatchdogUSec=%s\nRebootWatchdogUSec=%s\nKExecWatchdogUSec=%s\nRuntimeWatchdogPreUSec=%s\nWatchdogDevice=%s\nWatchdogLastPingTimestampMonotonic=%s\n' \
    "$1" "$2" "$3" "$4" "$5" "$6" >"$R/props"
}
todo() {  # todo open|closed|none — the to-do watchdog in $ATLAS_STATE/todo.jsonl
  rm -f "$ATLAS_STATE/todo.jsonl"
  case "$1" in
    open) echo '{"ts":"t","phase":"phase1","id":"watchdog","title":"x","detail":"","done":false}' >"$ATLAS_STATE/todo.jsonl" ;;
    closed) printf '%s\n' '{"ts":"t","phase":"phase1","id":"watchdog","title":"x","detail":"","done":false}' \
                        '{"ts":"t","phase":"phase1","id":"watchdog","title":"","detail":"","done":true}' >"$ATLAS_STATE/todo.jsonl" ;;
  esac
}
# base — the passing node: drop-in and unit (modprobe sp5100_tco), unit enabled and active, one hardware device
# watchdog0 armed by PID 1 (/proc/1/fd/7), RuntimeWatchdogUSec=2min, last ping 30 s ago, PID 1's arming line.
base() {
  rm -rf "$R"; mkdir -p "$R/etc/systemd/system.conf.d" "$R/etc/systemd/system" "$R/proc/1/fd" "$ATLAS_STATE"
  printf '[Manager]\nRuntimeWatchdogSec=2min\nRebootWatchdogSec=10min\n' >"$R/etc/systemd/system.conf.d/90-atlas-watchdog.conf"
  printf '[Service]\nType=oneshot\nExecStart=/usr/sbin/modprobe sp5100_tco\n' >"$R/etc/systemd/system/atlas-watchdog-driver.service"
  echo enabled >"$R/ufs"; echo active >"$R/act"
  mkdev 0 'SP5100 TCO timer' sp5100-tco
  ln -s /dev/watchdog0 "$R/proc/1/fd/7"; ln -s /dev/null "$R/proc/1/fd/0"; echo systemd >"$R/proc/1/comm"
  echo "1000.25 7000.00" >"$R/proc/uptime"
  props 2min 10min 0 0 /dev/watchdog0 970250000
  printf '%s\n' "Failed to open any watchdog device before the initial transaction completed: No such file or directory" \
    "$USE_SP" "Watchdog running with a hardware timeout of 2min." "Reached target graphical.target - Graphical Interface." >"$R/jl"
  printf '%s\n' "$RESET" "sp5100_tco: SP5100/SB800 TCO WatchDog Timer Driver" \
    "sp5100-tco sp5100-tco: Using 0xfeb00000 for watchdog MMIO address" "sp5100-tco sp5100-tco: initialized. heartbeat=60 sec (nowayout=0)" >"$R/kj"
  rm -f "$R/show_fails"; todo none
}
v24() {  # v24 [ARGS] -> "rc:stdout"; also checks that stdout is one line
  local out rc
  out="$(bash "$T/v24.sh" "$@" 2>"$T/stderr")"; rc=$?
  [[ "$(printf '%s\n' "$out" | wc -l)" == 1 ]] || out="NOT ONE LINE: $out"
  printf '%s' "$rc:$out"
}
has() { grep -cF -- "$1" <<<"$2"; }

# --- pass ---------------------------------------------------------------------------------------------------------
base; r="$(v24 120 600)"
ok "armed hardware: pass" "${r%%:*}" "0"
ok "armed hardware: one evidence line naming the driver, device and timeouts" \
   "$(has "hardware watchdog armed by PID 1: watchdog0 'SP5100 TCO timer' (driver sp5100-tco; atlas-watchdog-driver.service loads 'sp5100_tco'), WatchdogDevice=/dev/watchdog0, held by PID 1: yes, state active, timeout 120 s (RuntimeWatchdogUSec=2min, journal: 2min), timeleft 97 s, last ping 30 s ago, nowayout 0, RebootWatchdogUSec=10min, KExecWatchdogUSec=0, RuntimeWatchdogPreUSec=0; bootstatus 0; previous reset: [0x00080800]: software wrote 0x6 to reset control register 0xCF9" "$r")" "1"
base; r="$(v24)"
ok "defaults are 120 and 600" "${r%%:*}" "0"
# The early 'Failed to open any watchdog device' (before the driver loaded) is before the arming line: not a failure.
ok "an open failure before the last arming line does not count" "$(has 'logged after arming' "$r")" "0"
base; props 2min 10min 0 0 /dev/watchdog 970250000
ok "WatchdogDevice=/dev/watchdog (the legacy name of id 0): pass" "$(v24 | cut -d: -f1)" "0"
base; echo 32 >"$R/sys/class/watchdog/watchdog0/bootstatus"
printf '%s\n' 'x86/amd: Previous system reset reason [0x02000800]: hardware watchdog timer expired' >"$R/kj"
r="$(v24)"
ok "after a watchdog reset: pass, bootstatus 32 explained, reset reason quoted" \
   "${r%%:*}:$(has 'bootstatus 32 (WDIOF_CARDRESET: the previous boot ended in a watchdog reset)' "$r"):$(has 'previous reset: [0x02000800]: hardware watchdog timer expired' "$r")" "0:1:1"
base; : >"$R/kj"
ok "no reset-reason line: pass, said so" "$(v24 | grep -c 'previous reset: not logged')" "1"
# A re-arming (daemon-reexec, a systemd upgrade) after a ping failure: only lines after the LAST arming line count.
base; printf '%s\n' "$USE_SP" "Failed to ping hardware watchdog /dev/watchdog0: Device or resource busy" "$USE_SP" \
  "Watchdog running with a hardware timeout of 2min." >"$R/jl"
ok "ping failure before the last arming line: pass" "$(v24 | cut -d: -f1)" "0"
base; props 1min 5min 0 0 /dev/watchdog0 970250000; echo 60 >"$R/sys/class/watchdog/watchdog0/timeout"
echo 45 >"$R/sys/class/watchdog/watchdog0/timeleft"
ok "other timeouts passed as arguments (60 300): pass" "$(v24 60 300 | cut -d: -f1)" "0"
base; mkdir -p "$R/proc/4242/fd"; ln -s /dev/null "$R/proc/4242/fd/1"; ln -s /dev/null "$R/proc/1/fd/8"
ok "other processes with other fds: pass" "$(v24 | cut -d: -f1)" "0"

# --- deferred: the softdog fallback -------------------------------------------------------------------------------
sd() {  # sd — base with softdog in place of the hardware device, as step 4's fallback leaves the node
  base; rm -rf "$R/sys/class/watchdog/watchdog0"; mkdev 0 'Software Watchdog'
  printf '[Service]\nExecStart=/usr/sbin/modprobe softdog soft_panic=1\n' >"$R/etc/systemd/system/atlas-watchdog-driver.service"
  printf '%s\n' "$USE_SD" "Watchdog running with a hardware timeout of 2min." >"$R/jl"
}
sd; todo open; r="$(v24)"
ok "softdog armed, to-do open: deferred" "${r%%:*}" "2"
ok "softdog: says what it covers and names the to-do" \
   "$(has "no hardware watchdog: softdog armed by PID 1 in its place (restarts a frozen or crashed systemd, not reliably a hard lockup of the whole kernel; to-do watchdog open): watchdog0 'Software Watchdog' (driver none; atlas-watchdog-driver.service loads 'softdog soft_panic=1')" "$r")" "1"
sd; todo closed; r="$(v24)"
ok "softdog armed, to-do closed: still deferred, says the to-do is not open" "${r%%:*}:$(has 'to-do watchdog NOT open' "$r")" "2:1"
sd; echo inactive >"$R/sys/class/watchdog/watchdog0/state"; todo open; r="$(v24)"
ok "softdog loaded but not armed: fail, not deferred" "${r%%:*}:$(has 'watchdog0 is inactive, not active' "$r")" "1:1"
sd; printf '[Service]\nExecStart=/usr/sbin/modprobe sp5100_tco\n' >"$R/etc/systemd/system/atlas-watchdog-driver.service"; r="$(v24)"
ok "softdog armed while the unit names sp5100_tco: fail" "${r%%:*}:$(has "watchdog0 is softdog, but atlas-watchdog-driver.service loads 'sp5100_tco'" "$r")" "1:1"

# --- fail: configured but not armed, and each mismatch ------------------------------------------------------------
base; echo inactive >"$R/sys/class/watchdog/watchdog0/state"; rm -f "$R/proc/1/fd/7"
echo 60 >"$R/sys/class/watchdog/watchdog0/timeout"; echo 0 >"$R/sys/class/watchdog/watchdog0/timeleft"
props 2min 10min 0 0 "" 18446744073709551615
printf '%s\n' "Failed to open any watchdog device before the initial transaction completed: No such file or directory" >"$R/jl"
r="$(v24)"
ok "configured, not armed: fail" "${r%%:*}" "1"
ok "not armed: names each missing piece" \
   "$(has 'watchdog0 is inactive, not active: nobody has armed it' "$r"):$(has "PID 1's WatchdogDevice is 'empty'" "$r"):$(has 'PID 1 holds no /dev/watchdog*' "$r"):$(has 'PID 1 has not pinged a watchdog this boot (WatchdogLastPingTimestampMonotonic=18446744073709551615)' "$r"):$(has "no 'Using hardware watchdog' line" "$r")" "1:1:1:1:1"
ok "not armed: the message is a V24 fail line with the evidence" "$(grep -c '^1:V24 fail: .*\. Evidence: watchdog0 ' <<<"$r")" "1"
base; props 1min 10min 0 0 /dev/watchdog0 970250000; echo 60 >"$R/sys/class/watchdog/watchdog0/timeout"
r="$(v24)"
ok "RuntimeWatchdogUSec overridden to 1min (a later drop-in): fail, both named" \
   "${r%%:*}:$(has 'RuntimeWatchdogUSec=1min, want 120 s' "$r"):$(has 'watchdog0 timeout 60 s, want 120' "$r")" "1:1:1"
base; echo 60 >"$R/sys/class/watchdog/watchdog0/timeout"
ok "hardware timeout differs from the setting: fail" "$(v24 | grep -c '^1:.*watchdog0 timeout 60 s, want 120')" "1"
base; props infinity 10min 0 0 /dev/watchdog0 970250000
ok "RuntimeWatchdogSec=default (infinity): fail" "$(v24 | grep -c '^1:.*RuntimeWatchdogUSec=infinity, want 120 s')" "1"
base; props 2min 5min 0 0 /dev/watchdog0 970250000
ok "RebootWatchdogUSec 5min: fail" "$(v24 | grep -c '^1:.*RebootWatchdogUSec=5min, want 600 s')" "1"
base; props 2min 10min 1min 0 /dev/watchdog0 970250000
ok "KExecWatchdogUSec set: fail" "$(v24 | grep -c '^1:.*KExecWatchdogUSec=1min, want 0')" "1"
base; props 2min 10min 0 30s /dev/watchdog0 970250000
ok "RuntimeWatchdogPreUSec set: fail" "$(v24 | grep -c '^1:.*RuntimeWatchdogPreUSec=30s, want 0 (sp5100_tco has no pretimeout)')" "1"
base; props "" "" "" "" /dev/watchdog0 970250000
ok "properties missing (an older systemd): fail" "$(v24 | grep -c '^1:.*RuntimeWatchdogUSec=unset')" "1"
base; touch "$R/show_fails"
ok "systemctl show fails: fail" "$(v24 | grep -c '^1:V24 fail: systemctl show (the manager.s watchdog properties) failed: Failed to connect to bus')" "1"
base; rm -f "$R/etc/systemd/system.conf.d/90-atlas-watchdog.conf"
ok "drop-in missing: fail" "$(v24 | grep -c '^1:.*no .*/etc/systemd/system.conf.d/90-atlas-watchdog.conf (Phase 1 step 4 writes it)')" "1"
base; echo disabled >"$R/ufs"
ok "driver unit not enabled: fail" "$(v24 | grep -c "^1:.*atlas-watchdog-driver.service is 'disabled', not enabled")" "1"
base; echo failed >"$R/act"
ok "driver unit failed: fail" "$(v24 | grep -c "^1:.*atlas-watchdog-driver.service is 'failed', not active")" "1"
base; echo 1 >"$R/sys/class/watchdog/watchdog0/nowayout"
ok "nowayout 1: fail" "$(v24 | grep -c '^1:.*watchdog0 nowayout 1, want 0')" "1"
base; echo 0 >"$R/sys/class/watchdog/watchdog0/timeleft"
ok "timeleft 0: fail" "$(v24 | grep -c '^1:.*watchdog0 timeleft 0 s, want 1..120')" "1"
base; echo 121 >"$R/sys/class/watchdog/watchdog0/timeleft"
ok "timeleft above the timeout: fail" "$(v24 | grep -c '^1:.*watchdog0 timeleft 121 s')" "1"
base; props 2min 10min 0 0 /dev/watchdog0 880250000
ok "last ping 120 s ago: fail (stale)" "$(v24 | grep -c '^1:.*PID 1 last pinged the watchdog 120 s ago, want under 120 s')" "1"
base; props 2min 10min 0 0 /dev/watchdog0 880250001
ok "last ping 119.99 s ago: pass" "$(v24 | cut -d: -f1)" "0"
base; props 2min 10min 0 0 /dev/watchdog0 2000000000
ok "last ping stamp later than the uptime: fail" "$(v24 | grep -c '^1:.*later than the uptime')" "1"
base; rm -f "$R/proc/uptime"
ok "/proc/uptime unreadable: fail" "$(v24 | grep -c '^1:.*/proc/uptime is unreadable, so the age of PID 1.s last ping is unknown')" "1"
base; mkdir -p "$R/proc/4242/fd"; ln -s /dev/watchdog0 "$R/proc/4242/fd/3"; echo wd_keepalive >"$R/proc/4242/comm"
ok "another process holds /dev/watchdog0: fail, names it" "$(v24 | grep -c '^1:.*other processes hold a /dev/watchdog\*: 4242 (wd_keepalive)')" "1"
base; printf '%s\n' "$USE_SP" "Failed to ping hardware watchdog /dev/watchdog0: Input/output error" >"$R/jl"
ok "ping failure after arming: fail, quotes it" "$(v24 | grep -c '^1:.*PID 1 logged after arming: Failed to ping hardware watchdog /dev/watchdog0: Input/output error')" "1"
base; printf '%s\n' "$USE_SP" "Failed to open watchdog device /dev/watchdog0: Device or resource busy" >"$R/jl"
ok "open failure after arming: fail" "$(v24 | grep -c '^1:.*PID 1 logged after arming: Failed to open watchdog device')" "1"
base; printf '%s\n' "$USE_SD" >"$R/jl"
ok "arming line names another identity: fail" "$(v24 | grep -c "^1:.*does not name watchdog0 'SP5100 TCO timer'")" "1"
base; : >"$R/jl"
ok "no arming line this boot: fail" "$(v24 | grep -c "^1:.*no 'Using hardware watchdog' line from PID 1")" "1"

# --- fail: the devices in /sys/class/watchdog ----------------------------------------------------------------------
base; rm -rf "$R/sys/class/watchdog/watchdog0"
printf '%s\n' "$RESET" "sp5100_tco: SP5100/SB800 TCO WatchDog Timer Driver" "sp5100-tco sp5100-tco: Watchdog hardware is disabled" >"$R/kj"
r="$(v24)"
ok "no watchdog device at all (sysfs empty): fail, quotes the driver's kernel lines" \
   "${r%%:*}:$(has "no watchdog device in $R/sys/class/watchdog (the driver's kernel lines this boot: sp5100_tco: SP5100/SB800 TCO WatchDog Timer Driver;sp5100-tco sp5100-tco: Watchdog hardware is disabled)" "$r")" "1:1"
base; rm -rf "$R/sys/class/watchdog"
ok "no /sys/class/watchdog directory: fail" "$(v24 | grep -c '^1:.*no watchdog device in ')" "1"
base; mkdev 1 'Software Watchdog'
ok "hardware and softdog both loaded: fail (which one PID 1 arms depends on the order)" \
   "$(v24 | grep -c '^1:.*2 watchdog devices (watchdog0 watchdog1): only one driver may be loaded')" "1"
base; mv "$R/sys/class/watchdog/watchdog0" "$R/sys/class/watchdog/watchdog1"
ok "the only device is watchdog1: fail" "$(v24 | grep -c '^1:.*the only watchdog device is watchdog1, not watchdog0')" "1"
base; rm -f "$R/sys/class/watchdog/watchdog0/device"
ok "identity not softdog but no driver bound: fail" "$(v24 | grep -c "^1:.*watchdog0 'SP5100 TCO timer' is neither softdog nor bound to a driver")" "1"
base; rm -f "$R/sys/class/watchdog/watchdog0/state"
ok "a sysfs file missing: fail" "$(v24 | grep -c '^1:.*watchdog0 is unreadable, not active')" "1"

# --- arguments -----------------------------------------------------------------------------------------------------
base
ok "bad argument: fail with usage" "$(v24 2min | grep -c '^1:V24 fail: usage: ')" "1"
ok "zero timeout: fail with usage" "$(v24 0 600 | grep -c '^1:V24 fail: usage: ')" "1"

# --- the script never writes to or opens a watchdog device, and never reads the status file (reading it clears the
#     keepalive flag) --------------------------------------------------------------------------------------------------
code="$(grep -v '^[[:space:]]*#' "$REPO/verify/v24-watchdog.sh")"
ok "no redirection into /dev/watchdog*" "$(grep -cE '>[[:space:]]*"?/dev/watchdog' <<<"$code")" "0"
ok "no wdctl, no reads of /dev/watchdog*" "$(grep -cE 'wdctl|(cat|<)[[:space:]]+"?/dev/watchdog' <<<"$code")" "0"
ok "no read of the status attribute" "$(grep -cE 'rd status|/status' <<<"$code")" "0"

echo "v24_watchdog_test: $pass passed, $fail failed"
(( fail == 0 ))
