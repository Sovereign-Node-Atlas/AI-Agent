#!/usr/bin/env python3
"""UI-TARS-1.5-7B one GUI-grounding turn on a synthetic screenshot (rocm-containers.md §6.5: repo VERIFIED, loading
code UNVERIFIED — Qwen2.5-VL convention, SDPA attention on AOTriton). The dtype keyword spelling is decided once by
p4common.load_kwargs from the transformers version (no try/except retry: a 16 GB load runs exactly once under the
load watchdog). Writes ui_tars.txt."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test, load_kwargs, synthetic_image

REPO = "ByteDance-Seed/UI-TARS-1.5-7B"


def main(t: Test) -> None:
    import torch
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor

    proc = AutoProcessor.from_pretrained(REPO, min_pixels=256 * 28 * 28, max_pixels=1280 * 28 * 28)
    kw = load_kwargs(AutoModelForImageTextToText, torch.bfloat16, disable_mmap=False)
    t.note(f"from_pretrained kwargs: {sorted(kw)}")
    model = AutoModelForImageTextToText.from_pretrained(REPO, attn_implementation="sdpa", **kw)
    model = model.to(t.device).eval()
    t.loaded()
    shot = Image.open(synthetic_image(t.out / "screenshot.png", "gui")).convert("RGB")
    msgs = [{"role": "user", "content": [
        {"type": "image"},
        {"type": "text",
         "text": "You are a GUI agent. Describe the next click to open Settings. Output Thought and Action."},
    ]}]
    prompt = proc.apply_chat_template(msgs, add_generation_prompt=True)
    inputs = proc(text=prompt, images=[shot], return_tensors="pt").to(t.device)
    with torch.inference_mode():
        ids = model.generate(**inputs, max_new_tokens=128, do_sample=False)
    text = proc.batch_decode(ids[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
    if not text.strip():
        t.fail("empty generation")
    out = t.out / "ui_tars.txt"
    out.write_text(text, encoding="utf-8")
    t.done(out, notes=f"output: {text[:160]!r}")


if __name__ == "__main__":
    Test("ui-tars").run(main)
