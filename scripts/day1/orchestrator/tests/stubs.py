"""Test doubles for what must never be live in tests (CONVENTIONS.md §7.8): llama-server (StubLlama), the engine
controller and the GTT probe behind the real Arbiter (make_arbiter), the atlas-vault helper (FakeVaultRunner). The
router, the prompt builder and the approval queue are the real modules; their own doubles (StubClassifier, StubSender,
StubNotifier) come from atlas.router / atlas.approval. Only tests import this file."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from atlas.arbiter import GIB, Arbiter, StubProbe
from atlas.config import EngineSpec
from atlas.engines import EngineError, StubController

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
    """An EngineStreamer that yields OpenAI chunks for `text`, or raises when `fail` is set."""

    def __init__(self, text: str = "Stub answer.", *, fail: str | None = None, spec: EngineSpec | None = None) -> None:
        self.text, self.fail, self.spec = text, fail, spec
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
        yield {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "timings": {"prompt_n": 10, "predicted_n": len(words), "predicted_per_second": 30.0},
        }


@dataclass
class StubLlamaFactory:
    text: str = "Stub answer from the engine."
    fail: str | None = None
    made: list[StubLlama] = field(default_factory=list)

    def __call__(self, spec: EngineSpec) -> StubLlama:
        s = StubLlama(self.text, fail=self.fail, spec=spec)
        self.made.append(s)
        return s


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
