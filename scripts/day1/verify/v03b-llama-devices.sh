#!/usr/bin/env bash
# verify/v03b-llama-devices.sh — V3 second half (Section 21, Phase 2 gate): `llama-cli --list-devices` (llama-server's
# when llama-cli is absent) lists a Vulkan0 device with at least FLOOR MiB of device memory in total.
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; one stdout line.
#
# The floor (v0.4.0, D-36 option (c) and S-28, approved by the Principal 2026-10-16): the kernel line stays as written
# (amdgpu.gttsize=196608 ttm.pages_limit=50331648, Section 3.3), and the floor rises from a fixed 160000 MiB to the
# largest Day 1 LLM seat (engines.json, arbiter_class core/apex, placement gpu) WITH its context memory: the Arbiter's
# own projection at the profile engines.json renders (weights from files[].bytes, KV and per-slot state at its
# kv_class, the compute-buffer term; atlas/arbiter.py projected_footprint), run in the orchestrator's venv. It is never
# below the v0.3.5 floor of 160000 MiB, so it only rises: the Day 1 seats project to about 152-155 GiB (GLM-5.3-Flash
# 8 x 32k f16 with its mmproj, DeepSeek-V4-Flash-0731 1 x 128k f16), under 160000 MiB, and a phased seat appended to
# engines.json later (Qwen3.8-Flash-Next UD-Q6_K_XL, 157.55 GiB of weights) raises it by itself. Before Phase 2 step 2
# has installed the orchestrator (step 1 records V3b early) the projection is unavailable and the weights alone are
# used, said so in the message; the Phase 2 gate re-runs V3b with the projection.
#
# The figure llama.cpp prints for an integrated GPU is the sum of every Vulkan heap RADV exposes, derived from the kernel
# GTT/VRAM sizes but not the same counter as mem_info_gtt_total (llama-cpp-vulkan.md §3, conflict 3), so the check is a
# floor, "at least 160000 MiB", and the number (total and free) is recorded rather than asserted equal to anything. It
# shows that llama.cpp's Vulkan backend sees the large GTT pool V3a checked; it is not the engine budget, which the
# Arbiter computes from the GTT total, MemTotal, the resident set and its host reserve (Section 4.1, ~164 GiB expected).
# T-01 settles on the node whether the usable heap is the 176-189 GiB the 192 GiB request allows or the vendor's 160 GB
# (149.01 GiB) cap; a heap under the floor shuts the largest seat out, which is what this check is for (B-31).
# Output format (VERIFIED, common/arg.cpp): "  Vulkan0: AMD Radeon Graphics (RADV GFX1151) (NNNNN MiB, MMMMM MiB free)".

# shellcheck source-path=SCRIPTDIR
# shellcheck source=../lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
export ATLAS_LOG_TO_STDERR=1

MIN_MIB_V035=160000
# _v03b_floor — prints "FLOOR_MIB<TAB>WHY" (header). Never fails: an unreadable engines.json leaves the v0.3.5 floor.
_v03b_floor() {
  local py="$ATLAS_OPT/venv/bin/python"
  [[ -x "$py" ]] || py=python3
  "$py" -I -B - "$ATLAS_DAY1_DIR/config" "$MIN_MIB_V035" <<'PY' 2>/dev/null || printf '%s	%s
' "$MIN_MIB_V035" "the v0.3.5 floor (engines.json unreadable)"
import json, math, sys
from pathlib import Path
cfg, floor = Path(sys.argv[1]), int(sys.argv[2])
MIB = 1024 ** 2
seats = [e for e in json.loads((cfg / "engines.json").read_text(encoding="utf-8"))["engines"]
         if e.get("arbiter_class") in ("core", "apex") and e.get("placement", "gpu") == "gpu"]
best, how = (0, ""), "weights only: the orchestrator's projection is not installed yet (Phase 2 step 2)"
try:
    from atlas.arbiter import Arbiter
    from atlas.config import load_engines
    arb = Arbiter(load_engines(cfg), None, None)
    for e in seats:
        fp = arb.projected_footprint(e["key"], int(e["ctx_size"]), int(e.get("parallel", 1)), e.get("kv_class", "none"))
        best = max(best, (int(fp.total_bytes), e["key"]))
    how = "the Arbiter's projection: weights, KV and per-slot state at the rendered profile, compute buffers"
except Exception:  # noqa: BLE001  (step 1 runs before the venv exists: weights only, said so)
    for e in seats:
        files = e.get("files") or []
        size = (sum(int(f["bytes"]) for f in files) if files and all(f.get("bytes") for f in files)
                else int(float(e.get("footprint_gb") or 0) * 1024 ** 3))
        best = max(best, (size, e["key"]))
need = math.ceil(best[0] / MIB)
if need > floor:
    print(f"{need}	{best[1]} needs {need} MiB ({how}; D-36 (c), S-28)")
else:
    print(f"{floor}	the v0.3.5 floor; the largest Day 1 seat, {best[1] or '?'}, needs {need} MiB ({how}; D-36 (c), S-28)")
PY
}
IFS=$'\t' read -r MIN_MIB floor_why <<<"$(_v03b_floor)"
[[ "$MIN_MIB" =~ ^[0-9]+$ ]] || { MIN_MIB=$MIN_MIB_V035; floor_why="the v0.3.5 floor"; }
bin=""
for cand in /usr/local/bin/llama-cli /usr/local/bin/llama-server; do
  [[ -x "$cand" ]] && { bin="$cand"; break; }
done
if [[ -z "$bin" ]]; then
  echo "llama-cli not installed (Phase 2 step 1 has not run)"
  exit 1
fi

out="$(timeout 300 "$bin" --list-devices 2>&1 || true)"
# `|| true` inside the substitution: with no Vulkan0 line grep exits 1, and under pipefail + errexit the assignment would
# abort the script before the diagnostic below could print (fix round, reproduced).
line="$(grep -m1 -E '^[[:space:]]*Vulkan0:' <<<"$out" | sed 's/^[[:space:]]*//' || true)"
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
  echo "$desc: ${total} MiB total, ${free} MiB free (>= ${MIN_MIB} MiB of Vulkan device memory: $floor_why)"
  exit 0
fi
echo "$desc: ${total} MiB total, ${free} MiB free (< ${MIN_MIB} MiB: $floor_why; check ttm.pages_limit=50331648 and the GTT pool, Section 3.3)"
exit 1
