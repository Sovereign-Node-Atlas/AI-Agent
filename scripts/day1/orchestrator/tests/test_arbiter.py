"""V14a: the Engine Arbiter against stub footprints (Section 4.2 rules 1-9, Section 21 V14, Phase 2 gate).

Numbers: GTT total 192 GiB (amdgpu.gttsize=196608, Appendix B), resident set 22 GiB -> budget 170 GiB (Section 4.1).
Footprints are engines.json's Section 5.1 figures (decimal GB), KV from the Section 4.3 model in atlas.arbiter.
"""

from __future__ import annotations

import threading

import pytest

from atlas.arbiter import (
    APEX_KEY,
    GIB,
    MAX_RESIDENT,
    Arbiter,
    ArbiterError,
    Decision,
    ReleaseTimeout,
    StubProbe,
    kv_estimate_bytes,
)
from atlas.config import EngineSpec
from atlas.engines import StubController
from atlas.ledger import Ledger

GTT_TOTAL = 196608 * 1024 * 1024  # 206158430208 bytes, the V3 figure
RESIDENT_SET = 22 * GIB


class FakeClock:
    """Monotonic clock the tests drive; sleep() advances it so release polling terminates instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 0.001
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make(engines: dict[str, EngineSpec], *, budget_gib: float = 170.0, leak: bool = False,
         ledger: Ledger | None = None,
         release_timeout_s: float = 60.0) -> tuple[Arbiter, StubController, StubProbe, FakeClock]:
    probe = StubProbe(total_bytes=int(RESIDENT_SET + budget_gib * GIB), used_bytes=RESIDENT_SET)
    controller = StubController(engines=engines, probe=probe, leak_on_stop=leak)
    clock = FakeClock()
    arb = Arbiter(engines, controller, probe, ledger=ledger, release_timeout_s=release_timeout_s,
                  poll_interval_s=1.0, clock=clock, sleep=clock.sleep)
    arb.measure_resident_set()
    return arb, controller, probe, clock


# --- budget and the KV model -------------------------------------------------------------------------------------


def test_budget_is_gtt_total_minus_resident_set(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    assert arb.resident_set_bytes == RESIDENT_SET
    assert arb.budget_bytes == 170 * GIB
    assert arb.free_bytes == arb.budget_bytes


def test_kv_estimate_uses_the_total_pool_not_per_slot(engines: dict[str, EngineSpec]) -> None:
    spec = engines["gpt-oss-120b"]
    # Research conflict 9: ctx is the TOTAL pool; parallel splits it and must not multiply the estimate.
    assert kv_estimate_bytes(spec, 262144, 8) == kv_estimate_bytes(spec, 262144, 1)
    assert kv_estimate_bytes(spec, 262144, 8) == 8 * kv_estimate_bytes(spec, 32768, 1)
    # q4_0 (Ren's engines) is smaller than q8_0 which is smaller than f16 (Section 4.3).
    assert kv_estimate_bytes(spec, 262144, 8, "q4_0") < kv_estimate_bytes(spec, 262144, 8, "q8_0") < kv_estimate_bytes(
        spec, 262144, 8, "f16")
    with pytest.raises(ArbiterError, match="TOTAL pool"):
        kv_estimate_bytes(spec, 1024, 8)  # a per-slot figure passed by mistake
    with pytest.raises(ArbiterError, match="parallel"):
        kv_estimate_bytes(spec, 262144, 0)  # never auto (gguf-models.md §1.3)


def test_everyday_pairing_fits_and_ren_plus_arthur_does_not(engines: dict[str, EngineSpec]) -> None:
    """Section 4.1: gpt-oss + Qwen2.5-VL (2 slots) fits ~170 GB; Nemotron + gpt-oss (186 GB) does not."""
    arb, _, _, _ = make(engines)
    ren = arb.projected_footprint("gpt-oss-120b").total_bytes
    vision = arb.projected_footprint("qwen2.5-vl-72b", coresident=True).total_bytes
    arthur = arb.projected_footprint("nemotron-3-super").total_bytes
    assert ren + vision <= arb.budget_bytes
    assert ren + arthur > arb.budget_bytes
    assert arb.projected_footprint("qwen2.5-vl-72b", coresident=True).parallel == 2
    assert arb.projected_footprint("qwen2.5-vl-72b", coresident=True).ctx == 65536


# --- rule 2: refusal of an over-budget load ----------------------------------------------------------------------


def test_refuses_over_budget_load_and_logs_it(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, _, _ = make(engines, budget_gib=60.0, ledger=ledger)
    d = arb.request_load("nemotron-3-super", task_id="t-over")
    assert d.decision is Decision.REFUSED
    assert "exceeds the engine budget" in d.reason
    assert d.projected_bytes > d.budget_bytes
    assert controller.calls == []  # nothing was started (4.3: the Arbiter is the OOM backstop)
    assert arb.resident == {}
    rows = ledger.list_arbiter_decisions(task_id="t-over")
    assert len(rows) == 1 and rows[0]["decision"] == "refused" and rows[0]["engine"] == "nemotron-3-super"  # rule 9


def test_grant_records_task_id_in_ledger(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, probe, _ = make(engines, ledger=ledger)
    d = arb.request_load("gpt-oss-120b", task_id="t-load")
    assert d.granted and controller.calls == [("start", "gpt-oss-120b")]
    assert list(arb.resident) == ["gpt-oss-120b"]
    assert probe.used_bytes == RESIDENT_SET + engines["gpt-oss-120b"].footprint_bytes
    rows = ledger.list_arbiter_decisions(task_id="t-load")
    assert [r["decision"] for r in rows] == ["granted"]
    assert rows[0]["budget_bytes"] == 170 * GIB
    # A second request for a resident engine is a free swap (rule 3: residency costs memory only).
    again = arb.request_load("gpt-oss-120b", task_id="t-load-2")
    assert again.granted and "already resident" in again.reason and len(controller.calls) == 1


# --- rules 3 and 4: two resident, one generating, second generation queues ---------------------------------------


def test_second_generation_queues_fifo(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.request_load("qwen2.5-vl-72b", task_id="t2").granted
    assert len(arb.resident) == MAX_RESIDENT
    first = arb.try_generation("gpt-oss-120b", task_id="g1")
    assert first.granted and arb.generating == ("gpt-oss-120b", "g1")
    second = arb.try_generation("qwen2.5-vl-72b", task_id="g2")
    assert second.decision is Decision.QUEUED  # rule 4: waits seconds, never runs concurrently (V21 shape)
    assert "generation in progress on gpt-oss-120b" in second.reason
    arb.release_generation(task_id="g1")
    assert arb.try_generation("qwen2.5-vl-72b", task_id="g2").granted
    arb.release_generation(task_id="g2")
    assert arb.generating is None


def test_blocking_generation_waits_its_turn_in_order(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with arb.acquire_generation("gpt-oss-120b", task_id="g-hold"):
            order.append("g-hold")
            holding.set()
            release.wait(5)

    def waiter(name: str, started: threading.Event) -> None:
        started.set()
        with arb.acquire_generation("gpt-oss-120b", task_id=name):
            order.append(name)

    t0 = threading.Thread(target=holder)
    t0.start()
    assert holding.wait(5)
    assert arb.try_generation("gpt-oss-120b", task_id="probe").decision is Decision.QUEUED
    s1, s2 = threading.Event(), threading.Event()
    t1 = threading.Thread(target=waiter, args=("g-a", s1))
    t1.start()
    assert s1.wait(5)
    while "g-a" not in arb.generation_queue:
        pass
    t2 = threading.Thread(target=waiter, args=("g-b", s2))
    t2.start()
    assert s2.wait(5)
    while "g-b" not in arb.generation_queue:
        pass
    assert arb.generation_queue == ("g-a", "g-b")
    release.set()
    for t in (t0, t1, t2):
        t.join(5)
    assert order == ["g-hold", "g-a", "g-b"]  # FIFO (rule 4)
    assert arb.generating is None


# --- rule 3: at most two resident; rule 6: never preempt mid-generation ------------------------------------------


def test_two_resident_limit_evicts_least_recently_used(engines: dict[str, EngineSpec]) -> None:
    arb, controller, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.request_load("qwen2.5-vl-72b", task_id="t2").granted
    assert len(arb.resident) == 2
    d = arb.request_load("gpt-oss-120b-abliterated", task_id="t3")
    assert d.granted
    assert d.evicted == ("gpt-oss-120b",)  # LRU: loaded and last used first
    assert list(arb.resident) == ["qwen2.5-vl-72b", "gpt-oss-120b-abliterated"]
    assert len(arb.resident) <= MAX_RESIDENT
    assert ("stop", "gpt-oss-120b") in controller.calls
    assert arb.charged_bytes <= arb.budget_bytes


def test_never_preempts_mid_generation(engines: dict[str, EngineSpec]) -> None:
    arb, controller, _, _ = make(engines, budget_gib=100.0)  # room for one Ren-class engine only
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.try_generation("gpt-oss-120b", task_id="g1").granted
    d = arb.request_load("gpt-oss-120b-abliterated", task_id="t2")
    assert d.decision is Decision.QUEUED  # the only victim is generating (rule 6)
    assert "never preempted" in d.reason
    assert ("stop", "gpt-oss-120b") not in controller.calls
    # The unload request queues too.
    assert arb.request_unload("gpt-oss-120b", task_id="t2").decision is Decision.QUEUED
    arb.release_generation(task_id="g1")
    d2 = arb.request_load("gpt-oss-120b-abliterated", task_id="t2")
    assert d2.granted and d2.evicted == ("gpt-oss-120b",)
    assert list(arb.resident) == ["gpt-oss-120b-abliterated"]


def test_queued_load_with_wait_proceeds_when_generation_ends(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines, budget_gib=100.0)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.try_generation("gpt-oss-120b", task_id="g1").granted
    result: list[Decision] = []

    def loader() -> None:
        result.append(arb.request_load("gpt-oss-120b-abliterated", task_id="t2", wait_s=10.0).decision)

    t = threading.Thread(target=loader)
    t.start()
    arb.release_generation(task_id="g1")
    t.join(5)
    assert result == [Decision.GRANTED]


# --- rule 7: apex exclusivity ------------------------------------------------------------------------------------


def test_apex_unloads_everything_and_refuses_others(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, probe, _ = make(engines, ledger=ledger)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.request_load("qwen2.5-vl-72b", task_id="t2").granted
    d = arb.request_load(APEX_KEY, task_id="t-apex")
    assert d.granted
    assert set(d.evicted) == {"gpt-oss-120b", "qwen2.5-vl-72b"}
    assert list(arb.resident) == [APEX_KEY]
    assert engines[APEX_KEY].is_apex and engines[APEX_KEY].exclusive
    assert probe.used_bytes == RESIDENT_SET + engines[APEX_KEY].footprint_bytes  # memory of both victims came back
    refused = arb.request_load("gpt-oss-120b", task_id="t3")
    assert refused.decision is Decision.REFUSED and "exclusive" in refused.reason
    assert list(arb.resident) == [APEX_KEY]
    assert arb.request_unload(APEX_KEY, task_id="t4").granted
    assert arb.request_load("gpt-oss-120b", task_id="t5").granted
    decisions = [(r["engine"], r["decision"], r["action"]) for r in ledger.list_arbiter_decisions(limit=50)]
    assert ("gpt-oss-120b", "refused", "load") in decisions
    assert (APEX_KEY, "granted", "load") in decisions


def test_apex_request_queues_while_a_victim_generates(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.try_generation("gpt-oss-120b", task_id="g1").granted
    assert arb.request_load(APEX_KEY, task_id="t-apex").decision is Decision.QUEUED
    arb.release_generation(task_id="g1")
    assert arb.request_load(APEX_KEY, task_id="t-apex").granted


# --- rule 5: release confirmation with a timeout that fails loudly ------------------------------------------------


def test_release_confirmation_timeout_fails_loudly(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, _, clock = make(engines, leak=True, ledger=ledger, release_timeout_s=60.0)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    before = clock.now
    with pytest.raises(ReleaseTimeout, match="GTT not released after stopping gpt-oss-120b"):
        arb.request_load("nemotron-3-super", task_id="t2")  # needs the eviction (186 GB pair does not fit)
    assert clock.now - before >= 60.0  # polled for the full time-box (research §6.3), not a single read
    assert ("stop", "gpt-oss-120b") in controller.calls and ("start", "nemotron-3-super") not in controller.calls
    assert arb.resident == {}  # the process is gone; the memory is not, and the Arbiter says so
    # Nothing else loads until a human looks (rule §7.4 fail loudly, never silently).
    later = arb.request_load("gpt-oss-120b", task_id="t3")
    assert later.decision is Decision.REFUSED and "halted" in later.reason
    errors = [r for r in ledger.list_arbiter_decisions(limit=50) if r["decision"] == "error"]
    assert errors and errors[0]["engine"] == "gpt-oss-120b" and errors[0]["task_id"] == "t2"


def test_release_confirmation_polls_until_the_counter_drops(engines: dict[str, EngineSpec]) -> None:
    arb, _, probe, clock = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    high = probe.used_bytes
    # Simulate a slow release: the stub stop already lowered the counter; put it back and let the poll see it drop.
    ticks = {"n": 0}
    real_sleep = clock.sleep

    def slow_sleep(seconds: float) -> None:
        real_sleep(seconds)
        ticks["n"] += 1
        if ticks["n"] >= 3:
            probe.used_bytes = RESIDENT_SET

    arb._sleep = slow_sleep
    original_stop = arb.controller.stop

    def stop_but_keep_memory(key: str) -> None:
        original_stop(key)
        probe.used_bytes = high  # process exit does not mean the GTT is back (research §6.3)

    arb.controller.stop = stop_but_keep_memory  # type: ignore[method-assign]
    assert arb.request_unload("gpt-oss-120b", task_id="t2").granted
    assert ticks["n"] == 3 and probe.used_bytes == RESIDENT_SET


# --- rule 8: Deep Think footprint pre-request and tier downgrade --------------------------------------------------


def test_deep_think_tier_downgrades_when_it_does_not_fit(engines: dict[str, EngineSpec]) -> None:
    full, _, _, _ = make(engines, budget_gib=170.0)
    plan = full.plan_deep_think("deep", task_id="dt1")
    assert plan.granted == "deep" and not plan.downgraded and plan.engines == (APEX_KEY,)
    assert plan.required_bytes == full.projected_footprint(APEX_KEY).total_bytes

    mid, _, _, _ = make(engines, budget_gib=140.0)  # the Apex engine (155 GB + KV) no longer fits
    plan = mid.plan_deep_think("deep", task_id="dt2")
    assert plan.granted == "standard" and plan.downgraded
    assert set(plan.engines) == {"gpt-oss-120b", "nemotron-3-super"}
    assert "downgraded from deep" in plan.reason
    assert mid.plan_deep_think("standard", task_id="dt3").granted == "standard"

    small, _, _, _ = make(engines, budget_gib=100.0)  # Nemotron does not fit either
    plan = small.plan_deep_think("deep", task_id="dt4")
    assert plan.granted == "quick" and plan.engines == () and plan.required_bytes == 0
    assert small.plan_deep_think("standard", task_id="dt5").granted == "quick"
    assert small.plan_deep_think("quick", task_id="dt6").granted == "quick"
    with pytest.raises(ArbiterError):
        small.plan_deep_think("ultra", task_id="dt7")


def test_deep_think_decisions_are_in_the_ledger(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, _, _ = make(engines, budget_gib=140.0, ledger=ledger)
    arb.plan_deep_think("deep", task_id="dt-led")
    rows = ledger.list_arbiter_decisions(task_id="dt-led")
    assert rows and rows[0]["action"] == "deep-think" and "deep -> standard" in rows[0]["reason"]


# --- rule 1: measured footprints replace the estimate --------------------------------------------------------------


def test_confirm_loaded_records_the_measured_footprint(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    projected = arb.resident["gpt-oss-120b"].footprint.total_bytes
    measured = arb.confirm_loaded("gpt-oss-120b", task_id="t1")
    assert measured == engines["gpt-oss-120b"].footprint_bytes  # the stub charges weights only
    assert measured < projected
    assert arb.resident["gpt-oss-120b"].charged_bytes == measured
    assert arb.projected_footprint("gpt-oss-120b").measured is True
    status = arb.status()
    assert status["resident"][0]["measured_bytes"] == measured and status["budget_bytes"] == 170 * GIB


def test_resident_small_models_are_never_budgeted(engines: dict[str, EngineSpec]) -> None:
    arb, controller, _, _ = make(engines, budget_gib=1.0)
    d = arb.request_load("router-qwen3.5-4b", task_id="t-res")
    assert d.granted and d.projected_bytes == 0 and "not budgeted" in d.reason
    assert arb.resident == {} and controller.calls == [("start", "router-qwen3.5-4b")]
    assert arb.projected_footprint("embed-bge-m3").total_bytes == 0


def test_unknown_engine_is_an_error(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    with pytest.raises(ArbiterError, match="unknown engine"):
        arb.request_load("llama-4-does-not-exist", task_id="t")
    with pytest.raises(ArbiterError, match="resident set not measured"):
        Arbiter(engines, StubController(), StubProbe(GTT_TOTAL)).budget_bytes  # noqa: B018 — the property must raise
