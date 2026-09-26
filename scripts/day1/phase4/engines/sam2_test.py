#!/usr/bin/env python3
"""SAM 2 image segmentation from one point prompt (rocm-containers.md §6.5: install VERIFIED, from_pretrained is the
README example; eager mode, bf16 autocast). The repo id comes from the build step (2.1 or the 2.0 fallback).
Writes sam2_mask.png."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, synthetic_image


def main(t: Test) -> None:
    import numpy as np
    import torch
    from PIL import Image
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    repo = t.setting("repo", "facebook/sam2.1-hiera-large")
    pred = SAM2ImagePredictor.from_pretrained(repo, device=t.device)
    t.loaded(f"repo {repo}")
    img = np.array(Image.open(synthetic_image(t.out / "input.png", "scene")).convert("RGB"))
    h, w = img.shape[:2]
    with torch.inference_mode(), torch.autocast(t.device, dtype=torch.bfloat16):
        pred.set_image(img)
        # The blue disc in the synthetic scene is centred around (460, 220) of a 640x480 picture.
        point = np.array([[int(w * 0.72), int(h * 0.46)]])
        masks, scores, _ = pred.predict(point_coords=point, point_labels=np.array([1]))
    if masks.shape[0] < 1 or masks.shape[-2:] != (h, w):
        t.fail(f"unexpected masks shape {masks.shape}")
    best = int(np.argmax(scores))
    mask = (masks[best].astype(np.uint8) * 255)
    area = float(mask.mean() / 255.0)
    out = t.out / "sam2_mask.png"
    Image.fromarray(mask).save(out)
    if area <= 0.0:
        t.fail("empty mask")
    t.done(out, notes=f"{masks.shape[0]} masks, best score {float(scores[best]):.3f}, area {area:.3f}; "
                      "CUDA extension disabled")


if __name__ == "__main__":
    Test("sam2").run(main)
