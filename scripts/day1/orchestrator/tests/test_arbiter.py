"""V14a: the Engine Arbiter against stub footprints (Section 4.2 rules 1-9, Section 21 V14, Phase 2 gate).

Stub numbers (make()): GTT total 192 GiB (amdgpu.gttsize=196608, Appendix B), GPU resident set 22 GiB, the host resident
set of the CPU-placed retrieval pair added on top (D-14 (e)), no host reserve -> an engine budget of 170 GiB. That is a
test figure, not Section 4.1's: v0.4.0 expects about 156 GiB on the node (MemTotal ~187.5 GiB less the router-only GPU
set, the 16 GiB reserve and the CPU pair), and the tests that set the reserve assert those figures.
Footprints are the fixture engines.json's exact file bytes (D-31; EngineSpec.footprint_bytes; footprint_gb read as GiB
only for the unchanged abliterated entry, which carries no byte counts), KV, per-slot state and the compute-buffer term
from atlas.arbiter (S-17). The residency limit is the D-32 default (one LLM seat) unless a test passes max_resident=2.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from atlas.arbiter import (
    APEX_KEY,
    DEFAULT_COMPANION_COMPUTE_BUFFER_GIB,
    DEFAULT_COMPUTE_BUFFER_GIB,
    GIB,
    MAX_REQUEST_SPECS,
    MAX_RESIDENT,
    Arbiter,
    ArbiterError,
    Decision,
    ReleaseTimeout,
    StubProbe,
    SysfsMemoryProbe,
    UnknownEngine,
    build_arbiter,
    compute_buffer_bytes,
    host_used_bytes,
    kv_estimate_bytes,
    kv_state_bytes,
    process_rss_bytes,
    read_unit_profile,
)
from atlas.config import ConfigError, EngineSpec, load_phase4_engines
from atlas.engines import EngineControlError, EngineError, StubController
from atlas.ledger import Ledger

GTT_TOTAL = 196608 * 1024 * 1024  # 206158430208 bytes, the V3 figure
RESIDENT_SET = 22 * GIB
GLM = "glm-5.3-flash"


class FakeClock:
    """Monotonic clock the tests drive; sleep() advances it so release polling terminates instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 0.001
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def host_set(engines: dict[str, EngineSpec]) -> int:
    """The projected host resident set of the CPU-placed companions (D-14 (e)), what measure_resident_set() charges
    when no pid is known."""
    arb = Arbiter(engines, None, None)  # type: ignore[arg-type]  # a projection touches neither
    return sum(arb.companion_projection(k).total_bytes for k, s in engines.items()
               if s.is_resident and s.placement == "cpu")


def make(engines: dict[str, EngineSpec], *, budget_gib: float = 170.0, leak: bool = False,
         ledger: Ledger | None = None, release_timeout_s: float = 60.0,
         engines_env_dir: Path | None = None, max_resident: int = MAX_RESIDENT,
         rss_of: Callable[[int], int | None] | None = None) -> tuple[Arbiter, StubController, StubProbe, FakeClock]:
    """An Arbiter whose ENGINE budget is `budget_gib`: the probe's GTT total carries the GPU resident set and the
    host resident set on top of it."""
    probe = StubProbe(total_bytes=int(RESIDENT_SET + budget_gib * GIB) + host_set(engines), used_bytes=RESIDENT_SET)
    controller = StubController(engines=engines, probe=probe, leak_on_stop=leak)
    clock = FakeClock()
    extra = {"rss_of": rss_of} if rss_of is not None else {}
    arb = Arbiter(engines, controller, probe, ledger=ledger, release_timeout_s=release_timeout_s,
                  poll_interval_s=1.0, clock=clock, sleep=clock.sleep, engines_env_dir=engines_env_dir,
                  max_resident=max_resident, **extra)
    arb.measure_resident_set()
    return arb, controller, probe, clock


def write_env(env_dir: Path, key: str, *, ctx: int, parallel: int, kv: str, coresident: bool | None = None) -> Path:
    """An env file as phase2/engine-env.py renders it (single-quoted values; the keys the Arbiter reads). `coresident`
    writes the ATLAS_CORESIDENT line an env file rendered before v0.4.0 carries (S-22: ignored)."""
    path = env_dir / f"{key}.env"
    co = "" if coresident is None else f"ATLAS_CORESIDENT={1 if coresident else 0}\n"
    path.write_text(f"# {key}.env — test\nATLAS_ENGINE='{key}'\nATLAS_MODE='chat'\nATLAS_KV_TYPE='{kv}'\n"
                    f"ATLAS_CTX_SIZE={ctx}\nATLAS_PARALLEL={parallel}\nATLAS_N_KEEP=4096\n"
                    f"{co}LLAMA_ARG_PORT=8103\nARGS='--ctx-size {ctx}'\n")
    return path


def with_twin(engines: dict[str, EngineSpec]) -> dict[str, EngineSpec]:
    """The fixture plus a second gpt-oss-sized seat ('gpt-oss-120b-b', test only) so the D-32 option (b) pairing and
    its LRU eviction can be exercised with three small seats."""
    twin = engines["gpt-oss-120b"].model_copy(update={"key": "gpt-oss-120b-b", "index": 0, "port": 0,
                                                     "kv_bytes_per_token_f16": 36_864,
                                                     "kv_state_bytes_per_sequence": 84_934_656})
    return {**engines, "gpt-oss-120b-b": twin}


# --- budget and the KV model -------------------------------------------------------------------------------------


def test_budget_is_gtt_total_minus_resident_set(engines: dict[str, EngineSpec]) -> None:
    arb, _, probe, _ = make(engines)
    assert arb.resident_set_bytes == RESIDENT_SET
    # D-14 (e): the CPU retrieval pair is charged beside the GPU set, at its projection while no pid is known.
    assert arb.host_resident_set_bytes == host_set(engines) > 0
    assert arb.budget_bytes == probe.total_bytes - RESIDENT_SET - arb.host_resident_set_bytes == 170 * GIB
    assert arb.free_bytes == arb.budget_bytes
    st = arb.status()
    assert st["host_resident_set_bytes"] == host_set(engines) and st["max_resident"] == 1
    assert set(st["host_resident"]) == {"embed-qwen3-4b", "rerank-qwen3-4b"}
    assert all("projected" in v["source"] for v in st["host_resident"].values())


def test_kv_estimate_uses_the_total_pool_not_per_slot(engines: dict[str, EngineSpec]) -> None:
    spec = engines["gpt-oss-120b"]
    # Research conflict 9: ctx is the TOTAL pool; parallel splits it and must not multiply the estimate.
    assert kv_estimate_bytes(spec, 262144, 8) == kv_estimate_bytes(spec, 262144, 1)
    assert kv_estimate_bytes(spec, 262144, 8) == 8 * kv_estimate_bytes(spec, 32768, 1)
    # q4_0 is smaller than q8_0 which is smaller than f16 (Section 4.3).
    assert kv_estimate_bytes(spec, 262144, 8, "q4_0") < kv_estimate_bytes(spec, 262144, 8, "q8_0") < kv_estimate_bytes(
        spec, 262144, 8, "f16")
    with pytest.raises(ArbiterError, match="TOTAL pool"):
        kv_estimate_bytes(spec, 1024, 8)  # a per-slot figure passed by mistake
    with pytest.raises(ArbiterError, match="parallel"):
        kv_estimate_bytes(spec, 262144, 0)  # never auto (gguf-models.md §1.3)


def test_kv_model_is_the_day1_table_with_per_slot_state_and_a_compute_term(engines: dict[str, EngineSpec]) -> None:
    """S-17 (v040 engine_data.md, b11474 cache code): GLM-5.3-Flash 19,712 B/token f16 (10,472 at q8_0), DeepSeek V4
    6,880 (3,655), gpt-oss-120b 36,864 (full-attention layers only), the Qwen3 retrieval pair 147,456; the per-slot
    state that does not grow with context is charged once per slot; every LLM projection carries the compute-buffer
    term."""
    glm, dsv4, oss = engines[GLM], engines[APEX_KEY], engines["gpt-oss-120b"]
    assert kv_estimate_bytes(glm, 32768, 1, "f16") == 19_712 * 32768
    assert kv_estimate_bytes(glm, 32768, 1, "q8_0") == 10_472 * 32768
    assert kv_estimate_bytes(dsv4, 131072, 1, "f16") == 6_880 * 131072
    assert kv_estimate_bytes(dsv4, 131072, 1, "q8_0") == 3_655 * 131072
    assert kv_estimate_bytes(oss, 262144, 8, "f16") == 36_864 * 262144
    assert kv_estimate_bytes(engines["embed-qwen3-4b"], 4096, 1, "q8_0") == int(147_456 * 4096 * 8.5 / 16)
    # kv_class `none` on an engine with a cache is llama.cpp's default f16 (no --cache-type flag rendered).
    spec_none = engines["embed-qwen3-4b"].model_copy(update={"kv_class": "none"})
    assert kv_estimate_bytes(spec_none, 4096, 1) == 147_456 * 4096
    # GLM's 34 KDA recurrent layers: 152,633,344 B per slot (f32, independent of context).
    assert kv_state_bytes(glm, 8) == 8 * 152_633_344 and kv_state_bytes(glm, 1) == 152_633_344
    assert kv_state_bytes(engines["gpt-oss-120b-abliterated"], 8) == 0  # the unchanged uncensored entry: no new term
    fp = Arbiter(engines, None, None).projected_footprint(GLM)  # type: ignore[arg-type]
    assert (fp.ctx, fp.parallel, fp.kv_class) == (262144, 8, "f16")
    assert fp.weights_bytes == glm.footprint_bytes and fp.compute_bytes == int(DEFAULT_COMPUTE_BUFFER_GIB * GIB)
    assert fp.kv_bytes == 19_712 * 262144 + 8 * 152_633_344
    assert fp.total_bytes == fp.weights_bytes + fp.kv_bytes + fp.compute_bytes
    assert compute_buffer_bytes(engines["embed-qwen3-4b"]) == int(DEFAULT_COMPANION_COMPUTE_BUFFER_GIB * GIB)
    # engines.json may carry its own figures (a phased engine brings them this way).
    custom = glm.model_copy(update={"kv_bytes_per_token_f16": 1000, "kv_state_bytes_per_sequence": 7,
                                    "compute_buffer_gib": 3.5})
    assert kv_estimate_bytes(custom, 4096, 1, "f16") == 4_096_000 and kv_state_bytes(custom, 2) == 14
    assert compute_buffer_bytes(custom) == int(3.5 * GIB)


def test_each_day1_seat_fits_alone_and_no_two_seats_fit_together(engines: dict[str, EngineSpec]) -> None:
    """D-32 / S-21: every Day 1 seat runs alone against the whole budget. At the ~156 GiB Section 4.1 expects (156.0
    here, MemTotal 187 GiB less a 5.5 GiB router-only GPU set, the 16 GiB reserve and the CPU pair) GLM-5.3-Flash
    (147.10 GiB of files with its mmproj, D-31) and DeepSeek-V4-Flash-0731 UD-Q8_K_XL (150.75 GiB) each fit; no pair of
    seats does, so the residency limit of one costs nothing the budget would have allowed."""
    arb, _, _, _ = make(engines, budget_gib=156.0)
    seats = {k: arb.projected_footprint(k).total_bytes for k in (GLM, APEX_KEY, "gpt-oss-120b")}
    for key, size in seats.items():
        assert size <= arb.budget_bytes, (key, size / GIB)
    assert seats[GLM] + seats["gpt-oss-120b"] > arb.budget_bytes
    assert seats[APEX_KEY] + seats["gpt-oss-120b"] > arb.budget_bytes
    # D-31: before v0.4.0 the 161.87 GB DeepSeek file was read as 161.87 GiB and refused at this budget.
    assert engines[APEX_KEY].footprint_gb * GIB > arb.budget_bytes > engines[APEX_KEY].footprint_bytes


# --- rule 2: refusal of an over-budget load ----------------------------------------------------------------------


def test_refuses_over_budget_load_and_logs_it(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, _, _ = make(engines, budget_gib=60.0, ledger=ledger)
    d = arb.request_load(GLM, task_id="t-over")
    assert d.decision is Decision.REFUSED
    assert "exceeds the engine budget" in d.reason
    assert d.projected_bytes > d.budget_bytes
    assert controller.calls == []  # nothing was started (4.3: the Arbiter is the OOM backstop)
    assert arb.resident == {}
    rows = ledger.list_arbiter_decisions(task_id="t-over")
    assert len(rows) == 1 and rows[0]["decision"] == "refused" and rows[0]["engine"] == GLM  # rule 9


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
    # One seat resident (D-32): two tasks on it share the ONE generation slot, FIFO (rule 4).
    arb, _, _, _ = make(engines)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    first = arb.try_generation("gpt-oss-120b", task_id="g1")
    assert first.granted and arb.generating == ("gpt-oss-120b", "g1")
    second = arb.try_generation("gpt-oss-120b", task_id="g2")
    assert second.decision is Decision.QUEUED  # rule 4: waits, never runs concurrently
    assert "generation in progress on gpt-oss-120b" in second.reason
    arb.release_generation(task_id="g1")
    assert arb.try_generation("gpt-oss-120b", task_id="g2").granted
    arb.release_generation(task_id="g2")
    assert arb.generating is None
    # With D-32 option (b) (two seats), a generation on the other resident seat queues the same way.
    two, _, _, _ = make(engines, max_resident=2)
    assert two.request_load("gpt-oss-120b", task_id="t1").granted
    assert two.request_load("gpt-oss-120b-abliterated", task_id="t2").granted and len(two.resident) == 2
    assert two.try_generation("gpt-oss-120b", task_id="g1").granted
    assert two.try_generation("gpt-oss-120b-abliterated", task_id="g2").decision is Decision.QUEUED
    two.release_generation(task_id="g1")
    assert two.try_generation("gpt-oss-120b-abliterated", task_id="g2").granted


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


def test_one_seat_limit_evicts_the_resident_seat(engines: dict[str, EngineSpec]) -> None:
    """D-32 (a) / S-23: at the default limit one LLM seat is resident; the next seat evicts it even where both would
    fit the budget (gpt-oss-120b 68.3 + its twin 70.1 GiB < 170), and the eviction says why (rule 7 for every seat)."""
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, _, _ = make(engines, ledger=ledger)
    assert arb.max_resident == 1 == MAX_RESIDENT
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    d = arb.request_load("gpt-oss-120b-abliterated", task_id="t2")
    assert d.granted and d.evicted == ("gpt-oss-120b",)
    assert list(arb.resident) == ["gpt-oss-120b-abliterated"] and ("stop", "gpt-oss-120b") in controller.calls
    unloads = [r["reason"] for r in ledger.list_arbiter_decisions(limit=20) if r["action"] == "unload"]
    assert any("rule 7" in why and "D-32" in why for why in unloads)
    assert arb.charged_bytes <= arb.budget_bytes


def test_two_seat_setting_brings_back_pairing_with_lru_eviction(engines: dict[str, EngineSpec]) -> None:
    """D-32 option (b) without a code change: max_resident=2 pairs seats that fit and evicts the least recently used one
    for a third; the Apex engine stays exclusive (next test) and a 300B-class seat still evicts for memory."""
    specs = with_twin(engines)
    arb, _controller, _, _ = make(specs, max_resident=2)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.request_load("gpt-oss-120b-abliterated", task_id="t2").granted
    assert len(arb.resident) == 2
    d = arb.request_load("gpt-oss-120b-b", task_id="t3")
    assert d.granted and d.evicted == ("gpt-oss-120b",)  # LRU: loaded and last used first
    assert list(arb.resident) == ["gpt-oss-120b-abliterated", "gpt-oss-120b-b"]
    assert arb.charged_bytes <= arb.budget_bytes
    big = arb.request_load(GLM, task_id="t4")  # 155 GiB: neither small seat fits beside it
    assert big.granted and set(big.evicted) == {"gpt-oss-120b-abliterated", "gpt-oss-120b-b"}
    assert list(arb.resident) == [GLM]


def test_no_standing_coresident_profile_remains(engines: dict[str, EngineSpec], tmp_path: Path) -> None:
    """S-22: the S52 co-resident vision profile is gone. The vision engine (GLM-5.3-Flash with its glm5v mmproj) is
    projected at ctx_size x parallel like every engine, an engines.json that still carries the old fields loads and
    ignores them, and an older env file's ATLAS_CORESIDENT line changes nothing."""
    glm = engines[GLM]
    assert glm.mode == "vision" and glm.mmproj == "mmproj-F16.gguf"
    for gone in ("standing_coresident", "profile", "ctx_size_coresident", "parallel_coresident"):
        assert not hasattr(glm, gone)
    old = EngineSpec.model_validate({**glm.model_dump(), "ctx_size_coresident": 65536, "parallel_coresident": 2})
    assert (old.ctx_size, old.parallel) == (262144, 8)
    arb, _, _, _ = make(engines)
    fp = arb.projected_footprint(GLM)
    assert (fp.ctx, fp.parallel) == (262144, 8)
    write_env(tmp_path, GLM, ctx=131072, parallel=4, kv="f16", coresident=True)
    profile = read_unit_profile(tmp_path / f"{GLM}.env")
    assert profile is not None and (profile.ctx, profile.parallel, profile.kv_type) == (131072, 4, "f16")
    assert not hasattr(profile, "coresident") and "CORESIDENT" not in profile.describe()
    with pytest.raises(TypeError):
        arb.request_load(GLM, task_id="t", coresident=True)  # type: ignore[call-arg]  # the argument is gone too


def test_projection_follows_the_unit_env_file(engines: dict[str, EngineSpec], tmp_path: Path) -> None:
    """What `systemctl start` runs is <ATLAS_ENGINES_ENV_DIR>/<key>.env, not the Arbiter's wish: the projection
    follows the file, and a request that disagrees with it is refused (the Arbiter cannot re-render a root file)."""
    write_env(tmp_path, GLM, ctx=131072, parallel=4, kv="f16")
    write_env(tmp_path, "gpt-oss-120b", ctx=262144, parallel=8, kv="q8_0")
    arb, controller, _, _ = make(engines, engines_env_dir=tmp_path)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    refused = arb.request_load(GLM, task_id="t2", ctx=262144)
    assert refused.decision is Decision.REFUSED
    assert "ctx 262144 != unit 131072" in refused.reason and "engine-env.py --set-override" in refused.reason
    assert list(arb.resident) == ["gpt-oss-120b"] and ("start", GLM) not in controller.calls
    d = arb.request_load(GLM, task_id="t3")
    assert d.granted and d.evicted == ("gpt-oss-120b",)  # one seat resident (D-32)
    res = arb.resident[GLM].footprint
    assert (res.ctx, res.parallel, res.kv_class) == (131072, 4, "f16")
    assert d.projected_bytes == (engines[GLM].footprint_bytes + 19_712 * 131072 + 4 * 152_633_344
                                 + int(DEFAULT_COMPUTE_BUFFER_GIB * GIB))
    assert f"{GLM}.env: ctx 131072 x 4 slots, kv f16" in d.reason
    assert arb.request_load(GLM, task_id="t4", kv_class="q8_0").decision is Decision.REFUSED
    # The Apex KV ladder's rung (f16, then q8_0; S-42) reaches the Arbiter the same way.
    write_env(tmp_path, APEX_KEY, ctx=262144, parallel=1, kv="q8_0")
    fp, profile = arb.unit_footprint(APEX_KEY)  # type: ignore[misc]
    assert profile.kv_type == "q8_0" and fp.kv_bytes == 3_655 * 262144 + 46_022_656
    # A malformed file fails loudly, never silently projects from engines.json.
    (tmp_path / "gpt-oss-120b-abliterated.env").write_text("ATLAS_CTX_SIZE=4096\n")
    with pytest.raises(ArbiterError, match="missing"):
        arb.request_load("gpt-oss-120b-abliterated", task_id="t7")
    assert read_unit_profile(tmp_path / "nope.env") is None
    # No env file for an engine: engines.json projection, with a warning (systemctl start fails until it is rendered).
    (tmp_path / "gpt-oss-120b-abliterated.env").unlink()
    assert arb.unit_profile("gpt-oss-120b-abliterated") is None


def test_headroom_reserves_the_cpu_side_and_host_use_is_observed_not_budgeted(
        engines: dict[str, EngineSpec], caplog: pytest.LogCaptureFixture) -> None:
    """Section 4.1 (S44): mem_info_gtt_used never shows CPU processes, so the budget keeps `headroom_bytes` back for
    them. /proc/meminfo is read at each measurement and reported (status, one WARNING when CPU-side use outgrows the
    reserve), but never subtracted, so a transient CPU job cannot change a grant. The CPU retrieval pair (D-14 (e)) is
    inside the CPU-side figure but already charged as the host resident set, so the warning looks past it."""
    host = host_set(engines)
    total = RESIDENT_SET + 186 * GIB + host
    probe = StubProbe(total_bytes=total, used_bytes=RESIDENT_SET, mem_total_bytes=total, mem_available_bytes=total)
    controller = StubController(engines=engines, probe=probe)
    clock = FakeClock()
    arb = Arbiter(engines, controller, probe, headroom_bytes=16 * GIB, poll_interval_s=1.0, clock=clock,
                  sleep=clock.sleep)
    # CPU side at 12 GiB beside the pair (MemTotal - MemAvailable - GTT resident set): inside the reserve, no warning.
    probe.mem_available_bytes = total - RESIDENT_SET - host - 12 * GIB
    with caplog.at_level(logging.WARNING, logger="atlas.arbiter"):
        arb.measure_resident_set()
    assert arb.budget_bytes == 170 * GIB
    st = arb.status()
    assert st["headroom_bytes"] == 16 * GIB and st["host_used_bytes"] == host + 12 * GIB
    assert st["budget_bytes"] == 170 * GIB and st["host_resident_set_bytes"] == host
    assert "headroom" not in caplog.text
    # The Apex engine (153.6 GiB projected at its Day 1 profile) still fits the reserved budget on its own.
    assert arb.projected_footprint(APEX_KEY).total_bytes <= arb.budget_bytes
    # CPU side grows to 20 GiB beside the pair: reported and warned once, budget unchanged; a re-measure is quiet.
    probe.mem_available_bytes = total - RESIDENT_SET - host - 20 * GIB
    with caplog.at_level(logging.WARNING, logger="atlas.arbiter"):
        arb.measure_resident_set()
        arb.measure_resident_set()
    assert arb.budget_bytes == 170 * GIB and arb.status()["host_used_bytes"] == host + 20 * GIB
    assert caplog.text.count("ATLAS_ARBITER_HEADROOM_GIB") == 1
    # Back inside the reserve, then over again: warned again (a new episode, not a repeat).
    probe.mem_available_bytes = total - RESIDENT_SET - host - 10 * GIB
    arb.measure_resident_set()
    probe.mem_available_bytes = total - RESIDENT_SET - host - 30 * GIB
    with caplog.at_level(logging.WARNING, logger="atlas.arbiter"):
        arb.measure_resident_set()
    assert caplog.text.count("ATLAS_ARBITER_HEADROOM_GIB") == 2
    # A probe that cannot read the host reports None, never 0.
    probe.mem_total_bytes = None
    arb.measure_resident_set()
    assert arb.status()["host_used_bytes"] is None


def test_pool_is_capped_at_memtotal_and_gpu_page_counters_are_preferred(engines: dict[str, EngineSpec]) -> None:
    """S47: kernel 7.0.0-38 reports the requested 192 GiB as the GTT total on a node with less RAM; the budget must
    start from MemTotal then, or the excess silently eats the host reserve. GPUActive + GPUReclaim, where the kernel
    has them, are the GPU's real pages and replace gtt_used in the CPU-side estimate."""
    gtt_total, mem_total, resident = 192 * GIB, 187 * GIB, 6 * GIB
    host = host_set(engines)
    probe = StubProbe(total_bytes=gtt_total, used_bytes=resident, mem_total_bytes=mem_total,
                      mem_available_bytes=mem_total - resident - host - 12 * GIB)
    controller = StubController(engines=engines, probe=probe)
    clock = FakeClock()
    arb = Arbiter(engines, controller, probe, headroom_bytes=16 * GIB, poll_interval_s=1.0, clock=clock,
                  sleep=clock.sleep)
    arb.measure_resident_set()
    assert arb.pool_bytes == mem_total
    assert arb.budget_bytes == mem_total - resident - host - 16 * GIB  # ~155.4 GiB, not 170
    st = arb.status()
    assert st["gtt_total_bytes"] == gtt_total and st["mem_total_bytes"] == mem_total and st["pool_bytes"] == mem_total
    assert st["host_used_bytes"] == host + 12 * GIB
    # Both Day 1 seats still fit on this node (6 GiB GPU set, the larger end of the expected router-only figure).
    assert arb.projected_footprint(APEX_KEY).total_bytes <= arb.budget_bytes
    assert arb.projected_footprint(GLM).total_bytes <= arb.budget_bytes
    # A kernel that caps the GTT total itself (7.0.0-39+): the smaller figure wins either way.
    probe.total_bytes = 186 * GIB
    assert arb.pool_bytes == 186 * GIB
    # GPU page counters present: pooled pages after an unload are the GPU's, not the CPU side's.
    probe.total_bytes = gtt_total
    probe.gpu_pages_bytes = resident + 3 * GIB  # 3 GiB pooled by TTM after a free; gtt_used no longer shows it
    probe.mem_available_bytes = mem_total - resident - 3 * GIB - host - 12 * GIB
    arb.measure_resident_set()
    assert arb.status()["host_used_bytes"] == host + 12 * GIB
    # No host figures at all (a probe without /proc/meminfo): the GTT total alone, as before.
    probe2 = StubProbe(total_bytes=gtt_total, used_bytes=resident)
    arb2 = Arbiter(engines, StubController(engines=engines, probe=probe2), probe2, headroom_bytes=16 * GIB)
    arb2.measure_resident_set()
    assert arb2.pool_bytes == gtt_total and arb2.status()["mem_total_bytes"] is None


def _seats_on(engines: dict[str, EngineSpec], mem_total: int, resident: int) -> tuple[Arbiter, dict[str, int]]:
    """An Arbiter at the default reserve on a node with this MemTotal and GPU resident set (the GTT total reported as
    the requested 192 GiB, as kernel 7.0.0-38 does), and the projections of the Day 1 seats: GLM at its profile and the
    Apex engine at each rung of its S-42 KV ladder (f16, then q8_0; never q4_0) at its Day 1 context."""
    from atlas.config import DEFAULT_ARBITER_HEADROOM_GIB

    probe = StubProbe(total_bytes=192 * GIB, used_bytes=resident, mem_total_bytes=mem_total,
                      mem_available_bytes=mem_total - resident - 10 * GIB)
    arb = Arbiter(engines, StubController(engines=engines, probe=probe), probe,
                  headroom_bytes=DEFAULT_ARBITER_HEADROOM_GIB * GIB)
    arb.measure_resident_set()
    sizes = {kv: arb.projected_footprint(APEX_KEY, ctx=131072, parallel=1, kv_class=kv).total_bytes
             for kv in ("f16", "q8_0")}
    sizes[GLM] = arb.projected_footprint(GLM).total_bytes
    return arb, sizes


def test_default_reserve_keeps_both_day1_seats_on_the_expected_node(engines: dict[str, EngineSpec]) -> None:
    """v0.4.0 (S-21, S-38, D-14 (e)): the default reserve (16 GiB) must leave room for GLM-5.3-Flash at its Day 1
    profile and for every rung of the Apex ladder on the node Section 4.1 expects once Phase 1 step 4 has dropped
    kdump's crash-kernel reservation: MemTotal ~187-188 GiB, taken at its low end, with the router alone on the iGPU
    (~5.5 GiB) and the CPU retrieval pair charged beside it. INFERRED margins: about 0.9 GiB for GLM, 2.3 GiB for the
    Apex engine."""
    arb, sizes = _seats_on(engines, 187 * GIB, int(5.5 * GIB))
    assert arb.budget_bytes == 187 * GIB - int(5.5 * GIB) - 16 * GIB - host_set(engines)
    assert max(sizes.values()) <= arb.budget_bytes, {k: v / GIB for k, v in sizes.items()}


def test_a_surviving_crashkernel_reservation_refuses_both_day1_seats(engines: dict[str, EngineSpec]) -> None:
    """S48: why V3a fails when crash-kernel memory is still reserved. kdump-tools' crashkernel= takes 4.25 GiB out of
    MemTotal (~183.5 GiB left); at the 16 GiB default, the router-only GPU set and the CPU pair, the budget is about
    152.4 GiB, below GLM-5.3-Flash and below every rung of the Apex engine: neither Day 1 seat could load."""
    arb, sizes = _seats_on(engines, int(183.5 * GIB), int(5.5 * GIB))
    assert all(size > arb.budget_bytes for size in sizes.values()), {k: v / GIB for k, v in sizes.items()}


def test_sysfs_probe_reads_memtotal_and_memavailable(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       196083712 kB\nMemFree:         1000 kB\nMemAvailable:   180000000 kB\n")
    probe = SysfsMemoryProbe(drm_root=tmp_path, meminfo=meminfo)
    assert probe.host_meminfo() == (196083712 * 1024, 180000000 * 1024, None)  # a kernel without the GPU fields
    # Ubuntu 7.0.0-38 / mainline v7.1 add GPUActive and GPUReclaim (TTM pool pages in use / pooled after a free).
    meminfo.write_text("MemTotal:       196083712 kB\nMemAvailable:   180000000 kB\nGPUActive:       6000000 kB\n"
                       "GPUReclaim:       10000 kB\n")
    assert probe.host_meminfo() == (196083712 * 1024, 180000000 * 1024, 6010000 * 1024)
    meminfo.write_text("MemTotal:       196083712 kB\nMemFree:         1000 kB\n")
    assert probe.host_meminfo() is None
    assert SysfsMemoryProbe(drm_root=tmp_path, meminfo=tmp_path / "absent").host_meminfo() is None


def test_build_arbiter_takes_the_headroom_from_settings(engines: dict[str, EngineSpec], tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Production wiring (S44): build_arbiter reserves Settings.arbiter_headroom_bytes; an explicit argument wins."""
    monkeypatch.setenv("ATLAS_ENGINES_ENV_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_ARBITER_HEADROOM_GIB", raising=False)
    assert build_arbiter(engines).headroom_bytes == 16 * GIB
    monkeypatch.setenv("ATLAS_ARBITER_HEADROOM_GIB", "20")
    assert build_arbiter(engines).headroom_bytes == 20 * GIB
    assert build_arbiter(engines, headroom_bytes=0).headroom_bytes == 0
    # A bad override stops the Arbiter's construction (the orchestrator start), loudly, and nothing else.
    monkeypatch.setenv("ATLAS_ARBITER_HEADROOM_GIB", "3")
    with pytest.raises(ConfigError, match=r"outside 4\.\.64"):
        build_arbiter(engines)


def test_build_arbiter_takes_the_seat_limit_from_settings(engines: dict[str, EngineSpec], tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """D-32: the LLM-seat residency limit is a setting (ATLAS_ARBITER_MAX_RESIDENT, default 1, range 1..2); an explicit
    argument wins and a bad override stops the Arbiter's construction only, like the headroom. build_arbiter also wires
    the controller's MainPID reader for the CPU companions' host resident set (D-14 (e))."""
    from atlas.config import Settings
    from atlas.engines import SystemdEngineController

    monkeypatch.setenv("ATLAS_ENGINES_ENV_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_ARBITER_MAX_RESIDENT", raising=False)
    arb = build_arbiter(engines)
    assert arb.max_resident == 1 and isinstance(arb.controller, SystemdEngineController)
    assert arb._pid_of is not None and arb._pid_of.__self__ is arb.controller  # type: ignore[attr-defined]
    monkeypatch.setenv("ATLAS_ARBITER_MAX_RESIDENT", "2")
    assert build_arbiter(engines).max_resident == 2
    assert build_arbiter(engines, max_resident=1).max_resident == 1
    for bad, why in (("3", r"outside 1\.\.2"), ("0", r"outside 1\.\.2"), ("one", "not a whole number")):
        monkeypatch.setenv("ATLAS_ARBITER_MAX_RESIDENT", bad)
        assert Settings.from_env().arbiter_max_resident == 1  # the default stays; the error is kept for the Arbiter
        with pytest.raises(ConfigError, match=why):
            build_arbiter(engines)
    with pytest.raises(ArbiterError, match="must be >= 1"):
        Arbiter(engines, StubController(), StubProbe(GTT_TOTAL), max_resident=0)


def test_host_used_bytes_prefers_the_gpu_page_counters_and_never_goes_negative() -> None:
    """S44: the one rule for the CPU-side figure (the Arbiter's warning and atlas-admin arbiter status)."""
    assert host_used_bytes(187 * GIB, 165 * GIB, 6 * GIB, 9 * GIB) == 16 * GIB  # GPUActive + GPUReclaim win
    assert host_used_bytes(187 * GIB, 165 * GIB, None, 9 * GIB) == 13 * GIB  # else the GTT counter
    assert host_used_bytes(187 * GIB, 180 * GIB, None, 9 * GIB) == 0  # clamped, never negative


def test_admin_engines_list_shows_the_profile_and_the_budgeted_bytes(config_dir: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """`atlas-admin engines list` prints what the units run by default (ctx_size x parallel; S-22 removed the
    co-resident profile), the GiB the Arbiter budgets (D-31: exact file bytes) and where each server runs (D-14)."""
    from atlas import admin

    assert admin.main(["engines", "list", "--json", "--no-state"]) == 0
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)}
    assert (rows[GLM]["ctx_size"], rows[GLM]["parallel"]) == (262144, 8)
    assert rows[GLM]["footprint_gib"] == 147.1 and rows[GLM]["footprint_source"] == "files"
    assert rows[APEX_KEY]["footprint_gib"] == 150.75 and rows[APEX_KEY]["footprint_gb"] == 161.87
    assert rows["gpt-oss-120b-abliterated"]["footprint_source"] == "footprint_gb"  # unchanged entry, no byte counts
    assert rows["embed-qwen3-4b"]["placement"] == rows["rerank-qwen3-4b"]["placement"] == "cpu"
    assert rows["router-qwen3.5-4b"]["placement"] == "gpu"
    assert admin.main(["engines", "list", "--no-state"]) == 0
    assert "GiB" in capsys.readouterr().out


def test_admin_arbiter_status_shows_the_headroom_the_unit_uses(config_dir: Path, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    """`atlas-admin arbiter status` from a shell (S44): the unit reads ATLAS_ARBITER_HEADROOM_GIB from orchestrator.env,
    so the CLI reads that file too; the process environment still wins, and a bad value is shown, not fatal. Beside the
    headroom it shows the CPU-side use the Arbiter warns on, by the Arbiter's own rule (arbiter.host_used_bytes)."""
    from atlas import admin

    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "orchestrator.env").write_text("ATLAS_ARBITER_HEADROOM_GIB=20\n")
    monkeypatch.setenv("ATLAS_ETC", str(etc))
    monkeypatch.setenv("ATLAS_DB_PATH", str(tmp_path / "absent.sqlite3"))  # no ledger: nothing is opened

    class Probe:
        gpu_pages: int | None = 6 * GIB
        gtt_error = False

        def gtt_total_bytes(self) -> int:
            if self.gtt_error:
                raise ArbiterError("no AMD GPU (vendor 0x1002) under /sys/class/drm; is amdgpu bound?")
            return 187 * GIB

        def gtt_used_bytes(self) -> int:
            return 6 * GIB

        def host_meminfo(self) -> tuple[int, int, int | None] | None:
            return 187 * GIB, 165 * GIB, self.gpu_pages

    monkeypatch.setattr(admin, "SysfsMemoryProbe", Probe)
    monkeypatch.setattr(admin, "_unit_state", lambda *a, **k: "inactive")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    host = json.loads(capsys.readouterr().out)["host"]
    # CPU-side use: MemTotal 187 - MemAvailable 165 - GPU pages 6 = 16 GiB.
    assert host == {"headroom_bytes": 20 * GIB, "mem_total_bytes": 187 * GIB, "mem_available_bytes": 165 * GIB,
                    "gpu_pages_bytes": 6 * GIB, "host_used_bytes": 16 * GIB}
    assert admin.main(["arbiter", "status"]) == 0
    text = capsys.readouterr().out
    assert ("host: Arbiter headroom 20 GiB (ATLAS_ARBITER_HEADROOM_GIB); CPU-side use 16.00 GiB (MemTotal - "
            "MemAvailable - (GPUActive + GPUReclaim)); MemAvailable 165.00 GiB of MemTotal 187.00 GiB") in text
    # The same figure the Arbiter computes for the same counters.
    assert host["host_used_bytes"] == host_used_bytes(187 * GIB, 165 * GIB, 6 * GIB, 6 * GIB)
    # A kernel without GPUActive: the GTT counter stands in, as in the Arbiter; above the headroom it says so.
    Probe.gpu_pages = None
    monkeypatch.setenv("ATLAS_ARBITER_HEADROOM_GIB", "12")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    host = json.loads(capsys.readouterr().out)["host"]
    assert host["host_used_bytes"] == 16 * GIB and "gpu_pages_bytes" not in host
    assert admin.main(["arbiter", "status"]) == 0
    assert "CPU-side use 16.00 GiB (MemTotal - MemAvailable - GTT used, ABOVE the headroom)" in capsys.readouterr().out
    # Neither GPU figure readable: no CPU-side figure rather than a wrong one.
    Probe.gtt_error = True
    assert admin.main(["arbiter", "status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "error" in out["gtt"] and "host_used_bytes" not in out["host"]
    assert admin.main(["arbiter", "status"]) == 0
    assert "CPU-side use" not in capsys.readouterr().out
    Probe.gpu_pages, Probe.gtt_error = 6 * GIB, False
    monkeypatch.delenv("ATLAS_ARBITER_HEADROOM_GIB")
    monkeypatch.setenv("ATLAS_ARBITER_HEADROOM_GIB", "24")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["host"]["headroom_bytes"] == 24 * GIB
    monkeypatch.delenv("ATLAS_ARBITER_HEADROOM_GIB")
    (etc / "orchestrator.env").write_text("ATLAS_ARBITER_HEADROOM_GIB=2\n")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    host = out["host"]
    assert host["headroom_bytes"] == 16 * GIB and "outside 4..64" in host["error"]
    # D-32: the LLM-seat limit the unit runs with, read the same way (default 1; a bad value is shown, not fatal).
    assert out["max_resident"] == 1 and "max_resident_error" not in out
    (etc / "orchestrator.env").write_text("ATLAS_ARBITER_MAX_RESIDENT=2\n")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["max_resident"] == 2
    assert admin.main(["arbiter", "status"]) == 0
    assert "LLM seats active: none (limit 2, ATLAS_ARBITER_MAX_RESIDENT)" in capsys.readouterr().out
    (etc / "orchestrator.env").write_text("ATLAS_ARBITER_MAX_RESIDENT=5\n")
    assert admin.main(["arbiter", "status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["max_resident"] == 1 and "outside 1..2" in out["max_resident_error"]


def test_build_arbiter_wires_the_engines_env_dir_from_settings(engines: dict[str, EngineSpec], tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENGINES_ENV_DIR", str(tmp_path))
    arb = build_arbiter(engines)
    assert arb.engines_env_dir == tmp_path
    assert build_arbiter(engines, engines_env_dir=tmp_path / "x").engines_env_dir == tmp_path / "x"


def test_cpu_companions_are_charged_their_measured_rss_never_below_their_weights(
        engines: dict[str, EngineSpec]) -> None:
    """D-14 (e): the CPU retrieval pair is host memory the GTT counter never shows. measure_resident_set() charges each
    companion the VmRSS of its unit's MainPID (pid_of + rss_of) when it can read it, never below the bytes of its
    weights (file-backed pages the kernel may drop and re-read), else its projection; the budget follows."""
    pids = {"embed-qwen3-4b": 4101, "rerank-qwen3-4b": None}
    rss = {4101: 2 * GIB}

    def pid_of(key: str) -> int | None:
        if key == "boom":
            raise RuntimeError("never asked")
        return pids.get(key)

    probe = StubProbe(total_bytes=RESIDENT_SET + 170 * GIB, used_bytes=RESIDENT_SET)
    arb = Arbiter(engines, StubController(engines=engines, probe=probe), probe, pid_of=pid_of,
                  rss_of=lambda pid: rss.get(pid))
    arb.measure_resident_set()
    embed, rerank = engines["embed-qwen3-4b"], engines["rerank-qwen3-4b"]
    detail = arb.status()["host_resident"]
    # RSS 2 GiB is below the embedder's 3.99 GiB of weights: the weights are charged.
    assert detail["embed-qwen3-4b"]["charged_bytes"] == embed.footprint_bytes
    assert "measured VmRSS 2.00 GiB of pid 4101" in detail["embed-qwen3-4b"]["source"]
    # No pid (unit not running): the projection (weights + q8_0 KV at 4096 tokens + 0.5 GiB of compute buffers).
    projection = arb.companion_projection("rerank-qwen3-4b")
    assert projection.total_bytes == (rerank.footprint_bytes + int(147_456 * 4096 * 8.5 / 16)
                                      + int(DEFAULT_COMPANION_COMPUTE_BUFFER_GIB * GIB))
    assert detail["rerank-qwen3-4b"]["charged_bytes"] == projection.total_bytes
    assert arb.host_resident_set_bytes == embed.footprint_bytes + projection.total_bytes
    assert arb.budget_bytes == 170 * GIB - arb.host_resident_set_bytes
    # A larger RSS than the weights is charged as measured; the re-measure (nothing resident) picks it up.
    rss[4101] = 6 * GIB
    arb.measure_resident_set()
    assert arb.status()["host_resident"]["embed-qwen3-4b"]["charged_bytes"] == 6 * GIB
    # A pid reader that fails never stops a measurement: the projection stands in.
    arb._pid_of = lambda key: (_ for _ in ()).throw(RuntimeError("systemctl show failed"))
    arb.measure_resident_set()
    assert arb.host_resident_set_bytes == host_set(engines)
    # The GPU companion (the router) is never in the host set: the GTT counter measures it.
    assert "router-qwen3.5-4b" not in arb.status()["host_resident"]


def test_status_and_generation_release_answer_while_a_load_is_in_flight(engines: dict[str, EngineSpec]) -> None:
    """The controller's start (minutes on a cold load) runs outside the state lock: /health's status() call, a
    generation finishing on the other resident engine, and the FIFO must not wait for it. Two seats resident needs
    D-32 option (b) (max_resident=2); with one seat the swap stops the other first."""
    arb, controller, _, _ = make(engines, max_resident=2)
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
    t = threading.Thread(target=lambda: result.append(arb.request_load("gpt-oss-120b-abliterated", task_id="t2")))
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    status = arb.status()  # must return now, not after the load
    assert time.monotonic() - t0 < 1.0
    assert status["busy"] == "loading gpt-oss-120b-abliterated"
    placeholder = [r for r in status["resident"] if r["engine"] == "gpt-oss-120b-abliterated"]
    assert placeholder and placeholder[0]["loading"] is True
    assert arb.charged_bytes == sum(r.charged_bytes for r in arb.resident.values())  # the budget is reserved
    # The generation on the other engine ends and releases; the FIFO and a further request are answered at once.
    arb.release_generation(task_id="g1")
    assert arb.generating is None
    assert arb.try_generation("gpt-oss-120b-abliterated", task_id="g2").decision is Decision.REFUSED  # not serving
    assert arb.try_generation("gpt-oss-120b", task_id="g3").granted
    arb.release_generation(task_id="g3")
    queued = arb.request_load(GLM, task_id="t3")
    assert queued.decision is Decision.QUEUED and "arbiter busy" in queued.reason
    assert arb.request_unload("gpt-oss-120b", task_id="t4").decision is Decision.QUEUED
    assert arb.request_load("gpt-oss-120b-abliterated", task_id="t5").decision is Decision.QUEUED  # in progress
    release.set()
    t.join(5)
    assert result and result[0].granted and arb.busy is None
    assert arb.resident["gpt-oss-120b-abliterated"].loading is False and arb.status()["busy"] is None
    assert arb.try_generation("gpt-oss-120b-abliterated", task_id="g4").granted


def test_failed_start_releases_the_reservation(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    arb.controller = StubController(engines=engines, probe=arb.probe, fail_start=[GLM])
    with pytest.raises(EngineControlError):
        arb.request_load(GLM, task_id="t1")
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
    assert arb.request_load(GLM, task_id="t2").granted
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
    # Eviction path: GLM is a seat and one seat runs at a time (D-32), so gpt-oss is the victim; the stop fails;
    # nothing changed and nothing is wedged.
    with pytest.raises(EngineControlError, match="password is required"):
        arb.request_load(GLM, task_id="t2")
    victim = arb.resident["gpt-oss-120b"]
    assert victim.serving and not victim.unloading and GLM not in arb.resident
    assert arb.busy is None and arb.try_generation("gpt-oss-120b", task_id="g1").granted
    arb.release_generation(task_id="g1")
    # Direct unload path: same failure, same recovery; the ledger holds the error row with the systemctl hint.
    with pytest.raises(EngineControlError):
        arb.request_unload("gpt-oss-120b", task_id="t3")
    assert arb.resident["gpt-oss-120b"].serving and arb.busy is None
    errors = [(r["task_id"], r["engine"], r["action"], r["reason"])
              for r in ledger.list_arbiter_decisions(limit=50) if r["decision"] == "error"]
    assert [e[:3] for e in reversed(errors)] == [("t2", "gpt-oss-120b", "unload"), ("t2", GLM, "load"),
                                                 ("t3", "gpt-oss-120b", "unload")]
    for _tid, engine, action, why in errors:
        if action == "unload":
            assert "stop failed" in why and "systemctl is-active llama-server@gpt-oss-120b" in why
        else:
            assert why.startswith(f"eviction of gpt-oss-120b failed; {engine} not loaded, reservation released")
    # Once the cause is fixed (the stop works again) the retry succeeds.
    arb.controller.fail_stop.clear()
    assert arb.request_unload("gpt-oss-120b", task_id="t4").granted
    assert arb.resident == {} and arb.request_load(GLM, task_id="t5").granted


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
    # Rule 7 at D-32 option (b) (two seats): requesting the Apex engine unloads every co-resident seat first, and
    # nothing co-resides with it. Its guarantee is exclusivity of residency, not permanence: an idle Apex engine is the
    # first victim of the next seat's load, so no human has to POST /arbiter/unload after a deep run (fix round).
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, probe, _ = make(engines, ledger=ledger, max_resident=2)
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted
    assert arb.request_load("gpt-oss-120b-abliterated", task_id="t2").granted
    d = arb.request_load(APEX_KEY, task_id="t-apex")
    assert d.granted
    assert set(d.evicted) == {"gpt-oss-120b", "gpt-oss-120b-abliterated"}
    assert list(arb.resident) == [APEX_KEY]
    assert engines[APEX_KEY].is_apex and engines[APEX_KEY].exclusive
    assert probe.used_bytes == RESIDENT_SET + engines[APEX_KEY].footprint_bytes  # memory of both victims came back
    # Apex generating: a gpt-oss load is QUEUED (rule 6), never preempted, and the Apex engine stays.
    assert arb.try_generation(APEX_KEY, task_id="g-apex").granted
    queued = arb.request_load("gpt-oss-120b", task_id="t3")
    assert queued.decision is Decision.QUEUED and "never preempted" in queued.reason
    assert list(arb.resident) == [APEX_KEY]
    arb.release_generation(task_id="g-apex")
    # Apex idle: the load is GRANTED with the Apex engine as its sole victim, released through rule 5.
    back = arb.request_load("gpt-oss-120b", task_id="t4")
    assert back.granted and back.evicted == (APEX_KEY,)
    assert list(arb.resident) == ["gpt-oss-120b"]
    assert probe.used_bytes == RESIDENT_SET + engines["gpt-oss-120b"].footprint_bytes
    assert arb.request_load("gpt-oss-120b-abliterated", task_id="t5").granted  # the option (b) pair is back
    decisions = [(r["engine"], r["decision"], r["action"], r["reason"])
                 for r in ledger.list_arbiter_decisions(limit=50)]
    assert ("gpt-oss-120b", "queued", "load") in [d[:3] for d in decisions]
    assert (APEX_KEY, "granted", "load") in [d[:3] for d in decisions]
    assert any(e == APEX_KEY and dec == "granted" and act == "unload" and "rule 7" in why
               for e, dec, act, why in decisions)


def test_every_seat_is_exclusive_at_the_default_limit(engines: dict[str, EngineSpec]) -> None:
    """S-23: at the default limit rule 7 holds for every LLM seat, so GLM-5.3-Flash and DeepSeek-V4-Flash-0731 take
    turns (the Deep Think pair), each evicting the other when idle, never both resident; the companions stay."""
    arb, controller, _, _ = make(engines)
    assert arb.request_load(GLM, task_id="t1").granted
    d = arb.request_load(APEX_KEY, task_id="t2")
    assert d.granted and d.evicted == (GLM,) and list(arb.resident) == [APEX_KEY]
    back = arb.request_load(GLM, task_id="t3")
    assert back.granted and back.evicted == (APEX_KEY,) and list(arb.resident) == [GLM]
    assert not any(k in arb.resident for k in ("router-qwen3.5-4b", "embed-qwen3-4b", "rerank-qwen3-4b"))
    assert arb.host_resident_set_bytes == host_set(engines)  # untouched by the swaps
    assert [c for c in controller.calls if c[0] == "stop"] == [("stop", GLM), ("stop", APEX_KEY)]


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
        arb.request_load(GLM, task_id="t2")  # one seat at a time: gpt-oss must go first
    assert clock.now - before >= 60.0  # polled for the full time-box (research §6.3), not a single read
    assert ("stop", "gpt-oss-120b") in controller.calls and ("start", GLM) not in controller.calls
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
    # Section 9.1 / S-43: GLM-5.3-Flash generates, DeepSeek-V4-Flash-0731 scores and delivers the synthesis,
    # gpt-oss-120b gives the third opinion on Day 1 — one at a time, all requested up front (rule 8); the largest sets
    # the need.
    assert plan.engines == (GLM, APEX_KEY, "gpt-oss-120b")
    assert full.plan_deep_think("standard", task_id="dt1b").engines == (GLM, APEX_KEY)
    assert plan.required_bytes == full.projected_footprint(GLM).total_bytes
    assert plan.required_bytes == max(full.projected_footprint(k).total_bytes for k in plan.engines)
    # The pair is both tiers' largest engine, so a budget without room for GLM leaves only quick.
    small, _, _, _ = make(engines, budget_gib=150.0)
    plan = small.plan_deep_think("deep", task_id="dt4")
    assert plan.granted == "quick" and plan.engines == () and plan.required_bytes == 0 and plan.downgraded
    assert small.plan_deep_think("standard", task_id="dt5").granted == "quick"
    assert small.plan_deep_think("quick", task_id="dt6").granted == "quick"
    with pytest.raises(ArbiterError):
        small.plan_deep_think("ultra", task_id="dt7")
    # The deep -> standard step itself (rule 8), with tier lists where deep needs more than standard.
    probe = StubProbe(total_bytes=RESIDENT_SET + 100 * GIB + host_set(engines), used_bytes=RESIDENT_SET)
    mid = Arbiter(engines, StubController(engines=engines, probe=probe), probe,
                  deep_think_engines={"deep": (APEX_KEY, "gpt-oss-120b"), "standard": ("gpt-oss-120b",)})
    mid.measure_resident_set()
    plan = mid.plan_deep_think("deep", task_id="dt2")
    assert plan.granted == "standard" and plan.downgraded and plan.engines == ("gpt-oss-120b",)
    assert "downgraded from deep" in plan.reason


def test_deep_think_module_and_arbiter_name_the_same_day1_pair(engines: dict[str, EngineSpec]) -> None:
    """S-43: atlas.deep_think's role engines and the Arbiter's tier lists agree, and every one is a Day 1 key of
    engines.json (the third opinion is gpt-oss-120b until Qwen3.8-Flash-Next is pulled)."""
    from atlas import deep_think

    assert deep_think.GENERATOR_ENGINE == GLM and deep_think.ADVERSARY_ENGINE == APEX_KEY
    assert deep_think.SYNTHESIS_ENGINE == APEX_KEY and deep_think.THIRD_OPINION_ENGINE == "gpt-oss-120b"
    arb = Arbiter(engines, None, None)  # type: ignore[arg-type]
    assert set(arb.deep_think_engines["deep"]) == {deep_think.GENERATOR_ENGINE, deep_think.ADVERSARY_ENGINE,
                                                   deep_think.THIRD_OPINION_ENGINE, deep_think.SYNTHESIS_ENGINE}
    assert arb.deep_think_engines["standard"] == (deep_think.GENERATOR_ENGINE, deep_think.ADVERSARY_ENGINE)
    assert all(k in engines and engines[k].is_llm_seat for k in arb.deep_think_engines["deep"])


def test_deep_think_decisions_are_in_the_ledger(engines: dict[str, EngineSpec]) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, _, _, _ = make(engines, budget_gib=140.0, ledger=ledger)
    arb.plan_deep_think("deep", task_id="dt-led")
    rows = ledger.list_arbiter_decisions(task_id="dt-led")
    assert rows and rows[0]["action"] == "deep-think" and "deep -> quick" in rows[0]["reason"]


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


def test_measured_footprint_applies_at_the_engines_json_profile(engines: dict[str, EngineSpec]) -> None:
    """Rule 1: Phase 3 step 2 measures the unit as rendered; a figure registered while the engine is not resident is
    taken as engines.json's profile (ctx_size x parallel, kv_class), and any other profile keeps the projection, which
    errs high (Section 4.3 hard pre-flight)."""
    arb, _, _, _ = make(engines)
    arb.register_measured(GLM, 150 * GIB, task_id="p3")
    standing = arb.projected_footprint(GLM)
    assert standing.measured is True and standing.total_bytes == 150 * GIB and standing.parallel == 8
    assert standing.compute_bytes == 0  # the measurement includes the compute buffers
    other = arb.projected_footprint(GLM, ctx=131072, parallel=1)
    assert other.measured is False and other.parallel == 1 and other.compute_bytes > 0


def test_measurement_is_charged_only_at_the_profile_it_was_taken_at(engines: dict[str, EngineSpec],
                                                                    tmp_path: Path) -> None:
    """S52 fix round: a figure measured at one profile (an overrides.json change, a KV-ladder rung) is never charged
    to a unit re-rendered at another while the orchestrator keeps running; the other profile keeps its projection
    until it is measured itself."""
    write_env(tmp_path, GLM, ctx=131072, parallel=1, kv="f16")
    arb, _, _, _ = make(engines, engines_env_dir=tmp_path)
    assert arb.request_load(GLM, task_id="p3-02").granted
    m1 = 151 * GIB
    arb.register_measured(GLM, m1, task_id="p3-02")
    assert arb.measured_profile[GLM] == (131072, 1, "f16")
    fp1, _ = arb.unit_footprint(GLM) or (None, None)
    assert fp1 is not None and fp1.measured and fp1.total_bytes == m1  # the 1-slot unit is charged its measurement
    assert {m["engine"]: m["profile"] for m in arb.status()["measured"]}[GLM] == [131072, 1, "f16"]
    assert arb.request_unload(GLM, task_id="p3-02").granted
    # The unit is re-rendered at engines.json's 8 x 32k profile.
    write_env(tmp_path, GLM, ctx=262144, parallel=8, kv="f16")
    fp8, prof8 = arb.unit_footprint(GLM) or (None, None)
    assert fp8 is not None and prof8 is not None and (prof8.ctx, prof8.parallel) == (262144, 8)
    assert fp8.measured is False and fp8.total_bytes == arb.projected_footprint(GLM).total_bytes  # not M1
    d = arb.request_load(GLM, task_id="p3-03")
    assert d.granted and d.projected_bytes == fp8.total_bytes
    # Measured again while resident, the figure is charged there; registered while not resident (no profile known),
    # it is taken as engines.json's profile.
    arb.register_measured(GLM, 154 * GIB, task_id="p3-04")
    assert arb.measured_profile[GLM] == (262144, 8, "f16")
    assert arb.unit_footprint(GLM)[0].measured  # type: ignore[index]
    arb2, _, _, _ = make(engines)
    arb2.register_measured(GLM, 154 * GIB, task_id="p3-05")
    assert GLM not in arb2.measured_profile and arb2.projected_footprint(GLM).measured
    # A later registration while not resident replaces an earlier one-slot one and its profile with it.
    write_env(tmp_path, GLM, ctx=131072, parallel=1, kv="f16")
    arb3, _, _, _ = make(engines, engines_env_dir=tmp_path)
    assert arb3.request_load(GLM, task_id="p3-06").granted
    arb3.register_measured(GLM, m1, task_id="p3-06")
    assert arb3.request_unload(GLM, task_id="p3-06").granted
    arb3.register_measured(GLM, 154 * GIB, task_id="p3-07")
    assert GLM not in arb3.measured_profile
    standing = arb3.projected_footprint(GLM)
    assert standing.measured and standing.total_bytes == 154 * GIB and standing.parallel == 8
    assert not arb3.projected_footprint(GLM, ctx=131072, parallel=1).measured


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
    assert measured["flux1-dev"] == {"engine": "flux1-dev", "class": "phase4", "measured_bytes": 24 * GIB,
                                     "profile": None}  # registered while not resident
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
    # The CPU pair is never a load either: it is the host resident set (D-14 (e)), counted once, beside the budget.
    assert arb.projected_footprint("embed-qwen3-4b").total_bytes == 0
    assert arb.request_load("rerank-qwen3-4b", task_id="t-res2").projected_bytes == 0
    assert arb.companion_projection("embed-qwen3-4b").total_bytes > engines["embed-qwen3-4b"].footprint_bytes


def test_external_engine_is_budgeted_and_ledgered_without_systemctl(engines: dict[str, EngineSpec]) -> None:
    """Section 4.2 "Chatterbox when invoked": class external (engines.json `external` list) passes through the Arbiter
    like every weight-bearing load (rule 2 budget, rules 1/9 ledger) but no controller start or stop ever happens (the
    caller runs the process), it is not an LLM seat (D-32: it never takes or frees the seat), the registered peak RSS
    replaces the 4 GiB projection, an unload WITHOUT a pid is taken on the caller's word and says so ("not measured";
    the pid path is the next test), and an external resident is never an eviction victim (queued instead)."""
    ledger = Ledger(":memory:")
    ledger.init_db()
    arb, controller, probe, _ = make(engines, ledger=ledger, budget_gib=157.0)
    spec = engines["chatterbox"]
    assert spec.is_external and not spec.is_phase4 and not spec.is_llm_seat and arb.unit_profile("chatterbox") is None
    d = arb.request_load("chatterbox", task_id="v7")
    assert d.granted and d.projected_bytes == 4 * GIB and "class external" in d.reason and "ledger" in d.reason
    assert controller.calls == [] and "chatterbox" in arb.resident and arb.resident["chatterbox"].serving
    assert arb.charged_bytes == 4 * GIB and arb.resident["chatterbox"].observed_bytes == 0
    assert arb.status()["resident"][0]["class"] == "external"
    # rule 1: the measured footprint (phase2/05-voice.sh: peak RSS) replaces the projection, in place.
    arb.register_measured("chatterbox", 3 * GIB, task_id="p2-05")
    assert arb.charged_bytes == 3 * GIB and arb.resident["chatterbox"].measured_bytes == 3 * GIB
    # A seat loads beside it (it takes no seat); a seat that would need chatterbox's memory is QUEUED, never evicts it.
    assert arb.request_load("gpt-oss-120b", task_id="t1").granted and len(arb.resident) == 2
    q = arb.request_load(GLM, task_id="t2")  # 155 GiB + 3 GiB > 157 GiB: only chatterbox's memory would make room
    assert q.decision is Decision.QUEUED and "external engine(s) chatterbox" in q.reason
    assert "chatterbox" in arb.resident and "gpt-oss-120b" in arb.resident
    assert ("stop", "chatterbox") not in controller.calls and ("stop", "gpt-oss-120b") not in controller.calls
    # The caller's unload without a pid: no GTT poll (the probe did not move for it), no controller call, the charge
    # dropped on the caller's word and the reason says the release was NOT measured (fix round 6).
    probe.used_bytes = probe.used_bytes  # unchanged on purpose: nothing of chatterbox is on the GTT counter
    u = arb.request_unload("chatterbox", task_id="v7")
    assert u.granted and "caller's word" in u.reason and "NOT measured" in u.reason
    assert "chatterbox" not in arb.resident and arb.status()["halted"] is None
    assert [c for c in controller.calls if c[1] == "chatterbox"] == []
    rows = ledger.list_arbiter_decisions(limit=20)
    assert any(r["engine"] == "chatterbox" and r["action"] == "unload" and "caller's word" in r["reason"] for r in rows)
    # Now the queued load goes through (gpt-oss-120b is the seat it replaces, a real unit, stopped and confirmed).
    assert arb.request_load(GLM, task_id="t2").granted and ("stop", "gpt-oss-120b") in controller.calls
    # A TTS render beside the resident seat, where it fits, leaves the seat alone (D-32: it takes no seat).
    roomy, controller3, _, _ = make(engines, budget_gib=170.0)
    assert roomy.request_load(GLM, task_id="t3").granted
    tts = roomy.request_load("chatterbox", task_id="v8")
    assert tts.granted and tts.evicted == () and set(roomy.resident) == {GLM, "chatterbox"}
    assert ("stop", GLM) not in controller3.calls
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


def test_apex_request_beside_an_external_resident_is_decided_by_the_budget(engines: dict[str, EngineSpec]) -> None:
    """v0.4.0 (D-32): an external process is not an LLM seat, so seat exclusivity no longer queues the Apex engine
    behind it (v0.3.5's rule 7 exception); the budget decides. Where both fit they co-reside; where the Apex engine
    needs chatterbox's memory the request is QUEUED with that reason and goes through once the caller has unloaded."""
    roomy, controller, _, _ = make(engines, budget_gib=170.0)
    assert roomy.request_load("chatterbox", task_id="v7").granted
    d = roomy.request_load(APEX_KEY, task_id="t-apex")
    assert d.granted and d.evicted == () and set(roomy.resident) == {"chatterbox", APEX_KEY}
    tight, controller, _, _ = make(engines, budget_gib=155.0)
    assert tight.request_load("chatterbox", task_id="v7").granted
    q = tight.request_load(APEX_KEY, task_id="t-apex")
    assert q.decision is Decision.QUEUED
    assert "external engine(s) chatterbox" in q.reason and "POST /arbiter/unload" in q.reason
    assert list(tight.resident) == ["chatterbox"] and controller.calls == []
    assert tight.request_unload("chatterbox", task_id="v7").granted
    assert tight.request_load(APEX_KEY, task_id="t-apex").granted and list(tight.resident) == [APEX_KEY]


def test_unknown_engine_is_an_error(engines: dict[str, EngineSpec]) -> None:
    arb, _, _, _ = make(engines)
    with pytest.raises(ArbiterError, match="unknown engine"):
        arb.request_load("llama-4-does-not-exist", task_id="t")
    with pytest.raises(ArbiterError, match="resident set not measured"):
        Arbiter(engines, StubController(), StubProbe(GTT_TOTAL)).budget_bytes  # noqa: B018 — the property must raise
