#!/usr/bin/env bash
# phase4/engines/trellis.sh — Microsoft TRELLIS image-to-3D, YELLOW (Section 15.2: "community ROCm forks exist but hit
# build errors on sparse-voxel kernels, attempt, log, move on"; Section 17 step 3). Any failure -> deferred with the
# last 20 log lines in the result (lib-engine.sh EXIT trap), never a block.
# Research: rocm-containers.md §3.13. Attempt A: upstream microsoft/TRELLIS code + kroqueta-s/trellis-strix-halo
# pure-torch shims for spconv/flash_attn/nvdiffrast/kaolin (gfx1151-validated on WINDOWS only; the Linux port and the
# shim directory layout are UNVERIFIED: trellis_test.py discovers the shim packages by name and says what it found).
# Attempt B (TRELLIS.2 + paladx2105/ComfyUI-Trellis2-AMD ROCm 10.0 wheels) is NOT automated: the wheel URLs are
# unverified, so it is recorded here as the next thing to try by hand.
P4_KEY="trellis"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_git_from_json
  [[ -d "$P4_HOST_SRC/TRELLIS/trellis" ]] || die "$P4_KEY: $P4_HOST_SRC/TRELLIS/trellis package directory not found (upstream layout changed?)"
  # rembg's background-removal model is fetched at first use from GitHub releases (allowlisted); do it now with the
  # network on so the offline test does not stall on it. Best effort: the test also passes an RGBA input.
  # UNVERIFIED: rembg's new_session("u2net") API name.
  p4_in_venv --net -- python -c 'from rembg import new_session; new_session("u2net"); print("rembg u2net cached")' \
    || p4_note "rembg u2net pre-fetch failed (best effort; the test sends an RGBA image so rembg may not be needed)"
  p4_pull
  p4_note "attempt A: upstream TRELLIS + kroqueta-s shims; attempt B (TRELLIS.2 + paladx2105 ROCm 10.0 wheels, gfx1150-gfx1153 fat build) not automated (unverified wheel URLs)"
}

p4_main "$@"
