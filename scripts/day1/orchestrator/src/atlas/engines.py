"""Engines: the llama-server HTTP client and the controllers that start and stop engine units.

Facts typed here are VERIFIED in research/llama-cpp-vulkan.md (§3 flags, §6.1 timings, §7 readiness) and
research/gguf-models.md (§9 endpoints) unless marked UNVERIFIED:
  * GET /health -> 503 {"error":{"code":503,"message":"Loading model",...}} while loading, then 200 {"status":"ok"}
    when ready.
  * POST /v1/chat/completions carries a top-level "timings" object (cache_n, prompt_n, prompt_ms, prompt_per_second,
    predicted_n, predicted_ms, predicted_per_second); streams carry per-token timings with "timings_per_token": true.
  * POST /v1/embeddings (OpenAI shape) on the --embedding server; POST /v1/rerank {"model","query","documents","top_n"}
    on the --reranking server.
  * The control path (CONVENTIONS.md §8): `sudo systemctl start|stop|restart llama-server@<key>` under
    /etc/sudoers.d/atlas-engines; plain sudo, never `sudo -E` (adjudicated conflict 7); `systemctl is-active` needs
    no sudo.
    llama-server@.service's ExecStartPost polls /health, so `systemctl start` returns only when the engine can serve.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from atlas.config import EngineSpec

log = logging.getLogger("atlas.engines")

SYSTEMCTL = "systemctl"
SUDO = "sudo"
UNIT_PREFIX = "llama-server@"


class EngineError(RuntimeError):
    """An engine did not do what was asked; the message says which engine and what the server or systemd said."""


class EngineControlError(EngineError):
    pass


# --- llama-server client ----------------------------------------------------------------------------------------------


class Timings(BaseModel):
    """The `timings` object of a llama-server completion (research §6.1, VERIFIED field names)."""

    model_config = ConfigDict(extra="ignore")

    cache_n: int = 0
    prompt_n: int = 0
    prompt_ms: float = 0.0
    prompt_per_token_ms: float = 0.0
    prompt_per_second: float = 0.0  # prefill tok/s
    predicted_n: int = 0
    predicted_ms: float = 0.0
    predicted_per_token_ms: float = 0.0
    predicted_per_second: float = 0.0  # decode tok/s

    @property
    def context_in_use(self) -> int:
        return self.prompt_n + self.cache_n + self.predicted_n


@dataclass
class HealthStatus:
    ok: bool
    code: int
    body: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class ChatResult:
    text: str
    timings: Timings | None
    finish_reason: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float


class LlamaClient:
    """Synchronous client for one llama-server instance (127.0.0.1:<port>)."""

    def __init__(self, base_url: str, *, timeout_s: float = 600.0, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Local engines only: never through the allowlist proxy (trust_env=False ignores HTTPS_PROXY for 127.0.0.1).
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, headers=headers, trust_env=False,
                                  transport=transport)

    @classmethod
    def for_engine(cls, spec: EngineSpec, **kw: Any) -> LlamaClient:
        return cls(spec.base_url, **kw)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LlamaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- readiness -------------------------------------------------------------------------------------------------

    def health(self) -> HealthStatus:
        try:
            r = self._http.get("/health", timeout=5.0)
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, code=0, error=f"{type(exc).__name__}: {exc}")
        body: dict[str, Any] | None
        try:
            body = r.json()
        except ValueError:
            body = None
        return HealthStatus(ok=(r.status_code == 200), code=r.status_code, body=body)

    def wait_ready(self, timeout_s: float, poll_s: float = 2.0, sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic) -> HealthStatus:
        deadline = clock() + timeout_s
        last = self.health()
        while not last.ok and clock() < deadline:
            sleep(poll_s)
            last = self.health()
        if not last.ok:
            raise EngineError(f"{self.base_url}/health did not answer 200 within {timeout_s:.0f}s "
                              f"(last: {last.code} {last.error or last.body})")
        return last

    def props(self) -> dict[str, Any]:
        r = self._http.get("/props")
        r.raise_for_status()
        return r.json()

    def slots(self) -> list[dict[str, Any]]:
        r = self._http.get("/slots")
        r.raise_for_status()
        return r.json()

    def tokenize(self, content: str) -> list[int]:
        r = self._http.post("/tokenize", json={"content": content})
        r.raise_for_status()
        return list(r.json().get("tokens", []))

    # --- generation ------------------------------------------------------------------------------------------------

    def chat(self, messages: Sequence[dict[str, Any]], stream: bool = False, *, model: str = "any",
             max_tokens: int | None = None, temperature: float | None = None,
             on_delta: Callable[[str], None] | None = None, **params: Any) -> ChatResult:
        """POST /v1/chat/completions; returns the text and the server's timings object.

        stream=True consumes the SSE stream (calling on_delta per text delta) and still returns the aggregate; the
        timings come from the final chunk, which llama-server sends with "timings" when the generation finishes.
        """
        body: dict[str, Any] = {"model": model, "messages": list(messages), "stream": stream, **params}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if not stream:
            r = self._http.post("/v1/chat/completions", json=body)
            _raise_for_status(r, "chat")
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            timings = Timings.model_validate(data["timings"]) if isinstance(data.get("timings"), dict) else None
            return ChatResult(text=text, timings=timings, finish_reason=choice.get("finish_reason"), raw=data)
        body.setdefault("timings_per_token", True)
        parts: list[str] = []
        timings: Timings | None = None
        finish: str | None = None
        last: dict[str, Any] = {}
        with self._http.stream("POST", "/v1/chat/completions", json=body) as r:
            _raise_for_status(r, "chat(stream)")
            for chunk in _sse_events(r.iter_lines()):
                last = chunk
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        parts.append(delta)
                        if on_delta is not None:
                            on_delta(delta)
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                if isinstance(chunk.get("timings"), dict):
                    timings = Timings.model_validate(chunk["timings"])
        return ChatResult(text="".join(parts), timings=timings, finish_reason=finish, raw=last)

    def embeddings(self, inputs: Sequence[str] | str, *, model: str = "any") -> list[list[float]]:
        """POST /v1/embeddings on the --embedding server; one vector per input, in order."""
        r = self._http.post("/v1/embeddings", json={"model": model, "input": inputs})
        _raise_for_status(r, "embeddings")
        data = r.json().get("data") or []
        data.sort(key=lambda d: d.get("index", 0))
        return [list(map(float, d["embedding"])) for d in data]

    def rerank(self, query: str, documents: Sequence[str], *, top_n: int | None = None,
               model: str = "any") -> list[RerankResult]:
        """POST /v1/rerank on the --reranking server; results sorted by score, highest first."""
        body: dict[str, Any] = {"model": model, "query": query, "documents": list(documents)}
        if top_n is not None:
            body["top_n"] = top_n
        r = self._http.post("/v1/rerank", json=body)
        _raise_for_status(r, "rerank")
        results = r.json().get("results") or []
        out = [RerankResult(index=int(x["index"]), relevance_score=float(x.get("relevance_score", 0.0)))
               for x in results]
        out.sort(key=lambda x: x.relevance_score, reverse=True)
        return out


def _raise_for_status(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        try:
            detail = r.read().decode("utf-8", "replace")[:500]
        except httpx.HTTPError:
            detail = ""
        raise EngineError(f"{what}: {r.request.url} -> HTTP {r.status_code} {detail}")


def _sse_events(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            log.warning("llama-server sent a non-JSON SSE line: %r", payload[:200])


# --- controllers ------------------------------------------------------------------------------------------------------


class EngineController(Protocol):
    """Starts and stops weight-bearing processes. Only the Arbiter calls it (Section 4.2)."""

    def start(self, key: str) -> None: ...

    def stop(self, key: str) -> None: ...

    def is_active(self, key: str) -> bool: ...


class SystemdEngineController:
    """CONVENTIONS.md §8 control path: `sudo systemctl start|stop|restart llama-server@<key>` as user atlas."""

    ALLOWED = ("start", "stop", "restart")

    def __init__(self, *, engines: dict[str, EngineSpec] | None = None, start_timeout_s: float = 900.0,
                 stop_timeout_s: float = 180.0, ready_timeout_s: float = 60.0, sudo: bool = True,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.engines = engines or {}
        self.start_timeout_s = start_timeout_s  # matches TimeoutStartSec=900 in systemd/llama-server@.service
        self.stop_timeout_s = stop_timeout_s  # TimeoutStopSec=120 plus margin
        self.ready_timeout_s = ready_timeout_s
        self.sudo = sudo
        self._run = runner

    def _systemctl(self, verb: str, key: str, timeout_s: float) -> None:
        if verb not in self.ALLOWED:
            raise EngineControlError(f"systemctl {verb} is not in the sudoers fragment (only {self.ALLOWED})")
        unit = f"{UNIT_PREFIX}{key}"
        # Plain `sudo`, no -E (conflict 7); stdin closed so sudo-rs can never wait for a password.
        cmd = ([SUDO] if self.sudo else []) + [SYSTEMCTL, verb, unit]
        try:
            proc = self._run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s,
                             check=False)
        except subprocess.TimeoutExpired as exc:
            raise EngineControlError(f"{' '.join(cmd)} did not return within {timeout_s:.0f}s") from exc
        except OSError as exc:
            raise EngineControlError(f"{' '.join(cmd)} could not run: {exc}") from exc
        if proc.returncode != 0:
            raise EngineControlError(f"{' '.join(cmd)} failed (exit {proc.returncode}): "
                                     f"{(proc.stderr or proc.stdout).strip()[:800]} "
                                     f"(see: journalctl -u {unit} -n 60)")

    def start(self, key: str) -> None:
        self._systemctl("start", key, self.start_timeout_s)
        spec = self.engines.get(key)
        if spec is not None and spec.port:
            # ExecStartPost already waited for /health 200; a short re-check catches a unit that exited right after.
            with LlamaClient.for_engine(spec) as client:
                client.wait_ready(self.ready_timeout_s)
        log.info("engine started: %s", key)

    def stop(self, key: str) -> None:
        self._systemctl("stop", key, self.stop_timeout_s)
        log.info("engine stopped: %s (process exit; memory release is confirmed by the Arbiter)", key)

    def restart(self, key: str) -> None:
        self._systemctl("restart", key, self.start_timeout_s)

    def is_active(self, key: str) -> bool:
        unit = f"{UNIT_PREFIX}{key}"
        try:
            proc = self._run([SYSTEMCTL, "is-active", "--quiet", unit], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineControlError(f"systemctl is-active {unit} could not run: {exc}") from exc
        return proc.returncode == 0


class StubController:
    """Test double: records calls and, when given a StubProbe, moves the fake memory counter like a real load.

    `leak_on_stop` keeps the counter high after stop, which is how tests provoke the release-confirmation timeout
    (Section 4.2 rule 5). `fail_start` names keys whose start raises, for the fail-loudly path.
    """

    def __init__(self, *, engines: dict[str, EngineSpec] | None = None, probe: Any = None,
                 footprints: dict[str, int] | None = None, leak_on_stop: bool = False,
                 fail_start: Sequence[str] = ()) -> None:
        self.engines = engines or {}
        self.probe = probe
        self.footprints = dict(footprints or {})
        self.leak_on_stop = leak_on_stop
        self.fail_start = set(fail_start)
        self.active: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _bytes(self, key: str) -> int:
        if key in self.footprints:
            return self.footprints[key]
        spec = self.engines.get(key)
        return spec.footprint_bytes if spec is not None else 0

    def start(self, key: str) -> None:
        self.calls.append(("start", key))
        if key in self.fail_start:
            raise EngineControlError(f"stub: start of {key} refused (test)")
        self.active.add(key)
        if self.probe is not None:
            self.probe.used_bytes += self._bytes(key)

    def stop(self, key: str) -> None:
        self.calls.append(("stop", key))
        self.active.discard(key)
        if self.probe is not None and not self.leak_on_stop:
            self.probe.used_bytes = max(0, self.probe.used_bytes - self._bytes(key))

    def is_active(self, key: str) -> bool:
        return key in self.active
