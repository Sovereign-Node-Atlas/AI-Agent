#!/usr/bin/env bash
# phase1/kernel_line_test.sh — regression test (v0.3.5, doc S45) for phase1/04-system.sh's kernel-line helpers
# (_kl_installed, phase1_kernel_line_report, _kernel_line) and phase1/05-postboot.sh's _npu_record, with stubbed
# dpkg-query, apt-get, uname, lspci and dmesg. The stub dpkg-query matches its patterns as globs against a package list,
# the way dpkg-query -W does; the stub apt-get prints the "Purg" lines of a simulation and edits that list on a real
# purge. Touches nothing outside a fresh temp dir. Run: bash phase1/kernel_line_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/sys/0000:c5:00.1" "$T/drivers/amdxdna" "$T/accel"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
extract() { awk -v n="$2" '$0 ~ "^"n"\\(\\) \\{" {on=1} on{print} on && /^\}/{exit}' "$1"; }

# Installed packages, one per line, in $T/pkgs; extra lines a purge simulation would print, in $T/purge_extra.
cat >"$T/bin/dpkg-query" <<'SH'
#!/bin/bash
pats=(); for a in "$@"; do case "$a" in -W|-f=*) ;; *) pats+=("$a") ;; esac; done
while read -r p; do
  [[ -n "$p" ]] || continue
  for g in "${pats[@]}"; do
    # shellcheck disable=SC2053  # glob match on purpose, as dpkg-query does
    if [[ "$p" == $g ]]; then printf 'ii  %s\n' "$p"; break; fi
  done
done <"$KL_T/pkgs"
SH
cat >"$T/bin/apt-get" <<'SH'
#!/bin/bash
echo "apt-get $*" >>"$KL_T/apt.log"
sim=0; purge=0; pk=()
for a in "$@"; do case "$a" in -s) sim=1 ;; purge) purge=1 ;; -*) ;; *) pk+=("$a") ;; esac; done
(( purge )) || exit 0
if (( sim )); then
  for p in "${pk[@]}"; do echo "Purg $p [1.0]"; done
  [[ -s "$KL_T/purge_extra" ]] && cat "$KL_T/purge_extra"
  exit 0
fi
for p in "${pk[@]}"; do grep -vxF -- "$p" "$KL_T/pkgs" >"$KL_T/pkgs.new" || true; mv "$KL_T/pkgs.new" "$KL_T/pkgs"; done
SH
cat >"$T/bin/uname" <<'SH'
#!/bin/sh
cat "$KL_T/uname"
SH
cat >"$T/bin/lspci" <<'SH'
#!/bin/sh
[ -s "$KL_T/lspci" ] && cat "$KL_T/lspci"
exit 0
SH
cat >"$T/bin/dmesg" <<'SH'
#!/bin/sh
[ -s "$KL_T/dmesg" ] && cat "$KL_T/dmesg"
exit 0
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" KL_T="$T"

die() { echo "DIE: $*"; return 99; }
log() { echo "LOG: $*" >>"$T/out"; }
warn() { echo "WARN: $*" >>"$T/out"; }
todo_add() { echo "$1" >>"$T/todo"; }
todo_done() { echo "done $1" >>"$T/todo"; }
todo_is_open() { [[ -s "$T/todo" ]] && grep -qx "$1" "$T/todo" && ! grep -qx "done $1" "$T/todo"; }
apt_install() { echo "apt_install $*" >>"$T/apt.log"; local p; for p in "$@"; do grep -qxF -- "$p" "$T/pkgs" || echo "$p" >>"$T/pkgs"; done; }
# shellcheck source=/dev/null
source <(grep -E '^ATLAS_KERNEL_GA_METAS=' "$REPO/phase1/04-system.sh"
         for f in _kl_installed phase1_kernel_line_report phase1_kernel_line_todo _kernel_line; do
           extract "$REPO/phase1/04-system.sh" "$f"; done
         extract "$REPO/phase1/05-postboot.sh" _npu_record | sed -e "s#/sys/bus/pci/devices#$T/sys#g" -e "s#find /dev/accel #find $T/accel #")

reset_case() {  # reset_case UNAME PACKAGE...
  : >"$T/out"; : >"$T/apt.log"; : >"$T/todo"; : >"$T/purge_extra"
  printf '%s\n' "$1" >"$T/uname"; shift
  printf '%s\n' "$@" >"$T/pkgs"
}
GA=(linux-generic linux-image-generic linux-headers-generic linux-image-7.0.0-38-generic linux-modules-7.0.0-38-generic
    linux-headers-7.0.0-38-generic ubuntu-server)
HWE=(linux-generic-hwe-26.04 linux-image-generic-hwe-26.04 linux-headers-generic-hwe-26.04 linux-image-7.0.0-38-generic
     linux-modules-7.0.0-38-generic linux-headers-7.0.0-38-generic ubuntu-server)

# 1. The GA line as Section 3.3 wants it: reported as such, nothing installed or removed.
reset_case 7.0.0-38-generic "${GA[@]}"
phase1_kernel_line_report >/dev/null; ok "GA line: report rc" "$?" "0"
_kernel_line
ok "GA line: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "GA line: no to-do" "$(wc -l <"$T/todo")" "0"

# 2. HWE metapackages over the GA 7.0 image (the 26.04.1 case): GA installed, HWE purged after a clean simulation.
reset_case 7.0.0-38-generic "${HWE[@]}"
phase1_kernel_line_report >/dev/null; ok "HWE on 7.0: report rc" "$?" "1"
_kernel_line
ok "HWE on 7.0: linux-generic installed" "$(grep -c '^apt_install linux-generic$' "$T/apt.log")" "1"
ok "HWE on 7.0: simulated first" "$(grep -n 'apt-get -s purge' "$T/apt.log" | cut -d: -f1)" "2"
ok "HWE on 7.0: purge names the three metas" \
   "$(grep -c '^apt-get -y -q purge linux-generic-hwe-26.04 linux-headers-generic-hwe-26.04 linux-image-generic-hwe-26.04$' "$T/apt.log")" "1"
phase1_kernel_line_report >/dev/null; ok "HWE on 7.0: GA afterwards" "$?" "0"
ok "HWE on 7.0: image kept" "$(grep -cx linux-image-7.0.0-38-generic "$T/pkgs")" "1"
ok "HWE on 7.0: no to-do" "$(wc -l <"$T/todo")" "0"

# 3. The simulation would take more than the metapackages: nothing purged, to-do raised.
reset_case 7.0.0-38-generic "${HWE[@]}"
echo "Purg ubuntu-server [1.0]" >"$T/purge_extra"
_kernel_line
ok "purge takes more: no real purge" "$(grep -c '^apt-get -y -q purge' "$T/apt.log")" "0"
ok "purge takes more: to-do" "$(cat "$T/todo")" "kernel-line"
ok "purge takes more: names it" "$(grep -c 'would also remove: ubuntu-server' "$T/out")" "1"

# 4. An HWE image outside 7.0 is installed (unattended-upgrades after the roll): never automatic.
reset_case 7.0.0-38-generic "${HWE[@]}" linux-image-7.3.0-9-generic
_kernel_line
ok "7.3 image: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "7.3 image: to-do" "$(cat "$T/todo")" "kernel-line"

# 5. Running a 7.3 kernel (installed from a 26.04.2+ image): never automatic.
reset_case 7.3.0-9-generic linux-generic-hwe-26.04 linux-image-generic-hwe-26.04 linux-image-7.3.0-9-generic
_kernel_line
ok "running 7.3: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "running 7.3: to-do" "$(cat "$T/todo")" "kernel-line"

# 6. An OEM kernel (metapackage or -oem uname): never automatic.
reset_case 7.0.0-1015-oem "${GA[@]}" linux-oem-26.04 linux-image-oem-26.04 linux-image-7.0.0-1015-oem
phase1_kernel_line_report >/dev/null; ok "OEM: report rc" "$?" "1"
_kernel_line
ok "OEM: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "OEM: to-do" "$(cat "$T/todo")" "kernel-line"
ok "OEM: report names it" "$(phase1_kernel_line_report | grep -c 'OEM: linux-image-oem-26.04 linux-oem-26.04;')" "1"

# 7. GA line but an older 7.0 image beside the current one, and an unsigned image: still GA 7.0.
reset_case 7.0.0-38-generic "${GA[@]}" linux-image-7.0.0-36-generic linux-image-unsigned-7.0.0-37-generic
phase1_kernel_line_report >/dev/null; ok "two 7.0 images: GA" "$?" "0"
ok "images listed without prefix" "$(phase1_kernel_line_report | grep -o 'images: .*')" \
   "images: 7.0.0-36-generic 7.0.0-37-generic 7.0.0-38-generic"

# 8. GA metapackage missing altogether (only the image): not GA, and not the automatic case (no HWE metas).
reset_case 7.0.0-38-generic linux-image-7.0.0-38-generic
_kernel_line
ok "no metapackage: to-do, nothing installed" "$(cat "$T/todo"):$(wc -l <"$T/apt.log")" "kernel-line:0"

# 9. GA and HWE metapackages side by side: not GA (the HWE ones would still roll), switched with nothing new installed.
reset_case 7.0.0-38-generic "${GA[@]}" linux-generic-hwe-26.04 linux-image-generic-hwe-26.04
phase1_kernel_line_report >/dev/null; ok "GA+HWE: report rc" "$?" "1"
_kernel_line
ok "GA+HWE: HWE purged" "$(grep -c 'hwe' "$T/pkgs")" "0"
phase1_kernel_line_report >/dev/null; ok "GA+HWE: GA afterwards" "$?" "0"
ok "GA+HWE: no to-do" "$(wc -l <"$T/todo")" "0"

# 10. HWE metapackages, only 7.0 generic images, but the running kernel is not one of them (a kernel booted from
#     outside dpkg's view): never automatic.
reset_case 7.0.0-1015-oem "${HWE[@]}"
_kernel_line
ok "foreign running kernel: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "foreign running kernel: to-do" "$(cat "$T/todo")" "kernel-line"
reset_case 7.0.0-1015-oem "${GA[@]}"
phase1_kernel_line_report >/dev/null; ok "foreign running kernel, GA packages: report rc" "$?" "1"

# 11. HWE metapackages plus an OEM enablement metapackage, no OEM image yet: never automatic.
reset_case 7.0.0-38-generic "${HWE[@]}" oem-gmktec-evo-x5-meta
phase1_kernel_line_report >/dev/null; ok "OEM enablement meta: report rc" "$?" "1"
_kernel_line
ok "OEM enablement meta: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "OEM enablement meta: to-do" "$(cat "$T/todo")" "kernel-line"

# 12. A to-do left open by an earlier run closes once the switch succeeds.
reset_case 7.0.0-38-generic "${HWE[@]}"
echo kernel-line >"$T/todo"
_kernel_line
ok "earlier to-do closed after the switch" "$(tail -n1 "$T/todo")" "done kernel-line"

# 13. _npu_record: bound NPU, revision and driver read from sysfs, accel node listed; then amdxdna errors warned.
: >"$T/out"
echo "0000:c5:00.1 1180: 1022:17f0 (rev 11)" >"$T/lspci"
echo 0x11 >"$T/sys/0000:c5:00.1/revision"
ln -sfn "$T/drivers/amdxdna" "$T/sys/0000:c5:00.1/driver"
: >"$T/accel/accel0"
: >"$T/dmesg"
_npu_record
ok "npu: one log line" "$(grep -c '^LOG: npu: 0000:c5:00.1 1022:17f0 revision 0x11, driver amdxdna, /dev/accel: accel0 (not used' "$T/out")" "1"
ok "npu: no warning" "$(grep -c '^WARN' "$T/out")" "0"
echo "amdxdna 0000:c5:00.1: failed to load firmware" >"$T/dmesg"
_npu_record
ok "npu: error warned" "$(grep -c '^WARN: npu: amdxdna reported: amdxdna 0000:c5:00.1: failed to load firmware' "$T/out")" "1"
: >"$T/out"; : >"$T/lspci"; rm -f "$T/sys/0000:c5:00.1/driver"
_npu_record
ok "npu: absent device logged, no warning" "$(grep -c '^LOG: npu: no 1022:17f0' "$T/out"):$(grep -c '^WARN' "$T/out")" "1:0"

echo "kernel_line_test: $pass passed, $fail failed"
(( fail == 0 ))
