#!/usr/bin/env python3
"""Stable Audio Open 1.0: a 10-second conditioned generation (rocm-containers.md §6.5, UNVERIFIED-by-snippet of the
card; the package is VERIFIED). Writes stable_audio.wav."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "stabilityai/stable-audio-open-1.0"


def main(t: Test) -> None:
    import torch
    import torchaudio
    from einops import rearrange
    from stable_audio_tools import get_pretrained_model
    from stable_audio_tools.inference.generation import generate_diffusion_cond

    model, cfg = get_pretrained_model(REPO)
    sr, n = int(cfg["sample_rate"]), int(cfg["sample_size"])
    model = model.to(t.device)
    t.loaded()
    seconds = int(t.setting("seconds", "10"))
    steps = int(t.setting("steps", "50"))
    out_t = generate_diffusion_cond(
        model, steps=steps, cfg_scale=7,
        conditioning=[{"prompt": "128 BPM tech house drum loop", "seconds_start": 0, "seconds_total": seconds}],
        sample_size=n, sigma_min=0.3, sigma_max=500, sampler_type="dpmpp-3m-sde", device=t.device,
    )
    audio = rearrange(out_t, "b d n -> d (b n)").to(torch.float32)
    peak = audio.abs().max()
    if not torch.isfinite(audio).all() or float(peak) == 0.0:
        t.fail("silent or non-finite audio")
    audio = audio.div(peak).clamp(-1, 1).cpu()
    out = t.out / "stable_audio.wav"
    torchaudio.save(str(out), audio, sr)
    t.done(out, notes=f"{seconds} s at {sr} Hz, {steps} steps")


if __name__ == "__main__":
    Test("stable-audio-open").run(main)
