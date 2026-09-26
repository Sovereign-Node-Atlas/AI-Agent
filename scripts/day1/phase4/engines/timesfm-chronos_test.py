#!/usr/bin/env python3
"""Chronos-Bolt forecast on a synthetic series (rocm-containers.md §6.5: package/ids VERIFIED, predict_quantiles
signature UNVERIFIED-by-snippet — a TypeError falls back to .predict()). Writes chronos_forecast.csv."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "amazon/chronos-bolt-base"


def main(t: Test) -> None:
    import torch
    from chronos import BaseChronosPipeline

    pipe = BaseChronosPipeline.from_pretrained(REPO, device_map=t.device, torch_dtype=torch.bfloat16)
    t.loaded()
    ctx = torch.sin(torch.linspace(0, 40, 400)) + 0.1 * torch.randn(400, generator=torch.Generator().manual_seed(0))
    horizon = 24
    try:
        quantiles, mean = pipe.predict_quantiles(context=ctx, prediction_length=horizon,
                                                 quantile_levels=[0.1, 0.5, 0.9])
        q = quantiles[0].float().cpu()
        m = mean[0].float().cpu()
    except (TypeError, AttributeError) as exc:
        t.note(f"predict_quantiles unavailable ({exc}); using predict()")
        samples = pipe.predict(context=ctx, prediction_length=horizon)
        s = samples[0].float().cpu()
        q = torch.stack([s.quantile(0.1, dim=0), s.quantile(0.5, dim=0), s.quantile(0.9, dim=0)], dim=-1)
        m = s.mean(dim=0)
    if m.shape[0] != horizon or not torch.isfinite(m).all():
        t.fail(f"forecast shape/values wrong: {tuple(m.shape)}")
    out = t.out / "chronos_forecast.csv"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("step,q10,q50,q90,mean\n")
        for i in range(horizon):
            fh.write(f"{i},{q[i, 0]:.4f},{q[i, 1]:.4f},{q[i, 2]:.4f},{m[i]:.4f}\n")
    t.done(out, notes=f"horizon {horizon}, quantiles shape {tuple(q.shape)}")


if __name__ == "__main__":
    Test("timesfm-chronos").run(main)
