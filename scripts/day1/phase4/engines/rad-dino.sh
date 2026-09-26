#!/usr/bin/env bash
# phase4/engines/rad-dino.sh — Rad-DINO, green (Section 15.2; step 2). Research: rocm-containers.md §3.6 (loader
# UNVERIFIED-by-snippet; the card says research use only, recorded in the json licence_note).
# Test: rad-dino_test.py (a synthetic chest-X-ray-like image -> pooler_output [1, 768] -> rad_dino_embedding.npy).
P4_KEY="rad-dino"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
