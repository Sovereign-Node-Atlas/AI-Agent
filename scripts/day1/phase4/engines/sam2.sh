#!/usr/bin/env bash
# phase4/engines/sam2.sh — SAM 2.1 hiera-large, green with the CUDA post-processing extension DISABLED (Section 15.2:
# "minor mask cleanup lost"; Section 17 step 2 "SAM 2 with the extension flag").
# Research: rocm-containers.md §3.7: `SAM2_BUILD_CUDA=0 pip install -e .` VERIFIED (INSTALL.md, setup.py). The 2.1 HF
# id facebook/sam2.1-hiera-large is UNVERIFIED; the pull falls back to facebook/sam2-hiera-large (README example,
# VERIFIED) and the test is told which one landed. dl.fbaipublicfiles.com is not allowlisted, so no .pt fallback.
P4_KEY="sam2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_git_from_json
  local marker="$P4_HOST_VENV/.atlas-sam2-installed"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: sam2 already installed in the venv"
  else
    # The extension is skipped on purpose (no CUDA on ROCm); SAM2_BUILD_ALLOW_ERRORS=1 is the setup.py default.
    p4_in_venv --net -e SAM2_BUILD_CUDA=0 -w "$P4_SRC/sam2" -- \
      pip install -c /opt/atlas/constraints-rocm.txt -e . \
      || die "$P4_KEY: SAM2_BUILD_CUDA=0 pip install -e . failed (see $P4_LOG)"
    date -Is >"$marker"
  fi
  p4_pull
  P4_TEST_SETTINGS=("repo=$(p4_resolved_repo facebook/sam2.1-hiera-large)")
}

p4_main "$@"
