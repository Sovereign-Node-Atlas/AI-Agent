#!/usr/bin/env python3
"""TRELLIS image-to-3D attempt (rocm-containers.md §6.5, UNVERIFIED end-to-end on Linux gfx1151). The upstream
package is imported from the clone after the kroqueta-s shims are installed: trellis-strix-halo ships ONE module,
runners/trellis/shims.py (plus runners/trellis/raster.py for nvdiffrast), whose install() puts pure-torch replacements
for spconv, flash_attn, nvdiffrast, kaolin (and stubs for open3d, kaolin.utils.testing) into sys.modules before
`import trellis` (VERIFIED: the repo README table "What is replaced" and the shims.py docstring). SPCONV_ALGO=native and
ATTN_BACKEND=sdpa per the README. Writes trellis.glb (or trellis_outputs.txt when no mesh export is possible)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "microsoft/TRELLIS-image-large"
SHIM_MODULES = ("spconv", "flash_attn", "nvdiffrast", "kaolin", "open3d")


def _install_shims(src: Path, t: Test) -> None:
    shim_dir = src / "trellis-strix-halo" / "runners" / "trellis"
    if not (shim_dir / "shims.py").is_file():
        raise FileNotFoundError(f"{shim_dir}/shims.py not found (kroqueta-s/trellis-strix-halo layout changed?)")
    sys.path.insert(0, str(shim_dir))
    import shims  # type: ignore[import-not-found]  # the clone's module, not a package

    shims.install()
    registered = [m for m in SHIM_MODULES if m in sys.modules]
    t.note(f"kroqueta-s shims.install() registered: {registered}")


def _rgba_object(path: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([120, 120, 392, 392], fill=(200, 60, 60, 255))
    d.rectangle([200, 300, 312, 460], fill=(60, 60, 200, 255))
    img.save(path)
    return path


def main(t: Test) -> None:
    os.environ["SPCONV_ALGO"] = "native"
    os.environ["ATTN_BACKEND"] = "sdpa"
    src = Path(t.args.src)
    _install_shims(src, t)
    sys.path.insert(0, str(src / "TRELLIS"))
    from PIL import Image
    from trellis.pipelines import TrellisImageTo3DPipeline

    pipe = TrellisImageTo3DPipeline.from_pretrained(REPO)
    pipe.cuda()
    t.loaded()
    image = Image.open(_rgba_object(t.out / "object.png"))
    outputs = pipe.run(image, seed=1)
    mesh = outputs.get("mesh", [None])[0] if isinstance(outputs, dict) else None
    out = t.out / "trellis.glb"
    if mesh is not None and hasattr(mesh, "to_trimesh"):
        mesh.to_trimesh().export(str(out))
        t.done(out, notes="mesh exported as glb")
    out = t.out / "trellis_outputs.txt"
    out.write_text(str(list(outputs.keys()) if isinstance(outputs, dict) else type(outputs)), encoding="utf-8")
    t.done(out, notes="pipeline ran; no to_trimesh on the mesh output, keys recorded")


if __name__ == "__main__":
    Test("trellis").run(main)
