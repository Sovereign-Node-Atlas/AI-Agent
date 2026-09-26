# syntax=docker/dockerfile:1
# docker/rocm-base/Dockerfile — the ONE ROCm/PyTorch base image for gfx1151 (ATLAS_FRAMEWORK_REVIEW.md Section 3.4,
# Appendix B "Containers needing the GPU"; Section 17 Phase 4 step 1; CONVENTIONS.md §1). Built by phase4-engines.sh
# step 01 as atlas/rocm-base:10.0.0 (label org.atlas.rocm-base.version, see config/phase4-engines.json base_image) and
# proven by verify/v11-rocm-selftest.sh before any engine is touched. ROCm userspace lives ONLY here; the host supplies
# the amdgpu driver, /dev/kfd and /dev/dri (rule §7.5).
#
# Sources (research/rocm-containers.md, 2026-09-22):
#   * index https://stable.repo.amd.com/rocm/whl-next/ and the extras "torch[device-gfx1151]" etc.: VERIFIED
#     (ROCm/TheRock RELEASES.md). Versions torch 2.13.0+rocm10.0.0 / torchvision 0.28.0+rocm10.0.0: VERIFIED (TheRock
#     #7839). "rocm[libraries,devel,device-gfx1151]" installs the SDK (rocminfo, hipcc): extras VERIFIED (RELEASES.md).
#     Adjudicated conflict 12: AMD's own wheels are the primary; scottt's 2025 wheel is not pinned.
#   * If the exact-arch device wheel is missing every kernel launch fails with hipErrorInvalidImage (TheRock #7839),
#     so the build asserts `pip show amd-torch-device-gfx1151`.
#   * Env: TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 and TORCH_BLAS_PREFER_HIPBLASLT=1 (VERIFIED kyuz0
#     scripts/01-rocm-envs.sh, ROCm/ROCm #6034). HSA_OVERRIDE_GFX_VERSION is deliberately UNSET (native gfx1151
#     kernels; the override breaks them, #6034). PYTORCH_HIP_ALLOC_CONF is never set to backend:malloc (#6034).
#   * torch goes into the SYSTEM python so per-engine venvs created with --system-site-packages inherit it
#     (research §4 item 2: a venv made from a venv does not see the parent venv's packages).
#   * HF_HUB_ENABLE_HF_TRANSFER=0: hf_transfer ignores proxies (lib/common.sh proxy_env, platform research §6), and
#     every outbound request must go through the allowlist proxy (rule §7.1). The research Dockerfile set it to 1;
#     this file follows the rule.
#
# UNVERIFIED (each fails the build loudly rather than silently when wrong):
#   * that the stable index serves cp312 wheels for the "==" pins above (research §1.1: exact cp-tag list unverified).
#     If pip cannot resolve "torch==2.13.0+rocm10.0.0" the RUN fails and the error names the pin; drop the "==" pins
#     and let the extras resolve, then record the resolved versions from /opt/atlas/constraints-rocm.txt.
#   * `rocm-sdk path --root` flag spelling (research §1.2); the rocminfo symlink is best-effort and selftest.py has
#     fallbacks (rocminfo on PATH, then $(rocm-sdk path --root)/bin/rocminfo, then torch's gcnArchName).
#   * torchaudio, diffusers, transformers, accelerate, huggingface_hub, safetensors, einops, pillow, numpy, scipy carry
#     no research pin (rule §7.9; the README must say so). The resolved versions are frozen into
#     /opt/atlas/base-freeze.txt at build time and copied to /srv/atlas/engines/manifests/ by the driver.
FROM ubuntu:24.04

ARG ROCM_INDEX=https://stable.repo.amd.com/rocm/whl-next/
ARG ROCM_VER=10.0.0
ARG TORCH_VER=2.13.0
ARG TORCHVISION_VER=0.28.0
# The atlas service account's uid/gid (from /etc/atlas/docker.env, written by Phase 1 step 6): the image runs as it.
ARG ATLAS_UID=999
ARG ATLAS_GID=999
# Proxy build args are passed by the driver (CONTAINER_HTTP(S)_PROXY from /etc/atlas/docker.env); apt and pip honour them.

LABEL org.atlas.rocm-base.version="1" \
      org.atlas.rocm-base.section="3.4,17.4.1" \
      org.atlas.rocm-base.rocm="${ROCM_VER}"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Python 3.12 (Ubuntu 24.04's default), build tools for source builds (SAM 2 without CUDA, tokenizers), media libs
# (ffmpeg for Wan/Stable Audio, sox and libsndfile for CosyVoice), and the X/GL runtime libs the Blender tarball needs
# (research §3.14, UNVERIFIED list; a missing one shows as a Blender dlopen error in the blender-cycles log).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev python3-pip \
      git git-lfs curl ca-certificates xz-utils \
      ffmpeg libsndfile1 sox libgomp1 libatomic1 \
      build-essential cmake ninja-build pkg-config libdrm-dev \
      libxi6 libxxf86vm1 libxfixes3 libxrender1 libgl1 libegl1 libxkbcommon0 libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*

# ROCm SDK + torch for gfx1151 into the SYSTEM python (see header). The last line fails the build when the gfx1151
# kernel pack did not come with torch (hipErrorInvalidImage at run time otherwise, TheRock #7839).
RUN python3.12 -m pip install --break-system-packages --upgrade pip \
    && python3.12 -m pip install --break-system-packages --index-url "${ROCM_INDEX}" \
      "rocm[libraries,devel,device-gfx1151]==${ROCM_VER}" \
      "torch[device-gfx1151]==${TORCH_VER}+rocm${ROCM_VER}" \
      "torchvision[device-gfx1151]==${TORCHVISION_VER}+rocm${ROCM_VER}" \
      torchaudio \
    && python3.12 -m pip show amd-torch-device-gfx1151 >/dev/null \
    && python3.12 -c "import torch; assert torch.version.hip, 'torch is not a ROCm build'; print('torch', torch.__version__, 'hip', torch.version.hip)"

# Shared pure-python libraries every engine venv inherits (no research pins; see header). diffusers is needed by the
# V11 self-test's two-step UNet (research §2.3 item 4).
RUN python3.12 -m pip install --break-system-packages \
      "huggingface_hub" safetensors accelerate einops pillow numpy scipy diffusers transformers \
    && python3.12 -m pip freeze > /opt/atlas-base-freeze.txt

# The constraints file every engine install runs under (research §6.6): generated from what pip actually resolved,
# so an engine pin (OpenVLA torch==2.2.0, CosyVoice requirements) can never replace the ROCm torch.
RUN mkdir -p /opt/atlas \
    && mv /opt/atlas-base-freeze.txt /opt/atlas/base-freeze.txt \
    && grep -E '^(torch|torchaudio|torchvision|amd-|rocm)[A-Za-z0-9_.-]*==' /opt/atlas/base-freeze.txt > /opt/atlas/constraints-rocm.txt \
    && test -s /opt/atlas/constraints-rocm.txt \
    && grep -q '^torch==' /opt/atlas/constraints-rocm.txt \
    && cat /opt/atlas/constraints-rocm.txt

# rocminfo/hipcc from the wheel install on PATH (best-effort; `rocm-sdk path --root` spelling UNVERIFIED, §1.2).
RUN ROCM_ROOT="$(rocm-sdk path --root 2>/dev/null || true)"; \
    if [ -n "$ROCM_ROOT" ] && [ -x "$ROCM_ROOT/bin/rocminfo" ]; then \
      ln -sf "$ROCM_ROOT/bin/rocminfo" /usr/local/bin/rocminfo; \
      [ -x "$ROCM_ROOT/bin/rocm-smi" ] && ln -sf "$ROCM_ROOT/bin/rocm-smi" /usr/local/bin/rocm-smi; \
      echo "rocminfo linked from $ROCM_ROOT"; \
    else \
      echo "NOTE: rocm-sdk path --root gave nothing usable; selftest.py falls back to torch's gcnArchName"; \
    fi

# gfx1151 runtime knobs (research §2.2). HF_HOME is bind-mounted from the host (Appendix C: one cache for all engines).
ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 \
    TORCH_BLAS_PREFER_HIPBLASLT=1 \
    HIP_VISIBLE_DEVICES=0 \
    HF_HOME=/srv/atlas/engines/hf \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    MIOPEN_USER_DB_PATH=/srv/atlas/engines/miopen \
    MIOPEN_CUSTOM_CACHE_DIR=/srv/atlas/engines/miopen \
    HOME=/srv/atlas/engines/home \
    ATLAS_ROCM_CONSTRAINTS=/opt/atlas/constraints-rocm.txt

# The atlas service account (Appendix B: run as the service user in render and video; the gids are added at run time
# with --group-add, numerically, because "render" has no fixed gid). The mount points exist so a run without the
# bind mounts fails on a missing weight, not on a missing directory.
RUN groupadd -g "${ATLAS_GID}" atlas 2>/dev/null || true \
    && useradd -u "${ATLAS_UID}" -g "${ATLAS_GID}" -M -d /srv/atlas/engines/home -s /usr/sbin/nologin atlas 2>/dev/null || true \
    && mkdir -p /srv/atlas/engines /srv/atlas/workspace/phase4-samples /opt/atlas/phase4 \
    && chown -R "${ATLAS_UID}:${ATLAS_GID}" /srv/atlas

USER ${ATLAS_UID}:${ATLAS_GID}
WORKDIR /srv/atlas/engines
CMD ["python3.12"]
