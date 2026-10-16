"""The Engine Arbiter — Section 4.2 rules 1-9, the hard pre-flight of Section 4.3 and the OOM backstop.

Every load and unload of a weight-bearing process passes through one Arbiter instance (rule: "a single service inside
the orchestrator"). The unit tests (tests/test_arbiter.py, V14a) drive it with StubController and StubProbe; Phase 3
drives it against real engines (V10, V14b, V21).

Rule map (the numbers are Section 4.2's; v0.4.0 changes approved by the Principal on 2026-10-16 are marked):
  1  Ledger: `Arbiter.resident` holds each resident engine with its projected and, once known, measured footprint.
  2  `request_load(engine, ctx, parallel)` projects weights + KV + compute buffers against the live budget and returns
     granted / queued / refused.
  3  At most `max_resident` LLM seats resident (D-32, S-23: Settings.arbiter_max_resident, default 1, so every seat
     runs alone with the whole budget); always-resident companions, class-external loads and Phase 4 containers never
     count against it. Exactly one generation at a time (`acquire_generation`).
  4  A second generation request queues FIFO behind the running one (`try_generation` says "queued"; the blocking
     form waits its turn).
  5  Release confirmation: after a stop the GTT counter is polled until it drops within `release_tolerance_bytes`
     of the expected value; `ReleaseTimeout` is raised loudly after `release_timeout_s`.
  6  Never preempt mid-generation: an eviction whose victim is generating makes the request "queued".
  7  Exclusivity (S-23: rule 7 extended from the Apex engine to every LLM seat at the default limit of 1): loading a
     seat unloads every other resident seat first, and nothing co-resides with it; a later request for another seat
     evicts it when it is idle (rule 5 path) or queues while it generates (rule 6). Residency is exclusive; it is not
     permanent. At a limit of 2 (D-32 option (b)) only the Apex engine (deepseek-v4-flash) is exclusive.
  8  `plan_deep_think(tier)` pre-requests the full footprint and downgrades deep -> standard -> quick when it does
     not fit.
  9  Every decision is logged (structured line) and written to ledger.arbiter_decisions with the task id.

Budget (Section 4.1): budget = pool - GPU resident set - host resident set - headroom, where pool = min(GTT total,
MemTotal). Kernel 7.0.0-38 reports the requested 192 GiB as the GTT total even though the node has less RAM; 7.0.0-39
and later cap it at MemTotal themselves (S47), and MemTotal is the real limit either way because Phase 1 step 4 puts
kho=off on the kernel line (S49): Ubuntu's 7.0 kernel has kexec handover on by default, and it then keeps about 13 GiB
of MemTotal (INFERRED) as CMA scratch that TTM/GTT pages can never use, so the pool would overstate what engines can get
by that much; V3a fails while any of it is set aside. The GPU resident set is what mem_info_gtt_used shows before any
engine is loaded, measured by `measure_resident_set()` at startup and again whenever nothing is resident: GPU buffer
objects, i.e. the router (v0.4.0: the only companion left on the iGPU) and whatever else sits there. The host resident
set (D-14 option (e), approved 2026-10-16) is the always-resident companions with placement "cpu", the Qwen3 4B
retrieval pair: their memory is host RAM the GTT counter never shows, so each is charged the bytes of its weights in
full (file-backed pages the kernel may drop and re-read, so VmRSS can show less than the file) plus its anonymous memory
(RssAnon + RssShmem of the unit's MainPID, when the controller can name it: the KV cache and compute buffers, which are
never reclaimable), never below its projection (weights + KV + compute buffers at its engines.json profile), which
stands in when the unit cannot be measured (`measure_host_companions`); it is re-measured with the GPU set, the pids
and /proc reads outside the state lock. Ordinary CPU memory (Ubuntu, XFCE, Docker, Open WebUI,
ChromaDB, Kokoro/Whisper/PyAnnote, the orchestrator stack) shares the same unified RAM but appears on neither counter
(only memory a process hands the GPU through userptr or a dma-buf import shows on the GTT one), so `headroom_bytes`
reserves it (v0.3.5, S44: Settings.arbiter_headroom_bytes, default config.DEFAULT_ARBITER_HEADROOM_GIB = 16 GiB, set
by build_arbiter; the constructor's 0 is for the stub tests only).
Each measurement also reads /proc/meminfo when the probe can: MemTotal - MemAvailable - (GPUActive + GPUReclaim where
the kernel reports them, else gtt_used) is the CPU-side use at that moment (`host_used_bytes()`, which `atlas-admin
arbiter status` prints too), shown in status() as host_used_bytes and logged at WARNING when, less the CPU companions'
anonymous share of the host resident set (their clean file pages count as available, so only the anonymous share is
inside that figure; `host_unreserved_bytes`, one rule for both), it exceeds the headroom (the reserve is then too small
for this node: raise ATLAS_ARBITER_HEADROOM_GIB). It is observed, never budgeted from, so a transient CPU job cannot
shrink the budget between two identical requests.

Phase 4 engines (Section 15.2, CONVENTIONS.md §8 class `phase4`). build_arbiter() merges config/phase4-engines.json into
the Arbiter's engine map so Phase 4 step 5 (POST /arbiter/register) records each container engine's measured footprint
under a known key (rule 1); they run in their own containers, so request_load() on one is REFUSED with that reason.
A registration for a key in neither file is UnknownEngine (fix round 3: §8 makes phase4-engines.json the single list of
Phase 4 engines, and a typo such as 'ui-tars-1.5-7b' for the real key 'ui-tars' must fail the registration, not split
one engine's footprint across two ledger keys); `register_measured(..., allow_unknown=True)` is the one escape hatch,
for a driver path that measured a real process the config does not list yet: bounded (MAX_REQUEST_SPECS), logged at
WARNING and written to the ledger as such. The API route (POST /arbiter/register, another writer) does not pass it, so
an unknown key is a 404 there.

External engines (Section 4.2 "Chatterbox when invoked"; config/engines.json `external` list, CONVENTIONS.md §8 class
`external`). Chatterbox is loaded in-process by phase2/voice_render.py and the orchestrator's voice path, never as a
llama-server unit, so the Arbiter budgets it (rule 2: weights only, the measured peak RSS once POST /arbiter/register
has recorded it), never counts it against the LLM-seat limit (rule 3, D-32: a TTS render must not evict the seat for
a slot it does not take), evicts LRU victims for it only when the budget needs their memory (rule 5 per victim) and
ledgers every decision (rules 1, 9) but calls NO controller for it: the caller owns the process and POSTs
/arbiter/unload when it has exited. RELEASE (rule 5's substance, "by polling the memory counters, not by trusting the
process exit", with the right counter for a CPU process; fix round 6): its release is not observable on the GTT
counter (a CPU process on the unified memory of the APU is charged to the ledger, not to mem_info_gtt_used), so
`request_unload(key, pid=<the process>)` polls /proc/<pid> (`process_rss_bytes`) until the process is gone or its
VmRSS is within `release_tolerance_bytes`, bounded by `release_timeout_s`; on timeout the engine STAYS resident and
charged, an ERROR row is written and ReleaseTimeout is raised WITHOUT halting the Arbiter (the process is the caller's;
the caller retries the unload once it has really exited). An unload WITHOUT a pid is accepted on the caller's word,
said so in the reason ("not measured") and logged at WARNING: the step-5 renders and voice_render.py pass the pid; the
API route (POST /arbiter/unload, another writer) is asked to forward a `pid` field. An external resident is never chosen
as an eviction victim: a load that would need its memory is QUEUED with that reason until the caller unloads it. An
external process is not an LLM seat, so seat exclusivity (rule 7) never evicts it and never evicts a seat for it: only
the budget does (v0.4.0; in v0.3.5 an Apex request queued behind any external resident).

Failure paths (fix round). A controller start that raises — EngineControlError from systemctl, or the parent
EngineError from the post-start /health check — releases the reservation and clears `_busy`; a catch-all does the same
for anything else, so a placeholder can never wedge the Arbiter. A controller stop that raises leaves the victim
resident and SERVING again (unloading cleared, an error row in the ledger, the exception re-raised): the unit may well
still be active, and the next request_unload/request_load can retry once the cause (the sudoers fragment, a hung unit)
is fixed.

What the projection follows (fix round). `systemctl start llama-server@<key>` runs the flags of
$ATLAS_ENGINES_ENV_DIR/<key>.env (ATLAS_CTX_SIZE, ATLAS_PARALLEL, ATLAS_KV_TYPE), rendered by phase2/engine-env.py from
engines.json plus overrides.json — the orchestrator (user atlas) cannot re-render that root-owned file. So when the env
file exists the Arbiter projects against exactly those values (the "hard pre-flight, the actual OOM backstop" of
Section 4.3 then guards the real load) and refuses a request whose explicit ctx/parallel/kv argument disagrees with it.
Without an env file (unit tests, an engine not rendered yet) the projection comes from engines.json's ctx_size x
parallel and kv_class. S-22 (v0.4.0) removed the standing co-resident profile of S52 with the pairing it served: an
ATLAS_CORESIDENT line in an older env file is ignored.

Locking (fix round). One state lock (`_cv`, a Condition) guards the ledger; the controller's stop/start and the
release poll — minutes on a cold load — run OUTSIDE it. A load reserves its budget under the lock by inserting a
placeholder Resident (`loading=True`, charged at the projection) and marking its victims `unloading=True`, sets `_busy`,
releases the lock, does the I/O, then re-acquires to finalise. Every other entry point (status, generation lock, the
FIFO, register/confirm) takes the short state lock only, so /health answers and a generation on the other resident
engine can finish and release while a swap is in flight. A second load/unload while `_busy` is set is "queued".
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from atlas.config import DEFAULT_ARBITER_MAX_RESIDENT, PHASE4_MODE, EngineSpec, parse_env_file
from atlas.engines import UNIT_KEY_RE, EngineController, EngineError
from atlas.ledger import Ledger

log = logging.getLogger("atlas.arbiter")

GIB = 1024**3
MAX_RESIDENT = DEFAULT_ARBITER_MAX_RESIDENT  # Section 4.2 rule 3, LLM seats (D-32: 1; Settings.arbiter_max_resident)
# register_measured(allow_unknown=True) may create at most this many specs per process (16.3 item 6: configuration
# changes are the Principal's; a measurement for a key the config does not list is tolerated, never unbounded).
MAX_REQUEST_SPECS = 32
APEX_KEY = "deepseek-v4-flash"  # CONVENTIONS.md §8 arbiter class `apex`: DeepSeek-V4-Flash-0731 UD-Q8_K_XL (S-42)

# --- KV-cache size model (Section 4.3; S-17, v0.4.0) -----------------------------------------------------------------
#
# Bytes of K+V per token of the TOTAL pool at f16, read from the GGUF headers and the cache code of the pinned
# llama.cpp (b11474 = b9acf138; research_notes/ATLAS v0.4.0 build record/engine_data.md, 2026-10-16, VERIFIED against
# src/llama-model.cpp create_memory, llama-kv-cache.cpp, llama-memory-hybrid-idx.cpp, llama-kv-cache-dsv4.cpp). The
# projection is an OOM backstop (4.3 "Hard pre-flight"), replaced by the measured footprint once an engine has been
# loaded (rule 1), and Phase 3 step 4's kv-estimates to-do sets it beside each engine's own load log.
#   glm-5.3-flash: hybrid_idx; 11 DSA layers keep a K-only MLA cache (512) plus an indexer cache (384) per token:
#       11 x 896 x 2 B = 19,712 B/token f16 (10,472 at q8_0); the NextN layer is outside the main context.
#   deepseek-v4-flash: dsv4; 21 CSA layers (compressed K 512/4 + indexer K 128/4 per token) and 20 HCA layers (512/128):
#       21 x 320 + 20 x 8 = 6,880 B/token f16 (3,655 at q8_0). v0.3.5 charged 327,680 (an estimate ~48x too high).
#   gpt-oss-120b: iSWA, 18 full-attention layers x (K 512 + V 512) x 2 B = 36,864 B/token f16; the 18 sliding layers are
#       a fixed window per slot (KV_STATE_BYTES_PER_SEQUENCE). v0.3.5 charged both halves the full pool (73,728).
#   gpt-oss-120b-abliterated: the v0.3.5 figure, unchanged with the uncensored entry (this revision leaves it alone).
#   router-qwen3.5-4b: resident, never budgeted (Section 4.1): 8 full-attention layers, 32,768 B/token f16.
#   embed-qwen3-4b, rerank-qwen3-4b: causal qwen3, 36 layers x 8 KV heads x 128 x K and V x 2 B = 147,456 B/token f16;
#       unlike the non-causal bge pair they allocate a real cache, charged in the host resident set (D-14 (e)).
# A phased engine brings its own figure in engines.json (kv_bytes_per_token_f16), never a key here before it exists.
KV_BYTES_PER_TOKEN_F16: dict[str, int] = {
    "gpt-oss-120b": 36_864,
    "gpt-oss-120b-abliterated": 73_728,
    "glm-5.3-flash": 19_712,
    "deepseek-v4-flash": 6_880,
    "router-qwen3.5-4b": 32_768,
    "embed-qwen3-4b": 147_456,
    "rerank-qwen3-4b": 147_456,
}
# Context memory that does not grow with the context, charged once per slot (llama.cpp sizes it by n_seq_max =
# --parallel), at f16 for the K parts and f32 for the recurrent and compressor states (build record engine_data.md,
# VERIFIED code paths, sizes INFERRED by arithmetic): GLM's 34 KDA recurrent layers (152,633,344 B per sequence);
# DeepSeek's raw sliding-window K (43 layers x 512 x 2 B x 768 cells at -ub 512: 33,816,576 B) plus its compressor
# states (12,206,080 B); gpt-oss's 18 sliding-window layers (18 x 1024 x 2 B x 2,304 cells at -ub 2048: 84,934,656 B).
# The window parts grow with -ub (cells = GGML_PAD(window + n_ubatch, 256)); the figures are the Day 1 defaults, an
# estimate that the Phase 3 memory record checks against the load log. engines.json kv_state_bytes_per_sequence
# overrides a key.
KV_STATE_BYTES_PER_SEQUENCE: dict[str, int] = {
    "glm-5.3-flash": 152_633_344,
    "deepseek-v4-flash": 33_816_576 + 12_206_080,
    "gpt-oss-120b": 84_934_656,
    "router-qwen3.5-4b": 52_690_944,
}
# ggml block layouts (VERIFIED, ggml-common.h): q8_0 = 34 bytes per 32 values (8.5 bit), q4_0 = 18 bytes per 32
# values (4.5 bit). f16 is an engines.json class since D-29 (v0.4.0); `none` renders no --cache-type flag, so an engine
# with a cache then runs llama.cpp's default f16 (kv_estimate_bytes charges it so).
KV_CLASS_FACTOR: dict[str, float] = {
    "f32": 2.0, "f16": 1.0, "bf16": 1.0, "q8_0": 8.5 / 16, "q4_0": 4.5 / 16, "none": 0.0,
}
# S-17: the compute buffers a llama-server allocates beside its weights and cache (Vulkan0 and host compute buffers,
# the mmproj's image encoder). No log of them exists for any Day 1 engine on gfx1151 (research: "the 9 GiB allowance is
# inferred from two whole-footprint measurements, not from logs"), so these are planning figures, INFERRED: 2 GiB for an
# LLM seat, 0.5 GiB for a retrieval companion at -ub 2048. engines.json compute_buffer_gib overrides a key; Phase 3
# records the logged figures (S52 memory record, to-do kv-estimates) and the measured footprint replaces the whole
# projection once the engine has been loaded (rule 1).
DEFAULT_COMPUTE_BUFFER_GIB = 2.0
DEFAULT_COMPANION_COMPUTE_BUFFER_GIB = 0.5
# Section 9.1 / S-43 (approved 2026-10-16): the Deep Think pair is GLM-5.3-Flash (generator) and DeepSeek-V4-Flash-0731
# (adversary; the Apex engine, also the deep tier's synthesis). The deep tier's third opinion on documents runs on
# gpt-oss-120b on Day 1, the stand-in for Qwen3.8-Flash-Next, whose pull is phased. atlas.deep_think names the same
# keys.
DEEP_THINK_GENERATOR = "glm-5.3-flash"
DEEP_THINK_THIRD_OPINION = "gpt-oss-120b"


class ArbiterError(RuntimeError):
    pass


class ReleaseTimeout(ArbiterError):
    """Rule 5: the GTT counter did not drop after an unload. Nothing else loads until a human looks (rule §7.4)."""


class UnknownEngine(ArbiterError):
    pass


class Decision(StrEnum):
    GRANTED = "granted"
    QUEUED = "queued"
    REFUSED = "refused"
    ERROR = "error"


class MemoryProbe(Protocol):
    def gtt_used_bytes(self) -> int: ...

    def gtt_total_bytes(self) -> int: ...

    def host_meminfo(self) -> tuple[int, int, int | None] | None:
        """(MemTotal, MemAvailable, GPUActive + GPUReclaim or None) in bytes, or None when the probe cannot tell."""
        ...


def host_used_bytes(mem_total: int, mem_available: int, gpu_pages: int | None, gtt_used: int) -> int:
    """CPU-side memory in use (Section 4.1, S44), the figure the Arbiter warns on when it exceeds its headroom:
    MemTotal - MemAvailable less the GPU's pages, which are not reclaimable and so sit inside that difference. The
    GPU's pages are GPUActive + GPUReclaim (the TTM pool pages) when the kernel reports them; else the GTT counter read
    at the same moment, which counts BO sizes and misses pages TTM keeps pooled after a free, so it over-states CPU use
    slightly after an unload. Never below 0. One rule for the Arbiter (`_observe_host`) and `atlas-admin arbiter
    status`."""
    gpu = gpu_pages if gpu_pages is not None else gtt_used
    return max(0, mem_total - mem_available - gpu)


def process_rss_bytes(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """VmRSS of a live process in bytes, None once /proc/<pid> is gone: the release counter of a class-external (CPU)
    process, Section 4.2 rule 5 (module docstring "External engines"). A zombie has no VmRSS line: its memory is
    released (0)."""
    try:
        text = (proc_root / str(int(pid)) / "status").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return 0


def process_anon_bytes(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """RssAnon + RssShmem of a live process in bytes (/proc/<pid>/status, kB there; the fields phase3/loadtest.py's
    read_proc_status records): the memory the kernel cannot drop and re-read from a file. For a CPU-placed companion
    that is its KV cache and compute buffers; its Q8_0 weights stay file-backed (mmap). None once /proc/<pid> is gone
    or when the kernel reports neither field."""
    try:
        text = (proc_root / str(int(pid)) / "status").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    total, found = 0, False
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name in ("RssAnon", "RssShmem"):
            parts = value.split()
            if parts and parts[0].isdigit():
                total += int(parts[0]) * 1024
                found = True
    return total if found else None


def host_unreserved_bytes(host_used: int, host_resident: dict[str, dict[str, Any]]) -> int:
    """The CPU-side use the headroom must cover (S44): `host_used_bytes` less the CPU companions' anonymous share
    (`anon_bytes` of measure_host_companions). Their weights are clean file pages, which the kernel counts as available,
    so they were never inside host_used. One rule for the Arbiter (`_observe_host`) and `atlas-admin arbiter status`."""
    return host_used - sum(max(0, int(d.get("anon_bytes", 0))) for d in host_resident.values())


class SysfsMemoryProbe:
    """/sys/class/drm/card*/device/mem_info_gtt_{used,total} in bytes (research §5.2, VERIFIED; lib/common.sh twin)."""

    def __init__(self, drm_root: Path = Path("/sys/class/drm"), meminfo: Path = Path("/proc/meminfo")) -> None:
        self.drm_root = drm_root
        self.meminfo = meminfo
        self._device: Path | None = None

    def device_dir(self) -> Path:
        if self._device is None:
            for card in sorted(self.drm_root.glob("card[0-9]*")):
                if "-" in card.name:  # connector nodes (card0-DP-1)
                    continue
                vendor = card / "device" / "vendor"
                try:
                    if vendor.read_text().strip() == "0x1002":
                        self._device = card / "device"
                        break
                except OSError:
                    continue
            if self._device is None:
                raise ArbiterError(f"no AMD GPU (vendor 0x1002) under {self.drm_root}; is amdgpu bound?")
        return self._device

    def _read(self, attr: str) -> int:
        path = self.device_dir() / attr
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError) as exc:
            raise ArbiterError(f"cannot read {path}: {exc}") from exc

    def gtt_used_bytes(self) -> int:
        return self._read("mem_info_gtt_used")

    def gtt_total_bytes(self) -> int:
        return self._read("mem_info_gtt_total")

    def host_meminfo(self) -> tuple[int, int, int | None] | None:
        """MemTotal, MemAvailable and, where the kernel has them, GPUActive + GPUReclaim from /proc/meminfo (kB there);
        None if MemTotal or MemAvailable is missing or the file is unreadable. The GPU fields are the TTM pool pages,
        in use or pooled after a free (Ubuntu 7.0.0-38 and mainline v7.1 onward; absent before)."""
        try:
            text = self.meminfo.read_text(encoding="ascii", errors="replace")
        except OSError:
            return None
        kb: dict[str, int] = {}
        for line in text.splitlines():
            name, _, rest = line.partition(":")
            parts = rest.split()
            if name in ("MemTotal", "MemAvailable", "GPUActive", "GPUReclaim") and parts and parts[0].isdigit():
                kb[name] = int(parts[0])
        if "MemTotal" not in kb or "MemAvailable" not in kb:
            return None
        gpu = (kb["GPUActive"] + kb.get("GPUReclaim", 0)) * 1024 if "GPUActive" in kb else None
        return kb["MemTotal"] * 1024, kb["MemAvailable"] * 1024, gpu


class StubProbe:
    """Test double with settable counters (bytes). host_meminfo() answers only when both host figures are set."""

    def __init__(self, total_bytes: int, used_bytes: int = 0, *, mem_total_bytes: int | None = None,
                 mem_available_bytes: int | None = None, gpu_pages_bytes: int | None = None) -> None:
        self.total_bytes = total_bytes
        self.used_bytes = used_bytes
        self.mem_total_bytes = mem_total_bytes
        self.mem_available_bytes = mem_available_bytes
        self.gpu_pages_bytes = gpu_pages_bytes

    def gtt_used_bytes(self) -> int:
        return self.used_bytes

    def gtt_total_bytes(self) -> int:
        return self.total_bytes

    def host_meminfo(self) -> tuple[int, int, int | None] | None:
        if self.mem_total_bytes is None or self.mem_available_bytes is None:
            return None
        return self.mem_total_bytes, self.mem_available_bytes, self.gpu_pages_bytes


@dataclass(frozen=True)
class Footprint:
    weights_bytes: int
    kv_bytes: int
    ctx: int
    parallel: int
    kv_class: str
    measured: bool = False
    compute_bytes: int = 0  # S-17: the compute-buffer term (0 once measured: the measurement includes it)

    @property
    def total_bytes(self) -> int:
        return self.weights_bytes + self.kv_bytes + self.compute_bytes

    @property
    def total_gib(self) -> float:
        return self.total_bytes / GIB


@dataclass(frozen=True)
class UnitProfile:
    """What `systemctl start llama-server@<key>` will actually run: the values of <engines_env_dir>/<key>.env."""

    ctx: int
    parallel: int
    kv_type: str
    path: Path

    def describe(self) -> str:
        return f"{self.path.name}: ctx {self.ctx} x {self.parallel} slots, kv {self.kv_type}"


def read_unit_profile(path: Path) -> UnitProfile | None:
    """Parse phase2/engine-env.py's env file; None when it does not exist, ArbiterError when it is malformed."""
    if not path.is_file():
        return None
    env = parse_env_file(path)
    missing = [k for k in ("ATLAS_CTX_SIZE", "ATLAS_PARALLEL", "ATLAS_KV_TYPE") if not env.get(k)]
    if missing:
        raise ArbiterError(f"{path}: missing {missing}; not rendered by phase2/engine-env.py? Re-run Phase 2 step 1")
    try:
        ctx, parallel = int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"])
    except ValueError as exc:
        raise ArbiterError(f"{path}: ATLAS_CTX_SIZE/ATLAS_PARALLEL are not integers ({exc})") from exc
    kv_type = env["ATLAS_KV_TYPE"]
    if kv_type not in KV_CLASS_FACTOR:
        raise ArbiterError(f"{path}: ATLAS_KV_TYPE {kv_type!r} has no size factor (known: {sorted(KV_CLASS_FACTOR)})")
    # S-22: an ATLAS_CORESIDENT line (an env file rendered before v0.4.0) is ignored; the file's ctx and parallel are
    # what the unit runs either way.
    return UnitProfile(ctx=ctx, parallel=parallel, kv_type=kv_type, path=path)


@dataclass
class Resident:
    key: str
    spec: EngineSpec
    footprint: Footprint
    loaded_at: float
    last_used: float
    observed_bytes: int = 0  # GTT delta seen when the engine loaded; the release target of rule 5
    measured_bytes: int | None = None
    loading: bool = False  # placeholder: budget reserved, `systemctl start` in flight outside the lock
    unloading: bool = False  # stop/release poll in flight; still charged until the counter confirms the release

    @property
    def charged_bytes(self) -> int:
        """What the ledger charges against the budget: the measurement when there is one, else the projection."""
        return self.measured_bytes if self.measured_bytes is not None else self.footprint.total_bytes

    @property
    def serving(self) -> bool:
        return not self.loading and not self.unloading


@dataclass(frozen=True)
class LoadDecision:
    decision: Decision
    engine: str
    task_id: str | None
    projected_bytes: int
    budget_bytes: int
    free_bytes: int
    reason: str
    evicted: tuple[str, ...] = ()

    @property
    def granted(self) -> bool:
        return self.decision is Decision.GRANTED


@dataclass(frozen=True)
class DeepThinkPlan:
    requested: str
    granted: str
    engines: tuple[str, ...]
    required_bytes: int
    budget_bytes: int
    reason: str
    task_id: str | None = None

    @property
    def downgraded(self) -> bool:
        return self.requested != self.granted


@dataclass(frozen=True)
class _LoadPlan:
    """A reserved load: the placeholder is in `resident`, the victims are marked, `_busy` is set."""

    key: str
    spec: EngineSpec
    footprint: Footprint
    victims: tuple[str, ...]
    task_id: str | None
    profile: UnitProfile | None
    resident_small: bool = False
    external: bool = False  # Section 4.2 class external: budgeted and ledgered, no controller start/stop


DEEP_THINK_TIERS: tuple[str, ...] = ("deep", "standard", "quick")  # Section 9.1, highest first


def kv_estimate_bytes(spec: EngineSpec, ctx: int, parallel: int, kv_class: str | None = None) -> int:
    """K+V bytes for a TOTAL pool of `ctx` tokens (research conflict 9: n_ctx is split across `parallel` slots).

    `parallel` does not multiply the size (the pool is already the total); it is validated so a caller who passes a
    per-slot figure by mistake is told so instead of under-estimating. kv_class `none` on an engine with a cache (a
    causal embedder or reranker) is charged at f16, llama.cpp's default when no --cache-type flag is rendered.
    """
    if parallel < 1:
        raise ArbiterError(f"{spec.key}: parallel must be >= 1 (never auto; gguf-models.md §1.3)")
    if ctx < 256 * parallel:
        raise ArbiterError(f"{spec.key}: ctx {ctx} is the TOTAL pool and leaves < 256 tokens per slot for {parallel} "
                           "slots; did you pass ctx_per_slot? (engines.json ctx_rule)")
    kv_class = kv_class or spec.kv_class
    if kv_class not in KV_CLASS_FACTOR:
        raise ArbiterError(f"{spec.key}: unknown kv class {kv_class!r}")
    per_token = spec.kv_bytes_per_token_f16
    if per_token is None:
        per_token = KV_BYTES_PER_TOKEN_F16.get(spec.key)
    if per_token is None:
        raise ArbiterError(f"{spec.key}: no KV size model (add kv_bytes_per_token_f16 to engines.json or register a "
                           "measured footprint)")
    factor = KV_CLASS_FACTOR["f16"] if kv_class == "none" else KV_CLASS_FACTOR[kv_class]
    return int(per_token * ctx * factor)


def kv_state_bytes(spec: EngineSpec, parallel: int) -> int:
    """The per-sequence context memory that does not grow with ctx (recurrent, sliding-window and compressor state),
    once per slot (S-17; KV_STATE_BYTES_PER_SEQUENCE, or engines.json kv_state_bytes_per_sequence)."""
    per_seq = spec.kv_state_bytes_per_sequence
    if per_seq is None:
        per_seq = KV_STATE_BYTES_PER_SEQUENCE.get(spec.key, 0)
    return int(per_seq) * max(1, parallel)


def compute_buffer_bytes(spec: EngineSpec) -> int:
    """The compute-buffer term of a projection (S-17): engines.json compute_buffer_gib, else the planning default for
    an LLM seat or a retrieval companion (DEFAULT_COMPUTE_BUFFER_GIB, DEFAULT_COMPANION_COMPUTE_BUFFER_GIB)."""
    if spec.compute_buffer_gib is not None:
        return int(spec.compute_buffer_gib * GIB)
    if spec.mode in ("chat", "vision"):
        return int(DEFAULT_COMPUTE_BUFFER_GIB * GIB)
    return int(DEFAULT_COMPANION_COMPUTE_BUFFER_GIB * GIB)


def companion_projection(spec: EngineSpec) -> Footprint:
    """A CPU-placed companion at its engines.json profile: weights (exact bytes, D-31) + KV + per-slot state +
    compute buffers (D-14 (e), S-17): what the host resident set charges while the unit cannot be measured."""
    kv = kv_estimate_bytes(spec, spec.ctx_size, spec.parallel) + kv_state_bytes(spec, spec.parallel)
    return Footprint(spec.footprint_bytes, kv, spec.ctx_size, spec.parallel, spec.kv_class,
                     compute_bytes=compute_buffer_bytes(spec))


def measure_host_companions(engines: dict[str, EngineSpec], *, pid_of: Callable[[str], int | None] | None,
                            anon_of: Callable[[int], int | None] = process_anon_bytes,
                            rss_of: Callable[[int], int | None] = process_rss_bytes) -> dict[str, dict[str, Any]]:
    """The host resident set of the always-resident companions with placement "cpu" (D-14 (e)), per companion:
    `charged_bytes`, `projected_bytes`, `anon_bytes` (charged less the weights: the share that is anonymous memory)
    and `source`. The weights are file-backed (mmap on the CPU backend), so VmRSS falls below weights + anonymous
    memory once the kernel reclaims some of their pages (a 146-151 GiB seat load streams through the page cache):
    they are charged in full by bytes, and the anonymous memory as measured (RssAnon + RssShmem of the unit's MainPID),
    never below its projection (KV + compute). Without those fields the VmRSS stands in, never below the projection;
    without a pid, the projection. It does I/O (`pid_of` is SystemdEngineController.main_pid, a systemctl subprocess),
    so the Arbiter calls it outside its state lock; `atlas-admin arbiter status` calls it too."""
    detail: dict[str, dict[str, Any]] = {}
    for spec in engines.values():
        if not (spec.is_resident and spec.placement == "cpu"):
            continue
        weights = spec.footprint_bytes
        projection = companion_projection(spec).total_bytes
        projected_anon = projection - weights
        pid: int | None = None
        if pid_of is not None:
            try:
                pid = pid_of(spec.key)
            except Exception as exc:  # a status read must never stop a measurement; the projection stands in
                log.warning("arbiter: MainPID of %s unreadable (%s); charging its projection", spec.key, exc)
        anon = anon_of(pid) if pid else None
        rss = rss_of(pid) if pid and anon is None else None
        if anon is not None:
            charged = weights + max(anon, projected_anon)
            source = (f"weights {weights / GIB:.2f} GiB (file-backed, budgeted in full) + measured RssAnon+RssShmem "
                      f"{anon / GIB:.2f} GiB of pid {pid}, never below its projected KV + compute "
                      f"{projected_anon / GIB:.2f} GiB")
        elif rss is not None:
            charged = max(rss, projection)
            source = (f"measured VmRSS {rss / GIB:.2f} GiB of pid {pid} (no RssAnon), never below its projection "
                      f"{projection / GIB:.2f} GiB")
        else:
            charged, source = projection, "projected (weights + KV + compute; unit not running or pid unknown)"
        detail[spec.key] = {"charged_bytes": charged, "projected_bytes": projection,
                            "anon_bytes": charged - weights, "source": source}
    return detail


class Arbiter:
    def __init__(self, engines: dict[str, EngineSpec], controller: EngineController, probe: MemoryProbe, *,
                 ledger: Ledger | None = None, release_timeout_s: float = 60.0,
                 release_tolerance_bytes: int = GIB, poll_interval_s: float = 1.0,
                 resident_set_bytes: int | None = None, headroom_bytes: int = 0,
                 deep_think_engines: dict[str, Sequence[str]] | None = None,
                 engines_env_dir: Path | None = None, max_resident: int = MAX_RESIDENT,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 rss_of: Callable[[int], int | None] = process_rss_bytes,
                 pid_of: Callable[[str], int | None] | None = None,
                 anon_of: Callable[[int], int | None] = process_anon_bytes) -> None:
        if max_resident < 1:
            raise ArbiterError(f"max_resident {max_resident} must be >= 1 LLM seat (Section 4.2 rule 3, D-32)")
        self.engines = engines
        self.controller = controller
        self.probe = probe
        self.max_resident = max_resident  # rule 3: LLM seats resident at once (D-32; build_arbiter reads the setting)
        self._rss_of = rss_of  # /proc/<pid> VmRSS: the release counter of a class-external process (rule 5)
        # The MainPID of a companion's unit (SystemdEngineController.main_pid): its RssAnon + RssShmem (anon_of) is the
        # measured anonymous share of a CPU-placed companion (D-14 (e)); None (the stub tests) charges the projection.
        self._pid_of = pid_of
        self._anon_of = anon_of
        self.ledger = ledger
        self.release_timeout_s = release_timeout_s  # research §6.3: time-box 60 s
        self.release_tolerance_bytes = release_tolerance_bytes  # research §6.3: within ~1 GiB of the baseline
        self.poll_interval_s = poll_interval_s
        self.headroom_bytes = headroom_bytes  # the host reserve (module docstring "Budget"); build_arbiter sets it
        self._host_used_bytes: int | None = None  # CPU-side use at the last measurement (observed, never budgeted)
        self._mem_total_bytes: int | None = None  # MemTotal: caps the pool when the GTT total exceeds it (7.0.0-38)
        self._host_over = False  # WARNING once when host use first exceeds the headroom, not on every re-measure
        # $ATLAS_ENGINES_ENV_DIR (Settings.engines_env_dir): the unit env files the projection follows when present.
        self.engines_env_dir = engines_env_dir
        self._clock = clock
        self._sleep = sleep
        self._resident_set_bytes = resident_set_bytes
        self._host_resident_bytes = 0  # D-14 (e): the CPU-placed companions, charged beside the GPU resident set
        self._host_resident: dict[str, dict[str, Any]] = {}  # per companion: charged bytes and where they came from
        self.resident: dict[str, Resident] = {}  # rule 1, insertion order = load order
        self.measured: dict[str, int] = {}  # measured total footprints by engine key (Phase 3 / Phase 4 step 5)
        # (ctx, parallel, kv) each figure in `measured` was taken at, when the engine was resident at registration
        # (S52 fix round): a measurement is charged only to that profile; without an entry, engines.json's profile.
        self.measured_profile: dict[str, tuple[int, int, str]] = {}
        self._request_specs: set[str] = set()  # keys register_measured(allow_unknown=True) created (MAX_REQUEST_SPECS)
        self._cv = threading.Condition(threading.RLock())  # the state lock; never held across controller I/O
        self._busy: str | None = None  # "loading <key>" / "unloading <key>" while the I/O runs outside the lock
        self._gen_holder: str | None = None  # task id
        self._gen_engine: str | None = None
        self._gen_queue: deque[str] = deque()  # FIFO tickets (rule 4)
        self._halted: str | None = None  # set by a ReleaseTimeout; every later load is refused with this reason
        self._warned_no_env: set[str] = set()
        self.deep_think_engines: dict[str, tuple[str, ...]] = {
            # 9.1 (S-43): Standard plus a second expansion and scoring round, the third opinion on documents and the
            # Apex engine (the adversary's own engine) delivering the final synthesis — run one at a time, requested up
            # front (rule 8).
            "deep": (DEEP_THINK_GENERATOR, APEX_KEY, DEEP_THINK_THIRD_OPINION),
            "standard": (DEEP_THINK_GENERATOR, APEX_KEY),  # 9.1: Ren generates on GLM, Arthur scores on DeepSeek
            "quick": (),  # 9.1: the currently loaded engine, no swap
        }
        if deep_think_engines:
            self.deep_think_engines.update({k: tuple(v) for k, v in deep_think_engines.items()})

    # --- budget (Section 4.1) --------------------------------------------------------------------------------------

    def measure_resident_set(self) -> int:
        """Record what the GTT counter shows before any engine loads: the always-resident GPU set (Section 4.1 table),
        and beside it the host resident set of the CPU-placed companions (D-14 (e); module docstring "Budget").

        The companions' pids and /proc figures are read OUTSIDE the state lock (module docstring: the lock is never
        held across controller I/O; main_pid is a systemctl subprocess with a 30 s timeout, once per companion), then
        committed under it after re-checking that nothing became resident meanwhile."""
        with self._cv:
            if self.resident:
                raise ArbiterError("measure_resident_set() must run before any engine is resident")
        companions = measure_host_companions(self.engines, pid_of=self._pid_of, anon_of=self._anon_of,
                                             rss_of=self._rss_of)
        with self._cv:
            if self.resident:
                raise ArbiterError("measure_resident_set(): an engine became resident while the companions were read")
            self._resident_set_bytes = self.probe.gtt_used_bytes()
            self._host_resident = companions
            self._host_resident_bytes = sum(d["charged_bytes"] for d in companions.values())
            self._observe_host()
            host = "n/a" if self._host_used_bytes is None else f"{self._host_used_bytes / GIB:.2f}"
            log.info("arbiter resident_set_gib=%.2f host_resident_set_gib=%.2f gtt_total_gib=%.2f pool_gib=%.2f "
                     "headroom_gib=%.2f budget_gib=%.2f host_used_gib=%s max_resident_seats=%d",
                     self._resident_set_bytes / GIB, self._host_resident_bytes / GIB,
                     self.probe.gtt_total_bytes() / GIB, self.pool_bytes / GIB, self.headroom_bytes / GIB,
                     self.budget_bytes / GIB, host, self.max_resident)
            return self._resident_set_bytes

    def companion_projection(self, key: str) -> Footprint:
        """A CPU-placed companion at its engines.json profile (module-level companion_projection)."""
        return companion_projection(self.spec(key))

    def _observe_host(self) -> None:
        """Records MemTotal (it caps the pool) and the CPU-side use beside the GPU's pages: TTM pages backing GTT
        objects are not reclaimable, so they sit inside MemTotal - MemAvailable and are taken out again (observed)."""
        host = self.probe.host_meminfo()
        if host is None:
            self._host_used_bytes = None
            return
        mem_total, mem_available, gpu_pages = host
        self._mem_total_bytes = mem_total
        # GPUActive + GPUReclaim when the kernel reports them; else the GTT counter, which measure_resident_set() has
        # just read into the resident set (nothing is resident then).
        self._host_used_bytes = host_used_bytes(mem_total, mem_available, gpu_pages, self._resident_set_bytes or 0)
        # The CPU-placed companions' anonymous memory is inside that figure but already charged as the host resident
        # set (D-14 (e)); their weights are clean file pages the kernel counts as available, so only the anonymous
        # share is taken out (host_unreserved_bytes, the rule atlas-admin arbiter status applies too).
        unreserved = host_unreserved_bytes(self._host_used_bytes, self._host_resident)
        anon = self._host_used_bytes - unreserved
        over = unreserved > self.headroom_bytes
        if over and not self._host_over:
            log.warning("arbiter: CPU-side memory in use is %.2f GiB (%.2f GiB beside the CPU companions' %.2f GiB of "
                        "anonymous memory in the host resident set), above the %.2f GiB headroom the budget reserves "
                        "for it (Section 4.1); engines may be granted memory the host needs. Raise "
                        "ATLAS_ARBITER_HEADROOM_GIB in orchestrator.env and restart atlas-orchestrator",
                        self._host_used_bytes / GIB, unreserved / GIB, anon / GIB, self.headroom_bytes / GIB)
        self._host_over = over

    @property
    def resident_set_bytes(self) -> int:
        if self._resident_set_bytes is None:
            raise ArbiterError("resident set not measured: call measure_resident_set() at startup (Section 4.1)")
        return self._resident_set_bytes

    @property
    def pool_bytes(self) -> int:
        """The memory engines and the resident set share: the GTT total, but never more than MemTotal. Kernel 7.0.0-38
        reports the requested 192 GiB even though the node has less (S47); 7.0.0-39 and later cap it themselves.
        All of MemTotal can back GTT pages only with kexec handover off (kho=off, S49, gated by V3a): its CMA scratch,
        about 13 GiB inside MemTotal when it is on, is memory TTM never gets."""
        total = self.probe.gtt_total_bytes()
        return total if self._mem_total_bytes is None else min(total, self._mem_total_bytes)

    @property
    def host_resident_set_bytes(self) -> int:
        """The CPU-placed companions' charge (D-14 (e)), set by measure_resident_set(); 0 when there are none."""
        return self._host_resident_bytes

    @property
    def budget_bytes(self) -> int:
        return max(0, self.pool_bytes - self.resident_set_bytes - self._host_resident_bytes - self.headroom_bytes)

    @property
    def charged_bytes(self) -> int:
        with self._cv:
            return sum(r.charged_bytes for r in self.resident.values())

    @property
    def free_bytes(self) -> int:
        return self.budget_bytes - self.charged_bytes

    @property
    def busy(self) -> str | None:
        """The load/unload whose controller I/O is in flight, or None."""
        with self._cv:
            return self._busy

    # --- footprints (rule 2) ---------------------------------------------------------------------------------------

    def spec(self, key: str) -> EngineSpec:
        try:
            return self.engines[key]
        except KeyError as exc:
            raise UnknownEngine(f"unknown engine {key!r} (CONVENTIONS.md §8 keys)") from exc

    def _footprint(self, spec: EngineSpec, ctx: int, parallel: int, kv_class: str) -> Footprint:
        if spec.is_resident:
            # Never budgeted directly (Section 4.1): a GPU companion is in the measured GPU resident set, a CPU one in
            # the host resident set (D-14 (e)).
            return Footprint(0, 0, ctx, parallel, kv_class)
        if spec.is_phase4 or spec.is_external:
            # Containers (Section 15.2) and in-process external loads (Section 4.2 "Chatterbox when invoked"): weights
            # only, the measurement when the driver registered one (phase2/05-voice.sh: peak RSS of the CPU render).
            if spec.key in self.measured:
                return Footprint(self.measured[spec.key], 0, 0, 1, "none", measured=True)
            return Footprint(spec.footprint_bytes, 0, 0, 1, "none")
        # Phase 3 step 2 measures the rendered unit. A measurement applies only to the profile it was measured at
        # (measured_profile); one registered while the engine was not resident is taken as engines.json's profile.
        measured_at = self.measured_profile.get(spec.key, (spec.ctx_size, spec.parallel, spec.kv_class))
        if spec.key in self.measured and (ctx, parallel, kv_class) == measured_at:
            return Footprint(self.measured[spec.key], 0, ctx, parallel, kv_class, measured=True)
        kv = kv_estimate_bytes(spec, ctx, parallel, kv_class) + kv_state_bytes(spec, parallel)
        return Footprint(spec.footprint_bytes, kv, ctx, parallel, kv_class, compute_bytes=compute_buffer_bytes(spec))

    def projected_footprint(self, key: str, ctx: int | None = None, parallel: int | None = None,
                            kv_class: str | None = None) -> Footprint:
        """The engines.json projection at ctx_size x parallel and kv_class unless the caller names other values (the
        Phase 3 KV ladder, the load test's memory record). See unit_profile() for what the unit runs."""
        spec = self.spec(key)
        return self._footprint(spec, spec.ctx_size if ctx is None else ctx,
                               spec.parallel if parallel is None else parallel, kv_class or spec.kv_class)

    def unit_profile(self, key: str) -> UnitProfile | None:
        """<engines_env_dir>/<key>.env as phase2/engine-env.py rendered it, or None (no dir, or not rendered yet)."""
        spec = self.spec(key)
        if self.engines_env_dir is None or spec.is_resident or spec.is_phase4 or spec.is_external:
            return None
        profile = read_unit_profile(Path(self.engines_env_dir) / f"{key}.env")
        if profile is None and key not in self._warned_no_env:
            self._warned_no_env.add(key)
            log.warning("arbiter engine=%s no env file under %s; projecting from engines.json (systemctl start will "
                        "fail until phase2/engine-env.py has rendered it)", key, self.engines_env_dir)
        return profile

    def unit_footprint(self, key: str) -> tuple[Footprint, UnitProfile] | None:
        """The projection for what the unit will really run, or None when there is no env file."""
        profile = self.unit_profile(key)
        if profile is None:
            return None
        return self._footprint(self.spec(key), profile.ctx, profile.parallel, profile.kv_type), profile

    def register_measured(self, key: str, total_bytes: int, *, task_id: str | None = None,
                          allow_unknown: bool = False) -> None:
        """Phase 3 step 2 / Phase 4 step 5: replace the estimate with the measured footprint (rule 1), at the profile
        (ctx, parallel, kv) the engine is resident at when it is registered, or at engines.json's profile when it is not
        resident (measured_profile, S52 fix round). Held in memory only: nothing replays it after a restart.

        `key` must be in engines.json or phase4-engines.json (CONVENTIONS.md §8: one list of names); anything else is
        UnknownEngine, so a typo in a registration fails loudly and the key gets added to config/phase4-engines.json
        (rule §7.4). allow_unknown=True accepts a bare unit-style key the config does not list as a class `phase4` spec
        built from the request (footprint = the measurement), at most MAX_REQUEST_SPECS per process, logged at WARNING
        and recorded in the ledger as "created from request"; a key that is not a bare unit-style name is UnknownEngine
        either way.
        """
        if int(total_bytes) < 0:
            raise ArbiterError(f"cannot register {key!r}: total_bytes {total_bytes} is negative")
        with self._cv:
            created = False
            if key not in self.engines:
                if not UNIT_KEY_RE.fullmatch(key):
                    raise UnknownEngine(f"cannot register {key!r}: not an engine key ([A-Za-z0-9._-]+; "
                                        "CONVENTIONS.md §8)")
                if not allow_unknown:
                    raise UnknownEngine(f"cannot register {key!r}: in neither engines.json nor phase4-engines.json "
                                        "(CONVENTIONS.md §8: add it to config/phase4-engines.json; the Arbiter does "
                                        "not invent engine keys)")
                if len(self._request_specs) >= MAX_REQUEST_SPECS:
                    raise UnknownEngine(f"cannot register {key!r}: {MAX_REQUEST_SPECS} request-created engine specs "
                                        f"already exist ({sorted(self._request_specs)}); add the keys to "
                                        "config/phase4-engines.json")
                self.engines[key] = EngineSpec(key=key, mode=PHASE4_MODE, arbiter_class="phase4", kv_class="none",
                                               footprint_gb=int(total_bytes) / GIB, ctx_size=0, parallel=1,
                                               notes="created from a register_measured(allow_unknown=True) call; not "
                                                     "in config/phase4-engines.json")
                self._request_specs.add(key)
                created = True
                log.warning("arbiter engine=%s registered as class phase4 from the request (allow_unknown): it is in "
                            "neither engines.json nor phase4-engines.json; add it to config/phase4-engines.json "
                            "(%d/%d request-created specs)", key, len(self._request_specs), MAX_REQUEST_SPECS)
            self.measured[key] = int(total_bytes)
            res = self.resident.get(key)
            if res is not None:
                res.measured_bytes = int(total_bytes)
                # The profile the figure belongs to (S52 fix round): a measurement at one ctx x parallel x kv is never
                # charged to a unit re-rendered at another (an overrides.json change, the KV ladder's rungs).
                self.measured_profile[key] = (res.footprint.ctx, res.footprint.parallel, res.footprint.kv_class)
            else:
                self.measured_profile.pop(key, None)
            self._log("measure", key, Decision.GRANTED, task_id, int(total_bytes),
                      reason=f"measured footprint {total_bytes / GIB:.2f} GiB recorded "
                             f"(class {self.engines[key].arbiter_class})"
                             + ("; WARNING: spec created from request, key not in config/phase4-engines.json"
                                if created else ""))

    def confirm_loaded(self, key: str, *, task_id: str | None = None) -> int:
        """After a real load: measure the counter delta and record it as the engine's footprint."""
        with self._cv:
            res = self.resident.get(key)
            if res is None:
                raise ArbiterError(f"{key} is not resident; nothing to confirm")
            if not res.serving:
                what = "loading" if res.loading else "unloading"
                raise ArbiterError(f"{key} is still {what}; nothing to confirm yet")
            others = sum(r.charged_bytes for k, r in self.resident.items() if k != key)
            measured = max(0, self.probe.gtt_used_bytes() - self.resident_set_bytes - others)
            if measured > res.footprint.total_bytes + self.release_tolerance_bytes:
                log.warning("arbiter engine=%s measured_gib=%.2f exceeds projection_gib=%.2f (the KV model under-"
                            "estimates this engine; the measurement now replaces it)", key, measured / GIB,
                            res.footprint.total_gib)
            self.register_measured(key, measured, task_id=task_id)
            return measured

    # --- loads (rules 2, 3, 5, 6, 7) -------------------------------------------------------------------------------

    def request_load(self, key: str, ctx: int | None = None, parallel: int | None = None, *,
                     task_id: str | None = None, wait_s: float | None = None,
                     kv_class: str | None = None) -> LoadDecision:
        """Grant, queue or refuse a load. Granted means the engine is resident and serving when this returns.

        wait_s=None returns "queued" immediately when a running generation blocks an eviction (rules 4, 6) or another
        load/unload is in flight; a number waits up to that long and retries.
        """
        deadline = None if wait_s is None else self._clock() + wait_s
        while True:
            with self._cv:
                outcome = self._plan_load(key, ctx, parallel, task_id, kv_class)
                if isinstance(outcome, LoadDecision):
                    if outcome.decision is not Decision.QUEUED or deadline is None:
                        return outcome
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return outcome
                    self._cv.wait(timeout=min(remaining, 1.0))
                    continue
                plan = outcome
            # The budget is reserved and _busy is set: the slow part runs with the lock released.
            return self._execute_load(plan)

    def _plan_load(self, key: str, ctx: int | None, parallel: int | None, task_id: str | None,
                   kv_class: str | None) -> LoadDecision | _LoadPlan:
        """Under the lock: decide, and when the answer is a load, reserve it (placeholder + victims + _busy)."""
        spec = self.spec(key)
        if spec.is_phase4:
            # Section 15.2: a container engine run by phase4/engines/<key>.sh, not a llama-server unit; the Arbiter
            # holds its measured footprint (step 5) and nothing else. Loading it here would build a unit that does not
            # exist, so the answer is a reason, never a systemctl call.
            return self._decide(Decision.REFUSED, key, task_id, self._footprint(spec, 0, 1, "none").total_bytes,
                                "Phase 4 container engine (class phase4): run by its own container (Section 15.2), "
                                "registered with the Arbiter for its footprint only; not loadable as a llama-server "
                                "unit")
        if spec.is_resident:
            # Resident small models are started by systemd at boot and never counted (Section 4.1, 5.3).
            if self._busy:
                return self._decide(Decision.QUEUED, key, task_id, 0, f"arbiter busy: {self._busy}")
            self._busy = f"starting resident {key}"
            return _LoadPlan(key, spec, Footprint(0, 0, spec.ctx_size, spec.parallel, spec.kv_class), (), task_id,
                             None, resident_small=True)
        if self._halted:
            return self._decide(Decision.REFUSED, key, task_id, 0, f"arbiter halted: {self._halted}")
        res = self.resident.get(key)
        if res is not None:
            if res.loading:
                return self._decide(Decision.QUEUED, key, task_id, res.footprint.total_bytes,
                                    f"load of {key} already in progress")
            if res.unloading:
                return self._decide(Decision.QUEUED, key, task_id, res.footprint.total_bytes,
                                    f"unload of {key} in progress; retry when it has released")
            fp0 = res.footprint
            mismatch = [f"{name} {want!r} != resident {have!r}" for name, want, have in (
                ("ctx", ctx, fp0.ctx), ("parallel", parallel, fp0.parallel), ("kv_class", kv_class, fp0.kv_class),
            ) if want is not None and want != have]
            if mismatch:
                # A swap is free only at the profile that is running; a different one needs an unload first.
                return self._decide(Decision.REFUSED, key, task_id, fp0.total_bytes,
                                    f"resident at ctx {fp0.ctx} x {fp0.parallel} slots, kv {fp0.kv_class}; "
                                    f"requested {', '.join(mismatch)}: request_unload() first")
            res.last_used = self._clock()
            return self._decide(Decision.GRANTED, key, task_id, res.footprint.total_bytes,
                                "already resident (swap is free)")
        if self._busy:
            return self._decide(Decision.QUEUED, key, task_id, 0, f"arbiter busy: {self._busy}")
        budget = self.budget_bytes
        # 1. What will actually load: the unit's env file when it exists, else the engines.json profile.
        unit = self.unit_footprint(key)
        profile: UnitProfile | None = None
        if unit is not None:
            fp, profile = unit
            mismatch = [f"{name} {want!r} != unit {have!r}" for name, want, have in (
                ("ctx", ctx, profile.ctx), ("parallel", parallel, profile.parallel),
                ("kv_class", kv_class, profile.kv_type),
            ) if want is not None and want != have]
            if mismatch:
                return self._decide(Decision.REFUSED, key, task_id, fp.total_bytes,
                                    f"requested {', '.join(mismatch)}: the unit runs {profile.describe()}; change "
                                    "it with phase2/engine-env.py --set-override (root), the Arbiter cannot")
        else:
            # engines.json's profile unless the caller names one, as engine-env.py will render it.
            fp = self.projected_footprint(key, ctx, parallel, kv_class)
        if fp.total_bytes > budget:
            return self._decide(Decision.REFUSED, key, task_id, fp.total_bytes,
                                f"projected {fp.total_gib:.1f} GiB exceeds the engine budget {budget / GIB:.1f} GiB "
                                "(Section 4.2 rule 2)" + (f"; unit runs {profile.describe()}" if profile else ""))
        # 2. Evictions, planned against that footprint (rule 3, the LLM-seat limit; rule 7, exclusive seats leave first;
        #    then LRU for memory; rule 6 says queued when a victim generates).
        victims = self._plan_evictions(spec, fp)
        if victims is None:
            return self._decide(Decision.QUEUED, key, task_id, fp.total_bytes,
                                f"generation in progress on {self._gen_engine} (task {self._gen_holder}); "
                                "never preempted (Section 4.2 rule 6)")
        if isinstance(victims, str):
            # An external resident (Section 4.2 "Chatterbox when invoked") cannot be stopped by the Arbiter: queued.
            return self._decide(Decision.QUEUED, key, task_id, fp.total_bytes, victims)
        # 3. Reserve: the placeholder is charged at the projection so nothing else can claim the memory meanwhile.
        now = self._clock()
        self.resident[key] = Resident(key=key, spec=spec, footprint=fp, loaded_at=now, last_used=now, loading=True,
                                      measured_bytes=self.measured.get(key) if fp.measured else None)
        for victim in victims:
            self.resident[victim].unloading = True
        self._busy = f"loading {key}" + (f" (evicting {', '.join(victims)})" if victims else "")
        return _LoadPlan(key, spec, fp, tuple(victims), task_id, profile, external=spec.is_external)

    def _execute_load(self, plan: _LoadPlan) -> LoadDecision:
        """Outside the lock: stop the victims (rule 5 per victim), start the engine, then finalise under the lock."""
        key, task_id = plan.key, plan.task_id
        if plan.resident_small:
            try:
                if not self.controller.is_active(key):
                    self.controller.start(key)
            finally:
                with self._cv:
                    self._busy = None
                    self._cv.notify_all()
            return self._decide(Decision.GRANTED, key, task_id, 0, "resident small model; not budgeted (Section 4.1)")
        stage = "load"
        try:
            for victim in plan.victims:
                why = f"evicted for {key}"
                if plan.spec.is_llm_seat and self.engines[victim].is_llm_seat and (
                        self._exclusive(plan.spec) or self._exclusive(self.engines[victim])):
                    why += (" (Section 4.2 rule 7 with rule 3, D-32: an LLM seat runs alone, so the idle one leaves "
                            "for the next)")
                stage = f"eviction of {victim}"
                self._stop_and_confirm(victim, task_id, reason=why)
            used_before = self.probe.gtt_used_bytes()
            stage = "start"
            if not plan.external:
                # Section 4.2 class external: the caller runs the process (Chatterbox, own venv); nothing to start.
                self.controller.start(key)
        except ReleaseTimeout:
            with self._cv:
                self.resident.pop(key, None)  # nothing was started; the reservation is void
                self._release_victims(plan.victims)
                self._busy = None
                self._cv.notify_all()
            raise
        except EngineError as exc:
            # EngineControlError (systemctl failed) or the parent EngineError (the post-start /health check in
            # SystemdEngineController.start): either way nothing is serving and the reservation must go (fix round).
            with self._cv:
                self.resident.pop(key, None)
                self._release_victims(plan.victims)
                self._busy = None
                self._cv.notify_all()
                self._decide(
                    Decision.ERROR,
                    key,
                    task_id,
                    plan.footprint.total_bytes,
                    f"{stage} failed; {key} not loaded, reservation released: {exc}",
                )
            raise
        except BaseException as exc:
            # Anything else (a bug, KeyboardInterrupt in a test): the placeholder must never wedge the Arbiter.
            with self._cv:
                self.resident.pop(key, None)
                self._release_victims(plan.victims)
                self._busy = None
                self._cv.notify_all()
                self._decide(
                    Decision.ERROR,
                    key,
                    task_id,
                    plan.footprint.total_bytes,
                    f"{stage} aborted: {type(exc).__name__}: {exc}",
                )
            raise
        # What the counter actually grew by is what rule 5 must see come back after the stop. An external load is
        # charged from the ledger (projection, or the registered peak RSS), not from the GTT counter (module docstring).
        observed = 0 if plan.external else max(0, self.probe.gtt_used_bytes() - used_before)
        with self._cv:
            res = self.resident[key]
            now = self._clock()
            res.loading = False
            res.loaded_at = res.last_used = now
            res.observed_bytes = observed
            self._busy = None
            self._cv.notify_all()
            if plan.external:
                how = (f"external process, class external (Section 4.2 'Chatterbox when invoked'): "
                       f"{plan.footprint.total_gib:.2f} GiB charged from the ledger"
                       f"{' (measured)' if plan.footprint.measured else ' (engines.json projection)'}; the caller runs "
                       "it and POSTs /arbiter/unload when it exits")
            else:
                how = (
                    f"unit env {plan.profile.describe()}"
                    if plan.profile
                    else f"ctx {plan.footprint.ctx} x {plan.footprint.parallel} slots, kv {plan.footprint.kv_class}"
                )
            return self._decide(
                Decision.GRANTED,
                key,
                task_id,
                plan.footprint.total_bytes,
                f"loaded ({how})",
                evicted=plan.victims,
            )

    def _exclusive(self, spec: EngineSpec) -> bool:
        """Rule 7 (S-23): an LLM seat co-resides with no other seat. Every seat at the default limit of 1 (D-32); at a
        limit of 2 (option (b)) the Apex engine alone."""
        return spec.is_llm_seat and (self.max_resident <= 1 or spec.is_apex)

    def _plan_evictions(self, spec: EngineSpec, fp: Footprint) -> list[str] | str | None:
        """Which residents must go so `spec` fits; None when a needed victim is generating (rule 6); a str (the
        queued reason) when a needed victim is an external resident the Arbiter cannot stop (module docstring).

        D-32 / S-23: only LLM seats count against `max_resident` and only a seat triggers seat exclusivity; a
        class-external load evicts a seat only when the budget needs that seat's memory (LRU)."""
        seats = [k for k, r in self.resident.items() if r.spec.is_llm_seat]
        victims: list[str] = []
        if spec.is_llm_seat:
            # Rule 7: an exclusive seat (every seat at a limit of 1) unloads every other seat first; at a limit of 2 a
            # resident exclusive seat (the Apex engine) is still the first victim of any other seat's load (idle ->
            # evicted here through rule 5; generating -> None below, rule 6).
            victims = list(seats) if self._exclusive(spec) else [k for k in seats if self._exclusive(
                self.resident[k].spec)]
        free = self.free_bytes + sum(self.resident[k].charged_bytes for k in victims)
        count = len([k for k in seats if k not in victims])
        # LRU next (Section 6.3 groups work by engine; the least recently used is the cheapest to lose), for the seat
        # limit (rule 3) and for memory (rule 2).
        for k, r in sorted(self.resident.items(), key=lambda kv: kv[1].last_used):
            if k in victims:
                continue
            over_limit = spec.is_llm_seat and count >= self.max_resident
            if not over_limit and free >= fp.total_bytes:
                break
            if over_limit and free >= fp.total_bytes and not r.spec.is_llm_seat:
                continue  # unloading a non-seat frees memory that is not short, never a seat slot
            victims.append(k)
            free += r.charged_bytes
            if r.spec.is_llm_seat:
                count -= 1
        if any(self._is_generating(v) for v in victims):
            return None
        external = [v for v in victims if self.resident[v].spec.is_external]
        if external:
            return (f"external engine(s) {', '.join(external)} resident (Section 4.2 class external: run by their "
                    "caller, not stoppable by the Arbiter); retry after the caller's POST /arbiter/unload")
        return victims

    def _release_victims(self, victims: Sequence[str]) -> None:
        """Under the lock: victims still resident after a failed load are serving again (their stop never happened,
        or _stop_and_confirm already said why it failed)."""
        for v in victims:
            res = self.resident.get(v)
            if res is not None and res.unloading:
                res.unloading = False

    def _is_generating(self, key: str) -> bool:
        return self._gen_holder is not None and self._gen_engine == key

    def request_unload(self, key: str, *, task_id: str | None = None, wait_s: float | None = None,
                       pid: int | None = None) -> LoadDecision:
        """Unload `key` (rule 5 release check inside). `pid`: for a class-external engine, the caller's process, polled
        in /proc until it is gone or its RSS is back (module docstring "External engines"); ignored for a unit."""
        deadline = None if wait_s is None else self._clock() + wait_s
        while True:
            with self._cv:
                res = self.resident.get(key)
                if res is None:
                    return self._decide(Decision.GRANTED, key, task_id, 0, "not resident; nothing to unload")
                if self._busy or not res.serving:
                    decision = self._decide(Decision.QUEUED, key, task_id, 0, f"arbiter busy: {self._busy}")
                elif self._is_generating(key):
                    decision = self._decide(Decision.QUEUED, key, task_id, 0,
                                            f"generating (task {self._gen_holder}); never preempted (rule 6)")
                else:
                    res.unloading = True
                    self._busy = f"unloading {key}"
                    break
                if deadline is None or deadline - self._clock() <= 0:
                    return decision
                self._cv.wait(timeout=min(deadline - self._clock(), 1.0))
        try:
            note = self._stop_and_confirm(key, task_id, reason="unload requested", pid=pid)
        finally:
            with self._cv:
                self._busy = None
                self._cv.notify_all()
        if res.spec.is_external:
            return self._decide(Decision.GRANTED, key, task_id, 0,
                                f"unloaded; external process (class external) {note}")
        return self._decide(Decision.GRANTED, key, task_id, 0, "unloaded; memory release confirmed")

    def _release_external(self, key: str, res: Resident, task_id: str | None, reason: str, pid: int | None) -> str:
        """Section 4.2 rule 5 for a class-external process (module docstring "External engines"): with a pid, poll
        /proc/<pid> until the process is gone or its RSS is within the tolerance, bounded by release_timeout_s (timeout:
        the engine stays resident and charged, ERROR row, ReleaseTimeout without halting); without one, the caller's
        word, said so. Runs with the lock RELEASED. Returns the confirmation note for the decision."""
        if pid is None:
            note = ("released on the caller's word: no pid supplied, release NOT measured (pass the process pid in "
                    "POST /arbiter/unload so /proc/<pid> is polled, Section 4.2 rule 5)")
            log.warning("arbiter: %s unloaded without a pid; its release was not measured (Section 4.2 rule 5)", key)
        else:
            deadline = self._clock() + self.release_timeout_s
            rss = self._rss_of(pid)
            while rss is not None and rss > self.release_tolerance_bytes:
                if self._clock() >= deadline:
                    msg = (f"process {pid} of {key} still holds {rss / GIB:.2f} GiB RSS after "
                           f"{self.release_timeout_s:.0f}s; {key} stays resident and charged (Section 4.2 rule 5: the "
                           "release is confirmed by measurement, not by the caller's word)")
                    with self._cv:
                        res.unloading = False
                        self._decide(Decision.ERROR, key, task_id, res.charged_bytes, msg, action="unload")
                        self._cv.notify_all()
                    raise ReleaseTimeout(msg + "; the Arbiter is not halted: retry the unload once the process has "
                                         "exited (the pid is the caller's, not a unit)")
                self._sleep(self.poll_interval_s)
                rss = self._rss_of(pid)
            note = (f"release confirmed: process {pid} gone" if rss is None
                    else f"release confirmed: process {pid} RSS {rss / GIB:.2f} GiB within tolerance")
        with self._cv:
            del self.resident[key]
            self._decide(Decision.GRANTED, key, task_id, res.charged_bytes,
                         f"{reason}; external process (class external) {note}; GTT counter not the instrument",
                         action="unload")
            self._cv.notify_all()
        return note

    def _stop_and_confirm(self, key: str, task_id: str | None, *, reason: str, pid: int | None = None) -> str:
        """Stop the unit and poll the counter until the memory is back (rule 5). Runs with the lock RELEASED; the
        engine stays in `resident` (unloading=True, still charged) until the release is confirmed. Returns a note."""
        with self._cv:
            res = self.resident[key]
            external = res.spec.is_external
        if external:
            # Section 4.2 class external: no unit to stop; the release counter is the process itself (/proc/<pid>).
            return self._release_external(key, res, task_id, reason, pid)
        before = self.probe.gtt_used_bytes()
        try:
            self.controller.stop(key)
        except EngineError as exc:
            # The stop did not happen (sudoers fragment missing, systemctl timed out, ...): the engine is still
            # resident and, as far as the Arbiter can tell, still serving. Say so, give it back, and let the caller
            # retry once the cause is fixed — never leave it flagged `unloading` for the life of the process.
            with self._cv:
                res.unloading = False
                self._decide(Decision.ERROR, key, task_id, res.charged_bytes,
                             f"stop failed; {key} stays resident and serving (check `systemctl is-active "
                             f"llama-server@{key}`): {exc}", action="unload")
                self._cv.notify_all()
            raise
        # Rule 5: trust the counter, not the process exit. The engine's share is the delta observed at its load (or the
        # measurement when one replaced it); the counter must fall back by that much, within the tolerance.
        share = res.measured_bytes if res.measured_bytes is not None else res.observed_bytes
        target = max(self.resident_set_bytes, before - share) + self.release_tolerance_bytes
        deadline = self._clock() + self.release_timeout_s
        used = self.probe.gtt_used_bytes()
        while used > target:
            if self._clock() >= deadline:
                halted = (f"GTT not released after stopping {key}: {used / GIB:.2f} GiB still used, "
                          f"expected <= {target / GIB:.2f} GiB after {self.release_timeout_s:.0f}s")
                with self._cv:
                    self._halted = halted
                    del self.resident[key]  # the process is gone; the memory is not, and the Arbiter says so
                    self._decide(Decision.ERROR, key, task_id, res.charged_bytes, halted)
                    self._cv.notify_all()
                raise ReleaseTimeout(halted + " (Section 4.2 rule 5; check journalctl -u llama-server@"
                                     f"{key} and /sys/class/drm/card*/device/mem_info_gtt_used)")
            self._sleep(self.poll_interval_s)
            used = self.probe.gtt_used_bytes()
        with self._cv:
            del self.resident[key]
            self._decide(Decision.GRANTED, key, task_id, res.charged_bytes,
                         f"{reason}; release confirmed at {used / GIB:.2f} GiB used", action="unload")
            self._cv.notify_all()
        return f"release confirmed at {used / GIB:.2f} GiB used"

    # --- generation lock (rules 3, 4, 6) ---------------------------------------------------------------------------

    def _serving(self, key: str) -> bool:
        res = self.resident.get(key)
        return res is not None and res.serving

    def try_generation(self, key: str, *, task_id: str) -> LoadDecision:
        """Non-blocking: granted (and the lock is now held by task_id) or queued."""
        with self._cv:
            if self._gen_holder is None and not self._gen_queue and self._serving(key):
                self._gen_holder, self._gen_engine = task_id, key
                self.resident[key].last_used = self._clock()
                return self._decide(Decision.GRANTED, key, task_id, 0, "generation lock acquired", action="generation")
            if not self._serving(key):
                return self._decide(Decision.REFUSED, key, task_id, 0, "engine not resident; load it first",
                                    action="generation")
            return self._decide(Decision.QUEUED, key, task_id, 0,
                                f"generation in progress on {self._gen_engine} (task {self._gen_holder}); "
                                f"{len(self._gen_queue)} ahead in the FIFO (Section 4.2 rule 4)", action="generation")

    def release_generation(self, *, task_id: str) -> None:
        with self._cv:
            if self._gen_holder != task_id:
                raise ArbiterError(f"task {task_id} does not hold the generation lock (holder: {self._gen_holder})")
            self._gen_holder, self._gen_engine = None, None
            self._cv.notify_all()

    @contextmanager
    def acquire_generation(self, key: str, *, task_id: str, timeout_s: float | None = None) -> Iterator[None]:
        """Blocking FIFO acquisition of the single generation slot (rule 3/4); raises ArbiterError on timeout."""
        deadline = None if timeout_s is None else self._clock() + timeout_s
        with self._cv:
            if not self._serving(key):
                raise ArbiterError(f"{key} is not resident; request_load() first")
            self._gen_queue.append(task_id)
            try:
                while self._gen_holder is not None or self._gen_queue[0] != task_id:
                    remaining = None if deadline is None else deadline - self._clock()
                    if remaining is not None and remaining <= 0:
                        self._decide(Decision.QUEUED, key, task_id, 0, f"gave up after {timeout_s}s in the FIFO",
                                     action="generation")
                        raise ArbiterError(f"task {task_id}: no generation slot within {timeout_s}s")
                    self._cv.wait(timeout=None if remaining is None else min(remaining, 1.0))
                if not self._serving(key):
                    # Evicted while queued (rule 6 protects the running generation only): say so, do not run.
                    raise ArbiterError(f"{key} was unloaded while task {task_id} waited for the generation slot")
                self._gen_queue.popleft()
                self._gen_holder, self._gen_engine = task_id, key
                self.resident[key].last_used = self._clock()
            except BaseException:
                if task_id in self._gen_queue:
                    self._gen_queue.remove(task_id)
                self._cv.notify_all()
                raise
            self._decide(Decision.GRANTED, key, task_id, 0, "generation lock acquired", action="generation")
        try:
            yield
        finally:
            self.release_generation(task_id=task_id)

    @property
    def generating(self) -> tuple[str, str] | None:
        """(engine, task_id) of the running generation, or None."""
        with self._cv:
            if self._gen_holder is None or self._gen_engine is None:
                return None
            return self._gen_engine, self._gen_holder

    @property
    def generation_queue(self) -> tuple[str, ...]:
        with self._cv:
            return tuple(self._gen_queue)

    # --- Deep Think (rule 8, Section 9.1) --------------------------------------------------------------------------

    def tier_required_bytes(self, tier: str) -> tuple[int, tuple[str, ...]]:
        """The full footprint a tier pre-requests: its engines run one at a time, so the largest one must fit alone.

        Each engine is projected as its unit will run it (env file) when that is known, else from engines.json.
        """
        engines = self.deep_think_engines.get(tier)
        if engines is None:
            raise ArbiterError(f"unknown Deep Think tier {tier!r} (Section 9.1: {DEEP_THINK_TIERS})")
        if not engines:
            return 0, ()
        sizes = []
        for k in engines:
            unit = self.unit_footprint(k)
            sizes.append((unit[0] if unit else self.projected_footprint(k)).total_bytes)
        return max(sizes), engines

    def plan_deep_think(self, tier: str, *, task_id: str | None = None) -> DeepThinkPlan:
        if tier not in DEEP_THINK_TIERS:
            raise ArbiterError(f"unknown Deep Think tier {tier!r} (Section 9.1: {DEEP_THINK_TIERS})")
        with self._cv:
            budget = self.budget_bytes
            start = DEEP_THINK_TIERS.index(tier)
            for candidate in DEEP_THINK_TIERS[start:]:
                required, engines = self.tier_required_bytes(candidate)
                if required <= budget:
                    reason = ("fits" if candidate == tier
                              else f"downgraded from {tier}: its footprint exceeds the budget (Section 4.2 rule 8)")
                    plan = DeepThinkPlan(tier, candidate, engines, required, budget, reason, task_id)
                    self._decide(Decision.GRANTED, ",".join(engines) or None, task_id, required,
                                 f"deep-think {tier} -> {candidate}: {reason}", action="deep-think")
                    return plan
            required, engines = self.tier_required_bytes("quick")
            plan = DeepThinkPlan(tier, "quick", engines, required, budget, "quick uses the loaded engine", task_id)
            self._decide(Decision.GRANTED, None, task_id, required, f"deep-think {tier} -> quick", action="deep-think")
            return plan

    # --- status and logging (rule 9) -------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The ledger view; takes the state lock only, so it answers while a load or unload is in flight."""
        with self._cv:
            gen = self.generating
            return {
                "gtt_total_bytes": self.probe.gtt_total_bytes(),
                "gtt_used_bytes": self.probe.gtt_used_bytes(),
                "resident_set_bytes": self._resident_set_bytes,
                "host_resident_set_bytes": self._host_resident_bytes,
                "host_resident": {k: dict(v) for k, v in self._host_resident.items()},
                "max_resident": self.max_resident,
                "mem_total_bytes": self._mem_total_bytes,
                "pool_bytes": self.pool_bytes,
                "headroom_bytes": self.headroom_bytes,
                "host_used_bytes": self._host_used_bytes,
                "budget_bytes": self.budget_bytes if self._resident_set_bytes is not None else None,
                "charged_bytes": self.charged_bytes,
                "free_bytes": self.free_bytes if self._resident_set_bytes is not None else None,
                "resident": [
                    {"engine": r.key, "class": r.spec.arbiter_class, "projected_bytes": r.footprint.total_bytes,
                     "measured_bytes": r.measured_bytes, "ctx": r.footprint.ctx, "parallel": r.footprint.parallel,
                     "kv_class": r.footprint.kv_class, "loaded_at": r.loaded_at, "last_used": r.last_used,
                     "loading": r.loading, "unloading": r.unloading}
                    for r in self.resident.values()
                ],
                "measured": [
                    {"engine": k, "class": self.engines[k].arbiter_class if k in self.engines else None,
                     "measured_bytes": n,
                     # [ctx, parallel, kv] it applies to; None: registered while not resident (engines.json profile)
                     "profile": list(self.measured_profile[k]) if k in self.measured_profile else None}
                    for k, n in self.measured.items()
                ],
                "busy": self._busy,
                "generating": None if gen is None else {"engine": gen[0], "task_id": gen[1]},
                "generation_queue": list(self._gen_queue),
                "halted": self._halted,
            }

    def _decide(self, decision: Decision, key: str | None, task_id: str | None, projected: int, reason: str, *,
                action: str = "load", evicted: tuple[str, ...] = ()) -> LoadDecision:
        with self._cv:
            budget = self.budget_bytes if self._resident_set_bytes is not None else 0
            free = self.free_bytes if self._resident_set_bytes is not None else 0
            self._log(action, key, decision, task_id, projected, reason, budget=budget, free=free)
        return LoadDecision(decision, key or "", task_id, projected, budget, free, reason, evicted)

    def _log(self, action: str, key: str | None, decision: Decision, task_id: str | None, projected: int,
             reason: str, *, budget: int | None = None, free: int | None = None) -> None:
        with self._cv:
            budget = self.budget_bytes if budget is None and self._resident_set_bytes is not None else budget
            free = self.free_bytes if free is None and self._resident_set_bytes is not None else free
            resident = list(self.resident)
        level = logging.INFO if decision in (Decision.GRANTED, Decision.QUEUED) else logging.WARNING
        log.log(level, "arbiter action=%s decision=%s engine=%s task_id=%s projected_gib=%.2f budget_gib=%.2f "
                "free_gib=%.2f resident=%s reason=%s", action, decision.value, key, task_id, projected / GIB,
                (budget or 0) / GIB, (free or 0) / GIB, ",".join(resident) or "-", reason)
        if self.ledger is not None:
            try:
                self.ledger.insert_arbiter_decision(task_id=task_id, action=action, engine=key, decision=decision.value,
                                                    projected_bytes=projected, budget_bytes=budget, free_bytes=free,
                                                    resident=resident, reason=reason)
            except Exception:
                log.exception("arbiter: ledger write failed (decision above is still logged)")


def build_arbiter(engines: dict[str, EngineSpec], *, ledger: Ledger | None = None,
                  engines_env_dir: Path | None = None, phase4_engines: dict[str, EngineSpec] | None = None,
                  **kw: Any) -> Arbiter:
    """The production wiring: sysfs probe, the systemd controller (CONVENTIONS.md §8) and the unit env files
    (Settings.engines_env_dir = ATLAS_ENGINES_ENV_DIR, phase2/02-orchestrator.sh) the projection follows.

    `engines` is engines.json (the llama-server units; it is all the controller may ever start or stop). The Phase 4
    engines (config/phase4-engines.json in Settings.config_dir unless given) are merged into the Arbiter's map ONLY,
    so Phase 4 step 5 registers under known keys (rule 1) and no sudo line is ever built from a container key.
    """
    from atlas.config import ConfigError, Settings, load_phase4_engines
    from atlas.engines import SystemdEngineController

    settings = Settings.from_env()
    if engines_env_dir is None:
        engines_env_dir = settings.engines_env_dir
    if phase4_engines is None:
        phase4_engines = load_phase4_engines(settings.config_dir)  # ConfigError when absent: the tree is incomplete
    clash = sorted(set(phase4_engines) & set(engines))
    if clash:
        raise ConfigError(f"phase4-engines.json reuses engines.json keys {clash} (CONVENTIONS.md §8 names must agree)")
    if "headroom_bytes" not in kw:  # Section 4.1 host reserve (v0.3.5, S44); a bad override stops the Arbiter only
        if settings.arbiter_headroom_error:
            raise ConfigError(settings.arbiter_headroom_error)
        kw["headroom_bytes"] = settings.arbiter_headroom_bytes
    if "max_resident" not in kw:  # D-32: the LLM-seat residency limit; a bad override stops the Arbiter only
        if settings.arbiter_max_resident_error:
            raise ConfigError(settings.arbiter_max_resident_error)
        kw["max_resident"] = settings.arbiter_max_resident
    controller = SystemdEngineController(engines=engines)
    kw.setdefault("pid_of", controller.main_pid)  # D-14 (e): the CPU companions' units, measured by VmRSS
    return Arbiter({**engines, **phase4_engines}, controller, SysfsMemoryProbe(), ledger=ledger,
                   engines_env_dir=engines_env_dir, **kw)


__all__ = [
    "APEX_KEY", "DEEP_THINK_GENERATOR", "DEEP_THINK_THIRD_OPINION", "DEEP_THINK_TIERS",
    "DEFAULT_COMPANION_COMPUTE_BUFFER_GIB", "DEFAULT_COMPUTE_BUFFER_GIB", "GIB", "KV_BYTES_PER_TOKEN_F16",
    "KV_CLASS_FACTOR", "KV_STATE_BYTES_PER_SEQUENCE", "MAX_RESIDENT", "Arbiter", "ArbiterError", "Decision",
    "DeepThinkPlan", "Footprint", "LoadDecision", "MemoryProbe", "ReleaseTimeout", "Resident", "StubProbe",
    "SysfsMemoryProbe", "UnitProfile", "UnknownEngine", "build_arbiter", "compute_buffer_bytes", "kv_estimate_bytes",
    "kv_state_bytes", "process_rss_bytes", "read_unit_profile",
]
