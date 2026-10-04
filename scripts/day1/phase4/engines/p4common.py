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

2. CLI for the pull step:  python3.12 -I -P p4common.py pull REPO [--allow PATTERN ...] [--fallback REPO2]
                                                                 [--revision REV]
   Re-reads {HF_ENDPOINT}/api/models/<repo>?blobs=true (gated, cardData.license, sha, siblings[].size/lfs.oid) at pull
   time (research §0: never trust the snippet sizes), then huggingface_hub.snapshot_download(repo, allow_patterns=...,
   revision=<sha>) and verifies every LFS file against its sha256 from that listing (rule §7.3 / Section 17 Phase 3
   "pull, with checksum verification"); a mismatch deletes the file and fails. The container writes NOTHING but the
   download itself: the manifest, the pin and the resolved id go to stdout as tagged lines that lib-engine.sh stores
   root-held under $ATLAS_STATE/phase4/hf-manifests/ (fix round: the atlas-writable mount is not trusted for records):
       P4MANIFEST {json}      the file listing with sizes and sha256s, gated flag, licence, sha, pulled_at
       P4PIN <sha>            the hub revision this pull used (rule §7.9; lib-engine.sh pins it for every later run)
       P4RESOLVED <repo-id>   the repo that landed (the fallback when the first id does not exist)
   Revision pin: --revision (json hf_repos[].revision, else the root-held .pinned from an earlier pull); none -> HEAD.
   Offline contract (fix round, VERIFIED huggingface_hub 2.0.0): a download pinned to a commit sha leaves NO refs/main
   in the cache, and every GPU test runs with HF_HUB_OFFLINE=1 resolving the default revision "main" -> every from_
   pretrained / snapshot_download(REPO) would raise LocalEntryNotFoundError. So after the download refs/main is written
   with the pulled sha ("main" in this cache == the pinned revision) and the pull PROVES the offline path in-process
   (local_files_only=True: snapshot_download with the same patterns, hf_hub_download of one file, and
   DiffusionPipeline.download for repos with a model_index.json, whose own completeness check runs against the hub's
   trees/<sha>.json listing). A failed proof is exit 6 here, hours before the GPU test would have found it.
   Exit codes: 0 ok; 1 anything else (a squid TCP_DENIED 403 is reported as "proxy denied", never as a licence
   problem; an unreadable token file is reported as such); 3 gated/forbidden (the licence URL is printed on stderr as
   the last line: "LICENCE_URL https://huggingface.co/<repo>"); 4 repo not found (after trying --fallback); 5 sha256
   mismatch; 6 downloaded but not resolvable offline.
   HF_TOKEN: from the environment, else read from $HF_TOKEN_FILE (lib-engine.sh stages a copy readable by the container
   uid at /run/secrets/hf-token.env for gated repos only); never printed. HF_ENDPOINT (atlas.env, CONVENTIONS §3) is
   honoured for the pull itself; the token is sent ONLY when the endpoint is https://huggingface.co or a *.hf.co host
   (rule §7.2): a gated repo behind another mirror is refused (exit 1), a public one is pulled without the token.

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
import re
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESULT_TAG = "P4RESULT "
MANIFEST_TAG = "P4MANIFEST "
PIN_TAG = "P4PIN "
RESOLVED_TAG = "P4RESOLVED "
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


# --- loader kwargs (fix round 2) --------------------------------------------------------------------------------------
def _lib_version(name: str) -> tuple[int, ...] | None:
    """The installed version of a library as an integer tuple (None when it is not importable)."""
    try:
        from importlib.metadata import version

        raw = version(name)
    except Exception:
        return None
    parts: list[int] = []
    for piece in re.split(r"[.+-]", raw):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts) if parts else None


def load_kwargs(cls: type, dtype: object, *, disable_mmap: bool = True) -> dict[str, object]:
    """The from_pretrained keyword spellings this diffusers/transformers build accepts, decided ONCE from the
    library VERSION (fix round 2: both libraries declare from_pretrained as (cls, name, **kwargs), so the signature
    names neither `dtype` nor `disable_mmap` and inspecting it was a no-op). A TypeError raised inside a 30 GB load
    would otherwise re-run it. Thresholds VERIFIED from the released wheels (2026-10-04):
      diffusers    `dtype` resolved alongside `torch_dtype` from 0.40.0 (_resolve_dtype in pipeline_utils.py);
                   `disable_mmap` popped by DiffusionPipeline.from_pretrained from 0.37 (absent in 0.36.0);
      transformers `dtype` accepted from 4.56 (kwargs.pop("dtype") in modeling_utils.py; torch_dtype logs a
                   deprecation warning there and in 5.x).
    Below the thresholds, or for any other library (chronos, sam2, ...), `torch_dtype` is passed and `disable_mmap`
    is left out (the safetensors mmap patch in Test covers the mmap side)."""
    lib = (cls.__module__ or "").split(".")[0]
    kw: dict[str, object] = {}
    if lib == "diffusers":
        v = _lib_version("diffusers") or ()
        kw["dtype" if v >= (0, 40) else "torch_dtype"] = dtype
        if disable_mmap and v >= (0, 37):
            kw["disable_mmap"] = True
    elif lib == "transformers":
        v = _lib_version("transformers") or ()
        kw["dtype" if v >= (4, 56) else "torch_dtype"] = dtype
    else:
        kw["torch_dtype"] = dtype
    return kw


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
HF_DEFAULT = "https://huggingface.co"
HF_HOST_RE = re.compile(r"^https://(huggingface\.co|[a-z0-9.-]+\.hf\.co)/?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class TokenUnreadable(Exception):
    """The token file exists but this uid cannot open it (a permissions problem, never a licence problem)."""


def hf_token() -> str | None:
    """HF_TOKEN from the environment, else the first HF_TOKEN=... (or bare) line of $HF_TOKEN_FILE.
    Absent file -> None. Present but unreadable -> TokenUnreadable (fix round 2: never swallowed as 'no token')."""
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok
    path = os.environ.get("HF_TOKEN_FILE") or "/run/secrets/hf-token.env"
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("HF_TOKEN="):
                    line = line[len("HF_TOKEN="):].strip().strip("'\"")
                return line or None
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise TokenUnreadable(f"{path} is present but not readable by uid {os.getuid()} ({exc.strerror})") from exc
    except OSError as exc:
        raise TokenUnreadable(f"{path}: {exc!r}") from exc
    return None


def hf_base() -> tuple[str, bool]:
    """(hub base URL, token-safe). HF_ENDPOINT is honoured as given (CONVENTIONS §3: the Principal's mirror setting,
    like lib/common.sh hf_download); the second value says whether a token may travel to it (huggingface.co or a
    *.hf.co host only, rule §7.2)."""
    ep = (os.environ.get("HF_ENDPOINT") or "").strip()
    if not ep:
        return HF_DEFAULT, True
    return ep.rstrip("/"), bool(HF_HOST_RE.match(ep))


def _api_json(url: str, token: str | None) -> tuple[int, Any, dict[str, str]]:
    """GET url -> (status, json or None, lower-cased response headers)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "atlas-day1-phase4"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, json.loads(resp.read().decode("utf-8")), hdrs
    except urllib.error.HTTPError as exc:
        hdrs = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, None, hdrs
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _log(f"GET {url} failed: {exc!r} (proxy reachable? see /var/log/squid/access.log)")
        return 0, None, {}


def _is_proxy_denial(code: int, hdrs: dict[str, str]) -> bool:
    """squid answers 403 (TCP_DENIED) for a host outside the allowlist; its error pages carry Server: squid and
    X-Squid-Error. A 403 from squid is an allowlist problem, never a licence problem (rule §7.4)."""
    if code != 403:
        return False
    return "squid" in hdrs.get("server", "").lower() or "x-squid-error" in hdrs


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(16 * 2**20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(snapshot: Path, files: list[dict[str, Any]]) -> list[str]:
    """sha256 of every manifest file that carries an LFS oid; returns the names that mismatched (deleted)."""
    bad: list[str] = []
    for f in files:
        want = f.get("sha256")
        if not want:
            continue
        target = snapshot / str(f["name"])
        if not target.exists():
            _log(f"verify: {f['name']} missing from the snapshot")
            bad.append(str(f["name"]))
            continue
        got = _sha256_file(target)
        if got != want:
            _log(f"verify: sha256 MISMATCH {f['name']}: got {got} expected {want}; deleting")
            try:
                real = target.resolve()
                target.unlink()
                if real != target and real.exists():
                    real.unlink()
            except OSError as exc:
                _log(f"verify: could not delete {target}: {exc}")
            bad.append(str(f["name"]))
    return bad


def write_main_ref(snapshot: Path, sha: str) -> Path:
    """refs/main = sha, atomically (tmp + os.replace, like the hub). huggingface_hub writes refs/<revision> only when
    the requested revision is NOT a commit hash (_cache_commit_hash_for_specific_revision: `if revision != commit_hash`,
    VERIFIED 2.0.0), so a pinned download leaves the default revision unresolvable offline; this makes "main" in the
    offline cache mean the pinned snapshot."""
    storage = snapshot.resolve().parents[1]          # .../models--org--repo/snapshots/<sha> -> models--org--repo
    refs = storage / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    ref = refs / "main"
    tmp = refs / f".main.{os.getpid()}.tmp"
    tmp.write_text(sha, encoding="utf-8")
    os.replace(tmp, ref)
    return ref


def prove_offline(repo: str, allow: list[str] | None, files: list[dict[str, Any]], snapshot: Path) -> str | None:
    """Resolve the pulled repo exactly as the GPU test will (default revision, no network): returns None when every
    probe lands on `snapshot`, else the reason. local_files_only=True is the in-process form of HF_HUB_OFFLINE=1."""
    from huggingface_hub import hf_hub_download, snapshot_download

    try:
        got = Path(snapshot_download(repo_id=repo, allow_patterns=allow or None, local_files_only=True))
    except Exception as exc:
        return f"snapshot_download({repo!r}, local_files_only=True) failed: {type(exc).__name__}: {exc}"
    if got.resolve() != snapshot.resolve():
        return f"offline snapshot_download resolved {got}, expected {snapshot} (refs/main not honoured?)"
    probe = next((str(f["name"]) for f in files if f.get("name")), None)
    if probe:
        try:
            hf_hub_download(repo_id=repo, filename=probe, local_files_only=True)
        except Exception as exc:
            return f"hf_hub_download({repo!r}, {probe!r}, local_files_only=True) failed: {type(exc).__name__}: {exc}"
    if any(str(f.get("name")) == "model_index.json" for f in files):
        # A diffusers pipeline: its own offline path (DiffusionPipeline.download -> cached tree listing -> completeness
        # check) is what flux1-dev_test.py / wan2.2_test.py will run; prove it here, where a failure costs nothing.
        try:
            from diffusers import DiffusionPipeline
        except Exception as exc:
            _log(f"diffusers not importable in the pull container ({exc!r}); pipeline offline check skipped")
            return None
        try:
            folder = Path(DiffusionPipeline.download(repo, local_files_only=True))
        except Exception as exc:
            return (f"DiffusionPipeline.download({repo!r}, local_files_only=True) failed: {type(exc).__name__}: "
                    f"{str(exc)[:400]} (allow_patterns in config/phase4-engines.json skip a file diffusers expects?)")
        if folder.resolve() != snapshot.resolve():
            return f"DiffusionPipeline.download resolved {folder}, expected {snapshot}"
    return None


def pull(repo: str, allow: list[str] | None, fallback: str | None, revision: str | None = None) -> int:
    try:
        token = hf_token()
    except TokenUnreadable as exc:
        _log(f"{exc} — a permissions problem on the staged token copy (lib-engine.sh p4_docker_run --token), NOT a "
             "licence problem; nothing was sent to the hub")
        return 1
    base, token_ok = hf_base()
    if token and not token_ok:
        _log(f"HF_ENDPOINT={base!r} is not huggingface.co or *.hf.co: HF_TOKEN will NOT be sent there (rule §7.2); "
             "public repos are pulled from it without the token")
        token = None
    # Control request first: a public model. 403 here is the proxy (allowlist), not a licence.
    code, _, hdrs = _api_json(f"{base}/api/models/gpt2", None)
    if code == 0 or _is_proxy_denial(code, hdrs):
        _log(f"proxy denied {base} (control GET api/models/gpt2 -> HTTP {code}): the hub host is not allowlisted or "
             "the proxy is down; see /var/log/squid/access.log")
        return 1
    if revision:
        _log(f"{repo}: pinned revision {revision} (json hf_repos[].revision or the root-held .pinned; delete that file "
             "to re-pin)")
    tried = [repo] + ([fallback] if fallback else [])
    chosen: str | None = None
    meta: Any = None
    for cand in tried:
        url = f"{base}/api/models/{cand}" + (f"/revision/{revision}" if revision else "") + "?blobs=true"
        code, meta, hdrs = _api_json(url, token)
        _log(f"api/models/{cand}{'@' + revision if revision else ''}: HTTP {code}")
        if code == 200:
            chosen = cand
            break
        if _is_proxy_denial(code, hdrs) or code == 0:
            _log(f"proxy denied or unreachable for {cand} (HTTP {code}): allowlist / squid, not a licence problem")
            return 1
        if code in (401, 403):
            print(f"LICENCE_URL https://huggingface.co/{cand}", file=sys.stderr, flush=True)
            _log(f"{cand} is gated or private for this token: accept the licence at https://huggingface.co/{cand} "
                 "with the account that owns HF_TOKEN in /etc/atlas/secrets/hf-token.env (or the token is invalid)")
            return 3
        if code == 404:
            continue
        _log(f"unexpected HTTP {code} from the hub API for {cand}")
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
    sha = meta.get("sha")
    if not (isinstance(sha, str) and SHA_RE.match(sha)):
        _log(f"{chosen}: the hub API returned no commit sha ({sha!r}); cannot pin or resolve offline")
        return 1
    manifest = {
        "repo": chosen,
        "requested": repo,
        "gated": meta.get("gated"),
        "license": (meta.get("cardData") or {}).get("license"),
        "sha": sha,
        "revision_requested": revision,
        "allow_patterns": allow,
        "files": files,
        "total_bytes": total,
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _log(f"{chosen}: gated={manifest['gated']} license={manifest['license']} sha={sha} files={len(files)} "
         f"bytes={total / 1e9:.1f} GB")
    if meta.get("gated") and not token:
        if not token_ok:
            _log(f"{chosen} is gated and HF_ENDPOINT={base!r} is not a Hugging Face host: refusing to send HF_TOKEN "
                 "there (rule §7.2); unset HF_ENDPOINT in /etc/atlas/atlas.env or point it at a *.hf.co mirror")
            return 1
        print(f"LICENCE_URL https://huggingface.co/{chosen}", file=sys.stderr, flush=True)
        _log(f"{chosen} is gated and no HF_TOKEN is present")
        return 3

    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

    try:
        path = snapshot_download(repo_id=chosen, allow_patterns=allow or None, token=token, max_workers=4,
                                 revision=sha)
    except GatedRepoError:
        print(f"LICENCE_URL https://huggingface.co/{chosen}", file=sys.stderr, flush=True)
        return 3
    except HfHubHTTPError as exc:
        # RepositoryNotFoundError is raised for 401 too (invalid/expired token): branch on the real status code.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            print(f"LICENCE_URL https://huggingface.co/{chosen}", file=sys.stderr, flush=True)
            _log(f"{chosen}: HTTP {status} from the hub during download: licence not accepted for this token, or the "
                 "token in /etc/atlas/secrets/hf-token.env is invalid/expired")
            return 3
        if status == 404 or isinstance(exc, RepositoryNotFoundError):
            _log(f"{chosen}@{sha}: not found on the hub (HTTP {status})")
            return 4
        _log(f"{chosen}: hub error HTTP {status}: {exc}")
        return 1
    snapshot = Path(path)
    _log(f"{chosen}: snapshot at {snapshot}; verifying sha256 of {sum(1 for f in files if f.get('sha256'))} LFS files")
    bad = verify_snapshot(snapshot, files)
    if bad:
        _log(f"{chosen}: {len(bad)} file(s) failed sha256 verification and were deleted: {bad[:5]}")
        return 5
    ref = write_main_ref(snapshot, sha)
    _log(f"{chosen}: {ref} -> {sha} (offline readers resolve 'main' to this pinned snapshot)")
    why = prove_offline(chosen, allow, files, snapshot)
    if why:
        _log(f"{chosen}: pulled and verified, but NOT resolvable offline the way the GPU test resolves it: {why}")
        return 6
    _log(f"{chosen}: offline resolution proven (snapshot_download, hf_hub_download"
         f"{', DiffusionPipeline.download' if any(str(f.get('name')) == 'model_index.json' for f in files) else ''})")
    # Records for the host (lib-engine.sh stores them root-held; nothing is written to the mount by this process).
    print(MANIFEST_TAG + json.dumps(manifest, separators=(",", ":")), flush=True)
    print(PIN_TAG + sha, flush=True)
    print(RESOLVED_TAG + chosen, flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="snapshot_download one repo through the proxy, listing first, offline proof after")
    p.add_argument("repo")
    p.add_argument("--allow", action="append", default=None)
    p.add_argument("--fallback", default=None)
    p.add_argument("--revision", default=None, help="hub revision to pull (json hf_repos[].revision or the pin)")
    ns = parser.parse_args(argv)
    if ns.cmd == "pull":
        return pull(ns.repo, ns.allow, ns.fallback, ns.revision)
    return 1


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(main())
