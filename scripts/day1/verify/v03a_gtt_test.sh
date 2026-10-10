#!/usr/bin/env bash
# verify/v03a_gtt_test.sh — regression test (v0.3.5, doc S47) for verify/v03a-gtt.sh's GTT pool check. Kernel 7.0.0-38
# reports the requested 196608 MiB whatever MemTotal is; 7.0.0-39 and later cap the pool at MemTotal and log "Capping
# GTT to <N>M". Both must pass; the ~50 % TTM default and anything in between must fail. Since S48 (option (c)) also the
# crash-kernel and panic checks: crashkernel= anywhere on the cmdline, panic=10 missing or not alone, kernel.panic not
# 10, panic_on_oops not 0, and crash-kernel memory reserved (kexec_crash_size, /proc/iomem) each fail. Since S49 also
# kexec handover: kho=off missing, another kho= value or a second kho= word, kexec handover scratch in the zone 'cma'
# pages of /proc/zoneinfo (or CmaFree with CmaTotal 0), a named CMA area (CmaTotal) and an unreadable zoneinfo each fail.
# Runs a copy of the script with /proc, /sys/module, /sys/kernel/kexec_crash_size and the GTT reader redirected to a
# fresh temp dir; touches nothing else.
# Run: bash verify/v03a_gtt_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/mod/ttm/parameters" "$T/mod/amdgpu/parameters" "$T/procsys"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

BASE_CMDLINE="BOOT_IMAGE=/vmlinuz root=x amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000 panic=10 kho=off"
echo "$BASE_CMDLINE" >"$T/cmdline"
# /proc/zoneinfo in the kernel's own layout (mm/vmstat.c v7.0, zoneinfo_show_print): one stanza per zone, populated or
# not, each with a '        cma      N' line after 'managed'. The nr_free_cma lines (per-node stats, zone stats) must never
# be counted, so they are non-zero here even though the cma lines are 0.
zoneinfo() {  # zoneinfo DMA DMA32 NORMAL MOVABLE — the four zones' cma page counts
  printf 'Node 0, zone      DMA\n  per-node stats\n      nr_inactive_anon 1234\n      nr_free_cma  99\n  pages free     3840\n        boost    0\n        min      4\n        managed  3840\n        cma      %s\n        protection: (0, 1735, 191210, 191210, 191210)\n      nr_free_pages 3840\n      nr_free_cma  7\n' "$1"
  printf 'Node 0, zone    DMA32\n  pages free     400000\n        managed  450000\n        cma      %s\n        protection: (0, 0, 189474, 189474, 189474)\n      nr_free_cma  7\n' "$2"
  printf 'Node 0, zone   Normal\n  pages free     45000000\n        managed  48000000\n        cma      %s\n      nr_free_cma  7\n' "$3"
  printf 'Node 0, zone  Movable\n  pages free     0\n        managed  0\n        cma      %s\n' "$4"
}
meminfo() {  # meminfo CMATOTAL_KB CMAFREE_KB
  printf 'MemTotal:       195035136 kB\nMemAvailable:   180000000 kB\nCmaTotal:       %8s kB\nCmaFree:        %8s kB\n' "$1" "$2"
}
zoneinfo 0 0 0 0 >"$T/zoneinfo"
meminfo 0 0 >"$T/meminfo"   # MemTotal 190464 MiB: 192 GiB less carve-out and reservations
echo 10 >"$T/procsys/panic"
echo 0 >"$T/procsys/panic_on_oops"
echo 0 >"$T/kexec_crash_size"
printf '00000000-00000fff : Reserved\n00001000-0009ffff : System RAM\n100000000-2f7ffffff : System RAM\n' >"$T/iomem"
echo 50331648 >"$T/mod/ttm/parameters/pages_limit"
echo 196608 >"$T/mod/amdgpu/parameters/gttsize"
echo 10000,60000,10000,10000 >"$T/mod/amdgpu/parameters/lockup_timeout"
printf '#!/bin/sh\ncat "%s/dmesg" 2>/dev/null\nexit 0\n' "$T" >"$T/bin/dmesg"
printf '#!/bin/sh\necho "  deviceName = AMD Radeon Graphics (RADV GFX1151)"\n' >"$T/bin/vulkaninfo"
chmod +x "$T/bin/"*
sed -e "s#/proc/cmdline#$T/cmdline#" -e "s#/sys/module/#$T/mod/#g" -e "s#/proc/meminfo#$T/meminfo#g" -e "s#/proc/zoneinfo#$T/zoneinfo#g" \
    -e "s#/proc/sys/kernel/#$T/procsys/#g" -e "s#/sys/kernel/kexec_crash_size#$T/kexec_crash_size#g" -e "s#/proc/iomem#$T/iomem#g" \
    -e "s#^source \"\$(dirname \"\$(readlink -f \"\$0\")\")/../lib/common.sh\"#source \"$REPO/lib/common.sh\"; gpu_gtt_total_mb() { cat \"$T/gtt\"; }#" \
    "$REPO/verify/v03a-gtt.sh" >"$T/v3a.sh"
grep -q 'gpu_gtt_total_mb() { cat' "$T/v3a.sh" || { echo "FAIL could not redirect the GTT reader (source line changed?)"; exit 1; }
for f in procsys/panic procsys/panic_on_oops kexec_crash_size iomem zoneinfo meminfo; do
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
         echo 0 >"$T/kexec_crash_size"; sed -i '/Crash kernel/d' "$T/iomem"; zoneinfo 0 0 0 0 >"$T/zoneinfo"
         meminfo 0 0 >"$T/meminfo"; }
base; r="$(v3a 196608 "$ready")"
ok "S48 base: pass" "${r%%:*}" "0"
ok "S48 base: summary names the crash and panic state" \
   "$(grep -c "crash kernel: kexec_crash_size=0, iomem regions=0; panic: cmdline 'panic=10', kernel.panic=10, panic_on_oops=0" <<<"$r")" "1"
ok "S49 base: summary names the kexec handover state" \
   "$(grep -c "kho: cmdline 'kho=off', CMA 0 pages (KHO scratch 0 MiB), CmaTotal 0 kB, CmaFree 0 kB" <<<"$r")" "1"
base; echo "$BASE_CMDLINE crashkernel=2G-4G:320M,4G-32G:512M,32G-64G:1024M,64G-128G:2048M,128G-:4096M" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "crashkernel= on the cmdline: fail, names it" "${r%%:*}:$(grep -c 'cmdline carries crashkernel=' <<<"$r")" "1:1"
base; echo "$BASE_CMDLINE foo=\"crashkernel=512M\"" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "crashkernel= inside another word (the kernel's strstr finds it): fail" "${r%%:*}:$(grep -c 'cmdline carries crashkernel=' <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/ panic=10/}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "panic=10 missing: fail" "${r%%:*}:$(grep -c 'cmdline lacks panic=10' <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/ panic=10/ panic=0 panic=10}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "a second panic= word: fail, names both" "${r%%:*}:$(grep -c "cmdline carries 'panic=0 panic=10', want exactly one panic=10" <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/ panic=10/ panic=5}" >"$T/cmdline"; echo 5 >"$T/procsys/panic"
r="$(v3a 196608 "$ready")"
ok "panic=5 instead of 10: fail" "${r%%:*}:$(grep -c "cmdline carries 'panic=5'" <<<"$r"):$(grep -c 'kernel.panic live=5' <<<"$r")" "1:1:1"
base; echo "${BASE_CMDLINE/ panic=10/ \"panic=10\" panic=10}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "a quoted second panic= word counts too: fail" "${r%%:*}" "1"
# The count rule alone (F18 of the v0.3.5 review): two identical panic=10 words, kernel.panic still 10.
base; echo "$BASE_CMDLINE panic=10" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "two panic=10 words: fail for the count rule" \
   "${r%%:*}:$(grep -c "cmdline carries 'panic=10 panic=10', want exactly one panic=10" <<<"$r"):$(grep -c 'kernel.panic live=' <<<"$r")" "1:1:0"
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

# S49: kexec handover off. Each case starts from the passing node (kho=off, no CMA) and changes one thing.
base; echo "${BASE_CMDLINE/ kho=off/}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "kho=off missing: fail" "${r%%:*}:$(grep -c 'cmdline lacks kho=off' <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/kho=off/kho=on}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "kho=on: fail, names it" "${r%%:*}:$(grep -c "cmdline carries 'kho=on', want exactly one kho=off" <<<"$r")" "1:1"
base; echo "$BASE_CMDLINE kho=on" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "kho=off then kho=on: fail, names both" "${r%%:*}:$(grep -c "cmdline carries 'kho=off kho=on', want exactly one kho=off" <<<"$r")" "1:1"
base; echo "$BASE_CMDLINE kho=off" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "two kho=off words: fail for the count rule" "${r%%:*}:$(grep -c "cmdline carries 'kho=off kho=off', want exactly one kho=off" <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/kho=off/\"kho=on\"}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "a quoted \"kho=on\": fail" "${r%%:*}:$(grep -c "cmdline carries '\"kho=on\"'" <<<"$r")" "1:1"
base; echo "${BASE_CMDLINE/kho=off/kho=0}" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "kho=0 (off to the kernel, not the literal kho=off): fail" "${r%%:*}:$(grep -c "cmdline carries 'kho=0'" <<<"$r")" "1:1"
base; echo "$BASE_CMDLINE kho_scratch=10% kho" >"$T/cmdline"
r="$(v3a 196608 "$ready")"
ok "kho_scratch= and a bare kho beside kho=off are not kho= words: pass" "${r%%:*}" "0"
# The scratch still set aside although the cmdline says kho=off (3407872 pages = 13312 MiB, the estimate's order).
base; zoneinfo 0 131072 3276800 0 >"$T/zoneinfo"; meminfo 0 12520000 >"$T/meminfo"
r="$(v3a 196608 "$ready")"
ok "KHO scratch in the zone cma pages, CmaTotal 0: fail with the size" \
   "${r%%:*}:$(grep -c 'kexec handover scratch is still set aside: 13312 MiB of CMA pageblocks' <<<"$r"):$(grep -c 'a named CMA area' <<<"$r")" "1:1:0"
ok "KHO scratch: points at the drop-in and update-grub" "$(grep -c "Malformed early option.*zzz-atlas-crash.cfg .*update-grub" <<<"$r")" "1"
ok "KHO scratch: summary carries the figure" "$(grep -c 'CMA 3407872 pages (KHO scratch 13312 MiB), CmaTotal 0 kB, CmaFree 12520000 kB' <<<"$r")" "1"
# A named CMA area only (cma=512M): the zone pages equal CmaTotal, so no scratch, but a failure of its own.
base; zoneinfo 0 131072 0 0 >"$T/zoneinfo"; meminfo 524288 500000 >"$T/meminfo"
r="$(v3a 196608 "$ready")"
ok "named CMA area (CmaTotal 524288 kB): fail, not as KHO scratch" \
   "${r%%:*}:$(grep -c 'a named CMA area holds 524288 kB' <<<"$r"):$(grep -c 'kexec handover scratch is still set aside' <<<"$r")" "1:1:0"
# Both: the scratch beyond the named area is reported, and the named area too.
base; zoneinfo 0 131072 3407872 0 >"$T/zoneinfo"; meminfo 524288 13000000 >"$T/meminfo"
r="$(v3a 196608 "$ready")"
ok "named CMA area and KHO scratch: both named" \
   "${r%%:*}:$(grep -c 'still set aside: 13312 MiB' <<<"$r"):$(grep -c 'a named CMA area holds 524288 kB' <<<"$r")" "1:1:1"
# CmaFree with CmaTotal 0 and no zone cma pages (a boot that is itself a kexec handover): scratch all the same.
base; meminfo 0 4096 >"$T/meminfo"
r="$(v3a 196608 "$ready")"
ok "CmaFree > 0 with CmaTotal 0 and zone cma 0: fail" "${r%%:*}:$(grep -c 'kexec handover scratch is still set aside: 0 MiB .*CmaFree 4096 kB' <<<"$r")" "1:1"
# No CMA lines in /proc/meminfo at all (a kernel without CONFIG_CMA) is no CMA: pass.
base; printf 'MemTotal:       195035136 kB\n' >"$T/meminfo"
r="$(v3a 196608 "$ready")"
ok "no CmaTotal/CmaFree lines (no CONFIG_CMA): pass" "${r%%:*}" "0"
# zoneinfo without any cma line, or absent: cannot tell, a failure.
base; zoneinfo 0 0 0 0 | grep -v '^        cma ' >"$T/zoneinfo"
r="$(v3a 196608 "$ready")"
ok "zoneinfo without cma lines: fail, cannot tell" "${r%%:*}:$(grep -c 'cannot tell whether kexec handover scratch is set aside' <<<"$r")" "1:1"
base; rm -f "$T/zoneinfo"
r="$(v3a 196608 "$ready")"
ok "zoneinfo unreadable: fail, cannot tell" "${r%%:*}:$(grep -c 'cannot tell whether kexec handover scratch is set aside' <<<"$r")" "1:1"
base

echo "v03a_gtt_test: $pass passed, $fail failed"
(( fail == 0 ))
