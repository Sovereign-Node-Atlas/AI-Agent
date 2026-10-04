"""phase2/engine-env.py: the renderer of /etc/atlas/engines/<key>.env (CONVENTIONS.md §7.8: pytest, no live services).

The script is not a package module (it is run as `python3 phase2/engine-env.py` by phase2/01-llama.sh and 04-memory.sh),
so it is loaded here with importlib from its repository path and its main() driven through sys.argv in-process. Every
engine of the real config/engines.json is rendered into a temp tree, and the hardening rules the docstring states
(RESERVED_FLAGS / RESERVED_ENV, token quoting, ctx/parallel sanity, kv_types_must_match) and the --set-override /
--clear-override round trip Phase 3 relies on are asserted.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

DAY1 = Path(__file__).resolve().parents[2]
SCRIPT = DAY1 / "phase2" / "engine-env.py"
ENGINES_JSON = DAY1 / "config" / "engines.json"
PORT_BASE = 8100


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("engine_env", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def engine_env() -> ModuleType:
    return _load()


@pytest.fixture
def engines() -> list[dict[str, Any]]:
    return json.loads(ENGINES_JSON.read_text(encoding="utf-8"))["engines"]


def _concrete_name(pattern: str) -> str:
    """A file name that fnmatch-es the FIRST '|'-pattern (globs replaced by a literal that matches them)."""
    return pattern.split("|")[0].replace("*", "x").replace("?", "x")


@pytest.fixture
def tree(tmp_path: Path, engines: list[dict[str, Any]]) -> dict[str, Path]:
    models = tmp_path / "models"
    for eng in engines:
        d = models / eng["key"]
        d.mkdir(parents=True)
        pattern = eng.get("model_file_pattern") or eng["files"][0]["name"].split("/")[-1]
        (d / _concrete_name(pattern)).write_bytes(b"GGUF")
        if eng.get("mmproj"):
            (d / str(eng["mmproj"])).write_bytes(b"GGUF")
    return {
        "models": models,
        "out": tmp_path / "etc-engines",
        "slots": tmp_path / "data" / "slots",
        "overrides": tmp_path / "etc-engines" / "overrides.json",
    }


def _run(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch, tree: dict[str, Path], *extra: str,
         engines_file: Path = ENGINES_JSON) -> int:
    argv = [
        str(SCRIPT), "--engines", str(engines_file), "--out", str(tree["out"]), "--models-dir", str(tree["models"]),
        "--slots-dir", str(tree["slots"]), "--port-base", str(PORT_BASE), "--overrides", str(tree["overrides"]), *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return int(engine_env.main())


def _kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k] = v[1:-1] if len(v) >= 2 and v[0] == v[-1] == "'" else v
    return out


def _write_engines(tmp_path: Path, engines: list[dict[str, Any]]) -> Path:
    p = tmp_path / "engines.json"
    p.write_text(json.dumps({"engines": engines}), encoding="utf-8")
    return p


# --- the real engines.json renders for every key ---------------------------------------------------------------------

def test_renders_every_engine(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch, tree: dict[str, Path],
                              engines: list[dict[str, Any]]) -> None:
    assert _run(engine_env, monkeypatch, tree) == 0
    for index, eng in enumerate(engines, start=1):  # CONVENTIONS.md §8: port = LLAMA_PORT_BASE + index, 1-based
        f = tree["out"] / f"{eng['key']}.env"
        assert f.is_file(), f
        assert stat.S_IMODE(f.stat().st_mode) == 0o640
        kv = _kv(f)
        assert kv["ATLAS_ENGINE"] == eng["key"]
        assert kv["ATLAS_MODEL_PRESENT"] == "1"
        assert kv["ATLAS_MODEL_PATTERN"] == (eng.get("model_file_pattern") or eng["files"][0]["name"].split("/")[-1])
        assert kv["LLAMA_ARG_HOST"] == "127.0.0.1"
        assert kv["LLAMA_ARG_PORT"] == str(PORT_BASE + index)
        assert kv["ATLAS_CTX_SIZE"] == str(eng["ctx_size"])
        assert kv["ATLAS_PARALLEL"] == str(eng["parallel"])
        args = kv["ARGS"].split()
        assert args[args.index("--port") + 1] == str(PORT_BASE + index)
        assert args[args.index("--host") + 1] == "127.0.0.1"
        assert args[args.index("--ctx-size") + 1] == str(eng["ctx_size"])  # the TOTAL pool (conflict 9)
        assert "--n-gpu-layers" in args and "--no-webui" in args
        assert Path(args[args.index("--model") + 1]).is_file()
        if eng.get("mode", "chat") in {"chat", "vision"}:
            assert args[args.index("--slot-save-path") + 1] == str(tree["slots"])
            assert "--context-shift" in args
        if eng.get("mmproj"):
            assert Path(args[args.index("--mmproj") + 1]).is_file()
        kv_type = eng.get("kv_class", "none")
        if kv_type != "none":
            assert args[args.index("--cache-type-k") + 1] == kv_type
            assert args[args.index("--cache-type-v") + 1] == kv_type
            assert "--flash-attn" in args
    assert len(list(tree["out"].glob("*.env"))) == len(engines)
    # Appendix B slot directory: created for the chat/vision engines, 0750 (KV snapshots of the Principal's chats).
    assert tree["slots"].is_dir()
    assert stat.S_IMODE(tree["slots"].stat().st_mode) == 0o750


def test_env_json_values_survive_and_bash_can_source(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                      tree: dict[str, Path], engines: list[dict[str, Any]]) -> None:
    assert _run(engine_env, monkeypatch, tree) == 0
    with_env = [e for e in engines if e.get("env")]
    assert with_env, "engines.json carries at least one JSON-valued LLAMA_ARG_* option (gpt-oss reasoning_effort)"
    for eng in with_env:
        kv = _kv(tree["out"] / f"{eng['key']}.env")
        for name, value in eng["env"].items():
            assert kv[name] == str(value)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    for eng in engines:
        f = tree["out"] / f"{eng['key']}.env"
        res = subprocess.run(
            [bash, "-c", 'set -eu; source "$1"; printf "%s\\n%s\\n" "$LLAMA_ARG_PORT" "$ARGS"', "_", str(f)],
            capture_output=True, text=True, check=False,
        )
        assert res.returncode == 0, res.stderr
        port, args = res.stdout.splitlines()[:2]
        assert port == _kv(f)["LLAMA_ARG_PORT"]
        assert f"--port {port}" in args


def test_missing_model_renders_absent_and_rerender_is_idempotent(engine_env: ModuleType,
                                                                  monkeypatch: pytest.MonkeyPatch,
                                                                  tree: dict[str, Path],
                                                                  engines: list[dict[str, Any]],
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    key = engines[0]["key"]
    for p in (tree["models"] / key).iterdir():
        p.unlink()
    assert _run(engine_env, monkeypatch, tree) == 0
    kv = _kv(tree["out"] / f"{key}.env")
    assert kv["ATLAS_MODEL_PRESENT"] == "0"
    # The expected path is rendered literally so `systemctl start` fails with llama.cpp's own "failed to load model".
    assert kv["ATLAS_MODEL_FILE"].endswith(engines[0]["model_file_pattern"].split("|")[0])
    assert "model file not present yet" in capsys.readouterr().err
    before = {p.name: p.read_text(encoding="utf-8") for p in tree["out"].glob("*.env")}
    assert _run(engine_env, monkeypatch, tree) == 0
    assert "(0 changed)" in capsys.readouterr().out
    assert {p.name: p.read_text(encoding="utf-8") for p in tree["out"].glob("*.env")} == before


def test_print_mode_writes_nothing(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch, tree: dict[str, Path],
                                   capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(engine_env, monkeypatch, tree, "--print", "router-qwen3.5-4b") == 0
    out = capsys.readouterr().out
    assert "ATLAS_ENGINE='router-qwen3.5-4b'" in out
    assert not tree["out"].exists()
    assert not tree["slots"].exists()


# --- hardening: refused inputs ---------------------------------------------------------------------------------------

def _mutated(engines: list[dict[str, Any]], key: str, **fields: Any) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(engines))
    for eng in out:
        if eng["key"] == key:
            eng.update(fields)
    return out


@pytest.mark.parametrize(
    "fields",
    [
        {"extra_args": ["--rpc", "10.0.0.5:50052"]},            # off-node inference (Section 1)
        {"extra_args": ["--host=0.0.0.0"]},                     # rebind, --flag=value spelling
        {"extra_args": ["--hf-repo", "x/y"]},                   # model pull outside hf_download
        {"extra_args": ["--slot-save-path", "/tmp"]},           # move the slot files
        {"extra_args": ["--chat-template-kwargs", '{"reasoning_effort":"high"}']},  # JSON token: quotes
        {"extra_args": ["--temp", "0.2 0.3"]},                  # whitespace inside a token
        {"env": {"LLAMA_ARG_HOST": "0.0.0.0"}},                 # reserved env name
        {"env": {"LLAMA_ARG_MODEL_URL": "https://x/y.gguf"}},   # reserved env name
        {"env": {"NOT_LLAMA": "1"}},                            # not an LLAMA_ARG_* name
        {"env": ["LLAMA_ARG_X=1"]},                             # not an object
        {"parallel": 0},                                        # never auto (gguf-models.md §1.3)
        {"ctx_size": 1024, "parallel": 8},                      # < 256 tokens per slot
        {"kv_class": "q3_k"},                                   # not a cache type
    ],
)
def test_refused_inputs(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch, tree: dict[str, Path],
                        engines: list[dict[str, Any]], tmp_path: Path, fields: dict[str, Any]) -> None:
    bad = _write_engines(tmp_path, _mutated(engines, "router-qwen3.5-4b", **fields))
    with pytest.raises(SystemExit) as exc:
        _run(engine_env, monkeypatch, tree, engines_file=bad)
    assert exc.value.code == 1


def test_kv_types_must_match_requires_a_cache_type(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                   tree: dict[str, Path], engines: list[dict[str, Any]],
                                                   tmp_path: Path) -> None:
    deepseek = next(e for e in engines if e["key"] == "deepseek-v4-flash")
    assert deepseek.get("kv_types_must_match") is True
    bad = _write_engines(tmp_path, _mutated(engines, "deepseek-v4-flash", kv_class="none"))
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, engines_file=bad)


def test_duplicate_keys_and_unknown_key_are_refused(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                    tree: dict[str, Path], engines: list[dict[str, Any]],
                                                    tmp_path: Path) -> None:
    dup = _write_engines(tmp_path, [*engines, json.loads(json.dumps(engines[-1]))])
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, engines_file=dup)
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, "--key", "no-such-engine")


# --- overrides: the contract Phase 3's KV ladder relies on ------------------------------------------------------------

def test_set_and_clear_override_round_trip(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                           tree: dict[str, Path]) -> None:
    key = "deepseek-v4-flash"
    assert _run(engine_env, monkeypatch, tree,
                "--set-override", key, "kv_type", "f16", "--set-override", key, "ctx_size", "65536") == 0
    ov = json.loads(tree["overrides"].read_text(encoding="utf-8"))
    assert ov == {key: {"kv_type": "f16", "ctx_size": 65536}}
    kv = _kv(tree["out"] / f"{key}.env")
    assert kv["ATLAS_KV_TYPE"] == "f16"
    assert kv["ATLAS_CTX_SIZE"] == "65536"
    args = kv["ARGS"].split()
    assert args[args.index("--cache-type-k") + 1] == "f16"
    assert args[args.index("--cache-type-v") + 1] == "f16"
    assert args[args.index("--ctx-size") + 1] == "65536"
    # A plain re-render keeps the stored override (the unit file and the env file always agree).
    assert _run(engine_env, monkeypatch, tree) == 0
    assert _kv(tree["out"] / f"{key}.env")["ATLAS_KV_TYPE"] == "f16"
    # Clearing restores engines.json's values.
    assert _run(engine_env, monkeypatch, tree,
                "--clear-override", key, "kv_type", "--clear-override", key, "ctx_size") == 0
    kv = _kv(tree["out"] / f"{key}.env")
    assert kv["ATLAS_KV_TYPE"] == "q4_0"
    assert kv["ATLAS_CTX_SIZE"] != "65536"
    assert json.loads(tree["overrides"].read_text(encoding="utf-8")) == {key: {}}


def test_override_refuses_unknown_engine_and_field(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                   tree: dict[str, Path]) -> None:
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, "--set-override", "nope", "kv_type", "f16")
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, "--set-override", "deepseek-v4-flash", "host", "0.0.0.0")
    with pytest.raises(SystemExit):
        _run(engine_env, monkeypatch, tree, "--set-override", "deepseek-v4-flash", "kv_type", "q3_k")


def test_coresident_override_uses_the_coresident_ctx(engine_env: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                     tree: dict[str, Path], engines: list[dict[str, Any]]) -> None:
    cores = [e for e in engines if "ctx_size_coresident" in e]
    if not cores:
        pytest.skip("no engine declares ctx_size_coresident")
    eng = cores[0]
    assert _run(engine_env, monkeypatch, tree, "--set-override", eng["key"], "coresident", "true") == 0
    kv = _kv(tree["out"] / f"{eng['key']}.env")
    assert kv["ATLAS_CORESIDENT"] == "1"
    assert kv["ATLAS_CTX_SIZE"] == str(eng["ctx_size_coresident"])


def test_reserved_lists_are_mirrored_in_engines_json_meta(engine_env: ModuleType) -> None:
    """engines.json _meta documents the two rules; the script's sets are the ones enforced (docstring)."""
    meta = json.loads(ENGINES_JSON.read_text(encoding="utf-8")).get("_meta", {})
    for flag in ("--rpc", "--host", "--port", "--model", "--hf-repo", "--slot-save-path", "--api-key"):
        assert flag in engine_env.RESERVED_FLAGS
    for name in ("LLAMA_ARG_HOST", "LLAMA_ARG_PORT", "LLAMA_ARG_MODEL", "LLAMA_ARG_RPC", "LLAMA_ARG_HF_REPO"):
        assert name in engine_env.RESERVED_ENV
    if isinstance(meta, dict) and "extra_args_rule" in meta:
        assert "--rpc" in str(meta["extra_args_rule"]) or "reserved" in str(meta["extra_args_rule"]).lower()
    assert os.access(SCRIPT, os.R_OK)
