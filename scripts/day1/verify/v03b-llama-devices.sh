#!/usr/bin/env bash
# verify/v03b-llama-devices.sh — V3 second half (Section 21, Phase 2 gate): `llama-cli --list-devices` reports the
# ~170 GB usable budget. Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; one stdout line.
#
# The figure llama.cpp prints for an integrated GPU is the sum of every Vulkan heap RADV exposes, derived from the kernel
# GTT/VRAM sizes but not the same counter as mem_info_gtt_total (llama-cpp-vulkan.md §3, conflict 3), so the check is
# "at least 160000 MiB" (the 170 GB budget with tolerance), and the number is recorded rather than asserted equal.
# Output format (VERIFIED, common/arg.cpp): "  Vulkan0: AMD Radeon Graphics (RADV GFX1151) (NNNNN MiB, MMMMM MiB free)".

# shellcheck source-path=SCRIPTDIR
# shellcheck source=../lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
export ATLAS_LOG_TO_STDERR=1

MIN_MIB=160000
bin=""
for cand in /usr/local/bin/llama-cli /usr/local/bin/llama-server; do
  [[ -x "$cand" ]] && { bin="$cand"; break; }
done
if [[ -z "$bin" ]]; then
  echo "llama-cli not installed (Phase 2 step 1 has not run)"
  exit 1
fi

out="$(timeout 300 "$bin" --list-devices 2>&1 || true)"
line="$(grep -m1 -E '^[[:space:]]*Vulkan0:' <<<"$out" | sed 's/^[[:space:]]*//')"
if [[ -z "$line" ]]; then
  echo "no Vulkan0 device in '$bin --list-devices': $(tr '\n' ' ' <<<"$out" | cut -c1-200)"
  exit 1
fi
# "... (<total> MiB, <free> MiB free)" — take the last parenthesised group so a device name with parentheses is safe.
total="$(sed -nE 's/.*\(([0-9]+) MiB, ([0-9]+) MiB free\)[[:space:]]*$/\1/p' <<<"$line")"
free="$(sed -nE 's/.*\(([0-9]+) MiB, ([0-9]+) MiB free\)[[:space:]]*$/\2/p' <<<"$line")"
if [[ -z "$total" ]]; then
  echo "could not parse the memory figure from: $line"
  exit 1
fi
desc="${line% (*}"   # strip only the trailing "(N MiB, M MiB free)" group
if (( total >= MIN_MIB )); then
  echo "$desc: ${total} MiB total, ${free} MiB free (>= ${MIN_MIB} MiB, ~170 GB budget)"
  exit 0
fi
echo "$desc: ${total} MiB total, ${free} MiB free (< ${MIN_MIB} MiB; check ttm.pages_limit=50331648 and the GTT pool, Section 3.3)"
exit 1
