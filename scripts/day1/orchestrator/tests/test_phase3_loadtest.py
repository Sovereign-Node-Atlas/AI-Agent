"""scripts/day1/phase3/loadtest.py, the pure parts (CONVENTIONS.md §7.8: no live services; llama-server, systemd,
journald, the GTT counter and the orchestrator are stubbed). Imported by path: loadtest.py is a script, not part of
the atlas package."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

LOADTEST = Path(__file__).resolve().parents[2] / "phase3" / "loadtest.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p3_loadtest", LOADTEST)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p3_loadtest"] = mod
    spec.loader.exec_module(mod)
    return mod


lt = _load()

GPT_OSS_JOURNAL = [
    "2026-09-27T10:00:01Z llama_context: flash_attn    = enabled",
    "llama_kv_cache: size = 5120.00 MiB (262144 cells, 18 layers, 8/1 seqs), K (q4_0): 2560.00 MiB, V (q4_0): "
    "2560.00 MiB",
    "llama_kv_cache: size =  640.00 MiB (  4096 cells, 18 layers, 8/1 seqs), K (q4_0):  320.00 MiB, V (q4_0): "
    " 320.00 MiB",
    "srv  init: initializing slots, n_slots = 8",
]
DEEPSEEK_JOURNAL = ["llama_context: flash_attn = enabled"] + [
    f"llama_kv_cache: size = {n}.00 MiB (32768 cells, 1 layers, 1/1 seqs), K (q4_0): 1.00 MiB, V (q4_0): 1.00 MiB"
    for n in (100, 200, 300, 400)
]
NEMOTRON_JOURNAL = [
    "llama_context: flash_attn = enabled",
    "llama_kv_cache: size = 8192.00 MiB (262144 cells, 6 layers, 8/1 seqs), K (q8_0): 4096.00 MiB, V (q8_0): "
    "4096.00 MiB",
    "llama_memory_recurrent: size = 1234.00 MiB (8 cells, 46 layers, 8 seqs), R (f32): 600.00 MiB, S (f32): "
    "634.00 MiB",
]
REFUSED_JOURNAL = [
    "llama_context: flash_attn = disabled",
    "llama_kv_cache: size = 1.00 MiB (1 cells, 1 layers, 1/1 seqs), K (f16): 0.50 MiB, V (f16): 0.50 MiB",
    "common_init_from_params: quantized V cache requires flash_attn",
]


# --- V4 proof ---------------------------------------------------------------------------------------------------------


def test_prove_gpt_oss_two_iswa_lines_with_timestamp_prefix() -> None:
    ok, msg, applied = lt.evaluate_kv_lines("gpt-oss-120b", "q4_0", 2, GPT_OSS_JOURNAL)
    assert ok and applied == "q4_0"
    assert "2 llama_kv_cache line(s)" in msg and "flash_attn=enabled" in msg


def test_prove_deepseek_four_lines() -> None:
    ok, msg, applied = lt.evaluate_kv_lines("deepseek-v4-flash", "q4_0", 4, DEEPSEEK_JOURNAL)
    assert ok and applied == "q4_0" and "4 llama_kv_cache" in msg


def test_prove_nemotron_recurrent_line_is_not_a_failure() -> None:
    ok, msg, applied = lt.evaluate_kv_lines("nemotron-3-super", "q8_0", 1, NEMOTRON_JOURNAL)
    assert ok and applied == "q8_0"
    assert "recurrent R/S f32/f32" in msg


def test_prove_refused_line_fails_even_when_a_kv_line_exists() -> None:
    ok, msg, applied = lt.evaluate_kv_lines("qwen3.5-122b", "q8_0", 1, REFUSED_JOURNAL)
    assert not ok and applied == "f16"
    assert "refused" in msg and "quantized V cache requires flash_attn" in msg and "NOT proven" in msg


def test_prove_q8_requested_f16_applied_is_a_fallback_failure() -> None:
    lines = ["llama_context: flash_attn = enabled",
             "llama_kv_cache: size = 1.00 MiB (1 cells), K (f16): 0.50 MiB, V (f16): 0.50 MiB"]
    ok, msg, applied = lt.evaluate_kv_lines("meditron-70b", "q8_0", 1, lines)
    assert not ok and applied == "f16" and "q8_0 requested, NOT proven" in msg


def test_prove_mismatched_k_v_types() -> None:
    lines = ["llama_context: flash_attn = enabled",
             "llama_kv_cache: size = 1.00 MiB (1 cells), K (q8_0): 0.50 MiB, V (f16): 0.50 MiB"]
    ok, _msg, applied = lt.evaluate_kv_lines("x", "q8_0", 1, lines)
    assert not ok and applied == "q8_0/f16"


def test_prove_no_line_is_unproven_and_missing_flash_attn_fails() -> None:
    ok, msg, applied = lt.evaluate_kv_lines("x", "q8_0", 1, ["srv: nothing"])
    assert not ok and applied == "" and "V4 unproven" in msg
    ok, msg, _ = lt.evaluate_kv_lines("x", "q4_0", 2, GPT_OSS_JOURNAL[1:])
    assert not ok and "no flash_attn line" in msg


def test_prove_kv_retries_the_journal_read(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []
    answers = [[], [], GPT_OSS_JOURNAL]

    def fake_journal(_key: str, _inv: str, _since: str) -> list[str]:
        reads.append(1)
        return answers[len(reads) - 1]

    monkeypatch.setattr(lt, "journal_lines", fake_journal)
    monkeypatch.setattr(lt.time, "sleep", lambda _s: None)
    monkeypatch.setattr(lt, "http", lambda *_a, **_k: (_ for _ in ()).throw(lt.HttpFail("down")))
    ok, _msg, applied = lt.prove_kv("gpt-oss-120b", "q4_0", 2, "inv", "since", 8101)
    assert ok and applied == "q4_0" and len(reads) == 3


# --- V21 arithmetic ---------------------------------------------------------------------------------------------------


def _req(t_send: float, t_recv: float, timings: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"t_send": t_send, "t_recv": t_recv, "timings": timings or {}}


def test_queue_proof_timings_branch_pass_and_fail() -> None:
    a = _req(0.0, 10.0, {"prompt_ms": 1000, "predicted_ms": 8000})
    b = _req(0.5, 18.0, {"prompt_ms": 500, "predicted_ms": 7500})  # busy 8 s of 17.5 s wall: started at 10.0
    ok, proof = lt.queue_proof(a, b, 8.0)
    assert ok and "started +0.0s" in proof
    b2 = _req(0.5, 12.0, {"prompt_ms": 500, "predicted_ms": 7500})  # started at 4.0, before the first finished
    ok, proof = lt.queue_proof(a, b2, 8.0)
    assert not ok and "-6.0s" in proof


def test_queue_proof_wall_clock_branch() -> None:
    a = _req(0.0, 10.0)
    ok, proof = lt.queue_proof(a, _req(0.5, 18.0), 8.0)
    assert ok and proof.startswith("wall-clock")
    ok, _ = lt.queue_proof(a, _req(0.5, 12.0), 8.0)
    assert not ok


# --- V10 verdicts -----------------------------------------------------------------------------------------------------


def _good(key: str = "gpt-oss-120b") -> Any:
    return lt.EngineResult(key=key, load_ok=True, generated=True, released=True, decode_tps_512=40.0,
                           decode_tps_8k=35.0, prefill_tps_512=800.0, prefill_tps_8k=700.0, swap_s=30.0,
                           kv_applied="q4_0", control_mode="orchestrator", footprint_bytes=70 * lt.GB)


SPEC = {"expected_decode_tok_s": [30, 55], "arbiter_class": "core"}


def test_v10_line_pass_carries_footprint_and_control() -> None:
    result, msg = lt.v10_line(_good(), SPEC)
    assert result == "pass" and "footprint 70.0 GB" in msg and "control orchestrator" in msg


def test_v10_line_crash_at_8k_is_a_fail_with_the_s6_issue_number() -> None:
    res = _good("qwen3.5-122b")
    res.crashed_at_8k = True
    res.load_error = "8k prefill crashed the server (died during the prefill)"
    assert lt.v10_line(res, SPEC)[0] == "fail"
    # Section 23 S6 / engines.json known_issue: the alive check ran, the server was dead -> V10 fail carrying #20732.
    res = _good("nemotron-3-super")
    res.crashed_at_8k = True
    res.released = True
    res.known_issue_note = f"8k prefill crashed the server: {lt.NEMOTRON_ISSUE}; Section 23 S6: alive check: dead"
    res.warn(res.known_issue_note)
    result, msg = lt.v10_line(res, SPEC)
    assert result == "fail" and "#20732" in msg and "warn:" in msg
    # Alive after a failed 8k request is a fail too (V10 wants generation at 8k), and alive + measured is a pass.
    res = _good("nemotron-3-super")
    res.load_error = "8k prefill request failed: HTTP 500"
    assert lt.v10_line(res, SPEC)[0] == "fail"
    res = _good("nemotron-3-super")
    res.prefill_8k_note = f"{lt.NEMOTRON_ISSUE_ID} checked, server alive"
    result, msg = lt.v10_line(res, SPEC)
    assert result == "pass" and "#20732 checked, server alive" in msg


def test_run_timeout_is_a_recorded_failure_not_a_traceback() -> None:
    p = lt.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
    assert p.returncode == 124 and "timed out" in p.stderr


def test_admin_header_goes_to_arbiter_routes_only() -> None:
    ctl = lt.Control.__new__(lt.Control)
    ctl.headers = {lt.ADMIN_TOKEN_HEADER: "secret"}
    assert ctl._hdr("/arbiter/load") == ctl.headers
    assert ctl._hdr("/internal/deep-think/plan") == {} and ctl._hdr("/internal/v1/chat/completions") == {}


def test_v10_line_external_stop_is_a_fail_not_a_crash() -> None:
    res = _good()
    res.stopped_externally = True
    res.load_error = "server stopped externally during the 8k prefill"
    assert lt.v10_line(res, SPEC)[0] == "fail"
    assert lt.stopped_externally("... Stopping llama-server@x.service...") and not lt.stopped_externally("boom")


def test_check_band_verdicts() -> None:
    # Section 21 V10 "within the expected bands": anything under the lower bound is a fail, no tolerance factor.
    res = _good()
    res.decode_tps_512 = 20.0
    lt.check_band(res, SPEC)
    assert res.band_fail and lt.v10_line(res, SPEC)[0] == "fail"
    res = _good()
    res.decode_tps_512 = 29.9
    lt.check_band(res, SPEC)
    assert res.band_fail and "below the expected band 30-55" in res.band_fail and lt.v10_line(res, SPEC)[0] == "fail"
    res = _good()
    res.decode_tps_512 = 30.0
    lt.check_band(res, SPEC)
    assert not res.band_fail and "within" in res.band_note and lt.v10_line(res, SPEC)[0] == "pass"
    res = _good()
    res.decode_tps_512 = 60.0
    lt.check_band(res, SPEC)
    assert not res.band_fail and "above" in res.band_note and not res.warnings


def test_v10_line_load_failed_and_not_released() -> None:
    res = lt.EngineResult(key="x", load_error="Arbiter refused to load x: budget", control_mode="orchestrator")
    assert lt.v10_line(res, SPEC) == (
        "fail", "x: load FAILED (Arbiter refused to load x: budget) [control orchestrator]")
    res = _good()
    res.released = False
    assert lt.v10_line(res, SPEC)[0] == "fail"


# --- summarize --------------------------------------------------------------------------------------------------------


def _ctx(tmp_path: Path, engines: list[dict[str, Any]]) -> Any:
    ep = tmp_path / "engines.json"
    ep.write_text(json.dumps({"engines": engines}), encoding="utf-8")
    ctx = lt.Ctx(engines_path=ep, env_dir=tmp_path / "env", results_dir=tmp_path / "results",
                 orch_url="http://127.0.0.1:8800", engine_env=tmp_path / "engine-env.py", models_dir=tmp_path,
                 slots_dir=tmp_path, port_base=8100, overrides=tmp_path / "overrides.json")
    ctx.load_engines()
    return ctx


ENGINES = [
    {"key": "gpt-oss-120b", "arbiter_class": "core", "expected_decode_tok_s": [30, 55], "ctx_size": 262144},
    {"key": "deepseek-v4-flash", "arbiter_class": "apex", "kv_ladder": ["f16", "q8_0", "q4_0"], "kv_class": "q4_0",
     "ctx_size_f16_cap": 16384, "ctx_size": 32768},
    {"key": "qwen2.5-vl-72b", "arbiter_class": "vision", "expected_decode_tok_s": [2, 4], "ctx_size": 262144},
    {"key": "router-qwen3.5-4b", "arbiter_class": "resident"},
]


def _records(capsys: pytest.CaptureFixture[str]) -> list[tuple[str, str, str]]:
    out = []
    for line in capsys.readouterr().out.splitlines():
        tag, vid, result, msg = line.split("\t", 3)
        assert tag == "RECORD"
        out.append((vid, result, msg))
    return out


def test_summarize_missing_apex_is_deferred_and_not_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    _good().save(ctx)
    lt.EngineResult(key="qwen2.5-vl-72b", load_ok=True, generated=True, released=True, decode_tps_512=3.0).save(ctx)
    lt.cmd_summarize(ctx, None)
    recs = _records(capsys)
    assert ("V22", "deferred", "deepseek-v4-flash: no ladder result recorded (R19)") in recs
    assert any(v == "V10" and r == "deferred" and "Apex" in m for v, r, m in recs)
    summary = [m for v, r, m in recs if v == "V10" and m.startswith("summary")]
    assert summary == [
        "summary: all 2 engines load, generate, swap and release memory at their fixed quantisations, within the "
        "expected bands; warns: none; apex: deepseek-v4-flash=deferred"]


def test_summarize_counts_missing_and_off_band(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    res = _good()
    res.decode_tps_512 = 20.0
    lt.check_band(res, ctx.spec("gpt-oss-120b"))
    res.save(ctx)
    lt.cmd_summarize(ctx, None)
    recs = _records(capsys)
    summary = [m for v, r, m in recs if v == "V10" and m.startswith("summary")]
    assert len(summary) == 1 and summary[0].startswith("summary: 2 of 2 engines failed (gpt-oss-120b, qwen2.5-vl-72b)")
    assert "off-band: gpt-oss-120b" in summary[0]
    assert [r for v, r, _ in recs if v == "V10"].count("fail") == 3


# --- the ladder decision (engines.json kv_ladder_rule: descent f16 -> q8_0 -> q4_0, lowest coherent rung reached) ----

LADDER = ["f16", "q8_0", "q4_0"]


def _rung(kv: str, load_ok: bool, coherent: bool, proof: bool = True, **extra: Any) -> dict[str, Any]:
    return {"kv": kv, "load_ok": load_ok, "coherent": coherent, "kv_proof_ok": proof and load_ok, **extra}


def test_ladder_q4_win_keeps_baseline() -> None:
    rungs = [_rung("f16", True, True), _rung("q8_0", True, True), _rung("q4_0", True, True)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "q4_0", 16384, "", True, "", 28.0, 500.0)
    assert v.winner == "q4_0" and v.v4_result == "pass" and v.clear_ov == ["kv_type", "ctx_size"] and not v.set_ov
    assert v.v22_result == "pass" and "not tried" not in v.summary and not v.deviation
    assert "Section 4.3 target reached" in v.v4_msg


def test_ladder_q8_win_names_the_incoherent_baseline() -> None:
    rungs = [_rung("f16", True, True), _rung("q8_0", True, True), _rung("q4_0", True, False)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "q4_0", 16384, "", True, "", 28.0, 500.0)
    assert v.winner == "q8_0" and v.v4_result == "pass"
    assert "q8_0 applied (Section 4.3 setting q4_0 incoherent: q4_0=incoherent" in v.v4_msg
    assert v.set_ov == [("kv_type", "q8_0")] and v.clear_ov == ["ctx_size"] and v.deviation


def test_ladder_f16_win_defers_v4_and_passes_v22_with_cap() -> None:
    rungs = [_rung("f16", True, True), _rung("q8_0", False, False)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "q4_0", 16384, "", True, "", 20.0, 400.0)
    assert v.winner == "f16" and v.v4_result == "deferred" and "unquantised KV" in v.v4_msg
    assert v.v22_result == "pass" and "unquantised KV" in v.v22_msg
    assert ("ctx_size", "16384") in v.set_ov and ("kv_type", "f16") in v.set_ov
    assert "q8_0=load failed" in v.summary and "q4_0=not tried" in v.summary


def test_ladder_no_winner_defers_both_and_lowest_coherent_wins() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, False)], "q4_0", 16384, "", True, "", None,
                         None)
    assert v.winner == "" and v.v4_result == "deferred" and v.v22_result == "deferred"
    assert v.clear_ov == ["kv_type", "ctx_size"]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, True), _rung("q8_0", True, True)],
                         "q4_0", 16384, "", True, "", 1.0, 1.0)
    assert v.winner == "q8_0"  # the LAST coherent rung (lowest reached), never f16 while a quantised rung is coherent
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, True), _rung("q8_0", True, True),
                                                       _rung("q4_0", True, True)], "q4_0", 16384, "", False,
                         "GTT still 200 GB", 1.0, 1.0)
    assert v.winner == "q4_0" and v.v22_result == "deferred" and "GTT still 200 GB" in v.v22_msg


def test_ladder_measurement_failure_keeps_the_rung_and_defers_v22() -> None:
    # A coherent, proven rung whose measurement failed is still the winner (not incoherence); V22 is deferred (R19).
    rungs = [_rung("f16", True, True), _rung("q8_0", True, True, measure_error="8k prefill crashed the server")]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "q4_0", 16384, "", True, "", 25.0, 450.0)
    assert v.winner == "q8_0" and v.v4_result == "pass" and v.v22_result == "deferred"
    assert "measurement failed at q8_0: 8k prefill crashed" in v.v22_msg
    assert "q8_0=coherent (measurement failed)" in v.summary


class FakeControl:
    def __init__(self, fail_loads: set[str]) -> None:
        self.mode = "orchestrator"
        self.fail_loads = fail_loads
        self.loads: list[str] = []
        self.registered: list[int] = []

    def load(self, key: str, _ctx: int, _par: int, _port: int) -> float:
        kv = key.split(":")[-1]
        self.loads.append(kv)
        if kv in self.fail_loads:
            raise RuntimeError("Arbiter error for x: start failed: llama_init_from_model: failed to create context")
        return 12.0

    def resident_keys(self) -> list[str]:
        return []

    def register(self, _key: str, total: int) -> str:
        self.registered.append(total)
        return ""


def _ladder_harness(ctx: Any, ctl: FakeControl, monkeypatch: pytest.MonkeyPatch, measure: Any,
                    ) -> tuple[list[Any], dict[str, str]]:
    rendered: list[tuple[list[tuple[str, str]], list[str]]] = []
    current = {"kv": ""}

    def fake_render(_key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((list(set_ov), list(clear_ov)))
        for fld, val in set_ov:
            if fld == "kv_type":
                current["kv"] = val
        return {"ATLAS_MODEL_PRESENT": "1", "LLAMA_ARG_PORT": "8105", "ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "1",
                "ATLAS_KV_TYPE": current["kv"]}

    monkeypatch.setattr(ctx, "render", fake_render)
    # Control.load sees the key; the fake reads the rung through the rendered kv type instead.
    monkeypatch.setattr(ctl, "load", lambda _k, c, p, port: FakeControl.load(ctl, f"x:{current['kv']}", c, p, port))
    monkeypatch.setattr(lt, "unit_active", lambda _k: False)
    monkeypatch.setattr(lt, "unit_invocation_id", lambda _k: "inv")
    monkeypatch.setattr(lt, "prove_kv", lambda key, kv, *_a: (True, f"{key}: {kv} applied", kv))
    monkeypatch.setattr(lt, "coherence_check", lambda _port, _model: (True, "fine"))
    monkeypatch.setattr(lt, "swap_out", lambda *_a: (0.0, "no previous engine"))
    monkeypatch.setattr(lt, "release_or_poll", lambda *_a: (True, 5.0, "GTT back"))
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 160 * lt.GB)
    monkeypatch.setattr(lt, "run_measurements", measure)
    monkeypatch.setattr(lt, "require_generation_idle", lambda *_a: None)
    return rendered, current


def test_ladder_engine_descends_and_keeps_the_rung_above_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"q4_0"})

    def fake_measure(_ctx: Any, res: Any, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0

    rendered, _ = _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0", "q4_0"]  # f16 and q8_0 coherent -> descended; q4_0 failed -> q8_0 stands
    assert res.kv_applied == "q8_0" and res.ok and res.v22_result == "pass"
    assert [r["kv"] for r in res.ladder] == ["f16", "q8_0", "q4_0"] and res.ladder[2]["note"].startswith("load failed")
    assert rendered[0] == ([("kv_type", "f16"), ("ctx_size", "16384")], ["coresident"])
    assert rendered[1] == ([("kv_type", "q8_0")], ["coresident", "ctx_size"])
    assert rendered[-1] == ([("kv_type", "q8_0")], ["ctx_size"])
    assert res.footprint_bytes == 150 * lt.GB and ctl.registered == [150 * lt.GB, 150 * lt.GB]
    recs = _records(capsys)
    assert recs[-1][0] == "V4" and recs[-1][1] == "pass"
    assert "Section 4.3 setting q4_0 load failed: q4_0=load failed" in recs[-1][2]
    saved = json.loads(ctx.result_path("deepseek-v4-flash").read_text())
    assert saved["control_mode"] == "orchestrator" and saved["baseline_deviation"].startswith("KV q8_0")
    assert not lt.pending_path(ctx).exists()  # the journal of the temporary rung overrides is closed


def test_ladder_engine_tries_every_rung_so_q4_wins_below_a_failed_q8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    # Section 4.3: "a coherence prompt at each rung ... keeps the lowest coherent setting": a q8_0 that fails to load
    # (its KV at 32768 is about the f16 rung's bytes at the 16384 cap) must not hide a q4_0 that fits.
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"q8_0"})

    def fake_measure(_ctx: Any, res: Any, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0

    _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0", "q4_0"]
    assert res.kv_applied == "q4_0" and res.ok and res.v22_result == "pass" and not res.baseline_deviation
    assert res.ladder[1]["note"].startswith("load failed") and res.ladder[2]["coherent"]
    recs = _records(capsys)
    assert recs[-1][1] == "pass" and "Section 4.3 target reached" in recs[-1][2]
    assert "rungs above the winner failed (q8_0=load failed)" in recs[-1][2]


def test_ladder_engine_measurement_failure_does_not_stop_the_descent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads=set())

    def fake_measure(_ctx: Any, res: Any, _key: str, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0
        if res.ladder[-1]["kv"] == "q8_0":
            res.crashed_at_8k, res.load_error = True, "8k prefill crashed the server (died during the prefill)"

    _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0", "q4_0"]  # q8_0's measurement failed; q4_0 was still tried and wins
    assert res.kv_applied == "q4_0" and res.v22_result == "pass" and res.ok
    assert ctl.registered == [150 * lt.GB, 150 * lt.GB]  # f16 and q4_0 registered, the dead q8_0 server never (rule 1)
    assert _records(capsys)[-1][1] == "pass"


def test_ladder_engine_winner_with_failed_measurement_defers_v22(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"q4_0"})

    def fake_measure(_ctx: Any, res: Any, _key: str, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0
        if res.ladder[-1]["kv"] == "q8_0":
            res.crashed_at_8k, res.load_error = True, "8k prefill crashed the server (died during the prefill)"

    _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0", "q4_0"]
    assert res.kv_applied == "q8_0" and res.v22_result == "deferred" and not res.ok
    assert "measurement failed at q8_0" in res.v22_msg and ctl.registered == [150 * lt.GB]  # only f16 was registered
    assert _records(capsys)[-1][1] == "pass"


def test_pending_overrides_journal_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    (tmp_path / "overrides.json").write_text(json.dumps({"nemotron-3-super": {"ctx_size": 131072}}))
    rendered: list[tuple[str, list[tuple[str, str]], list[str]]] = []

    def fake_render(key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((key, list(set_ov), list(clear_ov)))
        return {"ATLAS_CTX_SIZE": "x"}

    monkeypatch.setattr(ctx, "render", fake_render)
    e1 = lt.pending_begin(ctx, "nemotron-3-super", ["ctx_size"], "probe")
    # clear_always: a field journalled as "clear" whatever overrides.json holds now (never a persistent override).
    (tmp_path / "overrides.json").write_text(json.dumps({"nemotron-3-super": {"ctx_size": 131072},
                                                         "deepseek-v4-flash": {"n_keep": 64}}))
    e2 = lt.pending_begin(ctx, "deepseek-v4-flash", ["n_keep"], "probe2", clear_always=["n_keep"])
    assert e1["set"] == {"ctx_size": 131072} and e1["clear"] == [] and e2["set"] == {} and e2["clear"] == ["n_keep"]
    assert len(lt.pending_read(ctx)) == 2
    lt.pending_recover(ctx)  # an interrupted run: both restored, newest first, journal gone
    assert rendered == [("deepseek-v4-flash", [], ["n_keep"]), ("nemotron-3-super", [("ctx_size", "131072")], [])]
    assert not lt.pending_path(ctx).exists() and lt.pending_read(ctx) == []
    e3 = lt.pending_begin(ctx, "nemotron-3-super", ["ctx_size"], "probe")
    lt.pending_end(ctx, e3)
    assert not lt.pending_path(ctx).exists()


# --- prompts and coherence --------------------------------------------------------------------------------------------


def test_make_prompt_sizes_by_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lt, "tokenize_count", lambda _port, text: len(text.split()) * 2)
    prompt, n = lt.make_prompt(8101, 8192, "nonce-1")
    assert abs(n - 8192) <= 8192 // 50
    assert prompt.startswith("nonce-1 ") and prompt.endswith("Continue this list of words:")
    _, n = lt.make_prompt(8101, 512, "nonce-2")
    assert abs(n - 512) <= 10


def test_text_quality_heuristics() -> None:
    good = ("Water evaporates from the ocean, rises as vapour, condenses into clouds and falls as rain; rivers and "
            "groundwater carry it back while ice stores some for centuries, moving heat around the planet as it goes "
            "and shaping storms and regional climates through latent energy.")
    assert lt.text_quality(good)[0]
    assert "too short" in lt.text_quality("Paris.")[1]
    assert "repetitive" in lt.text_quality(" ".join(["water rain"] * 40))[1]
    nato = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec "
            "romeo sierra tango uniform victor whiskey xray yankee zulu")
    assert "off-topic" in lt.text_quality(nato)[1]
    assert "mojibake" in lt.text_quality("é" * 50 + " " + good)[1]


def test_coherence_check_retries_an_exhausted_thinking_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    summary = ("Water evaporates from the ocean, rises as vapour, condenses into clouds and falls as rain; rivers "
               "and groundwater carry it back while ice stores some for centuries, moving heat around the planet.")

    def fake_chat(_url: str, _model: str, messages: list[dict[str, str]], max_tokens: int, **_k: Any) -> dict[str, Any]:
        calls.append(max_tokens)
        if "capital" in messages[0]["content"]:
            if max_tokens == lt.COHERENCE_TOKENS_FACTUAL:
                return {"content": "", "reasoning": "thinking about France...", "finish": "length", "timings": {},
                        "error": "", "t_send": 0.0, "t_recv": 1.0, "code": 200}
            return {"content": "Paris", "reasoning": "", "finish": "stop", "timings": {}, "error": "",
                    "t_send": 0.0, "t_recv": 1.0, "code": 200}
        return {"content": summary, "reasoning": "", "finish": "stop", "timings": {}, "error": "", "t_send": 0.0,
                "t_recv": 1.0, "code": 200}

    monkeypatch.setattr(lt, "chat", fake_chat)
    ok, detail = lt.coherence_check(8105, "deepseek-v4-flash")
    assert ok and "thinking budget" in detail and "retried at 4096" in detail
    assert calls == [1024, 4096, 2048]


def test_coherence_check_empty_answer_after_retry_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"content": "", "reasoning": "still thinking", "finish": "length", "timings": {}, "error": "",
                "t_send": 0.0, "t_recv": 1.0, "code": 200}

    monkeypatch.setattr(lt, "chat", fake_chat)
    ok, detail = lt.coherence_check(8105, "deepseek-v4-flash")
    assert not ok and "produced no answer" in detail and "finish=length" in detail


# --- guards -----------------------------------------------------------------------------------------------------------


def test_orch_url_must_be_loopback() -> None:
    lt.require_loopback("http://127.0.0.1:8800")
    lt.require_loopback("http://localhost:8800/")
    with pytest.raises(lt.Infra):
        lt.require_loopback("http://192.168.1.10:8800")


def test_drm_root_hook_refused_on_the_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ATLAS_DRM_ROOT", str(tmp_path))
    monkeypatch.setenv("ATLAS_ETC", "/etc/atlas")
    with pytest.raises(lt.Infra):
        lt.drm_root()
    monkeypatch.setenv("ATLAS_ETC", "/etc//atlas/")  # canonical compare: still the node
    with pytest.raises(lt.Infra):
        lt.drm_root()
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    assert lt.drm_root() == tmp_path
    monkeypatch.delenv("ATLAS_DRM_ROOT")
    assert lt.drm_root() == Path("/sys/class/drm")


def test_read_admin_token(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("# comment\nORCH_ADMIN_TOKEN='abc123'\n")
    assert lt.read_admin_token(str(f)) == "abc123"
    f.write_text("bare-token\n")
    assert lt.read_admin_token(str(f)) == "bare-token"
    assert lt.read_admin_token(None) == ""
    with pytest.raises(lt.Infra):
        lt.read_admin_token(str(tmp_path / "missing"))


def test_record_line_protocol(capsys: pytest.CaptureFixture[str]) -> None:
    lt.record("V21", "pass", "two\tlines\nof text")
    assert capsys.readouterr().out == "RECORD\tV21\tpass\ttwo lines of text\n"


# --- fix round: residency window, cumulative release, rule-3 belt, queued retry --------------------------------------


def test_residency_bounds_are_capped_at_the_budget() -> None:
    weights = 142 * lt.GB
    low, high = lt.residency_bounds(weights, 170 * lt.GB)
    assert low == int(0.85 * weights) and high == 170 * lt.GB  # 142 + 28 + 10 = 180 would exceed the budget
    _, high2 = lt.residency_bounds(weights, 200 * lt.GB)
    assert high2 == weights + 28 * lt.GB + 10 * lt.GB
    _, high3 = lt.residency_bounds(weights, 0)
    assert high3 == 0  # a zero budget means nothing can pass; the caller falls back to DEFAULT_BUDGET_BYTES before


class _UnloadControl:
    def __init__(self, refuse: set[str] = frozenset()) -> None:
        self.unloaded: list[str] = []
        self.refuse = refuse

    def unload(self, key: str) -> str:
        if key in self.refuse:
            raise RuntimeError(f"Arbiter did not unload {key}: refused: generating")
        self.unloaded.append(key)
        return f"{key} released"


def _fast_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lt.time, "sleep", lambda _s: None)


def test_clear_residents_polls_once_after_the_last_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_clock(monkeypatch)
    ctl = _UnloadControl()
    counter = {"v": 160 * lt.GB}
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: counter["v"])

    def unload(key: str) -> str:
        r = _UnloadControl.unload(ctl, key)
        counter["v"] -= 70 * lt.GB  # each unload returns its share; only after the LAST is the baseline reached
        return r

    monkeypatch.setattr(ctl, "unload", unload)
    ok, _, note, settled = lt.clear_residents(ctl, ["gpt-oss-120b", "qwen2.5-vl-72b"], 20 * lt.GB, "prepare")
    assert ok and ctl.unloaded == ["gpt-oss-120b", "qwen2.5-vl-72b"] and settled == 20 * lt.GB
    assert "gpt-oss-120b+qwen2.5-vl-72b" in note and "Arbiter: gpt-oss-120b: gpt-oss-120b released" in note


def test_clear_residents_stale_baseline_is_not_a_leak_when_the_arbiter_granted(monkeypatch: pytest.MonkeyPatch,
                                                                             ) -> None:
    _fast_clock(monkeypatch)
    ctl = _UnloadControl()
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 25 * lt.GB)  # 5 GB above the earlier run's baseline
    monkeypatch.setattr(lt, "RELEASE_TIMEOUT_S", 0.0)
    ok, _, note, settled = lt.clear_residents(ctl, ["gpt-oss-120b"], 20 * lt.GB, "coresident")
    assert not ok and settled == 25 * lt.GB and "stale baseline 20.0 GB vs settled 25.0 GB" in note
    assert "Arbiter release check passed" in note


def test_clear_residents_refusal_is_infra_and_empty_set_just_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_clock(monkeypatch)
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 17 * lt.GB)
    with pytest.raises(lt.Infra, match="cannot clear the resident engines"):
        lt.clear_residents(_UnloadControl(refuse={"meditron-70b"}), ["meditron-70b"], 17 * lt.GB, "prepare")
    ok, secs, note, settled = lt.clear_residents(_UnloadControl(), [], None, "prepare")
    assert ok and secs == 0.0 and note == "nothing was resident" and settled == 17 * lt.GB
    ok, _, note, _ = lt.clear_residents(_UnloadControl(), ["meditron-70b"], None, "prepare")
    assert ok and "no earlier baseline" in note


def test_require_generation_idle_waits_then_passes_or_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_clock(monkeypatch)
    answers = iter([{"generating": ["gpt-oss-120b", "t1"], "generation_queue": []},
                    {"generating": None, "generation_queue": ["t2"]},
                    {"generating": None, "generation_queue": []}])
    monkeypatch.setattr(lt, "http", lambda *_a, **_k: (200, next(answers)))
    lt.require_generation_idle("http://127.0.0.1:8800/", "x")  # third answer is idle: returns
    monkeypatch.setattr(lt, "GENERATION_IDLE_WAIT_S", 0.0)
    monkeypatch.setattr(lt, "http", lambda *_a, **_k: (200, {"generating": ["qwen3.5-122b", "t9"],
                                                             "generation_queue": []}))
    with pytest.raises(lt.Infra, match="still generating"):
        lt.require_generation_idle("http://127.0.0.1:8800", "x")
    monkeypatch.setattr(lt, "http", lambda *_a, **_k: (503, {"status": "starting"}))
    with pytest.raises(lt.Infra, match="answered 503"):
        lt.require_generation_idle("http://127.0.0.1:8800", "x")


def test_control_load_queued_retries_the_arbiter_not_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(lt.time, "sleep", lambda s: slept.append(s))
    ctl = lt.Control.__new__(lt.Control)
    ctl.orch_url, ctl.headers, ctl.last_decision = "http://127.0.0.1:8800", {}, {}
    answers = iter([(200, {"decision": "queued", "reason": "arbiter busy: loading meditron-70b"}),
                    (200, {"decision": "queued", "reason": "generation in progress on gpt-oss-120b (task t)"}),
                    (200, {"decision": "granted", "reason": "fits", "projected_bytes": 70 * lt.GB})])
    monkeypatch.setattr(ctl, "post", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(ctl, "_wait_resident", lambda *_a: pytest.fail("the ledger is not watched for a busy queue"))
    monkeypatch.setattr(lt, "health_code", lambda _p: 200)
    assert ctl.load("qwen3.5-122b", 262144, 8, 8104) >= 0
    assert slept == [lt.QUEUED_RETRY_S, lt.QUEUED_RETRY_S]
    # "already in progress" (another caller loading this engine) IS a ledger wait.
    answers = iter([(200, {"decision": "queued", "reason": "load of qwen3.5-122b already in progress"})])
    monkeypatch.setattr(ctl, "post", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(ctl, "_wait_resident", lambda *_a: True)
    assert ctl.load("qwen3.5-122b", 262144, 8, 8104) >= 0


def test_test_engine_renders_the_unit_as_production_runs_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    """S52: step 2 measures the vision engine at the profile production runs (its standing co-resident one, or the
    Principal's overrides.json coresident=false): it neither sets nor clears `coresident` any more."""
    ctx = _ctx(tmp_path, ENGINES)
    ctx.results_dir.mkdir(parents=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({"gtt_used_bytes": 10 * lt.GB}))
    rendered: list[tuple[str, list[Any], list[Any]]] = []

    def fake_render(key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((key, list(set_ov), list(clear_ov)))
        return {"ATLAS_MODEL_PRESENT": "0", "ATLAS_MODEL_FILE": "x.gguf", "ATLAS_KV_TYPE": "q8_0"}

    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: FakeControl(set()))
    monkeypatch.setattr(ctx, "render", fake_render)
    monkeypatch.setattr(lt, "swap_out", lambda *_a: (0.0, "released"))
    lt.test_engine(ctx, "qwen2.5-vl-72b", None)
    assert rendered == [("qwen2.5-vl-72b", [], [])]
    assert _records(capsys)[0][:2] == ("V4", "fail")


VISION_CO = {"key": "qwen2.5-vl-72b", "arbiter_class": "vision", "ctx_size": 262144, "parallel": 8,
             "ctx_size_coresident": 65536, "parallel_coresident": 2, "footprint_gb": 79}


class _CoControl:
    mode = "orchestrator"

    def resident_keys(self) -> list[str]:
        return []

    def status(self) -> dict[str, Any]:
        return {"budget_bytes": 164 * lt.GB}


def _coresident_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vision_env: dict[str, str],
                        ) -> tuple[Any, list[tuple[str, list[Any], list[Any]]]]:
    engines = [{**ENGINES[0], "footprint_gb": 63}, VISION_CO]
    ctx = _ctx(tmp_path, engines)
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({"gtt_used_bytes": 10 * lt.GB}))
    rendered: list[tuple[str, list[Any], list[Any]]] = []

    def fake_render(key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((key, list(set_ov), list(clear_ov)))
        base = {"ATLAS_MODEL_PRESENT": "0", "ATLAS_MODEL_FILE": f"{key}.gguf", "LLAMA_ARG_PORT": "8101"}
        return {**base, **vision_env} if key == "qwen2.5-vl-72b" else base

    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: _CoControl())
    monkeypatch.setattr(ctx, "render", fake_render)
    monkeypatch.setattr(lt, "unit_active", lambda _k: False)
    monkeypatch.setattr(lt, "clear_residents", lambda _c, _r, base, _w: (True, 0.0, "nothing resident", base))
    return ctx, rendered


def test_coresident_step_renders_the_standing_profile_without_a_temporary_override(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """S52: step 3 renders both units exactly as production runs them (no set, no clear, nothing journalled, nothing
    restored at the end), and still asserts ATLAS_CORESIDENT=1 at engines.json's 2-slot values before any load."""
    import argparse

    good = {"ATLAS_CORESIDENT": "1", "ATLAS_PARALLEL": "2", "ATLAS_CTX_SIZE": "65536"}
    ctx, rendered = _coresident_harness(tmp_path, monkeypatch, good)
    args = argparse.Namespace(text="gpt-oss-120b", vision="qwen2.5-vl-72b")
    assert lt.cmd_coresident(ctx, args) == 0  # the model files are absent here: V21 recorded as a fail, no load
    assert rendered == [("gpt-oss-120b", [], []), ("qwen2.5-vl-72b", [], [])]
    assert not lt.pending_path(ctx).exists() and not ctx.overrides.exists()
    assert [r[:2] for r in _records(capsys)] == [("V21", "fail"), ("V14b", "fail")]
    # The Principal's coresident=false: the pair cannot form at the full profile and Phase 3 does not override it.
    ctx.overrides.write_text(json.dumps({"qwen2.5-vl-72b": {"coresident": False}}))
    ctx, rendered = _coresident_harness(tmp_path, monkeypatch, {**good, "ATLAS_CORESIDENT": "0",
                                                                "ATLAS_PARALLEL": "8", "ATLAS_CTX_SIZE": "262144"})
    principal = r"coresident=false for qwen2\.5-vl-72b.*--clear-override qwen2\.5-vl-72b coresident"
    with pytest.raises(lt.Infra, match=principal):
        lt.cmd_coresident(ctx, args)
    assert json.loads(ctx.overrides.read_text()) == {"qwen2.5-vl-72b": {"coresident": False}}  # untouched
    # No override, yet not rendered co-resident (an engine-env.py without the S52 default): still refused.
    ctx.overrides.unlink()
    ctx, _ = _coresident_harness(tmp_path, monkeypatch, {**good, "ATLAS_CORESIDENT": "0"})
    with pytest.raises(lt.Infra, match=re.escape("did not render qwen2.5-vl-72b with ATLAS_CORESIDENT=1")):
        lt.cmd_coresident(ctx, args)
    # Co-resident flag but not engines.json's 2-slot values: refused before any load (the existing check).
    ctx, _ = _coresident_harness(tmp_path, monkeypatch, {**good, "ATLAS_PARALLEL": "8"})
    with pytest.raises(lt.Infra, match="parallel_coresident 2"):
        lt.cmd_coresident(ctx, args)
    assert _records(capsys) == []


def test_test_engine_releases_the_previous_engine_before_the_model_check(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctx.results_dir.mkdir(parents=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({"gtt_used_bytes": 10 * lt.GB}))
    swapped: list[str | None] = []
    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: FakeControl(set()))
    monkeypatch.setattr(ctx, "render", lambda *_a, **_k: {"ATLAS_MODEL_PRESENT": "0", "ATLAS_MODEL_FILE": "x.gguf",
                                                           "ATLAS_KV_TYPE": "q4_0"})
    monkeypatch.setattr(lt, "swap_out", lambda _c, _l, prev, _b: (swapped.append(prev), (0.0, "released"))[1])
    lt.test_engine(ctx, "gpt-oss-120b", "qwen2.5-vl-72b")
    assert swapped == ["qwen2.5-vl-72b"]  # the previous engine was released although this one never loaded
    saved = json.loads(ctx.result_path("gpt-oss-120b").read_text())
    assert saved["resident_after"] is False and not saved["load_ok"]
    assert _records(capsys) == [("V4", "fail", "gpt-oss-120b: not loaded (ATLAS_MODEL_PRESENT=0 in gpt-oss-120b.env: "
                                 "model file x.gguf not found (step 01))")]


# --- S52 Phase 3 items: context memory, host-RAM caches, context shift (records, never a verdict) --------------------
#
# Fixture lines are produced from the format strings of llama.cpp b29c606 (the pinned build), copied verbatim below
# with their file and line, through common_log's prefix (common/log.cpp:141-154: "%d.%02d.%03d.%03d " then "I ", "W "
# or "D "). The sizes are invented for the fixtures except where a test says it re-derives one.

REAL_ENGINES = Path(__file__).resolve().parents[2] / "config" / "engines.json"
FMT_PREFIX = "%d.%02d.%03d.%03d "  # common/log.cpp:141 ("[M.s.ms.us]"), then "I ", "W " or "D " (log.cpp:151-154)
FMT_KV = ("%s: size = %7.2f MiB (%6u cells, %3d layers, %2u/%u seqs), K (%s): %7.2f MiB, V (%s): %7.2f MiB"
          )  # src/llama-kv-cache.cpp:301
FMT_ISWA_BASE = "%s: creating non-SWA KV cache, size = %u cells"  # src/llama-kv-cache-iswa.cpp:83
FMT_ISWA_SWA = "%s: creating     SWA KV cache, size = %u cells"  # src/llama-kv-cache-iswa.cpp:100
FMT_RS = ("%s: size = %7.2f MiB (%6u cells, %3d layers, %2u seqs %2u rs_seq), R (%s): %7.2f MiB, S (%s): %7.2f MiB, "
          "P (%s): %7.2f MiB")  # src/llama-memory-recurrent.cpp:133
FMT_DSV4_RAW = "%s: creating DSV4 raw KV cache"  # src/llama-kv-cache-dsv4.cpp:1251
FMT_DSV4_CSA = "%s: creating DSV4 CSA compressed KV cache, size = %u cells"  # src/llama-kv-cache-dsv4.cpp:1289
FMT_DSV4_HCA = "%s: creating DSV4 HCA compressed KV cache, size = %u cells"  # src/llama-kv-cache-dsv4.cpp:1297
FMT_DSV4_LID = "%s: creating DSV4 lightning-indexer KV cache, size = %u cells"  # src/llama-kv-cache-dsv4.cpp:1305
FMT_DSV4_STATE = ("%s: %s ratio = %u, state = %u x %u, streams = %u, rs_seq = %u, layers = %d, size = %7.2f MiB"
                  )  # src/llama-kv-cache-dsv4.cpp:998 (%zu in C for layers)
FMT_COMPUTE = "%s: %10s compute buffer size = %8.2f MiB"  # src/llama-context.cpp:692, tools/mtmd/clip.cpp:3773
FMT_FIT_END = "%s: fitting params to free memory took %.2f seconds"  # common/fit.cpp:902 (LOG_TRC: printed "I ")
FMT_SRV = "srv  %12.*s: "  # tools/server/server-common.h:34-36 (SRV_TRC/SRV_INF/SRV_WRN)
FMT_SLT = "slot %12.*s: id %2d | task %d | "  # tools/server/server-common.h:27-29
FMT_SLOTS = "initializing, n_slots = %d, n_ctx_slot = %d, kv_unified = '%s'"  # tools/server/server-context.cpp:1250
FMT_SHIFT_CTX = "ctx_shift is not supported by this context, it will be disabled"  # server-context.cpp:1188
FMT_SHIFT_MM = "ctx_shift is not supported by multimodal, it will be disabled"  # server-context.cpp:1176
FMT_PC_ROOM = " - making room for prompt cache entry, removing oldest entry (size = %.3f MiB)"  # server-task.cpp:1753
FMT_PC_LIMIT = " - cache size limit reached, removing oldest entry (size = %.3f MiB)"  # server-task.cpp:1873
FMT_PC_SKIP = " - prompt state size %.3f MiB exceeds cache size limit %.3f MiB, skipping"  # server-task.cpp:1732
FMT_PC_STATE = " - cache state: %d prompts, %.3f MiB (limits: %.3f MiB, %d tokens, %d est)"  # server-task.cpp:1894
FMT_CK_ERASE = ("erasing old context checkpoint (pos_min = %d, pos_max = %d, n_tokens = %d, size = %.3f MiB)"
                )  # server-context.cpp:2335 (PRId64 n_tokens)
FMT_CK_CREATE = ("created context checkpoint %d of %d (pos_min = %d, pos_max = %d, n_tokens = %d, size = %.3f MiB)"
                 )  # server-context.cpp:2369 (TRACE)


def _cl(level: str, msg: str, t_us: int = 3_123_456) -> str:
    """One journal line as common_log prints it with prefix and timestamps on (common/common.cpp:391-392)."""
    s = t_us // 1_000_000
    return FMT_PREFIX % (s // 60, s % 60, t_us // 1000 % 1000, t_us % 1000) + f"{level} {msg}"


def _kv(cells: int, layers: int, seqs: int, streams: int, typ: str, k_mib: float, v_mib: float) -> str:
    return _cl("I", FMT_KV % ("llama_kv_cache", k_mib + v_mib, cells, layers, seqs, streams, typ, k_mib, typ, v_mib))


def _srv(func: str, level: str, msg: str) -> str:
    return _cl(level, FMT_SRV % (12, func) + msg)


def _compute(func: str, buf: str, mib: float) -> str:
    return _cl("I", FMT_COMPUTE % (func, buf, mib))


# gpt-oss-120b at 262144 x 8, q4_0 (sizes re-derived: 18 full layers of 8 KV heads x 64 x 32768 cells x 8 streams at
# 18/32 B = 1296 MiB each for K and V; the window of 2304 cells gives 91.125 MiB each). The first line is --fit's trial
# context as -lv 5 or -v prints it ("I ": common/common.cpp:1326 keeps its INFO level there), then the fit-end line.
GPT_OSS_MEM = [
    _cl("I", FMT_KV % ("llama_kv_cache", 9999.0, 32768, 18, 8, 8, "q4_0", 4999.5, "q4_0", 4999.5)),  # --fit's trial
    _cl("I", FMT_FIT_END % ("common_fit_params", 0.42)),
    _cl("I", FMT_ISWA_BASE % ("llama_kv_cache_iswa", 32768)),
    _kv(32768, 18, 8, 8, "q4_0", 1296.0, 1296.0),
    _cl("I", FMT_ISWA_SWA % ("llama_kv_cache_iswa", 2304)),
    _kv(2304, 18, 8, 8, "q4_0", 91.125, 91.125),
    _cl("I", "llama_context: flash_attn            = enabled"),
    _compute("sched_reserve", "Vulkan0", 1200.25),
    _compute("sched_reserve", "Vulkan_Host", 40.0),
    _compute("sched_reserve", "Vulkan0", 1100.0),  # a later re-reservation: the largest stands
    _srv("load_model", "W", FMT_SHIFT_CTX),
    _srv("load_model", "I", FMT_SLOTS % (8, 32768, "false")),
    _srv("main", "I", "model loaded"),
    _srv("alloc", "W", FMT_PC_ROOM % 345.5),
    _srv("update", "W", FMT_PC_LIMIT % 512.25),
    _cl("W", FMT_SLT % (12, "create_checkpoint", 3, 41) + FMT_CK_ERASE % (0, 8191, 8192, 22.781)),
]
VISION_MEM = [
    _compute("reserve_compute_meta", "Vulkan0", 300.0),
    _kv(32768, 80, 2, 2, "q8_0", 5440.0, 5440.0),
    _compute("sched_reserve", "Vulkan0", 700.0),
    _srv("load_model", "W", FMT_SHIFT_MM),
    _srv("load_model", "I", FMT_SLOTS % (2, 32768, "false")),
]
NEMOTRON_MEM = [
    _kv(32768, 8, 8, 8, "q8_0", 768.0, 768.0),
    _cl("I", FMT_RS % ("llama_memory_recurrent", 2400.0, 8, 40, 8, 0, "f32", 400.0, "f32", 2000.0, "f32", 0.0)),
    _srv("load_model", "I", FMT_SLOTS % (8, 32768, "false")),
    _srv("update", "I", FMT_PC_STATE % (3, 1234.5, 8192.0, 0, 0)),
    _cl("I", FMT_SLT % (12, "create_checkpoint", 0, 7) + FMT_CK_CREATE % (4, 32, 0, 4095, 4096, 300.0)),
]
DEEPSEEK_MEM = [
    _cl("I", FMT_DSV4_RAW % "llama_kv_cache_dsv4"),
    _cl("I", FMT_ISWA_BASE % ("llama_kv_cache_iswa", 32768)),
    _kv(32768, 0, 1, 1, "q4_0", 0.0, 0.0),
    _cl("I", FMT_ISWA_SWA % ("llama_kv_cache_iswa", 768)),
    _kv(768, 43, 1, 1, "q4_0", 11.5, 0.0),
    _cl("I", FMT_DSV4_CSA % ("llama_kv_cache_dsv4", 8192)),
    _kv(8192, 21, 1, 1, "q4_0", 40.0, 0.0),
    _cl("I", FMT_DSV4_HCA % ("llama_kv_cache_dsv4", 256)),
    _kv(256, 20, 1, 1, "q4_0", 1.25, 0.0),
    _cl("I", FMT_DSV4_LID % ("llama_kv_cache_dsv4", 8192)),
    _kv(8192, 21, 1, 1, "q4_0", 10.0, 0.0),
    _cl("I", FMT_DSV4_STATE % ("llama_dsv4_comp_state", "csa", 4, 8, 1024, 1, 0, 21, 2.5)),
    _cl("I", FMT_DSV4_STATE % ("llama_dsv4_comp_state", "hca", 128, 256, 1024, 1, 0, 20, 1.5)),
    _srv("load_model", "W", FMT_SHIFT_CTX),
    _srv("load_model", "I", FMT_SLOTS % (1, 32768, "false")),
]
ROUTER_MEM = [
    _kv(8192, 8, 4, 4, "q8_0", 204.0, 204.0),
    _cl("I", FMT_RS % ("llama_memory_recurrent", 120.0, 4, 24, 4, 0, "f32", 20.0, "f32", 100.0, "f32", 0.0)),
    _srv("load_model", "W", FMT_SHIFT_CTX),
    _srv("load_model", "I", FMT_SLOTS % (4, 8192, "false")),
]
CHAT_ARGS = "--model /m --ctx-size 262144 --parallel 8 --keep 4096 --context-shift --slot-save-path /s"


def test_fixture_lines_have_the_pinned_shape() -> None:
    # The regexes are matched against the printf output, not against hand-typed lines: spot-check two renderings.
    assert GPT_OSS_MEM[3].endswith("I llama_kv_cache: size = 2592.00 MiB ( 32768 cells,  18 layers,  8/8 seqs), "
                                   "K (q4_0): 1296.00 MiB, V (q4_0): 1296.00 MiB")
    assert GPT_OSS_MEM[1] == "0.03.123.456 I common_fit_params: fitting params to free memory took 0.42 seconds"
    assert GPT_OSS_MEM[0].startswith("0.03.123.456 I llama_kv_cache: size = 9999.00 MiB") and GPT_OSS_MEM[10] == (
        "0.03.123.456 W srv    load_model: ctx_shift is not supported by this context, it will be disabled")


def test_parse_memory_lines_gpt_oss_iswa_compute_caches_and_shift() -> None:
    m = lt.parse_memory_lines(GPT_OSS_MEM)
    assert [p["part"] for p in m["kv_parts"]] == ["non-SWA", "SWA"] and m["kv_lines"] == 2  # the trial is discarded
    assert m["kv_mib"] == 2774.25 and m["swa_mib"] == 182.25 and m["context_mib"] == 2774.25
    assert m["recurrent_mib"] is None and m["dsv4_state_mib"] is None
    assert m["compute_mib"] == {"Vulkan0": 1200.25, "Vulkan_Host": 40.0} and m["mmproj_compute_mib"] == {}
    assert (m["n_slots"], m["n_ctx_slot"], m["kv_unified"]) == (8, 32768, "false")
    assert m["ctx_shift_off"] == "not supported by this context"
    assert m["prompt_cache"]["evicted"] == 2 and m["prompt_cache"]["evicted_max_mib"] == 512.25
    assert m["prompt_cache"]["state_max_mib"] is None  # TRACE, and this fixture has no cache-state line
    assert m["checkpoints"]["erased"] == 1 and m["checkpoints"]["size_max_mib"] == 22.781
    assert m["checkpoint_state_mib"] == round(182.25 / 8, 3)  # one slot's window cells (INFERRED)


def test_parse_memory_lines_vision_hybrid_dsv4() -> None:
    v = lt.parse_memory_lines(VISION_MEM)
    assert v["kv_mib"] == 10880.0 and [p["part"] for p in v["kv_parts"]] == ["full"]
    assert v["compute_mib"] == {"Vulkan0": 700.0} and v["mmproj_compute_mib"] == {"Vulkan0": 300.0}
    assert v["ctx_shift_off"] == "not supported by multimodal" and v["checkpoint_state_mib"] is None
    n = lt.parse_memory_lines(NEMOTRON_MEM)
    assert n["kv_mib"] == 1536.0 and n["recurrent_mib"] == 2400.0 and n["context_mib"] == 3936.0
    assert n["checkpoint_state_mib"] == 300.0 and not n["ctx_shift_off"]
    assert n["prompt_cache"]["state_max_mib"] == 1234.5 and n["prompt_cache"]["prompts_max"] == 3
    assert n["checkpoints"]["created_max"] == 4 and n["checkpoints"]["limit"] == 32
    d = lt.parse_memory_lines(DEEPSEEK_MEM)
    assert [p["part"] for p in d["kv_parts"]] == ["non-SWA", "SWA", "DSV4 CSA compressed", "DSV4 HCA compressed",
                                                  "DSV4 lightning-indexer"]
    assert d["kv_lines"] == 5 and d["kv_mib"] == 62.75 and d["dsv4_state_mib"] == 4.0 and d["context_mib"] == 66.75
    assert d["checkpoint_state_mib"] == 15.5  # SWA 11.5 + compressor states 4.0, one stream
    empty = lt.parse_memory_lines([])
    assert empty["kv_mib"] is None and empty["context_mib"] is None and empty["n_ctx_slot"] is None
    # An announcement names only the next size line: a later unannounced one is a full cache again.
    mixed = lt.parse_memory_lines([GPT_OSS_MEM[4], GPT_OSS_MEM[5], VISION_MEM[1]])
    assert [p["part"] for p in mixed["kv_parts"]] == ["SWA", "full"]


def test_parse_memory_lines_discards_fits_trial_context() -> None:
    """S52 fix round: at -lv 5 or -v --fit's trial context prints its iSWA, K/V and compute lines as "I " (never
    "D "); everything before the fit-end line (common/fit.cpp:902) is the trial's, so nothing counts twice. At the
    rendered -lv 4 the trial is not printed at all, and a "D " line is still skipped."""
    trial = [
        _cl("I", FMT_ISWA_BASE % ("llama_kv_cache_iswa", 32768)),
        _kv(32768, 18, 8, 8, "q4_0", 1296.0, 1296.0),
        _cl("I", FMT_ISWA_SWA % ("llama_kv_cache_iswa", 2304)),
        _kv(2304, 18, 8, 8, "q4_0", 91.125, 91.125),
        _cl("I", FMT_RS % ("llama_memory_recurrent", 77.0, 8, 4, 8, 0, "f32", 7.0, "f32", 70.0, "f32", 0.0)),
        _cl("I", FMT_DSV4_STATE % ("llama_dsv4_comp_state", "csa", 4, 8, 1024, 1, 0, 21, 9.5)),  # reset too
        _compute("sched_reserve", "Vulkan0", 5000.0),  # larger than the served context's: must not stand
        _cl("I", FMT_ISWA_BASE % ("llama_kv_cache_iswa", 32768)),  # a dangling announcement: must not tag
    ]
    served = GPT_OSS_MEM[2:]
    lv5 = [*trial, GPT_OSS_MEM[1], _cl("D", "llama_kv_cache: size = 1.00 MiB (1 cells, 1 layers, 1/1 seqs), "
                                            "K (q4_0): 0.50 MiB, V (q4_0): 0.50 MiB"), *served]
    m = lt.parse_memory_lines(lv5)
    assert m["kv_lines"] == 2 and m["kv_mib"] == 2774.25 and m["context_mib"] == 2774.25  # not 5548.5 + 77
    assert [p["part"] for p in m["kv_parts"]] == ["non-SWA", "SWA"] and m["recurrent_mib"] is None
    assert m["dsv4_state_mib"] is None  # the trial's compressor state is reset at the fit-end line too
    assert m["compute_mib"] == {"Vulkan0": 1200.25, "Vulkan_Host": 40.0}
    assert m["checkpoint_state_mib"] == round(182.25 / 8, 3)
    assert lt.parse_memory_lines(served) == m  # -lv 4: the served context's lines only, the same record
    # Lines after the fit-end line are all kept (the served context and the mmproj load after the fit), and the
    # trial's last announcement does not name the served context's first, unannounced, size line.
    after = lt.parse_memory_lines([trial[-1], GPT_OSS_MEM[1], *VISION_MEM])
    assert after["kv_mib"] == 10880.0 and after["mmproj_compute_mib"] == {"Vulkan0": 300.0}
    assert [p["part"] for p in after["kv_parts"]] == ["full"]


def _host(pid: int, rss_gib: float, anon_gib: float) -> dict[str, Any]:
    return {"pid": pid, "comm": "llama-server", "VmRSS": int(rss_gib * lt.GIB), "RssAnon": int(anon_gib * lt.GIB)}


def test_memory_record_context_shift_states_growth_and_unknowns() -> None:
    env = {"ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8", "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    proj = {"kv_bytes": int(5.0625 * lt.GIB), "per_token_f16": 73728, "kv_factor": 0.28125}
    rec = lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_MEM), proj, "", _host(7, 3.0, 1.0), _host(7, 3.5, 1.5))
    assert rec["ctx_shift"] == "disabled" and rec["ctx_shift_why"] == "not supported by this context"
    assert rec["profile"] == {"ctx": 262144, "parallel": 8, "kv": "q4_0", "coresident": False}
    assert rec["anon_growth_bytes"] == int(1.5 * lt.GIB) - lt.GIB
    assert any("prompt-cache size" in u for u in rec["unknown"]) and not any("projection" in u for u in rec["unknown"])
    kept = lt.memory_record(env, lt.parse_memory_lines(NEMOTRON_MEM), None, "no python", {"why": "x"}, {"why": "y"})
    assert kept["ctx_shift"] == "kept" and kept["anon_growth_bytes"] is None
    assert any(u == "projection: no python" for u in kept["unknown"])
    assert any(u.startswith("host RAM after the load: x") for u in kept["unknown"])
    no_slot = lt.memory_record(env, lt.parse_memory_lines(NEMOTRON_MEM[:2]), None, "", {}, {})
    assert no_slot["ctx_shift"] == "UNKNOWN" and any("slot line" in u for u in no_slot["unknown"])
    unrequested = lt.memory_record({**env, "ARGS": "--model /m"}, lt.parse_memory_lines(NEMOTRON_MEM), None, "", {}, {})
    assert unrequested["ctx_shift"] == "not requested"
    restarted = lt.memory_record(env, lt.parse_memory_lines(NEMOTRON_MEM), None, "", _host(7, 3, 1), _host(9, 3, 1))
    assert restarted["anon_growth_bytes"] is None and any("restart" in u for u in restarted["unknown"])
    blank = lt.memory_record(env, lt.parse_memory_lines([]), None, "", {}, {})
    assert any("no 'llama_kv_cache: size =' line" in u for u in blank["unknown"])


def test_read_proc_status_and_host_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "4242").mkdir()
    (tmp_path / "4242" / "status").write_text("Name:\tllama-server\nVmHWM:\t 9000 kB\nVmRSS:\t 8000 kB\n"
                                              "RssAnon:\t 3000 kB\nRssFile:\t 4000 kB\nRssShmem:\t 1000 kB\n")
    st = lt.read_proc_status(4242, tmp_path)
    assert st == {"pid": 4242, "comm": "llama-server", "VmHWM": 9000 * 1024, "VmRSS": 8000 * 1024,
                  "RssAnon": 3000 * 1024, "RssFile": 4000 * 1024, "RssShmem": 1000 * 1024}
    assert "why" in lt.read_proc_status(1, tmp_path)
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float = 60.0) -> Any:
        seen.append(cmd)
        return lt.subprocess.CompletedProcess(cmd, 0, "4242\n", "")

    monkeypatch.setattr(lt, "run", fake_run)
    assert lt.host_memory("gpt-oss-120b", tmp_path)["VmRSS"] == 8000 * 1024
    assert seen == [["systemctl", "show", "-p", "MainPID", "--value", "llama-server@gpt-oss-120b"]]  # read-only
    monkeypatch.setattr(lt, "run", lambda cmd, timeout=60.0: lt.subprocess.CompletedProcess(cmd, 0, "0\n", ""))
    assert "no main process" in lt.host_memory("gpt-oss-120b", tmp_path)["why"]

    def no_systemctl(cmd: list[str], timeout: float = 60.0) -> Any:
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    monkeypatch.setattr(lt, "run", no_systemctl)
    assert "systemctl show" in lt.host_memory("gpt-oss-120b", tmp_path)["why"]  # a measurement never raises


def _real_ctx(tmp_path: Path, python: str = sys.executable) -> Any:
    ctx = lt.Ctx(engines_path=REAL_ENGINES, env_dir=tmp_path / "env", results_dir=tmp_path / "results",
                 orch_url="http://127.0.0.1:8800", engine_env=tmp_path / "engine-env.py", models_dir=tmp_path,
                 slots_dir=tmp_path, port_base=8100, overrides=tmp_path / "overrides.json", atlas_python=python)
    ctx.load_engines()
    return ctx


def test_arbiter_projection_runs_the_arbiters_own_code(tmp_path: Path) -> None:
    ctx = _real_ctx(tmp_path)
    d, why = lt.arbiter_projection(ctx, "qwen2.5-vl-72b", 65536, 2, "q8_0")  # the S52 standing vision profile
    assert why == "" and d is not None and not d["measured"]
    assert d["kv_bytes"] == int(327_680 * 65536 * 8.5 / 16) and d["total_bytes"] == int(89.625 * lt.GIB)
    d, _ = lt.arbiter_projection(ctx, "gpt-oss-120b", 262144, 8, "q4_0")
    assert d is not None and d["kv_bytes"] == int(73_728 * 262144 * 4.5 / 16) and d["kv_factor"] == 0.28125
    d, _ = lt.arbiter_projection(ctx, "deepseek-v4-flash", 16384, 1, "f16")  # a ladder rung: its own profile
    assert d is not None and d["total_bytes"] == 160 * lt.GIB
    d, why = lt.arbiter_projection(_real_ctx(tmp_path, ""), "gpt-oss-120b", 262144, 8, "q4_0")
    assert d is None and "no orchestrator python" in why
    argv: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float = 60.0) -> Any:
        argv.append(cmd)
        return lt.subprocess.CompletedProcess(cmd, 0, 'noise\n{"kv_bytes": 1}\n', "")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(lt, "run", fake_run)
        assert lt.arbiter_projection(ctx, "meditron-70b", 4096, 1, "q8_0") == ({"kv_bytes": 1}, "")
    # Isolated (no PYTHONPATH, no cwd on sys.path) and no .pyc written as root; the config dir, then the profile.
    assert argv[0][1:4] == ["-I", "-B", "-c"] and argv[0][5:] == [str(REAL_ENGINES.parent), "meditron-70b", "4096",
                                                                  "1", "q8_0"]
    bad = _ctx(tmp_path, ENGINES)  # four engines: atlas.config refuses it, so the projection is UNKNOWN, not a crash
    bad.atlas_python = sys.executable
    d, why = lt.arbiter_projection(bad, "gpt-oss-120b", 262144, 8, "q4_0")
    assert d is None and why.startswith("projection failed (exit 1)") and "ConfigError" in why


def _engine_harness(ctx: Any, monkeypatch: pytest.MonkeyPatch, journal: Any) -> dict[str, Any]:
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({"gtt_used_bytes": 10 * lt.GB}))
    calls: dict[str, Any] = {"host": [], "journal": []}
    env = {"ATLAS_MODEL_PRESENT": "1", "LLAMA_ARG_PORT": "8101", "ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8",
           "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    hosts = iter([_host(77, 3.0, 1.0), _host(77, 3.4, 1.4)])

    def fake_journal(key: str, inv: str, since: str) -> list[str]:
        calls["journal"].append((key, inv))
        return journal(key) if callable(journal) else journal

    def fake_measure(_ctx: Any, res: Any, *_a: Any) -> None:
        calls["host"].append("<the tests>")
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 40.0, 800.0

    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: FakeControl(set()))
    monkeypatch.setattr(ctx, "render", lambda *_a, **_k: dict(env))
    monkeypatch.setattr(lt, "swap_out", lambda *_a: (0.0, "no previous engine"))
    monkeypatch.setattr(lt, "unit_invocation_id", lambda _k: "inv-1")
    monkeypatch.setattr(lt, "prove_kv", lambda key, kv, *_a: (True, f"{key}: {kv} applied", kv))
    monkeypatch.setattr(lt, "run_measurements", fake_measure)
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 80 * lt.GB)
    monkeypatch.setattr(lt, "journal_lines", fake_journal)
    monkeypatch.setattr(lt, "host_memory", lambda key: calls["host"].append(key) or next(hosts))
    return calls


def test_test_engine_records_memory_beside_the_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _real_ctx(tmp_path)
    calls = _engine_harness(ctx, monkeypatch, GPT_OSS_MEM)
    lt.test_engine(ctx, "gpt-oss-120b", None)
    saved = json.loads(ctx.result_path("gpt-oss-120b").read_text())
    assert saved["ok"] and saved["resident_after"] and len(saved["memory"]) == 1
    rec = saved["memory"][0]
    assert rec["measured"]["context_mib"] == 2774.25 and rec["projected"]["kv_bytes"] == int(5.0625 * lt.GIB)
    assert rec["ctx_shift"] == "disabled" and rec["anon_growth_bytes"] == int(1.4 * lt.GIB) - lt.GIB
    assert calls["journal"] == [("gpt-oss-120b", "inv-1")]
    assert calls["host"] == ["gpt-oss-120b", "<the tests>", "gpt-oss-120b"]  # RSS read before and after the requests
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "memory (S52)" in ln]
    assert len(lines) == 1 and "context 2.71 GiB measured (2 K/V line(s), SWA 0.18) vs 5.06 GiB projected" in lines[0]
    assert "context shift disabled (not supported by this context)" in lines[0] and "(anon +410 MiB)" in lines[0]


def test_test_engine_never_fails_on_the_memory_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _real_ctx(tmp_path, "")

    def broken(_key: str) -> list[str]:
        raise RuntimeError("journalctl exploded")

    _engine_harness(ctx, monkeypatch, broken)
    lt.test_engine(ctx, "gpt-oss-120b", None)
    saved = json.loads(ctx.result_path("gpt-oss-120b").read_text())
    assert saved["ok"] and saved["memory"][0]["error"] == "RuntimeError: journalctl exploded"
    assert "memory (S52): not recorded (RuntimeError: journalctl exploded)" in capsys.readouterr().err

    def stopped(_key: str) -> list[str]:
        raise lt.Terminated("signal 15")

    _engine_harness(ctx, monkeypatch, stopped)
    with pytest.raises(lt.Terminated):  # SIGTERM still stops the run: only measurement faults are absorbed
        lt.test_engine(ctx, "gpt-oss-120b", None)


def test_ladder_records_every_rung_that_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"q4_0"})

    def fake_measure(_ctx: Any, res: Any, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0

    _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    monkeypatch.setattr(lt, "journal_lines", lambda *_a: DEEPSEEK_MEM)
    monkeypatch.setattr(lt, "host_memory", lambda _k: _host(5, 160.0, 2.0))
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert [r["label"] for r in res.memory] == ["f16", "q8_0"]  # the q4_0 rung never loaded: nothing to measure
    assert all(r["measured"]["kv_lines"] == 5 and r["ctx_shift"] == "disabled" for r in res.memory)
    saved = json.loads(ctx.result_path("deepseek-v4-flash").read_text())
    assert [r["label"] for r in saved["memory"]] == ["f16", "q8_0"] and saved["kv_applied"] == "q8_0"


def test_cache_suggestion_rules() -> None:
    env = {"ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8", "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    s = lt.cache_suggestion(lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_MEM), None, "", {}, {}))
    ckpt = round(182.25 / 8, 3)
    assert s == {"cache_ram_mib": 2816, "ctx_checkpoints": 5, "checkpoints_known": True,
                 "worst_default_mib": round(8192 + 32 * 8 * ckpt), "worst_suggested_mib": round(2816 + 5 * 8 * ckpt)}
    vis = lt.memory_record({**env, "ATLAS_CTX_SIZE": "65536", "ATLAS_PARALLEL": "2"},
                           lt.parse_memory_lines(VISION_MEM), None, "", {}, {})
    # K/V lines parsed and none of them sliding-window, recurrent or compressor state: none made.
    assert lt.cache_suggestion(vis) == {"cache_ram_mib": 8192, "ctx_checkpoints": None, "checkpoints_known": True,
                                        "worst_default_mib": 8192, "worst_suggested_mib": 8192}
    # Nothing measured is not "none made" (S52 fix round): --ctx-checkpoints from the window anyway, lower bounds.
    unknown = lt.memory_record(env, lt.parse_memory_lines([]), None, "", {}, {})
    assert lt.cache_suggestion(unknown) == {"cache_ram_mib": 8192, "ctx_checkpoints": 5, "checkpoints_known": False,
                                            "worst_default_mib": 8192, "worst_suggested_mib": 8192}
    router = lt.memory_record({**env, "ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "4"},
                              lt.parse_memory_lines(ROUTER_MEM), None, "", {}, {})
    assert lt.cache_suggestion(router)["cache_ram_mib"] == 768 and lt.cache_suggestion(router)["ctx_checkpoints"] == 2
    nem = lt.memory_record({**env, "ATLAS_KV_TYPE": "q8_0"}, lt.parse_memory_lines(NEMOTRON_MEM), None, "",
                           _host(8, 130.0, 1.0), _host(8, 131.0, 2.0))
    text, sug = lt.cache_item("nemotron-3-super", "", nem)
    assert sug is not None and sug["cache_ram_mib"] == 4096 and sug["ctx_checkpoints"] == 5
    assert text == (
        "nemotron-3-super: RSS 130.00 GiB after the load, 131.00 after the tests (RssAnon 1.00 -> 2.00, +1024 MiB); "
        "prompt cache: no eviction logged (below its limit), 1234.5 MiB in 3 prompt(s) at most (TRACE); checkpoints: "
        "0 erased, 4 of 32 made at most (TRACE), one slot's state about 300.0 MiB (INFERRED), so the defaults allow up "
        f"to {8192 + 32 * 8 * 300} MiB; suggested --cache-ram 4096 --ctx-checkpoints 5 (up to "
        f"{4096 + 5 * 8 * 300} MiB)")


# gpt-oss as llama-server's default -lv 3 logs it at the pin: only the srv/slot INF and WRN lines (library INFO is
# TRACE, common/log.cpp:532), so no K/V, flash_attn or compute line; the checkpoint erasures (WRN) are there.
GPT_OSS_LV3 = [ln for ln in GPT_OSS_MEM if " srv " in ln or " slot " in ln]


def test_cache_item_says_unknown_not_none_made_when_nothing_was_measured() -> None:
    """S52 fix round: with no K/V, recurrent or compressor size line (an empty or rotated journal, a unit at -lv 3)
    the cache-ram item says checkpoints UNKNOWN and still suggests --ctx-checkpoints, never 'none made'."""
    assert GPT_OSS_LV3 and not any("llama_kv_cache" in ln or "compute buffer" in ln for ln in GPT_OSS_LV3)
    env = {"ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8", "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    empty = lt.memory_record(env, lt.parse_memory_lines([]), None, "", _host(7, 3.0, 1.0), _host(7, 3.5, 1.5))
    text, sug = lt.cache_item("gpt-oss-120b", "", empty)
    assert sug is not None and sug["checkpoints_known"] is False and sug["ctx_checkpoints"] == 5
    assert "checkpoints: UNKNOWN (no K/V, recurrent or compressor size line in the load log" in text
    assert "none made" not in text and "so it makes them" not in text
    assert text.endswith("suggested --cache-ram 8192 --ctx-checkpoints 5 (at least 8192 MiB, checkpoints UNKNOWN)")
    assert any(u.startswith("checkpoints: UNKNOWN") for u in empty["unknown"])
    lv3 = lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_LV3), None, "", _host(7, 3.0, 1.0), _host(7, 3.5, 1.5))
    assert lv3["measured"]["kv_mib"] is None and lv3["measured"]["n_ctx_slot"] == 32768
    text3, sug3 = lt.cache_item("gpt-oss-120b", "", lv3)
    assert sug3 is not None and sug3["checkpoints_known"] is False and "none made" not in text3
    # An erasure is a WRN line, printed at -lv 3: the engine makes checkpoints, their size is what is UNKNOWN.
    assert ("checkpoints: 1 erased logged, so it makes them, but one slot's state is UNKNOWN (no K/V, recurrent or "
            "compressor size line in the load log)") in text3
    assert text3.endswith("suggested --cache-ram 8192 --ctx-checkpoints 5 (at least 8192 MiB, checkpoints UNKNOWN)")
    assert any(u.startswith("checkpoints: one slot's state UNKNOWN") for u in lv3["unknown"])
    assert any("printed only at the -lv 4" in u for u in lv3["unknown"])
    # Measured K/V lines without sliding-window, recurrent or compressor state: still "none made", no UNKNOWN reason.
    vis = lt.memory_record({**env, "ATLAS_CTX_SIZE": "65536", "ATLAS_PARALLEL": "2", "ATLAS_KV_TYPE": "q8_0"},
                           lt.parse_memory_lines(VISION_MEM), None, "", {}, {})
    assert "checkpoints: none made (its cache rolls back)" in lt.cache_item("qwen2.5-vl-72b", "", vis)[0]
    assert not any(u.startswith("checkpoints: UNKNOWN") for u in vis["unknown"])


def test_memory_todos_word_an_unknown_total_as_at_least(capsys: pytest.CaptureFixture[str]) -> None:
    env = {"ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8", "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    known = lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_MEM), None, "", {}, {})
    unknown = lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_LV3), None, "", {}, {})
    lt.raise_memory_todos([("gpt-oss-120b", "", known, None), ("nemotron-3-super", "", unknown, None)])
    todos = _todos(capsys.readouterr().out)
    kv, cache = todos["kv-estimates"][1], todos["cache-ram"][1]
    assert "could hold at worst at least " in cache and "checkpoints UNKNOWN for at least one engine" in cache
    assert "could hold up to " not in cache
    # An UNKNOWN engine ranked out of the two largest still makes the total a lower bound (S52 fix round).
    lt.raise_memory_todos([("gpt-oss-120b", "", known, None), ("gpt-oss-120b-abliterated", "", known, None),
                           ("nemotron-3-super", "", unknown, None)])
    cache = _todos(capsys.readouterr().out)["cache-ram"][1]
    assert "could hold at worst at least " in cache and "may be larger than the two ranked here" in cache
    assert ("nemotron-3-super 262144 x 8 q4_0: context UNKNOWN GiB measured (no K/V size line in the load log: TRACE "
            "at the pin, printed only at -lv 4)") in kv
    lt.raise_memory_todos([("gpt-oss-120b", "", known, None)])
    cache = _todos(capsys.readouterr().out)["cache-ram"][1]
    assert "could hold up to " in cache and "at least" not in cache
    # S52 fix round: the registered measurement holds at its profile and until a restart only; the estimates govern
    # every decision after that, so a corrected figure is never "on paper only".
    assert "only at the profile it was measured at and only until the orchestrator restarts" in kv
    assert "a corrected figure changes real Arbiter decisions" in kv and "on paper only" not in kv
    assert "already replaces" not in kv


def _todos(stdout: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        tag, tid, title, detail = line.split("\t")
        assert tag == "TODO" and re.fullmatch(r"[a-z0-9][a-z0-9.-]*", tid)  # lib/common.sh todo_add's ID rule
        out[tid] = (title, detail)
    return out


def test_cmd_memory_raises_both_todos_with_the_figures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _real_ctx(tmp_path)
    env = {"ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8", "ATLAS_KV_TYPE": "q4_0", "ARGS": CHAT_ARGS}
    gpt = lt.EngineResult(key="gpt-oss-120b", ok=True, footprint_bytes=70 * lt.GB)
    gpt.memory.append(lt.memory_record(env, lt.parse_memory_lines(GPT_OSS_MEM),
                                       {"kv_bytes": int(5.0625 * lt.GIB), "per_token_f16": 73728, "kv_factor": 0.28125},
                                       "", _host(7, 3.0, 1.0), _host(7, 3.5, 1.5)))
    gpt.save(ctx)
    vis = lt.EngineResult(key="qwen2.5-vl-72b", ok=True)
    vis_env = {**env, "ATLAS_CTX_SIZE": "65536", "ATLAS_PARALLEL": "2", "ATLAS_KV_TYPE": "q8_0"}
    vis.memory.append(lt.memory_record(vis_env, lt.parse_memory_lines(VISION_MEM), None, "no python", {}, {}))
    vis.save(ctx)
    ds = lt.EngineResult(key="deepseek-v4-flash", kv_applied="q8_0", ladder=[
        {"kv": "f16", "footprint_bytes": 160 * lt.GB}, {"kv": "q8_0", "footprint_bytes": None}])
    for kv in ("f16", "q8_0"):
        ds.memory.append({**lt.memory_record({**env, "ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "1",
                                              "ATLAS_KV_TYPE": kv}, lt.parse_memory_lines(DEEPSEEK_MEM), None, "",
                                             {}, {}, kv)})
    ds.save(ctx)
    lt.EngineResult(key="meditron-70b", memory=[{"label": "", "error": "OSError: boom", "unknown": []}]).save(ctx)
    monkeypatch.setattr(lt, "unit_active", lambda k: k == "router-qwen3.5-4b")
    monkeypatch.setattr(lt, "unit_invocation_id", lambda _k: "inv-router")
    monkeypatch.setattr(lt, "run", lambda cmd, timeout=60.0: lt.subprocess.CompletedProcess(
        cmd, 0, "\n".join(ROUTER_MEM) if cmd[-1] == "_SYSTEMD_INVOCATION_ID=inv-router" else "", ""))
    monkeypatch.setattr(lt, "host_memory", lambda _k: _host(3, 5.0, 0.9))
    monkeypatch.setattr(ctx, "env", lambda _k: {**env, "ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "4",
                                                "ATLAS_KV_TYPE": "q8_0"})
    assert lt.cmd_memory(ctx, None) == 0
    cap = capsys.readouterr()
    todos = _todos(cap.out)
    assert set(todos) == {"kv-estimates", "cache-ram"}
    kv_title, kv = todos["kv-estimates"]
    assert kv_title.startswith("Phase 3 measured each engine's context memory") and "(S52)" in kv_title
    assert ("gpt-oss-120b 262144 x 8 q4_0: context 2.71 GiB measured (K/V 2.71 GiB in 2 line(s), of which "
            "sliding-window 0.18) vs 5.06 GiB projected (73728 B/token at f16 x 0.28125); implied 39,456 B/token at "
            "f16 at this profile; compute buffers 1.21 GiB; GTT footprint 70.0 GB registered (rule 1)") in kv
    assert "qwen2.5-vl-72b 65536 x 2 q8_0: context 10.62 GiB measured" in kv and "projection UNKNOWN" in kv
    assert "(+ mmproj 0.29)" in kv
    assert "deepseek-v4-flash (ladder rung f16)" in kv and "deepseek-v4-flash (ladder rung q8_0, the rung that " \
        "stands)" in kv and "GTT footprint 160.0 GB" in kv
    assert "meditron-70b: UNKNOWN (record not taken: OSError: boom)" in kv
    assert "nemotron-3-super (not tested): UNKNOWN" in kv
    assert "router-qwen3.5-4b (resident, not loaded by Phase 3" in kv and "(resident: never charged, Section 4.1)" in kv
    assert "Updating the figures is the Principal's call" in kv
    cache_title, cache = todos["cache-ram"]
    assert cache_title.startswith("Set --cache-ram and --ctx-checkpoints explicitly")
    assert ("gpt-oss-120b: RSS 3.00 GiB after the load, 3.50 after the tests (RssAnon 1.00 -> 1.50, +512 MiB); "
            "prompt cache: 2 eviction(s) logged, largest entry 512.25 MiB; checkpoints: 1 erased") in cache
    assert "suggested --cache-ram 2816 --ctx-checkpoints 5" in cache
    assert "qwen2.5-vl-72b: RSS UNKNOWN" in cache and "checkpoints: none made (its cache rolls back); suggested " \
        "--cache-ram 8192 (up to 8192 MiB)" in cache
    assert "deepseek-v4-flash (ladder rung q8_0, the rung that stands)" in cache and "rung f16)" not in cache
    assert ("router-qwen3.5-4b (resident, not loaded by Phase 3: current journal and RSS, read-only): RSS 5.00 GiB "
            "now (RssAnon 0.90)") in cache and "Phase 3 changed no engine setting" in cache
    assert "The two largest engines plus the resident chat engine(s) could hold up to" in cache
    assert "router-qwen3.5-4b: memory (S52) resident:" in cap.err and "context shift disabled" in cap.err


def test_cmd_memory_never_fails_and_still_raises_both_todos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _real_ctx(tmp_path)

    def unreadable(_ctx: Any) -> Any:
        raise json.JSONDecodeError("bad", "x", 0)

    monkeypatch.setattr(lt, "memory_entries", unreadable)
    assert lt.cmd_memory(ctx, None) == 0
    todos = _todos(capsys.readouterr().out)
    assert set(todos) == {"kv-estimates", "cache-ram"}
    assert all(d.startswith("Figures UNKNOWN: the result files could not be read (JSONDecodeError") and "no record" in d
               for _, d in todos.values())
    monkeypatch.setattr(lt, "memory_entries", lambda _c: [("x", "", {"measured": {}}, None)])  # a malformed record
    assert lt.cmd_memory(ctx, None) == 0
    todos = _todos(capsys.readouterr().out)
    assert set(todos) == {"kv-estimates", "cache-ram"}
    assert all(d.startswith("Figures UNKNOWN: the to-do could not be built (KeyError") for _, d in todos.values())

    def stopped(_ctx: Any) -> Any:
        raise lt.Terminated("signal 15")

    monkeypatch.setattr(lt, "memory_entries", stopped)
    with pytest.raises(lt.Terminated):
        lt.cmd_memory(ctx, None)


def test_todo_line_protocol(capsys: pytest.CaptureFixture[str]) -> None:
    lt.todo("cache-ram", "a\ttitle", "two\nlines\tof detail")
    assert capsys.readouterr().out == "TODO\tcache-ram\ta title\ttwo lines of detail\n"


def test_driver_raises_the_todos_and_passes_the_venv() -> None:
    """phase3-models.sh (root-only, not runnable here): the TODO lines reach todo_add, `memory` runs in step 04 before
    the gate, and loadtest.py gets the orchestrator's venv python for the Arbiter's projection."""
    driver = (LOADTEST.parents[1] / "phase3-models.sh").read_text(encoding="utf-8")
    assert re.search(r'^\s*TODO\) todo_add "\$id" "\$result" "\$msg" ;;', driver, re.M)
    assert re.search(r'^\s*RECORD\) record_v "\$id" "\$result" "\$msg" ;;', driver, re.M)
    assert '--atlas-python "$ATLAS_OPT/venv/bin/python"' in driver
    step4 = driver[driver.index("step_04() {"):]
    step4 = step4[:step4.index("\n}\n")]
    assert step4.index("_p3_loadtest memory") < step4.index("_p3_loadtest table") < step4.index("gate phase3")


def test_resident_memory_record_is_read_only_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _real_ctx(tmp_path)
    monkeypatch.setattr(ctx, "env", lambda _k: {"ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "4", "ARGS": CHAT_ARGS})
    monkeypatch.setattr(lt, "unit_active", lambda _k: False)
    rec = lt.resident_memory_record(ctx, "router-qwen3.5-4b")
    assert rec["error"] == "llama-server@router-qwen3.5-4b is not active"
    monkeypatch.setattr(lt, "unit_active", lambda _k: True)
    monkeypatch.setattr(lt, "unit_invocation_id", lambda _k: "")
    monkeypatch.setattr(lt, "host_memory", lambda _k: _host(3, 5.0, 0.9))
    rec = lt.resident_memory_record(ctx, "router-qwen3.5-4b")
    assert rec["unknown"][0].startswith("the journal of its current invocation (none) is empty")
    assert rec["ctx_shift"] == "UNKNOWN" and rec["host_after_tests"]["VmRSS"] == 5 * lt.GIB

    def missing(_k: str) -> dict[str, str]:
        raise lt.Infra("router-qwen3.5-4b.env missing")

    monkeypatch.setattr(ctx, "env", missing)
    assert lt.resident_memory_record(ctx, "router-qwen3.5-4b")["error"] == "Infra: router-qwen3.5-4b.env missing"

    def stopped(_k: str) -> dict[str, str]:
        raise lt.Terminated("signal 15")

    monkeypatch.setattr(ctx, "env", stopped)
    with pytest.raises(lt.Terminated):
        lt.resident_memory_record(ctx, "router-qwen3.5-4b")
