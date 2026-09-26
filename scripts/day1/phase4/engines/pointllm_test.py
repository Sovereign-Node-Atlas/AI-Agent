#!/usr/bin/env python3
"""PointLLM 7B v1.2: one question about a synthetic point cloud (V8). The call sequence mirrors
pointllm/eval/PointLLM_chat.py (README VERIFIED as the CLI; the class and helper names below are UNVERIFIED and any
ImportError/AttributeError is a recorded fail -> V8 deferred, as Section 17 step 4 allows). The chat script itself
loops on input() and is never used: nothing here waits for a terminal. Writes pointllm_answer.txt."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "RunsenXu/PointLLM_7B_v1.2"


def _synthetic_cloud(n: int = 8192) -> object:
    import torch

    g = torch.Generator().manual_seed(0)
    v = torch.randn(n, 3, generator=g)
    xyz = v / v.norm(dim=1, keepdim=True)                # a unit sphere
    xyz[: n // 4, 2] = xyz[: n // 4, 2] * 0.2 - 1.2      # plus a flat base under it
    rgb = torch.stack([(xyz[:, 0] + 1) / 2, (xyz[:, 1] + 1) / 2, torch.full((n,), 0.5)], dim=1)
    return torch.cat([xyz, rgb], dim=1)


def main(t: Test) -> None:
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    sys.path.insert(0, str(Path(t.args.src) / "PointLLM"))
    from pointllm.conversation import conv_templates
    from pointllm.model import PointLLMLlamaForCausalLM
    from pointllm.model.utils import KeywordsStoppingCriteria

    path = snapshot_download(REPO)
    tokenizer = AutoTokenizer.from_pretrained(path)
    model = PointLLMLlamaForCausalLM.from_pretrained(path, low_cpu_mem_usage=False, use_cache=True,
                                                    torch_dtype=torch.float16).to(t.device).eval()
    model.initialize_tokenizer_point_backbone_config_wo_embedding(tokenizer)
    t.loaded()
    cfg = model.get_model().point_backbone_config
    point_token_len = cfg["point_token_len"]
    patch = cfg["default_point_patch_token"]
    if cfg.get("mm_use_point_start_end", False):
        vis = cfg["default_point_start_token"] + patch * point_token_len + cfg["default_point_end_token"]
    else:
        vis = patch * point_token_len
    conv = conv_templates["vicuna_v1_1"].copy()
    conv.append_message(conv.roles[0], vis + "\n" + "What is this object? Answer in one sentence.")
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()
    input_ids = torch.as_tensor(tokenizer([prompt]).input_ids).to(t.device)
    stop_str = conv.sep if getattr(conv, "sep_style", None) is None else (conv.sep2 or conv.sep)
    stopping = KeywordsStoppingCriteria([stop_str], tokenizer, input_ids)
    cloud = _synthetic_cloud().unsqueeze(0).to(t.device, torch.float16)
    with torch.inference_mode():
        out_ids = model.generate(input_ids, point_clouds=cloud, do_sample=False, max_new_tokens=64,
                                 stopping_criteria=[stopping])
    text = tokenizer.decode(out_ids[0, input_ids.shape[1]:], skip_special_tokens=True).strip()
    if text.endswith(stop_str):
        text = text[: -len(stop_str)].strip()
    if not text:
        t.fail("empty answer")
    out = t.out / "pointllm_answer.txt"
    out.write_text(text + "\n", encoding="utf-8")
    t.done(out, notes=f"answer: {text[:160]!r}; float16")


if __name__ == "__main__":
    Test("pointllm").run(main)
