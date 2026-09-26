#!/usr/bin/env python3
"""Phase 3 load tests: the measuring half of scripts/day1/phase3-models.sh (ATLAS_FRAMEWORK_REVIEW.md Section 17
Phase 3 steps 2-4; Section 21 V4, V10, V14b, V21, V22; Section 4.2 rules 3-5, 7; R19).

Run as root by the driver (never by hand while the orchestrator is serving). Stdout carries ONLY lines of the form
    RECORD<TAB><ID><TAB><pass|fail|deferred|info><TAB><message>
which the driver turns into verify.jsonl records with record_v; everything else goes to stderr (the unit's journal).
Exit status 0 means "measured and recorded" (a failed engine is a recorded fail, not an exit code); non-zero means an
infrastructure error the driver must stop on.

Subcommands (global options first, see main()):
    prepare                 stop any resident weight-bearing engine, wait for the GTT counter, write baseline.json
    engine KEY [--previous PREV]
                            swap PREV out (unload + release check, credited to PREV) and KEY in, prove V4 from the
                            journal, measure decode/prefill at 512 and 8k prompt tokens, leave KEY resident.
                            DeepSeek (kv_ladder) runs the f16 -> q8_0 -> q4_0 ladder with a coherence prompt instead.
    finish [--previous PREV]
                            unload the last resident engine and credit its release check
    summarize               print the V10 lines (one per engine, then the summary the gate reads) and V22
    coresident --text KEY --vision KEY
                            Section 17 step 3: both resident (vision at parallel_coresident), GTT ~142 GB + caches,
                            two generation requests through the orchestrator, the second proven to queue (V21, V14b)
    table                   the Section 17 step 4 table from the result files

Result files: <results-dir>/<key>.json, one per engine (schema: EngineResult below), plus baseline.json and
coresident.json. A result with "ok": true makes the driver skip that engine on a re-run (file-level resumability).

Facts typed here and where they were VERIFIED (research/llama-cpp-vulkan.md, research/gguf-models.md, 2026-09-22):
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
  * /props does not expose the KV cache types (README): when the journal has no proof line, V4 is a fail with reason.
  * GTT in use: /sys/class/drm/card*/device/mem_info_gtt_used, bytes (amdgpu_gtt_mgr.c); the Arbiter's counter.
  * Nemotron 3 Super: llama.cpp issue #20732, GPU memory fault at ~20k-token prompts on Vulkan/HIP (conflict f).
  * DeepSeek V4: K and V types must be identical (llama-context.cpp), --parallel explicit (issue #26654), quantised
    KV disputed (issues #25382, #26423; conflict b); kernel 7.x DeviceLost needs amdgpu.lockup_timeout (issue #25664).
  # UNVERIFIED: the per-request "cache_prompt": false field of POST /completion — README field (not re-read in the
    research); a nonce in every prompt and a check that timings.cache_n == 0 make the measurement independent of it.

CONTRACT with the orchestrator (atlas.api, another writer; CONVENTIONS.md §8 control path). Loopback, no API key:
  * GET  {ORCH_URL}/health -> 200 when ready.
  * GET  {ORCH_URL}/arbiter/status -> 200 JSON as Arbiter.status(): gtt_used_bytes, resident[{engine,...}],
         generating {engine, task_id} | null, generation_queue [...].
  * POST {ORCH_URL}/arbiter/load   {"engine": KEY, "ctx": N, "parallel": N, "task_id": "phase3-loadtest"}
         -> 200 {"decision": "granted"|"queued"|"refused", "reason": "..."}; the engine is then started through the
         sudoers control path, /health polled here. A refusal is a real Arbiter decision and fails the engine loudly.
  * POST {ORCH_URL}/arbiter/unload {"engine": KEY, "task_id": ...} -> 200.
  * POST {ORCH_URL}/v1/chat/completions accepts "model": <engine key> (personas too) and routes it through the
         Arbiter's generation lock (Section 4.2 rule 3/4); the llama-server "timings" object is passed through.
  When /health is not 200 or /arbiter/* answers 404/405, this script says so in the log and falls back to
  `systemctl start|stop llama-server@<key>` for the loads; the queueing proof (V14b/V21) then records fail with that
  reason, because only the Arbiter can queue.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GIB = 1024**3
GB = 10**9
RELEASE_TOLERANCE_BYTES = 2 * GIB  # Section 17 step 2 brief: tolerance 2 GB
RELEASE_TIMEOUT_S = 120.0
LOAD_TIMEOUT_S = 960.0  # llama-server@.service TimeoutStartSec=900 plus margin
STOP_TIMEOUT_S = 180.0  # TimeoutStopSec=120 plus margin
REQUEST_TIMEOUT_S = 1200.0  # an 8k prefill on a dense 72B at Q8_0 plus 128 decoded tokens at ~3 tok/s
PREFILL_SHORT = 512
PREFILL_LONG = 8192
N_PREDICT = 128
TASK_ID = "phase3-loadtest"
UNIT_PREFIX = "llama-server@"
P3_CLASSES = {"core", "apex", "vision", "crosscheck"}
NEMOTRON_ISSUE = "llama.cpp issue #20732 (Vulkan/HIP memory fault at ~20k-token prompts)"
DEVICELOST_ISSUE = "llama.cpp issue #25664 (kernel 7.x amdgpu lockup_timeout; Phase 1 GRUB line)"

KV_LINE_RE = re.compile(r"llama_kv_cache: size = .*K \((\w+)\): .*V \((\w+)\):")
RECURRENT_RE = re.compile(r"llama_memory_recurrent: size = .*R \((\w+)\): .*S \((\w+)\):")
FA_RE = re.compile(r"llama_context: flash_attn\s*= (\w+)")
KV_REFUSED = (
    "Flash Attention not supported, set to disabled",
    "quantized V cache requires flash_attn",
    "does not support different K",
    "does not divide n_embd_head",
)

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


class Infra(RuntimeError):
    """An infrastructure error: the driver stops the phase (exit non-zero)."""


# --- HTTP (loopback only, never through the allowlist proxy) ----------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HttpFail(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: str = "") -> None:
        super().__init__(msg)
        self.status = status
        self.body = body


def http(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 30.0) -> tuple[int, Any]:
    """Returns (status, parsed JSON or text). Raises HttpFail on transport errors; HTTP errors are returned."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
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


# --- sysfs GTT counter (llama-cpp-vulkan.md §5.2) ---------------------------------------------------------------------


def gpu_device_dir() -> Path:
    # ATLAS_DRM_ROOT is a test hook (CONVENTIONS.md §4: node paths are overridable for tests); never set on the node.
    for c in sorted(Path(os.environ.get("ATLAS_DRM_ROOT", "/sys/class/drm")).glob("card*")):
        if not re.fullmatch(r"card\d+", c.name):
            continue
        vendor = c / "device" / "vendor"
        try:
            if vendor.read_text().strip() == "0x1002":
                return c / "device"
        except OSError:
            continue
    raise Infra("no AMD GPU (vendor 0x1002) under /sys/class/drm")


def gtt_used_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_used").read_text().strip())


def gtt_total_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_total").read_text().strip())


def fmt_gb(b: float) -> str:
    return f"{b / GB:.1f} GB"


# --- systemd helpers --------------------------------------------------------------------------------------------------


def run(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)


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

    def render(self, key: str, set_ov: list[tuple[str, str]] = (), clear_ov: list[str] = ()) -> dict[str, str]:
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
        try:
            os.chmod(path, 0o640)
        except OSError:
            pass
        return self.env(key)

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
    released: bool | None = None
    release_s: float | None = None
    release_note: str = ""
    warnings: list[str] = field(default_factory=list)
    ladder: list[dict[str, Any]] = field(default_factory=list)
    v22_result: str = ""
    v22_msg: str = ""
    resident_after: bool = False
    ok: bool = False
    tested_at: str = ""

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


def read_baseline(ctx: Ctx) -> int:
    p = ctx.results_dir / "baseline.json"
    if not p.is_file():
        raise Infra(f"{p} missing: run `loadtest.py prepare` first (the driver does)")
    return int(json.loads(p.read_text(encoding="utf-8"))["gtt_used_bytes"])


# --- engine control: orchestrator Arbiter API, systemctl fallback -----------------------------------------------------


class Control:
    def __init__(self, orch_url: str) -> None:
        self.orch_url = orch_url.rstrip("/")
        self.mode = "systemctl"
        self.fallback_reason = ""
        self._probe()

    def _probe(self) -> None:
        try:
            code, _ = http("GET", f"{self.orch_url}/health", timeout=5)
        except HttpFail as exc:
            self.fallback_reason = f"orchestrator {self.orch_url} unreachable ({exc})"
            log(f"control: {self.fallback_reason}; falling back to systemctl for loads (V14b cannot pass this way)")
            return
        if code != 200:
            self.fallback_reason = f"orchestrator {self.orch_url}/health answered {code}"
            log(f"control: {self.fallback_reason}; falling back to systemctl")
            return
        try:
            code, body = http("GET", f"{self.orch_url}/arbiter/status", timeout=10)
        except HttpFail as exc:
            self.fallback_reason = f"GET /arbiter/status failed ({exc})"
            log(f"control: {self.fallback_reason}; falling back to systemctl")
            return
        if code != 200 or not isinstance(body, dict):
            self.fallback_reason = f"GET /arbiter/status answered {code} (Arbiter API not implemented yet?)"
            log(f"control: {self.fallback_reason}; falling back to systemctl")
            return
        self.mode = "orchestrator"
        log(f"control: orchestrator Arbiter API at {self.orch_url} (resident: "
            f"{[r.get('engine') for r in body.get('resident', [])]})")

    def status(self) -> dict[str, Any] | None:
        if self.mode != "orchestrator":
            return None
        try:
            code, body = http("GET", f"{self.orch_url}/arbiter/status", timeout=10)
        except HttpFail:
            return None
        return body if code == 200 and isinstance(body, dict) else None

    def _fallback(self, why: str) -> None:
        self.mode = "systemctl"
        self.fallback_reason = why
        log(f"control: {why}; falling back to systemctl from here on")

    def load(self, key: str, ctx_size: int, parallel: int, port: int) -> float:
        """Start the engine and wait for /health 200. Returns the load time in seconds. Raises RuntimeError with the
        reason on failure (the caller records it); Infra only for broken tooling."""
        t0 = time.monotonic()
        started_via = self.mode
        if self.mode == "orchestrator":
            body = {"engine": key, "ctx": ctx_size, "parallel": parallel, "task_id": TASK_ID}
            try:
                code, resp = http("POST", f"{self.orch_url}/arbiter/load", body, timeout=LOAD_TIMEOUT_S)
            except HttpFail as exc:
                self._fallback(f"POST /arbiter/load failed ({exc})")
                code, resp = 0, None
            if code in (404, 405):
                self._fallback(f"POST /arbiter/load answered {code}")
            elif code and code != 200:
                raise RuntimeError(f"Arbiter refused to load {key}: HTTP {code} {str(resp)[:300]}")
            elif code == 200:
                decision = (resp or {}).get("decision") if isinstance(resp, dict) else None
                if decision != "granted":
                    raise RuntimeError(f"Arbiter did not grant {key}: {json.dumps(resp)[:400]}")
        if self.mode == "systemctl":
            run(["systemctl", "reset-failed", unit_of(key)])
            p = run(["systemctl", "start", unit_of(key)], timeout=LOAD_TIMEOUT_S + 30)
            if p.returncode != 0:
                raise RuntimeError(f"systemctl start {unit_of(key)} failed (exit {p.returncode}): "
                                   f"{(p.stderr or p.stdout).strip()[:300]}; journal: {journal_tail(key, 15)[-600:]}")
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            if health_code(port) == 200:
                secs = time.monotonic() - t0
                log(f"control: {key} healthy on 127.0.0.1:{port} after {secs:.1f}s (via {started_via})")
                return secs
            if not unit_active(key) and time.monotonic() - t0 > 10:
                raise RuntimeError(f"{unit_of(key)} is not active after start; journal: {journal_tail(key, 20)[-800:]}")
            time.sleep(2)
        raise RuntimeError(f"{key} did not answer /health 200 within {LOAD_TIMEOUT_S:.0f}s")

    def unload(self, key: str) -> None:
        if self.mode == "orchestrator":
            try:
                code, resp = http("POST", f"{self.orch_url}/arbiter/unload", {"engine": key, "task_id": TASK_ID},
                                  timeout=STOP_TIMEOUT_S)
            except HttpFail as exc:
                self._fallback(f"POST /arbiter/unload failed ({exc})")
                code = 0
            if code in (404, 405):
                self._fallback(f"POST /arbiter/unload answered {code}")
            elif code and code != 200:
                log(f"control: /arbiter/unload {key} answered {code} {str(resp)[:200]}; stopping the unit directly")
        # Whatever the Arbiter did, the unit must be down before the release check (the Arbiter's own stop is the same
        # systemctl call through sudoers; repeating it as root is idempotent).
        p = run(["systemctl", "stop", unit_of(key)], timeout=STOP_TIMEOUT_S + 30)
        if p.returncode != 0:
            log(f"control: systemctl stop {unit_of(key)} exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
        run(["systemctl", "reset-failed", unit_of(key)])


def wait_release(baseline: int, label: str) -> tuple[bool, float, str]:
    """Section 4.2 rule 5: poll the GTT counter until it is back within tolerance of the baseline."""
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


# --- V4 proof ---------------------------------------------------------------------------------------------------------


def prove_kv(key: str, expected: str, proof_lines: int, invocation_id: str, since: str, port: int,
             ) -> tuple[bool, str, str]:
    """(ok, message, applied type). Reads the journal of the current invocation; /props only for the reason text."""
    lines = journal_lines(key, invocation_id, since)
    kv = [m for m in (KV_LINE_RE.search(ln) for ln in lines) if m]
    rec = [m for m in (RECURRENT_RE.search(ln) for ln in lines) if m]
    fa = [m.group(1) for m in (FA_RE.search(ln) for ln in lines) if m]
    refused = [s for s in KV_REFUSED if any(s in ln for ln in lines)]
    if not kv:
        build = ""
        try:
            code, props = http("GET", f"http://127.0.0.1:{port}/props", timeout=10)
            if code == 200 and isinstance(props, dict):
                build = f"; /props build_info={props.get('build_info')} (no cache-type field exists there)"
        except HttpFail:
            pass
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
         ) -> dict[str, Any]:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "stream": False}
    t_send = time.monotonic()
    try:
        code, resp = http("POST", f"{url}/v1/chat/completions", body, timeout=timeout)
    except HttpFail as exc:
        return {"content": "", "timings": {}, "error": str(exc), "t_send": t_send, "t_recv": time.monotonic(),
                "code": 0}
    t_recv = time.monotonic()
    if code != 200 or not isinstance(resp, dict):
        return {"content": "", "timings": {}, "error": f"HTTP {code}: {str(resp)[:300]}", "t_send": t_send,
                "t_recv": t_recv, "code": code}
    try:
        content = resp["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        content = ""
    return {"content": content, "timings": resp.get("timings") or {}, "error": "", "t_send": t_send, "t_recv": t_recv,
            "code": code}


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


def coherence_check(port: int, model: str) -> tuple[bool, str]:
    """A short factual question with a known answer plus a 200-token summary; garbage or repetition fails."""
    r1 = chat(f"http://127.0.0.1:{port}", model,
              [{"role": "user", "content": "What is the capital of France? Answer with the city name only."}], 32)
    if r1["error"]:
        return False, f"factual question failed: {r1['error'][:200]}"
    a1 = r1["content"].strip()
    if "paris" not in a1.lower():
        return False, f"factual question wrong/garbled: {a1[:80]!r}"
    r2 = chat(f"http://127.0.0.1:{port}", model,
              [{"role": "user",
                "content": f"Summarise the following passage in about 120 words:\n\n{COHERENCE_PASSAGE}"}], 200)
    if r2["error"]:
        return False, f"summary request failed: {r2['error'][:200]}"
    a2 = r2["content"].strip()
    words = re.findall(r"[A-Za-z']+", a2)
    if len(words) < 20:
        return False, f"summary too short/empty ({len(words)} words): {a2[:80]!r}"
    non_ascii = sum(1 for ch in a2 if ord(ch) > 127) / max(len(a2), 1)
    if non_ascii > 0.15:
        return False, f"summary is mojibake ({non_ascii:.0%} non-ASCII): {a2[:80]!r}"
    uniq = len({w.lower() for w in words}) / len(words)
    if uniq < 0.3:
        return False, f"summary is repetitive (unique-word ratio {uniq:.2f}): {a2[:80]!r}"
    grams = [" ".join(words[i:i + 4]).lower() for i in range(len(words) - 3)]
    if grams and max(grams.count(g) for g in set(grams)) > 4:
        return False, f"summary repeats a 4-gram more than 4 times: {a2[:80]!r}"
    hits = sum(1 for k in COHERENCE_KEYWORDS if k in a2.lower())
    if hits < 3:
        return False, f"summary is off-topic ({hits} of {len(COHERENCE_KEYWORDS)} keywords): {a2[:80]!r}"
    return True, f"'{a1[:20]}' + {len(words)}-word summary, {hits} keywords, unique ratio {uniq:.2f}"


# --- per-engine test --------------------------------------------------------------------------------------------------


def credit_release(ctx: Ctx, key: str, ok: bool, secs: float, note: str) -> None:
    """The release check of the engine being swapped out belongs to that engine's result (V10 'released')."""
    res = load_result(ctx, key)
    if res is None:
        res = EngineResult(key=key, arbiter_class=ctx.spec(key).get("arbiter_class", ""))
    res.released, res.release_s, res.release_note, res.resident_after = ok, round(secs, 1), note, False
    if not ok:
        res.ok = False
    res.save(ctx)


def swap_out(ctx: Ctx, ctl: Control, previous: str | None, baseline: int) -> tuple[float, str]:
    """Unload the previous engine (if resident) and confirm its memory came back. Returns (seconds, note)."""
    t0 = time.monotonic()
    if not previous:
        return 0.0, "no previous engine"
    if not unit_active(previous):
        return 0.0, f"{previous} was not resident"
    log(f"swap: unloading {previous}")
    ctl.unload(previous)
    ok, secs, note = wait_release(baseline, previous)
    credit_release(ctx, previous, ok, secs, note)
    if not ok:
        log(f"swap: {previous} did NOT release its memory; the next load may be over budget")
    return time.monotonic() - t0, f"unloaded {previous} ({note})"


def stop_and_release(ctx: Ctx, ctl: Control, key: str, baseline: int, res: EngineResult) -> None:
    ctl.unload(key)
    ok, secs, note = wait_release(baseline, key)
    res.released, res.release_s, res.release_note, res.resident_after = ok, round(secs, 1), note, False


def run_measurements(ctx: Ctx, res: EngineResult, key: str, port: int, env: dict[str, str]) -> None:
    spec = ctx.spec(key)
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
    band = spec.get("expected_decode_tok_s")
    if band and res.decode_tps_512 < band[0] * 0.7:
        res.warnings.append(f"decode {res.decode_tps_512} tok/s is >30% below the expected band {band[0]}-{band[1]}")
    ctx_size = int(env.get("ATLAS_CTX_SIZE", spec["ctx_size"]))
    parallel = int(env.get("ATLAS_PARALLEL", spec.get("parallel", 1)))
    per_slot = ctx_size // max(parallel, 1)
    if per_slot < PREFILL_LONG + N_PREDICT + 256:
        res.prefill_8k_note = f"skipped: {per_slot} tokens per slot ({ctx_size}/{parallel}) cannot hold an 8k prompt"
        log(f"{key}: 8k point {res.prefill_8k_note}")
        return
    r, note = measure_point(port, PREFILL_LONG, "8k")
    log(f"{key}: {note}")
    if r["error"]:
        # Conflict f: Nemotron's ~20k memory fault; any engine that dies here is caught, never hung.
        time.sleep(3)
        alive = unit_active(key) and health_code(port) == 200
        if not alive:
            res.crashed_at_8k = True
            issue = NEMOTRON_ISSUE if key == "nemotron-3-super" else "server died during the 8k prefill"
            tail = " | ".join(journal_tail(key, 6).splitlines()[-3:])[-300:]
            res.warnings.append(f"8k prefill crashed the server ({issue}); journal: {tail}")
            res.prefill_8k_note = "crashed"
        else:
            res.prefill_8k_note = f"request failed but server alive: {r['error'][:160]}"
            res.warnings.append(res.prefill_8k_note)
        return
    t = r["timings"]
    res.prefill_tps_8k = round(float(t.get("prompt_per_second") or 0), 2)
    res.decode_tps_8k = round(float(t.get("predicted_per_second") or 0), 2)


def test_engine(ctx: Ctx, key: str, previous: str | None) -> None:
    spec = ctx.spec(key)
    ctl = Control(ctx.orch_url)
    baseline = read_baseline(ctx)
    res = EngineResult(key=key, arbiter_class=spec.get("arbiter_class", ""), control_mode=ctl.mode, tested_at=now_iso())
    if ctl.mode != "orchestrator":
        res.warnings.append(f"loaded via systemctl fallback: {ctl.fallback_reason}")
    if spec.get("kv_ladder"):
        ladder_engine(ctx, ctl, key, previous, baseline, res)
        return
    env = ctx.env(key)
    res.kv_requested = env.get("ATLAS_KV_TYPE", spec.get("kv_class", ""))
    if env.get("ATLAS_MODEL_PRESENT") != "1":
        res.load_error = (f"ATLAS_MODEL_PRESENT=0 in {key}.env: model file {env.get('ATLAS_MODEL_FILE')} "
                          "not found (step 01)")
        res.save(ctx)
        record("V4", "fail", f"{key}: not loaded ({res.load_error})")
        return
    port = int(env["LLAMA_ARG_PORT"])
    proof_lines = int(env.get("ATLAS_KV_PROOF_LINES", spec.get("kv_proof_lines", 0)) or 0)
    t_swap = time.monotonic()
    _, res.swap_note = swap_out(ctx, ctl, previous, baseline)
    since = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        res.load_s = round(ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), port), 1)
    except RuntimeError as exc:
        res.load_error = str(exc)
        if "ErrorDeviceLost" in res.load_error:
            res.load_error += f" [{DEVICELOST_ISSUE}]"
        log(f"{key}: LOAD FAILED: {res.load_error}")
        stop_and_release(ctx, ctl, key, baseline, res)
        res.save(ctx)
        record("V4", "fail", f"{key}: not loaded ({res.load_error[:300]})")
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
    if res.crashed_at_8k or res.load_error:
        stop_and_release(ctx, ctl, key, baseline, res)
    else:
        res.resident_after = True  # the next engine's swap (or `finish`) measures this one's release
    res.ok = res.load_ok and res.generated and not res.load_error and res.released is not False
    res.save(ctx)
    log(f"{key}: result ok={res.ok} decode512={res.decode_tps_512} decode8k={res.decode_tps_8k} "
        f"prefill512={res.prefill_tps_512} prefill8k={res.prefill_tps_8k} warnings={res.warnings}")


def ladder_engine(ctx: Ctx, ctl: Control, key: str, previous: str | None, baseline: int, res: EngineResult) -> None:
    """DeepSeek V4 Flash (research conflict b): f16 -> q8_0 -> q4_0, coherence at each rung, winner to overrides.json.
    Any failure records V22 deferred with the reason and V4 deferred (R19: never blocks the phase)."""
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
    for kv in ladder:
        rung: dict[str, Any] = {"kv": kv, "load_ok": False, "coherent": False, "kv_proof_ok": False, "note": ""}
        res.ladder.append(rung)
        set_ov: list[tuple[str, str]] = [("kv_type", kv)]
        clear_ov: list[str] = []
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
            log(f"{key}: rung {kv} {rung['note']}")
            stop_and_release(ctx, ctl, key, baseline, res)
            stop_reason = rung["note"]
            break
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
        coherent, detail = coherence_check(port, key)
        rung["coherent"], rung["coherence"] = coherent, detail
        log(f"{key}: rung {kv} coherence {'ok' if coherent else 'FAIL'}: {detail}")
        if coherent and ok:
            run_measurements(ctx, res, key, port, env)
            rung.update({"decode_tps_512": res.decode_tps_512, "decode_tps_8k": res.decode_tps_8k,
                         "prefill_tps_512": res.prefill_tps_512, "prefill_tps_8k": res.prefill_tps_8k,
                         "prefill_8k_note": res.prefill_8k_note})
            if res.load_error:
                rung["note"] = res.load_error
                rung["coherent"] = False
                res.load_error = ""
        stop_and_release(ctx, ctl, key, baseline, res)
        if res.released is False:
            stop_reason = f"memory not released after the {kv} rung: {res.release_note}"
            break
        if kv == "f16" and not coherent:
            stop_reason = f"incoherent already at f16 KV ({detail}); the model itself is broken on this build"
            break
    winners = [r["kv"] for r in res.ladder if r.get("coherent") and r.get("kv_proof_ok")]
    def verdict(r: dict[str, Any]) -> str:
        return "coherent" if r.get("coherent") else ("incoherent" if r.get("load_ok") else "load failed")

    summary = ", ".join(f"{r['kv']}={verdict(r)}" for r in res.ladder)
    untried = [kv for kv in ladder if kv not in {r["kv"] for r in res.ladder}]
    if untried:
        summary += ", " + ", ".join(f"{kv}=not tried" for kv in untried)
    if winners:
        winner = winners[-1]
        res.kv_applied = winner
        for r in res.ladder:
            if r.get("load_ok") and not r.get("coherent") and ladder.index(r["kv"]) < ladder.index(winner):
                res.warnings.append(f"rung {r['kv']} was incoherent although the more compressed {winner} is coherent "
                                    "(suspicious)")
        if winner == base_kv:
            ctx.render(key, [], ["kv_type", "ctx_size"])  # a q4_0 win keeps the baseline (engines.json kv_class)
            note = "baseline q4_0 kept (overrides cleared)"
        elif winner == "f16":
            ctx.render(key, [("kv_type", "f16")] + ([("ctx_size", str(int(f16_cap)))] if f16_cap else []), [])
            note = f"f16 written to overrides.json, --ctx-size capped to {f16_cap} (~10 GB of cache beside the weights)"
        else:
            ctx.render(key, [("kv_type", winner)], ["ctx_size"])
            note = f"{winner} written to overrides.json"
        res.kv_proof_ok = True
        res.kv_proof_msg = f"{key}: {winner} applied and coherent (ladder: {summary}; {note})"
        record("V4", "pass", res.kv_proof_msg)
        res.generated = True
        res.ok = res.released is not False
        res.v22_result = "pass" if res.ok else "deferred"
        res.v22_msg = (f"DeepSeek V4 Flash 0731 UD-Q4_K_XL loads and generates coherently with {winner} KV "
                       f"(decode {res.decode_tps_512} tok/s at 512, prefill {res.prefill_tps_512} tok/s; {note})")
        if not res.ok:
            res.v22_msg += f"; deferred: {res.release_note} (R19)"
    else:
        ctx.render(key, [], ["kv_type", "ctx_size"])
        reason = stop_reason or f"no coherent rung ({summary})"
        res.kv_proof_msg = f"{key}: no KV type proven coherent, V22 deferred (R19): {reason}"
        record("V4", "deferred", res.kv_proof_msg)
        res.v22_result = "deferred"
        res.v22_msg = f"DeepSeek V4 Flash deferred without blocking the phase (R19): {reason}; ladder: {summary}"
        res.ok = False
    res.resident_after = False
    res.save(ctx)
    log(f"{key}: ladder result: {res.v22_result}: {res.v22_msg}")


# --- prepare / finish / summarize / table -----------------------------------------------------------------------------


def cmd_prepare(ctx: Ctx, _args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url)
    active = [k for k in ctx.core_keys() if unit_active(k)]
    for k in active:
        log(f"prepare: {k} is resident before the tests; unloading it")
        ctl.unload(k)
    if active:
        time.sleep(5)
    # Settle: two readings 10 s apart within tolerance, so the baseline is not taken mid-release.
    deadline = time.monotonic() + RELEASE_TIMEOUT_S
    prev = gtt_used_bytes()
    while True:
        time.sleep(10)
        cur = gtt_used_bytes()
        if abs(cur - prev) < 256 * 1024 * 1024 or time.monotonic() > deadline:
            break
        prev = cur
    total = gtt_total_bytes()
    log(f"prepare: baseline GTT used {fmt_gb(cur)} of {fmt_gb(total)} (residents only; control via {ctl.mode})")
    if cur > 40 * GB:
        log(f"prepare: WARNING baseline {fmt_gb(cur)} is far above the ~17 GB resident set of Section 4.1; "
            "something weight-bearing is still loaded outside the llama-server@ units")
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({
        "gtt_used_bytes": cur, "gtt_total_bytes": total, "measured_at": now_iso(), "control_mode": ctl.mode,
        "unloaded_first": active}, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_engine(ctx: Ctx, args: argparse.Namespace) -> int:
    test_engine(ctx, args.key, args.previous)
    return 0


def cmd_finish(ctx: Ctx, args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url)
    baseline = read_baseline(ctx)
    prev = args.previous
    if prev and unit_active(prev):
        log(f"finish: unloading the last engine {prev}")
        ctl.unload(prev)
        ok, secs, note = wait_release(baseline, prev)
        credit_release(ctx, prev, ok, secs, note)
    for k in ctx.core_keys():
        if unit_active(k):
            log(f"finish: {k} still active (unexpected); stopping it")
            ctl.unload(k)
            ok, secs, note = wait_release(baseline, k)
            credit_release(ctx, k, ok, secs, note)
    return 0


def fmt_num(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def v10_line(res: EngineResult, spec: dict[str, Any]) -> tuple[str, str]:
    band = spec.get("expected_decode_tok_s")
    band_s = f"{band[0]}-{band[1]}" if band else "n/a"
    warn = " WARN" if res.warnings else ""
    if not res.load_ok:
        return "fail", f"{res.key}: load FAILED ({res.load_error[:200]})"
    parts = [f"{res.key}: load ok (KV {res.kv_applied or res.kv_requested})",
             f"decode {fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)} tok/s at 512/8k (expected {band_s})",
             f"prefill {fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)} tok/s",
             f"swap {fmt_num(res.swap_s)}s",
             f"released {'yes' if res.released else ('NO' if res.released is False else '?')}"]
    if res.prefill_8k_note:
        parts.append(f"8k: {res.prefill_8k_note[:120]}")
    if res.warnings:
        parts.append("warn: " + " | ".join(w[:160] for w in res.warnings))
    if res.load_error:
        parts.append(f"error: {res.load_error[:160]}")
    ok = res.generated and not res.load_error and res.released is True
    return ("pass" if ok else "fail"), "; ".join(parts) + warn


def cmd_summarize(ctx: Ctx, _args: argparse.Namespace) -> int:
    failed: list[str] = []
    warned: list[str] = []
    missing: list[str] = []
    apex_lines: list[str] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        res = load_result(ctx, key)
        if res is None:
            missing.append(key)
            record("V10", "fail", f"{key}: no load-test result (step 02 did not reach it)")
            continue
        result, msg = v10_line(res, spec)
        if spec.get("arbiter_class") == "apex":
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
    tested = [k for k in ctx.core_keys() if ctx.spec(k).get("arbiter_class") != "apex"]
    if failed or missing:
        record("V10", "fail", f"summary: {len(failed) + len(missing)} of {len(tested)} engines failed "
               f"({', '.join(failed + missing)}); warns: {', '.join(warned) or 'none'}; "
               f"apex: {', '.join(apex_lines) or '-'}")
    else:
        record("V10", "pass", f"summary: all {len(tested)} engines load, generate, swap and release memory at their "
               f"fixed quantisations; warns: {', '.join(warned) or 'none'}; apex: {', '.join(apex_lines) or '-'}")
    return 0


def cmd_table(ctx: Ctx, _args: argparse.Namespace) -> int:
    cols = ("engine", "load", "KV type", "decode 512/8k", "prefill 512/8k", "swap s", "released", "notes")
    rows: list[tuple[str, ...]] = []
    for key in ctx.core_keys():
        res = load_result(ctx, key)
        if res is None:
            rows.append((key, "-", "-", "-", "-", "-", "-", "not tested"))
            continue
        notes = []
        if res.arbiter_class == "apex":
            notes.append(f"V22 {res.v22_result or '?'}")
        if res.warnings:
            notes.append("warn")
        if res.prefill_8k_note:
            notes.append(f"8k {res.prefill_8k_note.split(':')[0]}")
        if res.load_error:
            notes.append(res.load_error[:40])
        rows.append((key, "ok" if res.load_ok else "FAIL", res.kv_applied or res.kv_requested or "-",
                     f"{fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)}",
                     f"{fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)}", fmt_num(res.swap_s),
                     "yes" if res.released else ("NO" if res.released is False else "?"), "; ".join(notes) or "-"))
    widths = [max(len(str(r[i])) for r in [cols, *rows]) for i in range(len(cols))]
    for r in [cols, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)), file=sys.stderr)
    co = ctx.results_dir / "coresident.json"
    if co.is_file():
        d = json.loads(co.read_text(encoding="utf-8"))
        print(f"two-residency: {d.get('summary', '-')}", file=sys.stderr)
    return 0


# --- two-residency (Section 17 step 3; V21, V14b) ---------------------------------------------------------------------


def cmd_coresident(ctx: Ctx, args: argparse.Namespace) -> int:
    text_key, vision_key = args.text, args.vision
    tspec, vspec = ctx.spec(text_key), ctx.spec(vision_key)
    ctl = Control(ctx.orch_url)
    baseline = read_baseline(ctx)
    out: dict[str, Any] = {"text": text_key, "vision": vision_key, "control_mode": ctl.mode, "tested_at": now_iso()}
    for k in ctx.core_keys():
        if unit_active(k):
            log(f"coresident: {k} is resident; unloading it first")
            ctl.unload(k)
            wait_release(baseline, k)
    loaded: list[str] = []
    used_after: dict[str, int] = {}  # GTT used right after each load, so each unload has a measured release target

    def cleanup() -> None:
        # Reverse order: after unloading the vision engine the counter must be back where it was with the text engine
        # alone; after unloading the text engine it must be back at the baseline (Section 4.2 rule 5, per engine).
        for i, k in reversed(list(enumerate(loaded))):
            ctl.unload(k)
            target = baseline if i == 0 else used_after.get(loaded[i - 1], baseline)
            ok, _, note = wait_release(target, k)
            out[f"released_{k}"] = ok
            out[f"release_note_{k}"] = note
        # Restore the vision engine's stand-alone rendering (8 slots); the orchestrator decides co-residency live.
        ctx.render(vision_key, [], ["coresident"])

    def finish(v21: tuple[str, str], v14b: tuple[str, str]) -> int:
        cleanup()
        out["v21"] = v21
        out["v14b"] = v14b
        out["summary"] = f"V21 {v21[0]}: {v21[1][:160]} | V14b {v14b[0]}: {v14b[1][:120]}"
        (ctx.results_dir / "coresident.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        record("V21", *v21)
        record("V14b", *v14b)
        return 0

    tenv = ctx.render(text_key, [], ["coresident"])
    venv = ctx.render(vision_key, [("coresident", "true")], [])  # parallel_coresident / ctx_size_coresident
    if venv.get("ATLAS_CORESIDENT") != "1":
        raise Infra(f"engine-env.py did not render {vision_key} with ATLAS_CORESIDENT=1")
    for key, env in ((text_key, tenv), (vision_key, venv)):
        if env.get("ATLAS_MODEL_PRESENT") != "1":
            return finish(("fail", f"{key}: model file not present ({env.get('ATLAS_MODEL_FILE')})"),
                          ("fail", f"{key} not loadable"))
        try:
            secs = ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), int(env["LLAMA_ARG_PORT"]))
        except RuntimeError as exc:
            out[f"load_error_{key}"] = str(exc)
            loaded.append(key)  # stop it in cleanup even if half-loaded
            beside = loaded[0] if loaded[0] != key else "nothing"
            return finish(("fail", f"{key} failed to load beside {beside}: {str(exc)[:200]}"),
                          ("fail", f"{key} not loaded; queueing not testable"))
        loaded.append(key)
        time.sleep(3)
        used_after[key] = gtt_used_bytes()
        log(f"coresident: {key} loaded in {secs:.0f}s (ctx {env['ATLAS_CTX_SIZE']}, parallel {env['ATLAS_PARALLEL']}); "
            f"GTT used {fmt_gb(used_after[key])}")
    time.sleep(5)
    used = gtt_used_bytes()
    delta = used - baseline
    weights = (float(tspec["footprint_gb"]) + float(vspec["footprint_gb"])) * GB  # Section 4.1: 63 + 79 = 142 GB
    low, high = 0.85 * weights, weights + 28 * GB + 10 * GB  # 4.1: "~28 GB for both caches", plus slack
    residency_ok = low <= delta <= high
    mem_msg = (f"GTT delta {fmt_gb(delta)} for {text_key}+{vision_key} (weights {fmt_gb(weights)}, tolerance "
               f"{fmt_gb(low)}-{fmt_gb(high)}; used {fmt_gb(used)}, baseline {fmt_gb(baseline)}, "
               f"vision at parallel {venv['ATLAS_PARALLEL']}, ctx {venv['ATLAS_CTX_SIZE']})")
    out.update({"gtt_used_bytes": used, "gtt_delta_bytes": delta, "weights_bytes": int(weights),
                "residency_ok": residency_ok})
    log(f"coresident: {'OK' if residency_ok else 'OUT OF TOLERANCE'}: {mem_msg}")

    if ctl.mode != "orchestrator":
        why = f"queueing not provable: {ctl.fallback_reason} (only the Arbiter queues a second generation)"
        return finish(("fail", f"{mem_msg}; {why}"), ("fail", why))

    # Solo timings of each engine (direct, no Arbiter) give the wall-clock reference for the concurrent run.
    tport, vport = int(tenv["LLAMA_ARG_PORT"]), int(venv["LLAMA_ARG_PORT"])
    q_text = [{"role": "user", "content": "Write a 250-word essay about the history of lighthouses."}]
    q_vision = [{"role": "user", "content": "Describe, in about 150 words, how a suspension bridge carries its load."}]
    solo_t = chat(f"http://127.0.0.1:{tport}", text_key, q_text, 256)
    solo_v = chat(f"http://127.0.0.1:{vport}", vision_key, q_vision, 160)
    if solo_t["error"] or solo_v["error"]:
        why = f"solo generation failed: text={solo_t['error'][:120]!r} vision={solo_v['error'][:120]!r}"
        return finish(("fail", f"{mem_msg}; {why}"), ("fail", why))
    d_t = solo_t["t_recv"] - solo_t["t_send"]
    d_v = solo_v["t_recv"] - solo_v["t_send"]
    log(f"coresident: solo durations text {d_t:.1f}s, vision {d_v:.1f}s")

    # Two requests at once THROUGH THE ORCHESTRATOR; the Arbiter must run the second only after the first finished.
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
        results[name] = chat(ctx.orch_url, model, msgs, max_tokens)

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
    out["concurrent"] = {"first": {k: v for k, v in a.items() if k != "content"},
                         "second": {k: v for k, v in b.items() if k != "content"},
                         "queue_seen": seen_queue[:5], "generating_seen": sorted(seen_generating)}
    if a["error"] or b["error"]:
        why = (f"orchestrator chat failed: first(model={text_key})={a['error'][:140]!r} "
               f"second(model={vision_key})={b['error'][:140]!r} "
               "(contract: model = engine key, see loadtest.py header)")
        return finish(("fail", f"{mem_msg}; {why}"), ("fail", why))
    proof = ""
    ok = False
    ta, tb = a["timings"], b["timings"]
    if tb.get("prompt_ms") is not None and tb.get("predicted_ms") is not None:
        busy_b = (float(tb["prompt_ms"]) + float(tb["predicted_ms"])) / 1000.0
        start_b = b["t_recv"] - busy_b
        gap = start_b - a["t_recv"]
        ok = gap >= -1.0
        wall_a, wall_b = a["t_recv"] - a["t_send"], b["t_recv"] - b["t_send"]
        busy_a = (float(ta.get("prompt_ms", 0)) + float(ta.get("predicted_ms", 0))) / 1000.0 if ta else None
        proof = (f"timings: second started {gap:+.1f}s relative to the first's completion "
                 f"(second busy {busy_b:.1f}s of {wall_b:.1f}s wall; first {wall_a:.1f}s wall"
                 + (f", first busy {busy_a:.1f}s" if busy_a is not None else "") + ")")
    else:
        wall_b = b["t_recv"] - b["t_send"]
        ok = wall_b >= (a["t_recv"] - a["t_send"]) + 0.8 * d_v - 1.0
        proof = (f"wall-clock (no timings passed through): second took {wall_b:.1f}s vs {d_v:.1f}s solo while the "
                 f"first took {a['t_recv'] - a['t_send']:.1f}s")
    if seen_queue:
        proof += f"; Arbiter status showed a generation queue ({seen_queue[0]})"
    if seen_generating:
        proof += f"; generating seen: {sorted(seen_generating)}"
    log(f"coresident: queueing {'PROVEN' if ok else 'NOT proven'}: {proof}")
    verb = "queued behind" if ok else "did NOT wait for"
    v14b = ("pass" if ok else "fail",
            f"second generation request ({vision_key}) {verb} the first ({text_key}); {proof}")
    v21 = ("pass" if (ok and residency_ok) else "fail",
           f"{mem_msg}; {'queueing proven' if ok else 'queueing NOT proven'}"
           f"{'' if residency_ok else '; memory out of tolerance'}")
    rc = finish(v21, v14b)
    for k in loaded:
        if out.get(f"released_{k}") is False:
            record("V10", "fail",
                   f"{k}: memory not released after the two-residency test ({out.get(f'release_note_{k}')})")
    return rc


# --- main -------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", required=True, help="config/engines.json")
    ap.add_argument("--env-dir", required=True, help="$ATLAS_ETC/engines")
    ap.add_argument("--results-dir", required=True, help="$ATLAS_STATE/phase3/results")
    ap.add_argument("--orch-url", required=True, help="http://127.0.0.1:$ORCH_PORT")
    ap.add_argument("--engine-env", required=True, help="phase2/engine-env.py")
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--slots-dir", required=True)
    ap.add_argument("--port-base", type=int, required=True)
    ap.add_argument("--overrides", required=True, help="$ATLAS_ETC/engines/overrides.json")
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
    ctx = Ctx(engines_path=Path(args.engines), env_dir=Path(args.env_dir), results_dir=Path(args.results_dir),
              orch_url=args.orch_url, engine_env=Path(args.engine_env), models_dir=Path(args.models_dir),
              slots_dir=Path(args.slots_dir), port_base=args.port_base, overrides=Path(args.overrides))
    try:
        ctx.load_engines()
        return int(args.fn(ctx, args))
    except Infra as exc:
        log(f"FATAL: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
