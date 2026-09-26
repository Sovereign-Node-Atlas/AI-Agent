#!/usr/bin/env python3
"""Rad-DINO embedding of a synthetic X-ray-like image (rocm-containers.md §6.5, UNVERIFIED-by-snippet of the card:
AutoModel + AutoImageProcessor, pooler_output [1, 768]). Writes rad_dino_embedding.npy."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, synthetic_image

REPO = "microsoft/rad-dino"


def main(t: Test) -> None:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModel

    model = AutoModel.from_pretrained(REPO).to(t.device).eval()
    proc = AutoImageProcessor.from_pretrained(REPO)
    t.loaded()
    img = Image.open(synthetic_image(t.out / "cxr.png", "xray", (512, 512))).convert("RGB")
    with torch.inference_mode():
        out = model(**proc(images=img, return_tensors="pt").to(t.device))
    emb = out.pooler_output.float().cpu().numpy()
    if emb.shape != (1, 768) or not np.isfinite(emb).all():
        t.fail(f"pooler_output shape {emb.shape}, expected (1, 768)")
    path = t.out / "rad_dino_embedding.npy"
    np.save(path, emb)
    t.done(path, notes=f"embedding {emb.shape}, norm {float(np.linalg.norm(emb)):.3f}")


if __name__ == "__main__":
    Test("rad-dino").run(main)
