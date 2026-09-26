"""engines: the llama-server client over a mock transport, the systemd controller over a fake runner; no services."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import httpx
import pytest

from atlas.config import EngineSpec
from atlas.engines import (
    EngineControlError,
    EngineError,
    LlamaClient,
    StubController,
    SystemdEngineController,
    Timings,
)

TIMINGS = {"cache_n": 0, "prompt_n": 512, "prompt_ms": 1000.0, "prompt_per_token_ms": 1.95,
           "prompt_per_second": 512.0, "predicted_n": 128, "predicted_ms": 4000.0, "predicted_per_token_ms": 31.25,
           "predicted_per_second": 32.0}


def _client(handler: Any) -> LlamaClient:
    return LlamaClient("http://127.0.0.1:8101", transport=httpx.MockTransport(handler))


def test_health_503_while_loading_then_200() -> None:
    state = {"ready": False}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/health"
        if not state["ready"]:
            return httpx.Response(503, json={"error": {"code": 503, "message": "Loading model",
                                                       "type": "unavailable_error"}})
        return httpx.Response(200, json={"status": "ok"})

    c = _client(handler)
    h = c.health()
    assert not h.ok and h.code == 503 and h.body["error"]["message"] == "Loading model"
    state["ready"] = True
    assert c.health().ok
    state["ready"] = False
    with pytest.raises(EngineError, match="did not answer 200"):
        c.wait_ready(3.0, poll_s=1.0, sleep=lambda s: None, clock=iter(range(0, 100)).__next__)


def test_health_connection_refused_is_not_ok() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    h = _client(handler).health()
    assert not h.ok and h.code == 0 and "ConnectError" in (h.error or "")


def test_chat_returns_text_and_timings() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        assert req.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "timings": TIMINGS,
        })

    r = _client(handler).chat([{"role": "user", "content": "Reply with OK."}], max_tokens=64, temperature=0)
    assert r.text == "OK" and r.finish_reason == "stop"
    assert isinstance(r.timings, Timings)
    assert r.timings.predicted_per_second == 32.0 and r.timings.prompt_per_second == 512.0  # decode / prefill tok/s
    assert r.timings.context_in_use == 640
    assert seen["body"]["stream"] is False and seen["body"]["max_tokens"] == 64 and seen["body"]["temperature"] == 0


def test_chat_stream_aggregates_deltas_and_final_timings() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}], "timings": {"predicted_n": 1}},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "timings": TIMINGS},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    deltas: list[str] = []
    r = _client(handler).chat([{"role": "user", "content": "hi"}], stream=True, on_delta=deltas.append)
    assert r.text == "Hello" and deltas == ["Hel", "lo"] and r.finish_reason == "stop"
    assert r.timings is not None and r.timings.predicted_n == 128


def test_chat_http_error_is_loud() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(EngineError, match="HTTP 500"):
        _client(handler).chat([{"role": "user", "content": "x"}])


def test_embeddings_and_rerank() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path == "/v1/embeddings":
            assert body["input"] == ["b", "a"]
            return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.1, 0.2]},
                                                     {"index": 0, "embedding": [0.3, 0.4]}]})
        if req.url.path == "/v1/rerank":
            assert body["query"] == "panda" and body["top_n"] == 2 and len(body["documents"]) == 2
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.1},
                                                         {"index": 1, "relevance_score": 0.9}]})
        return httpx.Response(404)

    c = _client(handler)
    assert c.embeddings(["b", "a"]) == [[0.3, 0.4], [0.1, 0.2]]  # ordered by index
    top = c.rerank("panda", ["hi", "The giant panda is a bear."], top_n=2)
    assert [t.index for t in top] == [1, 0] and top[0].relevance_score == 0.9


# --- systemd controller (CONVENTIONS.md §8 control path) --------------------------------------------------------------


class FakeRunner:
    def __init__(self, rc: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.rc, self.stderr = rc, stderr

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.rc, stdout="", stderr=self.stderr)


def test_systemd_controller_uses_plain_sudo_and_the_unit_name(engines: dict[str, EngineSpec]) -> None:
    runner = FakeRunner()
    ctl = SystemdEngineController(runner=runner)  # no engines map: no health re-check, no network
    ctl.start("gpt-oss-120b")
    ctl.stop("gpt-oss-120b")
    ctl.restart("nemotron-3-super")
    assert ctl.is_active("gpt-oss-120b") is True
    cmds = [c for c, _ in runner.calls]
    assert cmds == [
        ["sudo", "systemctl", "start", "llama-server@gpt-oss-120b"],
        ["sudo", "systemctl", "stop", "llama-server@gpt-oss-120b"],
        ["sudo", "systemctl", "restart", "llama-server@nemotron-3-super"],
        ["systemctl", "is-active", "--quiet", "llama-server@gpt-oss-120b"],  # status needs no sudo
    ]
    for cmd, kw in runner.calls:
        assert "-E" not in cmd  # adjudicated conflict 7: never sudo -E
        assert kw["stdin"] is subprocess.DEVNULL  # sudo-rs can never wait for a password
    _, kw = runner.calls[0]
    assert kw["timeout"] == 900.0  # TimeoutStartSec=900 in systemd/llama-server@.service


def test_systemd_controller_failure_names_the_journal() -> None:
    ctl = SystemdEngineController(runner=FakeRunner(rc=1, stderr="Job for llama-server@x.service failed"))
    with pytest.raises(EngineControlError, match=r"journalctl -u llama-server@qwen3\.5-122b"):
        ctl.start("qwen3.5-122b")
    assert ctl.is_active("qwen3.5-122b") is False
    with pytest.raises(EngineControlError, match="not in the sudoers fragment"):
        ctl._systemctl("enable", "x", 1.0)


def test_stub_controller_moves_the_probe(engines: dict[str, EngineSpec]) -> None:
    from atlas.arbiter import StubProbe

    probe = StubProbe(total_bytes=100, used_bytes=10)
    ctl = StubController(engines=engines, probe=probe, footprints={"gpt-oss-120b": 30})
    ctl.start("gpt-oss-120b")
    assert probe.used_bytes == 40 and ctl.is_active("gpt-oss-120b")
    ctl.stop("gpt-oss-120b")
    assert probe.used_bytes == 10 and not ctl.is_active("gpt-oss-120b")
    leaky = StubController(engines=engines, probe=probe, footprints={"gpt-oss-120b": 30}, leak_on_stop=True)
    leaky.start("gpt-oss-120b")
    leaky.stop("gpt-oss-120b")
    assert probe.used_bytes == 40
    with pytest.raises(EngineControlError):
        StubController(fail_start=["x"]).start("x")
