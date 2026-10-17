#!/usr/bin/env python3
"""Render one systemd environment file per engine from config/engines.json.

Output: $ATLAS_ETC/engines/<key>.env (root:atlas 640), consumed by systemd/llama-server@.service through
EnvironmentFile= and by the Phase 3 driver (sourceable KEY='value' lines). Each file carries:

    ATLAS_ENGINE, ATLAS_MODE, ATLAS_PLACEMENT (gpu/cpu), ATLAS_KV_TYPE, ATLAS_CTX_SIZE, ATLAS_PARALLEL, ATLAS_N_KEEP,
    ATLAS_MODEL_FILE, ATLAS_MODEL_PATTERN, ATLAS_MODEL_PRESENT (1/0), ATLAS_KV_PROOF_LINES, LLAMA_ARG_HOST,
    LLAMA_ARG_PORT, ARGS

ARGS is the complete llama-server command line (the unit runs `llama-server $ARGS`). systemd splits an unquoted $ARGS
at whitespace with quotes respected and then REMOVED (systemd.service(5), "Command lines"), so a JSON token such as
{"reasoning_effort":"high"} would arrive as {reasoning_effort:high}; every token is therefore refused if it contains
whitespace, a quote of either kind or a backslash. Options whose value is JSON go through their LLAMA_ARG_* env var
instead, listed per engine under "env" in engines.json and written verbatim after LLAMA_ARG_PORT.

Overrides (the contract Phase 3 relies on): $ATLAS_ETC/engines/overrides.json, an object keyed by engine key with any
of {"kv_type": "f16|q8_0|...", "ctx_size": N, "parallel": N, "n_keep": N}. The Apex KV ladder (S-42, D-29 (a)) writes
its winning cache type there only when the f16 rung fails and q8_0 stands, and re-runs this script, so the unit file and
the env file always agree (gguf-models.md §6, §12 item 2).

No co-resident profile (S-22, v0.4.0): D-32 (a) keeps one LLM seat resident at a time, so the S52 standing two-slot
profile (ctx_size_coresident, parallel_coresident, ATLAS_CORESIDENT, the overrides "coresident" field) is gone. Every
unit renders engines.json's own ctx_size x parallel; a stale "coresident" field left in an old overrides.json, or an old
co-resident key in an engine entry, is ignored (never rendered).

Placement (D-14 option (e), v0.4.0): an entry with "placement": "cpu" (the Qwen3 embedder and reranker) is rendered
with `--device none` and without `--n-gpu-layers`, so it never touches the Vulkan device (common/arg.cpp:2741-2747,
`none` parsed at :1128 at b11474, VERIFIED); ATLAS_PLACEMENT tells Phase 2 step 4 and Phase 3 which side it runs on.
The Arbiter charges it as the host resident set (orchestrator atlas/arbiter.py). The default is gpu.

Model files are located in $ATLAS_SRV/models/<key>/ by model_file_pattern (first shard). A file that is not present yet
(Phase 2 renders the four LLM seats before Phase 3 pulls them) is rendered with ATLAS_MODEL_PRESENT=0 and the
expected path, so `systemctl start` fails loudly with llama.cpp's own "failed to load model" rather than silently.

Log verbosity (S52 fix round): every chat/vision engine runs with `--verbosity 4`. At the pinned llama.cpp (b11474,
re-verified 2026-10-16, v0.4.0 pin facts) common_log_get_verbosity maps the library's INFO level to TRACE
(common/log.cpp:535, since 67b2b7f2f) and llama-server's default threshold is 3 (common/common.h:539), so without it
"llama_kv_cache: size =", "llama_context: flash_attn =", the recurrent and compressor size lines and the compute-buffer
lines never reach the journal: V4's proof and the S52 memory records (phase3/loadtest.py) read them. Exactly 4: at 5 or
with -v, --fit's trial context keeps INFO (common/common.cpp:1267, common/fit.cpp:52) and prints a second set of K/V
lines. It changes the log level only.

Prompt cache (B-33; research_notes/ATLAS v0.4.0 build record/answers.md section 1.5 measure 5, approved by the
Principal on 2026-10-17): every engine is rendered with an explicit `--cache-ram N` (MiB), the host-RAM prompt cache
that sits outside the GTT counter and the Arbiter's budget. N is the engines.json field cache_ram_mib, an integer of 0
or more (0 disables the cache); anything else, -1 (no limit) included, is refused. When the field is absent the mode
default applies (CACHE_RAM_DEFAULT_MIB): 4096 for chat and vision, 0 for embedding and reranking; a mode with no
default must carry the field. Without the flag llama-server would keep up to 8192 MiB per server (common/common.h:638
at b11474; -cram/--cache-ram and LLAMA_ARG_CACHE_RAM at common/arg.cpp:1719-1725; 0 skips the cache,
tools/server/server-context.cpp:1484-1495; a negative value means no limit, tools/server/server-task.h:654; VERIFIED).
--cache-ram, -cram and LLAMA_ARG_CACHE_RAM are reserved, so extra_args and env cannot undo the field (_meta
cache_ram_rule). Phase 3 measures the caches and raises the to-do cache-ram if a cap should change.

--slot-save-path is Appendix B's single directory /srv/atlas/data/slots for every chat/vision engine (the orchestrator
names slot files per engine); the directory is created only when installing, never in --print mode (read-only).

Hardening (fix round): engines.json is root-owned, but one edit there must not be able to rebind an engine to 0.0.0.0,
make llama-server (built with an HTTPS client) pull a model by itself outside hf_download's manifest and hash path
(Section 16.3 item 8), move --slot-save-path, offload tensors to a remote host (--rpc: off-node inference, the one thing
Section 1 forbids outright), load a second model, a LoRA, a control vector or a template file from an arbitrary path,
serve a directory over HTTP (--path) or write a log file somewhere of its choosing. The env map therefore refuses the
LLAMA_ARG_* names in RESERVED_ENV and extra_args refuses the flags in RESERVED_FLAGS; both lists are mirrored in
engines.json _meta (extra_args_rule, env_rule). v0.4.0 (pin facts, b11474): the lists were re-read against
common/arg.cpp at the new pin and extended with the file, network and exec options both pins accept but v0.3.5 left
open: the draft-model aliases, -hft, the Docker model pull, -mmu, the UI and its config file and MCP proxy, the built-in
tools and their runtime (exec_shell_command, write_file, docker:/ssh: runtimes), --agent, the MCP server configs,
--media-path, --log-prompts-dir, --video-ffmpeg-dir, the grammar and JSON-schema files and the lookup caches. The env
names are the real LLAMA_ARG_* names at b11474; names that exist at neither pin stay listed defensively. The prompt-cache
cap (--cache-ram, -cram, LLAMA_ARG_CACHE_RAM) is reserved too: it is cache_ram_mib's alone (docstring "Prompt cache").
D-16 (approved 2026-10-17) adds one process variable, refused as an env key and inside any extra_args token
(RESERVED_PROCESS_ENV): LLAMA_ATTN_ROT_DISABLE, which turns off the Hadamard rotation of every quantised KV cache at
b11474 (src/llama-kv-cache.cpp:315-318, VERIFIED). A quantised cache without the rotation is the dangerous case; the
load log's n_rot_k and n_rot_v lines are the second check (engines.json phased notes for Intern-S1 and Baichuan-M3).

A model file that is not present yet is rendered with ATLAS_MODEL_PRESENT=0, ATLAS_MODEL_FILE set to the directory plus
the FIRST pattern (systemd does not glob, so the unit fails with llama.cpp's own "failed to load model") and
ATLAS_MODEL_PATTERN='<the full model_file_pattern>' so the Phase 3 driver's diagnostic can print what it tried.

Usage:
    engine-env.py [--engines FILE] [--out DIR] [--models-dir DIR] [--slots-dir DIR] [--port-base N]
                  [--overrides FILE] [--key KEY ...] [--set-override KEY FIELD VALUE] [--print KEY]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

KV_TYPES = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}  # common/arg.cpp:304-314, VERIFIED
CHAT_MODES = {"chat", "vision"}
PLACEMENTS = {"gpu", "cpu"}  # D-14 (e); orchestrator atlas/config.py PLACEMENTS states the same set
# B-33, measure 5 (approved 2026-10-17; docstring "Prompt cache"): --cache-ram in MiB when an entry has no
# cache_ram_mib. The mirror is engines.json _meta cache_ram_rule.
CACHE_RAM_DEFAULT_MIB = {"chat": 4096, "vision": 4096, "embedding": 0, "reranking": 0}
# Generated by this script or reserved for the hf_download path; never accepted from engines.json "env" (see docstring).
# VERIFIED at b11474 (v0.4.0 pin facts): every name below is a real set_env() name in common/arg.cpp. The token and the
# API key are read from HF_TOKEN and LLAMA_API_KEY, which ENV_KEY_RE (LLAMA_ARG_* only) already refuses.
RESERVED_ENV = {
    "LLAMA_ARG_HOST", "LLAMA_ARG_PORT", "LLAMA_ARG_MODEL", "LLAMA_ARG_MMPROJ", "LLAMA_ARG_HF_REPO", "LLAMA_ARG_HF_FILE",
    "LLAMA_ARG_MODEL_URL", "LLAMA_ARG_API_KEY_FILE", "LLAMA_ARG_SSL_KEY_FILE", "LLAMA_ARG_SSL_CERT_FILE",
    "LLAMA_ARG_ALIAS", "LLAMA_ARG_API_PREFIX",
    # Fix round 2: file-loading and network-path options (docstring).
    "LLAMA_ARG_RPC", "LLAMA_ARG_MODELS_DIR", "LLAMA_ARG_MODELS_PRESET", "LLAMA_ARG_STATIC_PATH",
    "LLAMA_ARG_CHAT_TEMPLATE_FILE", "LLAMA_ARG_LOG_FILE",
    # v0.4.0 (pin facts): the real names v0.3.5 missed. Env is applied before argv, so none can undo a generated flag,
    # but each could add an option the command line lacks (a draft model, a Docker pull, the UI, the built-in tools).
    "LLAMA_ARG_SPEC_DRAFT_MODEL", "LLAMA_ARG_SPEC_DRAFT_HF_REPO", "LLAMA_ARG_DOCKER_REPO", "LLAMA_ARG_MMPROJ_URL",
    "LLAMA_ARG_UI", "LLAMA_ARG_UI_CONFIG", "LLAMA_ARG_UI_CONFIG_FILE", "LLAMA_ARG_UI_MCP_PROXY", "LLAMA_ARG_TOOLS",
    "LLAMA_ARG_TOOLS_RUNTIME", "LLAMA_ARG_AGENT", "LLAMA_ARG_MCP_SERVERS_CONFIG", "LLAMA_ARG_MCP_SERVERS_JSON",
    "LLAMA_ARG_VIDEO_FFMPEG_DIR",
    # The device list is engines.json "placement"'s alone (D-14 (e)): an env LLAMA_ARG_DEVICE=none would move a gpu
    # entry onto the CPU behind the Arbiter's back.
    "LLAMA_ARG_DEVICE",
    # The prompt-cache cap is engines.json "cache_ram_mib"'s alone (B-33, docstring "Prompt cache").
    "LLAMA_ARG_CACHE_RAM",
    # Defensive: no such env name at either pin (v0.3.5's guesses, or options with no env); refusing costs nothing.
    "LLAMA_ARG_HF_TOKEN", "LLAMA_ARG_API_KEY", "LLAMA_ARG_WEBUI", "LLAMA_ARG_MODEL_DRAFT", "LLAMA_ARG_SLOT_SAVE_PATH",
    "LLAMA_ARG_LORA", "LLAMA_ARG_CONTROL_VECTOR",
}
# Process variables llama.cpp reads with getenv() outside the LLAMA_ARG_* set, refused by name in "env" (with their own
# message, before the LLAMA_ARG_* rule) and inside any extra_args token (docstring "Hardening"). D-16 (approved
# 2026-10-17): LLAMA_ATTN_ROT_DISABLE=<non-zero> turns off the Hadamard rotation of every quantised KV cache at b11474
# (src/llama-kv-cache.cpp:315-318, VERIFIED), and a quantised cache without the rotation is the dangerous case. The
# mirror is engines.json _meta env_rule.
RESERVED_PROCESS_ENV = {"LLAMA_ATTN_ROT_DISABLE"}
# The same options as command-line flags; never accepted from engines.json "extra_args" (also matched as --flag=value).
RESERVED_FLAGS = {
    "--host", "--port", "-m", "--model", "-mu", "--model-url", "-hf", "-hfr", "--hf-repo", "-hfd", "-hfrd",
    "--hf-repo-draft", "-hff", "--hf-file", "-hfv", "-hfrv", "--hf-repo-v", "-hffv", "--hf-file-v", "--hf-token",
    "--api-key", "--api-key-file", "--slot-save-path", "--mmproj", "--mmproj-url", "--ssl-key-file", "--ssl-cert-file",
    "--webui", "--no-webui", "--alias", "-a", "--api-prefix",
    # Fix round 2 (docstring): remote offload, second models, router mode, static files, adapters, template, log file.
    "--rpc", "-md", "--model-draft", "--models-dir", "--models-preset", "--path", "--lora", "--lora-scaled",
    "--control-vector", "--control-vector-scaled", "--control-vector-layer-range", "--chat-template-file", "--log-file",
    # v0.4.0 (pin facts; both pins accept them, VERIFIED in common/arg.cpp at b11474): draft-model aliases, the token
    # alias, the Docker pull, the mmproj URL alias, the UI and its file and MCP proxy, the built-in tools (shell and
    # file writes) and their docker:/ssh: runtime, --agent (all tools plus the proxy), MCP servers, a media directory,
    # prompt logs written to disk, an ffmpeg directory, grammar/schema files and the lookup caches.
    "--spec-draft-model", "--spec-draft-hf", "-hft", "-dr", "--docker-repo", "-mmu", "--ui", "--no-ui",
    "--ui-config-file", "--webui-config-file", "--ui-mcp-proxy", "--webui-mcp-proxy", "--tools", "--tools-runtime",
    "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json", "--media-path", "--log-prompts-dir",
    "--video-ffmpeg-dir", "--grammar-file", "-jf", "--json-schema-file", "-lcs", "--lookup-cache-static", "-lcd",
    "--lookup-cache-dynamic",
    # The device list is engines.json "placement"'s alone (D-14 (e), docstring "Placement").
    "--device", "-dev",
    # The prompt-cache cap is engines.json "cache_ram_mib"'s alone (B-33, docstring "Prompt cache").
    "--cache-ram", "-cram",
}


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
    if any(ch.isspace() for ch in token) or "'" in token or '"' in token or "\\" in token:
        die(f"{key}: argument {token!r} contains whitespace, a quote or a backslash; systemd cannot pass it via $ARGS "
            "(use an LLAMA_ARG_* entry under \"env\" in engines.json instead)")
    return token


ENV_KEY_RE = re.compile(r"^LLAMA_ARG_[A-Z0-9_]+$")
# The overrides.json fields this script reads (docstring "Overrides"); S-22 retired "coresident".
OVERRIDE_FIELDS = {"kv_type", "ctx_size", "parallel", "n_keep"}


def env_lines(eng: dict[str, Any]) -> list[str]:
    """Extra KEY='value' lines from the engine's "env" map (JSON-valued llama-server options)."""
    key = eng["key"]
    extra = eng.get("env") or {}
    if not isinstance(extra, dict):
        die(f"{key}: env must be an object of LLAMA_ARG_* keys")
    lines: list[str] = []
    for name, value in extra.items():
        if str(name) in RESERVED_PROCESS_ENV:
            die(f"{key}: env key {name!r} is refused: it turns off the KV-cache rotation a quantised cache needs "
                "(D-16; see docstring)")
        if not ENV_KEY_RE.match(str(name)):
            die(f"{key}: env key {name!r} is not an LLAMA_ARG_* variable")
        if str(name) in RESERVED_ENV:
            die(f"{key}: env key {name!r} is reserved (generated here, or a model-download/bind option; see docstring)")
        lines.append(f"{name}={sh_quote(str(value))}")
    return lines


def check_extra_flag(token: str, key: str) -> str:
    """Refuse reserved flags in extra_args, in both `--flag value` and `--flag=value` spellings."""
    flag = token.split("=", 1)[0] if token.startswith("-") else token
    if any(name in token for name in RESERVED_PROCESS_ENV):
        die(f"{key}: extra_args may not name {token!r}: it turns off the KV-cache rotation a quantised cache needs "
            "(D-16; see docstring)")
    if flag in RESERVED_FLAGS:
        die(f"{key}: extra_args may not carry {token!r} (bind, model source, remote offload, file-loading, slot path, "
            "key, UI, device or prompt-cache flag; see docstring)")
    return token


def placement_of(eng: dict[str, Any]) -> str:
    """engines.json "placement" (docstring "Placement"): gpu by default, cpu for the D-14 (e) companions."""
    placement = str(eng.get("placement", "gpu"))
    if placement not in PLACEMENTS:
        die(f"{eng['key']}: placement {placement!r} is not one of {sorted(PLACEMENTS)}")
    if placement == "cpu" and eng.get("arbiter_class") != "resident":
        die(f"{eng['key']}: placement cpu is only for an always-resident companion (arbiter_class resident; D-14 (e))")
    return placement


def cache_ram_of(eng: dict[str, Any]) -> int:
    """engines.json "cache_ram_mib" (docstring "Prompt cache"): an integer of 0 or more, else the mode default."""
    key, mode = eng["key"], str(eng.get("mode", "chat"))
    if "cache_ram_mib" not in eng:
        if mode not in CACHE_RAM_DEFAULT_MIB:
            die(f"{key}: no cache_ram_mib and no default for mode {mode!r}; set cache_ram_mib (MiB, 0 or more)")
        return CACHE_RAM_DEFAULT_MIB[mode]
    value = eng["cache_ram_mib"]
    # bool is an int in Python; JSON true must not render as --cache-ram 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        die(f"{key}: cache_ram_mib {value!r} is not an integer of 0 or more (MiB; 0 disables the prompt cache, and "
            "-1, no limit, is refused: B-33)")
    return value


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
    placement = placement_of(eng)
    cache_ram = cache_ram_of(eng)
    # S-22 (docstring "No co-resident profile"): engines.json's own profile, whatever an old entry or overrides.json
    # says about a co-resident one.
    ctx_size = int(ov.get("ctx_size", eng["ctx_size"]))
    parallel = int(ov.get("parallel", eng["parallel"]))
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
        # Log verbosity 4 (S52 fix round; docstring "Log verbosity"): at the pin library INFO lines are TRACE
        # (common/log.cpp:532, since 67b2b7f2f "logs : reduce"), so at the default 3 the V4 proof lines and the S52
        # memory records' size and compute lines never reach the journal; V4 (phase3/loadtest.py prove_kv) and the S52
        # records (parse_memory_lines) read them. Exactly 4, never 5 or -v (docstring). A log level only.
        args += ["--verbosity", "4"]
        # Appendix B: `--slot-save-path /srv/atlas/data/slots`, the same directory for every engine (no per-key subdir).
        args += ["--slot-save-path", str(slots_dir)]
    if kv_type != "none":
        # Flash attention is the prerequisite for cache quantisation (Section 4.3); K and V always identical here.
        args += ["--flash-attn", "on", "--cache-type-k", kv_type, "--cache-type-v", kv_type]
    if placement == "cpu":
        # D-14 (e) (docstring "Placement"): no Vulkan device at all, so no layer offload either.
        args += ["--device", "none"]
    else:
        args += ["--n-gpu-layers", "all"]
    args += ["--no-webui", "--metrics"]
    # B-33 (docstring "Prompt cache"): an explicit host-RAM prompt-cache cap on every engine, never the 8192 default.
    args += ["--cache-ram", str(cache_ram)]
    args += [check_extra_flag(str(t), key) for t in eng.get("extra_args", [])]
    args = [check_token(t, key) for t in args]
    meta = {
        "mode": mode,
        "placement": placement,
        "kv_type": kv_type,
        "ctx_size": ctx_size,
        "parallel": parallel,
        "n_keep": n_keep,
        "cache_ram_mib": cache_ram,
    }
    return args, meta


def sh_quote(value: str) -> str:
    # Values are single-quoted so both systemd's EnvironmentFile parser and bash `source` read them verbatim (double
    # quotes inside single quotes are literal in both); check_token already refused quotes and backslashes inside ARGS.
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
    if meta["mode"] in CHAT_MODES and opts.mkdirs and not opts.print_key:
        # --slot-save-path refuses a missing directory (arg.cpp "not a directory", VERIFIED). Never in --print mode.
        # 0750: slot files are KV snapshots of the Principal's conversations, so no world traverse (fix round 2;
        # the unit's UMask=0077 makes the files themselves 0600).
        slots_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(slots_dir, 0o750)
            shutil.chown(slots_dir, "atlas", "atlas")
        except (LookupError, PermissionError):
            pass
    lines = [
        f"# {key}.env — rendered by phase2/engine-env.py from config/engines.json; edit overrides.json, not this file.",
        f"# {eng.get('display_name', key)}",
        f"ATLAS_ENGINE={sh_quote(key)}",
        f"ATLAS_MODE={sh_quote(meta['mode'])}",
        f"ATLAS_PLACEMENT={sh_quote(meta['placement'])}",
        f"ATLAS_ARBITER_CLASS={sh_quote(str(eng.get('arbiter_class', '')))}",
        f"ATLAS_KV_TYPE={sh_quote(meta['kv_type'])}",
        f"ATLAS_CTX_SIZE={meta['ctx_size']}",
        f"ATLAS_PARALLEL={meta['parallel']}",
        f"ATLAS_N_KEEP={meta['n_keep']}",
        f"ATLAS_KV_PROOF_LINES={int(eng.get('kv_proof_lines', 0))}",
        f"ATLAS_FOOTPRINT_GB={eng.get('footprint_gb', 0)}",
        f"ATLAS_MODEL_FILE={sh_quote(str(model_path))}",
        f"ATLAS_MODEL_PATTERN={sh_quote(patterns)}",
        f"ATLAS_MODEL_PRESENT={1 if present else 0}",
        "LLAMA_ARG_HOST=127.0.0.1",
        f"LLAMA_ARG_PORT={port}",
        *env_lines(eng),
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
                    help="store FIELD=VALUE for KEY in overrides.json before rendering "
                         "(kv_type, ctx_size, parallel, n_keep)")
    ap.add_argument("--clear-override", nargs=2, action="append", default=[], metavar=("KEY", "FIELD"))
    ap.add_argument("--print", dest="print_key",
                    help="print the rendered file for KEY to stdout instead of installing (read-only: no directories)")
    ap.add_argument("--no-mkdirs", dest="mkdirs", action="store_false", help="do not create the slot directory")
    opts = ap.parse_args()
    if opts.print_key:
        opts.mkdirs = False

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
        if field not in OVERRIDE_FIELDS:
            die(f"--set-override: unknown field {field!r} (S-22: coresident is gone with the co-resident profile)")
        parsed: Any = value
        if field in {"ctx_size", "parallel", "n_keep"}:
            parsed = int(value)
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
            pattern = eng.get("model_file_pattern") or "(derived from files[])"
            print(f"engine-env.py: {eng['key']}: model file not present yet under {opts.models_dir}/{eng['key']} "
                  f"(no file matching {pattern!r}; expected after Phase 3; ATLAS_MODEL_FILE holds the first pattern "
                  "literally and ATLAS_MODEL_PATTERN the full pattern); unit will refuse to start until it is",
                  file=sys.stderr)
    if not opts.print_key:
        print(f"engine-env.py: rendered {len(wanted)} env file(s) into {opts.out} ({changed} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
