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
#     this file follows the rule. HF_HUB_DISABLE_XET=1 (fix round 3): huggingface_hub >= 1.0 (2.0.0 VERIFIED) downloads
#     LFS/Xet files through the hf_xet Rust client by default; whether it honours https_proxy is UNVERIFIED, and a
#     bypass would be dropped by DOCKER-USER (no direct egress) as a hung download, so the plain HTTP client, which
#     honours the proxy, is forced (the constant HF_HUB_DISABLE_XET is VERIFIED in huggingface_hub constants.py).
#     Remove it only after one pull is observed in /var/log/squid/access.log going through cas-bridge.xethub.hf.co.
#   * No `# syntax=docker/dockerfile:1` line (fix round 3): it would make BuildKit pull and run the floating
#     docker/dockerfile:1 frontend image on every fresh build (unpinned third-party code, rule §7.9); nothing here
#     needs more than the built-in frontend.
#
# UNVERIFIED (each fails the build loudly rather than silently when wrong):
#   * that the stable index serves cp312 wheels for the "==" pins above (research §1.1: exact cp-tag list unverified).
#     If pip cannot resolve "torch==2.13.0+rocm10.0.0" the RUN fails and the error names the pin; drop the "==" pins
#     and let the extras resolve, then record the resolved versions from /opt/atlas/constraints-rocm.txt.
#   * that the stable index ALONE (--index-url, the research-VERIFIED pattern) also serves torch's third-party
#     dependencies (sympy, filelock, jinja2, networkx, fsspec, typing-extensions, setuptools). If pip reports "no
#     matching distribution" for one of them, add `--extra-index-url https://pypi.org/simple` to that RUN (pypi.org is
#     allowlisted) and pin torchaudio to the +rocm${ROCM_VER} build it resolves, so PyPI cannot substitute a CUDA
#     torchaudio; phase4-engines.sh's build failure message says the same.
#   * The constraints file is generated IMMEDIATELY after the ROCm install, from what pip resolved there, and every
#     later install in this image runs under it (fix round 3): a shared library that dragged in another torch would
#     fail that install loudly instead of replacing the gfx1151 wheel and poisoning the constraints for every venv.
#     The ROCm build is re-asserted after the shared libraries (torch.version.hip and the exact +rocm version).
#   * `rocm-sdk path --root` flag spelling (research §1.2): the rocminfo step tries it first, then searches the wheel
#     install for an executable rocminfo, and FAILS THE BUILD when none is found (V11 requires rocminfo itself,
#     Section 21; selftest.py no longer accepts torch's gcnArchName as a substitute).
#   * Telemetry off for every process in this image (rule §7.1): HF_HUB_DISABLE_TELEMETRY, DISABLE_TELEMETRY,
#     DO_NOT_TRACK, HF_HUB_DISABLE_IMPLICIT_TOKEN in the ENV block (lib-engine.sh repeats them at run time).
#   * torchaudio, diffusers, transformers, accelerate, huggingface_hub, safetensors, einops, pillow, numpy, scipy carry
#     no research pin (rule §7.9; the README must say so). The resolved versions are frozen into
#     /opt/atlas/base-freeze.txt at build time and copied to /srv/atlas/engines/manifests/ by the driver (as atlas).
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

# PYTHONNOUSERSITE=1 (fix round 2): the shared bind-mounted HOME could carry a ~/.local/lib/python3.12/site-packages
# planted by any earlier install; the system python (and every --system-site-packages venv) must never import from it,
# even in a hand run that bypasses lib-engine.sh (which sets it again at run time).
# HF_HUB_DISABLE_XET=1 and HF_HUB_ENABLE_HF_TRANSFER=0 are set HERE, before the first RUN (fix round 4), so every
# Hugging Face download in this image, at build time and at run time, uses huggingface_hub's plain HTTPS client, which
# honours https_proxy and therefore goes through squid (Section 12.5: outbound only through the allowlist; CONVENTIONS
# §7.1). hf_xet and hf_transfer are the two paths that may bypass the proxy (header); neither is ever on in this image.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    HF_HUB_DISABLE_XET=1

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

# ROCm SDK + torch for gfx1151 into the SYSTEM python (see header). The pip show line fails the build when the gfx1151
# kernel pack did not come with torch (hipErrorInvalidImage at run time otherwise, TheRock #7839). The constraints file
# every later install runs under (research §6.6) is generated HERE, from exactly what this step resolved, so no later
# step and no engine pin (OpenVLA torch==2.2.0, CosyVoice requirements) can replace the ROCm torch (header).
RUN python3.12 -m pip install --break-system-packages --upgrade pip \
    && python3.12 -m pip install --break-system-packages --index-url "${ROCM_INDEX}" \
      "rocm[libraries,devel,device-gfx1151]==${ROCM_VER}" \
      "torch[device-gfx1151]==${TORCH_VER}+rocm${ROCM_VER}" \
      "torchvision[device-gfx1151]==${TORCHVISION_VER}+rocm${ROCM_VER}" \
      torchaudio \
    && python3.12 -m pip show amd-torch-device-gfx1151 >/dev/null \
    && python3.12 -c "import torch; assert torch.version.hip, 'torch is not a ROCm build'; assert torch.__version__ == '${TORCH_VER}+rocm${ROCM_VER}', torch.__version__; print('torch', torch.__version__, 'hip', torch.version.hip)" \
    && mkdir -p /opt/atlas \
    && python3.12 -m pip freeze | grep -E '^(torch|torchaudio|torchvision|amd-|rocm)[A-Za-z0-9_.-]*==' > /opt/atlas/constraints-rocm.txt \
    && test -s /opt/atlas/constraints-rocm.txt \
    && grep -q "^torch==${TORCH_VER}+rocm${ROCM_VER}$" /opt/atlas/constraints-rocm.txt \
    && grep -q '^torchaudio==' /opt/atlas/constraints-rocm.txt \
    && cat /opt/atlas/constraints-rocm.txt

# Shared pure-python libraries every engine venv inherits (no research pins; see header), installed UNDER the
# constraints file: a resolver that wanted another torch fails here, loudly (rule §7.4), instead of replacing the
# gfx1151 wheel. diffusers is needed by the V11 self-test's two-step UNet (research §2.3 item 4). The ROCm build is
# then re-asserted and the full resolved set frozen as the image's manifest.
RUN python3.12 -m pip install --break-system-packages -c /opt/atlas/constraints-rocm.txt \
      "huggingface_hub" safetensors accelerate einops pillow numpy scipy diffusers transformers \
    && python3.12 -c "import torch; assert torch.version.hip, 'torch is not a ROCm build after the shared libraries'; assert torch.__version__ == '${TORCH_VER}+rocm${ROCM_VER}', torch.__version__" \
    && python3.12 -m pip show amd-torch-device-gfx1151 >/dev/null \
    && python3.12 -m pip freeze > /opt/atlas/base-freeze.txt \
    && test -s /opt/atlas/base-freeze.txt

# rocminfo (and rocm-smi when present) from the wheel install on PATH. `rocm-sdk path --root` spelling is UNVERIFIED
# (research §1.2), so the wheel tree is searched as well; the build stops when no rocminfo exists, because V11's
# proof is rocminfo listing gfx1151 (Section 21) and an image without it cannot pass.
RUN set -e; \
    ROCM_ROOT="$(rocm-sdk path --root 2>/dev/null || true)"; \
    BIN=""; \
    if [ -n "$ROCM_ROOT" ] && [ -x "$ROCM_ROOT/bin/rocminfo" ]; then BIN="$ROCM_ROOT/bin/rocminfo"; fi; \
    if [ -z "$BIN" ]; then \
      PKG="$(python3.12 -c 'import rocm_sdk,os; print(os.path.dirname(rocm_sdk.__file__))' 2>/dev/null || true)"; \
      for d in "$PKG" /usr/local/lib/python3.12 /usr/lib/python3/dist-packages; do \
        [ -d "$d" ] || continue; \
        BIN="$(find "$d" -name rocminfo -type f -perm -u+x 2>/dev/null | head -n1)"; \
        [ -n "$BIN" ] && break; \
      done; \
    fi; \
    if [ -z "$BIN" ]; then echo "FATAL: rocminfo not found in the rocm[libraries,devel,device-gfx1151] install (rocm-sdk path --root gave '$ROCM_ROOT'); research §1.2 layout changed" >&2; exit 1; fi; \
    ln -sf "$BIN" /usr/local/bin/rocminfo; \
    SMI="$(find "$(dirname "$(dirname "$BIN")")" -name rocm-smi -type f -perm -u+x 2>/dev/null | head -n1)"; \
    [ -n "$SMI" ] && ln -sf "$SMI" /usr/local/bin/rocm-smi; \
    test -x /usr/local/bin/rocminfo || exit 1; \
    echo "rocminfo linked from $BIN"

# gfx1151 runtime knobs (research §2.2). HF_HOME is bind-mounted from the host (Appendix C: one cache for all engines);
# the HF download-path switches (XET, hf_transfer) are in the first ENV block above so they also bind at build time.
ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 \
    TORCH_BLAS_PREFER_HIPBLASLT=1 \
    HIP_VISIBLE_DEVICES=0 \
    HF_HOME=/srv/atlas/engines/hf \
    MIOPEN_USER_DB_PATH=/srv/atlas/engines/miopen \
    MIOPEN_CUSTOM_CACHE_DIR=/srv/atlas/engines/miopen \
    HOME=/srv/atlas/engines/home \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
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
