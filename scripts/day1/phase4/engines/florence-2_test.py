#!/usr/bin/env python3
"""Florence-2-large caption/OCR/detection (rocm-containers.md §6.5, VERIFIED from transformers florence2.md; the
-large repo id is UNVERIFIED-by-snippet). No trust_remote_code (conflict 15). Writes florence2.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, synthetic_image

REPO = "florence-community/Florence-2-large"


def main(t: Test) -> None:
    import torch
    from PIL import Image
    from transformers import AutoProcessor, Florence2ForConditionalGeneration

    try:
        model = Florence2ForConditionalGeneration.from_pretrained(REPO, dtype=torch.bfloat16)
    except TypeError:
        model = Florence2ForConditionalGeneration.from_pretrained(REPO, torch_dtype=torch.bfloat16)
    model = model.to(t.device).eval()
    proc = AutoProcessor.from_pretrained(REPO)
    t.loaded()
    image = Image.open(synthetic_image(t.out / "input.png", "scene")).convert("RGB")
    results: dict[str, object] = {}
    for task in ("<CAPTION>", "<OCR>", "<OD>"):
        inputs = proc(text=task, images=image, return_tensors="pt").to(t.device, torch.bfloat16)
        with torch.inference_mode():
            ids = model.generate(**inputs, max_new_tokens=512)
        text = proc.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = proc.post_process_generation(text, task=task, image_size=image.size)
        results[task] = parsed
        t.note(f"{task}: {str(parsed)[:120]}")
    out = t.out / "florence2.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    caption = str(results.get("<CAPTION>", ""))
    if not caption.strip():
        t.fail("empty caption")
    t.done(out)


if __name__ == "__main__":
    Test("florence-2").run(main)
