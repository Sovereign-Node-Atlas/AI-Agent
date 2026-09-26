#!/usr/bin/env bash
# phase4/engines/stable-audio-open.sh — Stable Audio Open 1.0, green (Section 15.2; step 2). Gated with a manual form:
# HF_TOKEN from the secrets file; a 401/403 stops this engine with the licence URL (adjudicated conflict 16).
# Research: rocm-containers.md §3.8 (pip package VERIFIED; flash-attn unavailable on ROCm gfx1151 -> SDPA fallback
# UNVERIFIED for every module; inference call UNVERIFIED-by-snippet). Test: stable-audio-open_test.py (10 s, 50 steps).
P4_KEY="stable-audio-open"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
