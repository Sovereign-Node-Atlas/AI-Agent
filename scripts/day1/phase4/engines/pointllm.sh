#!/usr/bin/env bash
# phase4/engines/pointllm.sh — PointLLM 7B v1.2, tier VERIFY: V8 "PointLLM builds and runs on ROCm in the container"
# (Section 21; Section 17 step 4: deferred if it fails). Licence cc-by-nc-4.0 (UNVERIFIED; adjudicated conflict 17,
# recorded in the json licence_note and the README).
# Research: rocm-containers.md §3.11: the point ops are pure torch (VERIFIED), the blocker is dependency rot. The pinned
# pyproject.toml (VERIFIED 2026-10-04 at cb72f4e6) names tokenizers==0.12.1, transformers @ cae78c46 (4.28.0.dev0),
# timm==0.4.12, open3d==0.16.0, deepspeed, wandb, gradio, fastapi, uvicorn, openai.
# Fix round 3 — UNVERIFIED deviation, chosen so that the attempt is MADE (rule §7.4; the earlier revision stopped by
# construction on an absent crate-registry allowlist entry and would have failed anyway: tokenizers 0.12.1's sdist pins
# pyo3 0.15, which refuses Python 3.12 whatever the Rust toolchain, and its wheels stop at cp310, VERIFIED PyPI):
#   * tokenizers>=0.14,<0.15   the oldest tokenizers line with a cp312 wheel (0.14.0, VERIFIED PyPI);
#   * transformers==4.34.1     the oldest release whose dependency table accepts it (tokenizers>=0.14,<0.15, VERIFIED
#                              PyPI); the pinned 4.28.0.dev0 commit requires tokenizers<0.14 at import time (VERIFIED
#                              dependency_versions_table.py), so no cp312 tokenizers exists for it;
#   * open3d                   unpinned (0.16.0 has no cp312 wheel; 0.19/0.20 do, VERIFIED PyPI);
#   * dropped                  torch* (the ROCm torch stays), deepspeed, flash-attn, ninja, wandb, gradio, fastapi,
#                              uvicorn, openai: training/serving only; pointllm/__init__ -> model/pointllm.py,
#                              model/utils.py, pointbert/* import torch, transformers, timm, easydict, yaml, requests
#                              only (VERIFIED import graph at the pin).
#   Whether PointLLM's LLaMA subclass (model/pointllm.py) runs on transformers 4.34.1 is UNVERIFIED: an ImportError or
#   a forward-signature mismatch is the recorded reason for V8 deferred. No Rust toolchain, no derived image and no
#   crate registry are needed any more.
# Test: pointllm_test.py (a synthetic 8192-point cloud, one question, float16) -> pointllm_answer.txt.
P4_KEY="pointllm"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

# The python that derives requirements-atlas.txt from pyproject.toml; run INSIDE the container as atlas (root never
# writes into the atlas-writable checkout: a planted symlink would redirect the write). `read -d ''` returns 1 at EOF.
read -r -d '' P4_REQ_PY <<'PY' || true
import re, sys, tomllib
src, dst = sys.argv[1:3]
deps = tomllib.load(open(src, "rb")).get("project", {}).get("dependencies", [])
keep, dropped = [], []
DROP = {"torch", "torchvision", "torchaudio", "deepspeed", "flash-attn", "flash_attn", "ninja",
        "wandb", "gradio", "fastapi", "uvicorn", "openai"}
for d in deps:
    name = re.split(r"[ =<>@\[;]", d.strip(), 1)[0].lower()
    if name in DROP:
        dropped.append(d)
    elif name == "open3d":
        keep.append("open3d"); dropped.append(d + " -> open3d (unpinned, no cp312 wheel for 0.16.0)")
    elif name == "tokenizers":
        keep.append("tokenizers>=0.14,<0.15"); dropped.append(d + " -> tokenizers>=0.14,<0.15 (oldest cp312 wheel; UNVERIFIED deviation)")
    elif name == "transformers":
        keep.append("transformers==4.34.1"); dropped.append(d + " -> transformers==4.34.1 (oldest release accepting tokenizers 0.14; UNVERIFIED deviation)")
    else:
        keep.append(d)
for must in ("tokenizers>=0.14,<0.15", "transformers==4.34.1"):
    if must not in keep:
        sys.exit(f"pyproject.toml no longer names {must.split('=')[0].split('>')[0]}: the upstream dependency list changed; review the filter")
open(dst, "w", encoding="utf-8").write("\n".join(keep) + "\n")
print("kept:", keep, file=sys.stderr); print("dropped:", dropped, file=sys.stderr)
PY

p4_build() {
  p4_venv_create
  p4_git_from_json
  local src="$P4_HOST_SRC/PointLLM"
  [[ -f "$src/pyproject.toml" && ! -L "$src/pyproject.toml" ]] || die "$P4_KEY: $src/pyproject.toml not found (upstream layout changed?)"
  p4_docker_run -- python3.12 -c "$P4_REQ_PY" "$P4_SRC/PointLLM/pyproject.toml" "$P4_SRC/PointLLM/requirements-atlas.txt" \
    || die "$P4_KEY: could not derive requirements from pyproject.toml"
  p4_note "pyproject pins relaxed: tokenizers>=0.14,<0.15 and transformers==4.34.1 in place of 0.12.1 / cae78c46 (UNVERIFIED deviation, header); open3d unpinned; training/serving deps dropped"
  p4_venv_pip -r "$P4_SRC/PointLLM/requirements-atlas.txt"
  p4_venv_pip --no-deps -e "$P4_SRC/PointLLM"
  # Import smoke check, no GPU: the model module must import on transformers 4.34.1 now (rule §7.4: a named build
  # failure, not a GPU-test failure later). This is where the UNVERIFIED deviation above is decided.
  local marker
  marker="$(p4_marker import-installed)"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: import smoke check already passed"
  else
    p4_in_venv -- python -c 'import transformers, tokenizers; from pointllm.model import PointLLMLlamaForCausalLM; from pointllm.conversation import conv_templates; from pointllm.model.utils import KeywordsStoppingCriteria; print("pointllm import ok on transformers", transformers.__version__, "tokenizers", tokenizers.__version__)' \
      || die "$P4_KEY: 'from pointllm.model import PointLLMLlamaForCausalLM' failed in the venv on transformers 4.34.1 / tokenizers 0.14 (the UNVERIFIED deviation of the header did not hold, or a dependency is missing: the error above names it; see $P4_LOG). V8 is deferred with this reason"
    date -Is >"$marker"
  fi
  p4_pull
}

p4_main "$@"
