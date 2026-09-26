#!/usr/bin/env bash
# verify/v03a-gtt.sh — V3 first half (Section 3.3, 17 step 5, 21): the kernel parameters were accepted on kernel 7.x,
# the GTT pool read from sysfs matches them, and vulkaninfo sees the GPU as RADV GFX1151 (adjudicated conflict 6:
# match on "GFX1151", never on the marketing name; conflict 8: the amdgpu.gttsize deprecation warning is expected).
# Usage: v03a-gtt.sh [EXPECTED_GTT_MIB]   (default 196608 = 192 GiB; tolerance: >= 95 % of it, <= it + 1 MiB)
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

expected="${1:-196608}"
cmdline="$(cat /proc/cmdline)"
fails=()
for p in "ttm.pages_limit=50331648" "amdgpu.gttsize=$expected" "amdgpu.lockup_timeout=10000,60000,10000,10000"; do
  grep -qw -- "$p" <<<"$cmdline" || fails+=("cmdline lacks $p")
done
pages="$(cat /sys/module/ttm/parameters/pages_limit 2>/dev/null || echo missing)"
[[ "$pages" == "50331648" ]] || fails+=("ttm.pages_limit live=$pages")
gttparam="$(cat /sys/module/amdgpu/parameters/gttsize 2>/dev/null || echo missing)"
[[ "$gttparam" == "$expected" ]] || fails+=("amdgpu.gttsize live=$gttparam")
lockup="$(cat /sys/module/amdgpu/parameters/lockup_timeout 2>/dev/null || echo missing)"
[[ "$lockup" == "10000,60000,10000,10000" ]] || fails+=("amdgpu.lockup_timeout live=$lockup")

total=""
if total="$(gpu_gtt_total_mb 2>/dev/null)"; then
  low=$(( expected * 95 / 100 ))
  if (( total < low || total > expected + 1 )); then
    fails+=("mem_info_gtt_total=${total} MiB outside [$low, $expected]")
  fi
else
  fails+=("no AMD GPU under /sys/class/drm or mem_info_gtt_total unreadable")
fi
ram_mib=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
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

summary="gtt_total=${total:-?} MiB (expected $expected, RAM $ram_mib MiB); ttm.pages_limit=$pages; lockup_timeout=$lockup; dmesg='${ready:-no GTT ready line}' deprecation_warn=$deprec; vulkan='$vk'"
if (( ${#fails[@]} > 0 )); then
  echo "V3a fail: ${fails[*]}; $summary"
  exit 1
fi
echo "$summary"
exit 0
