#!/usr/bin/env bash
# phase4/engines/clay-prithvi.sh — Prithvi-EO-2.0-300M-TL via TerraTorch (V9 engine) with Clay v1.5 second, tier
# VERIFY: V9 "Clay or Prithvi builds and runs" (Section 21; Section 17 step 4: deferred if they fail).
# Research: rocm-containers.md §3.12: `pip install terratorch` and the registry name prithvi_eo_v2_300_tl VERIFIED
# (README); the BACKBONE_REGISTRY.build(..., pretrained=True) call is UNVERIFIED-by-snippet. Clay: the quickstart
# (git install, clay-v1.5.ckpt, ClayMAEModule.load_from_checkpoint, model.encoder(chips, timestamps, wavelengths))
# is VERIFIED; the Clay install is best effort (Prithvi alone satisfies V9).
P4_KEY="clay-prithvi"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_git_from_json
  # Clay's own dependency list may pin torch/lightning; the constraints file keeps the ROCm torch. Best effort.
  if [[ -e "$P4_HOST_VENV/.atlas-clay-installed" ]]; then
    log "$P4_KEY: clay already installed"
  elif p4_in_venv --net -- pip install -c /opt/atlas/constraints-rocm.txt "$P4_SRC/clay-model"; then
    date -Is >"$P4_HOST_VENV/.atlas-clay-installed"
  else
    p4_note "Clay install failed (best effort; V9 rests on Prithvi); see $P4_LOG"
  fi
  p4_pull
}

p4_main "$@"
