"""scripts/day1/phase3/loadtest.py, the pure parts (CONVENTIONS.md §7.8: no live services; llama-server, systemd,
journald, the GTT counter and the orchestrator are stubbed). Imported by path: loadtest.py is a script, not part of
the atlas package."""

from __future__ import annotations

import importlib.util
import json
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


def test_v10_line_crash_at_8k_is_a_fail() -> None:
    res = _good("nemotron-3-super")
    res.crashed_at_8k = True
    res.load_error = f"8k prefill crashed the server ({lt.NEMOTRON_ISSUE})"
    result, msg = lt.v10_line(res, SPEC)
    assert result == "fail" and "#20732" in msg


def test_v10_line_external_stop_is_a_fail_not_a_crash() -> None:
    res = _good()
    res.stopped_externally = True
    res.load_error = "server stopped externally during the 8k prefill"
    assert lt.v10_line(res, SPEC)[0] == "fail"
    assert lt.stopped_externally("... Stopping llama-server@x.service...") and not lt.stopped_externally("boom")


def test_check_band_verdicts() -> None:
    res = _good()
    res.decode_tps_512 = 20.0  # < 0.7 x 30
    lt.check_band(res, SPEC)
    assert res.band_fail and lt.v10_line(res, SPEC)[0] == "fail"
    res = _good()
    res.decode_tps_512 = 25.0  # between 21 and 30
    lt.check_band(res, SPEC)
    assert not res.band_fail and "under the expected band" in res.band_note and res.warnings
    assert lt.v10_line(res, SPEC)[0] == "pass"
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
    {"key": "deepseek-v4-flash", "arbiter_class": "apex", "kv_ladder": ["q4_0", "q8_0", "f16"], "kv_class": "q4_0",
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


# --- the ladder decision ----------------------------------------------------------------------------------------------

LADDER = ["q4_0", "q8_0", "f16"]


def _rung(kv: str, load_ok: bool, coherent: bool, proof: bool = True) -> dict[str, Any]:
    return {"kv": kv, "load_ok": load_ok, "coherent": coherent, "kv_proof_ok": proof and load_ok}


def test_ladder_q4_win_keeps_baseline() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("q4_0", True, True)], "q4_0", 16384, "", True, "", 28.0,
                         500.0)
    assert v.winner == "q4_0" and v.v4_result == "pass" and v.clear_ov == ["kv_type", "ctx_size"] and not v.set_ov
    assert v.v22_result == "pass" and "q8_0=not tried" in v.summary and not v.deviation


def test_ladder_q8_win_names_the_incoherent_baseline() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("q4_0", True, False), _rung("q8_0", True, True)],
                         "q4_0", 16384, "", True, "", 28.0, 500.0)
    assert v.winner == "q8_0" and v.v4_result == "pass"
    assert "q8_0 applied (Section 4.3 setting q4_0 incoherent: q4_0=incoherent" in v.v4_msg
    assert v.set_ov == [("kv_type", "q8_0")] and v.clear_ov == ["ctx_size"] and v.deviation


def test_ladder_f16_win_defers_v4_and_passes_v22_with_cap() -> None:
    rungs = [_rung("q4_0", False, False), _rung("q8_0", True, False), _rung("f16", True, True)]
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, rungs, "q4_0", 16384, "", True, "", 20.0, 400.0)
    assert v.winner == "f16" and v.v4_result == "deferred" and "unquantised KV" in v.v4_msg
    assert v.v22_result == "pass" and "unquantised KV" in v.v22_msg
    assert ("ctx_size", "16384") in v.set_ov and ("kv_type", "f16") in v.set_ov
    assert "q4_0=load failed" in v.summary and "q8_0=incoherent" in v.summary


def test_ladder_no_winner_defers_both_and_first_coherent_wins() -> None:
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung(kv, True, False) for kv in LADDER], "q4_0", 16384,
                         "", True, "", None, None)
    assert v.winner == "" and v.v4_result == "deferred" and v.v22_result == "deferred"
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("q4_0", True, True), _rung("q8_0", True, True)],
                         "q4_0", 16384, "", True, "", 1.0, 1.0)
    assert v.winner == "q4_0"
    v = lt.decide_ladder("deepseek-v4-flash", LADDER, [_rung("q4_0", True, True)], "q4_0", 16384, "", False,
                         "GTT still 200 GB", 1.0, 1.0)
    assert v.v22_result == "deferred" and "GTT still 200 GB" in v.v22_msg


class FakeControl:
    def __init__(self, fail_loads: set[str]) -> None:
        self.mode = "orchestrator"
        self.fallback_reason = ""
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


def test_ladder_engine_continues_after_a_load_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, ENGINES)
    rendered: list[tuple[list[tuple[str, str]], list[str]]] = []
    current = {"kv": ""}

    def fake_render(_key: str, set_ov: Any = (), clear_ov: Any = ()) -> dict[str, str]:
        rendered.append((list(set_ov), list(clear_ov)))
        for fld, val in set_ov:
            if fld == "kv_type":
                current["kv"] = val
        return {"ATLAS_MODEL_PRESENT": "1", "LLAMA_ARG_PORT": "8105", "ATLAS_CTX_SIZE": "32768", "ATLAS_PARALLEL": "1",
                "ATLAS_KV_TYPE": current["kv"]}

    ctl = FakeControl(fail_loads={"q4_0"})
    monkeypatch.setattr(ctx, "render", fake_render)
    # Control.load sees the key; the fake reads the rung through the rendered kv type instead.
    monkeypatch.setattr(ctl, "load", lambda _k, c, p, port: FakeControl.load(ctl, f"x:{current['kv']}", c, p, port))
    monkeypatch.setattr(lt, "unit_active", lambda _k: False)
    monkeypatch.setattr(lt, "unit_invocation_id", lambda _k: "inv")
    monkeypatch.setattr(lt, "prove_kv", lambda key, kv, *_a: (True, f"{key}: {kv} applied", kv))
    monkeypatch.setattr(lt, "coherence_check", lambda _port, _model: (True, "fine"))
    monkeypatch.setattr(lt, "swap_out", lambda *_a: (0.0, "no previous engine"))
    monkeypatch.setattr(lt, "unload_and_release", lambda *_a: (True, 5.0, "GTT back"))
    monkeypatch.setattr(lt, "gtt_used_bytes", lambda: 160 * lt.GB)

    def fake_measure(_ctx: Any, res: Any, *_a: Any) -> None:
        res.generated, res.decode_tps_512, res.prefill_tps_512 = True, 27.0, 600.0

    monkeypatch.setattr(lt, "run_measurements", fake_measure)
    res = lt.EngineResult(key="deepseek-v4-flash", arbiter_class="apex")
    lt.ladder_engine(ctx, ctl, "deepseek-v4-flash", None, 10 * lt.GB, res)
    assert ctl.loads == ["q4_0", "q8_0"]  # q4_0 load failed -> continued; q8_0 won -> stopped (rule 2, 3)
    assert res.kv_applied == "q8_0" and res.ok and res.v22_result == "pass"
    assert [r["kv"] for r in res.ladder] == ["q4_0", "q8_0"] and res.ladder[0]["note"].startswith("load failed")
    assert rendered[-1] == ([("kv_type", "q8_0")], ["ctx_size"])
    assert res.footprint_bytes == 150 * lt.GB and ctl.registered == [150 * lt.GB]
    recs = _records(capsys)
    assert recs[-1][0] == "V4" and recs[-1][1] == "pass"
    assert "Section 4.3 setting q4_0 load failed: q4_0=load failed" in recs[-1][2]
    saved = json.loads(ctx.result_path("deepseek-v4-flash").read_text())
    assert saved["control_mode"] == "orchestrator" and saved["baseline_deviation"].startswith("KV q8_0")


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
