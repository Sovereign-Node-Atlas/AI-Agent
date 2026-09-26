#!/usr/bin/env python3
"""OpenVLA-7b one action prediction (rocm-containers.md §6.5, VERIFIED from the README with attn_implementation
changed to sdpa). trust_remote_code=True is what the repo requires. Writes openvla_action.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, synthetic_image

REPO = "openvla/openvla-7b"


def main(t: Test) -> None:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForVision2Seq, AutoProcessor

    proc = AutoProcessor.from_pretrained(REPO, trust_remote_code=True)
    vla = AutoModelForVision2Seq.from_pretrained(
        REPO, attn_implementation="sdpa", torch_dtype=torch.bfloat16, low_cpu_mem_usage=True, trust_remote_code=True,
    ).to(t.device).eval()
    t.loaded()
    img = Image.open(synthetic_image(t.out / "robot_view.png", "scene", (256, 256))).convert("RGB")
    prompt = "In: What action should the robot take to pick up the cup?\nOut:"
    inputs = proc(prompt, img).to(t.device, dtype=torch.bfloat16)
    with torch.inference_mode():
        action = vla.predict_action(**inputs, unnorm_key="bridge_orig", do_sample=False)
    arr = np.asarray(action, dtype=np.float64).reshape(-1)
    if arr.shape[0] != 7 or not np.isfinite(arr).all():
        t.fail(f"expected a 7-dof action, got shape {arr.shape}")
    out = t.out / "openvla_action.json"
    out.write_text(json.dumps({"unnorm_key": "bridge_orig", "action": arr.tolist()}, indent=2), encoding="utf-8")
    t.done(out, notes=f"action {np.round(arr, 4).tolist()}")


if __name__ == "__main__":
    Test("openvla").run(main)
