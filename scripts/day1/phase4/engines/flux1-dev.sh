#!/usr/bin/env bash
# phase4/engines/flux1-dev.sh — FLUX.1-dev, green (Section 15.2; Section 17 Phase 4 step 2, first in order of value).
# Gated repo: HF_TOKEN from /etc/atlas/secrets/hf-token.env; a 401/403 stops this engine with the licence URL to accept
# (adjudicated conflict 16). The Principal accepted the dev non-commercial licence (Section 15.5).
# Research: rocm-containers.md §3.1 (diffusers snippet VERIFIED from flux.md; pip list unpinned; the pull skips the
# duplicate single-file weights via allow_patterns). Test: flux1-dev_test.py (20 steps, guidance 3.5, bf16, no offload).
P4_KEY="flux1-dev"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
