#!/usr/bin/env bash
# phase4/engines/stable-audio-open.sh — Stable Audio Open 1.0, green (Section 15.2; step 2). Gated with a manual form:
# HF_TOKEN from /etc/atlas/secrets/hf-token.env, read by root and staged as a copy the container uid can read (mode 400,
# private root-only tmpfs) for this engine's pull run only (lib-engine.sh header "HF_TOKEN"); an absent token file
# stops this engine before any container starts, naming the Phase 2 prompt and the licence URL; a 401/403 stops it
# with the licence URL (adjudicated conflict 16).
# Research: rocm-containers.md §3.8 (pip package VERIFIED; flash-attn unavailable on ROCm gfx1151 -> SDPA fallback
# UNVERIFIED for every module; inference call UNVERIFIED-by-snippet). Test: stable-audio-open_test.py (10 s, 50 steps).
# Install deviation (fix round, rule §7.9 — the README says so): stable-audio-tools 0.0.20 declares
# requires-python ">=3.10,<3.11" and pins torch==2.7.1 / torchaudio==2.7.1 (VERIFIED PyPI metadata), so it cannot
# resolve in the Python 3.12 venv under the ROCm constraints. It is installed with --no-deps --ignore-requires-python,
# then the wheel's OWN Requires-Dist set minus torch/torchaudio/setuptools (fix round 2, VERIFIED from the 0.0.20 wheel
# METADATA on 2026-10-04; the hand-written list of the first round lacked einops-exts and PyWavelets, both imported at
# module level on the get_pretrained_model() path, and carried packages 0.0.20 does not import). The `train` and `ui`
# extras are not installed. soundfile writes the wav (torchaudio >= 2.9 would need torchcodec). The json pip list is
# therefore empty; an import smoke check (no GPU) runs in the build so a missing module is a named BUILD failure.
P4_KEY="stable-audio-open"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

# Requires-Dist of stable_audio_tools-0.0.20-py3-none-any.whl (VERIFIED 2026-10-04), without torch==2.7.1,
# torchaudio==2.7.1 (the ROCm torch stays, constraints file) and setuptools<81 (the image's setuptools serves).
SAT_DEPS=(
  "alias-free-torch==0.0.6" dill einops einops-exts huggingface_hub "importlib-resources==5.12.0" torchsde nnAudio
  "PyWavelets==1.4.1" safetensors scipy "sentencepiece==0.1.99" soxr tqdm transformers "v-diffusion-pytorch==0.0.2"
  "vector-quantize-pytorch==1.14.41"
)

p4_build() {
  p4_venv_create
  p4_venv_pip --no-deps --ignore-requires-python "stable-audio-tools==0.0.20"
  p4_venv_pip "${SAT_DEPS[@]}" soundfile
  p4_note "stable-audio-tools==0.0.20 installed --no-deps --ignore-requires-python (its torch 2.7.1 / py<3.11 pins do not apply to the ROCm torch); its Requires-Dist set minus torch/torchaudio installed separately (VERIFIED wheel METADATA)"
  # Import smoke check, no GPU: the module-level imports of the inference path must resolve now (rule §7.4: a missing
  # module is a build failure with its name, not a GPU-test failure hours later).
  local marker
  marker="$(p4_marker import-installed)"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: import smoke check already passed"
  else
    p4_in_venv -- python -c 'import stable_audio_tools; from stable_audio_tools import get_pretrained_model; from stable_audio_tools.inference.generation import generate_diffusion_cond; import soundfile; print("stable_audio_tools import ok")' \
      || die "$P4_KEY: 'import stable_audio_tools' failed in the venv (a runtime dependency of 0.0.20 is missing or incompatible with Python 3.12; the ModuleNotFoundError above names it; see $P4_LOG)"
    date -Is >"$marker"
  fi
  p4_pull
}

p4_main "$@"
