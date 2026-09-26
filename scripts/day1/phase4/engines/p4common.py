#!/usr/bin/env python3
"""Shared helper for the Phase 4 engine tests and pulls; runs INSIDE the ROCm container (never on the host).

Two roles:

1. Library for phase4/engines/<key>_test.py (imported from the same directory):
       from p4common import Test
       t = Test("flux1-dev")            # parses --out/--load-timeout, arms the load watchdog, patches mmap away
       ...load...; t.loaded()           # disarms the watchdog, records the load time
       ...generate...; t.done(output_path, notes=...)   # prints "P4RESULT {json}" and exits 0
   Any exception is caught by `t.run(fn)` and reported as ok=false with the traceback in `notes`. The result line is
       P4RESULT {"ok": bool, "loaded": bool, "seconds": float, "load_seconds": float, "output": str|null,
                 "notes": str, "peak_alloc_mb": int|null, "device": str|null}
   which phase4/lib-engine.sh merges into $ATLAS_STATE/phase4/<key>.json (the footprint itself is measured on the host
   from the GTT counter while this process runs; peak_alloc_mb is torch's own view, a cross-check).

2. CLI for the pull step:  python3 p4common.py pull REPO [--allow PATTERN ...] [--fallback REPO2] [--manifest DIR]
   Re-reads https://huggingface.co/api/models/<repo> (gated, cardData.license, siblings[].rfilename/lfs) at pull
   time and writes <manifest-dir>/<repo with / as __>.json (research §0: never trust the snippet sizes), then
   huggingface_hub.snapshot_download(repo, allow_patterns=...). Exit codes: 0 ok; 3 gated/forbidden (the licence URL is
   printed on stderr as the last line: "LICENCE_URL https://huggingface.co/<repo>"); 4 repo not found (after trying
   --fallback); 1 anything else. HF_TOKEN comes from the environment (the driver passes the secrets file as
   --env-file for gated repos); it is never printed.

Kernel 7.0 mmap regression (ROCm/legacy-rocm-build #6530, research §0 item 5): safetensors loading through mmap runs
at ~1.5 MB/s on this kernel. `Test` therefore monkeypatches safetensors.torch.load_file to read-then-load
(research §6.4, UNVERIFIED as a fix for ROCm 10.0 but harmless) and arms a watchdog: a model load longer than
--load-timeout seconds is killed and reported as a fail with the issue number, never waited for.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESULT_TAG = "P4RESULT "
HANG_NOTE = (
    "model load exceeded the load watchdog: on kernel 7.0 + gfx1151 this is the mmap/hang symptom of "
    "ROCm/legacy-rocm-build #6530 (also ROCm/ROCm #6182); the host kernel may be the cause, not the container"
)


def _log(msg: str) -> None:
    print(f"[p4] {msg}", file=sys.stderr, flush=True)


def patch_safetensors_no_mmap() -> bool:
    """Replace safetensors.torch.load_file with a read-then-load version (research §6.4). Returns True when applied."""
    try:
        import safetensors.torch as st
    except Exception as exc:
        _log(f"safetensors not importable ({exc!r}); no mmap patch")
        return False

    def load_file_nommap(filename: str | os.PathLike[str], device: str | int = "cpu") -> dict[str, Any]:
        with open(filename, "rb") as fh:
            sd = st.load(fh.read())
        if device in ("cpu", None):
            return sd
        return {k: v.to(device) for k, v in sd.items()}

    st.load_file = load_file_nommap  # type: ignore[assignment]
    return True


class Test:
    """One engine test: argument parsing, load watchdog, result line."""

    def __init__(self, key: str, *, no_mmap: bool = True) -> None:
        self.key = key
        self.t0 = time.time()
        self.t_loaded: float | None = None
        self._watchdog: threading.Timer | None = None
        parser = argparse.ArgumentParser(description=f"Phase 4 engine test: {key}")
        parser.add_argument("--out", required=True,
                            help="sample output directory (host: /srv/atlas/workspace/phase4-samples/<key>)")
        parser.add_argument("--load-timeout", type=float, default=float(os.environ.get("P4_LOAD_TIMEOUT_S", "900")))
        parser.add_argument("--src", default="/srv/atlas/engines/src", help="git checkouts directory")
        parser.add_argument("--dl", default="/srv/atlas/engines/dl", help="non-HF downloads directory")
        parser.add_argument("extra", nargs="*", help="engine-specific key=value settings")
        self.args = parser.parse_args()
        self.out = Path(self.args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.settings: dict[str, str] = {}
        for kv in self.args.extra:
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.settings[k] = v
        self.notes: list[str] = []
        if no_mmap and patch_safetensors_no_mmap():
            self.notes.append("safetensors mmap disabled (#6530)")
        self.device = "cuda"
        self._arm(self.args.load_timeout)

    # --- watchdog ---------------------------------------------------------------------------------------------------
    def _arm(self, seconds: float) -> None:
        def fire() -> None:
            self._emit(ok=False, output=None, notes=f"{HANG_NOTE} ({seconds:.0f} s)")
            os._exit(124)

        self._watchdog = threading.Timer(seconds, fire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def loaded(self, note: str | None = None) -> None:
        """Call once the weights are on the device; disarms the load watchdog."""
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        self.t_loaded = time.time()
        _log(f"{self.key}: loaded in {self.t_loaded - self.t0:.1f} s")
        if note:
            self.notes.append(note)

    # --- helpers ----------------------------------------------------------------------------------------------------
    def setting(self, key: str, default: str) -> str:
        return self.settings.get(key, default)

    def note(self, msg: str) -> None:
        _log(msg)
        self.notes.append(msg)

    def peak_alloc_mb(self) -> int | None:
        try:
            import torch

            if torch.cuda.is_available():
                return int(torch.cuda.max_memory_allocated() / 2**20)
        except Exception:
            return None
        return None

    def device_name(self) -> str | None:
        try:
            import torch

            if torch.cuda.is_available():
                p = torch.cuda.get_device_properties(0)
                return f"{p.name} {getattr(p, 'gcnArchName', '?')}"
        except Exception:
            return None
        return None

    def _emit(self, *, ok: bool, output: str | None, notes: str) -> None:
        now = time.time()
        rec = {
            "ok": ok,
            "loaded": self.t_loaded is not None,
            "seconds": round(now - self.t0, 1),
            "load_seconds": round(self.t_loaded - self.t0, 1) if self.t_loaded else None,
            "output": output,
            "notes": notes,
            "peak_alloc_mb": self.peak_alloc_mb(),
            "device": self.device_name(),
        }
        print(RESULT_TAG + json.dumps(rec), flush=True)

    def done(self, output: str | os.PathLike[str] | None, notes: str = "") -> None:
        out = str(output) if output is not None else None
        if out is not None and not os.path.exists(out):
            self.fail(f"sample output {out} was not written")
        all_notes = "; ".join([*self.notes, notes] if notes else self.notes)
        self._emit(ok=True, output=out, notes=all_notes)
        sys.exit(0)

    def fail(self, msg: str) -> None:
        all_notes = "; ".join([*self.notes, msg])
        self._emit(ok=False, output=None, notes=all_notes)
        sys.exit(1)

    def run(self, fn: Callable[[Test], None]) -> None:
        """Run fn(self); any exception becomes an ok=false result with the last traceback lines."""
        try:
            fn(self)
        except SystemExit:
            raise
        except BaseException as exc:
            tb = traceback.format_exc().strip().splitlines()
            self.fail(f"{type(exc).__name__}: {exc} | " + " | ".join(tb[-6:]))


# --- synthetic inputs (no network, no gated data) --------------------------------------------------------------------
def synthetic_image(path: Path, kind: str = "scene", size: tuple[int, int] = (640, 480)) -> Path:
    """A deterministic test picture: coloured shapes and text ("scene"), a fake GUI ("gui"), or a grey X-ray-like
    field ("xray"). Enough to exercise captioning, OCR, detection, segmentation and a VLA prompt."""
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size, (240, 240, 235))
    d = ImageDraw.Draw(img)
    if kind == "gui":
        d.rectangle([0, 0, w, 40], fill=(60, 60, 70))
        d.text((12, 12), "File   Edit   View   Settings   Help", fill=(255, 255, 255))
        d.rectangle([40, 90, 260, 140], fill=(70, 130, 220))
        d.text((60, 108), "Open Settings", fill=(255, 255, 255))
        d.rectangle([300, 90, 520, 140], fill=(200, 60, 60))
        d.text((320, 108), "Cancel", fill=(255, 255, 255))
        d.text((40, 200), "Welcome to the ATLAS test window", fill=(20, 20, 20))
    elif kind == "xray":
        img = Image.new("L", size, 30)
        d = ImageDraw.Draw(img)
        d.ellipse([w * 0.2, h * 0.1, w * 0.8, h * 0.95], fill=110)
        d.ellipse([w * 0.3, h * 0.25, w * 0.48, h * 0.8], fill=60)
        d.ellipse([w * 0.52, h * 0.25, w * 0.7, h * 0.8], fill=60)
        d.rectangle([w * 0.48, h * 0.1, w * 0.52, h * 0.9], fill=170)
        img = img.convert("RGB")
    else:
        d.rectangle([60, 260, 300, 440], fill=(180, 40, 40))
        d.ellipse([360, 120, 560, 320], fill=(40, 90, 200))
        d.polygon([(100, 100), (200, 40), (300, 100)], fill=(40, 160, 70))
        d.text((60, 460), "ATLAS SAMPLE 2026", fill=(10, 10, 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# --- pull CLI -------------------------------------------------------------------------------------------------------
def _api_json(url: str, token: str | None) -> tuple[int, Any]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "atlas-day1-phase4"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None


def pull(repo: str, allow: list[str] | None, fallback: str | None, manifest_dir: Path) -> int:
    token = os.environ.get("HF_TOKEN") or None
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    tried = [repo] + ([fallback] if fallback else [])
    chosen: str | None = None
    meta: Any = None
    for cand in tried:
        code, meta = _api_json(f"{base}/api/models/{cand}", token)
        _log(f"api/models/{cand}: HTTP {code}")
        if code == 200:
            chosen = cand
            break
        if code in (401, 403):
            print(f"LICENCE_URL https://huggingface.co/{cand}", file=sys.stderr, flush=True)
            _log(f"{cand} is gated or private for this token: accept the licence at https://huggingface.co/{cand} "
                 "with the account that owns HF_TOKEN in /etc/atlas/secrets/hf-token.env")
            return 3
        if code == 404:
            continue
        _log(f"unexpected HTTP {code} from the hub API for {cand} (proxy? allowlist?)")
        return 1
    if chosen is None:
        _log(f"none of {tried} exists on the hub (HTTP 404)")
        return 4
    siblings = meta.get("siblings") or []
    files = []
    total = 0
    for s in siblings:
        name = s.get("rfilename")
        if not name:
            continue
        if allow and not any(fnmatch.fnmatch(name, pat) for pat in allow):
            continue
        lfs = s.get("lfs") or {}
        size = int(lfs.get("size") or s.get("size") or 0)
        total += size
        files.append({"name": name, "bytes": size or None, "sha256": lfs.get("oid")})
    manifest = {
        "repo": chosen,
        "requested": repo,
        "gated": meta.get("gated"),
        "license": (meta.get("cardData") or {}).get("license"),
        "sha": meta.get("sha"),
        "allow_patterns": allow,
        "files": files,
        "total_bytes": total,
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    mpath = manifest_dir / (chosen.replace("/", "__") + ".json")
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _log(f"{chosen}: gated={manifest['gated']} license={manifest['license']} files={len(files)} "
         f"bytes={total / 1e9:.1f} GB -> {mpath}")
    if meta.get("gated") and not token:
        print(f"LICENCE_URL https://huggingface.co/{chosen}", file=sys.stderr, flush=True)
        _log(f"{chosen} is gated and no HF_TOKEN is present")
        return 3

    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    try:
        path = snapshot_download(repo_id=chosen, allow_patterns=allow or None, token=token, max_workers=4)
    except GatedRepoError:
        print(f"LICENCE_URL https://huggingface.co/{chosen}", file=sys.stderr, flush=True)
        return 3
    except RepositoryNotFoundError:
        return 4
    _log(f"{chosen}: snapshot at {path}")
    # Record where the snapshot landed so a test can find a fallback repo id without the API.
    (manifest_dir / (repo.replace("/", "__") + ".resolved")).write_text(chosen + "\n", encoding="utf-8")
    print(chosen)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="snapshot_download one repo through the proxy, manifest first")
    p.add_argument("repo")
    p.add_argument("--allow", action="append", default=None)
    p.add_argument("--fallback", default=None)
    p.add_argument("--manifest", default=os.path.join(os.environ.get("HF_HOME", "/srv/atlas/engines/hf"), "manifests"))
    ns = parser.parse_args(argv)
    if ns.cmd == "pull":
        return pull(ns.repo, ns.allow, ns.fallback, Path(ns.manifest))
    return 1


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(main())
