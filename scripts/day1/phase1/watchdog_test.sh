#!/usr/bin/env bash
# phase1/watchdog_test.sh — regression test (S50, the hardware watchdog of 2026-10-11) for phase1/04-system.sh's
# _watchdog and its helpers (_watchdog_dev, _watchdog_klines, _watchdog_unit, _watchdog_dropin), for how step 5 hands
# V24 its timeouts, for the Phase 1 gate with V24 as a required row that may be deferred (phase1/08-gate.sh with the
# real lib/common.sh), and for tools/fill-workbook.py's V24 row. modprobe, dmesg and systemctl are stubs: the stub
# modprobe "loads" a driver by creating its /sys/module directory and, when the case says the hardware works, a
# /sys/class/watchdog device with the driver's identity and a parent device bound to its platform driver, exactly the
# files _watchdog reads; when the case says the firmware disabled it, the module loads with no device and the kernel log
# gets the driver's own "Watchdog hardware is disabled". Every node path is moved under a fresh temp dir per case.
# Nothing here opens a watchdog device. Run: bash phase1/watchdog_test.sh   (exit 0 when every case passes)
# shellcheck disable=SC2016,SC2030,SC2031  # file-wide: SC2030/SC2031, each gate and w-drill case sets its state in its own
# subshell on purpose; the single-quoted $ are meant unexpanded (the ${W_ROOT} that relocate() writes
#                              into the extracted functions, and the literal "$ATLAS_WATCHDOG_..." text step 5 must carry)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
extract() { awk -v n="$2" '$0 ~ "^"n"\\(\\) \\{" {on=1} on{print} on && /^\}/{exit}' "$1"; }

# modprobe [-r] MODULE [PARAMS]: $W_ROOT/sc.MODULE says what this board does — "hw" (a device appears), "disabled"
# (loads, no device, the driver's error in the kernel log), anything else or absent: "No such device" and exit 1, as
# kmod reports a module whose init fails. -r removes the module and its device.
cat >"$T/bin/modprobe" <<'SH'
#!/bin/bash
echo "modprobe $*" >>"$W_ROOT/calls"
rm=0; [[ "$1" == -r ]] && { rm=1; shift; }
m="$1"; mod="$W_ROOT/sys/module/$m"; cls="$W_ROOT/sys/class/watchdog"
case "$m" in
  sp5100_tco) ident='SP5100 TCO timer'; pdrv=sp5100-tco ;;
  wdat_wdt)   ident=wdat_wdt; pdrv=wdat_wdt ;;
  softdog)    ident='Software Watchdog'; pdrv="" ;;
  *) echo "modprobe: FATAL: Module $m not found in directory /lib/modules/7.0.0-38-generic" >&2; exit 1 ;;
esac
if (( rm )); then
  [[ -d "$mod" ]] || { echo "modprobe: FATAL: Module $m is not currently loaded." >&2; exit 1; }
  [[ -e "$W_ROOT/rm_fails" ]] && { echo "modprobe: FATAL: Module $m is in use." >&2; exit 1; }
  for d in "$cls"/watchdog*; do [[ "$(cat "$d/identity" 2>/dev/null)" == "$ident" ]] && rm -rf "$d"; done
  rm -rf "$mod"; exit 0
fi
[[ -d "$mod" ]] && exit 0
case "$(cat "$W_ROOT/sc.$m" 2>/dev/null)" in
  hw)
    mkdir -p "$mod" "$cls"
    n=0; while [[ -e "$cls/watchdog$n" ]]; do n=$((n + 1)); done
    d="$cls/watchdog$n"; mkdir -p "$d"
    printf '%s\n' "$ident" >"$d/identity"; echo inactive >"$d/state"; echo 60 >"$d/timeout"; echo 0 >"$d/nowayout"
    echo 0 >"$d/bootstatus"
    if [[ -n "$pdrv" ]]; then
      mkdir -p "$W_ROOT/sys/bus/platform/drivers/$pdrv" "$W_ROOT/sys/devices/platform/$pdrv"
      ln -sfn "$W_ROOT/sys/bus/platform/drivers/$pdrv" "$W_ROOT/sys/devices/platform/$pdrv/driver"
      ln -sfn "$W_ROOT/sys/devices/platform/$pdrv" "$d/device"
    fi
    echo "[    6.20] $pdrv $pdrv: initialized. heartbeat=60 sec (nowayout=0)" >>"$W_ROOT/dmesg" ;;
  renamed|unbound)
    # Contrived boards for the probe's two guards: a device bound to the driver under another identity, and the
    # right identity with no parent device bound to the driver.
    mkdir -p "$mod" "$cls/watchdog9"
    if [[ "$(cat "$W_ROOT/sc.$m")" == renamed ]]; then
      echo 'Some Other Timer' >"$cls/watchdog9/identity"
      mkdir -p "$W_ROOT/sys/bus/platform/drivers/$pdrv" "$W_ROOT/sys/devices/platform/$pdrv"
      ln -sfn "$W_ROOT/sys/bus/platform/drivers/$pdrv" "$W_ROOT/sys/devices/platform/$pdrv/driver"
      ln -sfn "$W_ROOT/sys/devices/platform/$pdrv" "$cls/watchdog9/device"
    else
      printf '%s\n' "$ident" >"$cls/watchdog9/identity"
    fi ;;
  disabled)
    mkdir -p "$mod"
    printf '[    6.10] sp5100_tco: SP5100/SB800 TCO WatchDog Timer Driver\n[    6.11] %s %s: Watchdog hardware is disabled\n' "$pdrv" "$pdrv" >>"$W_ROOT/dmesg" ;;
  *) echo "modprobe: ERROR: could not insert '$m': No such device" >&2; exit 1 ;;
esac
SH
cat >"$T/bin/dmesg" <<'SH'
#!/bin/sh
[ -s "$W_ROOT/dmesg" ] && cat "$W_ROOT/dmesg"
exit 0
SH
# systemctl: show -p RuntimeWatchdogUSec --value prints $W_ROOT/rt (0 by default); --no-reload enable marks the unit
# enabled unless $W_ROOT/enable_fails; is-enabled answers from that mark ($W_ROOT/is_enabled overrides the answer).
cat >"$T/bin/systemctl" <<'SH'
#!/bin/bash
echo "systemctl $*" >>"$W_ROOT/calls"
case "$*" in
  "show -p RuntimeWatchdogUSec --value") cat "$W_ROOT/rt" 2>/dev/null || echo 0 ;;
  *" enable "*) [[ -e "$W_ROOT/enable_fails" ]] && { echo "Failed to enable unit" >&2; exit 1; }; touch "$W_ROOT/enabled" ;;
  "is-enabled "*)
    if [[ -s "$W_ROOT/is_enabled" ]]; then cat "$W_ROOT/is_enabled"; exit 1; fi
    if [[ -e "$W_ROOT/enabled" ]]; then echo enabled; else echo disabled; exit 1; fi ;;
esac
exit 0
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" W_ROOT="$T/none"

die() { echo "DIE: $*" >>"$W_ROOT/out"; exit 99; }
log() { echo "LOG: $*" >>"$W_ROOT/out"; }
warn() { echo "WARN: $*" >>"$W_ROOT/out"; }
# The to-do list as lib/common.sh keeps it (latest record per ID wins): $W_ROOT/todo holds the IDs ("done ID" for a
# close), $W_ROOT/todo.title and todo.detail the TITLE and DETAIL of each todo_add.
todo_add() { echo "$1" >>"$W_ROOT/todo"; printf '%s\n' "$2" >>"$W_ROOT/todo.title"; printf '%s\n' "${3:-}" >>"$W_ROOT/todo.detail"; }
todo_done() { echo "done $1" >>"$W_ROOT/todo"; }
todo_is_open() { [[ -s "$W_ROOT/todo" ]] && [[ "$(grep -xE "(done )?$1" "$W_ROOT/todo" | tail -n1)" == "$1" ]]; }
relocate() { sed -e 's#/sys/#${W_ROOT}/sys/#g'; }
# shellcheck source=/dev/null
source <(grep -E '^ATLAS_WATCHDOG_[A-Z_]+=' "$REPO/phase1/04-system.sh"
         extract "$REPO/phase1/04-system.sh" phase1_write_file
         extract "$REPO/phase1/04-system.sh" _watchdog_unit
         extract "$REPO/phase1/04-system.sh" _watchdog_dropin
         for f in _watchdog_dev _watchdog_klines _watchdog; do extract "$REPO/phase1/04-system.sh" "$f" | relocate; done)
for f in phase1_write_file _watchdog_unit _watchdog_dropin _watchdog_dev _watchdog_klines _watchdog; do
  declare -F "$f" >/dev/null || { echo "FAIL could not extract $f"; exit 1; }
done
PROD_UNIT="$ATLAS_WATCHDOG_UNIT"; PROD_DROPIN="$ATLAS_WATCHDOG_DROPIN"
ok "production paths" "$PROD_UNIT|$PROD_DROPIN" \
   "/etc/systemd/system/atlas-watchdog-driver.service|/etc/systemd/system.conf.d/90-atlas-watchdog.conf"
ok "timeouts are whole minutes (the drop-in writes them in min)" \
   "$(( ATLAS_WATCHDOG_RUNTIME_SEC % 60 )):$(( ATLAS_WATCHDOG_REBOOT_SEC % 60 )):$ATLAS_WATCHDOG_RUNTIME_SEC:$ATLAS_WATCHDOG_REBOOT_SEC" "0:0:120:600"

# strict FUNC — runs FUNC as step 4 does (errexit, pipefail, an inherited ERR trap); prints "RC:ERR-TRAP-LINES".
strict() {
  : >"$W_ROOT/err"
  ( set -Eeo pipefail; trap 'echo "ERR trap: line $LINENO: $BASH_COMMAND" >>"$W_ROOT/err"' ERR; "$1" ); local rc=$?
  printf '%s:%s' "$rc" "$(cat "$W_ROOT/err")"
}
n=0
# node SP5100 [WDAT_TABLE [WDAT]] — a fresh node: the board's answer for sp5100_tco and wdat_wdt, the SMBus controller
# at 00:14.0 (1022:790b rev 0x71), an ACPI WDAT table when WDAT_TABLE is "wdat", /sys/class/watchdog empty.
node() {
  n=$((n + 1)); W_ROOT="$T/c$n"
  mkdir -p "$W_ROOT/sys/bus/pci/devices/0000:00:14.0" "$W_ROOT/sys/firmware/acpi/tables" "$W_ROOT/sys/class/watchdog" \
           "$W_ROOT/sys/module" "$W_ROOT/etc/systemd/system"
  printf '0x1022\n' >"$W_ROOT/sys/bus/pci/devices/0000:00:14.0/vendor"
  printf '0x790b\n' >"$W_ROOT/sys/bus/pci/devices/0000:00:14.0/device"
  printf '0x71\n' >"$W_ROOT/sys/bus/pci/devices/0000:00:14.0/revision"
  echo "$1" >"$W_ROOT/sc.sp5100_tco"; echo "${3:-none}" >"$W_ROOT/sc.wdat_wdt"; echo hw >"$W_ROOT/sc.softdog"
  [[ "${2:-}" == wdat ]] && : >"$W_ROOT/sys/firmware/acpi/tables/WDAT"
  : >"$W_ROOT/out"; : >"$W_ROOT/calls"; : >"$W_ROOT/dmesg"
  ATLAS_WATCHDOG_UNIT="$W_ROOT$PROD_UNIT"; ATLAS_WATCHDOG_DROPIN="$W_ROOT$PROD_DROPIN"
}
unit() { cat "$ATLAS_WATCHDOG_UNIT"; }
execstart() { sed -n 's/^ExecStart=//p' "$ATLAS_WATCHDOG_UNIT"; }
calls() { grep -c -- "$1" "$W_ROOT/calls" || true; }
lines() { grep -c -- "$1" "$W_ROOT/out" || true; }
todos() { paste -sd'|' "$W_ROOT/todo" 2>/dev/null || true; }
wds() { find "$W_ROOT/sys/class/watchdog" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | paste -sd' '; }

# --- 1. The rendered files (production paths) ---------------------------------------------------------------------
ATLAS_WATCHDOG_DROPIN="$PROD_DROPIN"
u="$(_watchdog_unit sp5100_tco)"
ok "unit: oneshot, kept active, one plain modprobe by name (never -b)" \
   "$(grep -E '^(Type|RemainAfterExit|ExecStart)=' <<<"$u" | paste -sd'|')" "Type=oneshot|RemainAfterExit=yes|ExecStart=/usr/sbin/modprobe sp5100_tco"
ok "unit: early boot, before sysinit.target, wanted by it" \
   "$(grep -E '^(DefaultDependencies|After|Before|Conflicts|WantedBy)=' <<<"$u" | paste -sd'|')" \
   "DefaultDependencies=no|After=systemd-modules-load.service|Before=sysinit.target shutdown.target|Conflicts=shutdown.target|WantedBy=sysinit.target"
ok "unit: softdog line" "$(_watchdog_unit 'softdog soft_panic=1' | sed -n 's/^ExecStart=//p')" "/usr/sbin/modprobe softdog soft_panic=1"
ok "unit: names the drop-in" "$(grep -c "RuntimeWatchdogSec in $PROD_DROPIN" <<<"$u")" "1"
d="$(_watchdog_dropin)"
ok "drop-in: [Manager] with exactly RuntimeWatchdogSec=2min and RebootWatchdogSec=10min" \
   "$(grep -v '^#' <<<"$d" | paste -sd'|')" "[Manager]|RuntimeWatchdogSec=2min|RebootWatchdogSec=10min"
ok "drop-in: no WatchdogDevice, pretimeout, kexec or CrashAction key" \
   "$(grep -v '^#' <<<"$d" | grep -cE 'WatchdogDevice|Pre|KExec|CrashAction' || true)" "0"
if command -v systemd-analyze >/dev/null; then
  F="$T/froot"; mkdir -p "$F/usr/sbin" "$F/etc/systemd/system"; printf '#!/bin/sh\nexit 0\n' >"$F/usr/sbin/modprobe"
  chmod +x "$F/usr/sbin/modprobe"
  for l in sp5100_tco 'softdog soft_panic=1'; do
    _watchdog_unit "$l" >"$F$PROD_UNIT"
    ok "systemd-analyze verify accepts the unit ($l)" "$(systemd-analyze verify --root="$F" "$F$PROD_UNIT" 2>&1; echo "rc=$?")" "rc=0"
  done
else
  echo "SKIP systemd-analyze verify (not installed)"
fi

# --- 2. _watchdog: hardware found -----------------------------------------------------------------------------------
node hw
ok "hardware: exits 0, no ERR trap" "$(strict _watchdog)" "0:"
ok "hardware: unit loads sp5100_tco" "$(execstart)" "/usr/sbin/modprobe sp5100_tco"
ok "hardware: unit is the rendered one" "$(cmp -s "$ATLAS_WATCHDOG_UNIT" <(_watchdog_unit sp5100_tco) && echo same)" "same"
ok "hardware: drop-in is the rendered one, mode 644" \
   "$(cmp -s "$ATLAS_WATCHDOG_DROPIN" <(_watchdog_dropin) && stat -c %a "$ATLAS_WATCHDOG_DROPIN")" "644"
ok "hardware: enabled without a daemon-reload, then checked" \
   "$(grep '^systemctl' "$W_ROOT/calls" | paste -sd'|')" \
   "systemctl show -p RuntimeWatchdogUSec --value|systemctl --no-reload enable atlas-watchdog-driver.service|systemctl is-enabled atlas-watchdog-driver.service"
ok "hardware: modprobe sp5100_tco by name only (no -b, no softdog, no wdat_wdt, no -r)" \
   "$(grep '^modprobe' "$W_ROOT/calls" | paste -sd'|')" "modprobe sp5100_tco"
ok "hardware: the driver stays loaded with its device" "$( [[ -d $W_ROOT/sys/module/sp5100_tco ]] && wds)" "watchdog0"
ok "hardware: no to-do" "$(todos)" ""
ok "hardware: one log line with what was found and written" \
   "$(lines "^LOG: watchdog (S50): hardware watchdog sp5100_tco 'SP5100 TCO timer' at watchdog0 (state inactive, timeout 60, nowayout 0, bootstatus 0); SMBus 00:14.0 0x1022 0x790b 0x71, ACPI WDAT absent, .*/sys/class/watchdog before: empty, PID 1 RuntimeWatchdogUSec=0, now: watchdog0; wrote .*atlas-watchdog-driver.service (modprobe sp5100_tco at every boot) and .*90-atlas-watchdog.conf (RuntimeWatchdogSec=2min, RebootWatchdogSec=10min); PID 1 arms it from the reboot, V24 checks it in step 5$")" "1"
ok "hardware: nothing else logged" "$(wc -l <"$W_ROOT/out")" "1"
# 2b. Rerun (rule §7.3): the same files byte for byte, the driver not loaded twice, no to-do.
cp "$ATLAS_WATCHDOG_UNIT" "$W_ROOT/unit.1"; cp "$ATLAS_WATCHDOG_DROPIN" "$W_ROOT/dropin.1"; : >"$W_ROOT/calls"; : >"$W_ROOT/out"
echo active >"$W_ROOT/sys/class/watchdog/watchdog0/state"; echo 120 >"$W_ROOT/sys/class/watchdog/watchdog0/timeout"; echo 2min >"$W_ROOT/rt"
ok "rerun: exits 0" "$(strict _watchdog)" "0:"
ok "rerun: both files unchanged" \
   "$(cmp -s "$ATLAS_WATCHDOG_UNIT" "$W_ROOT/unit.1" && cmp -s "$ATLAS_WATCHDOG_DROPIN" "$W_ROOT/dropin.1" && echo same)" "same"
ok "rerun: one device, nothing unloaded" "$(wds):$(calls 'modprobe -r')" "watchdog0:0"
ok "rerun: the log shows PID 1 already holding it (state active, timeout 120, RuntimeWatchdogUSec=2min)" \
   "$(lines "at watchdog0 (state active, timeout 120, nowayout 0, bootstatus 0); .*before: watchdog0, PID 1 RuntimeWatchdogUSec=2min,")" "1"
ok "rerun: still no to-do" "$(todos)" ""
# 2c. A to-do from an earlier run (softdog then, BIOS fixed since): closed once.
node hw; echo watchdog >"$W_ROOT/todo"
ok "hardware after an open to-do: exits 0" "$(strict _watchdog)" "0:"
ok "hardware after an open to-do: closed" "$(todos)" "watchdog|done watchdog"
: >"$W_ROOT/out"; strict _watchdog >/dev/null
ok "hardware after an open to-do: closed only once" "$(todos)" "watchdog|done watchdog"

# --- 3. _watchdog: no hardware watchdog -> softdog -----------------------------------------------------------------
# 3a. The FCH timer switched off by the firmware: the module loads, no device, the driver says why.
node disabled
ok "disabled: exits 0 (never stops the step)" "$(strict _watchdog)" "0:"
ok "disabled: unit loads softdog with soft_panic=1" "$(execstart)" "/usr/sbin/modprobe softdog soft_panic=1"
ok "disabled: drop-in written all the same" "$(cmp -s "$ATLAS_WATCHDOG_DROPIN" <(_watchdog_dropin) && echo same)" "same"
ok "disabled: sp5100_tco loaded by name, then unloaded; softdog not loaded now; no wdat_wdt" \
   "$(grep '^modprobe' "$W_ROOT/calls" | paste -sd'|')" "modprobe sp5100_tco|modprobe -r sp5100_tco"
ok "disabled: no module and no device left" "$(find "$W_ROOT/sys/module" "$W_ROOT/sys/class/watchdog" -mindepth 1 | wc -l)" "0"
ok "disabled: enabled without a daemon-reload" "$(calls 'systemctl --no-reload enable atlas-watchdog-driver.service'):$(calls 'daemon-reload')" "1:0"
ok "disabled: to-do watchdog raised once" "$(todos)" "watchdog"
ok "disabled: the to-do's title says what softdog covers" \
   "$(cat "$W_ROOT/todo.title")" "No hardware watchdog found: softdog stands in, which restarts a frozen or crashed systemd but not reliably a hard lockup of the whole kernel"
ok "disabled: the to-do quotes the driver and says what to check and how to re-run" \
   "$(grep -c "^S50. Step 4 found: sp5100_tco loaded but made no 'SP5100 TCO timer' device (kernel: sp5100_tco: SP5100/SB800 TCO WatchDog Timer Driver;sp5100-tco sp5100-tco: Watchdog hardware is disabled); no ACPI WDAT table\. .*BIOS offers a watchdog setting.*Never open /dev/watchdog\* .*phase1 --force 04 --force 05 " "$W_ROOT/todo.detail")" "1"
ok "disabled: one warning line" \
   "$(lines "^WARN: watchdog (S50): no hardware watchdog (sp5100_tco loaded but made no 'SP5100 TCO timer' device (kernel: .*Watchdog hardware is disabled); no ACPI WDAT table); SMBus 00:14.0 0x1022 0x790b 0x71, ACPI WDAT absent, .*; softdog stands in; wrote .*(modprobe softdog soft_panic=1 at every boot)"):$(wc -l <"$W_ROOT/out")" "1:1"
# 3b. Rerun with the fallback in place: softdog loaded at boot and held by PID 1; it is left alone.
mkdir -p "$W_ROOT/sys/module/softdog" "$W_ROOT/sys/class/watchdog/watchdog0"
echo 'Software Watchdog' >"$W_ROOT/sys/class/watchdog/watchdog0/identity"; echo active >"$W_ROOT/sys/class/watchdog/watchdog0/state"
cp "$ATLAS_WATCHDOG_UNIT" "$W_ROOT/unit.1"; : >"$W_ROOT/calls"; : >"$W_ROOT/out"
ok "fallback rerun: exits 0" "$(strict _watchdog)" "0:"
ok "fallback rerun: unit unchanged, softdog untouched" \
   "$(cmp -s "$ATLAS_WATCHDOG_UNIT" "$W_ROOT/unit.1" && echo same):$(calls 'softdog'):$(wds)" "same:0:watchdog0"
ok "fallback rerun: the to-do refreshed with this run's findings" "$(todos)" "watchdog|watchdog"
# 3c. No FCH SMBus device at all (sp5100_tco's init fails, the module never stays): nothing to unload.
node nodev
ok "no device: exits 0" "$(strict _watchdog)" "0:"
ok "no device: no unload attempted" "$(grep '^modprobe' "$W_ROOT/calls" | paste -sd'|')" "modprobe sp5100_tco"
ok "no device: softdog unit, to-do quoting modprobe" \
   "$(execstart):$(grep -c "^S50. Step 4 found: modprobe sp5100_tco: modprobe: ERROR: could not insert 'sp5100_tco': No such device ; no ACPI WDAT table\." "$W_ROOT/todo.detail")" \
   "/usr/sbin/modprobe softdog soft_panic=1:1"
# 3d. The unload refused (module in use): a warning, the step goes on.
node disabled; touch "$W_ROOT/rm_fails"
ok "unload refused: exits 0" "$(strict _watchdog)" "0:"
ok "unload refused: warned, softdog unit, to-do" \
   "$(lines '^WARN: watchdog: could not unload sp5100_tco (it made no device); the reboot clears it$'):$(execstart):$(todos)" \
   "1:/usr/sbin/modprobe softdog soft_panic=1:watchdog"

# 3e. The probe's guards: the right driver under another identity, or the right identity without the driver, is not
#     the FCH timer (research: hardware only when identity AND driver match).
node renamed
ok "another identity on sp5100-tco: not taken, softdog unit" "$(strict _watchdog):$(execstart):$(todos)" "0::/usr/sbin/modprobe softdog soft_panic=1:watchdog"
node unbound
ok "SP5100 identity without the driver bound: not taken, softdog unit" "$(strict _watchdog):$(execstart):$(todos)" "0::/usr/sbin/modprobe softdog soft_panic=1:watchdog"

# --- 4. _watchdog: the ACPI WDAT watchdog, only with a WDAT table -------------------------------------------------------
node disabled wdat hw
ok "WDAT: exits 0" "$(strict _watchdog)" "0:"
ok "WDAT: sp5100_tco tried and unloaded, then wdat_wdt" "$(grep '^modprobe' "$W_ROOT/calls" | paste -sd'|')" \
   "modprobe sp5100_tco|modprobe -r sp5100_tco|modprobe wdat_wdt"
ok "WDAT: unit loads wdat_wdt, no to-do" "$(execstart):$(todos)" "/usr/sbin/modprobe wdat_wdt:"
ok "WDAT: the log names it and why sp5100_tco was passed over" \
   "$(lines "^LOG: watchdog (S50): hardware watchdog wdat_wdt 'wdat_wdt' at watchdog0 (.*) (first: sp5100_tco loaded but made no 'SP5100 TCO timer' device (kernel: .*); SMBus .*, ACPI WDAT present,")" "1"
node disabled wdat none
ok "WDAT table, wdat_wdt fails: exits 0" "$(strict _watchdog)" "0:"
ok "WDAT table, wdat_wdt fails: softdog unit, to-do names both" \
   "$(execstart):$(grep -c "; wdat_wdt made no device (modprobe: ERROR: could not insert 'wdat_wdt': No such device )\. " "$W_ROOT/todo.detail")" \
   "/usr/sbin/modprobe softdog soft_panic=1:1"
node hw wdat hw
ok "WDAT table but sp5100_tco works: wdat_wdt never loaded" "$(strict _watchdog):$(calls 'wdat_wdt'):$(execstart)" "0::0:/usr/sbin/modprobe sp5100_tco"

# --- 5. _watchdog: a softdog from an earlier fallback boot, hardware now enabled in the BIOS ----------------------------
node hw; echo watchdog >"$W_ROOT/todo"
mkdir -p "$W_ROOT/sys/module/softdog" "$W_ROOT/sys/class/watchdog/watchdog0"
echo 'Software Watchdog' >"$W_ROOT/sys/class/watchdog/watchdog0/identity"; echo active >"$W_ROOT/sys/class/watchdog/watchdog0/state"
ok "softdog then hardware: exits 0" "$(strict _watchdog)" "0:"
ok "softdog then hardware: the SP5100 device is found as watchdog1, unit loads sp5100_tco" \
   "$(lines "hardware watchdog sp5100_tco 'SP5100 TCO timer' at watchdog1 .*before: watchdog0, .*now: watchdog0 watchdog1;"):$(execstart)" \
   "1:/usr/sbin/modprobe sp5100_tco"
ok "softdog then hardware: softdog left to the reboot (PID 1 holds it), to-do closed" "$(calls 'softdog'):$(todos)" "0:watchdog|done watchdog"

# --- 6. _watchdog: the unit cannot be enabled -> the step stops ------------------------------------------------------
node hw; touch "$W_ROOT/enable_fails"
r="$(strict _watchdog)"
ok "enable fails: dies" "${r%%:*}:$(lines '^DIE: systemctl enable atlas-watchdog-driver.service failed')" "99:1"
ok "enable fails: no drop-in written" "$( [[ -e "$ATLAS_WATCHDOG_DROPIN" ]] && echo written || echo none)" "none"
node hw; echo masked >"$W_ROOT/is_enabled"
r="$(strict _watchdog)"
ok "unit masked after enable: dies" "${r%%:*}:$(lines "^DIE: atlas-watchdog-driver.service is 'masked' after systemctl enable, not enabled")" "99:1"

# --- 7. Step 5 hands V24 the step 4 timeouts; step 5 stops on a V24 fail ----------------------------------------------
s5="$(extract "$REPO/phase1/05-postboot.sh" step_05)"
ok "step 5: V24 recorded through run_verify with the step 4 timeouts, stops on fail" \
   "$(grep -cE '^[[:space:]]+run_verify V24 v24-watchdog.sh "\$ATLAS_WATCHDOG_RUNTIME_SEC" "\$ATLAS_WATCHDOG_REBOOT_SEC" \|\| die "V24 failed' <<<"$s5")" "1"
ok "step 5: V24 after V3a" "$(grep -oE 'run_verify (V3a|V24) ' <<<"$s5" | awk '{print $2}' | paste -sd' ')" "V3a V24"

# --- 8. The Phase 1 gate (real lib/common.sh): V24 required, deferred never blocks, fail and missing do ----------------
gate_with() {  # gate_with V24_RESULT|missing -> "rc:FAIL/PASS line"
  local st="$T/gate$n"; n=$((n + 1)); rm -rf "$st"; mkdir -p "$st"
  ( export ATLAS_STATE="$st" ATLAS_PHASE=phase1 ATLAS_ENTRY=./atlas-day1.sh
    # shellcheck source=lib/common.sh
    source "$REPO/lib/common.sh"
    # shellcheck source=phase1/08-gate.sh
    source "$REPO/phase1/08-gate.sh"
    notify() { :; }
    for v in V2 V3a V5 V19; do record_v "$v" pass "ok" >/dev/null; done
    record_v V1 info "lan" >/dev/null
    [[ "$1" == missing ]] || record_v V24 "$1" "softdog or hardware" >/dev/null
    step_08 ) >"$T/gate.out" 2>&1
  local rc=$?
  printf '%s:%s' "$rc" "$(grep -E '^PHASE 1 GATE: ' "$T/gate.out")"
}
ok "gate: V24 pass" "$(gate_with pass)" "0:PHASE 1 GATE: PASS"
ok "gate: V24 deferred (softdog) does not block" "$(gate_with deferred)" "0:PHASE 1 GATE: PASS"
ok "gate: V24 fail blocks" "$(gate_with fail)" "1:PHASE 1 GATE: FAIL (V24=fail)"
ok "gate: V24 missing blocks" "$(gate_with missing)" "1:PHASE 1 GATE: FAIL (V24=missing)"
ok "gate: the table lists V24 as required" "$(grep -c '^PHASE 1 GATE — required: V2 V3a V5 V19 V24; recorded only: V1$' "$T/gate.out")" "1"

# --- 9. tools/fill-workbook.py: the V24 row ----------------------------------------------------------------------------
printf '%s\n' '{"ts":"t","phase":"phase1","id":"V23","result":"pass","msg":"token"}' \
  '{"ts":"t","phase":"phase1","id":"V24","result":"fail","msg":"not armed"}' \
  '{"ts":"t","phase":"phase1","id":"V24","result":"deferred","msg":"no hardware watchdog: softdog armed"}' >"$T/verify.jsonl"
ok "fill-workbook --print: V24 row, latest record" \
   "$(python3 -I "$REPO/tools/fill-workbook.py" --verify "$T/verify.jsonl" --print | grep '^V24 ' | tr -s ' ')" \
   "V24 Deferred no hardware watchdog: softdog armed"
if python3 -I -c 'import openpyxl' 2>/dev/null; then
  python3 -I - "$T/wb.xlsx" <<'PY'
import sys
from openpyxl import Workbook
wb = Workbook(); ws = wb.active; ws.title = "4 Verification"
ws.append(["4 Verification"]); ws.append([]); ws.append([])
ws.append(["ID", "Check", "Result", "Evidence or notes"])
for vid in ("V23", "V24"):
    ws.append([vid, "x", "Not yet run", ""])
wb.save(sys.argv[1])
PY
  python3 -I "$REPO/tools/fill-workbook.py" --verify "$T/verify.jsonl" --workbook "$T/wb.xlsx" --out "$T/wb.out.xlsx" >/dev/null
  ok "fill-workbook: the sheet's V24 row is filled" "$(python3 -I - "$T/wb.out.xlsx" <<'PY'
import sys
from openpyxl import load_workbook
ws = load_workbook(sys.argv[1])["4 Verification"]
print("|".join(f"{r[0].value}={r[2].value}" for r in ws.iter_rows(min_row=5)))
PY
)" "V23=Pass|V24=Deferred"
else
  echo "SKIP fill-workbook sheet case (openpyxl not installed)"
fi


# --- 10. Step 5's to-do w-drill (doc 3.7 W-DRILL), with the real lib/common.sh -----------------------------------------
drill_run() {  # drill_run STATE_DIR -> runs _drill_todo once in that state; prints nothing
  ( export ATLAS_STATE="$1" ATLAS_PHASE=phase1 ATLAS_ENTRY=./atlas-day1.sh
    # shellcheck source=lib/common.sh
    source "$REPO/lib/common.sh"
    export ATLAS_TODO_FILE="$1/todo.jsonl"
    eval "$(extract "$REPO/phase1/05-postboot.sh" _drill_todo)"
    _drill_todo ) >>"$1/out" 2>&1
}
drill_state() {  # drill_state STATE_DIR -> "open-records:latest-open(0|1)"
  python3 -I - "$1/todo.jsonl" <<'PY'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
d = [r for r in recs if r["id"] == "w-drill"]
print(f"{sum(1 for r in d if not r['done'])}:{0 if not d or d[-1]['done'] else 1}")
PY
}
ds="$T/drill"; rm -rf "$ds"; mkdir -p "$ds"
drill_run "$ds"
ok "w-drill: raised on the first run, open" "$(drill_state "$ds")" "1:1"
ok "w-drill: names the four tests and the Principal's go-ahead" \
   "$(python3 -I -c 'import json,sys; r=[json.loads(l) for l in open(sys.argv[1])][-1]; d=r["detail"]; print(all(k in d for k in ("(1) Crash restart","(2) Watchdog reset","(3) Timer survival","(4) Suspend","go-ahead")), r["title"].startswith("W-DRILL:"))' "$ds/todo.jsonl")" "True True"
drill_run "$ds"
ok "w-drill: a rerun while open adds no record" "$(drill_state "$ds")" "1:1"
( export ATLAS_STATE="$ds" ATLAS_PHASE=phase1 ATLAS_ENTRY=./atlas-day1.sh
  # shellcheck source=lib/common.sh
  source "$REPO/lib/common.sh"; export ATLAS_TODO_FILE="$ds/todo.jsonl"; todo_done w-drill ) >>"$ds/out" 2>&1
ok "w-drill: closed by the live ATLAS" "$(drill_state "$ds")" "1:0"
drill_run "$ds"
ok "w-drill: step 5 raises a closed one again" "$(drill_state "$ds")" "2:1"
ok "step 5: _drill_todo runs last, after the panic-log record" \
   "$(grep -oE '^[[:space:]]+_(panic_log_record|drill_todo)$' <<<"$s5" | tr -d ' ' | paste -sd' ')" "_panic_log_record _drill_todo"
echo "watchdog_test: $pass passed, $fail failed"
(( fail == 0 ))
