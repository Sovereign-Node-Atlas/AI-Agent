#!/usr/bin/env bash
# phase4/engines/wan2.2.sh — Wan2.2 TI2V-5B (diffusers), green (Section 15.2; Section 17 step 2).
# Research: rocm-containers.md §3.2 (repo ids and pipeline classes VERIFIED in diffusers wan.md; fp32 VAE, flow_shift,
# 4k+1 frames; the 5B checkpoint's size UNVERIFIED). Test: wan2.2_test.py (832x480, 33 frames, 20 steps -> mp4).
P4_KEY="wan2.2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
