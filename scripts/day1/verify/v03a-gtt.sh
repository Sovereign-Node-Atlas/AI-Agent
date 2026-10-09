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

summary="gtt_total=${total:-?} MiB, ${pool:-not matched} (requested $expected, MemTotal $ram_mib MiB); ttm.pages_limit=$pages; amdgpu.gttsize=$gttparam; lockup_timeout=$lockup; dmesg='${ready:-no GTT ready line}' deprecation_warn=$deprec; vulkan='$vk'$hint"
if (( ${#fails[@]} > 0 )); then
  echo "V3a fail: ${fails[*]}; $summary"
  exit 1
fi
echo "$summary"
exit 0
