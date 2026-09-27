#!/usr/bin/env bash
# phase4/engines/stable-audio-open.sh — Stable Audio Open 1.0, green (Section 15.2; step 2). Gated with a manual form:
# HF_TOKEN from the secrets file (mounted read-only for this pull only); a 401/403 stops this engine with the licence
# URL (adjudicated conflict 16).
# Research: rocm-containers.md §3.8 (pip package VERIFIED; flash-attn unavailable on ROCm gfx1151 -> SDPA fallback
# UNVERIFIED for every module; inference call UNVERIFIED-by-snippet). Test: stable-audio-open_test.py (10 s, 50 steps).
# Install deviation (fix round, rule §7.9 — the README says so): stable-audio-tools 0.0.20 declares
# requires-python ">=3.10,<3.11" and pins torch==2.7.1 / torchaudio==2.7.1 (VERIFIED PyPI metadata and pyproject), so
# it cannot resolve in the Python 3.12 venv under the ROCm constraints. It is installed with --no-deps
# --ignore-requires-python, then its runtime dependencies minus torch/torchaudio (list taken from its pyproject at
# 0.0.20; UNVERIFIED complete: an ImportError in the test names the missing one) under the constraints file. The json
# pip list is therefore empty. soundfile writes the wav (torchaudio >= 2.9 would need torchcodec).
P4_KEY="stable-audio-open"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_create
  p4_venv_pip --no-deps --ignore-requires-python "stable-audio-tools==0.0.20"
  p4_venv_pip einops alias-free-torch torchsde v-diffusion-pytorch vector-quantize-pytorch k-diffusion \
    x-transformers local-attention ema-pytorch descript-audio-codec laion-clap transformers safetensors pedalboard \
    huggingface_hub soundfile
  p4_note "stable-audio-tools==0.0.20 installed --no-deps --ignore-requires-python (its torch 2.7.1 / py<3.11 pins do not apply to the ROCm torch); deps installed separately, UNVERIFIED complete"
  p4_pull
}

p4_main "$@"
