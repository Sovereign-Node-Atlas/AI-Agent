"""The Engine Arbiter — Section 4.2 rules 1-9, the hard pre-flight of Section 4.3 and the OOM backstop.

Every load and unload of a weight-bearing process passes through one Arbiter instance (rule: "a single service inside
the orchestrator"). The unit tests (tests/test_arbiter.py, V14a) drive it with StubController and StubProbe; Phase 3
drives it against real engines (V10, V14b, V21).

Rule map (the numbers are Section 4.2's):
  1  Ledger: `Arbiter.resident` holds each resident engine with its projected and, once known, measured footprint.
  2  `request_load(engine, ctx, parallel)` projects weights + KV against the live budget and returns
     granted / queued / refused.
  3  At most MAX_RESIDENT = 2 resident engines; exactly one generation at a time (`acquire_generation`).
  4  A second generation request queues FIFO behind the running one (`try_generation` says "queued"; the blocking
     form waits its turn).
  5  Release confirmation: after a stop the GTT counter is polled until it drops within `release_tolerance_bytes`
     of the expected value; `ReleaseTimeout` is raised loudly after `release_timeout_s`.
  6  Never preempt mid-generation: an eviction whose victim is generating makes the request "queued".
  7  Apex exclusivity: loading deepseek-v4-flash unloads everything else first; nothing else loads while it is resident.
  8  `plan_deep_think(tier)` pre-requests the full footprint and downgrades deep -> standard -> quick when it does
     not fit.
  9  Every decision is logged (structured line) and written to ledger.arbiter_decisions with the task id.

Budget (Section 4.1): budget = GTT total - resident set, where the resident set is what the counter shows before any
engine is loaded (Ubuntu, XFCE, Docker, Open WebUI, the three resident small models, Kokoro/Whisper/PyAnnote, ...),
measured at startup by `measure_resident_set()` (V3 makes this ~170 GB).
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

from atlas.config import EngineSpec
from atlas.engines import EngineControlError, EngineController
from atlas.ledger import Ledger

log = logging.getLogger("atlas.arbiter")

GIB = 1024**3
MAX_RESIDENT = 2  # Section 4.2 rule 3
APEX_KEY = "deepseek-v4-flash"  # CONVENTIONS.md §8 arbiter class `apex`

# --- KV-cache size model (Section 4.3; bytes of K+V per token of the TOTAL pool, at f16) ------------------------------
#
# K+V per token at f16 = 2 * n_attention_layers * n_kv_heads * head_dim * 2 bytes. The projection is an OOM backstop
# (4.3 "Hard pre-flight"), replaced by the measured footprint once an engine has been loaded (rule 1), so estimates err
# on the high side. Sources per engine:
#   gpt-oss-120b(-abliterated): 36 layers (VERIFIED, gguf-models.md §1.2 "case 36: LLM_TYPE_120B"), head dim 64
#       (VERIFIED, §2 "head dim 64 % 64 == 0"); 8 KV heads UNVERIFIED (openai/gpt-oss config.json, from memory).
#       Both iswa sub-caches
#       are charged the full pool, which over-states the SWA half (its cells are the window, not n_ctx).
#   nemotron-3-super, qwen3.5-122b: hybrid models whose attention layers are a minority; the per-token figure is backed
#       out of Section 4.3's bounds ("Nemotron < 10 GB, Qwen3.5 < 15 GB at 32k x 8 slots" at q8_0). UNVERIFIED.
#   deepseek-v4-flash: ~10 GB of f16 cache at the 32768 cap (engines.json known_issue; Section 4.3 "10-15 GB margin").
#       Four sub-caches; the compressor states are f32 and are inside the figure. UNVERIFIED.
#   qwen2.5-vl-72b, meditron-70b: dense GQA, 80 layers x 8 KV heads x 128 head dim (Qwen2.5-72B / Llama-2-70B configs,
#       UNVERIFIED from memory) = 327,680 B/token at f16.
#   router-qwen3.5-4b: resident, never budgeted (Section 4.1); a nominal figure for completeness.
KV_BYTES_PER_TOKEN_F16: dict[str, int] = {
    "gpt-oss-120b": 73_728,
    "gpt-oss-120b-abliterated": 73_728,
    "nemotron-3-super": 77_102,  # UNVERIFIED: 10 GiB / 262144 tokens / 0.53125 (q8_0 factor), Section 4.3 bound
    "qwen3.5-122b": 115_653,  # UNVERIFIED: 15 GiB / 262144 tokens / 0.53125, Section 4.3 bound
    "deepseek-v4-flash": 327_680,  # UNVERIFIED: ~10 GiB at 32768 tokens f16 (engines.json known_issue)
    "qwen2.5-vl-72b": 327_680,
    "meditron-70b": 327_680,
    "router-qwen3.5-4b": 24_576,
    "embed-bge-m3": 0,
    "rerank-bge-v2-m3": 0,
}
# ggml block layouts (VERIFIED, ggml-common.h): q8_0 = 34 bytes per 32 values (8.5 bit), q4_0 = 18 bytes per 32
# values (4.5 bit).
KV_CLASS_FACTOR: dict[str, float] = {
    "f32": 2.0, "f16": 1.0, "bf16": 1.0, "q8_0": 8.5 / 16, "q4_0": 4.5 / 16, "none": 0.0,
}


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


class SysfsMemoryProbe:
    """/sys/class/drm/card*/device/mem_info_gtt_{used,total} in bytes (research §5.2, VERIFIED; lib/common.sh twin)."""

    def __init__(self, drm_root: Path = Path("/sys/class/drm")) -> None:
        self.drm_root = drm_root
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


class StubProbe:
    """Test double with settable counters (bytes)."""

    def __init__(self, total_bytes: int, used_bytes: int = 0) -> None:
        self.total_bytes = total_bytes
        self.used_bytes = used_bytes

    def gtt_used_bytes(self) -> int:
        return self.used_bytes

    def gtt_total_bytes(self) -> int:
        return self.total_bytes


@dataclass(frozen=True)
class Footprint:
    weights_bytes: int
    kv_bytes: int
    ctx: int
    parallel: int
    kv_class: str
    measured: bool = False

    @property
    def total_bytes(self) -> int:
        return self.weights_bytes + self.kv_bytes

    @property
    def total_gib(self) -> float:
        return self.total_bytes / GIB


@dataclass
class Resident:
    key: str
    spec: EngineSpec
    footprint: Footprint
    loaded_at: float
    last_used: float
    observed_bytes: int = 0  # GTT delta seen when the engine loaded; the release target of rule 5
    measured_bytes: int | None = None

    @property
    def charged_bytes(self) -> int:
        """What the ledger charges against the budget: the measurement when there is one, else the projection."""
        return self.measured_bytes if self.measured_bytes is not None else self.footprint.total_bytes


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


DEEP_THINK_TIERS: tuple[str, ...] = ("deep", "standard", "quick")  # Section 9.1, highest first


def kv_estimate_bytes(spec: EngineSpec, ctx: int, parallel: int, kv_class: str | None = None) -> int:
    """K+V bytes for a TOTAL pool of `ctx` tokens (research conflict 9: n_ctx is split across `parallel` slots).

    `parallel` does not multiply the size (the pool is already the total); it is validated so a caller who passes a
    per-slot figure by mistake is told so instead of under-estimating.
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
    return int(per_token * ctx * KV_CLASS_FACTOR[kv_class])


class Arbiter:
    def __init__(self, engines: dict[str, EngineSpec], controller: EngineController, probe: MemoryProbe, *,
                 ledger: Ledger | None = None, release_timeout_s: float = 60.0,
                 release_tolerance_bytes: int = GIB, poll_interval_s: float = 1.0,
                 resident_set_bytes: int | None = None, headroom_bytes: int = 0,
                 deep_think_engines: dict[str, Sequence[str]] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.engines = engines
        self.controller = controller
        self.probe = probe
        self.ledger = ledger
        self.release_timeout_s = release_timeout_s  # research §6.3: time-box 60 s
        self.release_tolerance_bytes = release_tolerance_bytes  # research §6.3: within ~1 GiB of the baseline
        self.poll_interval_s = poll_interval_s
        self.headroom_bytes = headroom_bytes
        self._clock = clock
        self._sleep = sleep
        self._resident_set_bytes = resident_set_bytes
        self.resident: dict[str, Resident] = {}  # rule 1, insertion order = load order
        self.measured: dict[str, int] = {}  # measured total footprints by engine key (Phase 3 / Phase 4 step 5)
        self._cv = threading.Condition(threading.RLock())
        self._gen_holder: str | None = None  # task id
        self._gen_engine: str | None = None
        self._gen_queue: deque[str] = deque()  # FIFO tickets (rule 4)
        self._halted: str | None = None  # set by a ReleaseTimeout; every later load is refused with this reason
        self.deep_think_engines: dict[str, tuple[str, ...]] = {
            "deep": (APEX_KEY,),  # 9.1: the Apex engine delivers the deep synthesis
            "standard": ("gpt-oss-120b", "nemotron-3-super"),  # 9.1: Ren generates, Arthur scores, swapped in turn
            "quick": (),  # 9.1: the currently loaded engine, no swap
        }
        if deep_think_engines:
            self.deep_think_engines.update({k: tuple(v) for k, v in deep_think_engines.items()})

    # --- budget (Section 4.1) --------------------------------------------------------------------------------------

    def measure_resident_set(self) -> int:
        """Record what the GTT counter shows before any engine loads: the always-resident set (Section 4.1 table)."""
        with self._cv:
            if self.resident:
                raise ArbiterError("measure_resident_set() must run before any engine is resident")
            self._resident_set_bytes = self.probe.gtt_used_bytes()
            log.info("arbiter resident_set_gib=%.2f gtt_total_gib=%.2f budget_gib=%.2f",
                     self._resident_set_bytes / GIB, self.probe.gtt_total_bytes() / GIB, self.budget_bytes / GIB)
            return self._resident_set_bytes

    @property
    def resident_set_bytes(self) -> int:
        if self._resident_set_bytes is None:
            raise ArbiterError("resident set not measured: call measure_resident_set() at startup (Section 4.1)")
        return self._resident_set_bytes

    @property
    def budget_bytes(self) -> int:
        return max(0, self.probe.gtt_total_bytes() - self.resident_set_bytes - self.headroom_bytes)

    @property
    def charged_bytes(self) -> int:
        return sum(r.charged_bytes for r in self.resident.values())

    @property
    def free_bytes(self) -> int:
        return self.budget_bytes - self.charged_bytes

    # --- footprints (rule 2) ---------------------------------------------------------------------------------------

    def spec(self, key: str) -> EngineSpec:
        try:
            return self.engines[key]
        except KeyError as exc:
            raise UnknownEngine(f"unknown engine {key!r} (CONVENTIONS.md §8 keys)") from exc

    def projected_footprint(self, key: str, ctx: int | None = None, parallel: int | None = None,
                            kv_class: str | None = None, *, coresident: bool = False) -> Footprint:
        spec = self.spec(key)
        if ctx is None:
            ctx = spec.ctx_size_coresident if coresident and spec.ctx_size_coresident else spec.ctx_size
        if parallel is None:
            parallel = spec.parallel_coresident if coresident and spec.parallel_coresident else spec.parallel
        if spec.is_resident:
            return Footprint(0, 0, ctx, parallel, spec.kv_class)
        if key in self.measured and ctx == spec.ctx_size and parallel == spec.parallel and kv_class is None:
            return Footprint(self.measured[key], 0, ctx, parallel, spec.kv_class, measured=True)
        kv = kv_estimate_bytes(spec, ctx, parallel, kv_class)
        return Footprint(spec.footprint_bytes, kv, ctx, parallel, kv_class or spec.kv_class)

    def register_measured(self, key: str, total_bytes: int, *, task_id: str | None = None) -> None:
        """Phase 3 step 2 / Phase 4 step 5: replace the estimate with the measured footprint (rule 1)."""
        with self._cv:
            if key not in self.engines:
                raise UnknownEngine(f"cannot register {key!r}: not in engines.json")
            self.measured[key] = int(total_bytes)
            res = self.resident.get(key)
            if res is not None:
                res.measured_bytes = int(total_bytes)
            self._log("measure", key, Decision.GRANTED, task_id, int(total_bytes),
                      reason=f"measured footprint {total_bytes / GIB:.2f} GiB recorded")

    def confirm_loaded(self, key: str, *, task_id: str | None = None) -> int:
        """After a real load: measure the counter delta and record it as the engine's footprint."""
        with self._cv:
            res = self.resident.get(key)
            if res is None:
                raise ArbiterError(f"{key} is not resident; nothing to confirm")
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
                     task_id: str | None = None, wait_s: float | None = None, coresident: bool | None = None,
                     kv_class: str | None = None) -> LoadDecision:
        """Grant, queue or refuse a load. Granted means the engine is resident and serving when this returns.

        wait_s=None returns "queued" immediately when a running generation blocks an eviction (rules 4, 6); a number
        waits up to that long for the generation to finish and then retries.
        """
        deadline = None if wait_s is None else self._clock() + wait_s
        with self._cv:
            while True:
                decision = self._try_load(key, ctx, parallel, task_id, coresident, kv_class)
                if decision.decision is not Decision.QUEUED or deadline is None:
                    return decision
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return decision
                self._cv.wait(timeout=min(remaining, 1.0))

    def _try_load(self, key: str, ctx: int | None, parallel: int | None, task_id: str | None,
                  coresident: bool | None, kv_class: str | None) -> LoadDecision:
        spec = self.spec(key)
        if spec.is_resident:
            # Resident small models are started by systemd at boot and never counted (Section 4.1, 5.3).
            if not self.controller.is_active(key):
                self.controller.start(key)
            return self._decide(Decision.GRANTED, key, task_id, 0, "resident small model; not budgeted (Section 4.1)")
        if self._halted:
            return self._decide(Decision.REFUSED, key, task_id, 0, f"arbiter halted: {self._halted}")
        if coresident is None:
            coresident = bool(self.resident) and not spec.is_apex and key not in self.resident
        fp = self.projected_footprint(key, ctx, parallel, kv_class, coresident=coresident)
        budget = self.budget_bytes
        if key in self.resident:
            self.resident[key].last_used = self._clock()
            return self._decide(Decision.GRANTED, key, task_id, fp.total_bytes, "already resident (swap is free)")
        if fp.total_bytes > budget:
            return self._decide(Decision.REFUSED, key, task_id, fp.total_bytes,
                                f"projected {fp.total_gib:.1f} GiB exceeds the engine budget {budget / GIB:.1f} GiB "
                                "(Section 4.2 rule 2)")
        apex_resident = [k for k, r in self.resident.items() if r.spec.is_apex]
        if apex_resident and not spec.is_apex:
            return self._decide(Decision.REFUSED, key, task_id, fp.total_bytes,
                                f"Apex engine {apex_resident[0]} is resident and exclusive (Section 4.2 rule 7)")
        victims = self._plan_evictions(key, spec, fp)
        if victims is None:
            return self._decide(Decision.QUEUED, key, task_id, fp.total_bytes,
                                f"generation in progress on {self._gen_engine} (task {self._gen_holder}); "
                                "never preempted (Section 4.2 rule 6)")
        try:
            for victim in victims:
                self._unload(victim, task_id, reason=f"evicted for {key}")
            used_before = self.probe.gtt_used_bytes()
            self.controller.start(key)
        except ReleaseTimeout:
            raise
        except EngineControlError as exc:
            self._decide(Decision.ERROR, key, task_id, fp.total_bytes, f"start failed: {exc}")
            raise
        now = self._clock()
        # What the counter actually grew by is what rule 5 must see come back after the stop.
        observed = max(0, self.probe.gtt_used_bytes() - used_before)
        self.resident[key] = Resident(key=key, spec=spec, footprint=fp, loaded_at=now, last_used=now,
                                      observed_bytes=observed,
                                      measured_bytes=self.measured.get(key) if fp.measured else None)
        self._cv.notify_all()
        return self._decide(Decision.GRANTED, key, task_id, fp.total_bytes,
                            f"loaded ({'coresident ' if coresident else ''}ctx {fp.ctx} x {fp.parallel} slots, "
                            f"kv {fp.kv_class})", evicted=tuple(victims))

    def _plan_evictions(self, key: str, spec: EngineSpec, fp: Footprint) -> list[str] | None:
        """Which residents must go so `key` fits; None when a needed victim is generating (rule 6)."""
        if spec.is_apex:
            victims = list(self.resident)  # rule 7: everything else leaves first
        else:
            victims = []
            free = self.free_bytes
            count = len(self.resident)
            # LRU first (Section 6.3 groups work by engine; the least recently used is the cheapest to lose).
            for k, _r in sorted(self.resident.items(), key=lambda kv: kv[1].last_used):
                if count < MAX_RESIDENT and free >= fp.total_bytes:
                    break
                victims.append(k)
                free += self.resident[k].charged_bytes
                count -= 1
        if any(self._is_generating(v) for v in victims):
            return None
        return victims

    def _is_generating(self, key: str) -> bool:
        return self._gen_holder is not None and self._gen_engine == key

    def request_unload(self, key: str, *, task_id: str | None = None, wait_s: float | None = None) -> LoadDecision:
        deadline = None if wait_s is None else self._clock() + wait_s
        with self._cv:
            while True:
                if key not in self.resident:
                    return self._decide(Decision.GRANTED, key, task_id, 0, "not resident; nothing to unload")
                if not self._is_generating(key):
                    self._unload(key, task_id, reason="unload requested")
                    self._cv.notify_all()
                    return self._decide(Decision.GRANTED, key, task_id, 0, "unloaded; memory release confirmed")
                if deadline is None or deadline - self._clock() <= 0:
                    return self._decide(Decision.QUEUED, key, task_id, 0,
                                        f"generating (task {self._gen_holder}); never preempted (rule 6)")
                self._cv.wait(timeout=min(deadline - self._clock(), 1.0))

    def _unload(self, key: str, task_id: str | None, *, reason: str) -> None:
        res = self.resident[key]
        before = self.probe.gtt_used_bytes()
        self.controller.stop(key)
        del self.resident[key]
        # Rule 5: trust the counter, not the process exit. The engine's share is the delta observed at its load (or the
        # measurement when one replaced it); the counter must fall back by that much, within the tolerance.
        share = res.measured_bytes if res.measured_bytes is not None else res.observed_bytes
        target = max(self.resident_set_bytes, before - share) + self.release_tolerance_bytes
        deadline = self._clock() + self.release_timeout_s
        used = self.probe.gtt_used_bytes()
        while used > target:
            if self._clock() >= deadline:
                self._halted = (f"GTT not released after stopping {key}: {used / GIB:.2f} GiB still used, "
                                f"expected <= {target / GIB:.2f} GiB after {self.release_timeout_s:.0f}s")
                self._decide(Decision.ERROR, key, task_id, res.charged_bytes, self._halted)
                raise ReleaseTimeout(self._halted + " (Section 4.2 rule 5; check journalctl -u llama-server@"
                                     f"{key} and /sys/class/drm/card*/device/mem_info_gtt_used)")
            self._sleep(self.poll_interval_s)
            used = self.probe.gtt_used_bytes()
        self._decide(Decision.GRANTED, key, task_id, res.charged_bytes,
                     f"{reason}; release confirmed at {used / GIB:.2f} GiB used", action="unload")

    # --- generation lock (rules 3, 4, 6) ---------------------------------------------------------------------------

    def try_generation(self, key: str, *, task_id: str) -> LoadDecision:
        """Non-blocking: granted (and the lock is now held by task_id) or queued."""
        with self._cv:
            if self._gen_holder is None and not self._gen_queue and key in self.resident:
                self._gen_holder, self._gen_engine = task_id, key
                self.resident[key].last_used = self._clock()
                return self._decide(Decision.GRANTED, key, task_id, 0, "generation lock acquired", action="generation")
            if key not in self.resident:
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
            if key not in self.resident:
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
                self._gen_queue.popleft()
                self._gen_holder, self._gen_engine = task_id, key
                self.resident[key].last_used = self._clock()
            except BaseException:
                if task_id in self._gen_queue:
                    self._gen_queue.remove(task_id)
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
        """The full footprint a tier pre-requests: its engines run one at a time, so the largest one must fit alone."""
        engines = self.deep_think_engines.get(tier)
        if engines is None:
            raise ArbiterError(f"unknown Deep Think tier {tier!r} (Section 9.1: {DEEP_THINK_TIERS})")
        if not engines:
            return 0, ()
        return max(self.projected_footprint(k).total_bytes for k in engines), engines

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
        with self._cv:
            gen = self.generating
            return {
                "gtt_total_bytes": self.probe.gtt_total_bytes(),
                "gtt_used_bytes": self.probe.gtt_used_bytes(),
                "resident_set_bytes": self._resident_set_bytes,
                "budget_bytes": self.budget_bytes if self._resident_set_bytes is not None else None,
                "charged_bytes": self.charged_bytes,
                "free_bytes": self.free_bytes if self._resident_set_bytes is not None else None,
                "resident": [
                    {"engine": r.key, "class": r.spec.arbiter_class, "projected_bytes": r.footprint.total_bytes,
                     "measured_bytes": r.measured_bytes, "ctx": r.footprint.ctx, "parallel": r.footprint.parallel,
                     "kv_class": r.footprint.kv_class, "loaded_at": r.loaded_at, "last_used": r.last_used}
                    for r in self.resident.values()
                ],
                "generating": None if gen is None else {"engine": gen[0], "task_id": gen[1]},
                "generation_queue": list(self._gen_queue),
                "halted": self._halted,
            }

    def _decide(self, decision: Decision, key: str | None, task_id: str | None, projected: int, reason: str, *,
                action: str = "load", evicted: tuple[str, ...] = ()) -> LoadDecision:
        budget = self.budget_bytes if self._resident_set_bytes is not None else 0
        free = self.free_bytes if self._resident_set_bytes is not None else 0
        self._log(action, key, decision, task_id, projected, reason, budget=budget, free=free)
        return LoadDecision(decision, key or "", task_id, projected, budget, free, reason, evicted)

    def _log(self, action: str, key: str | None, decision: Decision, task_id: str | None, projected: int,
             reason: str, *, budget: int | None = None, free: int | None = None) -> None:
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


def build_arbiter(engines: dict[str, EngineSpec], *, ledger: Ledger | None = None, **kw: Any) -> Arbiter:
    """The production wiring: sysfs probe and the systemd controller (CONVENTIONS.md §8)."""
    from atlas.engines import SystemdEngineController

    return Arbiter(engines, SystemdEngineController(engines=engines), SysfsMemoryProbe(), ledger=ledger, **kw)


__all__ = [
    "APEX_KEY", "GIB", "KV_BYTES_PER_TOKEN_F16", "KV_CLASS_FACTOR", "MAX_RESIDENT", "Arbiter", "ArbiterError",
    "Decision", "DeepThinkPlan", "Footprint", "LoadDecision", "MemoryProbe", "ReleaseTimeout", "Resident",
    "StubProbe", "SysfsMemoryProbe", "UnknownEngine", "build_arbiter", "kv_estimate_bytes",
]
