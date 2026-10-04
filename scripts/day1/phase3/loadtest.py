#!/usr/bin/env python3
"""Phase 3 load tests: the measuring half of scripts/day1/phase3-models.sh (ATLAS_FRAMEWORK_REVIEW.md Section 17
Phase 3 steps 2-4; Section 21 V4, V10, V14b, V21, V22; Section 4.2 rules 1-9; R19).

Run as root by the driver. Stdout carries ONLY lines of the form
    RECORD<TAB><ID><TAB><pass|fail|deferred|info><TAB><message>
which the driver turns into verify.jsonl records with record_v; everything else goes to stderr (the unit's journal).
Exit status 0 means "measured and recorded" (a failed engine is a recorded fail, not an exit code); non-zero means an
infrastructure error the driver must stop on.

Subcommands (global options first, see main()):
    prepare                 unload every resident weight-bearing engine through the Arbiter (every answer granted:
                            its rule-5 check is the authority), poll the GTT counter ONCE against the earlier run's
                            baseline after the last one (a multi-resident counter is never attributed to a single
                            unload), settle, write baseline.json
    engine KEY [--previous PREV]
                            swap PREV out (unload + release check, credited to PREV) and KEY in through the Arbiter,
                            prove V4 from the journal, measure decode/prefill at 512 and 8k prompt tokens, register
                            the measured footprint with the Arbiter (rule 1), leave KEY resident.
                            DeepSeek (kv_ladder) runs the engines.json ladder with a coherence prompt instead.
                            A result whose engine is NOT resident afterwards (not loaded, failed, the ladder) says
                            so in "resident_after": the driver reads it to know what the next swap releases.
    finish [--previous PREV]
                            unload the last resident engine and credit its release check
    summarize               print the V10 lines (one per engine, then the summary the gate reads) and V22
    coresident --text KEY --vision KEY
                            Section 17 step 3: both resident (vision at parallel_coresident / ctx_size_coresident,
                            asserted on the rendered env before the load), GTT delta within [0.85 x 142 GB,
                            min(142 + 28 + 10 GB, the Arbiter's live budget)], two generation requests through the
                            orchestrator, the second proven to queue (V21), then the real-engine refusal and Deep
                            Think downgrade of Section 21 V14 (V14b). A rule-5 leak in its cleanup fails V21 AND
                            stops the phase (exit 2) after the records are written, as step 02 does.
    table                   the Section 17 step 4 table from the result files, plus every baseline_deviation

Verdicts fixed by the baseline (the document wins over this file and over engines.json, CONVENTIONS.md preamble):
  * Section 21 V10 "measured tok/s within the expected bands": a decode rate under the band's lower bound is a V10
    fail for that engine (no tolerance factor; a tolerance would have to be a declared baseline_deviation).
  * Section 23 S6 / engines.json known_issue (nemotron-3-super): the 8k prefill is followed by an alive check; a
    server that died is a V10 FAIL carrying the issue number #20732 in its message (engines.json: "fail when it does
    not" answer /health), never a hang and never a silent pass; alive with the request failed is a fail too.
  * Section 4.3 / Section 23 S2: the DeepSeek ladder loads f16, then q8_0, then q4_0, "running a coherence prompt at
    each rung", and keeps the LOWEST rung that loaded, proved its K/V types and answered coherently. Every rung is
    tried whatever the rung above did (a q8_0 that failed to load for memory does not hide a q4_0 that fits); only a
    missing model file or a rule-5 leak ends the ladder early. engines.json kv_ladder_rule items (3)/(4) still say
    "stops at the first failing rung": the document wins; its writer is asked to align the text (see notes).
  * Section 4.2 rule 3 for the requests this script sends straight to a llama-server port (POST /completion timing
    points, the coherence prompts, the solo timings): the driver stops every other path to a generation (the
    Sentinel/prune timers, atlas-celery-gpu, the atlas-openwebui container) for steps 02/03, and this script waits
    for the orchestrator's single generation slot to be free (GET /health "generating"/"generation_queue", api.py)
    before each such request and stops (Infra) when it is not free within GENERATION_IDLE_WAIT_S.

Result files: <results-dir>/<key>.json, one per engine (schema: EngineResult below), plus baseline.json and
coresident.json. A result with "ok": true AND "released": true makes the driver skip that engine on a re-run
(file-level resumability, CONVENTIONS.md §7.3; an engine saved while still resident is re-tested unless `prepare`
credited its release on the re-run). <results-dir>/pending-overrides.json journals every TEMPORARY overrides.json
change (ladder rungs, the co-resident vision profile, the V14b probe profile) before it is written; every subcommand
that touches the node restores and deletes it first, so an interrupted run never leaves a probe profile in production.
SIGTERM (`systemctl stop atlas-day1-phase3`) is turned into an exception in the main thread so every finally block runs.

Facts typed here and where they were VERIFIED (research/llama-cpp-vulkan.md, research/gguf-models.md, 2026-09-22;
the research files are cited through config/engines.json _meta where this checkout does not carry them):
  * GET /health is 503 while loading and 200 {"status":"ok"} once loaded (server README, server.cpp).
  * POST /completion and POST /v1/chat/completions carry a top-level "timings" object: cache_n, prompt_n, prompt_ms,
    prompt_per_second (prefill tok/s), predicted_n, predicted_ms, predicted_per_second (decode tok/s) (README).
  * POST /tokenize {"content": ...} -> {"tokens": [...]} (README).
  * The V4 proof line, one per attention sub-cache, in the unit's journal (research conflict h; llama-kv-cache.cpp):
        llama_kv_cache: size = ... K (q8_0): ... MiB, V (q8_0): ... MiB
    gpt-oss prints two (iswa), DeepSeek V4 four (dsv4); hybrids also print
        llama_memory_recurrent: size = ... R (f32): ... S (f32): ...      (expected, never a failure)
    plus "llama_context: flash_attn = enabled"; hard-error lines that mean the request was refused (server exits):
    "Flash Attention not supported, set to disabled", "quantized V cache requires flash_attn",
    "does not support different K", "does not divide n_embd_head". No ^ anchor: --log-timestamps may prefix lines.
    Research conflict 11: llama.cpp errors out at context creation instead of falling back; the proof is the log line
    plus the process-alive/health check.
  * /props does not expose the KV cache types (README): when the journal has no proof line, V4 is a fail with reason.
  * GTT in use: /sys/class/drm/card*/device/mem_info_gtt_used, bytes (amdgpu_gtt_mgr.c); the Arbiter's counter.
  * Nemotron 3 Super: llama.cpp issue #20732, GPU memory fault at ~20k-token prompts on Vulkan/HIP (conflict f).
  * DeepSeek V4: K and V types must be identical (llama-context.cpp), --parallel explicit (issue #26654), quantised
    KV disputed (issues #25382, #26423; conflict b); kernel 7.x DeviceLost needs amdgpu.lockup_timeout (issue #25664).
  * --reasoning-format defaults to auto (VERIFIED, engines.json extra_args_rule): a thinking model's <think> block
    lands in message.reasoning_content and message.content holds only what follows it.
  # UNVERIFIED: the per-request "cache_prompt": false field of POST /completion — README field (not re-read in the
    research); a nonce in every prompt and a check that timings.cache_n == 0 make the measurement independent of it.
  # UNVERIFIED: that DeepSeek V4 Flash 0731's chat template emits a <think> block by default — the reviewer's reading
    of the 0731 template; handled by budget (1024/2048 tokens), finish_reason and one retry, never assumed.
    "chat_template_kwargs": {"thinking": false} is NOT sent (unverified for this template).

CONTRACT with the orchestrator (orchestrator/src/atlas/api.py, another writer; read at fix-round time, every route
below exists there; CONVENTIONS.md §8 control path). Loopback only; admin routes take X-Atlas-Token when
ORCH_ADMIN_TOKEN_FILE is configured (--admin-token-file), else loopback is enough:
  * GET  {ORCH_URL}/health -> 200 when ready.
  * GET  {ORCH_URL}/arbiter/status -> 200 Arbiter.status(): gtt_used_bytes, budget_bytes, free_bytes,
         resident[{engine, projected_bytes, measured_bytes, ctx, parallel, kv_class, loading, unloading}], busy,
         generating {engine, task_id} | null, generation_queue [...], halted.
  * GET  {ORCH_URL}/health also carries "generating" (the holder of the single generation slot or null) and
         "generation_queue" (api.py health()); unauthenticated; the rule-3 idle check above reads it.
  * POST {ORCH_URL}/arbiter/load {"engine": KEY, "ctx": N, "parallel": N, "task_id": ...}
         -> 200 {"engine", "decision": "granted"|"queued"|"refused"|"error", "granted", "reason", "projected_bytes",
         "task_id"}. The Arbiter projects against the unit's env file (a ctx/parallel argument that disagrees with it
         is refused), plans LRU evictions (rule 3), waits load_wait_s behind a running generation (rules 4, 6), starts
         the unit through the sudoers path and returns granted only when it serves. 404 unknown engine, 503 halted
         after a rule-5 failure, 500 controller error. queued here means "still blocked after load_wait_s" (arbiter.py
         _plan_load: "arbiter busy: ...", "generation in progress on ...", "load of KEY already in progress"): the
         Arbiter's own wait IS the queue, so this script re-POSTs after QUEUED_RETRY_S (the ledger only tells it
         anything when another caller is loading this very engine, "already in progress", and then it is watched)
         until LOAD_TIMEOUT_S; refused is a real decision and fails the engine loudly.
  * POST {ORCH_URL}/arbiter/unload {"engine": KEY, "task_id": ...} -> the same shape; the rule-5 release check runs
         inside (503 when the counter did not drop: the Arbiter halts). Anything but 200/granted is a refusal: this
         script never stops a unit behind a serving Arbiter (Section 4.2, 9.3).
  * POST {ORCH_URL}/arbiter/register {"engine": KEY, "total_bytes": N, "task_id": ...} -> 200 (rule 1).
  * POST {ORCH_URL}/internal/v1/chat/completions {"model": <engine key>, "messages", "max_tokens", "stream": false}
         -> OpenAI shape; runs the internal pipeline through the Arbiter's load and generation lock (rules 3, 4);
         the llama-server "timings" object is passed through at the top level. /v1/chat/completions accepts engine
         keys too, but /internal is the route written for this script and is used here.
  * POST {ORCH_URL}/internal/deep-think/plan {"tier": "deep"|"standard"|"quick", "task_id"} -> {requested, granted,
         engines, required_bytes, budget_bytes, reason, resident_engine} (rule 8; the tier's largest engine is
         projected from its unit env file against the whole budget).
  There is NO systemctl fallback (fix round 3). Section 4.2 is a hard requirement ("every load and unload of any
  weight-bearing process passes" through the Arbiter) and Section 17 Phase 3 step 2 says "load through the Engine
  Arbiter": an orchestrator that is not serving is the missing Phase 2 step 2 prerequisite (CONVENTIONS.md §7.5) and an
  Infra error (§7.4). A unit that is active/activating but not yet 200 (Restart=on-failure, RestartSec=5, an
  ExecStartPre that waits up to 180 s for Redis) is waited for up to ORCH_WAIT_S, never worked around. When /health is
  200 but /arbiter/status or /arbiter/load|unload is missing, it REFUSES to run (Infra): engines are never controlled
  behind a serving orchestrator whose ledger would not see it. The admin header goes to /arbiter/* only; /internal/* is
  loopback-guarded and never sees the token.
"""

from __future__ import annotations

import argparse
import datetime as dt
import grp
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GIB = 1024**3
GB = 10**9
# Section 4.2 rule 5: release confirmation belongs to the Arbiter (its own release_tolerance_bytes and
# release_timeout_s, not exposed by /arbiter/status). The two figures below are this script's MEASUREMENT window
# (the "released"/seconds columns of the Section 17 step 4 table), polled after EVERY unload and after every unit
# that went away on its own (a failed load, a server that died at 8k): the process exit is never trusted (rule 5).
# The Arbiter's granted/503 answer is the authority and both are reported.
RELEASE_TOLERANCE_BYTES = 2 * GIB
RELEASE_TIMEOUT_S = 120.0
LOAD_TIMEOUT_S = 960.0  # llama-server@.service TimeoutStartSec=900 plus margin
ARBITER_WAIT_S = 900.0  # api.py DEFAULT_LOAD_WAIT_S: how long /arbiter/load|unload may block before "queued"
STOP_TIMEOUT_S = 180.0  # TimeoutStopSec=120 plus margin
ORCH_UNIT = "atlas-orchestrator"
ORCH_WAIT_S = 300.0  # atlas-orchestrator.service: ExecStartPre waits up to 180 s for Redis, TimeoutStartSec=300
ORCH_TRANSIENT_STATES = ("active", "activating", "reloading", "deactivating")  # systemd ActiveState: wait, do not fail
PENDING_FILE = "pending-overrides.json"
ENGINE_ENV_GROUP = "atlas"  # engine-env.py installs root:atlas 640 (CONVENTIONS.md §2)
REQUEST_TIMEOUT_S = 1200.0  # an 8k prefill on a dense 72B at Q8_0 plus 128 decoded tokens at ~3 tok/s
PREFILL_SHORT = 512
PREFILL_LONG = 8192
N_PREDICT = 128
# Section 21 V10 "measured tok/s within the expected bands" (Section 5.1 figures in engines.json): a decode rate
# under the band's lower bound is a V10 fail; there is no tolerance factor (one would be a baseline_deviation the
# gate table must show, engines.json baseline_deviation_rule). Above the band is informational.
QUEUED_RETRY_S = 15.0  # re-POST interval after an Arbiter "queued" answer (its own load_wait_s is the real queue)
SETTLE_DELTA_BYTES = 256 * 1024 * 1024  # two GTT readings 10 s apart closer than this = the counter has settled
GENERATION_IDLE_WAIT_S = 600.0  # Section 4.2 rule 3 belt: how long a direct request waits for the generation slot
RESIDENCY_CACHE_BYTES = 28 * GB  # Section 4.1/4.3: "~28 GB for both caches" beside the 142 GB pair
RESIDENCY_SLACK_BYTES = 10 * GB  # allocator overhead on top of the caches before the delta is out of tolerance
RESIDENCY_LOW_FACTOR = 0.85  # less than this fraction of the weights on the counter = the pair is not resident
DEFAULT_BUDGET_BYTES = 170 * GB  # Section 4.1 engine budget, used only when /arbiter/status carries no budget_bytes
TASK_ID = "phase3-loadtest"
UNIT_PREFIX = "llama-server@"
P3_CLASSES = {"core", "apex", "vision", "crosscheck"}
ORCH_CHAT_PATH = "/internal/v1/chat/completions"
LLAMA_CHAT_PATH = "/v1/chat/completions"
ADMIN_TOKEN_HEADER = "X-Atlas-Token"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
NEMOTRON_ISSUE_ID = "#20732"  # engines.json known_issue of nemotron-3-super names it; Section 23 S6 rules on it
NEMOTRON_ISSUE = f"llama.cpp issue {NEMOTRON_ISSUE_ID} (Vulkan/HIP memory fault at ~20k-token prompts)"
DEVICELOST_ISSUE = "llama.cpp issue #25664 (kernel 7.x amdgpu lockup_timeout; Phase 1 GRUB line)"
DEEPSEEK_KV_ISSUES = "llama.cpp issues #25382/#26423"
COHERENCE_TOKENS_FACTUAL = 1024
COHERENCE_TOKENS_SUMMARY = 2048
COHERENCE_RETRY_FACTOR = 4
V14B_MAX_DOUBLINGS = 8

KV_LINE_RE = re.compile(r"llama_kv_cache: size = .*K \((\w+)\): .*V \((\w+)\):")
RECURRENT_RE = re.compile(r"llama_memory_recurrent: size = .*R \((\w+)\): .*S \((\w+)\):")
FA_RE = re.compile(r"llama_context: flash_attn\s*= (\w+)")
KV_REFUSED = (
    "Flash Attention not supported, set to disabled",
    "quantized V cache requires flash_attn",
    "does not support different K",
    "does not divide n_embd_head",
)
EXTERNAL_STOP_MARKS = ("Received SIGTERM", "Stopping ", "Deactivated successfully", "Stopped ")

WORDS = (
    "atlas harbour ledger granite meadow copper lantern orbit velvet marble quartz ember willow falcon cinder timber "
    "saddle beacon canyon anchor prairie thistle summit glacier compass vellum saffron pewter juniper tundra barley "
    "mortar plaster gable rafter lintel keystone terrace paddock furrow hedgerow estuary fjord delta moraine scree "
    "basalt schist gneiss feldspar mica pumice obsidian gypsum shale loam silt clay peat humus lichen sphagnum sedge "
    "heron kestrel osprey plover curlew wren linnet finch grouse teal widgeon gannet petrel shearwater kittiwake "
    "clarinet oboe viola cello timpani marimba celesta bassoon piccolo cornet euphonium tuba harp lute zither "
    "ledgerline quaver crotchet minim semibreve cadence coda fugue motet canon rondo sonata toccata partita chaconne"
).split()


# --- logging and records ----------------------------------------------------------------------------------------------


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [phase3] loadtest {msg}", file=sys.stderr, flush=True)


def record(vid: str, result: str, msg: str) -> None:
    """One verify record for the driver (record_v). Tabs and newlines would break the line protocol."""
    clean = " ".join(str(msg).split())
    print(f"RECORD\t{vid}\t{result}\t{clean}", flush=True)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def short(obj: Any, n: int = 300) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return " ".join(text.split())[:n]


class Infra(RuntimeError):
    """An infrastructure error: the driver stops the phase (exit non-zero)."""


class Terminated(Infra):
    """SIGTERM (`systemctl stop atlas-day1-phase3`, the documented way to abort): raised in the main thread by the
    handler installed in main() so every `finally` (override restores, pending journal) runs before exit 143."""


def _on_sigterm(signum: int, _frame: Any) -> None:
    raise Terminated(f"signal {signum} (SIGTERM, e.g. systemctl stop atlas-day1-phase3): stopping, restoring temporary "
                     "overrides; re-run to resume")


# --- HTTP (loopback only, never through the allowlist proxy) ----------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HttpFail(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: str = "") -> None:
        super().__init__(msg)
        self.status = status
        self.body = body


def http(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 30.0,
         headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """Returns (status, parsed JSON or text). Raises HttpFail on transport errors; HTTP errors are returned."""
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise HttpFail(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def health_code(port: int) -> int:
    try:
        status, _ = http("GET", f"http://127.0.0.1:{port}/health", timeout=5)
    except HttpFail:
        return 0
    return status


def require_loopback(url: str) -> None:
    """CONVENTIONS.md §8: the orchestrator binds 127.0.0.1; engine control and generation never leave the host."""
    host = urllib.parse.urlparse(url).hostname
    if host not in LOOPBACK_HOSTS:
        raise Infra(f"--orch-url must be loopback (CONVENTIONS.md §8), got {url!r}")


# --- sysfs GTT counter (llama-cpp-vulkan.md §5.2) ---------------------------------------------------------------------


def drm_root() -> Path:
    """ATLAS_DRM_ROOT is a test-only hook (CONVENTIONS.md §4: node paths are overridable for tests). On the node
    (ATLAS_ETC is the real /etc/atlas) it is refused: the GTT counter is the rule-5 measurement behind every
    'released' verdict and the V21 proof, and a fake tree must never reach it through the environment."""
    override = os.environ.get("ATLAS_DRM_ROOT")
    # Canonical compare: "/etc/atlas/" or "/etc//atlas" still point at the real config (fix round 3, minor).
    if override and os.path.realpath(os.environ.get("ATLAS_ETC", "/etc/atlas")) == "/etc/atlas":
        raise Infra("ATLAS_DRM_ROOT is a test-only hook; unset it on the node (ATLAS_ETC is /etc/atlas)")
    return Path(override or "/sys/class/drm")


def gpu_device_dir() -> Path:
    root = drm_root()
    for c in sorted(root.glob("card*")):
        if not re.fullmatch(r"card\d+", c.name):
            continue
        vendor = c / "device" / "vendor"
        try:
            if vendor.read_text().strip() == "0x1002":
                return c / "device"
        except OSError:
            continue
    raise Infra(f"no AMD GPU (vendor 0x1002) under {root}")


def gtt_used_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_used").read_text().strip())


def gtt_total_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_total").read_text().strip())


def fmt_gb(b: float) -> str:
    return f"{b / GB:.1f} GB"


# --- systemd helpers --------------------------------------------------------------------------------------------------


def run(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """A timed-out command comes back as exit 124 with the reason in stderr (callers treat non-zero as a recorded
    failure), never as an uncaught TimeoutExpired traceback that would stop the whole phase."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return subprocess.CompletedProcess(cmd, 124, out, f"{cmd[0]} timed out after {timeout:.0f}s")


def orchestrator_state() -> str:
    """systemd ActiveState of atlas-orchestrator.service ("unknown" when systemctl itself fails)."""
    p = run(["systemctl", "show", "-p", "ActiveState", "--value", ORCH_UNIT])
    return p.stdout.strip() or "unknown"


def unit_of(key: str) -> str:
    return f"{UNIT_PREFIX}{key}"


def unit_active(key: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit_of(key)]).returncode == 0


def unit_invocation_id(key: str) -> str:
    p = run(["systemctl", "show", "-p", "InvocationID", "--value", unit_of(key)])
    return p.stdout.strip()


def journal_lines(key: str, invocation_id: str, since: str) -> list[str]:
    """Log lines of the current invocation of llama-server@KEY (stderr goes to the journal)."""
    lines: list[str] = []
    if invocation_id:
        p = run(["journalctl", "--no-pager", "-o", "cat", f"_SYSTEMD_INVOCATION_ID={invocation_id}"], timeout=120)
        lines = p.stdout.splitlines()
    if not lines:
        p = run(["journalctl", "--no-pager", "-o", "cat", "-u", unit_of(key), "--since", since], timeout=120)
        lines = p.stdout.splitlines()
    return lines


def journal_tail(key: str, n: int = 40) -> str:
    p = run(["journalctl", "--no-pager", "-o", "cat", "-u", unit_of(key), "-n", str(n)], timeout=60)
    return p.stdout.strip()


def stopped_externally(tail: str) -> bool:
    """True when the journal tail shows systemd stopping the unit (an orchestrator restart stops every unit it does
    not own, api.py startup) rather than the server dying on its own: recorded as 'stopped externally', never as a
    GPU fault, and still a failure so the engine is re-tested on the next run."""
    return any(mark in tail for mark in EXTERNAL_STOP_MARKS)


# --- configuration ----------------------------------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    """KEY=value / KEY='value' lines as written by phase2/engine-env.py."""
    out: dict[str, str] = {}
    if not path.is_file():
        raise Infra(f"{path} missing: phase2/engine-env.py has not rendered this engine (Phase 2 step 1)")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def read_admin_token(path: str | None) -> str:
    """ORCH_ADMIN_TOKEN_FILE (api.py): `ORCH_ADMIN_TOKEN=...` or the bare token. Never logged."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        raise Infra(f"--admin-token-file {path} is not readable; the orchestrator's admin routes need it")
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ORCH_ADMIN_TOKEN="):
            line = line.split("=", 1)[1].strip().strip("'\"")
        return line
    raise Infra(f"--admin-token-file {path} is empty")


@dataclass
class Ctx:
    engines_path: Path
    env_dir: Path
    results_dir: Path
    orch_url: str
    engine_env: Path
    models_dir: Path
    slots_dir: Path
    port_base: int
    overrides: Path
    admin_token: str = ""
    engines: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def load_engines(self) -> None:
        data = json.loads(self.engines_path.read_text(encoding="utf-8"))
        for e in data["engines"]:
            self.engines[e["key"]] = e
            self.order.append(e["key"])

    def spec(self, key: str) -> dict[str, Any]:
        if key not in self.engines:
            raise Infra(f"{key} is not in {self.engines_path}")
        return self.engines[key]

    def env(self, key: str) -> dict[str, str]:
        return read_env(self.env_dir / f"{key}.env")

    def port(self, key: str) -> int:
        env = self.env(key)
        try:
            return int(env["LLAMA_ARG_PORT"])
        except (KeyError, ValueError) as exc:
            raise Infra(f"{key}.env has no LLAMA_ARG_PORT") from exc

    def render(self, key: str, set_ov: Sequence[tuple[str, str]] = (), clear_ov: Sequence[str] = ()) -> dict[str, str]:
        """Write overrides through engine-env.py (its contract) and re-render this engine's env file."""
        cmd = [sys.executable, str(self.engine_env), "--engines", str(self.engines_path), "--out", str(self.env_dir),
               "--models-dir", str(self.models_dir), "--slots-dir", str(self.slots_dir),
               "--port-base", str(self.port_base), "--overrides", str(self.overrides), "--key", key]
        for fld, val in set_ov:
            cmd += ["--set-override", key, fld, val]
        for fld in clear_ov:
            cmd += ["--clear-override", key, fld]
        p = run(cmd, timeout=120)
        if p.returncode != 0:
            raise Infra(f"engine-env.py failed for {key}: {(p.stderr or p.stdout).strip()[:600]}")
        path = self.env_dir / f"{key}.env"
        # Owner/mode belt, failing loudly like the driver's _p3_render_envs (rule §7.4): engine-env.py's install()
        # swallows a failed chown because the tests run unprivileged; on the node (root) it must hold.
        try:
            os.chmod(path, 0o640)
        except OSError as exc:
            raise Infra(f"could not set mode 640 on {path}: {exc}") from exc
        if os.geteuid() == 0:
            try:
                want_gid = grp.getgrnam(ENGINE_ENV_GROUP).gr_gid
            except KeyError as exc:
                raise Infra(f"group {ENGINE_ENV_GROUP!r} does not exist (Phase 1 creates it)") from exc
            st = path.stat()
            if (st.st_uid, st.st_gid) != (0, want_gid):
                try:
                    os.chown(path, 0, want_gid)
                except OSError as exc:
                    raise Infra(f"could not set root:{ENGINE_ENV_GROUP} on {path}: {exc}") from exc
        return self.env(key)

    def override_of(self, key: str, fld: str) -> Any:
        """The current overrides.json value of one field (None when absent), so a temporary override can be restored."""
        if not self.overrides.is_file():
            return None
        try:
            data = json.loads(self.overrides.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Infra(f"{self.overrides} is not valid JSON: {exc}") from exc
        return (data.get(key) or {}).get(fld)

    def result_path(self, key: str) -> Path:
        return self.results_dir / f"{key}.json"

    def core_keys(self) -> list[str]:
        return [k for k in self.order if self.engines[k].get("arbiter_class") in P3_CLASSES]


# --- results ----------------------------------------------------------------------------------------------------------


@dataclass
class EngineResult:
    key: str
    arbiter_class: str = ""
    control_mode: str = ""
    kv_requested: str = ""
    kv_applied: str = ""
    kv_proof_ok: bool = False
    kv_proof_msg: str = ""
    load_ok: bool = False
    load_error: str = ""
    load_s: float | None = None
    swap_s: float | None = None
    swap_note: str = ""
    generated: bool = False
    decode_tps_512: float | None = None
    decode_tps_8k: float | None = None
    prefill_tps_512: float | None = None
    prefill_tps_8k: float | None = None
    prefill_8k_note: str = ""
    crashed_at_8k: bool = False
    known_issue_note: str = ""  # the Section 23 S6 note with the issue number (#20732) recorded BESIDE the V10 fail
    stopped_externally: bool = False
    band_note: str = ""
    band_fail: str = ""
    footprint_bytes: int | None = None
    footprint_note: str = ""
    released: bool | None = None
    release_s: float | None = None
    release_note: str = ""
    warnings: list[str] = field(default_factory=list)
    ladder: list[dict[str, Any]] = field(default_factory=list)
    baseline_deviation: str = ""
    v22_result: str = ""
    v22_msg: str = ""
    resident_after: bool = False
    ok: bool = False
    tested_at: str = ""

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def save(self, ctx: Ctx) -> None:
        ctx.results_dir.mkdir(parents=True, exist_ok=True)
        p = ctx.result_path(self.key)
        p.with_suffix(".tmp").write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(p.with_suffix(".tmp"), p)


def load_result(ctx: Ctx, key: str) -> EngineResult | None:
    p = ctx.result_path(key)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    res = EngineResult(key=key)
    for k, v in data.items():
        if hasattr(res, k):
            setattr(res, k, v)
    return res


def read_baseline_optional(ctx: Ctx) -> int | None:
    p = ctx.results_dir / "baseline.json"
    if not p.is_file():
        return None
    return int(json.loads(p.read_text(encoding="utf-8"))["gtt_used_bytes"])


def read_baseline(ctx: Ctx) -> int:
    b = read_baseline_optional(ctx)
    if b is None:
        raise Infra(f"{ctx.results_dir / 'baseline.json'} missing: run `loadtest.py prepare` first (the driver does)")
    return b


# --- temporary overrides journal (CONVENTIONS.md §7.3 resumable, §7.4) ------------------------------------------------


def pending_path(ctx: Ctx) -> Path:
    return ctx.results_dir / PENDING_FILE


def pending_read(ctx: Ctx) -> list[dict[str, Any]]:
    p = pending_path(ctx)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Infra(f"{p} is not valid JSON: {exc}; the temporary overrides it journals must be restored by hand "
                    f"(engine-env.py --clear-override) before re-running") from exc
    return data if isinstance(data, list) else []


def pending_write(ctx: Ctx, entries: list[dict[str, Any]]) -> None:
    p = pending_path(ctx)
    if not entries:
        p.unlink(missing_ok=True)
        return
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    p.with_suffix(".tmp").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    os.replace(p.with_suffix(".tmp"), p)


def pending_begin(ctx: Ctx, key: str, fields: Sequence[str], why: str, clear_always: Sequence[str] = (),
                  ) -> dict[str, Any]:
    """Journal a TEMPORARY overrides.json change BEFORE it is written: the prior value of every field (restored on
    end), or "clear" for fields that are never a legitimate persistent override (coresident: the orchestrator decides
    co-residency live). A SIGKILL, OOM or power cut between the render and its restore then cannot leave a probe
    profile (a 256x ctx_size, coresident=true, an f16 ladder rung) in a production unit env file: pending_recover()
    restores it at the start of the next invocation."""
    entry: dict[str, Any] = {"id": f"{key}:{why}:{now_iso()}:{random.randrange(10**6)}", "key": key, "why": why,
                             "set": {}, "clear": []}
    for f in fields:
        prior = None if f in clear_always else ctx.override_of(key, f)
        if prior is None:
            entry["clear"].append(f)
        else:
            entry["set"][f] = prior
    entries = pending_read(ctx)
    entries.append(entry)
    pending_write(ctx, entries)
    return entry


def pending_restore(ctx: Ctx, entry: dict[str, Any]) -> dict[str, str]:
    set_ov = [(f, str(v)) for f, v in (entry.get("set") or {}).items()]
    return ctx.render(str(entry["key"]), set_ov, list(entry.get("clear") or []))


def pending_end(ctx: Ctx, entry: dict[str, Any]) -> None:
    entries = [e for e in pending_read(ctx) if e.get("id") != entry.get("id")]
    pending_write(ctx, entries)


def pending_recover(ctx: Ctx) -> None:
    """Called first by every subcommand that touches the node: restore what an interrupted run left behind, loudly."""
    entries = pending_read(ctx)
    if not entries:
        return
    for entry in reversed(entries):
        log(f"RECOVERY: {entry.get('key')} still carries the temporary override of an interrupted run "
            f"({entry.get('why')}; id {entry.get('id')}): restoring set={entry.get('set')} clear={entry.get('clear')}")
        env = pending_restore(ctx, entry)
        log(f"RECOVERY: {entry.get('key')} env restored (kv {env.get('ATLAS_KV_TYPE')}, ctx "
            f"{env.get('ATLAS_CTX_SIZE')}, parallel {env.get('ATLAS_PARALLEL')}, "
            f"coresident {env.get('ATLAS_CORESIDENT')})")
    pending_path(ctx).unlink(missing_ok=True)


# --- engine control: the orchestrator's Arbiter API (no fallback) -----------------------------------------------------


class Control:
    """Every load and unload goes through the Arbiter (Section 4.2, a hard requirement: "every load and unload of any
    weight-bearing process passes" through it; Section 17 Phase 3 step 2 "load through the Engine Arbiter"). There is
    NO systemctl fallback (fix round 3): an orchestrator that is not serving is the missing Phase 2 step 2 prerequisite
    (CONVENTIONS.md §7.5) and an Infra error (§7.4), never a reason to start units behind the ledger's back (api.py's
    startup would stop them as unowned, and V4/V10 would be recorded from loads the Arbiter never saw). A unit that is
    active/activating but not yet 200 (Restart=on-failure, RestartSec=5, an ExecStartPre that waits for Redis) is
    waited for up to ORCH_WAIT_S. A serving orchestrator whose Arbiter API is missing is an Infra error too."""

    mode = "orchestrator"  # the only mode; kept as a field because every result file and the step 4 table print it

    def __init__(self, orch_url: str, admin_token: str = "") -> None:
        self.orch_url = orch_url.rstrip("/")
        self.last_decision: dict[str, Any] = {}
        self.headers = {ADMIN_TOKEN_HEADER: admin_token} if admin_token else {}
        self._probe()

    # -- probe --
    def _health(self) -> tuple[int, str]:
        try:
            code, body = http("GET", f"{self.orch_url}/health", timeout=5)
        except HttpFail as exc:
            return 0, str(exc)
        return code, short(body, 160)

    def _probe(self) -> None:
        code, detail = self._health()
        state = orchestrator_state()
        if code != 200 and state in ORCH_TRANSIENT_STATES:
            log(f"control: {ORCH_UNIT} is {state} and GET /health answered {code or 'nothing'} ({detail}); waiting up "
                f"to {ORCH_WAIT_S:.0f}s for it to serve (Restart=on-failure; ExecStartPre waits for Redis)")
            deadline = time.monotonic() + ORCH_WAIT_S
            while code != 200 and time.monotonic() < deadline:
                time.sleep(5)
                code, detail = self._health()
                state = orchestrator_state()
                if state not in ORCH_TRANSIENT_STATES:
                    break
        if code != 200:
            raise Infra(f"{ORCH_UNIT} is not serving (unit {state}; GET {self.orch_url}/health -> "
                        f"{code or 'unreachable'}: {detail}): Phase 2 step 2 prerequisite (CONVENTIONS.md §7.5). Every "
                        "load passes through the Engine Arbiter (Section 4.2); there is no systemctl fallback. "
                        f"systemctl start {ORCH_UNIT}, then re-run")
        code, body = self._get("/arbiter/status")
        if code in (401, 403):
            raise Infra(f"GET /arbiter/status answered {code}: the orchestrator's admin routes need X-Atlas-Token "
                        "(pass --admin-token-file, the ORCH_ADMIN_TOKEN_FILE of orchestrator.env)")
        if code != 200 or not isinstance(body, dict):
            raise Infra(f"orchestrator {self.orch_url} is serving (/health 200) but GET /arbiter/status answered "
                        f"{code}: refusing to control engines behind it (Section 4.2); implement /arbiter/* (api.py)")
        for route in ("/arbiter/load", "/arbiter/unload", "/arbiter/register"):
            # FastAPI answers 405 for a GET on a POST-only route that exists and 404 when the route is absent.
            rc, _ = self._get(route)
            if rc == 404:
                raise Infra(f"orchestrator {self.orch_url} is serving without POST {route} (api.py); V10/V14b/V21 "
                            "cannot be proved behind it: implement the route")
        halted = body.get("halted")
        if halted:
            raise Infra(f"the Arbiter is halted ({halted}): every load is refused until a human looks (Section 4.2 "
                        f"rule 5); restart {ORCH_UNIT}.service after checking the GTT counter")
        log(f"control: orchestrator Arbiter API at {self.orch_url} (unit {state}; resident: "
            f"{[r.get('engine') for r in body.get('resident', [])]}, budget {fmt_gb(body.get('budget_bytes') or 0)})")

    def _hdr(self, path: str) -> dict[str, str]:
        """The admin credential goes to /arbiter/* only; /internal/* is loopback-guarded (api.py) and never needs it."""
        return self.headers if path.startswith("/arbiter/") else {}

    def _get(self, path: str) -> tuple[int, Any]:
        try:
            return http("GET", f"{self.orch_url}{path}", timeout=10, headers=self._hdr(path))
        except HttpFail as exc:
            raise Infra(f"GET {path} failed while the orchestrator answered /health: {exc}") from exc

    def post(self, path: str, body: dict[str, Any], timeout: float) -> tuple[int, Any]:
        """A POST to an admin/internal route; transport errors surface as HttpFail for the caller to record."""
        return http("POST", f"{self.orch_url}{path}", body, timeout=timeout, headers=self._hdr(path))

    def status(self) -> dict[str, Any] | None:
        try:
            code, body = http("GET", f"{self.orch_url}/arbiter/status", timeout=10, headers=self.headers)
        except HttpFail:
            return None
        return body if code == 200 and isinstance(body, dict) else None

    def resident_keys(self) -> list[str]:
        st = self.status() or {}
        return [str(r.get("engine")) for r in st.get("resident", [])
                if isinstance(r, dict) and not r.get("loading") and not r.get("unloading")]

    def _wait_resident(self, key: str, deadline: float) -> bool:
        """Rule 4/6: a queued load waits its turn; the ledger says when the engine serves."""
        while time.monotonic() < deadline:
            if key in self.resident_keys():
                return True
            time.sleep(5)
        return False

    # -- load --
    def load(self, key: str, ctx_size: int, parallel: int, port: int) -> float:
        """Load through the Arbiter and wait for /health 200. Returns the load time in seconds. Raises RuntimeError with
        the reason on failure (the caller records it); Infra only for broken tooling."""
        t0 = time.monotonic()
        body = {"engine": key, "ctx": ctx_size, "parallel": parallel, "task_id": TASK_ID}
        deadline = t0 + ARBITER_WAIT_S + LOAD_TIMEOUT_S
        while True:
            try:
                code, resp = self.post("/arbiter/load", body, timeout=ARBITER_WAIT_S + LOAD_TIMEOUT_S + 60)
            except HttpFail as exc:
                raise RuntimeError(f"POST /arbiter/load {key} failed: {exc} (no systemctl fallback: Section 4.2)"
                                   ) from exc
            if code == 503:
                raise RuntimeError(f"Arbiter halted, {key} not loaded: HTTP 503 {short(resp)} (Section 4.2 rule 5)")
            if code != 200 or not isinstance(resp, dict):
                raise RuntimeError(f"Arbiter did not load {key}: HTTP {code} {short(resp)}")
            self.last_decision = resp
            decision = resp.get("decision")
            reason = resp.get("reason", "")
            if decision == "granted":
                log(f"control: Arbiter granted {key}: {reason} "
                    f"(projected {fmt_gb(resp.get('projected_bytes') or 0)})")
                break
            if decision == "queued":
                # Rules 4 and 6: never a failure. api.py blocks load_wait_s inside the Arbiter's own queue and answers
                # queued as "retry later" (arbiter.py _plan_load: busy, a generating victim); nothing in the ledger
                # changes until the retry, so the retry re-enters that queue after a short sleep. Only "load of KEY
                # already in progress" (another caller loading this very engine) is a ledger wait.
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Arbiter kept {key} queued for {deadline - t0:.0f}s: {reason}")
                if "already in progress" in reason:
                    log(f"control: Arbiter queued {key}: {reason}; watching the ledger for it to serve")
                    if self._wait_resident(key, min(deadline, time.monotonic() + 300)):
                        break
                else:
                    log(f"control: Arbiter queued {key}: {reason}; asking again in {QUEUED_RETRY_S:.0f}s")
                    time.sleep(QUEUED_RETRY_S)
                continue
            raise RuntimeError(f"Arbiter {decision or 'answered without a decision'} for {key}: {reason} "
                               f"({short(resp, 200)})")
        health_deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < health_deadline:
            if health_code(port) == 200:
                secs = time.monotonic() - t0
                log(f"control: {key} healthy on 127.0.0.1:{port} after {secs:.1f}s (via the Arbiter)")
                return secs
            if not unit_active(key) and time.monotonic() - t0 > 10:
                raise RuntimeError(f"{unit_of(key)} is not active after the grant; journal: "
                                   f"{journal_tail(key, 20)[-800:]}")
            time.sleep(2)
        raise RuntimeError(f"{key} did not answer /health 200 within {LOAD_TIMEOUT_S:.0f}s")

    # -- unload --
    def unload(self, key: str) -> str:
        """Unload through the Arbiter (its rule-5 check inside). Returns the Arbiter's reason. Raises RuntimeError on
        any answer but granted: a refusal means the engine is busy (Section 9.3: never swapped out from under a
        session) and this script never stops a unit behind a serving Arbiter."""
        try:
            code, resp = self.post("/arbiter/unload", {"engine": key, "task_id": TASK_ID},
                                   timeout=ARBITER_WAIT_S + STOP_TIMEOUT_S + 60)
        except HttpFail as exc:
            raise RuntimeError(f"POST /arbiter/unload {key} failed: {exc}") from exc
        if code == 503:
            raise RuntimeError(f"Arbiter halted after unloading {key}: HTTP 503 {short(resp)} (Section 4.2 rule "
                               f"5: the GTT counter did not drop; restart {ORCH_UNIT}.service after looking)")
        if code != 200 or not isinstance(resp, dict):
            raise RuntimeError(f"Arbiter refused to unload {key}: HTTP {code} {short(resp, 200)}")
        self.last_decision = resp
        if resp.get("decision") != "granted":
            raise RuntimeError(f"Arbiter did not unload {key}: {resp.get('decision')}: {resp.get('reason', '')}")
        reason = str(resp.get("reason", ""))
        if unit_active(key) and "not resident" in reason:
            raise RuntimeError(f"{unit_of(key)} is active but the Arbiter does not list it ({reason}); a unit "
                               "started outside the Arbiter is never stopped behind it: stop it by hand or "
                               f"restart {ORCH_UNIT}.service (api.py startup stops unowned units)")
        log(f"control: Arbiter unloaded {key}: {reason}")
        return reason

    # -- register (rule 1) --
    def register(self, key: str, total_bytes: int) -> str:
        """POST /arbiter/register; returns "" on success or the reason it was not registered (the caller warns)."""
        try:
            code, resp = self.post("/arbiter/register", {"engine": key, "total_bytes": int(total_bytes),
                                                         "task_id": TASK_ID}, timeout=30)
        except HttpFail as exc:
            return f"POST /arbiter/register failed: {exc}"
        if code != 200:
            return f"POST /arbiter/register answered {code}: {short(resp, 200)}"
        return ""


def wait_release(baseline: int, label: str) -> tuple[bool, float, str]:
    """The measurement window of Section 4.2 rule 5 (see RELEASE_TOLERANCE_BYTES): poll the GTT counter until it
    is back within tolerance of the baseline."""
    t0 = time.monotonic()
    last = gtt_used_bytes()
    while True:
        last = gtt_used_bytes()
        if last <= baseline + RELEASE_TOLERANCE_BYTES:
            secs = time.monotonic() - t0
            note = f"GTT {fmt_gb(last)} (baseline {fmt_gb(baseline)}) after {secs:.0f}s"
            log(f"release {label}: {note}")
            return True, secs, note
        if time.monotonic() - t0 > RELEASE_TIMEOUT_S:
            note = (f"GTT still {fmt_gb(last)} after {RELEASE_TIMEOUT_S:.0f}s, baseline {fmt_gb(baseline)} "
                    f"+ {RELEASE_TOLERANCE_BYTES / GIB:.0f} GiB tolerance")
            log(f"release {label}: FAIL {note}")
            return False, time.monotonic() - t0, note
        time.sleep(2)


def settle_counter() -> int:
    """The GTT counter once it stopped moving: two readings 10 s apart within SETTLE_DELTA_BYTES (or RELEASE_TIMEOUT_S
    elapsed), so a baseline is never taken mid-release."""
    deadline = time.monotonic() + RELEASE_TIMEOUT_S
    prev = gtt_used_bytes()
    while True:
        time.sleep(10)
        cur = gtt_used_bytes()
        if abs(cur - prev) < SETTLE_DELTA_BYTES or time.monotonic() > deadline:
            return cur
        prev = cur


def clear_residents(ctl: Control, keys: Sequence[str], baseline: int | None, who: str,
                    ) -> tuple[bool, float, str, int]:
    """Unload several resident engines through the Arbiter and measure the release ONCE, against BASELINE, after the
    last one: with two engines resident the counter cannot be back at the baseline after the first unload, so a
    per-engine poll against it would report a leak that does not exist (fix round, major). Every /arbiter/unload must
    answer granted (its own rule-5 check is the authority; a refusal or 503 is an Infra error here: nothing is stopped
    behind the Arbiter). When the measurement window against BASELINE fails although every unload was granted, the
    baseline is treated as stale (the resident set grew since it was taken: voice containers, a desktop session, a
    Phase 2 model) and the settled counter is reported instead of a false leak.
    Returns (window_ok, seconds, note, settled_bytes); window_ok is False only in that stale-baseline case."""
    t0 = time.monotonic()
    reasons: list[str] = []
    for k in keys:
        log(f"{who}: {k} is resident; unloading it through the Arbiter")
        try:
            reasons.append(f"{k}: {ctl.unload(k)}")
        except RuntimeError as exc:
            raise Infra(f"{who}: cannot clear the resident engines: {exc}") from exc
    if not keys:
        return True, 0.0, "nothing was resident", settle_counter()
    label = "+".join(keys)
    if baseline is None:
        settled = settle_counter()
        return True, time.monotonic() - t0, (f"Arbiter release check passed for {label} ({'; '.join(reasons)}); no "
                                             f"earlier baseline to poll against; settled at {fmt_gb(settled)}"), settled
    ok, secs, note = wait_release(baseline, label)
    settled = settle_counter()
    if ok:
        return True, secs, f"{note} (measured once after {label}); Arbiter: {'; '.join(reasons)}", settled
    log(f"{who}: the counter did not come back to the earlier baseline {fmt_gb(baseline)} but the Arbiter's own "
        f"release check passed for {label}: treating the baseline as stale (settled at {fmt_gb(settled)})")
    return False, secs, (f"Arbiter release check passed for {label} ({'; '.join(reasons)}); stale baseline "
                         f"{fmt_gb(baseline)} vs settled {fmt_gb(settled)} ({note})"), settled


def require_generation_idle(orch_url: str, label: str) -> None:
    """Section 4.2 rule 3 ("exactly one may generate at any moment ... everywhere") for a request this script sends
    straight to a llama-server port, which the Arbiter's generation lock cannot see. The driver has stopped every
    other path to a generation (timers, atlas-celery-gpu, the Open WebUI container); this belt waits for the
    orchestrator's single generation slot to be free (GET /health "generating" / "generation_queue", api.py) and
    stops the phase when it is not within GENERATION_IDLE_WAIT_S: someone is using the assistant beside the tests."""
    deadline = time.monotonic() + GENERATION_IDLE_WAIT_S
    said = False
    while True:
        try:
            code, body = http("GET", f"{orch_url.rstrip('/')}/health", timeout=10)
        except HttpFail as exc:
            raise Infra(f"{label}: GET /health failed while checking the generation slot (rule 3): {exc}") from exc
        if code != 200 or not isinstance(body, dict):
            raise Infra(f"{label}: GET /health answered {code} while checking the generation slot (rule 3): "
                        f"{short(body, 200)}")
        gen, queue = body.get("generating"), body.get("generation_queue") or []
        if not gen and not queue:
            return
        if time.monotonic() > deadline:
            raise Infra(f"{label}: the orchestrator is still generating ({short(gen, 120)}; queue {short(queue, 120)}) "
                        f"after {GENERATION_IDLE_WAIT_S:.0f}s: something uses the assistant during the load tests "
                        "(Section 4.2 rule 3); stop it and re-run")
        if not said:
            log(f"{label}: waiting for the orchestrator's generation slot ({short(gen, 120)}) before a direct request "
                "(Section 4.2 rule 3)")
            said = True
        time.sleep(5)


# --- V4 proof ---------------------------------------------------------------------------------------------------------


def evaluate_kv_lines(key: str, expected: str, proof_lines: int, lines: list[str], build: str = "",
                      ) -> tuple[bool, str, str]:
    """Pure part of the V4 proof: (ok, message, applied type) from the journal lines of the current invocation."""
    kv = [m for m in (KV_LINE_RE.search(ln) for ln in lines) if m]
    rec = [m for m in (RECURRENT_RE.search(ln) for ln in lines) if m]
    fa = [m.group(1) for m in (FA_RE.search(ln) for ln in lines) if m]
    refused = [s for s in KV_REFUSED if any(s in ln for ln in lines)]
    if not kv:
        why = "journal of the current invocation has no 'llama_kv_cache: size =' line" if lines else \
            "journal of the current invocation is empty (journalctl access?)"
        return False, f"{key}: V4 unproven: {why}{build}" + (f"; refused: {refused}" if refused else ""), ""
    types = sorted({(m.group(1), m.group(2)) for m in kv})
    applied = types[0][0] if len(types) == 1 and types[0][0] == types[0][1] else ",".join(f"{k}/{v}" for k, v in types)
    ok = all(k == expected and v == expected for k, v in types) and not refused
    parts = [f"{len(kv)} llama_kv_cache line(s) K/V {applied}"]
    if proof_lines and len(kv) != proof_lines:
        parts.append(f"expected {proof_lines} line(s)")
    if fa:
        parts.append(f"flash_attn={fa[-1]}")
        if fa[-1] != "enabled":
            ok = False
    else:
        parts.append("no flash_attn line")
        ok = False
    if rec:
        parts.append(f"recurrent R/S {rec[-1].group(1)}/{rec[-1].group(2)} (expected f32, unaffected)")
    if refused:
        parts.append(f"refused: {refused}")
    verdict = f"{expected} applied" if ok else f"{expected} requested, NOT proven"
    return ok, f"{key}: {verdict} ({'; '.join(parts)})", applied


def prove_kv(key: str, expected: str, proof_lines: int, invocation_id: str, since: str, port: int,
             ) -> tuple[bool, str, str]:
    """(ok, message, applied type). Reads the journal of the current invocation; /props only for the reason text.
    journald commits stderr lines asynchronously, so a warm re-load can answer /health before the proof line is
    readable: three reads two seconds apart before the verdict."""
    lines: list[str] = []
    for attempt in range(3):
        lines = journal_lines(key, invocation_id, since)
        if any(KV_LINE_RE.search(ln) for ln in lines) or any(s in ln for s in KV_REFUSED for ln in lines):
            break
        if attempt < 2:
            time.sleep(2)
    build = ""
    if not any(KV_LINE_RE.search(ln) for ln in lines):
        try:
            code, props = http("GET", f"http://127.0.0.1:{port}/props", timeout=10)
            if code == 200 and isinstance(props, dict):
                build = f"; /props build_info={props.get('build_info')} (no cache-type field exists there)"
        except HttpFail:
            pass
    return evaluate_kv_lines(key, expected, proof_lines, lines, build)


# --- generation and measurement ---------------------------------------------------------------------------------------


def tokenize_count(port: int, text: str) -> int:
    code, body = http("POST", f"http://127.0.0.1:{port}/tokenize", {"content": text}, timeout=120)
    if code != 200 or not isinstance(body, dict) or "tokens" not in body:
        raise RuntimeError(f"/tokenize answered {code}: {str(body)[:200]}")
    return len(body["tokens"])


def make_prompt(port: int, target_tokens: int, nonce: str) -> tuple[str, int]:
    """A prompt of about target_tokens tokens: random words (never cached) sized by /tokenize in a few rounds."""
    rng = random.Random(nonce)
    words = [rng.choice(WORDS) for _ in range(int(target_tokens * 0.8) + 8)]
    text = f"{nonce} " + " ".join(words)
    n = tokenize_count(port, text)
    for _ in range(4):
        if abs(n - target_tokens) <= max(8, target_tokens // 50):
            break
        ratio = target_tokens / max(n, 1)
        want = max(4, int(len(words) * ratio))
        if want > len(words):
            words += [rng.choice(WORDS) for _ in range(want - len(words))]
        else:
            words = words[:want]
        text = f"{nonce} " + " ".join(words)
        n = tokenize_count(port, text)
    return text + "\n\nContinue this list of words:", n


def completion(port: int, prompt: str, n_predict: int) -> dict[str, Any]:
    """POST /completion; returns {"content", "timings", "error"}. Never raises on HTTP errors (the caller decides)."""
    body = {"prompt": prompt, "n_predict": n_predict, "temperature": 0, "cache_prompt": False, "stream": False}
    try:
        code, resp = http("POST", f"http://127.0.0.1:{port}/completion", body, timeout=REQUEST_TIMEOUT_S)
    except HttpFail as exc:
        return {"content": "", "timings": {}, "error": str(exc)}
    if code != 200 or not isinstance(resp, dict):
        return {"content": "", "timings": {}, "error": f"HTTP {code}: {str(resp)[:300]}"}
    return {"content": resp.get("content") or "", "timings": resp.get("timings") or {}, "error": ""}


def chat(url: str, model: str, messages: list[dict[str, str]], max_tokens: int, timeout: float = REQUEST_TIMEOUT_S,
         path: str = LLAMA_CHAT_PATH, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """POST an OpenAI chat request: to a llama-server directly (path /v1/chat/completions, the model field is
    ignored) or to the orchestrator (path /internal/v1/chat/completions, model = engine key, Arbiter lock)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "stream": False}
    t_send = time.monotonic()
    base = {"content": "", "reasoning": "", "finish": "", "timings": {}, "error": "", "t_send": t_send, "code": 0}
    try:
        code, resp = http("POST", f"{url}{path}", body, timeout=timeout, headers=headers)
    except HttpFail as exc:
        return {**base, "error": str(exc), "t_recv": time.monotonic()}
    t_recv = time.monotonic()
    if code != 200 or not isinstance(resp, dict):
        return {**base, "error": f"HTTP {code}: {str(resp)[:300]}", "t_recv": t_recv, "code": code}
    content, reasoning, finish = "", "", ""
    try:
        choice = resp["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        finish = choice.get("finish_reason") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return {**base, "content": content, "reasoning": reasoning, "finish": finish,
            "timings": resp.get("timings") or {}, "t_recv": t_recv, "code": code}


def measure_point(port: int, target: int, label: str) -> tuple[dict[str, Any], str]:
    """One timing point: prefill tok/s at ~target prompt tokens and decode tok/s for N_PREDICT tokens after it."""
    nonce = f"p3-{label}-{random.randrange(10**9)}"
    prompt, n = make_prompt(port, target, nonce)
    r = completion(port, prompt, N_PREDICT)
    if r["error"]:
        return r, f"{label}: request failed: {r['error'][:200]}"
    t = r["timings"]
    note = (f"{label}: prompt_n={t.get('prompt_n')} (target {target}, tokenized {n}) cache_n={t.get('cache_n')} "
            f"prefill {t.get('prompt_per_second', 0):.1f} tok/s, predicted_n={t.get('predicted_n')} "
            f"decode {t.get('predicted_per_second', 0):.1f} tok/s")
    if t.get("cache_n"):
        note += " (WARNING cache_n>0: prefill figure includes cached tokens)"
    return r, note


COHERENCE_PASSAGE = (
    "The water cycle describes how water moves between the oceans, the atmosphere and the land. Heat from the sun "
    "evaporates water from the sea surface, lakes and rivers, and plants release vapour through transpiration. The "
    "vapour rises, cools and condenses into clouds. When the droplets grow heavy enough they fall as rain, snow or "
    "hail. Some of that precipitation runs off into streams and rivers, some soaks into the soil to recharge "
    "groundwater, and some is stored for centuries in glaciers and ice sheets. Rivers carry the runoff back to the "
    "ocean, closing the loop. The cycle moves enormous amounts of energy: evaporation stores heat as latent energy, "
    "and condensation releases it, which drives storms and shapes regional climates. Human activity changes the "
    "cycle by paving land, pumping aquifers, building dams and warming the atmosphere, which holds more vapour and "
    "makes heavy rainfall more intense."
)
COHERENCE_KEYWORDS = ("water", "evaporat", "cloud", "rain", "ocean", "river", "vapour", "condens", "groundwater", "ice")


def chat_with_thinking_budget(port: int, model: str, messages: list[dict[str, str]], max_tokens: int,
                              ) -> tuple[dict[str, Any], str]:
    """One chat turn against a llama-server with a reasoning model in mind: when the answer is empty because the
    thinking budget ran out (finish_reason "length"), retry once with a larger budget rather than call it incoherent.
    Returns (response, note)."""
    r = chat(f"http://127.0.0.1:{port}", model, messages, max_tokens)
    if r["error"]:
        return r, ""
    if not r["content"].strip() and r["finish"] == "length":
        bigger = max_tokens * COHERENCE_RETRY_FACTOR
        r2 = chat(f"http://127.0.0.1:{port}", model, messages, bigger)
        if r2["error"]:
            return r2, ""
        return r2, (f"thinking budget of {max_tokens} tokens exhausted (reasoning {len(r['reasoning'])} chars); "
                    f"retried at {bigger}")
    return r, ""


def text_quality(a2: str) -> tuple[bool, str, dict[str, Any]]:
    """Garbage/repetition heuristics on a summary; (ok, reason, stats)."""
    words = re.findall(r"[A-Za-z']+", a2)
    stats: dict[str, Any] = {"words": len(words)}
    if len(words) < 20:
        return False, f"summary too short/empty ({len(words)} words): {a2[:80]!r}", stats
    non_ascii = sum(1 for ch in a2 if ord(ch) > 127) / max(len(a2), 1)
    if non_ascii > 0.15:
        return False, f"summary is mojibake ({non_ascii:.0%} non-ASCII): {a2[:80]!r}", stats
    uniq = len({w.lower() for w in words}) / len(words)
    stats["uniq"] = uniq
    if uniq < 0.3:
        return False, f"summary is repetitive (unique-word ratio {uniq:.2f}): {a2[:80]!r}", stats
    grams = [" ".join(words[i:i + 4]).lower() for i in range(len(words) - 3)]
    if grams and max(grams.count(g) for g in set(grams)) > 4:
        return False, f"summary repeats a 4-gram more than 4 times: {a2[:80]!r}", stats
    hits = sum(1 for k in COHERENCE_KEYWORDS if k in a2.lower())
    stats["hits"] = hits
    if hits < 3:
        return False, f"summary is off-topic ({hits} of {len(COHERENCE_KEYWORDS)} keywords): {a2[:80]!r}", stats
    return True, "", stats


def coherence_check(port: int, model: str) -> tuple[bool, str]:
    """A short factual question with a known answer plus a ~120-word summary; garbage or repetition fails. A reasoning
    model spends tokens in reasoning_content first, so the budgets are generous, an exhausted budget is retried once,
    and the quality checks fall back to reasoning_content when content is empty."""
    notes: list[str] = []
    r1, note = chat_with_thinking_budget(
        port, model, [{"role": "user", "content": "What is the capital of France? Answer with the city name only."}],
        COHERENCE_TOKENS_FACTUAL)
    if note:
        notes.append(note)
    if r1["error"]:
        return False, f"factual question failed: {r1['error'][:200]}"
    a1 = r1["content"].strip()
    if not a1:
        return False, (f"factual question produced no answer (finish={r1['finish'] or '?'}, reasoning "
                       f"{len(r1['reasoning'])} chars: {r1['reasoning'][:60]!r})")
    if "paris" not in a1.lower():
        return False, f"factual question wrong/garbled: {a1[:80]!r}"
    r2, note = chat_with_thinking_budget(
        port, model,
        [{"role": "user", "content": f"Summarise the following passage in about 120 words:\n\n{COHERENCE_PASSAGE}"}],
        COHERENCE_TOKENS_SUMMARY)
    if note:
        notes.append(note)
    if r2["error"]:
        return False, f"summary request failed: {r2['error'][:200]}"
    a2 = r2["content"].strip()
    source = "content"
    if not a2 and r2["reasoning"].strip():
        a2, source = r2["reasoning"].strip(), "reasoning_content (content empty)"
    ok, why, stats = text_quality(a2)
    if not ok:
        return False, why + (f" [{source}]" if source != "content" else "") + ("; " + "; ".join(notes) if notes else "")
    return True, (f"'{a1[:20]}' + {stats['words']}-word summary ({source}), {stats['hits']} keywords, unique ratio "
                  f"{stats['uniq']:.2f}" + ("; " + "; ".join(notes) if notes else ""))


# --- per-engine test --------------------------------------------------------------------------------------------------


def finalize_control(res: EngineResult, ctl: Control) -> None:
    """Record how the engine was controlled at save time (the step 4 table prints it)."""
    res.control_mode = ctl.mode


def credit_release(ctx: Ctx, key: str, ok: bool, secs: float, note: str) -> None:
    """The release check of the engine being swapped out belongs to that engine's result (V10 'released')."""
    res = load_result(ctx, key)
    if res is None:
        res = EngineResult(key=key, arbiter_class=ctx.spec(key).get("arbiter_class", ""))
    res.released, res.release_s, res.release_note, res.resident_after = ok, round(secs, 1), note, False
    if not ok:
        res.ok = False
    res.save(ctx)


def unload_and_release(ctl: Control, key: str, baseline: int) -> tuple[bool, float, str]:
    """Unload through Control and confirm the memory came back. A refusal is recorded as a failed release with the
    Arbiter's reason; nothing is stopped behind it."""
    try:
        reason = ctl.unload(key)
    except RuntimeError as exc:
        log(f"release {key}: FAIL {exc}")
        return False, 0.0, f"unload refused/failed: {exc}"
    ok, secs, note = wait_release(baseline, key)
    return ok, secs, f"{note}; Arbiter: {reason}"


def release_or_poll(ctl: Control, key: str, baseline: int) -> tuple[bool, float, str]:
    """Section 4.2 rule 5, always: an engine that is resident (unit active or in the ledger) is unloaded through the
    Arbiter and the counter polled; an engine whose unit already went away (a failed load, a server that died at 8k)
    still gets the counter polled, because the process exit is never trusted."""
    if unit_active(key) or key in ctl.resident_keys():
        return unload_and_release(ctl, key, baseline)
    ok, secs, note = wait_release(baseline, key)
    return ok, secs, f"unit not active, counter polled anyway: {note}"


def leak_message(ctx: Ctx, key: str, note: str) -> str:
    return (f"{key} did not release its memory ({note}): the phase stops here, as the Arbiter halts (Section 4.2 rule "
            f"5); recorded in {ctx.result_path(key)}. Check the GTT counter and the llama-server@ units, restart "
            f"{ORCH_UNIT}.service, then re-run (the engine is re-tested)")


def swap_out(ctx: Ctx, ctl: Control, previous: str | None, baseline: int) -> tuple[float, str]:
    """Unload the previous engine and confirm its memory came back, crediting the check to PREVIOUS. Returns (seconds,
    note). A release that fails is an Infra error: no further load (rule 5)."""
    t0 = time.monotonic()
    if not previous:
        return 0.0, "no previous engine"
    log(f"swap: releasing {previous}")
    ok, secs, note = release_or_poll(ctl, previous, baseline)
    credit_release(ctx, previous, ok, secs, note)
    if not ok:
        raise Infra(leak_message(ctx, previous, note))
    return time.monotonic() - t0, f"released {previous} ({note})"


def stop_and_release(ctx: Ctx, ctl: Control, key: str, baseline: int, res: EngineResult) -> None:
    """Release KEY (unload through the Arbiter, or poll the counter when its unit is already gone) into res."""
    ok, secs, note = release_or_poll(ctl, key, baseline)
    res.released, res.release_s, res.release_note = ok, round(secs, 1), note
    res.resident_after = unit_active(key) if not ok else False


def halt_if_leaked(ctx: Ctx, res: EngineResult) -> None:
    """Called after the result is saved and its records printed: a failed release stops the phase (rule 5)."""
    if res.released is False:
        raise Infra(leak_message(ctx, res.key, res.release_note))


def register_footprint(ctl: Control, res: EngineResult, baseline: int) -> None:
    """Section 4.2 rule 1: the measured footprint (GTT delta at the rendered ctx/parallel) replaces the Arbiter's
    estimate. Measured while the engine is resident, right after the timing points."""
    delta = max(0, gtt_used_bytes() - baseline)
    res.footprint_bytes = delta
    why = ctl.register(res.key, delta)
    if why:
        res.footprint_note = f"measured {fmt_gb(delta)}, NOT registered: {why}"
        res.warn(res.footprint_note)
    else:
        res.footprint_note = f"measured {fmt_gb(delta)}, registered with the Arbiter"
    log(f"{res.key}: footprint {res.footprint_note}")


def check_band(res: EngineResult, spec: dict[str, Any]) -> None:
    """Section 21 V10 'measured tok/s within the expected bands' (Section 5.1 figures in engines.json)."""
    band = spec.get("expected_decode_tok_s")
    if not band or res.decode_tps_512 is None:
        return
    lo, hi = float(band[0]), float(band[1])
    d = res.decode_tps_512
    if d < lo:
        # V10 fail, no tolerance: the baseline says "within the expected bands" and declares no deviation.
        res.band_fail = f"decode {d} tok/s below the expected band {band[0]}-{band[1]} (Section 5.1; V10 fail)"
    elif d > hi:
        res.band_note = f"decode {d} tok/s above the expected band {band[0]}-{band[1]} (informational)"
    else:
        res.band_note = f"decode {d} tok/s within the expected band {band[0]}-{band[1]}"


def run_measurements(ctx: Ctx, res: EngineResult, key: str, port: int, env: dict[str, str]) -> None:
    spec = ctx.spec(key)
    require_generation_idle(ctx.orch_url, f"{key} 512-token point")  # rule 3: a direct request to the port
    r, note = measure_point(port, PREFILL_SHORT, "512")
    log(f"{key}: {note}")
    if r["error"]:
        res.load_error = f"512-token request failed: {r['error'][:200]}"
        return
    t = r["timings"]
    res.prefill_tps_512 = round(float(t.get("prompt_per_second") or 0), 2)
    res.decode_tps_512 = round(float(t.get("predicted_per_second") or 0), 2)
    res.generated = bool(t.get("predicted_n")) and bool(r["content"].strip())
    if not res.generated:
        res.load_error = f"no generation at 512 tokens (predicted_n={t.get('predicted_n')}, content empty)"
        return
    check_band(res, spec)
    ctx_size = int(env.get("ATLAS_CTX_SIZE", spec["ctx_size"]))
    parallel = int(env.get("ATLAS_PARALLEL", spec.get("parallel", 1)))
    per_slot = ctx_size // max(parallel, 1)
    if per_slot < PREFILL_LONG + N_PREDICT + 256:
        res.prefill_8k_note = f"skipped: {per_slot} tokens per slot ({ctx_size}/{parallel}) cannot hold an 8k prompt"
        log(f"{key}: 8k point {res.prefill_8k_note}")
        return
    require_generation_idle(ctx.orch_url, f"{key} 8k point")
    r, note = measure_point(port, PREFILL_LONG, "8k")
    log(f"{key}: {note}")
    # Section 23 S6 (engines.json known_issue of nemotron-3-super names #20732): the 8k prefill test checks the server
    # is still alive afterwards (never a hang) and records the issue number. The verdict is engines.json's: "pass with
    # the '#20732 checked, server alive' note when /health answers, fail when it does not". Section 21 V10 wants load,
    # generate, swap and release at 8k as at 512: a server that is dead after 8k has not shown that, and its footprint
    # is never registered (rule 1), so for every engine a death at 8k is a V10 FAIL; Nemotron's carries the issue
    # number in the message (the S6 "warning with the issue number" is recorded beside the fail, not instead of it).
    known = NEMOTRON_ISSUE_ID in str(spec.get("known_issue") or "")
    if r["error"]:
        time.sleep(3)
        alive = unit_active(key) and health_code(port) == 200
        if not alive:
            tail_full = journal_tail(key, 30)
            tail = " | ".join(tail_full.splitlines()[-3:])[-300:]
            if stopped_externally(tail_full):
                res.stopped_externally = True
                res.prefill_8k_note = "stopped externally"
                res.load_error = (f"server stopped externally during the 8k prefill (systemd stop, e.g. an "
                                  f"atlas-orchestrator restart); journal: {tail}")
            else:
                res.crashed_at_8k = True
                lost = f" [{DEVICELOST_ISSUE}]" if "ErrorDeviceLost" in tail_full else ""
                if known:
                    res.prefill_8k_note = f"crashed ({NEMOTRON_ISSUE_ID} checked, server dead): V10 fail"
                    res.known_issue_note = (f"8k prefill crashed the server: {NEMOTRON_ISSUE}; Section 23 S6: the "
                                            "alive check ran and the server was found dead, not hung (the 512 point "
                                            "passed); engines.json known_issue: fail when /health does not answer")
                    res.warn(res.known_issue_note)
                    res.load_error = (f"8k prefill crashed the server ({NEMOTRON_ISSUE}; S6 alive check: dead); "
                                      f"journal: {tail}{lost}")
                    log(f"{key}: FAIL {res.load_error}")
                else:
                    res.prefill_8k_note = "crashed"
                    res.load_error = f"8k prefill crashed the server (died during the prefill); journal: {tail}{lost}"
        else:
            res.prefill_8k_note = f"request failed but server alive: {r['error'][:160]}"
            res.load_error = f"8k prefill request failed: {r['error'][:200]}"
        return
    t = r["timings"]
    res.prefill_tps_8k = round(float(t.get("prompt_per_second") or 0), 2)
    res.decode_tps_8k = round(float(t.get("predicted_per_second") or 0), 2)
    if known:
        res.prefill_8k_note = f"{NEMOTRON_ISSUE_ID} checked, server alive"  # engines.json known_issue wording


def test_engine(ctx: Ctx, key: str, previous: str | None) -> None:
    spec = ctx.spec(key)
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    baseline = read_baseline(ctx)
    res = EngineResult(key=key, arbiter_class=spec.get("arbiter_class", ""), control_mode=ctl.mode,
                       tested_at=now_iso(), baseline_deviation=str(spec.get("baseline_deviation") or ""))
    if spec.get("kv_ladder"):
        ladder_engine(ctx, ctl, key, previous, baseline, res)
        return
    # Step 2 measures the STAND-ALONE profile: a `coresident` override left by an interrupted step 3 would otherwise
    # register the 2-slot / 65536 profile as the vision engine's footprint (rule 1) and print it as its row.
    env = ctx.render(key, [], ["coresident"])
    res.kv_requested = env.get("ATLAS_KV_TYPE", spec.get("kv_class", ""))
    # The previous engine is released BEFORE the model-file check: an engine that never loads must not leave the
    # earlier one resident with the driver believing this one is (its --previous would then name the wrong engine and
    # the next release would be polled against the empty baseline with the real engine still loaded).
    t_swap = time.monotonic()
    _, res.swap_note = swap_out(ctx, ctl, previous, baseline)
    if env.get("ATLAS_MODEL_PRESENT") != "1":
        res.load_error = (f"ATLAS_MODEL_PRESENT=0 in {key}.env: model file {env.get('ATLAS_MODEL_FILE')} "
                          "not found (step 01)")
        res.resident_after = False
        finalize_control(res, ctl)
        res.save(ctx)
        record("V4", "fail", f"{key}: not loaded ({res.load_error})")
        return
    port = int(env["LLAMA_ARG_PORT"])
    proof_lines = int(env.get("ATLAS_KV_PROOF_LINES", spec.get("kv_proof_lines", 0)) or 0)
    since = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        res.load_s = round(ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), port), 1)
    except RuntimeError as exc:
        res.load_error = str(exc)
        if "ErrorDeviceLost" in res.load_error:
            res.load_error += f" [{DEVICELOST_ISSUE}]"
        log(f"{key}: LOAD FAILED: {res.load_error}")
        stop_and_release(ctx, ctl, key, baseline, res)  # rule 5: the counter is polled even when the unit is gone
        finalize_control(res, ctl)
        res.save(ctx)
        record("V4", "fail", f"{key}: not loaded ({res.load_error[:300]})")
        halt_if_leaked(ctx, res)
        return
    res.load_ok = True
    res.swap_s = round(time.monotonic() - t_swap, 1)
    if previous:
        log(f"{key}: swap from {previous} took {res.swap_s}s ({res.swap_note}; load {res.load_s}s)")
    inv = unit_invocation_id(key)
    res.kv_proof_ok, res.kv_proof_msg, res.kv_applied = prove_kv(key, res.kv_requested, proof_lines, inv, since, port)
    log(f"{key}: V4 {'PASS' if res.kv_proof_ok else 'FAIL'}: {res.kv_proof_msg}")
    record("V4", "pass" if res.kv_proof_ok else "fail", res.kv_proof_msg)
    run_measurements(ctx, res, key, port, env)
    if not res.load_error and not res.crashed_at_8k:
        register_footprint(ctl, res, baseline)
        res.resident_after = True  # the next engine's swap (or `finish`) measures this one's release
    else:
        if res.crashed_at_8k and not res.load_error:
            res.footprint_note = "not measured: the server died at 8k (see warnings)"
        stop_and_release(ctx, ctl, key, baseline, res)
    res.ok = (res.load_ok and res.generated and not res.load_error and not res.band_fail
              and not res.crashed_at_8k and res.released is not False)
    finalize_control(res, ctl)
    res.save(ctx)
    log(f"{key}: result ok={res.ok} decode512={res.decode_tps_512} decode8k={res.decode_tps_8k} "
        f"prefill512={res.prefill_tps_512} prefill8k={res.prefill_tps_8k} warnings={res.warnings}")
    halt_if_leaked(ctx, res)


# --- the DeepSeek KV ladder (engines.json kv_ladder_rule; Section 4.3; Section 23 S2; R19) ----------------------------


@dataclass
class LadderVerdict:
    winner: str
    v4_result: str
    v4_msg: str
    v22_result: str
    v22_msg: str
    set_ov: list[tuple[str, str]]
    clear_ov: list[str]
    note: str
    deviation: str
    summary: str


def rung_verdict(r: dict[str, Any]) -> str:
    if r.get("coherent") and r.get("kv_proof_ok"):
        return "coherent" + (" (measurement failed)" if r.get("measure_error") else "")
    if r.get("coherent"):
        return "coherent but KV unproven"
    return "incoherent" if r.get("load_ok") else "load failed"


def decide_ladder(key: str, ladder: Sequence[str], rungs: Sequence[dict[str, Any]], base_kv: str,
                  f16_cap: int | None, stop_reason: str, released: bool | None, release_note: str,
                  decode_512: float | None, prefill_512: float | None) -> LadderVerdict:
    """Pure decision of the ladder (Section 4.3; Section 23 S2; Section 21 V4/V22; R19; engines.json kv_ladder_rule
    where it agrees with the document). Every rung f16 -> q8_0 -> q4_0 is tried (a coherence prompt "at each rung");
    the winner is the LOWEST rung (last in list order) that loaded, printed its proof lines and answered coherently,
    whatever the rungs above it did (a q8_0 that failed to load for memory does not hide a q4_0 that fits):
      * q4_0 (the Section 4.3 target) -> V4 pass, overrides cleared (the rungs above were the path down);
      * q8_0 -> V4 pass with the note that q4_0 was refused or incoherent (issues #25382, #26423), written to overrides;
      * f16 only -> V4 DEFERRED (unquantised KV is exactly the fallback V4 exists to catch), kv_type=f16 and
        ctx_size=ctx_size_f16_cap written to overrides, V22 still passes with the note (S2);
      * no coherent rung at all -> V4 and V22 deferred with the reason (R19 keeps the phase unblocked);
      * a winner whose measurement failed (request error, death at 8k) is still the winner (a measurement failure is
        not incoherence) but V22 records deferred with that reason (R19).
    A rung the loop never reached (model file missing, a rule-5 leak ended the ladder) is listed as "not tried"."""
    summary = ", ".join(f"{r['kv']}={rung_verdict(r)}" for r in rungs)
    untried = [kv for kv in ladder if kv not in {r["kv"] for r in rungs}]
    if untried:
        summary += ", " + ", ".join(f"{kv}=not tried" for kv in untried)
    winners = [r for r in rungs if r.get("coherent") and r.get("kv_proof_ok")]
    if not winners:
        reason = stop_reason or f"no coherent rung ({summary})"
        return LadderVerdict(
            winner="", v4_result="deferred",
            v4_msg=f"{key}: no KV type proven coherent, V22 deferred (R19): {reason}",
            v22_result="deferred",
            v22_msg=f"DeepSeek V4 Flash deferred without blocking the phase (R19): {reason}; ladder: {summary}",
            set_ov=[], clear_ov=["kv_type", "ctx_size"], note="overrides cleared", deviation="", summary=summary)
    wrung = winners[-1]  # rungs are in list order, so the last winner is the lowest coherent rung
    winner = str(wrung["kv"])
    below = [r for r in rungs if ladder.index(r["kv"]) > ladder.index(winner)]
    detail = ", ".join(f"{r['kv']}={rung_verdict(r)}" for r in below) or "not reached"
    above_failed = [r for r in rungs if ladder.index(r["kv"]) < ladder.index(winner) and r not in winners]
    if above_failed:
        # The document keeps the lowest coherent setting; a failure above it is recorded, not a reason to stop.
        summary += "; note: rungs above the winner failed (" + ", ".join(
            f"{r['kv']}={rung_verdict(r)}" for r in above_failed) + "), the lowest coherent rung stands (Section 4.3)"
    base_rung = next((r for r in rungs if r["kv"] == base_kv), None)
    base_state = rung_verdict(base_rung) if base_rung else "not tried"
    measure_error = str(wrung.get("measure_error") or "")
    ok = released is not False and not measure_error
    perf = f"decode {decode_512} tok/s at 512, prefill {prefill_512} tok/s"
    if winner == base_kv:
        v = LadderVerdict(winner, "pass",
                          f"{key}: {winner} applied and coherent, the Section 4.3 target reached (ladder: {summary}; "
                          "overrides cleared)", "pass" if ok else "deferred", "", [], ["kv_type", "ctx_size"],
                          "baseline kept (overrides cleared)", "", summary)
    elif winner == "f16":
        cap = int(f16_cap) if f16_cap else None
        set_ov = [("kv_type", "f16")] + ([("ctx_size", str(cap))] if cap else [])
        note = f"f16 override written, ctx capped to {cap}" if cap else "f16 override written (no ctx_size_f16_cap)"
        deviation = (f"KV f16 instead of Section 4.3 q4_0: no quantised rung usable on this build ({detail}; "
                     f"{DEEPSEEK_KV_ISSUES}); {note}")
        v = LadderVerdict(winner, "deferred",
                          f"{key}: unquantised KV: every quantised rung below f16 failed ({detail}; "
                          f"{DEEPSEEK_KV_ISSUES}); {note} (ladder: {summary})",
                          "pass" if ok else "deferred", "", set_ov, [], note, deviation, summary)
    else:
        note = f"{winner} written to overrides.json"
        deviation = (f"KV {winner} instead of Section 4.3 q4_0: q4_0 {base_state} on this build "
                     f"({DEEPSEEK_KV_ISSUES})")
        v = LadderVerdict(winner, "pass",
                          f"{key}: {winner} applied (Section 4.3 setting {base_kv} {base_state}: {detail}; "
                          f"{DEEPSEEK_KV_ISSUES}); {note} (ladder: {summary})",
                          "pass" if ok else "deferred", "", [("kv_type", winner)], ["ctx_size"], note, deviation,
                          summary)
    v.v22_msg = (f"DeepSeek V4 Flash 0731 UD-Q4_K_XL loads and generates coherently with {winner} KV ({perf}; "
                 f"{v.note})" + (f"; unquantised KV, see V4 deferred ({DEEPSEEK_KV_ISSUES})" if winner == "f16" else "")
                 + (f"; deferred: measurement failed at {winner}: {measure_error[:200]} (R19)" if measure_error else "")
                 + (f"; deferred: {release_note} (R19)" if released is False else ""))
    return v


LADDER_RUNG_FIELDS = ("decode_tps_512", "decode_tps_8k", "prefill_tps_512", "prefill_tps_8k", "prefill_8k_note",
                      "crashed_at_8k", "footprint_bytes", "footprint_note", "band_fail", "band_note")


def ladder_engine(ctx: Ctx, ctl: Control, key: str, previous: str | None, baseline: int, res: EngineResult) -> None:
    """DeepSeek V4 Flash (research conflict b; Section 4.3; Section 23 S2): "Phase 3 loads f16, then q8_0, then q4_0,
    running a coherence prompt at each rung, and keeps the lowest coherent setting". EVERY rung is loaded, proved (V4
    lines) and asked the coherence prompt, whatever the rung above did: a q8_0 that fails to load (its KV at 32768 is
    about the bytes of the f16 rung at its 16384 cap) must not hide a q4_0 that fits, and the document says "at each
    rung". The winner is the lowest rung that loaded, proved and answered coherently (decide_ladder); a coherent rung
    whose measurement fails (request error, death at 8k) is not incoherence and still counts, with V22 deferred when it
    is the winner (R19). The f16 rung is the control that attributes an incoherent quantised rung to KV quantisation
    rather than to the build or the model. Only a missing model file or a rule-5 leak ends the ladder early (the leak
    stops the phase, Infra, after the result is saved). Every rung's overrides are journalled (pending-overrides.json)
    so an interrupted ladder cannot leave an f16 profile behind. engines.json kv_ladder_rule (3)/(4) still describe an
    early stop; the document wins (CONVENTIONS.md preamble) and its writer is asked to align the text."""
    spec = ctx.spec(key)
    ladder: list[str] = list(spec["kv_ladder"])
    f16_cap = spec.get("ctx_size_f16_cap")
    base_kv = spec.get("kv_class", "q4_0")
    proof_lines = int(spec.get("kv_proof_lines", 0) or 0)
    res.kv_requested = f"ladder {'->'.join(ladder)}"
    t_swap = time.monotonic()
    _, res.swap_note = swap_out(ctx, ctl, previous, baseline)
    swap_measured = False
    stop_reason = ""
    pending = pending_begin(ctx, key, ["kv_type", "ctx_size"], "DeepSeek KV ladder rungs")
    for kv in ladder:
        rung: dict[str, Any] = {"kv": kv, "load_ok": False, "coherent": False, "kv_proof_ok": False, "note": ""}
        res.ladder.append(rung)
        set_ov: list[tuple[str, str]] = [("kv_type", kv)]
        clear_ov: list[str] = ["coresident"]
        if kv == "f16" and f16_cap:
            set_ov.append(("ctx_size", str(int(f16_cap))))
        else:
            clear_ov.append("ctx_size")
        env = ctx.render(key, set_ov, clear_ov)
        if env.get("ATLAS_MODEL_PRESENT") != "1":
            stop_reason = f"model file {env.get('ATLAS_MODEL_FILE')} not found (step 01)"
            rung["note"] = stop_reason
            break
        port = int(env["LLAMA_ARG_PORT"])
        since = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"{key}: ladder rung {kv} (ctx {env.get('ATLAS_CTX_SIZE')}, parallel {env.get('ATLAS_PARALLEL')} explicit)")
        try:
            rung["load_s"] = round(ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), port), 1)
        except RuntimeError as exc:
            rung["note"] = f"load failed: {exc}"
            if "ErrorDeviceLost" in str(exc):
                rung["note"] += f" [{DEVICELOST_ISSUE}]"
            log(f"{key}: rung {kv} {rung['note']}; the next rung is still tried (Section 4.3: a coherence prompt at "
                "each rung, the lowest coherent setting is kept)")
            stop_and_release(ctx, ctl, key, baseline, res)  # rule 5: polled even when the unit never came up
            rung["released"] = res.released
            if res.released is False:
                stop_reason = f"memory not released after the failed {kv} rung: {res.release_note}"
                break
            continue
        rung["load_ok"] = True
        res.load_ok = True
        if not swap_measured:
            res.swap_s = round(time.monotonic() - t_swap, 1)
            res.load_s = rung["load_s"]
            swap_measured = True
        inv = unit_invocation_id(key)
        ok, msg, applied = prove_kv(key, kv, proof_lines, inv, since, port)
        rung["kv_proof_ok"], rung["kv_proof_msg"], rung["applied"] = ok, msg, applied
        log(f"{key}: rung {kv} proof {'ok' if ok else 'NOT ok'}: {msg}")
        require_generation_idle(ctx.orch_url, f"{key} rung {kv} coherence prompt")  # rule 3: direct to the port
        coherent, detail = coherence_check(port, key)
        rung["coherent"], rung["coherence"] = coherent, detail
        log(f"{key}: rung {kv} coherence {'ok' if coherent else 'FAIL'}: {detail}")
        won = coherent and ok
        if won:
            # Per-rung measurement into res (reset first so a rung never inherits the one above), copied into the rung.
            res.load_error, res.band_fail, res.band_note, res.crashed_at_8k, res.prefill_8k_note = "", "", "", False, ""
            res.footprint_bytes, res.footprint_note = None, ""
            run_measurements(ctx, res, key, port, env)
            if res.load_error:
                rung["measure_error"] = res.load_error  # a dead or failing server is never registered (rule 1)
            else:
                register_footprint(ctl, res, baseline)
            rung.update({f: getattr(res, f) for f in LADDER_RUNG_FIELDS})
        stop_and_release(ctx, ctl, key, baseline, res)
        rung["released"] = res.released
        if res.released is False:
            stop_reason = f"memory not released after the {kv} rung: {res.release_note}"
            break
        if not won:
            log(f"{key}: rung {kv} {rung_verdict(rung)}; the next rung is still tried (Section 4.3: a coherence "
                "prompt at each rung)")
        elif rung.get("measure_error"):
            log(f"{key}: rung {kv} is coherent and proven but its measurement failed ({rung['measure_error'][:160]}): "
                "it stands as a winner (V22 deferred if it is the lowest, R19); the next rung is still tried")
        else:
            log(f"{key}: rung {kv} coherent and proven: current winner; descending")
    standing = next((r for r in reversed(res.ladder) if r.get("coherent") and r.get("kv_proof_ok")), {})
    v = decide_ladder(key, ladder, res.ladder, base_kv, f16_cap, stop_reason, res.released, res.release_note,
                      standing.get("decode_tps_512"), standing.get("prefill_tps_512"))
    ctx.render(key, v.set_ov, v.clear_ov)
    pending_end(ctx, pending)
    # The result's figures are the winner's (the rung that stands), not the last rung tried.
    wrung = standing if v.winner else None
    for f in LADDER_RUNG_FIELDS:
        setattr(res, f, wrung.get(f) if wrung else EngineResult.__dataclass_fields__[f].default)
    res.load_error = ""
    res.kv_applied = v.winner
    res.kv_proof_ok = v.v4_result == "pass"
    res.kv_proof_msg = v.v4_msg
    res.generated = bool(v.winner)
    res.v22_result, res.v22_msg = v.v22_result, v.v22_msg
    if v.deviation:
        res.baseline_deviation = (res.baseline_deviation + " | " if res.baseline_deviation else "") + v.deviation
    res.ok = (bool(v.winner) and res.released is not False and not res.band_fail
              and not (wrung or {}).get("measure_error"))
    res.resident_after = False
    finalize_control(res, ctl)
    res.save(ctx)
    record("V4", v.v4_result, v.v4_msg)
    log(f"{key}: ladder result: {res.v22_result}: {res.v22_msg}")
    halt_if_leaked(ctx, res)


# --- prepare / finish / summarize / table -----------------------------------------------------------------------------


def cmd_prepare(ctx: Ctx, _args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    prev_baseline = read_baseline_optional(ctx)  # an earlier run's baseline: a resident engine's release is credited
    active = [k for k in ctx.core_keys() if unit_active(k)]
    ledger = ctl.resident_keys()
    foreign = [k for k in active if k not in ledger]
    if foreign:
        raise Infra(f"{foreign} active but not in the Arbiter's ledger (resident: {ledger}); a unit the Arbiter "
                    f"does not own is never stopped behind it: restart {ORCH_UNIT}.service (its startup "
                    "stops unowned units) or stop the unit by hand")
    to_unload = [k for k in ctx.core_keys() if k in ledger or k in active]
    # Every resident engine is unloaded through the Arbiter (each answer granted: its rule-5 check is the authority)
    # and the counter is polled ONCE against the earlier run's baseline after the last one: with two engines resident
    # (an interrupted step 03) a per-engine poll against the empty baseline would report a leak that does not exist.
    # A window that fails although every unload was granted means the earlier baseline is stale (the resident set
    # grew since); the fresh baseline below is what the tests use, and no false leak stops the phase.
    window_ok, secs, note, cur = clear_residents(ctl, to_unload, prev_baseline, "prepare")
    for k in to_unload:
        res = load_result(ctx, k)
        if res is not None and res.resident_after:
            # File-level resumability (CONVENTIONS.md §7.3): the engine passed in an interrupted run and was left
            # resident for the next swap to measure; this IS that measurement, credited so its V10 line can pass. With
            # several engines resident the measurement is cumulative and the note says so (never attributed to one).
            how = "cumulative after " + "+".join(to_unload) if len(to_unload) > 1 else "baseline of the earlier run"
            window = "window ok" if window_ok else "Arbiter release check passed, earlier baseline stale"
            credit_release(ctx, k, True, secs, f"credited by prepare on a re-run ({how}; {window}): {note}")
    total = gtt_total_bytes()
    log(f"prepare: baseline GTT used {fmt_gb(cur)} of {fmt_gb(total)} (residents only; control via {ctl.mode})")
    if cur > 40 * GB:
        log(f"prepare: WARNING baseline {fmt_gb(cur)} is far above the ~17 GB resident set of Section 4.1; "
            "something weight-bearing is still loaded outside the llama-server@ units")
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({
        "gtt_used_bytes": cur, "gtt_total_bytes": total, "measured_at": now_iso(), "control_mode": ctl.mode,
        "unloaded_first": to_unload}, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_engine(ctx: Ctx, args: argparse.Namespace) -> int:
    test_engine(ctx, args.key, args.previous)
    return 0


def cmd_finish(ctx: Ctx, args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    baseline = read_baseline(ctx)
    prev = args.previous
    if prev:
        log(f"finish: releasing the last engine {prev}")
        ok, secs, note = release_or_poll(ctl, prev, baseline)
        credit_release(ctx, prev, ok, secs, note)
        if not ok:
            raise Infra(leak_message(ctx, prev, note))
    for k in ctx.core_keys():
        if unit_active(k) or k in ctl.resident_keys():
            log(f"finish: {k} still resident (unexpected); unloading it")
            ok, secs, note = unload_and_release(ctl, k, baseline)
            credit_release(ctx, k, ok, secs, note)
            if not ok:
                raise Infra(leak_message(ctx, k, note))
    return 0


def fmt_num(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def v10_line(res: EngineResult, spec: dict[str, Any]) -> tuple[str, str]:
    """Section 21 V10 per engine: load, generate (512 and 8k), swap, release, tok/s within the band."""
    band = spec.get("expected_decode_tok_s")
    band_s = f"{band[0]}-{band[1]}" if band else "n/a"
    if not res.load_ok:
        return "fail", f"{res.key}: load FAILED ({res.load_error[:200]}) [control {res.control_mode or '?'}]"
    parts = [f"{res.key}: load ok (KV {res.kv_applied or res.kv_requested}, control {res.control_mode or '?'})",
             f"decode {fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)} tok/s at 512/8k (expected {band_s})",
             f"prefill {fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)} tok/s",
             f"swap {fmt_num(res.swap_s)}s",
             f"footprint {fmt_gb(res.footprint_bytes) if res.footprint_bytes is not None else '-'}",
             f"released {'yes' if res.released else ('NO' if res.released is False else '?')}"]
    if res.prefill_8k_note:
        parts.append(f"8k: {res.prefill_8k_note[:120]}")
    if res.band_fail:
        parts.append(f"BAND FAIL: {res.band_fail}")
    if res.warnings:
        parts.append("warn: " + " | ".join(w[:160] for w in res.warnings))
    if res.load_error:
        parts.append(f"error: {res.load_error[:200]}")
    # A death at 8k fails the engine; for nemotron-3-super the issue number rides in the message (known_issue_note,
    # Section 23 S6 / engines.json known_issue: "fail when it does not" answer /health).
    if res.crashed_at_8k and res.known_issue_note and NEMOTRON_ISSUE_ID not in "; ".join(parts):
        parts.append(f"S6: {res.known_issue_note[:160]}")
    ok = (res.generated and not res.load_error and not res.crashed_at_8k and not res.stopped_externally
          and not res.band_fail and res.released is True)
    return ("pass" if ok else "fail"), "; ".join(parts)


def cmd_summarize(ctx: Ctx, _args: argparse.Namespace) -> int:
    failed: list[str] = []
    warned: list[str] = []
    missing: list[str] = []
    off_band: list[str] = []
    apex_lines: list[str] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        res = load_result(ctx, key)
        is_apex = spec.get("arbiter_class") == "apex"
        if res is None:
            if is_apex:
                record("V10", "deferred", f"{key}: no ladder result [Apex: see V22]")
                record("V22", "deferred", f"{key}: no ladder result recorded (R19)")
                apex_lines.append(f"{key}=deferred")
            else:
                missing.append(key)
                record("V10", "fail", f"{key}: no load-test result (step 02 did not reach it)")
            continue
        result, msg = v10_line(res, spec)
        if is_apex:
            # R19: the Apex engine has its own row (V22); its V10 line is informational and never fails the summary.
            record("V10", "pass" if result == "pass" else "deferred", msg + " [Apex: see V22]")
            apex_lines.append(f"{key}={res.v22_result or 'deferred'}")
            record("V22", res.v22_result or "deferred", res.v22_msg or f"{key}: no ladder result recorded (R19)")
            continue
        record("V10", result, msg)
        if result != "pass":
            failed.append(key)
        elif res.warnings:
            warned.append(key)
        if res.band_fail or "under the expected band" in res.band_note:
            off_band.append(key)
    tested = [k for k in ctx.core_keys() if ctx.spec(k).get("arbiter_class") != "apex"]
    if failed or missing:
        record("V10", "fail", f"summary: {len(failed) + len(missing)} of {len(tested)} engines failed "
               f"({', '.join(failed + missing)}); off-band: {', '.join(off_band) or 'none'}; "
               f"warns: {', '.join(warned) or 'none'}; apex: {', '.join(apex_lines) or '-'}")
    else:
        bands = "within the expected bands" if not off_band else f"tok/s under band for {', '.join(off_band)}"
        record("V10", "pass", f"summary: all {len(tested)} engines load, generate, swap and release memory at their "
               f"fixed quantisations, {bands}; warns: {', '.join(warned) or 'none'}; "
               f"apex: {', '.join(apex_lines) or '-'}")
    return 0


def cmd_table(ctx: Ctx, _args: argparse.Namespace) -> int:
    cols = ("engine", "load", "KV type", "decode 512/8k", "prefill 512/8k", "swap s", "footprint", "released",
            "control", "notes")
    rows: list[tuple[str, ...]] = []
    deviations: list[tuple[str, str]] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        res = load_result(ctx, key)
        if spec.get("baseline_deviation"):
            deviations.append((key, str(spec["baseline_deviation"])))
        if res is None:
            rows.append((key, "-", "-", "-", "-", "-", "-", "-", "-", "not tested"))
            continue
        if res.baseline_deviation and res.baseline_deviation != str(spec.get("baseline_deviation") or ""):
            deviations.append((key, res.baseline_deviation))
        notes = []
        if res.arbiter_class == "apex":
            notes.append(f"V22 {res.v22_result or '?'}")
        if res.band_fail:
            notes.append("BELOW BAND")
        if res.warnings:
            notes.append("warn")
        if res.prefill_8k_note:
            notes.append(f"8k {res.prefill_8k_note.split(':')[0]}")
        if res.known_issue_note:
            notes.append(f"S6 {NEMOTRON_ISSUE_ID} (fail)")
        if res.load_error:
            notes.append(res.load_error[:40])
        rows.append((key, "ok" if res.load_ok else "FAIL", res.kv_applied or res.kv_requested or "-",
                     f"{fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)}",
                     f"{fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)}", fmt_num(res.swap_s),
                     fmt_gb(res.footprint_bytes) if res.footprint_bytes is not None else "-",
                     "yes" if res.released else ("NO" if res.released is False else "?"),
                     res.control_mode or "?", "; ".join(notes) or "-"))
    widths = [max(len(str(r[i])) for r in [cols, *rows]) for i in range(len(cols))]
    for r in [cols, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)), file=sys.stderr)
    co = ctx.results_dir / "coresident.json"
    if co.is_file():
        d = json.loads(co.read_text(encoding="utf-8"))
        print(f"two-residency: {d.get('summary', '-')}", file=sys.stderr)
    # engines.json baseline_deviation_rule: every deviation from a closed value is printed here, never only in the file.
    print("Baseline deviations (engines.json baseline_deviation_rule):" if deviations else
          "Baseline deviations: none", file=sys.stderr)
    for key, text in deviations:
        print(f"  {key}: {text}", file=sys.stderr)
    return 0


# --- two-residency (Section 17 step 3; V21) and the real-engine Arbiter proof (V14b) ---------------------------------


def queue_proof(a: dict[str, Any], b: dict[str, Any], solo_second_s: float) -> tuple[bool, str]:
    """Pure V21 arithmetic: did the second request start only after the first finished? With timings passed through,
    the second's busy time (prompt_ms + predicted_ms) is subtracted from its receive time to find when it started;
    without timings, its wall clock must cover the first's wall clock plus most of its own solo duration."""
    ta, tb = a.get("timings") or {}, b.get("timings") or {}
    wall_a, wall_b = a["t_recv"] - a["t_send"], b["t_recv"] - b["t_send"]
    if tb.get("prompt_ms") is not None and tb.get("predicted_ms") is not None:
        busy_b = (float(tb["prompt_ms"]) + float(tb["predicted_ms"])) / 1000.0
        start_b = b["t_recv"] - busy_b
        gap = start_b - a["t_recv"]
        ok = gap >= -1.0
        busy_a = (float(ta.get("prompt_ms", 0)) + float(ta.get("predicted_ms", 0))) / 1000.0 if ta else None
        proof = (f"timings: second started {gap:+.1f}s relative to the first's completion "
                 f"(second busy {busy_b:.1f}s of {wall_b:.1f}s wall; first {wall_a:.1f}s wall"
                 + (f", first busy {busy_a:.1f}s" if busy_a is not None else "") + ")")
        return ok, proof
    ok = wall_b >= wall_a + 0.8 * solo_second_s - 1.0
    proof = (f"wall-clock (no timings passed through): second took {wall_b:.1f}s vs {solo_second_s:.1f}s solo while "
             f"the first took {wall_a:.1f}s")
    return ok, proof


def residency_bounds(weights_bytes: int, budget_bytes: int) -> tuple[int, int]:
    """Pure V21 window for the GTT delta of the resident pair (Section 17 step 3 "confirm both hold at ~142 GB"):
    at least RESIDENCY_LOW_FACTOR of the weights (less means one engine is not really resident), at most the weights
    plus the ~28 GB of both caches (Section 4.1/4.3) plus allocator slack, CAPPED at the live engine budget: a pair
    that overflowed the ~170 GB budget can never pass the residency half of V21."""
    low = int(RESIDENCY_LOW_FACTOR * weights_bytes)
    high = min(weights_bytes + RESIDENCY_CACHE_BYTES + RESIDENCY_SLACK_BYTES, int(budget_bytes))
    return low, high


def pick_probe_key(ctx: Ctx, exclude: Sequence[str]) -> str:
    """The engine whose unit profile is rendered over budget for the V14b proof: nemotron-3-super by preference (the
    Deep Think adversary engine, so one over-budget profile makes both the standard and the deep tier not fit), else
    the first non-Apex core engine that is not part of the pair."""
    cands = [k for k in ctx.core_keys() if k not in exclude and ctx.spec(k).get("arbiter_class") != "apex"]
    if "nemotron-3-super" in cands:
        return "nemotron-3-super"
    if not cands:
        raise Infra("no engine left for the V14b over-budget proof (engines.json lists only the pair?)")
    return cands[0]


def arbiter_refusal_proof(ctx: Ctx, ctl: Control, exclude: Sequence[str], out: dict[str, Any],
                          loaded: list[str]) -> tuple[bool, str]:
    """Section 21 V14, real engines: the Arbiter refuses an over-budget load and downgrades a Deep Think depth when
    the projected footprint exceeds budget. On a healthy node every engine fits the ~170 GB budget alone (Section
    4.1) and the Arbiter EVICTS to make room rather than refusing, so an honest over-budget projection is produced
    the way the Arbiter itself reads it: the probe engine's unit env file (phase2/engine-env.py, root) is rendered
    with a ctx_size override doubled until /internal/deep-think/plan says the standard tier no longer fits; then
    (a) the deep tier is planned and must be downgraded, (b) POST /arbiter/load of that engine at that profile must be
    refused. The override is journalled in pending-overrides.json BEFORE the first render and restored in a finally
    block (SIGTERM is an exception here, so `systemctl stop` runs it; a SIGKILL/power cut is repaired by
    pending_recover() on the next invocation); nothing is loaded. The Arbiter's KV model (arbiter.py) scales with ctx,
    which is why doubling reaches over budget in a few steps. /internal/deep-think/plan is loopback-guarded and never
    sees the admin token (Control._hdr)."""
    key = pick_probe_key(ctx, exclude)
    spec = ctx.spec(key)
    prior_ctx = ctx.override_of(key, "ctx_size")
    base_ctx = int(prior_ctx or spec["ctx_size"])
    out["v14b_probe_engine"] = key
    out["v14b_prior_ctx_override"] = prior_ctx

    def plan(tier: str) -> dict[str, Any]:
        code, resp = ctl.post("/internal/deep-think/plan", {"tier": tier, "task_id": TASK_ID}, timeout=60)
        if code != 200 or not isinstance(resp, dict):
            raise RuntimeError(f"POST /internal/deep-think/plan {tier} answered {code}: {short(resp, 200)}")
        return resp

    pending = pending_begin(ctx, key, ["ctx_size"], "V14b over-budget probe profile")
    try:
        before = plan("deep")
        out["v14b_plan_deep_before"] = before
        log(f"coresident/V14b: deep-think plan before the proof: granted={before.get('granted')} "
            f"({before.get('reason')}; required {fmt_gb(before.get('required_bytes') or 0)}, "
            f"budget {fmt_gb(before.get('budget_bytes') or 0)})")
        ctx_over = base_ctx
        env: dict[str, str] = {}
        over = False
        for _ in range(V14B_MAX_DOUBLINGS):
            ctx_over *= 2
            env = ctx.render(key, [("ctx_size", str(ctx_over))], [])
            p = plan("standard")
            if p.get("granted") != "standard":
                over = True
                break
            log(f"coresident/V14b: {key} at ctx {ctx_over} still fits the standard tier "
                f"(required {fmt_gb(p.get('required_bytes') or 0)} of budget {fmt_gb(p.get('budget_bytes') or 0)})")
        if not over:
            return False, (f"could not render {key} over budget within {V14B_MAX_DOUBLINGS} doublings of ctx_size "
                           f"(last {ctx_over}); the Arbiter's projection does not scale with ctx as expected")
        # (a) rule 8: the deep tier must be downgraded now that its adversary engine does not fit.
        deep = plan("deep")
        out["v14b_plan_deep_over"] = deep
        downgraded = deep.get("granted") != "deep" and "downgrad" in str(deep.get("reason", "")).lower()
        a_msg = (f"deep-think deep -> {deep.get('granted')} ({deep.get('reason')}; budget "
                 f"{fmt_gb(deep.get('budget_bytes') or 0)})")
        # (b) rule 2: the load itself must be refused, not evicted around.
        try:
            code, resp = ctl.post("/arbiter/load", {"engine": key, "ctx": int(env["ATLAS_CTX_SIZE"]),
                                                    "parallel": int(env["ATLAS_PARALLEL"]), "task_id": TASK_ID},
                                  timeout=120)
        except HttpFail as exc:
            return False, f"{a_msg}; POST /arbiter/load {key} failed: {exc}"
        out["v14b_load_decision"] = resp if isinstance(resp, dict) else {"http": code, "body": short(resp)}
        if code != 200 or not isinstance(resp, dict):
            return False, f"{a_msg}; POST /arbiter/load {key} answered HTTP {code} {short(resp, 200)}"
        decision, reason = resp.get("decision"), str(resp.get("reason", ""))
        if decision == "granted":
            loaded.append(key)  # it really loaded: cleanup must unload it, and the proof failed
            return False, f"{a_msg}; the Arbiter GRANTED the over-budget load of {key} ({reason})"
        refused = decision == "refused" and "budget" in reason.lower()
        b_msg = (f"load of {key} at ctx {env['ATLAS_CTX_SIZE']} x {env['ATLAS_PARALLEL']} slots "
                 f"(projected {fmt_gb(resp.get('projected_bytes') or 0)}) -> {decision}: {reason}")
        ok = downgraded and refused
        msg = (f"{'refused' if refused else 'NOT refused'}: {b_msg}; "
               f"{'downgraded' if downgraded else 'NOT downgraded'}: {a_msg}; "
               "probe profile rendered through engine-env.py for the proof and restored")
        return ok, msg
    finally:
        env_back = pending_restore(ctx, pending)
        pending_end(ctx, pending)
        log(f"coresident/V14b: {key} env restored (ctx {env_back.get('ATLAS_CTX_SIZE')}, "
            f"parallel {env_back.get('ATLAS_PARALLEL')})")


def cmd_coresident(ctx: Ctx, args: argparse.Namespace) -> int:
    text_key, vision_key = args.text, args.vision
    tspec, vspec = ctx.spec(text_key), ctx.spec(vision_key)
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    saved_baseline = read_baseline(ctx)
    out: dict[str, Any] = {"text": text_key, "vision": vision_key, "control_mode": ctl.mode, "tested_at": now_iso()}
    # Whatever is resident (an interrupted step 03 leaves BOTH engines) goes through the Arbiter first and the counter
    # is polled once against the saved baseline after the last unload (clear_residents: never a multi-resident counter
    # attributed to a single unload). The test's own baseline is the SETTLED counter measured now: the saved one may
    # be hours old, and the delta that proves ~142 GB must not include whatever else was loaded since.
    ledger = ctl.resident_keys()
    resident = [k for k in ctx.core_keys() if unit_active(k) or k in ledger]
    _, _, clear_note, baseline = clear_residents(ctl, resident, saved_baseline, "coresident")
    out["cleared_first"] = {"engines": resident, "note": clear_note}
    out["baseline_bytes"] = baseline
    out["saved_baseline_bytes"] = saved_baseline
    if abs(baseline - saved_baseline) > RELEASE_TOLERANCE_BYTES:
        log(f"coresident: baseline for this test {fmt_gb(baseline)} (settled now) differs from baseline.json "
            f"{fmt_gb(saved_baseline)}: the resident set changed since step 02; the settled figure is used")
    budget = int((ctl.status() or {}).get("budget_bytes") or 0) or DEFAULT_BUDGET_BYTES
    loaded: list[str] = []
    # The vision engine's co-resident profile (parallel_coresident / ctx_size_coresident) is a TEMPORARY override:
    # journalled before it is written, cleared in cleanup(); the orchestrator decides co-residency live.
    pend_vision = pending_begin(ctx, vision_key, ["coresident"], "two-residency vision profile",
                                clear_always=["coresident"])
    used_after: dict[str, int] = {}  # GTT used right after each load, so each unload has a measured release target

    def cleanup() -> list[str]:
        # Reverse order: after unloading the vision engine the counter must be back where it was with the text engine
        # alone; after unloading the text engine it must be back at the baseline (Section 4.2 rule 5, per engine).
        problems: list[str] = []
        for i, k in reversed(list(enumerate(loaded))):
            target = baseline if i == 0 else used_after.get(loaded[i - 1], baseline)
            ok, _, note = unload_and_release(ctl, k, target)
            out[f"released_{k}"] = ok
            out[f"release_note_{k}"] = note
            if not ok:
                problems.append(f"{k} did not release after the two-residency test ({note})")
        # Restore the vision engine's stand-alone rendering (8 slots); the orchestrator decides co-residency live.
        pending_restore(ctx, pend_vision)
        pending_end(ctx, pend_vision)
        return problems

    def finish(v21: tuple[str, str], v14b: tuple[str, str]) -> int:
        problems = cleanup()
        if problems:
            # Section 4.2 rule 5 is part of the two-residency proof itself: a leak fails V21, never a stale V10.
            v21 = ("fail", f"{v21[1]}; release: {' | '.join(problems)}")
        out["v21"] = v21
        out["v14b"] = v14b
        out["control_mode"] = ctl.mode
        out["summary"] = f"V21 {v21[0]}: {v21[1][:200]} | V14b {v14b[0]}: {v14b[1][:200]}"
        (ctx.results_dir / "coresident.json").write_text(json.dumps(out, indent=2, default=str) + "\n",
                                                          encoding="utf-8")
        record("V21", *v21)
        record("V14b", *v14b)
        if problems:
            # Rule 5, as in step 02 (halt_if_leaked): the records are written, then the phase STOPS, so the driver
            # does not mark step 03 done and resume background generation with memory still held.
            raise Infra(f"two-residency cleanup: {' | '.join(problems)}: the phase stops here, as the Arbiter halts "
                        f"(Section 4.2 rule 5); recorded in {ctx.results_dir / 'coresident.json'}. Check the GTT "
                        f"counter and the llama-server@ units, restart {ORCH_UNIT}.service, then re-run step 03")
        return 0

    tenv = ctx.render(text_key, [], ["coresident"])
    venv = ctx.render(vision_key, [("coresident", "true")], [])  # parallel_coresident / ctx_size_coresident
    if venv.get("ATLAS_CORESIDENT") != "1":
        raise Infra(f"engine-env.py did not render {vision_key} with ATLAS_CORESIDENT=1")
    # Section 4.3: the vision engine runs 2 slots while co-resident "keeping the pair inside the ~28 GB of cache the
    # 142 GB combination leaves". The rendered profile must BE engines.json's parallel_coresident / ctx_size_coresident
    # before anything is loaded, or the delta accepted below would be an 8-slot profile's.
    want_par, want_ctx = vspec.get("parallel_coresident"), vspec.get("ctx_size_coresident")
    if want_par is None or want_ctx is None:
        raise Infra(f"engines.json: {vision_key} has no parallel_coresident/ctx_size_coresident (Section 4.3)")
    if venv.get("ATLAS_PARALLEL") != str(int(want_par)) or venv.get("ATLAS_CTX_SIZE") != str(int(want_ctx)):
        raise Infra(f"engine-env.py rendered {vision_key} co-resident as parallel {venv.get('ATLAS_PARALLEL')}, ctx "
                    f"{venv.get('ATLAS_CTX_SIZE')}; engines.json says parallel_coresident {want_par}, "
                    f"ctx_size_coresident {want_ctx} (Section 4.3)")
    for key, env in ((text_key, tenv), (vision_key, venv)):
        if env.get("ATLAS_MODEL_PRESENT") != "1":
            return finish(("fail", f"{key}: model file not present ({env.get('ATLAS_MODEL_FILE')})"),
                          ("fail", f"{key} not loadable"))
        try:
            secs = ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), int(env["LLAMA_ARG_PORT"]))
        except RuntimeError as exc:
            out[f"load_error_{key}"] = str(exc)
            if unit_active(key) or key in ctl.resident_keys():
                loaded.append(key)  # stop it in cleanup even if half-loaded
            beside = loaded[0] if loaded and loaded[0] != key else "nothing"
            return finish(("fail", f"{key} failed to load beside {beside}: {str(exc)[:200]}"),
                          ("fail", f"{key} not loaded; Arbiter proof not testable"))
        loaded.append(key)
        time.sleep(3)
        used_after[key] = gtt_used_bytes()
        log(f"coresident: {key} loaded in {secs:.0f}s (ctx {env['ATLAS_CTX_SIZE']}, parallel {env['ATLAS_PARALLEL']}); "
            f"GTT used {fmt_gb(used_after[key])}")
    # Rule 3: the Arbiter may have evicted the text engine to fit the vision one; the ledger says.
    ledger = ctl.resident_keys()
    gone = [k for k in loaded if k not in ledger]
    if gone:
        loaded[:] = [k for k in loaded if k in ledger]
        why = (f"the Arbiter evicted {gone} to load {vision_key} (resident now: {ledger}; last decision: "
               f"{short(ctl.last_decision, 200)}); the pair does not co-reside at these profiles")
        return finish(("fail", why), ("fail", f"pair not resident; {why}"))
    time.sleep(5)
    used = gtt_used_bytes()
    delta = used - baseline
    weights = int((float(tspec["footprint_gb"]) + float(vspec["footprint_gb"])) * GB)  # Section 4.1: 63 + 79 = 142 GB
    low, high = residency_bounds(weights, budget)
    residency_ok = low <= delta <= high
    mem_msg = (f"GTT delta {fmt_gb(delta)} for {text_key}+{vision_key} (weights {fmt_gb(weights)} + ~28 GB for both "
               f"caches expected; accepted {fmt_gb(low)}-{fmt_gb(high)}, the upper bound capped at the Arbiter's "
               f"budget {fmt_gb(budget)}; used {fmt_gb(used)}, baseline {fmt_gb(baseline)}, vision at parallel "
               f"{venv['ATLAS_PARALLEL']}, ctx {venv['ATLAS_CTX_SIZE']})")
    out.update({"gtt_used_bytes": used, "gtt_delta_bytes": delta, "weights_bytes": weights, "budget_bytes": budget,
                "accepted_low_bytes": low, "accepted_high_bytes": high, "residency_ok": residency_ok})
    log(f"coresident: {'OK' if residency_ok else 'OUT OF TOLERANCE'}: {mem_msg}")

    # Solo timings of each engine (direct to the llama-server port) give the wall-clock reference for the concurrent
    # run. Nothing else generates meanwhile: the driver stops atlas-sentinel.timer, atlas-prune.timer,
    # atlas-celery-gpu.service and the atlas-openwebui container around steps 02/03 (Section 4.2 rule 3: one
    # generation at a time, background work too), and the generation slot is checked free before each direct request.
    tport, vport = int(tenv["LLAMA_ARG_PORT"]), int(venv["LLAMA_ARG_PORT"])
    q_text = [{"role": "user", "content": "Write a 250-word essay about the history of lighthouses."}]
    q_vision = [{"role": "user", "content": "Describe, in about 150 words, how a suspension bridge carries its load."}]
    require_generation_idle(ctx.orch_url, f"coresident solo timing {text_key}")
    solo_t = chat(f"http://127.0.0.1:{tport}", text_key, q_text, 256)
    require_generation_idle(ctx.orch_url, f"coresident solo timing {vision_key}")
    solo_v = chat(f"http://127.0.0.1:{vport}", vision_key, q_vision, 160)
    if solo_t["error"] or solo_v["error"]:
        why = f"solo generation failed: text={solo_t['error'][:120]!r} vision={solo_v['error'][:120]!r}"
        return finish(("fail", f"{mem_msg}; {why}"), ("fail", f"not attempted: {why}"))
    d_t = solo_t["t_recv"] - solo_t["t_send"]
    d_v = solo_v["t_recv"] - solo_v["t_send"]
    log(f"coresident: solo durations text {d_t:.1f}s, vision {d_v:.1f}s")

    # Two requests at once THROUGH THE ORCHESTRATOR (/internal/v1/chat/completions, model = engine key); the Arbiter
    # must run the second only after the first finished (Section 4.2 rules 3, 4).
    results: dict[str, dict[str, Any]] = {}
    seen_queue: list[str] = []
    seen_generating: set[str] = set()
    stop_poll = threading.Event()

    def poll() -> None:
        while not stop_poll.is_set():
            st = ctl.status()
            if st:
                gen = st.get("generating") or {}
                if isinstance(gen, dict) and gen.get("engine"):
                    seen_generating.add(str(gen["engine"]))
                if st.get("generation_queue"):
                    seen_queue.append(json.dumps(st["generation_queue"])[:80])
            time.sleep(0.5)

    def fire(name: str, model: str, msgs: list[dict[str, str]], max_tokens: int) -> None:
        results[name] = chat(ctx.orch_url, model, msgs, max_tokens, path=ORCH_CHAT_PATH)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    th_a = threading.Thread(target=fire, args=("first", text_key, q_text, 256))
    th_b = threading.Thread(target=fire, args=("second", vision_key, q_vision, 160))
    th_a.start()
    time.sleep(0.5)
    th_b.start()
    th_a.join()
    th_b.join()
    stop_poll.set()
    poller.join(timeout=2)
    a, b = results["first"], results["second"]
    out["concurrent"] = {"first": {k: v for k, v in a.items() if k not in ("content", "reasoning")},
                         "second": {k: v for k, v in b.items() if k not in ("content", "reasoning")},
                         "queue_seen": seen_queue[:5], "generating_seen": sorted(seen_generating)}
    if a["error"] or b["error"]:
        why = (f"orchestrator chat failed: first(model={text_key})={a['error'][:140]!r} "
               f"second(model={vision_key})={b['error'][:140]!r} "
               f"(contract: POST {ORCH_CHAT_PATH} with model = engine key, see loadtest.py header)")
        v14b_ok, v14b_msg = False, "not attempted: " + why
        try:
            v14b_ok, v14b_msg = arbiter_refusal_proof(ctx, ctl, (text_key, vision_key), out, loaded)
        except (RuntimeError, Infra) as exc:
            v14b_msg = f"proof aborted: {exc}"
        return finish(("fail", f"{mem_msg}; {why}"), ("pass" if v14b_ok else "fail", v14b_msg))
    ok, proof = queue_proof(a, b, d_v)
    if seen_queue:
        proof += f"; Arbiter status showed a generation queue ({seen_queue[0]})"
    if seen_generating:
        proof += f"; generating seen: {sorted(seen_generating)}"
    log(f"coresident: queueing {'PROVEN' if ok else 'NOT proven'}: {proof}")
    verb = "queued behind" if ok else "did NOT wait for"
    v21 = ("pass" if (ok and residency_ok) else "fail",
           f"{mem_msg}; second generation request ({vision_key}) {verb} the first ({text_key}); {proof}"
           f"{'' if residency_ok else '; memory out of tolerance'}")

    # V14b: refusal and downgrade against the real engine set while the pair is resident.
    try:
        v14b_ok, v14b_msg = arbiter_refusal_proof(ctx, ctl, (text_key, vision_key), out, loaded)
    except (RuntimeError, Infra) as exc:
        v14b_ok, v14b_msg = False, f"proof aborted: {exc}"
    log(f"coresident: V14b {'PASS' if v14b_ok else 'FAIL'}: {v14b_msg}")
    v14b = ("pass" if v14b_ok else "fail", v14b_msg)
    return finish(v21, v14b)


# --- main -------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", required=True, help="config/engines.json")
    ap.add_argument("--env-dir", required=True, help="$ATLAS_ETC/engines")
    ap.add_argument("--results-dir", required=True, help="$ATLAS_STATE/phase3/results")
    ap.add_argument("--orch-url", required=True, help="http://127.0.0.1:$ORCH_PORT (loopback only)")
    ap.add_argument("--engine-env", required=True, help="phase2/engine-env.py")
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--slots-dir", required=True)
    ap.add_argument("--port-base", type=int, required=True)
    ap.add_argument("--overrides", required=True, help="$ATLAS_ETC/engines/overrides.json")
    ap.add_argument("--admin-token-file", default=None,
                    help="ORCH_ADMIN_TOKEN_FILE of orchestrator.env when configured (X-Atlas-Token for /arbiter/*)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    p = sub.add_parser("engine")
    p.add_argument("key")
    p.add_argument("--previous", default=None)
    p.set_defaults(fn=cmd_engine)
    p = sub.add_parser("finish")
    p.add_argument("--previous", default=None)
    p.set_defaults(fn=cmd_finish)
    sub.add_parser("summarize").set_defaults(fn=cmd_summarize)
    sub.add_parser("table").set_defaults(fn=cmd_table)
    p = sub.add_parser("coresident")
    p.add_argument("--text", required=True)
    p.add_argument("--vision", required=True)
    p.set_defaults(fn=cmd_coresident)
    args = ap.parse_args(argv)
    # `systemctl stop atlas-day1-phase3` sends SIGTERM to the whole cgroup: turn it into an exception in the main
    # thread so every finally block (override restores, pending journal) runs before exit (CONVENTIONS.md §7.3, §7.4).
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        require_loopback(args.orch_url)
        ctx = Ctx(engines_path=Path(args.engines), env_dir=Path(args.env_dir), results_dir=Path(args.results_dir),
                  orch_url=args.orch_url, engine_env=Path(args.engine_env), models_dir=Path(args.models_dir),
                  slots_dir=Path(args.slots_dir), port_base=args.port_base, overrides=Path(args.overrides),
                  admin_token=read_admin_token(args.admin_token_file))
        log(f"{args.cmd}: orchestrator {args.orch_url}, GTT counter under {drm_root()}"
            + (" (admin token loaded)" if ctx.admin_token else ""))
        ctx.load_engines()
        return int(args.fn(ctx, args))
    except Terminated as exc:
        log(f"TERMINATED: {exc}")
        return 143
    except Infra as exc:
        log(f"FATAL: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
