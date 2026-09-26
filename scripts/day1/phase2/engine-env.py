#!/usr/bin/env python3
"""Render one systemd environment file per engine from config/engines.json.

Output: $ATLAS_ETC/engines/<key>.env (root:atlas 640), consumed by systemd/llama-server@.service through
EnvironmentFile= and by the Phase 3 driver (sourceable KEY='value' lines). Each file carries:

    ATLAS_ENGINE, ATLAS_MODE, ATLAS_KV_TYPE, ATLAS_CTX_SIZE, ATLAS_PARALLEL, ATLAS_N_KEEP, ATLAS_MODEL_FILE,
    ATLAS_MODEL_PRESENT (1/0), ATLAS_KV_PROOF_LINES, LLAMA_ARG_HOST, LLAMA_ARG_PORT, ARGS

ARGS is the complete llama-server command line (the unit runs `llama-server $ARGS`). systemd splits $ARGS on
whitespace with no quoting, so every token is checked for whitespace, single quotes and backslashes.

Overrides (the contract Phase 3 relies on): $ATLAS_ETC/engines/overrides.json, an object keyed by engine key with any of
    {"kv_type": "f16|q8_0|q4_0", "ctx_size": N, "parallel": N, "coresident": true|false, "n_keep": N}
The DeepSeek KV ladder writes its winning cache type (and, for an f16-only result, the capped ctx_size) there and re-runs
this script, so the unit file and the env file always agree (gguf-models.md §6, §12 item 2).

Model files are located in $ATLAS_SRV/models/<key>/ by model_file_pattern (first shard). A file that is not present yet
(Phase 2 renders the seven large engines before Phase 3 pulls them) is rendered with ATLAS_MODEL_PRESENT=0 and the
expected path, so `systemctl start` fails loudly with llama.cpp's own "failed to load model" rather than silently.

Usage:
    engine-env.py [--engines FILE] [--out DIR] [--models-dir DIR] [--slots-dir DIR] [--port-base N]
                  [--overrides FILE] [--key KEY ...] [--set-override KEY FIELD VALUE] [--print KEY]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

KV_TYPES = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}  # common/arg.cpp, VERIFIED
CHAT_MODES = {"chat", "vision"}


def die(msg: str) -> None:
    print(f"engine-env.py: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        die(f"{path} does not exist")
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")
    return None


def find_model_file(model_dir: Path, patterns: str) -> Path | None:
    """First existing file in model_dir matching any '|'-separated fnmatch pattern (case-insensitive)."""
    if not model_dir.is_dir():
        return None
    names = sorted(p.name for p in model_dir.iterdir() if p.is_file() and not p.name.endswith((".part", ".sha256")))
    for pattern in patterns.split("|"):
        hits = [n for n in names if fnmatch.fnmatch(n.lower(), pattern.lower())]
        if hits:
            return model_dir / hits[0]
    return None


def expected_model_path(model_dir: Path, patterns: str) -> Path:
    """The path to print when nothing matches yet: the first pattern (literal names are their own pattern)."""
    return model_dir / patterns.split("|")[0]


def check_token(token: str, key: str) -> str:
    if any(ch.isspace() for ch in token) or "'" in token or "\\" in token:
        die(f"{key}: argument {token!r} contains whitespace, a quote or a backslash; systemd cannot pass it via $ARGS")
    return token


def build_args(
    eng: dict[str, Any],
    ov: dict[str, Any],
    model_path: Path,
    mmproj_path: Path | None,
    slots_dir: Path,
    port: int,
) -> tuple[list[str], dict[str, Any]]:
    key = eng["key"]
    mode = eng.get("mode", "chat")
    kv_type = str(ov.get("kv_type", eng.get("kv_class", "none")))
    coresident = bool(ov.get("coresident", False))
    ctx_size = int(ov.get("ctx_size", eng["ctx_size_coresident"] if coresident and "ctx_size_coresident" in eng else eng["ctx_size"]))
    parallel = int(ov.get("parallel", eng["parallel_coresident"] if coresident and "parallel_coresident" in eng else eng["parallel"]))
    n_keep = int(ov.get("n_keep", eng.get("n_keep", 0)))
    if kv_type != "none" and kv_type not in KV_TYPES:
        die(f"{key}: kv type {kv_type!r} is not one of {sorted(KV_TYPES)}")
    if parallel < 1:
        die(f"{key}: parallel must be >= 1 (never auto: gguf-models.md §1.3)")
    if ctx_size < 256 * parallel:
        die(f"{key}: ctx_size {ctx_size} leaves less than 256 tokens per slot for {parallel} slots")
    if eng.get("kv_types_must_match") and kv_type == "none":
        die(f"{key}: this architecture needs an explicit, identical K/V cache type")

    args: list[str] = ["--model", str(model_path)]
    if mmproj_path is not None:
        args += ["--mmproj", str(mmproj_path)]
    args += ["--alias", key, "--host", "127.0.0.1", "--port", str(port)]
    args += ["--ctx-size", str(ctx_size), "--parallel", str(parallel)]
    if mode in CHAT_MODES:
        # Section 4.3: context shift with n_keep on; both are off/0 by default now (research conflict c).
        args += ["--keep", str(n_keep), "--context-shift"]
        args += ["--slot-save-path", str(slots_dir / key)]
    if kv_type != "none":
        # Flash attention is the prerequisite for cache quantisation (Section 4.3); K and V always identical here.
        args += ["--flash-attn", "on", "--cache-type-k", kv_type, "--cache-type-v", kv_type]
    args += ["--n-gpu-layers", "all", "--no-webui", "--metrics"]
    args += [str(t) for t in eng.get("extra_args", [])]
    args = [check_token(t, key) for t in args]
    meta = {
        "mode": mode,
        "kv_type": kv_type,
        "ctx_size": ctx_size,
        "parallel": parallel,
        "n_keep": n_keep,
        "coresident": coresident,
    }
    return args, meta


def sh_quote(value: str) -> str:
    # Values are single-quoted so both systemd's EnvironmentFile parser and bash `source` read them verbatim;
    # check_token already refused single quotes and backslashes inside ARGS.
    if "'" in value or "\\" in value or "\n" in value:
        die(f"cannot quote value {value!r}")
    return f"'{value}'"


def render(eng: dict[str, Any], ov: dict[str, Any], opts: argparse.Namespace, index: int) -> tuple[Path, str]:
    key = eng["key"]
    port = opts.port_base + index
    model_dir = Path(opts.models_dir) / key
    patterns = eng.get("model_file_pattern") or (eng["files"][0]["name"].split("/")[-1] if eng.get("files") else None)
    if not patterns:
        die(f"{key}: no model_file_pattern and no files[] to derive it from")
    model_path = find_model_file(model_dir, patterns)
    present = model_path is not None
    if model_path is None:
        model_path = expected_model_path(model_dir, patterns)
    mmproj_path: Path | None = None
    if eng.get("mmproj"):
        mmproj_path = model_dir / str(eng["mmproj"])
        if present and not mmproj_path.is_file():
            present = False
    slots_dir = Path(opts.slots_dir)
    args, meta = build_args(eng, ov, model_path, mmproj_path, slots_dir, port)
    if meta["mode"] in CHAT_MODES and opts.mkdirs:
        # --slot-save-path refuses a missing directory (arg.cpp "not a directory", VERIFIED).
        d = slots_dir / key
        d.mkdir(parents=True, exist_ok=True)
        try:
            shutil.chown(d, "atlas", "atlas")
        except (LookupError, PermissionError):
            pass
    lines = [
        f"# {key}.env — rendered by phase2/engine-env.py from config/engines.json; edit overrides.json, not this file.",
        f"# {eng.get('display_name', key)}",
        f"ATLAS_ENGINE={sh_quote(key)}",
        f"ATLAS_MODE={sh_quote(meta['mode'])}",
        f"ATLAS_ARBITER_CLASS={sh_quote(str(eng.get('arbiter_class', '')))}",
        f"ATLAS_KV_TYPE={sh_quote(meta['kv_type'])}",
        f"ATLAS_CTX_SIZE={meta['ctx_size']}",
        f"ATLAS_PARALLEL={meta['parallel']}",
        f"ATLAS_N_KEEP={meta['n_keep']}",
        f"ATLAS_CORESIDENT={1 if meta['coresident'] else 0}",
        f"ATLAS_KV_PROOF_LINES={int(eng.get('kv_proof_lines', 0))}",
        f"ATLAS_FOOTPRINT_GB={eng.get('footprint_gb', 0)}",
        f"ATLAS_MODEL_FILE={sh_quote(str(model_path))}",
        f"ATLAS_MODEL_PRESENT={1 if present else 0}",
        "LLAMA_ARG_HOST=127.0.0.1",
        f"LLAMA_ARG_PORT={port}",
        f"ARGS={sh_quote(' '.join(args))}",
        "",
    ]
    return Path(opts.out) / f"{key}.env", "\n".join(lines)


def install(path: Path, content: str) -> bool:
    """Write atomically with root:atlas 640 when possible; return True when the content changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old == content:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    try:
        shutil.chown(tmp, "root", "atlas")
    except (LookupError, PermissionError):
        pass  # tests run unprivileged; the phase runs as root after Phase 1 created the atlas group
    os.replace(tmp, path)
    return True


def main() -> int:
    etc = os.environ.get("ATLAS_ETC", "/etc/atlas")
    srv = os.environ.get("ATLAS_SRV", "/srv/atlas")
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default=str(here / "config" / "engines.json"))
    ap.add_argument("--out", default=f"{etc}/engines")
    ap.add_argument("--models-dir", default=f"{srv}/models")
    ap.add_argument("--slots-dir", default=f"{srv}/data/slots")
    ap.add_argument("--port-base", type=int, default=int(os.environ.get("LLAMA_PORT_BASE", "8100")))
    ap.add_argument("--overrides", default=f"{etc}/engines/overrides.json")
    ap.add_argument("--key", action="append", default=[], help="render only these keys (default: all)")
    ap.add_argument("--set-override", nargs=3, action="append", default=[], metavar=("KEY", "FIELD", "VALUE"),
                    help="store FIELD=VALUE for KEY in overrides.json before rendering (kv_type, ctx_size, parallel, coresident, n_keep)")
    ap.add_argument("--clear-override", nargs=2, action="append", default=[], metavar=("KEY", "FIELD"))
    ap.add_argument("--print", dest="print_key", help="print the rendered file for KEY to stdout instead of installing")
    ap.add_argument("--no-mkdirs", dest="mkdirs", action="store_false", help="do not create slot directories")
    opts = ap.parse_args()

    data = load_json(Path(opts.engines))
    engines: list[dict[str, Any]] = data["engines"] if isinstance(data, dict) else data
    keys = [e["key"] for e in engines]
    if len(set(keys)) != len(keys):
        die("duplicate engine keys in engines.json")

    ov_path = Path(opts.overrides)
    overrides: dict[str, dict[str, Any]] = load_json(ov_path) if ov_path.is_file() else {}
    for key, field, value in opts.set_override:
        if key not in keys:
            die(f"--set-override: unknown engine {key!r}")
        if field not in {"kv_type", "ctx_size", "parallel", "coresident", "n_keep"}:
            die(f"--set-override: unknown field {field!r}")
        parsed: Any = value
        if field in {"ctx_size", "parallel", "n_keep"}:
            parsed = int(value)
        elif field == "coresident":
            parsed = value.lower() in {"1", "true", "on", "yes"}
        overrides.setdefault(key, {})[field] = parsed
    for key, field in opts.clear_override:
        overrides.get(key, {}).pop(field, None)
    if opts.set_override or opts.clear_override:
        ov_path.parent.mkdir(parents=True, exist_ok=True)
        ov_path.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    wanted = set(opts.key) if opts.key else set(keys)
    unknown = wanted - set(keys)
    if unknown:
        die(f"unknown engine key(s): {sorted(unknown)}")

    changed = 0
    for index, eng in enumerate(engines, start=1):  # CONVENTIONS.md §8: port = LLAMA_PORT_BASE + index, 1-based
        if eng["key"] not in wanted:
            continue
        path, content = render(eng, overrides.get(eng["key"], {}), opts, index)
        if opts.print_key:
            if eng["key"] == opts.print_key:
                sys.stdout.write(content)
            continue
        if install(path, content):
            changed += 1
        if "ATLAS_MODEL_PRESENT=0" in content:
            print(f"engine-env.py: {eng['key']}: model file not present yet under {opts.models_dir}/{eng['key']} "
                  f"(expected after Phase 3); unit will refuse to start until it is", file=sys.stderr)
    if not opts.print_key:
        print(f"engine-env.py: rendered {len(wanted)} env file(s) into {opts.out} ({changed} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
