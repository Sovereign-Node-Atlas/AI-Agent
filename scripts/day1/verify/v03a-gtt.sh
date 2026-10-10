#!/usr/bin/env bash
# verify/v03a-gtt.sh — V3 first half (Section 3.3, 17 step 5, 21): the three Section 3.3 / Appendix B kernel
# parameters were accepted on kernel 7.x (amdgpu.gttsize=196608 ttm.pages_limit=50331648
# amdgpu.lockup_timeout=10000,60000,10000,10000), the live module parameters carry them, the GTT pool read from sysfs
# matches, and vulkaninfo sees the GPU as RADV GFX1151 (adjudicated conflict 6: match on "GFX1151", never on the
# marketing name; conflict 8: the amdgpu.gttsize deprecation warning is expected). All three parameters are gated:
# Appendix B and Section 3.3 list them together, and S8 added lockup_timeout precisely so that V22 cannot fail "for a
# kernel reason", so a cmdline without it (hand edit, distro GRUB rewrite) must turn V3a red, not pass.
# GTT pool expectation (v0.3.5, S47, corrects fix round 3): whether amdgpu caps the pool at physical RAM depends on the
# kernel build. Mainline v7.0 and Ubuntu 7.0.0-38 do NOT cap it: mem_info_gtt_total is the requested 196608 MiB
# whatever MemTotal is (amdgpu_ttm_init, VERIFIED in the 7.0.0-38.38 source). Ubuntu 7.0.0-39 (upstream 5e70f6804b4d,
# "drm/amdgpu: cap GTT size to physical RAM on APUs"; in mainline from v7.2) caps it at MemTotal and logs "Capping GTT
# to <N>M". Either is a correctly applied parameter, so the pool passes when it equals EXPECTED or MemTotal (1 % below,
# 1 MiB above, as before); anything else (the ~50 % TTM default, a typo) fails. On a 192 GiB machine MemTotal sits
# below 196608 MiB by the firmware reservation plus the BIOS UMA frame-buffer carveout; when it is far below, the
# message points at the carveout (Section 3.2 wants it at the minimum so the GTT pool, not VRAM, holds the models).
# The three parameters themselves are checked separately against the literal values.
# Crash kernel and panic (option (c) of 2026-10-10, S48; step 4 writes /etc/default/grub.d/zzz-atlas-crash.cfg): V3a
# also fails when /proc/cmdline carries crashkernel= anywhere (the kernel finds it with strstr, not by word:
# get_last_crashkernel, v7.0 kernel/crash_reserve.c), when any crash-kernel memory is reserved
# (/sys/kernel/kexec_crash_size, the high and low regions together, 0 without a reservation, 4563402752 = 4.25 GiB with
# kdump-tools' value on this machine; and no "Crash kernel" region in /proc/iomem), when the cmdline does not carry
# exactly one panic= word, panic=10, when the live kernel.panic is not 10 (a sysctl override, or a value the kernel
# rejected), or when kernel.panic_on_oops is not 0 (kdump-config sets it to 1 after loading a crash kernel). A surviving
# reservation is a failure, not a note: it comes out of MemTotal, and at the larger GPU resident set (about 7-8 GiB) the
# Arbiter's 16 GiB host reserve would then refuse the Apex engine's f16 and q8_0 KV-ladder rungs (Section 4.1, S2).
# /sys/kernel/kexec_crash_size unreadable (missing in a kernel without CONFIG_CRASH_DUMP; -EBUSY while the kexec lock
# is held) is read as no reservation only when /proc/iomem shows no "Crash kernel" region either. panic= words are
# counted on /proc/cmdline split at whitespace, so a quoted value that contains " panic=" would be reported too: a false
# alarm that says what it saw, never a silent pass.
# Kexec handover (S49; the same drop-in appends kho=off): Ubuntu's 7.0 kernel has it on by default
# (CONFIG_KEXEC_HANDOVER_ENABLE_DEFAULT=y, VERIFIED). At boot it then sets aside scratch memory, about 13 GiB here
# (INFERRED), and frees it to the page allocator as MIGRATE_CMA pageblocks: they stay inside MemTotal, but TTM/GTT pages
# can never use them, so the Arbiter's pool, the smaller of the GTT total and MemTotal, would overstate what engines can
# use. V3a fails unless the cmdline carries exactly one kho= word and it is the literal kho=off (counted like panic=;
# kho_scratch= is another parameter and is not counted), and unless no CMA is set aside at all. The scratch is read from
# the zone 'cma' lines of /proc/zoneinfo (pages of 4 KiB; the scratch adds to them, VERIFIED mm/mm_init.c and
# mm/vmstat.c v7.0), less CmaTotal, which counts named CMA areas only and never the scratch; CmaFree is only the free
# part of all CMA, but a non-zero CmaFree with CmaTotal 0 is scratch too (on a boot that is itself a kexec handover the
# zone counters miss it). A named CMA area (cma=, hugetlb_cma=, numa_cma=, cma_pernuma=, crashkernel=...,cma) fails as
# well: GTT pages cannot use it either, and on this node's kernel config none exists unless the cmdline asks for one,
# so all three counters are 0 once kho=off holds. No 'cma' line at all means /proc/zoneinfo is unreadable or its format
# changed: "cannot tell", a failure. The awk sums stay in pages; bytes are computed in bash.
# Usage: v03a-gtt.sh [EXPECTED_GTT_MIB]   (default 196608 = 192 GiB)
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

expected="${1:-196608}"
lockup_want="10000,60000,10000,10000"
cmdline="$(cat /proc/cmdline)"
fails=()
for p in "ttm.pages_limit=50331648" "amdgpu.gttsize=$expected" "amdgpu.lockup_timeout=$lockup_want"; do
  grep -qw -- "$p" <<<"$cmdline" || fails+=("cmdline lacks $p")
done
pages="$(cat /sys/module/ttm/parameters/pages_limit 2>/dev/null || echo missing)"
[[ "$pages" == "50331648" ]] || fails+=("ttm.pages_limit live=$pages")
gttparam="$(cat /sys/module/amdgpu/parameters/gttsize 2>/dev/null || echo missing)"
[[ "$gttparam" == "$expected" ]] || fails+=("amdgpu.gttsize live=$gttparam")
# module_param_string: sysfs returns the string as typed on the cmdline (whitespace trimmed here to be safe).
lockup="$(tr -d '[:space:]' </sys/module/amdgpu/parameters/lockup_timeout 2>/dev/null || echo missing)"
[[ "$lockup" == "$lockup_want" ]] || fails+=("amdgpu.lockup_timeout live=$lockup (want $lockup_want)")

# S48: no crash kernel, panic=10 (header). Where to look is named in each message: a /etc/default/grub.d drop-in that
# sorts after zzz-atlas-crash.cfg, a hand edit of /etc/default/grub or /etc/grub.d, a sysctl.d file.
grubd="look for: /etc/default/grub.d/zzz-atlas-crash.cfg missing or changed (phase1 --force 04 rewrites it), a *.cfg there sorting after it, a hand edit of /etc/default/grub or /etc/grub.d; then update-grub and reboot"
if [[ "$cmdline" == *crashkernel=* ]]; then
  fails+=("cmdline carries crashkernel= ($grubd)")
fi
read -r -a words <<<"$cmdline"
panics=()
for w in "${words[@]}"; do
  if [[ "$w" =~ ^\"?panic= ]]; then panics+=("$w"); fi
done
if (( ${#panics[@]} == 0 )); then
  fails+=("cmdline lacks panic=10 ($grubd)")
elif (( ${#panics[@]} > 1 )) || [[ "${panics[0]}" != "panic=10" ]]; then
  fails+=("cmdline carries '${panics[*]}', want exactly one panic=10 ($grubd)")
fi
panic_live="$(cat /proc/sys/kernel/panic 2>/dev/null || echo missing)"
[[ "$panic_live" == 10 ]] || fails+=("kernel.panic live=$panic_live (want 10: a sysctl override, grep -rsE 'kernel[./]panic[[:space:]]*=' /etc/sysctl.conf /etc/sysctl.d /run/sysctl.d /usr/lib/sysctl.d, or a panic= value the kernel rejected, journalctl -k -b | grep -i panic)")
oops_live="$(cat /proc/sys/kernel/panic_on_oops 2>/dev/null || echo missing)"
[[ "$oops_live" == 0 ]] || fails+=("kernel.panic_on_oops live=$oops_live (want 0: kdump-config sets 1 after loading a crash kernel, systemctl is-enabled kdump-tools.service; or a sysctl.d override)")
crash_size="$(cat /sys/kernel/kexec_crash_size 2>/dev/null || echo unreadable)"
crash_iomem="$({ grep -c 'Crash kernel' /proc/iomem 2>/dev/null || true; } | head -n1)"
[[ "$crash_iomem" =~ ^[0-9]+$ ]] || crash_iomem="unreadable"
crash_note=""
if [[ "$crash_size" =~ ^[0-9]+$ && "$crash_size" != 0 ]] || [[ "$crash_iomem" =~ ^[0-9]+$ && "$crash_iomem" != 0 ]]; then
  fails+=("crash-kernel memory is reserved: kexec_crash_size=$crash_size bytes, $crash_iomem 'Crash kernel' region(s) in /proc/iomem (crashkernel= reached the kernel: journalctl -k -b | grep -i crashkernel; it comes out of MemTotal, so at the larger GPU resident set, about 7-8 GiB, the 16 GiB Arbiter reserve refuses the Apex f16 and q8_0 rungs)")
elif [[ "$crash_size" == unreadable && "$crash_iomem" == unreadable ]]; then
  fails+=("cannot tell whether crash-kernel memory is reserved: /sys/kernel/kexec_crash_size and /proc/iomem are both unreadable")
elif [[ "$crash_size" == unreadable ]]; then
  crash_note=" (kexec_crash_size unreadable, /proc/iomem decided)"
fi

# S49: kexec handover off (header). Exactly one kho= word, the literal kho=off (the kernel would also take 0, n, no ...).
khos=()
for w in "${words[@]}"; do
  if [[ "$w" =~ ^\"?kho= ]]; then khos+=("$w"); fi
done
if (( ${#khos[@]} == 0 )); then
  fails+=("cmdline lacks kho=off ($grubd)")
elif (( ${#khos[@]} > 1 )) || [[ "${khos[0]}" != "kho=off" ]]; then
  fails+=("cmdline carries '${khos[*]}', want exactly one kho=off ($grubd)")
fi
zone_cma="$(awk '$1 == "cma" && $2 ~ /^[0-9]+$/ {s += $2; n++} END {if (n) print s}' /proc/zoneinfo 2>/dev/null || true)"
[[ "$zone_cma" =~ ^[0-9]+$ ]] || zone_cma="unreadable"
cma_total_kb="$(awk '$1 == "CmaTotal:" {print $2}' /proc/meminfo 2>/dev/null || true)"
[[ "$cma_total_kb" =~ ^[0-9]+$ ]] || cma_total_kb=0   # no line: a kernel without CONFIG_CMA, so no CMA at all
cma_free_kb="$(awk '$1 == "CmaFree:" {print $2}' /proc/meminfo 2>/dev/null || true)"
[[ "$cma_free_kb" =~ ^[0-9]+$ ]] || cma_free_kb=0
kho_mib="?"
if [[ "$zone_cma" == unreadable ]]; then
  fails+=("cannot tell whether kexec handover scratch is set aside: /proc/zoneinfo is unreadable or has no 'cma' lines")
else
  kho_bytes=$(( zone_cma * 4096 - cma_total_kb * 1024 ))
  (( kho_bytes > 0 )) || kho_bytes=0
  kho_mib=$(( kho_bytes / 1048576 ))
  if (( kho_bytes > 0 )) || (( cma_total_kb == 0 && cma_free_kb > 0 )); then
    fails+=("kexec handover scratch is still set aside: ${kho_mib} MiB of CMA pageblocks (zone 'cma' pages in /proc/zoneinfo less CmaTotal; CmaTotal ${cma_total_kb} kB, CmaFree ${cma_free_kb} kB). GTT/TTM pages can never use CMA, so the Arbiter's pool (the smaller of the GTT total and MemTotal) overstates usable memory by that much and the Apex f16 and q8_0 rungs can fail with a Vulkan out-of-memory in Phase 3 (S49). kho=off did not take effect: grep -o '[^ ]*kho[^ ]*' /proc/cmdline; journalctl -k -b | grep -E 'KHO:|Malformed early option'; ls /sys/kernel/debug/kho ($grubd)")
  fi
  if (( cma_total_kb > 0 )); then
    fails+=("a named CMA area holds ${cma_total_kb} kB (CmaTotal; ls /sys/kernel/mm/cma), set by cma=, hugetlb_cma=, numa_cma=, cma_pernuma= or crashkernel=...,cma on /proc/cmdline; GTT pages cannot use it either (S49; $grubd)")
  fi
fi

ram_mib=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
capped="$(dmesg 2>/dev/null | grep -o 'Capping GTT to [0-9]*M' | tail -n1 || true)"
total=""
hint=""
pool=""
_v3a_near() { (( $1 >= $2 - $2 / 100 && $1 <= $2 + 1 )); }   # 1 % below, 1 MiB above
if total="$(gpu_gtt_total_mb 2>/dev/null)"; then
  if _v3a_near "$total" "$expected"; then
    pool="uncapped (the requested size; kernels without the APU cap, e.g. 7.0.0-38)"
  elif _v3a_near "$total" "$ram_mib"; then
    pool="capped at MemTotal (${capped:-no 'Capping GTT' line in dmesg}; 7.0.0-39 and later)"
  else
    fails+=("mem_info_gtt_total=${total} MiB is neither the requested $expected MiB nor MemTotal $ram_mib MiB (1 % below, 1 MiB above)")
  fi
else
  fails+=("no AMD GPU under /sys/class/drm or mem_info_gtt_total unreadable")
fi
if (( ram_mib < expected * 95 / 100 )); then
  carve_gib=$(( (expected - ram_mib + 512) / 1024 ))
  hint="; HINT: MemTotal is $ram_mib MiB, ~$carve_gib GiB below the $expected MiB target: the BIOS UMA frame-buffer carveout (and firmware reservation) takes it; Section 3.2 wants the carveout at the minimum so the GTT pool, not VRAM, holds the models (BIOS setting, not /proc/cmdline)"
fi
ready="$(dmesg 2>/dev/null | grep -o '[0-9]*M of GTT memory ready' | tail -n1 || true)"
deprec="no"
dmesg 2>/dev/null | grep -q 'gttsize via module parameter is deprecated' && deprec="yes(expected)"

vk="unavailable"
if command -v vulkaninfo >/dev/null; then
  vk="$(vulkaninfo --summary 2>/dev/null | grep -m1 -i 'deviceName' | sed -E 's/^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*//' || true)"
  [[ -n "$vk" ]] || vk="vulkaninfo printed no deviceName"
else
  vk="vulkaninfo not installed"
fi
grep -qi 'GFX1151' <<<"$vk" || fails+=("vulkaninfo does not report RADV GFX1151: $vk")

summary="gtt_total=${total:-?} MiB, ${pool:-not matched} (requested $expected, MemTotal $ram_mib MiB); ttm.pages_limit=$pages; amdgpu.gttsize=$gttparam; lockup_timeout=$lockup; crash kernel: kexec_crash_size=$crash_size, iomem regions=$crash_iomem$crash_note; panic: cmdline '${panics[*]:-none}', kernel.panic=$panic_live, panic_on_oops=$oops_live; kho: cmdline '${khos[*]:-none}', CMA ${zone_cma} pages (KHO scratch ${kho_mib} MiB), CmaTotal ${cma_total_kb} kB, CmaFree ${cma_free_kb} kB; dmesg='${ready:-no GTT ready line}' deprecation_warn=$deprec; vulkan='$vk'$hint"
if (( ${#fails[@]} > 0 )); then
  echo "V3a fail: ${fails[*]}; $summary"
  exit 1
fi
echo "$summary"
exit 0
