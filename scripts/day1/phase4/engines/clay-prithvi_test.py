#!/usr/bin/env python3
"""V9: Prithvi-EO-2.0 (TerraTorch backbone registry, UNVERIFIED call) on a synthetic HLS chip, then Clay v1.5
(VERIFIED quickstart) on a synthetic Sentinel-2 chip. Pass when either encoder runs on the device; the notes record
both outcomes. Writes geospatial_embeddings.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

PRITHVI = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL"
CLAY = "made-with-clay/Clay"


def run_prithvi(t: Test) -> dict[str, object]:
    import torch
    from terratorch.registry import BACKBONE_REGISTRY

    bb = BACKBONE_REGISTRY.build("prithvi_eo_v2_300_tl", pretrained=True).to(t.device).eval()
    x = torch.randn(1, 6, 1, 224, 224, device=t.device)     # [B, bands (6 HLS), T, H, W]
    with torch.no_grad():
        feats = bb(x)
    shapes = [list(f.shape) for f in feats] if isinstance(feats, list | tuple) else [list(feats.shape)]
    last = feats[-1] if isinstance(feats, list | tuple) else feats
    if not torch.isfinite(last.float()).all():
        raise AssertionError("non-finite features")
    return {"ok": True, "shapes": shapes}


def run_clay(t: Test) -> dict[str, object]:
    import torch
    import yaml
    from claymodel.module import ClayMAEModule
    from huggingface_hub import snapshot_download

    ckpt = Path(snapshot_download(CLAY, allow_patterns=["v1.5/clay-v1.5.ckpt"])) / "v1.5" / "clay-v1.5.ckpt"
    if not ckpt.is_file():
        raise FileNotFoundError(f"{ckpt} not in the offline snapshot")
    model = ClayMAEModule.load_from_checkpoint(str(ckpt)).to(t.device).eval()
    meta_path = Path(t.args.src) / "clay-model" / "configs" / "metadata.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))["sentinel-2-l2a"]
    waves = torch.tensor([[meta["bands"]["wavelength"][b] * 1000 for b in meta["band_order"]]],
                         dtype=torch.float32, device=t.device)
    chips = torch.randn(1, len(meta["band_order"]), 256, 256, device=t.device)
    times = torch.zeros(1, 4, device=t.device)
    with torch.no_grad():
        emb = model.encoder(chips, times, waves)
    emb = emb[0] if isinstance(emb, list | tuple) else emb
    return {"ok": True, "shape": list(emb.shape)}


def main(t: Test) -> None:
    results: dict[str, dict[str, object]] = {}
    for name, fn in (("prithvi", run_prithvi), ("clay", run_clay)):
        try:
            results[name] = fn(t)
            t.note(f"{name}: ok {results[name]}")
        except BaseException as exc:
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            t.note(f"{name}: FAILED {results[name]['error']}")
        if name == "prithvi" and results[name].get("ok"):
            t.loaded()
    if not t.t_loaded and results.get("clay", {}).get("ok"):
        t.loaded()
    out = t.out / "geospatial_embeddings.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if not (results["prithvi"].get("ok") or results["clay"].get("ok")):
        t.fail("neither Prithvi nor Clay ran")
    p_ok = "pass" if results["prithvi"].get("ok") else "fail"
    c_ok = "pass" if results["clay"].get("ok") else "fail"
    t.done(out, notes=f"prithvi={p_ok} clay={c_ok}")


if __name__ == "__main__":
    Test("clay-prithvi").run(main)
