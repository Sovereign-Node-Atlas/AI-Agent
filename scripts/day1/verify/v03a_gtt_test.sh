#!/usr/bin/env bash
# verify/v03a_gtt_test.sh — regression test (v0.3.5, doc S47) for verify/v03a-gtt.sh's GTT pool check. Kernel 7.0.0-38
# reports the requested 196608 MiB whatever MemTotal is; 7.0.0-39 and later cap the pool at MemTotal and log "Capping
# GTT to <N>M". Both must pass; the ~50 % TTM default and anything in between must fail. Since S48 (option (c)) also the
# crash-kernel and panic checks: crashkernel= anywhere on the cmdline, panic=10 missing or not alone, kernel.panic not
# 10, panic_on_oops not 0, and crash-kernel memory reserved (kexec_crash_size, /proc/iomem) each fail. Runs a copy of the
# script with /proc, /sys/module, /sys/kernel/kexec_crash_size and the GTT reader redirected to a fresh temp dir;
# touches nothing else.
# Run: bash verify/v03a_gtt_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/mod/ttm/parameters" "$T/mod/amdgpu/parameters" "$T/procsys"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

BASE_CMDLINE="BOOT_IMAGE=/vmlinuz root=x amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000 panic=10"
echo "$BASE_CMDLINE" >"$T/cmdline"
echo 10 >"$T/procsys/panic"
echo 0 >"$T/procsys/panic_on_oops"
echo 0 >"$T/kexec_crash_size"
printf '00000000-00000fff : Reserved\n00001000-0009ffff : System RAM\n100000000-2f7ffffff : System RAM\n' >"$T/iomem"
echo 50331648 >"$T/mod/ttm/parameters/pages_limit"
echo 196608 >"$T/mod/amdgpu/parameters/gttsize"
echo 10000,60000,10000,10000 >"$T/mod/amdgpu/parameters/lockup_timeout"
printf 'MemTotal:       195035136 kB\n' >"$T/meminfo"   # 190464 MiB: 192 GiB less carve-out and reservations
printf '#!/bin/sh\ncat "%s/dmesg" 2>/dev/null\nexit 0\n' "$T" >"$T/bin/dmesg"
printf '#!/bin/sh\necho "  deviceName = AMD Radeon Graphics (RADV GFX1151)"\n' >"$T/bin/vulkaninfo"
chmod +x "$T/bin/"*
sed -e "s#/proc/cmdline#$T/cmdline#" -e "s#/sys/module/#$T/mod/#g" -e "s#/proc/meminfo#$T/meminfo#" \
    -e "s#/proc/sys/kernel/#$T/procsys/#g" -e "s#/sys/kernel/kexec_crash_size#$T/kexec_crash_size#g" -e "s#/proc/iomem#$T/iomem#g" \
    -e "s#^source \"\$(dirname \"\$(readlink -f \"\$0\")\")/../lib/common.sh\"#source \"$REPO/lib/common.sh\"; gpu_gtt_total_mb() { cat \"$T/gtt\"; }#" \
    "$REPO/verify/v03a-gtt.sh" >"$T/v3a.sh"
grep -q 'gpu_gtt_total_mb() { cat' "$T/v3a.sh" || { echo "FAIL could not redirect the GTT reader (source line changed?)"; exit 1; }
for f in procsys/panic procsys/panic_on_oops kexec_crash_size iomem; do
  grep -qF "$T/$f" "$T/v3a.sh" || { echo "FAIL could not redirect $f (path changed in v03a-gtt.sh?)"; exit 1; }
done

v3a() {  # v3a GTT_MIB DMESG -> "rc:first 60 chars"
  echo "$1" >"$T/gtt"; printf '%s\n' "$2" >"$T/dmesg"
  local out rc
  out="$(PATH="$T/bin:$PATH" bash "$T/v3a.sh" 2>/dev/null)"; rc=$?
  printf '%s' "$rc:$out"
}
r="$(v3a 196608 'amdgpu 0000:c5:00.0: 196608M of GTT memory ready.')"
ok "uncapped (7.0.0-38): pass" "${r%%:*}" "0"
ok "uncapped: says so" "$(grep -c 'uncapped' <<<"$r")" "1"
r="$(v3a 190464 'amdgpu 0000:c5:00.0: Capping GTT to 190464M to not exceed available system memory')"
ok "capped at MemTotal (7.0.0-39): pass" "${r%%:*}" "0"
ok "capped: quotes the dmesg line" "$(grep -c 'Capping GTT to 190464M' <<<"$r")" "1"
r="$(v3a 189000 '')"
ok "capped, within 1 % below MemTotal: pass" "${r%%:*}" "0"
r="$(v3a 95232 '')"
ok "TTM default (~50 %): fail" "${r%%:*}" "1"
r="$(v3a 193000 '')"
ok "between MemTotal and the request: fail" "${r%%:*}" "1"
r="$(v3a 196610 '')"
ok "above the request: fail" "${r%%:*}" "1"
sed -i 's/ amdgpu.lockup_timeout=10000,60000,10000,10000//' "$T/cmdline"
r="$(v3a 196608 '')"
ok "parameter missing from the cmdline still fails" "${r%%:*}:$(grep -c 'cmdline lacks amdgpu.lockup_timeout' <<<"$r")" "1:1"

# S48 (option (c)): no crash kernel, panic=10. Each case starts from the passing node and changes one thing.
ready='amdgpu 0000:c5:00.0: 196608M of GTT memory ready.'
base() { echo "$BASE_CMDLINE" >"$T/cmdline"; echo 10 >"$T/procsys/panic"; echo 0 >"$T/procsys/panic_on_oops"
         echo 0 >"$T/kexec_crash_size"; sed -i '/Crash kernel/d' "$T/iomem"; }
base; r="$(v3a 196608 "$ready")"
ok "S48 base: pass" "${r%%:*}" "0"
ok "S48 base: summary names the crash and panic state" \
   "$(grep -c "crash kernel: kexec_crash_size=0, iomem regions=0; panic: cmdline 'panic=10', kernel.panic=10, panic_on_oops=0" <<<"$r")" "1"
base; echo "$BASE_CMDLINE crashkernel=2G-4G:320M,4G-32G:512M,32G-64G:1024M,64G-128G:2048M,128G-:4096M" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "crashkernel= on the cmdline: fail, names it" "${r%%:*}:$(grep -c 'cmdline carries crashkernel=' <<<"$r")" "1:1"
base; echo "$BASE_CMDLINE foo=\"crashkernel=512M\"" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "crashkernel= inside another word (the kernel's strstr finds it): fail" "${r%%:*}:$(grep -c 'cmdline carries crashkernel=' <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE% panic=10}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "panic=10 missing: fail" "${r%%:*}:$(grep -c 'cmdline lacks panic=10' <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE% panic=10} panic=0 panic=10" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "a second panic= word: fail, names both" "${r%%:*}:$(grep -c "cmdline carries 'panic=0 panic=10', want exactly one panic=10" <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE% panic=10} panic=5" >"$T/cmdline"; echo 5 >"$T/procsys/panic"
r="$(v3a 196608 "$ready")"
ok "panic=5 instead of 10: fail" "${r%%:*}:$(grep -c "cmdline carries 'panic=5'" <<<"$r"):$(grep -c 'kernel.panic live=5' <<<"$r")" "1:1:1"
base; echo "${BASE_CMDLINE% panic=10} \"panic=10\" panic=10" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "a quoted second panic= word counts too: fail" "${r%%:*}" "1"
base; echo "$BASE_CMDLINE oops=panic panic_on_warn=0" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "oops=panic and panic_on_warn= are not panic= words: pass" "${r%%:*}" "0"
base; echo 0 >"$T/procsys/panic"
r="$(v3a 196608 "$ready")"
ok "kernel.panic not 10 (a sysctl override): fail, points at sysctl.d" "${r%%:*}:$(grep -c 'kernel.panic live=0 (want 10: a sysctl override' <<<"$r")" "1:1"
base; rm -f "$T/procsys/panic"
r="$(v3a 196608 "$ready")"
ok "kernel.panic unreadable: fail" "${r%%:*}:$(grep -c 'kernel.panic live=missing' <<<"$r")" "1:1"
base; echo 1 >"$T/procsys/panic_on_oops"
r="$(v3a 196608 "$ready")"
ok "panic_on_oops=1 (a crash kernel was loaded): fail" "${r%%:*}:$(grep -c 'kernel.panic_on_oops live=1' <<<"$r")" "1:1"
base; echo 4563402752 >"$T/kexec_crash_size"; printf '  a0000000-afffffff : Crash kernel\n' >>"$T/iomem"
r="$(v3a 196608 "$ready")"
ok "crash memory reserved (4.25 GiB, kdump-tools' value): fail" \
   "${r%%:*}:$(grep -c 'crash-kernel memory is reserved: kexec_crash_size=4563402752 bytes, 1' <<<"$r")" "1:1"
base; echo 268435456 >"$T/kexec_crash_size"
r="$(v3a 196608 "$ready")"
ok "crash memory non-zero in kexec_crash_size alone: fail" "${r%%:*}" "1"
base; printf '  0f000000-0fffffff : Crash kernel\n' >>"$T/iomem"
r="$(v3a 196608 "$ready")"
ok "a Crash kernel region in /proc/iomem alone: fail" "${r%%:*}" "1"
base; rm -f "$T/kexec_crash_size"
r="$(v3a 196608 "$ready")"
ok "kexec_crash_size absent, no Crash kernel region: pass with a note" "${r%%:*}:$(grep -c 'kexec_crash_size unreadable, .*iomem decided' <<<"$r")" "0:1"
base; rm -f "$T/kexec_crash_size"; printf '  0f000000-0fffffff : Crash kernel\n' >>"$T/iomem"
r="$(v3a 196608 "$ready")"
ok "kexec_crash_size absent, Crash kernel region present: fail" "${r%%:*}" "1"
base; rm -f "$T/kexec_crash_size"; mv "$T/iomem" "$T/iomem.off"
r="$(v3a 196608 "$ready")"
ok "neither source readable: fail" "${r%%:*}:$(grep -c 'cannot tell whether crash-kernel memory is reserved' <<<"$r")" "1:1"
mv "$T/iomem.off" "$T/iomem"

echo "v03a_gtt_test: $pass passed, $fail failed"
(( fail == 0 ))
