#!/usr/bin/env bash
# phase4/engines/florence-2.sh — Florence-2-large, native transformers (>= 4.56.1), green (Section 15.2; step 2).
# Adjudicated conflict 15: florence-community/* checkpoints, no trust_remote_code. Research: rocm-containers.md §3.3.
# Test: florence-2_test.py (<CAPTION>, <OCR>, <OD> on a synthetic picture -> florence2.json).
P4_KEY="florence-2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
