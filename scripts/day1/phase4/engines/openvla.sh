#!/usr/bin/env bash
# phase4/engines/openvla.sh — OpenVLA-7b, green technically, dormant until robot hardware exists (Section 15.2).
# Research: rocm-containers.md §3.10 (pins VERIFIED from openvla pyproject.toml: transformers==4.40.1
# tokenizers==0.19.1 timm==0.9.10; torch==2.2.0 deliberately not applied — the constraints file keeps the ROCm torch;
# trust_remote_code=True per the README; attn_implementation=sdpa instead of flash-attn, README: works without it).
# Test: openvla_test.py (a synthetic camera view, predict_action with unnorm_key bridge_orig -> openvla_action.json).
P4_KEY="openvla"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
