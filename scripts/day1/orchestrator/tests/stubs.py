"""Test doubles for what must never be live in tests (CONVENTIONS.md §7.8): llama-server (StubLlama), the engine
controller and the GTT probe behind the real Arbiter (make_arbiter), the atlas-vault helper (FakeVaultRunner). The
router, the prompt builder and the approval queue are the real modules; their own doubles (StubClassifier, StubSender,
StubNotifier) come from atlas.router / atlas.approval. Only tests import this file."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from atlas.arbiter import GIB, Arbiter, StubProbe
from atlas.config import EngineSpec
from atlas.engines import CHAT_PATH, COUNT_PATH, EngineError, StubController, image_parts

GTT_TOTAL = 196608 * 1024 * 1024
RESIDENT_SET = 22 * GIB


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 0.001
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_arbiter(
    engines: dict[str, EngineSpec], *, budget_gib: float = 170.0, ledger: Any = None
) -> tuple[Arbiter, StubController, StubProbe]:
    probe = StubProbe(total_bytes=int(RESIDENT_SET + budget_gib * GIB), used_bytes=RESIDENT_SET)
    controller = StubController(engines=engines, probe=probe)
    clock = FakeClock()
    arb = Arbiter(
        engines,
        controller,
        probe,
        ledger=ledger,
        release_timeout_s=60.0,
        poll_interval_s=1.0,
        clock=clock,
        sleep=clock.sleep,
    )
    arb.measure_resident_set()
    return arb, controller, probe


class StubLlama:
    """An EngineStreamer that yields OpenAI chunks for `text`, or raises when `fail` is set. With a `gate`
    (threading.Event) the stream emits its first word, sets `started`, and holds the generation until the gate is set:
    that is how a test keeps one generation running while another request arrives."""

    def __init__(
        self,
        text: str = "Stub answer.",
        *,
        fail: str | None = None,
        spec: EngineSpec | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.text, self.fail, self.spec = text, fail, spec
        self.gate = gate
        self.started = threading.Event()
        self.requests: list[dict[str, Any]] = []

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        self.requests.append({"messages": list(messages), "temperature": temperature, "max_tokens": max_tokens})
        if self.fail:
            raise EngineError(self.fail)
        words = self.text.split(" ")
        for i, w in enumerate(words):
            piece = w if i == len(words) - 1 else w + " "
            yield {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            if i == 0:
                self.started.set()
                if self.gate is not None and not self.gate.wait(timeout=10.0):
                    raise EngineError("stub gate never opened")
        yield {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "timings": {"prompt_n": 10, "predicted_n": len(words), "predicted_per_second": 30.0},
        }


@dataclass
class StubLlamaFactory:
    text: str = "Stub answer from the engine."
    fail: str | None = None
    made: list[StubLlama] = field(default_factory=list)
    gates: dict[str, threading.Event] = field(default_factory=dict)  # engine key -> gate for a blocking stream

    def __call__(self, spec: EngineSpec) -> StubLlama:
        s = StubLlama(self.text, fail=self.fail, spec=spec, gate=self.gates.get(spec.key))
        self.made.append(s)
        return s


def _words(content: Any) -> int:
    if isinstance(content, list):
        return sum(len(str(p.get("text", "")).split()) for p in content if isinstance(p, Mapping))
    return len(str(content or "").split())


def stub_prompt_tokens(body: Mapping[str, Any], *, per_image: int = 1000) -> int:
    """A deterministic stand-in for llama-server's own prompt count (S52 tests): 3, plus 4 and the word count per
    message, plus `per_image` per image part (the real COUNT_PATH counts image tokens exactly; this one just must)."""
    messages = body.get("messages") or []
    return 3 + sum(4 + _words(m.get("content")) for m in messages) + per_image * image_parts(messages)


def counting(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap a stub llama-server handler with the count route every generation now asks first (S52): COUNT_PATH answers
    stub_prompt_tokens; every other request goes to `handler`, which therefore sees the generation requests only."""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == COUNT_PATH:
            return httpx.Response(200, json={"object": "response.input_tokens",
                                             "input_tokens": stub_prompt_tokens(json.loads(request.content))})
        return handler(request)

    return wrapped


class StubLlamaServer:
    """A llama-server behind httpx.MockTransport for the S52 tests: COUNT_PATH (stub_prompt_tokens, or 404 with
    count_route=False), /apply-template + /tokenize (one token per word of the rendered prompt) and CHAT_PATH (SSE or
    JSON). Every request is recorded in order. A generation is refused with HTTP 500 and recorded in `unguarded` when
    its messages were not counted since the previous generation or it carries no max_tokens: that is how a test proves
    a path went through engines.LlamaClient._open_chat. With `n_ctx`, a prompt + max_tokens that would run past the
    slot end is recorded in `overflows` (the real server would stop the reply there with finish "length")."""

    def __init__(self, text: str = "Stub answer from the engine.", *, n_ctx: int | None = None,
                 count_route: bool = True, per_image: int = 1000) -> None:
        self.text = text
        self.n_ctx = n_ctx
        self.count_route = count_route
        self.per_image = per_image
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.unguarded: list[dict[str, Any]] = []
        self.overflows: list[dict[str, Any]] = []
        self._counted: list[list[Any]] = []
        self._lock = threading.Lock()

    @property
    def generations(self) -> list[dict[str, Any]]:
        return [b for p, b in self.requests if p == CHAT_PATH]

    @property
    def counts(self) -> list[dict[str, Any]]:
        return [b for p, b in self.requests if p == COUNT_PATH]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        with self._lock:
            self.requests.append((path, body))
            if path == COUNT_PATH:
                if not self.count_route:
                    return httpx.Response(404, text="File Not Found")
                self._counted.append(body.get("messages"))
                return httpx.Response(200, json={"object": "response.input_tokens",
                                                 "input_tokens": stub_prompt_tokens(body, per_image=self.per_image)})
            if path == "/apply-template":
                self._counted.append(body.get("messages"))
                rendered = " ".join(f"<{m.get('role')}> " + " ".join(
                    str(p.get("text", "<__media__>")) for p in m["content"] if isinstance(p, Mapping))
                    if isinstance(m.get("content"), list) else f"<{m.get('role')}> {m.get('content') or ''}"
                    for m in body.get("messages") or [])
                return httpx.Response(200, json={"prompt": rendered})
            if path == "/tokenize":
                return httpx.Response(200, json={"tokens": list(range(len(str(body.get("content", "")).split())))})
            if path != CHAT_PATH:
                return httpx.Response(404, text="File Not Found")
            if body.get("messages") not in self._counted or not isinstance(body.get("max_tokens"), int):
                self.unguarded.append(body)
                return httpx.Response(500, json={"error": {"code": 500, "message": "unguarded generation (test)"}})
            self._counted = []
            if self.n_ctx is not None and stub_prompt_tokens(body, per_image=self.per_image) + body["max_tokens"] \
                    >= self.n_ctx:
                self.overflows.append(body)
        if not body.get("stream"):
            return httpx.Response(200, json={
                "choices": [{"index": 0, "message": {"role": "assistant", "content": self.text},
                             "finish_reason": "stop"}],
                "timings": {"prompt_n": 10, "predicted_n": 5}})
        words = self.text.split(" ")
        events = [{"choices": [{"index": 0, "delta": {"content": w if i == len(words) - 1 else w + " "},
                                "finish_reason": None}]} for i, w in enumerate(words)]
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "timings": {"prompt_n": 10, "predicted_n": len(words)}})
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})


class FakeVaultRunner:
    """Stands in for subprocess.run on the atlas-vault helper; records stdin so the passphrase path is provable."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.calls: list[tuple[list[str], str | None]] = []
        self.state = "locked"

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kw.get("input")))
        verb = argv[-1]
        if verb == "open":
            if self.refuse:
                return subprocess.CompletedProcess(argv, 1, "", "atlas-vault open: refused")
            self.state = "open"
            return subprocess.CompletedProcess(argv, 0, "open\n", "")
        if verb == "lock":
            self.state = "locked"
            return subprocess.CompletedProcess(argv, 0, "locked\n", "")
        return subprocess.CompletedProcess(argv, 0, f"{self.state}\n", "")
