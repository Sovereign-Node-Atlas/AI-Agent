#!/usr/bin/env bash
# verify/v24-watchdog.sh — V24: PID 1 has armed the watchdog that Phase 1 step 4 installed, with the S50 timeouts (the
# Principal's decision of 2026-10-11, S50; Sections 3.6, 21).
# Usage: v24-watchdog.sh [RUNTIME_SEC] [REBOOT_SEC]
#   (defaults 120 and 600: RuntimeWatchdogSec=2min and RebootWatchdogSec=10min, what step 4 writes)
# Reads systemctl show, sysfs, /proc and this boot's journal only. It NEVER opens /dev/watchdog*: an open starts the
# timer, a close without the magic 'V' leaves it running until the node resets, and PID 1 holds the device anyway.
#   PASS (exit 0): step 4's drop-in /etc/systemd/system.conf.d/90-atlas-watchdog.conf exists and PID 1 runs with
#     RuntimeWatchdogUSec=RUNTIME_SEC, RebootWatchdogUSec=REBOOT_SEC, KExecWatchdogUSec=0 and RuntimeWatchdogPreUSec=0;
#     atlas-watchdog-driver.service is enabled and active; /sys/class/watchdog holds exactly one device, watchdog0, and
#     it is hardware (identity not 'Software Watchdog' and its parent device bound to a driver; sp5100-tco, 'SP5100 TCO
#     timer', is the one expected here); PID 1 holds /dev/watchdog0 (WatchdogDevice and /proc/1/fd) and no other process
#     holds a /dev/watchdog*; watchdog0 is active with timeout RUNTIME_SEC, nowayout 0 and 0 < timeleft <= RUNTIME_SEC;
#     PID 1's last ping is less than RUNTIME_SEC old; this boot's journal has PID 1's line "Using hardware watchdog
#     /dev/watchdog0: '<that identity>', ..." and no "Failed to open ... watchdog device" or "Failed to ping hardware
#     watchdog" line after the last one.
#   DEFERRED (exit 2): all of that, but the device is softdog ('Software Watchdog'), the fallback step 4 installs when
#     it finds no hardware watchdog (to-do watchdog): it restarts a frozen or crashed PID 1, not reliably a hard lockup of
#     the whole kernel (INFERRED: its timer needs a working timer interrupt).
#   FAIL (exit 1): anything else: settings not in effect, the unit not enabled or not active, no watchdog device or more
#     than one, a device PID 1 has not armed or another process holds, a timeout or nowayout that differs, a stale ping,
#     an open or ping failure logged after arming, or softdog armed while the unit names a hardware driver.
#   Recorded, never decisive: bootstatus (32 = WDIOF_CARDRESET: the previous boot ended in a watchdog reset, when the
#   driver reads the fired bit) and the kernel's "Previous system reset reason" line of this boot (x86/amd, every Zen
#   CPU; the kernel clears the register at boot, so this boot's journal is the only record).
# Exit 0 pass, 1 fail, 2 deferred. Must run as root (PID 1's fd table).
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

runtime="${1:-120}"; reboot="${2:-600}"
[[ "$runtime" =~ ^[1-9][0-9]*$ && "$reboot" =~ ^[1-9][0-9]*$ ]] \
  || { echo "V24 fail: usage: v24-watchdog.sh [RUNTIME_SEC] [REBOOT_SEC] (whole seconds, got '$runtime' '$reboot')"; exit 1; }
# Node paths. The drop-in and the unit are written by phase1/04-system.sh (_watchdog).
dropin=/etc/systemd/system.conf.d/90-atlas-watchdog.conf
unit=atlas-watchdog-driver.service
unit_file=/etc/systemd/system/atlas-watchdog-driver.service
wd_class=/sys/class/watchdog
proc=/proc
problems=()

# _usec SPAN — a time span as systemctl show prints it ("2min", "1min 30s", "500ms", "0", "infinity") in microseconds,
# "inf" for infinity, nothing when it cannot be read.
_usec() {
  awk -v s="$1" 'BEGIN {
    if (s == "infinity") { print "inf"; exit }
    n = split(s, t, " "); if (n == 0) exit
    split("us ms s min h d w", unit, " "); split("1 1000 1000000 60000000 3600000000 86400000000 604800000000", mult, " ")
    for (i = 1; i <= n; i++) {
      if (!match(t[i], /^[0-9]+(\.[0-9]+)?/)) exit
      v = substr(t[i], 1, RLENGTH); u = substr(t[i], RLENGTH + 1); m = 0
      if (u == "" && v == 0) m = 1
      for (j = 1; j <= 7; j++) if (unit[j] == u) m = mult[j]
      if (!m) exit
      tot += v * m
    }
    printf "%.0f\n", tot }'
}

# 1. PID 1's settings and what it opened (systemd 259 Manager properties).
props="$(systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec -p KExecWatchdogUSec -p RuntimeWatchdogPreUSec \
          -p WatchdogDevice -p WatchdogLastPingTimestampMonotonic 2>&1)" \
  || { echo "V24 fail: systemctl show (the manager's watchdog properties) failed: $(tr '\n' ' ' <<<"$props")"; exit 1; }
prop() { sed -n "s/^$1=//p" <<<"$props" | head -n1; }
rt="$(prop RuntimeWatchdogUSec)"; rb="$(prop RebootWatchdogUSec)"; kx="$(prop KExecWatchdogUSec)"
pre="$(prop RuntimeWatchdogPreUSec)"; wdev="$(prop WatchdogDevice)"; mono="$(prop WatchdogLastPingTimestampMonotonic)"
[[ -f "$dropin" ]] || problems+=("no $dropin (Phase 1 step 4 writes it)")
[[ "$(_usec "$rt")" == "$(( runtime * 1000000 ))" ]] \
  || problems+=("RuntimeWatchdogUSec=${rt:-unset}, want $runtime s (systemd-analyze cat-config systemd/system.conf)")
[[ "$(_usec "$rb")" == "$(( reboot * 1000000 ))" ]] || problems+=("RebootWatchdogUSec=${rb:-unset}, want $reboot s")
[[ "$(_usec "$kx")" == 0 ]] || problems+=("KExecWatchdogUSec=${kx:-unset}, want 0")
[[ "$(_usec "$pre")" == 0 ]] || problems+=("RuntimeWatchdogPreUSec=${pre:-unset}, want 0 (sp5100_tco has no pretimeout)")

# 2. The driver unit (the one way past Ubuntu's watchdog blacklist, S50) and the driver it names.
ust="$(systemctl show -p UnitFileState -p ActiveState "$unit" 2>/dev/null || true)"
ufs="$(sed -n 's/^UnitFileState=//p' <<<"$ust")"; act="$(sed -n 's/^ActiveState=//p' <<<"$ust")"
[[ "$ufs" == enabled ]] || problems+=("$unit is '${ufs:-not installed}', not enabled")
[[ "$act" == active ]] || problems+=("$unit is '${act:-unknown}', not active (systemctl status $unit)")
load="$({ sed -n 's#^ExecStart=/usr/sbin/modprobe ##p' "$unit_file" 2>/dev/null || true; } | head -n1)"

# 3. The devices. One driver only: whichever registers first becomes watchdog0, the device PID 1 opens (/dev/watchdog0,
#    then /dev/watchdog, the legacy name of id 0).
kj="$(journalctl -k -b 0 -o cat 2>/dev/null || true)"
klines() { { grep -iE 'sp5100[-_]tco|softdog|wdat_wdt' <<<"$kj" || true; } | tail -n 3 | paste -sd';'; }
devs=()
for d in "$wd_class"/watchdog[0-9]*; do [[ -e "$d" ]] && devs+=("${d##*/}"); done
if (( ${#devs[@]} == 0 )); then
  kl="$(klines)"
  problems+=("no watchdog device in $wd_class (the driver's kernel lines this boot: ${kl:-none})")
elif (( ${#devs[@]} > 1 )); then
  problems+=("${#devs[@]} watchdog devices (${devs[*]}): only one driver may be loaded, or the boot order decides which one PID 1 arms")
elif [[ "${devs[0]}" != watchdog0 ]]; then
  problems+=("the only watchdog device is ${devs[0]}, not watchdog0, which PID 1 opens")
fi
w="$wd_class/watchdog0"
ident="" state="" timeout="" nowayout="" timeleft="" bootstatus="" drvname=none kind=none
if [[ -e "$w" ]]; then
  rd() { cat "$w/$1" 2>/dev/null || true; }
  ident="$(rd identity)"; state="$(rd state)"; timeout="$(rd timeout)"; nowayout="$(rd nowayout)"
  timeleft="$(rd timeleft)"; bootstatus="$(rd bootstatus)"
  drvlink="$(readlink -f "$w/device/driver" 2>/dev/null || true)"
  [[ -n "$drvlink" && -d "$drvlink" ]] && drvname="${drvlink##*/}"
  if [[ "$ident" == "Software Watchdog" ]]; then
    kind=softdog
    [[ "$load" == softdog* ]] || problems+=("watchdog0 is softdog, but $unit loads '${load:-?}' (softdog loaded by hand beside a missing hardware driver?)")
  elif [[ -n "$ident" && "$drvname" != none ]]; then
    kind=hardware
  else
    problems+=("watchdog0 '${ident:-no identity}' is neither softdog nor bound to a driver")
  fi
  [[ "$state" == active ]] || problems+=("watchdog0 is ${state:-unreadable}, not active: nobody has armed it")
  [[ "$timeout" == "$runtime" ]] || problems+=("watchdog0 timeout ${timeout:-unreadable} s, want $runtime")
  [[ "$nowayout" == 0 ]] || problems+=("watchdog0 nowayout ${nowayout:-unreadable}, want 0")
  if ! [[ "$timeleft" =~ ^[0-9]+$ ]] || (( timeleft == 0 || timeleft > runtime )); then
    problems+=("watchdog0 timeleft ${timeleft:-unreadable} s, want 1..$runtime")
  fi
fi

# 4. PID 1 holds the device, and nobody else does (it is single-open; the 'watchdog' daemon would be a second pinger).
case "$wdev" in
  /dev/watchdog0|/dev/watchdog) ;;
  *) problems+=("PID 1's WatchdogDevice is '${wdev:-empty}': it has opened no watchdog device") ;;
esac
holders="$({ find "$proc"/[0-9]*/fd -maxdepth 1 -lname '/dev/watchdog*' 2>/dev/null || true; } | LC_ALL=C sort)"
pid1="$({ grep -c "^$proc/1/fd/" <<<"$holders" || true; })"
(( pid1 > 0 )) || problems+=("PID 1 holds no /dev/watchdog* (/proc/1/fd)")
others=""
for p in $({ grep -v "^$proc/1/fd/" <<<"$holders" || true; } | sed -nE "s#^$proc/([0-9]+)/fd/.*#\\1#p" | sort -un); do
  others+="${others:+ }$p ($(cat "$proc/$p/comm" 2>/dev/null || echo '?'))"
done
[[ -z "$others" ]] || problems+=("other processes hold a /dev/watchdog*: $others")

# 5. The last ping. The Monotonic stamp is PID 1's CLOCK_BOOTTIME stamp mapped to CLOCK_MONOTONIC, and /proc/uptime is
#    CLOCK_BOOTTIME; the two clocks differ only by time spent suspended, and this node never suspends.
age="?"
up="$(awk '{printf "%.0f", $1 * 1000000}' "$proc/uptime" 2>/dev/null || true)"
if ! [[ "$mono" =~ ^[0-9]+$ && "$mono" != 0 && "$mono" != 18446744073709551615 ]]; then
  problems+=("PID 1 has not pinged a watchdog this boot (WatchdogLastPingTimestampMonotonic=${mono:-empty})")
elif ! [[ "$up" =~ ^[0-9]+$ ]]; then
  problems+=("$proc/uptime is unreadable, so the age of PID 1's last ping is unknown")
else
  if (( up < mono )); then
    problems+=("PID 1's last ping stamp ($mono us) is later than the uptime ($up us)")
  else
    age=$(( (up - mono) / 1000000 ))
    (( up - mono < runtime * 1000000 )) || problems+=("PID 1 last pinged the watchdog $age s ago, want under $runtime s")
  fi
fi

# 6. PID 1's own words this boot (SYSLOG_IDENTIFIER=systemd also catches lines it logged to kmsg before journald ran).
jl="$(journalctl -b 0 -o cat SYSLOG_IDENTIFIER=systemd 2>/dev/null || true)"
n="$({ grep -n '^Using hardware watchdog ' <<<"$jl" || true; } | tail -n1 | cut -d: -f1)"
hwt="?"
if [[ -z "$n" ]]; then
  problems+=("this boot's journal has no 'Using hardware watchdog' line from PID 1 (journalctl -b -o cat SYSLOG_IDENTIFIER=systemd | grep -i watchdog)")
else
  use="$(sed -n "${n}p" <<<"$jl")"
  if [[ "$use" != "Using hardware watchdog /dev/watchdog0: '$ident',"* && "$use" != "Using hardware watchdog /dev/watchdog: '$ident',"* ]]; then
    problems+=("PID 1's last arming line does not name watchdog0 '${ident:-?}': ${use%.}")
  fi
  bad="$(tail -n +"$(( n + 1 ))" <<<"$jl" | { grep -E '^Failed to (open (any )?watchdog device|ping hardware watchdog)' || true; } | tail -n 2 | paste -sd'|')"
  [[ -z "$bad" ]] || problems+=("PID 1 logged after arming: $bad")
  hwt="$(tail -n +"$n" <<<"$jl" | { grep -E '^Watchdog running with a hardware timeout of ' || true; } | tail -n1 \
         | sed -E 's/^Watchdog running with a hardware timeout of (.*)\.$/\1/')"
fi

# Evidence (one line), then the verdict.
reset="$({ grep -E 'Previous system reset reason' <<<"$kj" || true; } | tail -n1 | sed -E 's/^.*Previous system reset reason //')"
bs_note=""; [[ "$bootstatus" == 32 ]] && bs_note=" (WDIOF_CARDRESET: the previous boot ended in a watchdog reset)"
ev="watchdog0 '${ident:-?}' (driver $drvname; $unit loads '${load:-?}'), WatchdogDevice=${wdev:-empty}, held by PID 1: $( (( pid1 > 0 )) && echo yes || echo no), state ${state:-?}, timeout ${timeout:-?} s (RuntimeWatchdogUSec=${rt:-?}, journal: ${hwt:-?}), timeleft ${timeleft:-?} s, last ping $age s ago, nowayout ${nowayout:-?}, RebootWatchdogUSec=${rb:-?}, KExecWatchdogUSec=${kx:-?}, RuntimeWatchdogPreUSec=${pre:-?}; bootstatus ${bootstatus:-?}$bs_note; previous reset: ${reset:-not logged}"
if (( ${#problems[@]} > 0 )); then
  msg="${problems[0]}"; for p in "${problems[@]:1}"; do msg+="; $p"; done
  echo "V24 fail: $msg. Evidence: $ev"
  exit 1
fi
if [[ "$kind" == softdog ]]; then
  if todo_is_open watchdog; then td="to-do watchdog open"; else td="to-do watchdog NOT open (step 4 raises it with the probe's findings)"; fi
  echo "no hardware watchdog: softdog armed by PID 1 in its place (restarts a frozen or crashed systemd, not reliably a hard lockup of the whole kernel; $td): $ev"
  exit 2
fi
echo "hardware watchdog armed by PID 1: $ev"
exit 0
