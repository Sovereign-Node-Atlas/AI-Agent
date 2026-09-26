#!/usr/bin/env python3
"""V11 self-test, run INSIDE the atlas/rocm-base container by verify/v11-rocm-selftest.sh (ATLAS_FRAMEWORK_REVIEW.md
Section 17 Phase 4 step 1; Section 21 V11: "rocminfo reports gfx1151, and the PyTorch wheel passes tensor, matmul and
diffusion self-tests"). Research: rocm-containers.md §2.3 and §6.3 (each API call is standard torch/diffusers; the
script as a whole is UNVERIFIED on this hardware until it runs).

Steps (all must pass; each is timed and reported):
  1. rocminfo_gfx1151   rocminfo on PATH, else $(rocm-sdk path --root)/bin/rocminfo, else torch's gcnArchName
                        (the binary is a wheel-install detail, research §1.2; the arch is the proof).
  2. torch_device       torch.cuda.is_available(), device name/arch, HSA_OVERRIDE_GFX_VERSION unset (#6034),
                        `pip show amd-torch-device-gfx1151` succeeds (TheRock #7839: without it every kernel launch
                        fails with hipErrorInvalidImage).
  3. matmul_bf16        4096x4096 bf16 matmul on device vs the CPU result (rel. max error < 2e-2) + TFLOPS.
  4. alloc_4GB_gtt      a 4 GB device tensor filled and read back (GTT-backed device memory beyond the "15.5 GB only"
                        symptom, ROCm/ROCm #5444 UNVERIFIED-by-snippet).
  5. diffusion_2step    a randomly initialised UNet2DModel + DDPMScheduler for two steps in bf16 (no download, no
                        gated weights; conv, attention and GroupNorm kernels on gfx1151).

Output: one JSON document to $V11_OUT (default /srv/atlas/engines/v11.json) and a one-line summary on stdout as the
last line ("V11 ok ..." or "V11 FAIL ..."). Exit 0 only when every step passed. The caller runs it under `timeout`:
a hang (the kernel-7.0 symptom of #6530/#6182) must count as a fail, not wait forever.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

results: dict[str, dict[str, Any]] = {}


def step(name: str, fn: Callable[[], Any]) -> None:
    t = time.time()
    try:
        out = fn()
        results[name] = {"ok": True, "detail": out, "s": round(time.time() - t, 2)}
    except Exception as exc:
        results[name] = {"ok": False, "detail": repr(exc), "s": round(time.time() - t, 2)}
    tag = "PASS" if results[name]["ok"] else "FAIL"
    print(f"[{tag}] {name} ({results[name]['s']} s): {results[name]['detail']}", file=sys.stderr, flush=True)


def rocminfo() -> str:
    cands: list[str | None] = [shutil.which("rocminfo")]
    try:
        root = subprocess.check_output(["rocm-sdk", "path", "--root"], text=True, timeout=60).strip()
        cands.append(os.path.join(root, "bin", "rocminfo"))
    except Exception:
        pass
    for c in cands:
        if c and os.path.exists(c):
            out = subprocess.run([c], capture_output=True, text=True, timeout=120, check=False).stdout
            if "gfx1151" not in out:
                raise AssertionError(f"{c} ran but did not list gfx1151")
            return f"{c}: gfx1151 listed"
    import torch

    arch = torch.cuda.get_device_properties(0).gcnArchName
    if not arch.startswith("gfx1151"):
        raise AssertionError(f"gcnArchName={arch}")
    return f"no rocminfo binary; torch gcnArchName={arch}"


def torch_gpu() -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        raise AssertionError("torch.cuda.is_available() is False (is /dev/kfd and /dev/dri passed, gids added?)")
    if os.environ.get("HSA_OVERRIDE_GFX_VERSION"):
        raise AssertionError("HSA_OVERRIDE_GFX_VERSION must be unset for native gfx1151 kernels (ROCm/ROCm #6034)")
    p = torch.cuda.get_device_properties(0)
    arch = getattr(p, "gcnArchName", "?")
    if not str(arch).startswith("gfx1151"):
        raise AssertionError(f"device arch is {arch}, not gfx1151")
    subprocess.check_call([sys.executable, "-m", "pip", "show", "-q", "amd-torch-device-gfx1151"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "torch": torch.__version__,
        "hip": torch.version.hip,
        "name": p.name,
        "arch": arch,
        "total_mem_GB": round(p.total_memory / 2**30, 1),
        "arch_list": torch.cuda.get_arch_list(),
    }


def matmul() -> dict[str, float]:
    import torch

    torch.manual_seed(0)
    a = torch.randn(4096, 4096, dtype=torch.bfloat16)
    b = torch.randn(4096, 4096, dtype=torch.bfloat16)
    ref = a.float() @ b.float()
    ad, bd = a.cuda(), b.cuda()
    got = (ad @ bd).float().cpu()
    torch.cuda.synchronize()
    err = (got - ref).abs().max().item() / ref.abs().max().item()
    if err >= 2e-2:
        raise AssertionError(f"relative max error {err}")
    torch.cuda.synchronize()
    t = time.time()
    for _ in range(10):
        _ = ad @ bd
    torch.cuda.synchronize()
    dt = time.time() - t
    return {"rel_err": round(err, 5), "tflops_bf16": round(10 * 2 * 4096**3 / dt / 1e12, 1)}


def big_alloc() -> str:
    import torch

    x = torch.empty((4 * 2**30) // 2, dtype=torch.bfloat16, device="cuda")
    x.fill_(1.0)
    torch.cuda.synchronize()
    s = x[:1_000_000].float().sum().item()
    del x
    torch.cuda.empty_cache()
    if s != 1_000_000.0:
        raise AssertionError(f"read back {s}, expected 1000000.0")
    return "4 GB device tensor filled and read back"


def diffusion_2_steps() -> dict[str, Any]:
    import torch
    from diffusers import DDPMScheduler, UNet2DModel

    torch.manual_seed(0)
    unet = UNet2DModel(
        sample_size=32, in_channels=3, out_channels=3, layers_per_block=1, block_out_channels=(32, 64),
        down_block_types=("DownBlock2D", "AttnDownBlock2D"), up_block_types=("AttnUpBlock2D", "UpBlock2D"),
    ).to("cuda", dtype=torch.bfloat16).eval()
    sch = DDPMScheduler(num_train_timesteps=1000)
    sch.set_timesteps(2)
    x = torch.randn(1, 3, 32, 32, device="cuda", dtype=torch.bfloat16)
    with torch.no_grad():
        for t in sch.timesteps:
            eps = unet(x, t).sample
            x = sch.step(eps.float(), t, x.float()).prev_sample.to(torch.bfloat16)
    torch.cuda.synchronize()
    if not torch.isfinite(x.float()).all():
        raise AssertionError("NaN/Inf in diffusion output")
    return {"out_shape": list(x.shape), "peak_alloc_MB": round(torch.cuda.max_memory_allocated() / 2**20)}


def main() -> int:
    step("rocminfo_gfx1151", rocminfo)
    step("torch_device", torch_gpu)
    step("matmul_bf16", matmul)
    step("alloc_4GB_gtt", big_alloc)
    step("diffusion_2step_bf16", diffusion_2_steps)
    ok = all(v["ok"] for v in results.values())
    out = os.environ.get("V11_OUT", "/srv/atlas/engines/v11.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"ok": ok, "steps": results, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, fh, indent=2)
    except OSError as exc:
        print(f"[WARN] could not write {out}: {exc}", file=sys.stderr)
    td = results["torch_device"]["detail"] if results["torch_device"]["ok"] else {}
    mm = results["matmul_bf16"]["detail"] if results["matmul_bf16"]["ok"] else {}
    df = results["diffusion_2step_bf16"]["detail"] if results["diffusion_2step_bf16"]["ok"] else {}
    if ok:
        print(
            f"V11 ok: {results['rocminfo_gfx1151']['detail']}; torch={td.get('torch')} hip={td.get('hip')} "
            f"name='{td.get('name')}' arch={td.get('arch')} mem={td.get('total_mem_GB')}GB; "
            f"matmul rel_err={mm.get('rel_err')} {mm.get('tflops_bf16')} TFLOPS bf16; 4GB alloc ok; "
            f"diffusion 2-step ok peak={df.get('peak_alloc_MB')}MB"
        )
        return 0
    failed = [f"{k}: {v['detail']}" for k, v in results.items() if not v["ok"]]
    print("V11 FAIL: " + " | ".join(failed))
    return 1


if __name__ == "__main__":
    sys.exit(main())
