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
# Like the real dpkg-query -W: every pattern that matches no package is reported on stderr and makes the exit status 1,
# even when other patterns matched (the review v0.3.5 finding: under pipefail that aborted step 4). Packages listed in
# $T/held print as "hi" (apt-mark hold). A format asking for ${source:Package} gets a third column: the source named
# for the package in $T/src ("pkg src" lines), linux-meta otherwise (every kernel metapackage comes from linux-meta).
cat >"$T/bin/dpkg-query" <<'SH'
#!/bin/bash
pats=(); src=0; rc=0
for a in "$@"; do case "$a" in -W) ;; -f=*) [[ "$a" == *source:Package* ]] && src=1 ;; *) pats+=("$a") ;; esac; done
for g in "${pats[@]}"; do
  hit=0
  while read -r p; do
    [[ -n "$p" ]] || continue
    # shellcheck disable=SC2053  # glob match on purpose, as dpkg-query does
    [[ "$p" == $g ]] || continue
    hit=1
    st="ii"; grep -qxF -- "$p" "$KL_T/held" 2>/dev/null && st="hi"
    if (( src )); then
      s="$(awk -v p="$p" '$1 == p {print $2; exit}' "$KL_T/src" 2>/dev/null)"
      printf '%s  %s %s\n' "$st" "$p" "${s:-linux-meta}"
    else printf '%s  %s\n' "$st" "$p"; fi
  done <"$KL_T/pkgs"
  (( hit )) || { echo "dpkg-query: no packages found matching $g" >&2; rc=1; }
done
exit "$rc"
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
source <(grep -E '^ATLAS_KERNEL_(GA_METAS|HWE_GLOBS|OEM_GLOBS)=' "$REPO/phase1/04-system.sh"
         for f in _kl_installed phase1_kernel_line_report phase1_kernel_line_todo _kernel_line; do
           extract "$REPO/phase1/04-system.sh" "$f"; done
         extract "$REPO/phase1/05-postboot.sh" _npu_record | sed -e "s#/sys/bus/pci/devices#$T/sys#g" -e "s#find /dev/accel #find $T/accel #")

# Every _kernel_line and _npu_record call runs as step 4 and step 5 do: errexit, pipefail and an inherited ERR trap
# (lib/common.sh). It must return 0 and trip the trap nowhere, not even in a process substitution.
n_strict=0
strict() {  # strict FUNCTION — run it under the production shell options; records a PASS/FAIL of its own
  n_strict=$((n_strict + 1)); : >"$T/err"
  ( set -Eeo pipefail; trap 'echo "ERR trap: line $LINENO: $BASH_COMMAND" >>"$T/err"' ERR; "$1" ); local rc=$?
  ok "strict run $n_strict ($1): exit 0, no ERR trap" "$rc:$(cat "$T/err")" "0:"
}
reset_case() {  # reset_case UNAME PACKAGE...
  : >"$T/out"; : >"$T/apt.log"; : >"$T/todo"; : >"$T/purge_extra"; : >"$T/src"; : >"$T/held"
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
strict _kernel_line
ok "GA line: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "GA line: no to-do" "$(wc -l <"$T/todo")" "0"

# 2. HWE metapackages over the GA 7.0 image (the 26.04.1 case): GA installed, HWE purged after a clean simulation.
reset_case 7.0.0-38-generic "${HWE[@]}"
phase1_kernel_line_report >/dev/null; ok "HWE on 7.0: report rc" "$?" "1"
strict _kernel_line
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
strict _kernel_line
ok "purge takes more: no real purge" "$(grep -c '^apt-get -y -q purge' "$T/apt.log")" "0"
ok "purge takes more: to-do" "$(cat "$T/todo")" "kernel-line"
ok "purge takes more: names it" "$(grep -c 'would also remove: ubuntu-server' "$T/out")" "1"

# 4. An HWE image outside 7.0 is installed (unattended-upgrades after the roll): never automatic.
reset_case 7.0.0-38-generic "${HWE[@]}" linux-image-7.3.0-9-generic
strict _kernel_line
ok "7.3 image: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "7.3 image: to-do" "$(cat "$T/todo")" "kernel-line"

# 5. Running a 7.3 kernel (installed from a 26.04.2+ image): never automatic.
reset_case 7.3.0-9-generic linux-generic-hwe-26.04 linux-image-generic-hwe-26.04 linux-image-7.3.0-9-generic
strict _kernel_line
ok "running 7.3: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "running 7.3: to-do" "$(cat "$T/todo")" "kernel-line"

# 6. An OEM kernel (metapackage or -oem uname): never automatic.
reset_case 7.0.0-1015-oem "${GA[@]}" linux-oem-26.04 linux-image-oem-26.04 linux-image-7.0.0-1015-oem
phase1_kernel_line_report >/dev/null; ok "OEM: report rc" "$?" "1"
strict _kernel_line
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
strict _kernel_line
ok "no metapackage: to-do, nothing installed" "$(cat "$T/todo"):$(wc -l <"$T/apt.log")" "kernel-line:0"

# 9. GA and HWE metapackages side by side: not GA (the HWE ones would still roll), switched with nothing new installed.
reset_case 7.0.0-38-generic "${GA[@]}" linux-generic-hwe-26.04 linux-image-generic-hwe-26.04
phase1_kernel_line_report >/dev/null; ok "GA+HWE: report rc" "$?" "1"
strict _kernel_line
ok "GA+HWE: HWE purged" "$(grep -c 'hwe' "$T/pkgs")" "0"
phase1_kernel_line_report >/dev/null; ok "GA+HWE: GA afterwards" "$?" "0"
ok "GA+HWE: no to-do" "$(wc -l <"$T/todo")" "0"

# 10. HWE metapackages, only 7.0 generic images, but the running kernel is not one of them (a kernel booted from
#     outside dpkg's view): never automatic.
reset_case 7.0.0-1015-oem "${HWE[@]}"
strict _kernel_line
ok "foreign running kernel: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "foreign running kernel: to-do" "$(cat "$T/todo")" "kernel-line"
reset_case 7.0.0-1015-oem "${GA[@]}"
phase1_kernel_line_report >/dev/null; ok "foreign running kernel, GA packages: report rc" "$?" "1"

# 11. HWE metapackages plus an OEM enablement metapackage, no OEM image yet: never automatic.
reset_case 7.0.0-38-generic "${HWE[@]}" oem-gmktec-evo-x5-meta
phase1_kernel_line_report >/dev/null; ok "OEM enablement meta: report rc" "$?" "1"
strict _kernel_line
ok "OEM enablement meta: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "OEM enablement meta: to-do" "$(cat "$T/todo")" "kernel-line"

# 12a. A system upgraded from 24.04 carries the 24.04 transitionals (linux-meta, Depending on the -hwe-26.04 ones):
#      purged together with them.
reset_case 7.0.0-38-generic "${HWE[@]}" linux-generic-hwe-24.04 linux-image-generic-hwe-24.04 linux-oem-24.04c
strict _kernel_line
ok "24.04 transitionals: all purged" "$(grep -cE 'hwe|oem' "$T/pkgs")" "0"
ok "24.04 transitionals: no to-do" "$(wc -l <"$T/todo")" "0"

# 12b. A metapackage that matches the HWE globs but is not Ubuntu's linux-meta: never purged here.
reset_case 7.0.0-38-generic "${HWE[@]}" linux-nvidia-hwe-24.04
echo "linux-nvidia-hwe-24.04 linux-meta-nvidia" >"$T/src"
strict _kernel_line
ok "foreign source: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "foreign source: named" "$(grep -c 'never purged here: linux-nvidia-hwe-24.04' "$T/out")" "1"
ok "foreign source: to-do" "$(cat "$T/todo")" "kernel-line"

# 12c. The OEM 7.0 metapackage family and a platform hwe-*-meta count as OEM.
reset_case 7.0.0-38-generic "${GA[@]}" linux-oem-7.0
phase1_kernel_line_report >/dev/null; ok "linux-oem-7.0: not GA" "$?" "1"
reset_case 7.0.0-38-generic "${GA[@]}" hwe-dgx-gb10-meta
ok "hwe-*-meta: reported as OEM" "$(phase1_kernel_line_report | grep -c 'OEM: hwe-dgx-gb10-meta;')" "1"

# 12d. Held packages (apt-mark hold, status "hi") are installed packages: held GA metapackages are still GA, and a held
#      HWE metapackage beside GA is still HWE.
reset_case 7.0.0-38-generic "${GA[@]}"
printf '%s\n' linux-generic linux-image-generic linux-headers-generic >"$T/held"
phase1_kernel_line_report >/dev/null; ok "held GA metapackages: GA" "$?" "0"
reset_case 7.0.0-38-generic "${GA[@]}" linux-generic-hwe-26.04
echo linux-generic-hwe-26.04 >"$T/held"
phase1_kernel_line_report >/dev/null; ok "held HWE beside GA: not GA" "$?" "1"

# 12e. A hold is the Principal's: held GA metapackages beside HWE ones, or held HWE metapackages, are never changed (apt
#      refuses to, and apt_install would die): no apt call, the to-do instead, naming apt-mark unhold.
reset_case 7.0.0-38-generic "${GA[@]}" linux-generic-hwe-26.04 linux-image-generic-hwe-26.04
printf '%s\n' linux-generic linux-image-generic linux-headers-generic >"$T/held"
strict _kernel_line
ok "held GA beside HWE: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "held GA beside HWE: to-do" "$(cat "$T/todo")" "kernel-line"
ok "held GA beside HWE: names the hold" "$(grep -c 'held with apt-mark, left to the Principal: linux-generic linux-headers-generic linux-image-generic' "$T/out")" "1"
reset_case 7.0.0-38-generic "${HWE[@]}"
echo linux-generic-hwe-26.04 >"$T/held"
strict _kernel_line
ok "held HWE: no apt call" "$(wc -l <"$T/apt.log")" "0"
ok "held HWE: to-do" "$(cat "$T/todo")" "kernel-line"

# 12. A to-do left open by an earlier run closes once the switch succeeds.
reset_case 7.0.0-38-generic "${HWE[@]}"
echo kernel-line >"$T/todo"
strict _kernel_line
ok "earlier to-do closed after the switch" "$(tail -n1 "$T/todo")" "done kernel-line"

# 13. _npu_record: bound NPU, revision and driver read from sysfs, accel node listed; then amdxdna errors warned.
: >"$T/out"
echo "0000:c5:00.1 1180: 1022:17f0 (rev 11)" >"$T/lspci"
echo 0x11 >"$T/sys/0000:c5:00.1/revision"
ln -sfn "$T/drivers/amdxdna" "$T/sys/0000:c5:00.1/driver"
: >"$T/accel/accel0"
: >"$T/dmesg"
strict _npu_record
ok "npu: one log line" "$(grep -c '^LOG: npu: 0000:c5:00.1 1022:17f0 revision 0x11, driver amdxdna, /dev/accel: accel0 (not used' "$T/out")" "1"
ok "npu: no warning" "$(grep -c '^WARN' "$T/out")" "0"
echo "amdxdna 0000:c5:00.1: failed to load firmware" >"$T/dmesg"
strict _npu_record
ok "npu: error warned" "$(grep -c '^WARN: npu: amdxdna reported: amdxdna 0000:c5:00.1: failed to load firmware' "$T/out")" "1"
: >"$T/out"; : >"$T/lspci"; rm -f "$T/sys/0000:c5:00.1/driver"
strict _npu_record
ok "npu: absent device logged, no warning" "$(grep -c '^LOG: npu: no 1022:17f0' "$T/out"):$(grep -c '^WARN' "$T/out")" "1:0"

echo "kernel_line_test: $pass passed, $fail failed"
(( fail == 0 ))
