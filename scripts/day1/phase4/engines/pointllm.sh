#!/usr/bin/env bash
# phase4/engines/pointllm.sh — PointLLM 7B v1.2, tier VERIFY: V8 "PointLLM builds and runs on ROCm in the container"
# (Section 21; Section 17 step 4: deferred if it fails). Licence cc-by-nc-4.0 (UNVERIFIED; adjudicated conflict 17,
# recorded in the json licence_note and the README).
# Research: rocm-containers.md §3.11: the point ops are pure torch (VERIFIED), the blocker is dependency rot —
# transformers pinned to a 2023 commit, tokenizers==0.12.1 (wheels for cp36-cp310 only, VERIFIED PyPI: built from the
# Rust sdist with rustc/cargo in a derived image), open3d==0.16.0 (no cp312 wheel: unpinned), deepspeed/flash-attn
# dropped. All UNVERIFIED on 3.12. cargo fetches crates from index.crates.io / static.crates.io (crates.io for the
# registry redirect): these hosts must be in config/allowlist.txt (owned by the Phase 1 writer; VERIFIED absent on
# 2026-10-04, so V8 is deferred by construction until they are added) and the proxy reloaded with
# `phase1-platform.sh --reload-allowlist` (NOT `--force 04`, which resets ufw, repeats the dist-upgrade and reboots),
# or the build stops here with that message instead of a TCP_DENIED deep in the pip log.
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

p4_build() {
  # The allowlist check reads the /opt copy the driver runs from (atlas-day1.sh refreshes it from the repository on
  # every run), so an edit in the repository counts once the node has been re-run from it.
  local al="$P4_DAY1/config/allowlist.txt" h
  for h in index.crates.io static.crates.io crates.io; do
    grep -qxF "$h" "$al" 2>/dev/null \
      || die "$P4_KEY: $h is not in config/allowlist.txt: cargo (tokenizers==0.12.1 has no cp312 wheel, the Rust sdist is built) would be denied by squid. Add index.crates.io, static.crates.io and crates.io to scripts/day1/config/allowlist.txt in the repository, then: sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist /path/to/repo/scripts/day1/config/allowlist.txt (no ufw reset, no reboot; --force 04 is NOT the way), then delete $P4_RESULT and re-run: sudo ./atlas-day1.sh phase4 --force 04 (V8 stays deferred until then)"
  done
  # Derived image: Ubuntu 24.04's rustc/cargo for the tokenizers 0.12.1 source build (UNVERIFIED that 2022 Rust code
  # builds with rustc 1.75; a failure is the recorded reason for V8 deferred).
  p4_derive_image "atlas/rocm-pointllm:1" <<DOCKER
USER root
RUN apt-get update && apt-get install -y --no-install-recommends rustc cargo && rm -rf /var/lib/apt/lists/*
USER $P4_UID:$P4_GID
DOCKER
  p4_git_from_json
  local src="$P4_HOST_SRC/PointLLM"
  [[ -f "$src/pyproject.toml" && ! -L "$src/pyproject.toml" ]] || die "$P4_KEY: $src/pyproject.toml not found (upstream layout changed?)"
  # Dependencies from pyproject with the pins the research names as unbuildable on 3.12 relaxed (research plan).
  p4_docker_run -- python3.12 -c "$P4_REQ_PY" "$P4_SRC/PointLLM/pyproject.toml" "$P4_SRC/PointLLM/requirements-atlas.txt" \
    || die "$P4_KEY: could not derive requirements from pyproject.toml"
  p4_venv_pip -r "$P4_SRC/PointLLM/requirements-atlas.txt"
  p4_venv_pip --no-deps -e "$P4_SRC/PointLLM"
  p4_pull
}

p4_main "$@"
