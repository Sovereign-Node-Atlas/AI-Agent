#!/usr/bin/env bash
# phase4/engines/trellis.sh — Microsoft TRELLIS image-to-3D, YELLOW (Section 15.2: "community ROCm forks exist but hit
# build errors on sparse-voxel kernels, attempt, log, move on"; Section 17 step 3). Any failure -> deferred with the
# last 20 log lines in the result (lib-engine.sh EXIT trap), never a block.
# Research: rocm-containers.md §3.13. Attempt A: upstream microsoft/TRELLIS code + kroqueta-s/trellis-strix-halo
# pure-torch shims for spconv/flash_attn/nvdiffrast/kaolin (gfx1151-validated on WINDOWS only; the Linux port and the
# shim directory layout are UNVERIFIED: trellis_test.py discovers the shim packages by name and says what it found).
# Attempt B (TRELLIS.2 + paladx2105/ComfyUI-Trellis2-AMD ROCm 10.0 wheels) is NOT automated: the wheel URLs are
# unverified, so it is recorded here as the next thing to try by hand.
# Fix round 2 (VERIFIED from upstream main on 2026-10-04): the json pip list now carries TRELLIS setup.sh --basic's
# dependencies (xatlas pyvista pymeshfix igraph, utils3d at the pinned commit) plus plyfile — both are imported at
# module level by trellis/representations/gaussian/gaussian_model.py, so `from trellis.pipelines import ...` failed
# before any shim mattered. The image conditioner is DINOv2 through torch.hub.load('facebookresearch/dinov2', ...)
# (trellis_image_to_3d.py line 74): the code zip comes from github.com (allowlisted) and the weights from
# dl.fbaipublicfiles.com, which Section 12.5 / config/allowlist.txt do NOT list (sam2.sh records the same decision for
# its .pt fallback). The pre-fetch below runs with the network on; when the host is denied, this engine records
# deferred with that exact reason and widens nothing — adding the host is the Principal's decision for the Phase 1
# writer, not an engine script's. Both clones are pinned (json git[].ref).
P4_KEY="trellis"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

# DINOv2 model name used by TRELLIS-image-large's pipeline.json (UNVERIFIED-by-snippet: the pull's snapshot is read for
# the real name below when it carries one; dinov2_vitl14_reg is the upstream default).
TR_DINO_DEFAULT="dinov2_vitl14_reg"

p4_build() {
  p4_venv_from_json
  p4_git_from_json
  [[ -d "$P4_HOST_SRC/TRELLIS/trellis" ]] || die "$P4_KEY: $P4_HOST_SRC/TRELLIS/trellis package directory not found (upstream layout changed?)"
  # Import smoke check (no GPU, no shims needed for the module-level imports the fix round added): a missing module is
  # a named build failure (rule §7.4).
  p4_in_venv -e PYTHONPATH="$P4_SRC/TRELLIS" -- python -c 'import plyfile, utils3d, xatlas, pyvista, pymeshfix, igraph, cv2, trimesh, easydict, rembg; print("trellis module-level deps import ok")' \
    || die "$P4_KEY: a module-level dependency of upstream TRELLIS does not import in the venv (the ModuleNotFoundError above names it; see $P4_LOG)"
  # rembg's background-removal model is fetched at first use from GitHub releases (allowlisted); do it now with the
  # network on so the offline test does not stall on it. Best effort: the test also passes an RGBA input.
  # UNVERIFIED: rembg's new_session("u2net") API name.
  p4_in_venv --net -- python -c 'from rembg import new_session; new_session("u2net"); print("rembg u2net cached")' \
    || p4_note "rembg u2net pre-fetch failed (best effort; the test sends an RGBA image so rembg may not be needed)"
  p4_pull
  _tr_prefetch_dinov2
  p4_note "attempt A: upstream TRELLIS + kroqueta-s shims; attempt B (TRELLIS.2 + paladx2105 ROCm 10.0 wheels, gfx1150-gfx1153 fat build) not automated (unverified wheel URLs)"
}

# _tr_prefetch_dinov2 — torch.hub.load('facebookresearch/dinov2', NAME, pretrained=True) with the network on, into
# TORCH_HOME=$HOME/.cache/torch on the persisted HOME (lib-engine.sh sets TORCH_HOME explicitly for every run), so the
# offline GPU test finds both the hub repo and the weights. Idempotent by marker. A denied dl.fbaipublicfiles.com is
# the recorded, honest reason for deferred (header).
_tr_prefetch_dinov2() {
  local marker name
  marker="$(p4_marker dinov2-prefetched)"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: DINOv2 conditioner already pre-fetched"
    return 0
  fi
  name="$TR_DINO_DEFAULT"
  # pipeline.json of the pulled snapshot names the conditioner ("image_cond_model"); read it as data through the
  # root-held manifest's sha (p4_safe_read refuses symlinks; the hub lays snapshot files out as symlinks into blobs/,
  # so resolve through the container instead).
  local found
  found="$(p4_in_venv -- python -c '
import json, sys
from huggingface_hub import hf_hub_download
try:
    p = hf_hub_download(sys.argv[1], "pipeline.json", local_files_only=True)
    cfg = json.load(open(p))
    print(cfg.get("args", {}).get("image_cond_model", ""))
except Exception as exc:
    print("", file=sys.stdout); print(f"pipeline.json not readable: {exc!r}", file=sys.stderr)
' "$(p4_resolved_repo microsoft/TRELLIS-image-large)" 2>/dev/null | tail -n1 | tr -d '[:space:]')" || true
  if [[ "$found" =~ ^dinov2_[a-z0-9_]+$ ]]; then name="$found"; fi
  log "$P4_KEY: pre-fetching the DINOv2 image conditioner '$name' via torch.hub (github.com code zip + dl.fbaipublicfiles.com weights) through the proxy"
  if p4_in_venv --net -- python -c 'import sys, torch; m = torch.hub.load("facebookresearch/dinov2", sys.argv[1], pretrained=True); print("dinov2 cached:", type(m).__name__)' "$name"; then
    date -Is >"$marker"
    p4_note "DINOv2 '$name' pre-fetched into TORCH_HOME (torch.hub)"
    return 0
  fi
  if grep -qxF "dl.fbaipublicfiles.com" "$P4_DAY1/config/allowlist.txt" 2>/dev/null; then
    die "$P4_KEY: torch.hub.load('facebookresearch/dinov2', '$name') failed although dl.fbaipublicfiles.com is allowlisted (see $P4_LOG and /var/log/squid/access.log)"
  fi
  die "$P4_KEY: attempt A cannot load offline: TRELLIS's image conditioner needs the DINOv2 weights from dl.fbaipublicfiles.com (torch.hub, trellis_image_to_3d.py line 74), a host Section 12.5 / config/allowlist.txt do not list and this script will not add. Principal decision: if the host is to be allowed, add it to scripts/day1/config/allowlist.txt, then sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist /path/to/repo/scripts/day1/config/allowlist.txt (no reboot), delete $P4_RESULT and re-run: sudo ./atlas-day1.sh phase4 --force 03. Otherwise TRELLIS stays deferred (attempt B by hand)"
}

p4_main "$@"
