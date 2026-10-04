"""V14a: the Engine Arbiter against stub footprints (Section 4.2 rules 1-9, Section 21 V14, Phase 2 gate).

Numbers: GTT total 192 GiB (amdgpu.gttsize=196608, Appendix B), resident set 22 GiB -> budget 170 GiB (Section 4.1).
Footprints are engines.json's Section 5.1 figures read as GiB (EngineSpec.footprint_bytes, the upper bound), KV from
the Section 4.3 model in atlas.arbiter.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from atlas.arbiter import (
    APEX_KEY,
    GIB,
    MAX_REQUEST_SPECS,
    MAX_RESIDENT,
    Arbiter,
    ArbiterError,
    Decision,
    ReleaseTimeout,
    StubProbe,
    UnknownEngine,
    build_arbiter,
    kv_estimate_bytes,
    process_rss_bytes,
    read_unit_profile,
)
from atlas.config import EngineSpec, load_phase4_engines
from atlas.engines import EngineControlError, EngineError, StubController
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
         ledger: Ledger | None = None, release_timeout_s: float = 60.0,
         engines_env_dir: Path | None = None,
         rss_of: Callable[[int], int | None] | None = None) -> tuple[Arbiter, StubController, StubProbe, FakeClock]:
    probe = StubProbe(total_bytes=int(RESIDENT_SET + budget_gib * GIB), used_bytes=RESIDENT_SET)
    controller = StubController(engines=engines, probe=probe, leak_on_stop=leak)
    clock = FakeClock()
    extra = {"rss_of": rss_of} if rss_of is not None else {}
    arb = Arbiter(engines, controller, probe, ledger=ledger, release_timeout_s=release_timeout_s,
                  poll_interval_s=1.0, clock=clock, sleep=clock.sleep, engines_env_dir=engines_env_dir, **extra)
    arb.measure_resident_set()
    return arb, controller, probe, clock


def write_env(env_dir: Path, key: str, *, ctx: int, parallel: int, kv: str, coresident: bool) -> Path:
    """An env file as phase2/engine-env.py renders it (single-quoted values; the keys the Arbiter reads)."""
    path = env_dir / f"{key}.env"
    path.write_text(f"# {key}.env — test\nATLAS_ENGINE='{key}'\nATLAS_MODE='chat'\nATLAS_KV_TYPE='{kv}'\n"
                    f"ATLAS_CTX_SIZE={ctx}\nATLAS_PARALLEL={parallel}\nATLAS_N_KEEP=4096\n"
                    f"ATLAS_CORESIDENT={1 if coresident else 0}\nLLAMA_ARG_PORT=8106\nARGS='--ctx-size {ctx}'\n")
    return path


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


def test_evicted_engine_leaves_the_newcomer_alone_at_its_full_profile(engines: dict[str, EngineSpec]) -> None:
    """Nemotron resident, the vision engine requested: Nemotron goes (LRU), and the vision engine, now alone, runs
    its full 262144 x 8 profile, not the 65536 x 2 co-resident one it would run beside gpt-oss (Section 4.1/4.3)."""
    arb, controller, _, _ = make(engines)
    assert arb.request_load("nemotron-3-super", task_id="t1").granted
    d = arb.request_load("qwen2.5-vl-72b", task_id="t2")
    assert d.granted and d.evicted == ("nemotron-3-super",) and "coresident" not in d.reason
    res = arb.resident["qwen2.5-vl-72b"]
    assert (res.footprint.ctx, res.footprint.parallel) == (262144, 8)
    assert d.projected_bytes == arb.projected_footprint("qwen2.5-vl-72b").total_bytes
    assert list(arb.resident) == ["qwen2.5-vl-72b"] and controller.calls[-1] == ("start", "qwen2.5-vl-72b")
    # Beside gpt-oss it is the co-resident profile (the everyday pairing, no eviction).
    arb2, _, _, _ = make(engines)
    assert arb2.request_load("gpt-oss-120b", task_id="t3").granted
    d2 = arb2.request_load("qwen2.5-vl-72b", task_id="t4")
    assert d2.granted and d2.evicted == () and "coresident" in d2.reason
    assert (arb2.resident["qwen2.5-vl-72b"].footprint.ctx, arb2.resident["qwen2.5-vl-72b"].footprint.parallel) == (
        65536, 2)


def test_projection_follows_the_unit_env_file(engines: dict[str, EngineSpec], tmp_path: Path) -> None:
    """What `systemctl start` runs is <ATLAS_ENGINES_ENV_DIR>/<key>.env, not the Arbiter's wish: the projection
    follows the file, and a request that disagrees with it is refused (the Arbiter cannot re-render a root file)."""
    write_env(tmp_path, "qwen2.5-vl-72b", ctx=262144, parallel=8, kv="q8_0", coresident=False)
    write_env(tmp_path, "gpt-oss-120b", ctx=262144, parallel=8, kv="q4_0", coresident=False)
    arb, controller, _, _ = make(engines, engines_env_dir=tmp_path)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    # A co-resident load is REFUSED while the env file says 8 slots (V14a, fix round).
    refused = arb.request_load("qwen2.5-vl-72b", task_id="t2", coresident=True)
    assert refused.decision is Decision.REFUSED
    assert "coresident True != unit False" in refused.reason and "engine-env.py --set-override" in refused.reason
    assert list(arb.resident) == ["gpt-oss-120b"] and ("start", "qwen2.5-vl-72b") not in controller.calls
    # Left to itself the Arbiter projects the file's 8-slot footprint (~123 GB), which does not fit beside gpt-oss:
    # gpt-oss is evicted (LRU) instead of the pair being admitted at a projection the unit would not run.
    d = arb.request_load("qwen2.5-vl-72b", task_id="t3")
    assert d.granted and d.evicted == ("gpt-oss-120b",)
    assert (arb.resident["qwen2.5-vl-72b"].footprint.ctx, arb.resident["qwen2.5-vl-72b"].footprint.parallel) == (
        262144, 8)
    assert "ATLAS_CORESIDENT=0" in d.reason and "overrides.json coresident=true" in d.reason
    assert arb.charged_bytes <= arb.budget_bytes
    # With the override rendered (what Phase 3's coresident step does through engine-env.py), the pair fits.
    write_env(tmp_path, "qwen2.5-vl-72b", ctx=65536, parallel=2, kv="q8_0", coresident=True)
    arb2, _, _, _ = make(engines, engines_env_dir=tmp_path)
    assert arb2.request_load("gpt-oss-120b", task_id="t4").granted
    d2 = arb2.request_load("qwen2.5-vl-72b", task_id="t5")
    assert d2.granted and d2.evicted == () and "ATLAS_CORESIDENT=1" in d2.reason
    assert len(arb2.resident) == 2 and arb2.resident["qwen2.5-vl-72b"].footprint.parallel == 2
    assert arb2.request_load("qwen2.5-vl-72b", task_id="t6", ctx=262144).decision is Decision.REFUSED
    # The DeepSeek KV ladder's f16 result reaches the Arbiter the same way (config.KV_CLASSES stays quantised).
    write_env(tmp_path, APEX_KEY, ctx=16384, parallel=1, kv="f16", coresident=False)
    fp, profile = arb2.unit_footprint(APEX_KEY)
    assert profile.kv_type == "f16" and fp.kv_bytes == 327_680 * 16384
    # A malformed file fails loudly, never silently projects from engines.json.
    (tmp_path / "meditron-70b.env").write_text("ATLAS_CTX_SIZE=4096\n")
    with pytest.raises(ArbiterError, match="missing"):
        arb2.request_load("meditron-70b", task_id="t7")
    assert read_unit_profile(tmp_path / "nope.env") is None
    # No env file for an engine: engines.json projection, with a warning (systemctl start fails until it is rendered).
    assert arb2.unit_profile("nemotron-3-super") is None


def test_build_arbiter_wires_the_engines_env_dir_from_settings(engines: dict[str, EngineSpec], tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENGINES_ENV_DIR", str(tmp_path))
    arb = build_arbiter(engines)
    assert arb.engines_env_dir == tmp_path
    assert build_arbiter(engines, engines_env_dir=tmp_path / "x").engines_env_dir == tmp_path / "x"


def test_status_and_generation_release_answer_while_a_load_is_in_flight(engines: dict[str, EngineSpec]) -> None:
    """The controller's start (minutes on a cold load) runs outside the state lock: /health's status() call, a
    generation finishing on the other resident engine, and the FIFO must not wait for it."""
    arb, controller, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.try_generation("gpt-oss-120b", task_id="g1").granted
    started, release = threading.Event(), threading.Event()
    real_start = controller.start

    def slow_start(key: str) -> None:
        started.set()
        assert release.wait(5)
        real_start(key)

    controller.start = slow_start  # type: ignore[method-assign]
    result: list = []
    t = threading.Thread(target=lambda: result.append(arb.request_load("qwen2.5-vl-72b", task_id="t2")))
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    status = arb.status()  # must return now, not after the load
    assert time.monotonic() - t0 < 1.0
    assert status["busy"] == "loading qwen2.5-vl-72b"
    placeholder = [r for r in status["resident"] if r["engine"] == "qwen2.5-vl-72b"]
    assert placeholder and placeholder[0]["loading"] is True
    assert arb.charged_bytes == sum(r.charged_bytes for r in arb.resident.values())  # the budget is reserved
    # The generation on the other engine ends and releases; the FIFO and a further request are answered at once.
    arb.release_generation(task_id="g1")
    assert arb.generating is None
    assert arb.try_generation("qwen2.5-vl-72b", task_id="g2").decision is Decision.REFUSED  # not serving yet
    assert arb.try_generation("gpt-oss-120b", task_id="g3").granted
    arb.release_generation(task_id="g3")
    queued = arb.request_load("gpt-oss-120b-abliterated", task_id="t3")
    assert queued.decision is Decision.QUEUED and "arbiter busy" in queued.reason
    assert arb.request_unload("gpt-oss-120b", task_id="t4").decision is Decision.QUEUED
    assert arb.request_load("qwen2.5-vl-72b", task_id="t5").decision is Decision.QUEUED  # same key: in progress
    release.set()
    t.join(5)
    assert result and result[0].granted and arb.busy is None
    assert arb.resident["qwen2.5-vl-72b"].loading is False and arb.status()["busy"] is None
    assert arb.try_generation("qwen2.5-vl-72b", task_id="g4").granted


def test_failed_start_releases_the_reservation(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    arb.controller = StubController(engines=engines, probe=arb.probe, fail_start=["nemotron-3-super"])
    with pytest.raises(EngineControlError):
        arb.request_load("nemotron-3-super", task_id="t1")
    assert arb.resident == {} and arb.busy is None and arb.charged_bytes == 0
    assert arb.request_load("gpt-oss-120b", task_id="t2").granted  # not halted: the memory was never taken


def test_post_start_health_failure_releases_the_reservation(engines: dict[str, EngineSpec]) -> None:
    # SystemdEngineController.start raises the PARENT EngineError when `systemctl start` returned 0 but /health never
    # answered 200 (wait_ready). The placeholder and `_busy` must go all the same, or one flapping unit wedges every
    # later load of every engine behind "arbiter busy: loading <key>" (fix round).
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, probe, _ = make(engines, ledger=ledger)
    arb.controller = StubController(engines=engines, probe=probe, fail_start=["gpt-oss-120b"], start_error=EngineError)
    with pytest.raises(EngineError, match="start of gpt-oss-120b refused"):
        arb.request_load("gpt-oss-120b", task_id="t1")
    assert arb.busy is None and arb.resident == {} and arb.status()["busy"] is None and arb.charged_bytes == 0
    assert arb.request_load("qwen2.5-vl-72b", task_id="t2").granted
    rows = [(r["engine"], r["decision"], r["reason"]) for r in ledger.list_arbiter_decisions(limit=20)]
    assert any(e == "gpt-oss-120b" and d == "error" and "start failed" in why and "reservation released" in why
               for e, d, why in rows)


def test_failed_stop_gives_the_victim_back(engines: dict[str, EngineSpec]) -> None:
    # `sudo -n systemctl stop` fails (sudoers fragment missing, systemctl timed out): the victim keeps running, so the
    # ledger must say it is serving again (4.2 rule 1) instead of leaving `unloading=True` for the life of the process.
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, probe, _ = make(engines, ledger=ledger)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    arb.controller = StubController(engines=engines, probe=probe, fail_stop=["gpt-oss-120b"])
    arb.controller.active.add("gpt-oss-120b")
    # Eviction path: nemotron (133 GiB) does not fit beside gpt-oss in 170 GiB, so gpt-oss is the victim; the stop
    # fails; nothing changed and nothing is wedged.
    with pytest.raises(EngineControlError, match="password is required"):
        arb.request_load("nemotron-3-super", task_id="t2")
    victim = arb.resident["gpt-oss-120b"]
    assert victim.serving and not victim.unloading and "nemotron-3-super" not in arb.resident
    assert arb.busy is None and arb.try_generation("gpt-oss-120b", task_id="g1").granted
    arb.release_generation(task_id="g1")
    # Direct unload path: same failure, same recovery; the ledger holds the error row with the systemctl hint.
    with pytest.raises(EngineControlError):
        arb.request_unload("gpt-oss-120b", task_id="t3")
    assert arb.resident["gpt-oss-120b"].serving and arb.busy is None
    errors = [(r["task_id"], r["engine"], r["action"], r["reason"])
              for r in ledger.list_arbiter_decisions(limit=50) if r["decision"] == "error"]
    assert [e[:3] for e in reversed(errors)] == [("t2", "gpt-oss-120b", "unload"), ("t2", "nemotron-3-super", "load"),
                                                 ("t3", "gpt-oss-120b", "unload")]
    for _tid, engine, action, why in errors:
        if action == "unload":
            assert "stop failed" in why and "systemctl is-active llama-server@gpt-oss-120b" in why
        else:
            assert why.startswith(f"eviction of gpt-oss-120b failed; {engine} not loaded, reservation released")
    # Once the cause is fixed (the stop works again) the retry succeeds.
    arb.controller.fail_stop.clear()
    assert arb.request_unload("gpt-oss-120b", task_id="t4").granted
    assert arb.resident == {} and arb.request_load("nemotron-3-super", task_id="t5").granted


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


def test_apex_unloads_everything_and_is_evicted_when_idle(engines: dict[str, EngineSpec]) -> None:
    # Rule 7: requesting the Apex engine unloads every co-resident engine first, and nothing else loads while it is
    # resident. Its guarantee is exclusivity of residency, not permanence: an idle Apex engine is the first victim of
    # the next load (Section 6.3 wants gpt-oss back "by default"), so no human has to POST /arbiter/unload after a
    # Deep Think deep run or TF_OMEGA (fix round).
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
    # Apex generating: a Ren load is QUEUED (rule 6), never preempted, and the Apex engine stays.
    assert arb.try_generation(APEX_KEY, task_id="g-apex").granted
    queued = arb.request_load("gpt-oss-120b", task_id="t3")
    assert queued.decision is Decision.QUEUED and "never preempted" in queued.reason
    assert list(arb.resident) == [APEX_KEY]
    arb.release_generation(task_id="g-apex")
    # Apex idle: the Ren load is GRANTED with the Apex engine as its sole victim, released through rule 5.
    back = arb.request_load("gpt-oss-120b", task_id="t4")
    assert back.granted and back.evicted == (APEX_KEY,)
    assert list(arb.resident) == ["gpt-oss-120b"]
    assert probe.used_bytes == RESIDENT_SET + engines["gpt-oss-120b"].footprint_bytes
    assert arb.request_load("qwen2.5-vl-72b", task_id="t5").granted  # the everyday pairing is back
    decisions = [(r["engine"], r["decision"], r["action"], r["reason"])
                 for r in ledger.list_arbiter_decisions(limit=50)]
    assert ("gpt-oss-120b", "queued", "load") in [d[:3] for d in decisions]
    assert (APEX_KEY, "granted", "load") in [d[:3] for d in decisions]
    assert any(e == APEX_KEY and dec == "granted" and act == "unload" and "rule 7" in why
               for e, dec, act, why in decisions)


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
    assert plan.granted == "deep" and not plan.downgraded and APEX_KEY in plan.engines
    # Section 9.1 deep: Standard's pair, Qwen3.5 as third opinion, the Apex engine for the synthesis — one at a time,
    # all requested up front (rule 8); the largest (the Apex engine) sets the requirement.
    assert plan.engines == ("gpt-oss-120b", "nemotron-3-super", "qwen3.5-122b", APEX_KEY)
    assert plan.required_bytes == full.projected_footprint(APEX_KEY).total_bytes
    assert plan.required_bytes == max(full.projected_footprint(k).total_bytes for k in plan.engines)

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


def test_register_measured_accepts_phase4_engines(engines: dict[str, EngineSpec], config_dir: Path,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    # Section 4.2: every weight-bearing process, the Phase 4 container engines included, passes through the Arbiter;
    # Phase 4 step 5 registers each passing engine with its measured footprint (POST /arbiter/register). Those keys come
    # from config/phase4-engines.json (class phase4). A key in NEITHER file is UnknownEngine (fix round 3: §8 makes
    # that file the single list, and 'ui-tars-1.5-7b' for the real key 'ui-tars' must not split one engine's footprint
    # across two ledger keys); allow_unknown=True is the bounded, logged escape hatch.
    phase4 = load_phase4_engines(config_dir)
    assert "flux1-dev" in phase4 and phase4["flux1-dev"].is_phase4 and phase4["flux1-dev"].arbiter_class == "phase4"
    assert not phase4["flux1-dev"].is_apex and phase4["flux1-dev"].kv_class == "none"
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, _, _ = make({**engines, **phase4}, ledger=ledger)
    arb.register_measured("flux1-dev", 24 * GIB, task_id="p4-05")
    measured = {m["engine"]: m for m in arb.status()["measured"]}
    assert measured["flux1-dev"] == {"engine": "flux1-dev", "class": "phase4", "measured_bytes": 24 * GIB}
    rows = ledger.list_arbiter_decisions(task_id="p4-05")
    assert rows[0]["action"] == "measure" and rows[0]["decision"] == "granted" and "class phase4" in rows[0]["reason"]
    assert "created from request" not in rows[0]["reason"]
    # A Phase 4 engine is never loaded as a llama-server unit: the answer is a reason, not a systemctl call.
    refused = arb.request_load("flux1-dev", task_id="p4-x")
    assert refused.decision is Decision.REFUSED and "Section 15.2" in refused.reason
    # A typo for a real key fails loudly; the real key (here trellis, in the fixture AND the real file) registers.
    with pytest.raises(UnknownEngine, match=r"ui-tars-1\.5-7b.*neither engines\.json nor phase4-engines\.json"):
        arb.register_measured("ui-tars-1.5-7b", 16 * GIB, task_id="p4-06")
    assert "ui-tars-1.5-7b" not in arb.engines and "ui-tars-1.5-7b" not in arb.measured
    arb.register_measured("trellis", 16 * GIB, task_id="p4-06")
    assert arb.measured["trellis"] == 16 * GIB and arb.engines["trellis"].is_phase4
    # allow_unknown=True: a bare unit-style key becomes a class-phase4 spec carrying the measurement, with a WARNING in
    # the log AND in the ledger row, bounded by MAX_REQUEST_SPECS; a malformed key is UnknownEngine either way.
    with caplog.at_level(logging.WARNING, logger="atlas.arbiter"):
        arb.register_measured("ui-tars-1.5-7b", 16 * GIB, task_id="p4-07", allow_unknown=True)
    assert "allow_unknown" in caplog.text and f"1/{MAX_REQUEST_SPECS} request-created" in caplog.text
    assert arb.engines["ui-tars-1.5-7b"].is_phase4 and arb.engines["ui-tars-1.5-7b"].footprint_bytes == 16 * GIB
    assert {m["engine"]: m["class"] for m in arb.status()["measured"]}["ui-tars-1.5-7b"] == "phase4"
    row = ledger.list_arbiter_decisions(task_id="p4-07")[0]
    assert "WARNING: spec created from request" in row["reason"]
    for i in range(MAX_REQUEST_SPECS - 1):
        arb.register_measured(f"made-up-{i}", 1, allow_unknown=True)
    with pytest.raises(UnknownEngine, match=f"{MAX_REQUEST_SPECS} request-created engine specs already exist"):
        arb.register_measured("one-too-many", 1, allow_unknown=True)
    arb.register_measured("made-up-0", 2, allow_unknown=True)  # re-measuring an existing one is not a new spec
    with pytest.raises(ArbiterError, match="not an engine key"):
        arb.register_measured("x y;z", 1)
    with pytest.raises(ArbiterError, match="not an engine key"):
        arb.register_measured("x y;z", 1, allow_unknown=True)
    with pytest.raises(ArbiterError, match="negative"):
        arb.register_measured("flux1-dev", -1)


def test_resident_small_models_are_never_budgeted(engines: dict[str, EngineSpec]) -> None:
    arb, controller, _, _ = make(engines, budget_gib=1.0)
    d = arb.request_load("router-qwen3.5-4b", task_id="t-res")
    assert d.granted and d.projected_bytes == 0 and "not budgeted" in d.reason
    assert arb.resident == {} and controller.calls == [("start", "router-qwen3.5-4b")]
    assert arb.projected_footprint("embed-bge-m3").total_bytes == 0


def test_external_engine_is_budgeted_and_ledgered_without_systemctl(engines: dict[str, EngineSpec]) -> None:
    """Section 4.2 "Chatterbox when invoked": class external (engines.json `external` list) passes through the Arbiter
    like every weight-bearing load (rule 2 budget, rule 3 slot, rules 1/9 ledger) but no controller start or stop ever
    happens (the caller runs the process), the registered peak RSS replaces the 4 GiB projection, an unload WITHOUT a
    pid is taken on the caller's word and says so ("not measured"; the pid path is the next test), and an external
    resident is never an eviction victim (queued instead)."""
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, probe, _ = make(engines, ledger=ledger)
    spec = engines["chatterbox"]
    assert spec.is_external and not spec.is_phase4 and arb.unit_profile("chatterbox") is None
    d = arb.request_load("chatterbox", task_id="v7")
    assert d.granted and d.projected_bytes == 4 * GIB and "class external" in d.reason and "ledger" in d.reason
    assert controller.calls == [] and "chatterbox" in arb.resident and arb.resident["chatterbox"].serving
    assert arb.charged_bytes == 4 * GIB and arb.resident["chatterbox"].observed_bytes == 0
    assert arb.status()["resident"][0]["class"] == "external"
    # rule 1: the measured footprint (phase2/05-voice.sh: peak RSS) replaces the projection, in place.
    arb.register_measured("chatterbox", 3 * GIB, task_id="p2-05")
    assert arb.charged_bytes == 3 * GIB and arb.resident["chatterbox"].measured_bytes == 3 * GIB
    # rule 3: a second resident fits beside it; a third that would need chatterbox's slot is QUEUED, never evicts it.
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted and len(arb.resident) == 2
    q = arb.request_load("nemotron-3-super", task_id="t2")
    assert q.decision is Decision.QUEUED and "external engine(s) chatterbox" in q.reason
    assert "chatterbox" in arb.resident
    assert ("stop", "chatterbox") not in controller.calls
    # The caller's unload without a pid: no GTT poll (the probe did not move for it), no controller call, the charge
    # dropped on the caller's word and the reason says the release was NOT measured (fix round 6).
    probe.used_bytes = probe.used_bytes  # unchanged on purpose: nothing of chatterbox is on the GTT counter
    u = arb.request_unload("chatterbox", task_id="v7")
    assert u.granted and "caller's word" in u.reason and "NOT measured" in u.reason
    assert "chatterbox" not in arb.resident and arb.status()["halted"] is None
    assert [c for c in controller.calls if c[1] == "chatterbox"] == []
    rows = ledger.list_arbiter_decisions(limit=20)
    assert any(r["engine"] == "chatterbox" and r["action"] == "unload" and "caller's word" in r["reason"] for r in rows)
    # Now the queued load goes through (gpt-oss-120b is the LRU victim, a real unit, stopped and confirmed).
    assert arb.request_load("nemotron-3-super", task_id="t2").granted and ("stop", "gpt-oss-120b") in controller.calls
    # A fresh Arbiter: the projection is charged until a measurement exists; re-measuring an external engine is no-op
    # on the GTT side (confirm_loaded is never called for it) and a plain measured load is marked as such.
    arb2, controller2, _, _ = make(engines)
    arb2.register_measured("chatterbox", 5 * GIB)
    d2 = arb2.request_load("chatterbox")
    assert d2.granted and d2.projected_bytes == 5 * GIB and "(measured)" in d2.reason and controller2.calls == []


def test_external_release_is_confirmed_against_the_process(engines: dict[str, EngineSpec]) -> None:
    """Section 4.2 rule 5 for class external (fix round 6): the GTT counter is the wrong instrument for a CPU process,
    so `request_unload(pid=...)` polls /proc/<pid>: a process still holding its weights times out (the engine STAYS
    resident and charged, an ERROR row, ReleaseTimeout WITHOUT halting the Arbiter); a process that is gone, or whose
    RSS is back within the tolerance, confirms the release. No controller call at any point."""
    ledger = Ledger(":memory:")
    ledger.init_db()
    rss: dict[int, int | None] = {4242: 3 * GIB}
    polls: list[int] = []

    def rss_of(pid: int) -> int | None:
        polls.append(pid)
        return rss.get(pid)

    arb, controller, _, _ = make(engines, ledger=ledger, release_timeout_s=10.0, rss_of=rss_of)
    assert arb.request_load("chatterbox", task_id="v7").granted
    with pytest.raises(ReleaseTimeout, match=r"process 4242 of chatterbox still holds 3\.00 GiB RSS .* not halted"):
        arb.request_unload("chatterbox", task_id="v7", pid=4242)
    assert set(polls) == {4242} and len(polls) >= 10  # polled once per second for release_timeout_s
    assert "chatterbox" in arb.resident and arb.resident["chatterbox"].serving and arb.charged_bytes == 4 * GIB
    assert arb.status()["halted"] is None  # the process is the caller's: no halt, the caller retries
    assert any(r["engine"] == "chatterbox" and r["action"] == "unload" and r["decision"] == "error"
               for r in ledger.list_arbiter_decisions(limit=20))
    # The process exits: /proc/<pid> is gone and the next unload confirms the release.
    rss[4242] = None
    u = arb.request_unload("chatterbox", task_id="v7", pid=4242)
    assert u.granted and "release confirmed: process 4242 gone" in u.reason and "chatterbox" not in arb.resident
    # An interpreter that lives on but freed the weights (RSS within the tolerance) confirms too.
    assert arb.request_load("chatterbox", task_id="v7b").granted
    rss[4242] = GIB // 2
    u2 = arb.request_unload("chatterbox", task_id="v7b", pid=4242)
    assert u2.granted and "within tolerance" in u2.reason and "chatterbox" not in arb.resident
    assert controller.calls == []


def test_process_rss_bytes_reads_proc(tmp_path: Path) -> None:
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "status").write_text("Name:\tpython\nVmPeak:\t 5000 kB\nVmRSS:\t 2048 kB\nThreads:\t1\n")
    (tmp_path / "124").mkdir()
    (tmp_path / "124" / "status").write_text("Name:\tpython\nState:\tZ (zombie)\n")  # no VmRSS: released
    assert process_rss_bytes(123, proc_root=tmp_path) == 2048 * 1024
    assert process_rss_bytes(124, proc_root=tmp_path) == 0
    assert process_rss_bytes(125, proc_root=tmp_path) is None


def test_apex_request_queues_behind_an_external_resident(engines: dict[str, EngineSpec]) -> None:
    """Rule 7 exception, named in the module docstring and engines.json's `external` notes: "unloads any co-resident
    engine first" cannot be performed on a caller-owned process, so an Apex request while chatterbox is resident is
    QUEUED with that reason and goes through once the caller has unloaded."""
    arb, controller, _, _ = make(engines)
    assert arb.request_load("chatterbox", task_id="v7").granted
    d = arb.request_load(APEX_KEY, task_id="t-apex")
    assert d.decision is Decision.QUEUED
    assert "external engine(s) chatterbox" in d.reason and "POST /arbiter/unload" in d.reason
    assert list(arb.resident) == ["chatterbox"] and controller.calls == []
    assert arb.request_unload("chatterbox", task_id="v7").granted
    assert arb.request_load(APEX_KEY, task_id="t-apex").granted and list(arb.resident) == [APEX_KEY]


def test_unknown_engine_is_an_error(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    with pytest.raises(ArbiterError, match="unknown engine"):
        arb.request_load("llama-4-does-not-exist", task_id="t")
    with pytest.raises(ArbiterError, match="resident set not measured"):
        Arbiter(engines, StubController(), StubProbe(GTT_TOTAL)).budget_bytes  # noqa: B018 — the property must raise
