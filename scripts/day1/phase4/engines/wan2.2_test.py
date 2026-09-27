#!/usr/bin/env python3
"""Wan2.2 TI2V-5B text-to-video (rocm-containers.md §6.5: structure VERIFIED from diffusers wan.md — fp32 VAE,
UniPC with flow_shift, frames = 4k+1; the 5B-specific values are UNVERIFIED). The dtype/disable_mmap keyword
spellings are decided once from the from_pretrained signature (p4common.load_kwargs): a ~34 GB load runs exactly once.
Writes wan22_5b.mp4."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, load_kwargs

REPO = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"


def main(t: Test) -> None:
    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    from diffusers.utils import export_to_video

    vae = AutoencoderKLWan.from_pretrained(REPO, subfolder="vae",
                                           **load_kwargs(AutoencoderKLWan, torch.float32, disable_mmap=False))
    pipe = WanPipeline.from_pretrained(REPO, vae=vae, **load_kwargs(WanPipeline, torch.bfloat16))
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
