#!/usr/bin/env python3
"""TRELLIS image-to-3D attempt (rocm-containers.md §6.5, UNVERIFIED end-to-end on Linux gfx1151). The upstream
package is imported from the clone; the kroqueta-s shims (pure-torch spconv/flash_attn/nvdiffrast/kaolin
replacements) are put on sys.path when their package directories are found. SPCONV_ALGO=native and ATTN_BACKEND=sdpa
per the README. Writes trellis.glb (or trellis_outputs.txt when no mesh export is possible)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "microsoft/TRELLIS-image-large"
SHIM_PKGS = ("spconv", "flash_attn", "nvdiffrast", "kaolin")


def _find_shims(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    for pkg in SHIM_PKGS:
        for init in root.rglob(f"{pkg}/__init__.py"):
            parent = init.parent.parent
            if parent not in found:
                found.append(parent)
    return found


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
    shims = _find_shims(src / "trellis-strix-halo")
    for p in shims:
        sys.path.insert(0, str(p))
    t.note(f"shim paths: {[str(p) for p in shims] or 'none found (UNVERIFIED layout of trellis-strix-halo)'}")
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
