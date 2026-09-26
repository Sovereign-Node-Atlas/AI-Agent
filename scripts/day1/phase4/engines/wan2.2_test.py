#!/usr/bin/env python3
"""Wan2.2 TI2V-5B text-to-video (rocm-containers.md §6.5: structure VERIFIED from diffusers wan.md — fp32 VAE,
UniPC with flow_shift, frames = 4k+1; the 5B-specific values are UNVERIFIED). Writes wan22_5b.mp4."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"


def _load(cls: type, **kw: object) -> object:
    """from_pretrained with the dtype/torch_dtype and disable_mmap spellings tried in order (research §3.1 note)."""
    import torch

    for extra in ({"dtype": kw.pop("_dtype", torch.bfloat16), "disable_mmap": True},
                  {"dtype": torch.bfloat16}, {"torch_dtype": torch.bfloat16}):
        try:
            return cls.from_pretrained(REPO, **kw, **extra)
        except TypeError:
            continue
    return cls.from_pretrained(REPO, **kw)


def main(t: Test) -> None:
    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    from diffusers.utils import export_to_video

    try:
        vae = AutoencoderKLWan.from_pretrained(REPO, subfolder="vae", dtype=torch.float32)
    except TypeError:
        vae = AutoencoderKLWan.from_pretrained(REPO, subfolder="vae", torch_dtype=torch.float32)
    pipe = _load(WanPipeline, vae=vae)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=5.0)
    pipe.to(t.device)
    t.loaded()
    frames_n = int(t.setting("frames", "33"))
    steps = int(t.setting("steps", "20"))
    frames = pipe(
        prompt="a tram crossing a rainy city street at night, cinematic",
        negative_prompt="",
        height=480,
        width=832,
        num_frames=frames_n,
        guidance_scale=5.0,
        num_inference_steps=steps,
        generator=torch.Generator(t.device).manual_seed(0),
    ).frames[0]
    out = t.out / "wan22_5b.mp4"
    export_to_video(frames, str(out), fps=24)
    t.done(out, notes=f"{frames_n} frames 832x480 {steps} steps bf16 (fp32 VAE)")


if __name__ == "__main__":
    Test("wan2.2").run(main)
