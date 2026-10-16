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


# The v0.4.0 Day 1 shape (S-34): one plain core seat, the newer-architecture seat (glm5-next, first build b11279 >
# v0.4.1's b10964: its own deferrable V22 row, R19 generalised), the Apex with the S-42 ladder f16 -> q8_0, the
# resident router. No floors are set (D-33 (c): record only), no co-resident profile and no f16 context cap exist.
ENGINES = [
    {"key": "gpt-oss-120b", "arbiter_class": "core", "projected_decode_tok_s": [35, 50], "decode_floor_tok_s": None,
     "ctx_size": 262144, "min_llama_cpp_tag": "b6096"},
    {"key": "glm-5.3-flash", "arbiter_class": "core", "mode": "vision", "arch": "glm5-next",
     "min_llama_cpp_tag": "b11279", "projected_decode_tok_s": [10, 20], "decode_floor_tok_s": None,
     "ctx_size": 262144, "kv_class": "f16"},
    {"key": "deepseek-v4-flash", "arbiter_class": "apex", "kv_ladder": ["f16", "q8_0"], "kv_class": "f16",
     "ctx_size": 131072, "min_llama_cpp_tag": "b9840", "quant": "UD-Q8_K_XL"},
    {"key": "router-qwen3.5-4b", "arbiter_class": "resident"},
]


def _lines(capsys: pytest.CaptureFixture[str]) -> tuple[list[tuple[str, str, str]], dict[str, tuple[str, str]]]:
    """(RECORD lines, TODO lines by id) of one capture: summarize and quality emit both (D-33 (c), S-03)."""
    recs: list[tuple[str, str, str]] = []
    todos: dict[str, tuple[str, str]] = {}
    for line in capsys.readouterr().out.splitlines():
        tag, vid, result, msg = line.split("\t", 3)
        assert tag in ("RECORD", "TODO")
        if tag == "RECORD":
            recs.append((vid, result, msg))
        else:
            todos[vid] = (result, msg)
    return recs, todos


def _records(capsys: pytest.CaptureFixture[str]) -> list[tuple[str, str, str]]:
    return _lines(capsys)[0]


def test_summarize_missing_apex_is_deferred_and_not_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    _good().save(ctx)
    glm = _good("glm-5.3-flash")
    glm.v22_result, glm.v22_msg = "pass", "glm-5.3-flash (glm5-next, first build b11279): loads"
    glm.save(ctx)
    lt.cmd_summarize(ctx, None)
    recs, todos = _lines(capsys)
    assert ("V22", "deferred", "deepseek-v4-flash: no result recorded (R19)") in recs
    assert any(v == "V10" and r == "deferred" and "Apex" in m and m.startswith("deepseek") for v, r, m in recs)
    # The newer-architecture seat: its V10 line is a record pointing at V22, never counted in the V10 summary.
    assert any(v == "V10" and r == "pass" and m.startswith("glm-5.3-flash") and m.endswith("[see V22]")
               for v, r, m in recs)
    assert ("V22", "pass", "glm-5.3-flash (glm5-next, first build b11279): loads") in recs
    summary = [m for v, r, m in recs if v == "V10" and m.startswith("summary")]
    assert summary == [
        "summary: all 1 engines load, generate, swap and release memory at their fixed quantisations; decode recorded "
        "beside the projections (D-33 (c)); warns: none; V22 engines: glm-5.3-flash=pass, deepseek-v4-flash=deferred"]
    v22 = [(r, m) for v, r, m in recs if v == "V22" and m.startswith("summary")]
    assert v22 and v22[-1][0] == "deferred" and "glm-5.3-flash=pass, deepseek-v4-flash=deferred" in v22[-1][1]
    assert recs[-1][0] == "V22"  # the summary is the last V22 the gate reads
    # No floor is set on any seat: the to-do decode-floors carries every seat's measured rate beside its projection.
    title, detail = todos["decode-floors"]
    assert "D-33 (c)" in title and "gpt-oss-120b: 40.0/35.0 tok/s at 512/8k, projected 35-50 tok/s" in detail
    assert "deepseek-v4-flash: not measured, projected none tok/s" in detail


def test_summarize_counts_missing_and_off_band(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # D-33 (c): a seat FAILS only below a floor the Principal set; the abliterated entry keeps its v0.3.5 band and rule
    # (left exactly as v0.3.5 has it) and is counted when it has no result; the V22 engines are never counted.
    engines = [{**ENGINES[0], "decode_floor_tok_s": 30},
               {"key": "gpt-oss-120b-abliterated", "arbiter_class": "core", "expected_decode_tok_s": [30, 55]},
               *ENGINES[1:]]
    ctx = _ctx(tmp_path, engines)
    res = _good()
    res.decode_tps_512 = 20.0
    lt.check_band(res, ctx.spec("gpt-oss-120b"))
    res.save(ctx)
    lt.cmd_summarize(ctx, None)
    recs, todos = _lines(capsys)
    summary = [m for v, r, m in recs if v == "V10" and m.startswith("summary")]
    assert len(summary) == 1 and summary[0].startswith(
        "summary: 2 of 2 engines failed (gpt-oss-120b, gpt-oss-120b-abliterated)")
    assert "below their floor: gpt-oss-120b" in summary[0]
    assert [r for v, r, _ in recs if v == "V10"].count("fail") == 3
    assert any("FLOOR FAIL: decode 20.0 tok/s below the seat's floor 30 tok/s vs 35-50 tok/s projected" in m
               for v, r, m in recs if v == "V10" and r == "fail")
    # The to-do names only the seats without a verdict: floor set (gpt-oss) and v0.3.5 band (abliterated) are out.
    detail = todos["decode-floors"][1]
    assert "glm-5.3-flash: not measured" in detail and "deepseek-v4-flash: not measured" in detail
    assert "gpt-oss-120b" not in detail


def test_summarize_raises_no_floor_todo_once_every_seat_has_a_floor(tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    engines = [{**e, "decode_floor_tok_s": 5} if e["arbiter_class"] in ("core", "apex") else e for e in ENGINES]
    ctx = _ctx(tmp_path, engines)
    _good().save(ctx)
    lt.cmd_summarize(ctx, None)
    recs, todos = _lines(capsys)
    assert "decode-floors" not in todos
    assert any(v == "V10" and r == "pass" and "(projected 35-50, floor 5)" in m
               for v, r, m in recs if m.startswith("gpt-oss-120b"))


# --- the ladder decision (engines.json kv_ladder_rule under S-42 / D-29 (a): the highest-precision coherent rung) ---

LADDER = ["f16", "q8_0"]


def _rung(kv: str, load_ok: bool, coherent: bool, proof: bool = True, **extra: Any) -> dict[str, Any]:
    return {"kv": kv, "load_ok": load_ok, "coherent": coherent, "kv_proof_ok": proof and load_ok, **extra}


def test_ladder_f16_win_stands_and_clears_overrides() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, True)], "f16", "", True, "", 7.5, 300.0,
                         "UD-Q8_K_XL")
    assert v.winner == "f16" and v.v4_result == "pass" and v.clear_ov == ["kv_type", "ctx_size"] and not v.set_ov
    assert v.v22_result == "pass" and v.summary == "f16=coherent, q8_0=not tried" and not v.deviation
    assert "f16 applied and coherent, the highest-precision rung (S-42" in v.v4_msg
    assert v.v22_msg.startswith("deepseek-v4-flash UD-Q8_K_XL loads and generates coherently with f16 KV")


def test_ladder_q8_win_names_the_incoherent_baseline() -> None:
    rungs = [_rung("f16", True, False), _rung("q8_0", True, True)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "f16", "", True, "", 7.0, 280.0)
    assert v.winner == "q8_0" and v.v4_result == "pass" and v.v22_result == "pass"
    assert "q8_0 applied because the f16 rung did not stand (f16=incoherent;" in v.v4_msg
    assert v.set_ov == [("kv_type", "q8_0")] and v.clear_ov == ["ctx_size"] and not v.deviation  # S-42: no deviation


def test_ladder_f16_load_failure_falls_to_q8() -> None:
    rungs = [_rung("f16", False, False), _rung("q8_0", True, True)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "f16", "", True, "", 7.0, 280.0)
    assert v.winner == "q8_0" and "f16=load failed" in v.v4_msg and v.summary == "f16=load failed, q8_0=coherent"
    # A coherent rung whose KV type is unproven never stands: q8_0 wins over an unproven f16.
    rungs = [_rung("f16", True, True, proof=False), _rung("q8_0", True, True)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "f16", "", True, "", 7.0, 280.0)
    assert v.winner == "q8_0" and "f16=coherent but KV unproven" in v.v4_msg


def test_ladder_no_winner_defers_both_and_the_first_winner_stands() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, False), _rung("q8_0", False, False)], "f16",
                         "", True, "", None, None)
    assert v.winner == "" and v.v4_result == "deferred" and v.v22_result == "deferred"
    assert v.clear_ov == ["kv_type", "ctx_size"] and "no coherent rung (f16=incoherent, q8_0=load failed)" in v.v4_msg
    # Rungs are in list order and the FIRST coherent, proven one stands (ladder_engine stops there): never q8_0
    # while f16 is coherent, the opposite of v0.3.5's descent to the lowest coherent rung.
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, True), _rung("q8_0", True, True)], "f16",
                         "", True, "", 1.0, 1.0)
    assert v.winner == "f16"
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", True, True)], "f16", "", False,
                         "GTT still 200 GB", 1.0, 1.0)
    assert v.winner == "f16" and v.v22_result == "deferred" and "GTT still 200 GB" in v.v22_msg
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("f16", False, False)], "f16", "model file x not found",
                         True, "", None, None)
    assert "model file x not found" in v.v4_msg and v.summary == "f16=load failed, q8_0=not tried"


def test_ladder_measurement_failure_keeps_the_rung_and_defers_v22() -> None:
    # A coherent, proven rung whose measurement failed is still the winner (not incoherence); V22 is deferred (R19).
    rungs = [_rung("f16", True, False), _rung("q8_0", True, True, measure_error="8k prefill crashed the server")]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "f16", "", True, "", 6.0, 250.0)
    assert v.winner == "q8_0" and v.v4_result == "pass" and v.v22_result == "deferred"
    assert "measurement failed at q8_0: 8k prefill crashed" in v.v22_msg
    assert "q8_0=coherent (measurement failed)" in v.summary
    assert "q4_0" not in "".join([v.summary, v.v4_msg, v.v22_msg])  # D-29 (a): q4_0 is never a rung


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


def _measure_ok(_ctx: Any, res: Any, *_a: Any) -> None:
    res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 7.5, 300.0


def test_ladder_engine_f16_stands_when_it_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    # S-42 (2): f16 is loaded first at the entry's own profile; when it loads, proves its lines and is coherent it
    # stands, q8_0 is never loaded and nothing is written to overrides.json.
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads=set())
    rendered, _ = _ladder_harness(ctx, ctl, monkeypatch, _measure_ok)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16"]
    assert res.kv_applied == "f16" and res.ok and res.v22_result == "pass" and [r["kv"] for r in res.ladder] == ["f16"]
    assert rendered[0] == ([("kv_type", "f16")], ["ctx_size"])  # no f16 context cap any more (kv_ladder_rule)
    assert rendered[-1] == ([], ["kv_type", "ctx_size"])  # the standing profile: overrides cleared
    assert res.footprint_bytes == 150 * lt.GB and ctl.registered == [150 * lt.GB]
    recs = _records(capsys)
    assert recs[-1][0] == "V4" and recs[-1][1] == "pass" and "f16 applied and coherent" in recs[-1][2]
    assert "q8_0=not tried" in recs[-1][2]
    saved = json.loads(ctx.result_path("deepseek-v4-flash").read_text())
    assert saved["control_mode"] == "orchestrator" and saved["baseline_deviation"] == ""
    assert saved["resident_after"] is False
    assert not lt.pending_path(ctx).exists()  # the journal of the temporary rung overrides is closed


def test_ladder_engine_q8_only_after_f16_fails_to_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"f16"})
    rendered, _ = _ladder_harness(ctx, ctl, monkeypatch, _measure_ok)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0"]
    assert res.kv_applied == "q8_0" and res.ok and res.v22_result == "pass" and not res.baseline_deviation
    assert res.ladder[0]["note"].startswith("load failed") and res.ladder[1]["coherent"]
    assert rendered[1] == ([("kv_type", "q8_0")], ["ctx_size"])
    assert rendered[-1] == ([("kv_type", "q8_0")], ["ctx_size"])  # the rung that stands, written to overrides.json
    recs = _records(capsys)
    assert recs[-1][1] == "pass" and "q8_0 applied because the f16 rung did not stand (f16=load failed" in recs[-1][2]
    assert "q8_0 written to overrides.json" in recs[-1][2]


def test_ladder_engine_incoherent_f16_falls_to_q8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads=set())
    _, current = _ladder_harness(ctx, ctl, monkeypatch, _measure_ok)
    monkeypatch.setattr(lt, "coherence_check", lambda _port, _model: (current["kv"] != "f16", "rung-dependent"))
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0"] and res.kv_applied == "q8_0" and res.ok
    assert ctl.registered == [150 * lt.GB]  # only the rung that stands is measured and registered (rule 1)
    assert res.ladder[0]["coherent"] is False and res.ladder[0]["released"] is True
    assert _records(capsys)[-1][1] == "pass"


def test_ladder_engine_no_coherent_rung_defers_v4_and_v22(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads={"q8_0"})
    rendered, _ = _ladder_harness(ctx, ctl, monkeypatch, _measure_ok)
    monkeypatch.setattr(lt, "coherence_check", lambda _port, _model: (False, "gibberish"))
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["f16", "q8_0"] and res.kv_applied == "" and not res.ok
    assert res.v22_result == "deferred" and rendered[-1] == ([], ["kv_type", "ctx_size"])
    assert ctl.registered == []
    recs = _records(capsys)
    assert recs[-1][:2] == ("V4", "deferred") and "f16=incoherent, q8_0=load failed" in recs[-1][2]


def test_ladder_engine_winner_with_failed_measurement_defers_v22(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    ctl = FakeControl(fail_loads=set())

    def fake_measure(_ctx: Any, res: Any, _key: str, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 7.5, 300.0
        res.crashed_at_8k, res.load_error = True, "8k prefill crashed the server (died during the prefill)"

    _ladder_harness(ctx, ctl, monkeypatch, fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    # A measurement failure is not incoherence: f16 still stands, q8_0 is not tried, V22 is deferred (R19).
    assert ctl.loads == ["f16"]
    assert res.kv_applied == "f16" and res.v22_result == "deferred" and not res.ok
    assert "measurement failed at f16" in res.v22_msg and ctl.registered == []  # a dead server is never registered
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
    """Step 2 measures each seat at the profile production runs (engines.json or the Principal's overrides.json; v0.4.0
    has no co-resident profile, S-22): it neither sets nor clears anything. A newer-architecture seat that does not
    load records V4 deferred and its own V22 deferred (R19 generalised, S-26), never a blocking fail."""
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
    lt.test_engine(ctx, "glm-5.3-flash", None)
    assert rendered == [("glm-5.3-flash", [], [])]
    recs = _records(capsys)
    assert recs[0][:2] == ("V4", "deferred") and "newer architecture (glm5-next, first build b11279)" in recs[0][2]
    saved = json.loads(ctx.result_path("glm-5.3-flash").read_text())
    assert saved["v22_result"] == "deferred" and "pin 10964 cannot load it" in saved["v22_msg"]
    # The same failure on a seat both pins load is a plain V4 fail.
    rendered.clear()
    lt.test_engine(ctx, "gpt-oss-120b", None)
    assert rendered == [("gpt-oss-120b", [], [])] and _records(capsys)[0][:2] == ("V4", "fail")
    assert json.loads(ctx.result_path("gpt-oss-120b").read_text())["v22_result"] == ""


# --- step 03: the single-residency proof (S-24, D-32 (a)) ---------------------------------------------------------

RES_ENGINES = [
    {**ENGINES[0], "footprint_gb": 65.37, "files": [{"name": "gpt-oss-120b-F16.gguf", "bytes": 65 * lt.GB}]},
    {**ENGINES[1], "footprint_gb": 140.0, "files": [{"name": "a.gguf", "bytes": 100 * lt.GB},
                                                    {"name": "b.gguf", "bytes": 40 * lt.GB}]},
    ENGINES[2], ENGINES[3],
]


class _ResControl:
    """The Arbiter at limit 1 (D-32 (a)): granting the second seat evicts the first, unless evict=False."""

    mode = "orchestrator"

    def __init__(self, evict: bool = True) -> None:
        self.evict = evict
        self.ledger: list[str] = []
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.last_decision: dict[str, Any] = {}

    def resident_keys(self) -> list[str]:
        return list(self.ledger)

    def status(self) -> dict[str, Any]:
        return {"budget_bytes": 164 * lt.GB, "max_resident": 1, "generation_queue": []}

    def load(self, key: str, _ctx: int, _par: int, _port: int) -> float:
        evicted = [k for k in self.ledger if self.evict]
        self.ledger = [key] if self.evict else [*self.ledger, key]
        self.loads.append(key)
        self.last_decision = {"decision": "granted", "reason": f"fits after evicting {evicted or 'nothing'}"}
        return 30.0

    def unload(self, key: str) -> str:
        self.ledger.remove(key)
        self.unloads.append(key)
        return f"{key} released"


def _residency_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ctl: _ResControl, present: bool = True,
                       ) -> tuple[Any, list[tuple[str, list[Any], list[Any]]]]:
    ctx = _ctx(tmp_path, RES_ENGINES)
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({"gtt_used_bytes": 10 * lt.GB}))
    rendered: list[tuple[str, list[Any], list[Any]]] = []

    def fake_render(key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((key, list(set_ov), list(clear_ov)))
        return {"ATLAS_MODEL_PRESENT": "1" if present else "0", "ATLAS_MODEL_FILE": f"{key}.gguf",
                "LLAMA_ARG_PORT": "8101", "ATLAS_CTX_SIZE": "262144", "ATLAS_PARALLEL": "8"}

    weights = {"gpt-oss-120b": 65 * lt.GB, "glm-5.3-flash": 140 * lt.GB}

    def chat(_url: str, _model: str, messages: list[dict[str, str]], _max: int, **_k: Any) -> dict[str, Any]:
        if "lighthouses" in messages[0]["content"]:  # the first concurrent request
            return {"error": "", "t_send": 0.0, "t_recv": 10.0, "timings": {"prompt_ms": 1000, "predicted_ms": 8000}}
        if _k.get("path"):  # the second, through the orchestrator: started after the first finished
            return {"error": "", "t_send": 0.5, "t_recv": 18.0, "timings": {"prompt_ms": 500, "predicted_ms": 7500}}
        return {"error": "", "t_send": 0.0, "t_recv": 8.0, "timings": {}}  # the solo timing, direct to the port

    real_sleep = lt.time.sleep
    monkeypatch.setattr(lt.time, "sleep", lambda _s: real_sleep(0.001))
    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: ctl)
    monkeypatch.setattr(ctx, "render", fake_render)
    monkeypatch.setattr(lt, "unit_active", lambda k: k in ctl.ledger)
    monkeypatch.setattr(lt, "clear_residents", lambda _c, _r, base, _w: (True, 0.0, "nothing resident", base))
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 10 * lt.GB + 5 * lt.GB + sum(weights[k] for k in ctl.ledger))
    monkeypatch.setattr(lt, "unload_and_release", lambda c, k, _b: (bool(c.unload(k)), 1.0, "GTT back"))
    monkeypatch.setattr(lt, "require_generation_idle", lambda *_a: None)
    monkeypatch.setattr(lt, "chat", chat)
    monkeypatch.setattr(lt, "arbiter_refusal_proof", lambda *_a: (True, "refused: over budget; downgraded"))
    return ctx, rendered


def test_residency_proves_the_arbiter_evicts_the_first_seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctl = _ResControl()
    ctx, rendered = _residency_harness(tmp_path, monkeypatch, ctl)
    assert lt.cmd_residency(ctx, argparse.Namespace(first="gpt-oss-120b", second="glm-5.3-flash")) == 0
    assert ctl.loads == ["gpt-oss-120b", "glm-5.3-flash"] and ctl.unloads == ["glm-5.3-flash"]
    # Both units rendered as production runs them: no override set or cleared (S-22: no co-resident profile).
    assert rendered == [("gpt-oss-120b", [], []), ("glm-5.3-flash", [], [])] and not ctx.overrides.exists()
    recs = _records(capsys)
    assert [r[:2] for r in recs] == [("V21", "pass"), ("V14b", "pass")]
    v21 = recs[0][2]
    assert "loading glm-5.3-flash evicted gpt-oss-120b (ledger ['glm-5.3-flash']" in v21 and "limit 1" in v21
    assert "GTT delta 145.0 GB for glm-5.3-flash alone (weights 140.0 GB" in v21
    assert "queued behind the first" in v21 and "slot save and restore across the swap: phased" in v21
    saved = json.loads((ctx.results_dir / "residency.json").read_text())
    assert saved["single"] is True and saved["memory_ok"] is True and saved["ledger_after_second"] == ["glm-5.3-flash"]
    assert saved["v14b"][0] == "pass"


def test_residency_fails_when_both_seats_stay_resident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctl = _ResControl(evict=False)
    ctx, _ = _residency_harness(tmp_path, monkeypatch, ctl)
    assert lt.cmd_residency(ctx, argparse.Namespace(first="gpt-oss-120b", second="glm-5.3-flash")) == 0
    recs = _records(capsys)
    assert recs[0][:2] == ("V21", "fail")
    assert "NOT single residency after loading glm-5.3-flash" in recs[0][2] and "still active" in recs[0][2]
    assert "memory out of tolerance" in recs[0][2]  # 210 GB delta: above the budget cap
    assert sorted(ctl.unloads) == ["glm-5.3-flash", "gpt-oss-120b"]  # cleanup unloads both through the Arbiter


def test_residency_without_model_files_records_v21_fail_and_still_proves_v14b(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctl = _ResControl()
    ctx, _ = _residency_harness(tmp_path, monkeypatch, ctl, present=False)
    assert lt.cmd_residency(ctx, argparse.Namespace(first="gpt-oss-120b", second="glm-5.3-flash")) == 0
    recs = _records(capsys)
    assert recs == [("V21", "fail", "gpt-oss-120b: model file not present (gpt-oss-120b.gguf)"),
                    ("V14b", "pass", "refused: over budget; downgraded")]
    assert ctl.loads == []


def test_residency_leak_in_cleanup_fails_v21_and_stops_the_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctl = _ResControl()
    ctx, _ = _residency_harness(tmp_path, monkeypatch, ctl)
    monkeypatch.setattr(lt, "unload_and_release", lambda c, k, _b: (c.unload(k) and False, 120.0, "GTT 40 GB above"))
    with pytest.raises(lt.Infra, match="single-residency cleanup"):
        lt.cmd_residency(ctx, argparse.Namespace(first="gpt-oss-120b", second="glm-5.3-flash"))
    recs = _records(capsys)
    assert recs[0][:2] == ("V21", "fail") and "release: glm-5.3-flash did not release" in recs[0][2]
    assert recs[1] == ("V14b", "fail", "not attempted: memory still held after the residency test")


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
    lt.test_engine(ctx, "gpt-oss-120b", "glm-5.3-flash")
    assert swapped == ["glm-5.3-flash"]  # the previous engine was released although this one never loaded
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
    assert rec["profile"] == {"ctx": 262144, "parallel": 8, "kv": "q4_0"}  # S-22: no co-resident profile
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
    from atlas.arbiter import Arbiter
    from atlas.config import load_engines

    ctx = _real_ctx(tmp_path)
    # The subprocess projection is the Arbiter's own (v0.4.0: exact file bytes, D-31; the S-17 KV table with per-slot
    # state; the compute-buffer term), for the Day 1 seats at the profiles Phase 3 renders.
    arb = Arbiter(load_engines(REAL_ENGINES.parent), None, None)  # type: ignore[arg-type]
    for key, n_ctx, par, kv in (("glm-5.3-flash", 262144, 8, "f16"), ("gpt-oss-120b", 262144, 8, "q8_0"),
                                ("deepseek-v4-flash", 131072, 1, "f16"), ("deepseek-v4-flash", 131072, 1, "q8_0")):
        d, why = lt.arbiter_projection(ctx, key, n_ctx, par, kv)
        fp = arb.projected_footprint(key, n_ctx, par, kv)
        assert why == "" and d is not None and not d["measured"], (key, why)
        assert (d["weights_bytes"], d["kv_bytes"], d["total_bytes"]) == (fp.weights_bytes, fp.kv_bytes, fp.total_bytes)
    d, _ = lt.arbiter_projection(ctx, "glm-5.3-flash", 262144, 8, "f16")
    assert d is not None and d["per_token_f16"] == 19_712 and d["kv_bytes"] == 19_712 * 262144 + 8 * 152_633_344
    d, _ = lt.arbiter_projection(ctx, "gpt-oss-120b", 262144, 8, "q8_0")
    assert d is not None and d["per_token_f16"] == 36_864 and d["kv_factor"] == 0.53125
    d, _ = lt.arbiter_projection(ctx, "deepseek-v4-flash", 131072, 1, "f16")  # a ladder rung: its own profile
    assert d is not None and d["per_token_f16"] == 6_880 and d["kv_factor"] == 1.0
    d, why = lt.arbiter_projection(_real_ctx(tmp_path, ""), "gpt-oss-120b", 262144, 8, "q8_0")
    assert d is None and "no orchestrator python" in why
    argv: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float = 60.0) -> Any:
        argv.append(cmd)
        return lt.subprocess.CompletedProcess(cmd, 0, 'noise\n{"kv_bytes": 1}\n', "")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(lt, "run", fake_run)
        assert lt.arbiter_projection(ctx, "glm-5.3-flash", 4096, 1, "q8_0") == ({"kv_bytes": 1}, "")
    # Isolated (no PYTHONPATH, no cwd on sys.path) and no .pyc written as root; the config dir, then the profile.
    assert argv[0][1:4] == ["-I", "-B", "-c"] and argv[0][5:] == [str(REAL_ENGINES.parent), "glm-5.3-flash", "4096",
                                                                  "1", "q8_0"]
    bad = _ctx(tmp_path, ENGINES)  # four engines: atlas.config refuses it, so the projection is UNKNOWN, not a crash
    bad.atlas_python = sys.executable
    d, why = lt.arbiter_projection(bad, "gpt-oss-120b", 262144, 8, "q8_0")
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
    # The Arbiter's projection for gpt-oss-120b at 262144 x 8 q4_0 (S-17, v0.4.0): the 18 full-attention layers' cache
    # (36,864 B/token at f16) plus the sliding-window cells of each of the 8 slots.
    assert rec["measured"]["context_mib"] == 2774.25
    assert rec["projected"]["kv_bytes"] == int(36_864 * 262144 * 4.5 / 16) + 8 * 84_934_656
    assert rec["ctx_shift"] == "disabled" and rec["anon_growth_bytes"] == int(1.4 * lt.GIB) - lt.GIB
    assert calls["journal"] == [("gpt-oss-120b", "inv-1")]
    assert calls["host"] == ["gpt-oss-120b", "<the tests>", "gpt-oss-120b"]  # RSS read before and after the requests
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "memory (S52)" in ln]
    assert len(lines) == 1 and "context 2.71 GiB measured (2 K/V line(s), SWA 0.18) vs 3.16 GiB projected" in lines[0]
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
    ctl = FakeControl(fail_loads=set())
    _, current = _ladder_harness(ctx, ctl, monkeypatch, _measure_ok)
    monkeypatch.setattr(lt, "coherence_check", lambda _port, _model: (current["kv"] != "f16", "rung-dependent"))
    monkeypatch.setattr(lt, "journal_lines", lambda *_a: DEEPSEEK_MEM)
    monkeypatch.setattr(lt, "host_memory", lambda _k: _host(5, 160.0, 2.0))
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert [r["label"] for r in res.memory] == ["f16", "q8_0"]  # the incoherent f16 rung loaded: it is recorded too
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
    # v0.4.0 Day 1 list (S-34, real engines.json): the vision record belongs to glm-5.3-flash, the engine with the
    # mmproj (D-20); the journal is the generic vision fixture (VISION_MEM) at its own synthetic profile.
    vis = lt.EngineResult(key="glm-5.3-flash", ok=True)
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
    # gpt-oss-120b-abliterated has no result file: the "not tested" entry. The "record not taken" entry is checked
    # below by replacing gpt-oss-120b's record (the Day 1 list has no fifth Phase 3 engine to carry it).
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
    assert "glm-5.3-flash 65536 x 2 q8_0: context 10.62 GiB measured" in kv and "projection UNKNOWN" in kv
    assert "(+ mmproj 0.29)" in kv
    assert "deepseek-v4-flash (ladder rung f16)" in kv and "deepseek-v4-flash (ladder rung q8_0, the rung that " \
        "stands)" in kv and "GTT footprint 160.0 GB" in kv
    assert "gpt-oss-120b-abliterated (not tested): UNKNOWN" in kv
    for retired in ("qwen2.5-vl-72b", "meditron-70b", "nemotron-3-super", "qwen3.5-122b"):
        assert retired not in kv  # S-11: only engines.json's Phase 3 engines are listed
    assert "router-qwen3.5-4b (resident, not loaded by Phase 3" in kv and "(resident: never charged, Section 4.1)" in kv
    assert "Updating the figures is the Principal's call" in kv
    cache_title, cache = todos["cache-ram"]
    assert cache_title.startswith("Set --cache-ram and --ctx-checkpoints explicitly")
    assert ("gpt-oss-120b: RSS 3.00 GiB after the load, 3.50 after the tests (RssAnon 1.00 -> 1.50, +512 MiB); "
            "prompt cache: 2 eviction(s) logged, largest entry 512.25 MiB; checkpoints: 1 erased") in cache
    assert "suggested --cache-ram 2816 --ctx-checkpoints 5" in cache
    assert "glm-5.3-flash: RSS UNKNOWN" in cache and "checkpoints: none made (its cache rolls back); suggested " \
        "--cache-ram 8192 (up to 8192 MiB)" in cache
    assert "deepseek-v4-flash (ladder rung q8_0, the rung that stands)" in cache and "rung f16)" not in cache
    assert ("router-qwen3.5-4b (resident, not loaded by Phase 3: current journal and RSS, read-only): RSS 5.00 GiB "
            "now (RssAnon 0.90)") in cache and "Phase 3 changed no engine setting" in cache
    assert "The two largest engines plus the resident chat engine(s) could hold up to" in cache
    assert "router-qwen3.5-4b: memory (S52) resident:" in cap.err and "context shift disabled" in cap.err
    # A record that could not be taken is UNKNOWN with the reason, and the to-dos still go out.
    lt.EngineResult(key="gpt-oss-120b", memory=[{"label": "", "error": "OSError: boom", "unknown": []}]).save(ctx)
    assert lt.cmd_memory(ctx, None) == 0
    todos = _todos(capsys.readouterr().out)
    assert set(todos) == {"kv-estimates", "cache-ram"}
    assert "gpt-oss-120b: UNKNOWN (record not taken: OSError: boom)" in todos["kv-estimates"][1]


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


def test_driver_wires_the_v040_steps_and_the_day1_list() -> None:
    """phase3-models.sh for v0.4.0: the Day 1 classes (S-34), the parity and quality steps between the load tests and
    the single-residency test (S-02, S-03), V25 in the gate, both llama-perplexity builds passed to loadtest.py, the
    free space without the Meditron join, the size announced from engines.json (D-42 (b)), and every engine key the
    driver names present in engines.json."""
    driver = (LOADTEST.parents[1] / "phase3-models.sh").read_text(encoding="utf-8")
    assert re.search(r'^P3_CLASSES="core apex"', driver, re.M)
    assert ("run_step phase3 02 step_02\nrun_step phase3 02b step_02b\nrun_step phase3 02c step_02c\n"
            "run_step phase3 03 step_03\nrun_step phase3 04 step_04") in driver
    assert '_p3_loadtest residency --first "$P3_FIRST_KEY" --second "$P3_SECOND_KEY"' in driver
    assert "_p3_loadtest coresident" not in driver and "P3_VISION_KEY" not in driver
    assert "gate phase3 V4 V10 V14b V21 V25 -- V22" in driver
    assert '--perplexity "$P3_PERPLEXITY" --parity-perplexity "$P3_PARITY_PERPLEXITY"' in driver
    assert 'P3_PARITY_PERPLEXITY="$ATLAS_OPT/llama-parity/dist/bin/llama-perplexity"' in driver
    assert "local need=$(( total + total / 20 ))" in driver and "80000000000" not in driver and "690 GB" not in driver
    assert re.search(r"^P3_MEMORY_FNS=\(.* engine_tree_lfs .*\)$", driver, re.M)
    for fn in ("_p3_remaining_bytes", "_p3_tree_matches_manifest"):
        body = driver[driver.index(f"{fn}() {{"):]
        body = body[:body.index("\n}\n")]
        assert 'engine_tree_lfs "$key"' in body and "hf_tree_lfs " not in body, fn
    for step in ("step_02b", "step_02c"):
        body = driver[driver.index(f"{step}() {{"):]
        body = body[:body.index("\n}\n")]
        assert "_p3_quiesce_background" in body and "_p3_resume_background" in body, step
    keys = {e["key"]: e for e in json.loads(REAL_ENGINES.read_text())["engines"]}
    for var in ("P3_FIRST_KEY", "P3_SECOND_KEY"):
        m = re.search(rf'^{var}="([^"]+)"', driver, re.M)
        assert m and m.group(1) in keys and keys[m.group(1)]["arbiter_class"] in lt.P3_CLASSES, var
    assert re.search(r'^P3_SECOND_KEY="' + re.escape(lt.DEEP_THINK_GENERATOR) + '"', driver, re.M)


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


# --- v0.4.0: newer architectures (S-26), the glm5-next memory lines, the probe seat ----------------------------------


def test_newer_architecture_helpers() -> None:
    assert lt.build_number("b11279") == 11279 and lt.build_number("v0.4.1") is None and lt.build_number(None) is None
    gpt, glm, ds = ENGINES[0], ENGINES[1], ENGINES[2]
    assert lt.is_newer_arch(glm) and not lt.is_newer_arch(gpt) and not lt.is_newer_arch(ds)
    assert not lt.is_newer_arch({"min_llama_cpp_tag": f"b{lt.PARITY_PIN_BUILD}"})  # v0.4.1 itself loads it
    assert not lt.is_newer_arch({})  # no tag recorded (the unchanged abliterated entry): not newer
    assert lt.is_v22_engine(glm) and lt.is_v22_engine(ds) and not lt.is_v22_engine(gpt)
    assert lt.v4_verdict(gpt, True, "ok") == ("pass", "ok") and lt.v4_verdict(gpt, False, "bad") == ("fail", "bad")
    result, msg = lt.v4_verdict(glm, False, "bad")
    assert result == "deferred" and msg.startswith("bad [newer architecture (glm5-next, first build b11279)")
    assert "R19 generalised, S-26" in msg


def test_set_v22_for_a_newer_architecture_seat() -> None:
    res = _good("glm-5.3-flash")
    res.ok, res.kv_proof_ok, res.kv_applied = True, True, "f16"
    lt.set_v22(res, ENGINES[1])
    assert res.v22_result == "pass" and "proves f16 KV" in res.v22_msg and "pin 10964 cannot load it" in res.v22_msg
    res = _good("glm-5.3-flash")
    res.ok, res.kv_proof_ok, res.kv_proof_msg = True, False, "glm-5.3-flash: f16 requested, NOT proven"
    lt.set_v22(res, ENGINES[1])
    assert res.v22_result == "deferred" and "V4 not proven: glm-5.3-flash: f16 requested" in res.v22_msg
    plain, apex = _good(), _good("deepseek-v4-flash")
    lt.set_v22(plain, ENGINES[0])
    lt.set_v22(apex, {**ENGINES[2], "min_llama_cpp_tag": "b11300"})  # the Apex's V22 comes from its ladder only
    assert plain.v22_result == "" and apex.v22_result == ""


def test_check_band_d33c_floor_is_the_only_verdict() -> None:
    spec = {"projected_decode_tok_s": [35, 50], "decode_floor_tok_s": None, "arbiter_class": "core"}
    res = _good()
    res.decode_tps_512 = 5.0  # far below the projection, but no floor: a record, never a fail (D-33 (c))
    lt.check_band(res, spec)
    assert not res.band_fail and "no floor set" in res.band_note and "vs 35-50 tok/s projected" in res.band_note
    assert lt.v10_line(res, spec)[0] == "pass" and "(projected 35-50, no floor)" in lt.v10_line(res, spec)[1]
    floored = {**spec, "decode_floor_tok_s": 20}
    res = _good()
    res.decode_tps_512 = 19.9
    lt.check_band(res, floored)
    assert res.band_fail.startswith("decode 19.9 tok/s below the seat's floor 20 tok/s")
    assert lt.v10_line(res, floored)[0] == "fail"
    res = _good()
    res.decode_tps_512 = 20.0
    lt.check_band(res, floored)
    assert not res.band_fail and "at or above the floor 20" in res.band_note
    res = _good()
    res.decode_tps_512 = 3.0
    lt.check_band(res, {"decode_floor_tok_s": None})
    assert not res.band_fail and "(no projection recorded)" in res.band_note


FMT_IDX = "%s: creating indexer KV cache, size = %u cells"  # src/llama-memory-hybrid-idx.cpp:68 (b11474)
GLM_MEM = [
    _cl("I", "llama_context: flash_attn            = enabled"),
    _kv(262144, 47, 8, 1, "f16", 4700.0, 0.0),  # the MLA attention cache, announced by nothing: "full"
    _cl("I", FMT_IDX % ("llama_memory_hybrid_idx", 262144)),
    _kv(262144, 47, 8, 1, "f16", 470.0, 0.0),  # the DSA indexer cache
    _srv("load_model", "I", FMT_SLOTS % (8, 32768, "true")),
]


def test_glm5_next_memory_lines_tag_the_indexer_and_prove_v4() -> None:
    m = lt.parse_memory_lines(GLM_MEM)
    assert [p["part"] for p in m["kv_parts"]] == ["full", "indexer"] and m["kv_lines"] == 2
    assert m["kv_mib"] == 5170.0 and m["n_slots"] == 8
    real = {e["key"]: e for e in json.loads(REAL_ENGINES.read_text())["engines"]}
    ok, msg, applied = lt.evaluate_kv_lines("glm-5.3-flash", "f16", int(real["glm-5.3-flash"]["kv_proof_lines"]),
                                            GLM_MEM)
    assert ok and applied == "f16" and "2 llama_kv_cache line(s)" in msg


def test_pick_probe_key_prefers_the_deep_think_generator_and_never_the_abliterated(tmp_path: Path) -> None:
    ctx = _real_ctx(tmp_path)
    assert lt.pick_probe_key(ctx, ()) == "glm-5.3-flash" == lt.DEEP_THINK_GENERATOR
    assert lt.pick_probe_key(ctx, ("glm-5.3-flash",)) == "gpt-oss-120b"
    with pytest.raises(lt.Infra):
        lt.pick_probe_key(ctx, ("glm-5.3-flash", "gpt-oss-120b"))  # the abliterated twin and the Apex never probe


def test_main_offers_residency_parity_quality_and_no_coresident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 ) -> None:
    ep = tmp_path / "engines.json"
    ep.write_text(json.dumps({"engines": ENGINES}), encoding="utf-8")
    base = ["--engines", str(ep), "--env-dir", str(tmp_path), "--results-dir", str(tmp_path / "r"), "--orch-url",
            "http://127.0.0.1:8800", "--engine-env", "x", "--models-dir", "x", "--slots-dir", "x", "--port-base",
            "8100", "--overrides", str(tmp_path / "o.json")]
    seen: list[Any] = []
    monkeypatch.setattr(lt.signal, "signal", lambda *_a: None)
    for name in ("cmd_residency", "cmd_parity", "cmd_quality"):
        monkeypatch.setattr(lt, name, lambda _ctx, args: seen.append(args) or 0)
    # main() reads the subcommand functions from the module globals at call time, so the stand-ins receive the args.
    assert lt.main([*base, "residency", "--first", "gpt-oss-120b", "--second", "glm-5.3-flash"]) == 0
    assert lt.main([*base, "--perplexity", "/new", "--parity-perplexity", "/old", "parity"]) == 0
    assert lt.main([*base, "quality"]) == 0
    assert (seen[0].first, seen[0].second) == ("gpt-oss-120b", "glm-5.3-flash")
    assert (seen[1].perplexity, seen[1].parity_perplexity) == ("/new", "/old")
    assert seen[2].parity_perplexity.endswith("llama-parity/dist/bin/llama-perplexity")
    with pytest.raises(SystemExit):
        lt.main([*base, "coresident", "--text", "gpt-oss-120b", "--vision", "glm-5.3-flash"])


# --- S-02 output parity (V25) and the S-03 KLD record ----------------------------------------------------------------


def _hs_lines(answers: list[bool]) -> str:
    out, n = [], 0
    for i, a in enumerate(answers, start=1):
        n += a
        out.append(f"{i}\t{100.0 * n / i:3.8f}%\t[{0.0:.4f}%, {100.0:.4f}%]")  # tools/perplexity/perplexity.cpp:988
    return "\n".join(out)


def _kld_text(kld: float, top: float) -> str:
    return "\n".join([
        "====== Perplexity statistics ======",
        f"Mean PPL(Q)                   : {7.120001:10.6f} ± {0.051:10.6f}",
        f"Mean PPL(base)                : {7.110002:10.6f} ± {0.050:10.6f}",
        "====== KL divergence statistics ======",
        f"Mean    KLD: {kld:10.6f} ± {0.000012:10.6f}",
        "====== Token probability statistics ======",
        f"Same top p: {top:6.3f} ± {0.123:5.3f} %",
    ])


def test_parse_ppl_kld_and_hellaswag() -> None:
    assert lt.parse_ppl("[1]7.1,[2]7.0\nFinal estimate: PPL = 7.1100 +/- 0.05000\n") == 7.11
    assert lt.parse_ppl("llama_model_load: error") is None
    k = lt.parse_kld(_kld_text(0.000412, 99.512))
    assert k == {"mean_kld": 0.000412, "mean_kld_unc": 1.2e-05, "same_top_pct": 99.512, "same_top_unc": 0.123,
                 "ppl_q": 7.120001, "ppl_base": 7.110002}
    assert lt.parse_kld("Mean    KLD:   0.000412 ±   0.000012\n") is None  # no top-1 line: the run did not finish
    hs = lt.parse_hellaswag("hellaswag_score: calculating\n" + _hs_lines([True, False, True, True]) + "\n\n")
    assert hs == {"n": 4, "correct": 3, "acc_pct": 75.0, "answers": [True, False, True, True]}
    assert lt.parse_hellaswag("1\t100.00000000%\t[0%, 100%]\n3\t66.66666667%\t[0%, 100%]") is None  # numbering skips
    assert lt.parse_hellaswag("nothing") is None
    other = lt.parse_hellaswag(_hs_lines([True, True, False, True]))
    assert lt.changed_answers(hs, other) == 2 and lt.changed_answers(hs, hs) == 0
    assert lt.changed_answers(hs, None) is None
    assert lt.changed_answers(hs, lt.parse_hellaswag(_hs_lines([True]))) is None  # different task counts


def _run(kld: float = 0.0004, top: float = 99.5, old: list[bool] | None = None, new: list[bool] | None = None,
         **flags: Any) -> dict[str, Any]:
    old = [True, False, True, True] if old is None else old
    new = old if new is None else new
    return {"errors": [], "kld": {"mean_kld": kld, "mean_kld_unc": 0.0, "same_top_pct": top, "same_top_unc": 0.0},
            "old_tasks": lt.parse_hellaswag(_hs_lines(old)), "new_tasks": lt.parse_hellaswag(_hs_lines(new)), **flags}


def test_parity_engine_verdict_fa_on_decides_and_off_is_recorded() -> None:
    result, msg = lt.parity_engine_verdict("gpt-oss-120b", {"on": _run(), "off": _run(kld=0.3, top=80.0)})
    assert result == "pass" and "within parity at -fa on" in msg and "-fa off: KLD 0.300000, top-1 same 80.00%" in msg
    assert "tasks 3->3 of 4, 0 changed" in msg
    assert lt.parity_engine_verdict("x", {"on": _run(kld=0.02), "off": _run()})[0] == "fail"
    assert lt.parity_engine_verdict("x", {"on": _run(top=97.9), "off": _run()})[0] == "fail"
    result, msg = lt.parity_engine_verdict("x", {"on": _run(new=[True, False, False, True]), "off": _run()})
    assert result == "fail" and "tasks 3->2 of 4, 1 changed" in msg
    # More correct answers on the new build with the same KLD is still parity (no fewer correct tasks).
    assert lt.parity_engine_verdict("x", {"on": _run(old=[False, False, True, True], new=[True, False, True, True]),
                                          "off": _run()})[0] == "pass"
    # The old build cannot run it: nothing to compare (deferred); the new build failing is a fail.
    assert lt.parity_engine_verdict("x", {"on": {"errors": ["v0.4.1 base run exit 1"], "old_failed": True},
                                          "off": {}})[0] == "deferred"
    result, msg = lt.parity_engine_verdict("x", {"on": {**_run(), "kld": None, "new_failed": True,
                                                        "errors": ["b11474 KLD run exit 1: boom"]}, "off": _run()})
    assert result == "fail" and "errors: b11474 KLD run exit 1: boom" in msg


PARITY_ENGINES = [
    {**ENGINES[0], "files": [{"name": "gpt-oss-120b-F16.gguf", "bytes": 1}]},
    {"key": "gpt-oss-120b-abliterated", "arbiter_class": "core", "expected_decode_tok_s": [30, 55]},
    ENGINES[1], ENGINES[2],
    {"key": "router-qwen3.5-4b", "arbiter_class": "resident", "mode": "chat", "min_llama_cpp_tag": "b7990"},
    {"key": "embed-qwen3-4b", "arbiter_class": "resident", "mode": "embedding", "placement": "cpu",
     "min_llama_cpp_tag": "b6063"},
]


def test_parity_coverage_names_what_v25_cannot_compare(tmp_path: Path) -> None:
    covered, notes = lt.parity_coverage(_ctx(tmp_path, PARITY_ENGINES))
    assert covered == ["gpt-oss-120b", "deepseek-v4-flash", "router-qwen3.5-4b"]
    text = " | ".join(notes)
    assert "gpt-oss-120b-abliterated: not compared (unchanged by this revision" in text
    assert "glm-5.3-flash: not comparable (first build b11279, v0.4.1 cannot load it" in text
    assert "embed-qwen3-4b: not compared (embedding: no next-token logits" in text
    # The real Day 1 list: the same three are covered (engines.json pin_rule).
    real, _ = lt.parity_coverage(_real_ctx(tmp_path))
    assert real == ["gpt-oss-120b", "deepseek-v4-flash", "router-qwen3.5-4b"]


def test_parity_tasks_file_has_the_hellaswag_shape() -> None:
    raw = lt.PARITY_TASKS_FILE.read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.isascii()
    lines = raw.split("\n")[:-1]
    assert len(lines) % 6 == 0 and len(lines) // 6 >= 20 and all(ln.strip() for ln in lines)
    gold = [lines[i] for i in range(1, len(lines), 6)]
    assert set(gold) == {"0", "1", "2", "3"}  # every answer position is used
    assert len(lines) // 6 < 1000  # --hellaswag-tasks 1000 takes them all, in file order


def _fake_tool(path: Path, which: str, kld_on: float = 0.0004, answers: list[bool] | None = None,
               refuse: str = "") -> str:
    """A stand-in llama-perplexity printing what the pinned tool prints (tools/perplexity/perplexity.cpp) and logging
    its argv; refuse=SUBSTRING makes it exit 1 for a model path containing it (v0.4.1 cannot load that file)."""
    answers = [True, False, True, True, False, True] if answers is None else answers
    path.write_text(f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
with open({str(path) + ".calls"!r}, "a") as fh:
    fh.write(json.dumps(args) + "\\n")
model = args[args.index("-m") + 1]
if {refuse!r} and {refuse!r} in model:
    print("llama_model_load: error loading model: unknown model architecture")
    sys.exit(1)
fa = args[args.index("-fa") + 1]
if "--hellaswag" in args:
    print({_hs_lines(answers)!r})
    sys.exit(0)
if "--kl-divergence" in args:
    print({_kld_text(kld_on, 99.6)!r} if fa == "on" else {_kld_text(0.25, 85.0)!r})
    sys.exit(0)
if "--kl-divergence-base" in args:
    open(args[args.index("--kl-divergence-base") + 1], "wb").write(b"logits")
print("Final estimate: PPL = 7.1100 +/- 0.05000")
""", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _calls(tool: str) -> list[list[str]]:
    f = Path(tool + ".calls")
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []


def _tools_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_ok: bool = True) -> Any:
    ctx = _ctx(tmp_path, PARITY_ENGINES)
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "01-systems.md").write_text("# Systems\n\nA fixed corpus line.\n", encoding="utf-8")
    kv = {"gpt-oss-120b": "q8_0", "glm-5.3-flash": "f16", "deepseek-v4-flash": "f16", "router-qwen3.5-4b": "q8_0"}
    monkeypatch.setattr(lt, "Control", lambda *_a, **_k: object())
    monkeypatch.setattr(lt, "_clear_for_tools", lambda *_a: 10 * lt.GB)
    monkeypatch.setattr(lt, "require_generation_idle", lambda *_a: None)
    monkeypatch.setattr(lt, "wait_release", lambda _b, label: (release_ok, 1.0, "GTT back" if release_ok else
                                                              f"{label}: GTT 30 GB above the baseline"))
    monkeypatch.setattr(ctx, "env", lambda key: {"ATLAS_MODEL_PRESENT": "1", "ATLAS_MODEL_FILE": f"/m/{key}.gguf",
                                                 "ATLAS_KV_TYPE": kv.get(key, "")})
    return ctx


def test_cmd_parity_compares_both_builds_fa_on_and_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctx = _tools_harness(tmp_path, monkeypatch)
    new = _fake_tool(tmp_path / "new-perplexity", "new")
    old = _fake_tool(tmp_path / "old-perplexity", "old", refuse="deepseek")
    assert lt.cmd_parity(ctx, argparse.Namespace(perplexity=new, parity_perplexity=old)) == 0
    recs = _records(capsys)
    v25 = {m.split(":")[0]: r for v, r, m in recs if v == "V25"}
    assert v25["gpt-oss-120b"] == "pass" and v25["router-qwen3.5-4b"] == "pass"
    assert v25["deepseek-v4-flash"] == "deferred"  # the old build refused the file: nothing to compare
    assert recs[-1][:2] == ("V25", "deferred") and recs[-1][2].startswith(
        "summary: gpt-oss-120b=pass, deepseek-v4-flash=deferred, router-qwen3.5-4b=pass")
    assert "glm-5.3-flash: not comparable" in recs[-1][2]
    saved = json.loads((ctx.results_dir / "parity.json").read_text())
    assert saved["covered"] == ["gpt-oss-120b", "deepseek-v4-flash", "router-qwen3.5-4b"] and saved["leaks"] == []
    assert saved["engines"]["gpt-oss-120b"]["runs"]["off"]["kld"]["mean_kld"] == 0.25  # recorded, not a verdict
    old_calls, new_calls = _calls(old), _calls(new)
    base = [c for c in old_calls if "--kl-divergence-base" in c and "/m/gpt-oss-120b.gguf" in c]
    assert [c[c.index("-fa") + 1] for c in base] == ["on", "off"]
    assert all(c[c.index("-c") + 1] == "512" and c[c.index("--chunks") + 1] == "16" and "-f" in c for c in base)
    assert all("--cache-type-k" not in c for c in old_calls + new_calls)  # parity runs the default KV on both pins
    kld = [c for c in new_calls if "--kl-divergence" in c]
    assert len(kld) == 4 and all("-f" not in c for c in kld)  # gpt-oss and the router, -fa on and off
    hs = [c for c in new_calls + old_calls if "--hellaswag" in c]
    assert all(c[c.index("-f") + 1] == str(lt.PARITY_TASKS_FILE) for c in hs) and len(hs) == 12
    assert not list((ctx.results_dir / "parity-work").glob("*.kld"))  # the base logits are removed after each run


def test_cmd_parity_fails_a_changed_output_and_stops_on_a_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctx = _tools_harness(tmp_path, monkeypatch)
    new = _fake_tool(tmp_path / "new-perplexity", "new", kld_on=0.05)
    old = _fake_tool(tmp_path / "old-perplexity", "old")
    assert lt.cmd_parity(ctx, argparse.Namespace(perplexity=new, parity_perplexity=old)) == 0
    recs = _records(capsys)
    assert recs[-1][:2] == ("V25", "fail") and all(r == "fail" for v, r, _ in recs if v == "V25")
    (tmp_path / "leak").mkdir()
    ctx = _tools_harness(tmp_path / "leak", monkeypatch, release_ok=False)
    with pytest.raises(lt.Infra, match="memory not released after a llama-perplexity run"):
        lt.cmd_parity(ctx, argparse.Namespace(perplexity=_fake_tool(tmp_path / "leak" / "n", "new"),
                                              parity_perplexity=_fake_tool(tmp_path / "leak" / "o", "old")))
    recs = _records(capsys)
    assert recs[-1][:2] == ("V25", "fail") and "LEAK:" in recs[-1][2]
    assert json.loads((ctx.results_dir / "parity.json").read_text())["leaks"]  # written before the stop


def test_cmd_parity_missing_builds_fail_v25_without_touching_an_engine(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctx = _tools_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(lt, "_clear_for_tools", lambda *_a: pytest.fail("no seat is unloaded without the tools"))
    assert lt.cmd_parity(ctx, argparse.Namespace(perplexity=str(tmp_path / "none"),
                                                 parity_perplexity=str(tmp_path / "none-old"))) == 0
    recs = _records(capsys)
    assert len(recs) == 1 and recs[0][:2] == ("V25", "fail") and "phase2/01-llama.sh builds both" in recs[0][2]
    assert "summary" in json.loads((ctx.results_dir / "parity.json").read_text())


def test_cmd_quality_records_each_seat_and_raises_the_kld_todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ctx = _tools_harness(tmp_path, monkeypatch)
    tool = _fake_tool(tmp_path / "new-perplexity", "new")
    assert lt.cmd_quality(ctx, argparse.Namespace(perplexity=tool)) == 0
    recs, todos = _lines(capsys)
    assert recs == []  # a record with a to-do, never a verdict (S-03)
    title, detail = todos["kld-run"]
    assert "S-03" in title and "T-03" in title
    assert "gpt-oss-120b (q8_0 KV, -fa on): corpus PPL 7.11, tasks 4 of 6; reference: the F16-trunk GGUF" in detail
    assert "glm-5.3-flash (f16 KV, -fa on)" in detail and "deepseek-v4-flash (f16 KV, -fa on)" in detail
    assert "abliterated" not in detail and "router" not in detail and "embed" not in detail
    calls = _calls(tool)
    gpt = [c for c in calls if "/m/gpt-oss-120b.gguf" in c]
    assert len(gpt) == 2 and all(c[c.index("--cache-type-k") + 1] == "q8_0" and c[c.index("-fa") + 1] == "on"
                                 for c in gpt)
    saved = json.loads((ctx.results_dir / "quality.json").read_text())
    assert set(saved["engines"]) == {"gpt-oss-120b", "glm-5.3-flash", "deepseek-v4-flash"}
    # Without the tool: nothing is measured, the to-do still goes out and says why.
    assert lt.cmd_quality(ctx, argparse.Namespace(perplexity=str(tmp_path / "missing"))) == 0
    _, todos = _lines(capsys)
    assert "not measured:" in todos["kld-run"][1] and "is missing" in todos["kld-run"][1]
