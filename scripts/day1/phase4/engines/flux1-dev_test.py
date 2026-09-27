#!/usr/bin/env python3
"""FLUX.1-dev load + generate (rocm-containers.md §6.5, VERIFIED from diffusers flux.md except disable_mmap, which is
passed only when the from_pretrained signature names it — p4common.load_kwargs decides the spellings once, so the
24 GB load runs exactly once). Writes flux_dev.png; prints P4RESULT. bf16, whole pipeline on device (192 GB unified
memory: no CPU offload)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, load_kwargs

REPO = "black-forest-labs/FLUX.1-dev"


def main(t: Test) -> None:
    import torch
    from diffusers import FluxPipeline

    kw = load_kwargs(FluxPipeline, torch.bfloat16)
    t.note(f"from_pretrained kwargs: {sorted(kw)}")
    pipe = FluxPipeline.from_pretrained(REPO, **kw)
    pipe = pipe.to(t.device)
    t.loaded()
    steps = int(t.setting("steps", "20"))
    img = pipe(
        prompt="a modern timber-and-glass house on a cliff at dusk, architectural photograph",
        guidance_scale=3.5,
        height=768,
        width=1360,
        num_inference_steps=steps,
        generator=torch.Generator(t.device).manual_seed(0),
    ).images[0]
    out = t.out / "flux_dev.png"
    img.save(out)
    t.done(out, notes=f"{steps} steps 768x1360 bf16")


if __name__ == "__main__":
    Test("flux1-dev").run(main)
