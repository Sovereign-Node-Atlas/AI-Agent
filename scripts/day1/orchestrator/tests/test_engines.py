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


def test_llama_client_dials_loopback_only() -> None:
    # Rule §7.1: the client ignores HTTPS_PROXY (trust_env=False), which is legitimate only for the loopback
    # llama-server instances of §8; anything else is refused before a request exists, so no module can use it to reach
    # past the allowlist proxy at the library level.
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"status": "ok"})

    for bad in ("http://10.0.0.5:8101", "https://example.com", "https://127.0.0.1:8101", "http://192.168.1.10:8101",
                "http://host.docker.internal:8101", "ftp://127.0.0.1:8101"):
        with pytest.raises(EngineError, match="loopback llama-server instances only"):
            LlamaClient(bad, transport=httpx.MockTransport(handler))
    assert calls == []
    for ok in ("http://127.0.0.1:8101", "http://localhost:8101/", "http://[::1]:8101", "http://LOCALHOST:8101"):
        assert LlamaClient(ok, transport=httpx.MockTransport(handler)).health().ok
    assert len(calls) == 4
    spec = EngineSpec(key="x", mode="chat", arbiter_class="core", kv_class="q4_0", footprint_gb=1, ctx_size=1,
                      parallel=1, index=1, port=8101)
    assert LlamaClient.for_engine(spec).base_url == "http://127.0.0.1:8101"


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
        ["sudo", "-n", "systemctl", "start", "llama-server@gpt-oss-120b"],
        ["sudo", "-n", "systemctl", "stop", "llama-server@gpt-oss-120b"],
        ["sudo", "-n", "systemctl", "restart", "llama-server@nemotron-3-super"],
        ["systemctl", "is-active", "--quiet", "llama-server@gpt-oss-120b"],  # status needs no sudo
    ]
    for cmd, kw in runner.calls:
        assert "-E" not in cmd  # adjudicated conflict 7: never sudo -E; -n is non-interactive (sudo and sudo-rs)
        assert kw["stdin"] is subprocess.DEVNULL  # sudo-rs can never wait for a password
    _, kw = runner.calls[0]
    # Python's deadline is STRICTLY longer than the unit's TimeoutStartSec=900 (systemd/llama-server@.service) so
    # systemd's timeout fires first and the exit-code path with the journalctl hint is taken; were it shorter,
    # subprocess.run would try to kill root's sudo child and report EPERM instead of the cause (fix round).
    assert kw["timeout"] == 960.0 == 900.0 + SystemdEngineController.START_TIMEOUT_MARGIN_S
    assert ctl.start_timeout_s > 900.0 and ctl.stop_timeout_s > 120.0  # TimeoutStopSec=120
    _, kw_stop = runner.calls[1]
    assert kw_stop["timeout"] == ctl.stop_timeout_s == 180.0


def test_systemd_controller_failure_names_the_journal() -> None:
    ctl = SystemdEngineController(runner=FakeRunner(rc=1, stderr="Job for llama-server@x.service failed"))
    with pytest.raises(EngineControlError, match=r"journalctl -u llama-server@qwen3\.5-122b"):
        ctl.start("qwen3.5-122b")
    assert ctl.is_active("qwen3.5-122b") is False
    with pytest.raises(EngineControlError, match="not in the sudoers fragment"):
        ctl._systemctl("enable", "x", 1.0)


def test_systemd_controller_refuses_keys_that_are_not_bare_unit_names(engines: dict[str, EngineSpec]) -> None:
    # sudoers matches arguments as one string: `llama-server@*` would have permitted `stop llama-server@x ufw.service`.
    # The installers write explicit lines; this side never builds such a command in the first place.
    runner = FakeRunner()
    ctl = SystemdEngineController(runner=runner)
    for bad in ("x ufw.service atlas-aegis.timer", "x\tufw.service", "", "../x", "a;b", "x ufw.service"):
        with pytest.raises(EngineControlError, match="not a bare unit instance name"):
            ctl.stop(bad)
        with pytest.raises(EngineControlError, match="not a bare unit instance name"):
            ctl.is_active(bad)
    assert runner.calls == []  # refused before sudo or systemctl ran
    with_map = SystemdEngineController(engines=engines, runner=runner)
    with pytest.raises(EngineControlError, match=r"not in engines\.json"):
        with_map.start("llama-4-does-not-exist")
    assert runner.calls == []


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
    with pytest.raises(EngineError) as ei:  # the post-start /health failure of SystemdEngineController.start
        StubController(fail_start=["x"], start_error=EngineError).start("x")
    assert type(ei.value) is EngineError
    stuck = StubController(engines=engines, probe=probe, footprints={"gpt-oss-120b": 30}, fail_stop=["gpt-oss-120b"])
    stuck.start("gpt-oss-120b")
    with pytest.raises(EngineControlError, match="password is required"):
        stuck.stop("gpt-oss-120b")
    assert stuck.is_active("gpt-oss-120b") and probe.used_bytes == 70  # the unit kept running, the counter stayed up
