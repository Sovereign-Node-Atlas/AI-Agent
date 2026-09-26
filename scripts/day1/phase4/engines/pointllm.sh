#!/usr/bin/env bash
# phase4/engines/pointllm.sh — PointLLM 7B v1.2, tier VERIFY: V8 "PointLLM builds and runs on ROCm in the container"
# (Section 21; Section 17 step 4: deferred if it fails). Licence cc-by-nc-4.0 (UNVERIFIED; adjudicated conflict 17,
# recorded in the json licence_note and the README).
# Research: rocm-containers.md §3.11: the point ops are pure torch (VERIFIED), the blocker is dependency rot —
# transformers pinned to a 2023 commit, tokenizers==0.12.1 (no cp312 wheel: built from source with rustc/cargo in a
# derived image), open3d==0.16.0 (no cp312 wheel: unpinned), deepspeed/flash-attn dropped. All UNVERIFIED on 3.12.
# Test: pointllm_test.py (a synthetic 8192-point cloud, one question, float16) -> pointllm_answer.txt.
P4_KEY="pointllm"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  # Derived image: Ubuntu 24.04's rustc/cargo for the tokenizers 0.12.1 source build (UNVERIFIED that 2022 Rust code
  # builds with rustc 1.75; a failure is the recorded reason for V8 deferred).
  p4_derive_image "atlas/rocm-pointllm:1" <<DOCKER
USER root
RUN apt-get update && apt-get install -y --no-install-recommends rustc cargo && rm -rf /var/lib/apt/lists/*
USER $P4_UID:$P4_GID
DOCKER
  p4_git_from_json
  local src="$P4_HOST_SRC/PointLLM" req="$P4_HOST_SRC/PointLLM/requirements-atlas.txt"
  [[ -f "$src/pyproject.toml" ]] || die "$P4_KEY: $src/pyproject.toml not found (upstream layout changed?)"
  # Dependencies from pyproject with the pins the research names as unbuildable on 3.12 relaxed (research plan).
  python3 - "$src/pyproject.toml" "$req" <<'PY' || die "$P4_KEY: could not derive requirements from pyproject.toml"
import re, sys, tomllib
src, dst = sys.argv[1:3]
deps = tomllib.load(open(src, "rb")).get("project", {}).get("dependencies", [])
keep, dropped = [], []
for d in deps:
    name = re.split(r"[ =<>@\[;]", d.strip(), 1)[0].lower()
    if name in {"torch", "torchvision", "torchaudio", "deepspeed", "flash-attn", "flash_attn", "ninja"}:
        dropped.append(d)
    elif name == "open3d":
        keep.append("open3d"); dropped.append(d + " -> open3d (unpinned, no cp312 wheel for 0.16.0)")
    else:
        keep.append(d)
open(dst, "w", encoding="utf-8").write("\n".join(keep) + "\n")
print("kept:", keep, file=sys.stderr); print("dropped:", dropped, file=sys.stderr)
PY
  chown "$P4_UID:$P4_GID" "$req"
  p4_venv_pip -r "$P4_SRC/PointLLM/requirements-atlas.txt"
  p4_venv_pip --no-deps -e "$P4_SRC/PointLLM"
  p4_pull
}

p4_main "$@"
