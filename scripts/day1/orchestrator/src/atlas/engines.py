"""Engines: the llama-server HTTP client and the controllers that start and stop engine units.

Facts typed here are VERIFIED in research/llama-cpp-vulkan.md (§3 flags, §6.1 timings, §7 readiness) and
research/gguf-models.md (§9 endpoints) unless marked UNVERIFIED:
  * GET /health -> 503 {"error":{"code":503,"message":"Loading model",...}} while loading, then 200 {"status":"ok"}
    when ready.
  * POST /v1/chat/completions carries a top-level "timings" object (cache_n, prompt_n, prompt_ms, prompt_per_second,
    predicted_n, predicted_ms, predicted_per_second); streams carry per-token timings with "timings_per_token": true.
  * POST /v1/embeddings (OpenAI shape) on the --embedding server; POST /v1/rerank {"model","query","documents","top_n"}
    on the --reranking server.
  * The control path (CONVENTIONS.md §8): `sudo -n systemctl start|stop|restart llama-server@<key>` under
    /etc/sudoers.d/atlas-engines; plain sudo, never `sudo -E` (adjudicated conflict 7); `-n` (non-interactive, accepted
    by sudo and sudo-rs) makes a missing or mismatched fragment fail at once with "a password is required" instead of
    an askpass attempt; `systemctl is-active` needs no sudo.
    llama-server@.service's ExecStartPost polls /health, so `systemctl start` returns only when the engine can serve.

Privilege boundary, stated plainly (fix round):
  * sudoers(5) matches command-line arguments as ONE space-separated string, so a `llama-server@*` line would also
    permit `stop llama-server@x ufw.service`. The installers (phase2/01-llama.sh, 02-orchestrator.sh) therefore write
    one explicit line per verb and engine key (30 lines, no wildcard), and this module refuses any key that is not a
    bare unit instance name ([A-Za-z0-9._-]+) and, when it knows engines.json, any key that is not in it — before
    sudo is ever called. Both checks hold whether the installed sudo is sudo or sudo-rs.
  * `atlas` is in the `docker` group for the AEGIS sandbox (CONVENTIONS.md §2, Section 16.4; atlas-orchestrator.service
    SupplementaryGroups=docker). Docker-socket access is root-equivalent on the host (`docker run --privileged -v
    /:/host`), so the sudoers fragment bounds ACCIDENTS (a wrong key, a bug), not a hostile or prompt-injected atlas
    process. The real boundaries for that case are the sandbox caps (memory, cpu, pids, --network none, timeout, no
    docker.sock mount — atlas.sandbox, another writer's module, must assert the last one in code) and the approval
    gate (Section 16.2).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from atlas.config import EngineSpec

log = logging.getLogger("atlas.engines")

SYSTEMCTL = "systemctl"
SUDO = "sudo"
UNIT_PREFIX = "llama-server@"
# A key becomes a sudoers command argument and a systemd instance name: the same alphabet phase2/01-llama.sh enforces.
UNIT_KEY_RE = re.compile(r"[A-Za-z0-9._-]+")


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

    # llama-server binds loopback (CONVENTIONS.md §8 ports); only these hosts may be dialled with the proxy ignored.
    LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

    def __init__(self, base_url: str, *, timeout_s: float = 600.0, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        # Rule §7.1: every outbound request goes through the allowlist proxy. This client sets trust_env=False (so
        # HTTPS_PROXY is ignored), which is only legitimate for a loopback llama-server; refuse anything else BEFORE a
        # request is built, so no module can use it to reach past the proxy at the library level (fix round).
        parts = urlsplit(self.base_url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "http" or host not in self.LOOPBACK_HOSTS:
            raise EngineError(f"LlamaClient dials loopback llama-server instances only (http://127.0.0.1:<port>, §8); "
                              f"refusing {base_url!r}: it would bypass the allowlist proxy (rule §7.1)")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
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

    # Python's deadline must be STRICTLY longer than the unit's own (TimeoutStartSec=900, TimeoutStopSec=120 in
    # systemd/llama-server@.service) so systemd's timeout always fires first and the exit-code path below (with the
    # journalctl hint) is taken. Were Python's shorter, subprocess.run would try to kill a setuid sudo child owned by
    # root, get EPERM, and report that instead of the real cause (fix round).
    START_TIMEOUT_MARGIN_S = 60.0

    def __init__(self, *, engines: dict[str, EngineSpec] | None = None, start_timeout_s: float = 960.0,
                 stop_timeout_s: float = 180.0, ready_timeout_s: float = 60.0, sudo: bool = True,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.engines = engines or {}
        self.start_timeout_s = start_timeout_s  # TimeoutStartSec=900 + START_TIMEOUT_MARGIN_S
        self.stop_timeout_s = stop_timeout_s  # TimeoutStopSec=120 + margin
        self.ready_timeout_s = ready_timeout_s
        self.sudo = sudo
        self._run = runner

    def _check_key(self, key: str) -> str:
        """Refuse anything that is not a bare engine key before it reaches sudo or systemctl (see module docstring)."""
        if not UNIT_KEY_RE.fullmatch(key):
            raise EngineControlError(f"engine key {key!r} is not a bare unit instance name ([A-Za-z0-9._-]+); refusing "
                                     "to build a systemctl command from it")
        if self.engines and key not in self.engines:
            raise EngineControlError(f"engine key {key!r} is not in engines.json (CONVENTIONS.md §8); the sudoers "
                                     "fragment names exactly those keys")
        return key

    def _systemctl(self, verb: str, key: str, timeout_s: float) -> None:
        if verb not in self.ALLOWED:
            raise EngineControlError(f"systemctl {verb} is not in the sudoers fragment (only {self.ALLOWED})")
        unit = f"{UNIT_PREFIX}{self._check_key(key)}"
        # Plain `sudo -n`, never -E (conflict 7); stdin closed so neither sudo nor sudo-rs can wait for a password.
        cmd = ([SUDO, "-n"] if self.sudo else []) + [SYSTEMCTL, verb, unit]
        try:
            proc = self._run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s,
                             check=False)
        except subprocess.TimeoutExpired as exc:
            raise EngineControlError(f"{' '.join(cmd)} did not return within {timeout_s:.0f}s (the unit's own "
                                     f"TimeoutStartSec/TimeoutStopSec should have fired first; see journalctl -u "
                                     f"{unit} -n 60)") from exc
        except PermissionError as exc:
            # subprocess.run's timeout path kills the child; a setuid sudo child owned by root refuses the signal.
            raise EngineControlError(f"{' '.join(cmd)} could not be signalled ({exc}); the unit's own timeout should "
                                     f"have fired before Python's {timeout_s:.0f}s (see journalctl -u {unit} -n 60)"
                                     ) from exc
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
        unit = f"{UNIT_PREFIX}{self._check_key(key)}"
        try:
            proc = self._run([SYSTEMCTL, "is-active", "--quiet", unit], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineControlError(f"systemctl is-active {unit} could not run: {exc}") from exc
        return proc.returncode == 0


class StubController:
    """Test double: records calls and, when given a StubProbe, moves the fake memory counter like a real load.

    `leak_on_stop` keeps the counter high after stop, which is how tests provoke the release-confirmation timeout
    (Section 4.2 rule 5). `fail_start` names keys whose start raises, for the fail-loudly path; `fail_stop` names keys
    whose stop raises (the unit keeps running and the counter stays up), `start_error` picks the class raised by a
    failing start (EngineError stands for wait_ready's post-start health failure).
    """

    def __init__(self, *, engines: dict[str, EngineSpec] | None = None, probe: Any = None,
                 footprints: dict[str, int] | None = None, leak_on_stop: bool = False,
                 fail_start: Sequence[str] = (), fail_stop: Sequence[str] = (),
                 start_error: type[EngineError] = EngineControlError) -> None:
        self.engines = engines or {}
        self.probe = probe
        self.footprints = dict(footprints or {})
        self.leak_on_stop = leak_on_stop
        self.fail_start = set(fail_start)
        self.fail_stop = set(fail_stop)
        self.start_error = start_error
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
            raise self.start_error(f"stub: start of {key} refused (test)")
        self.active.add(key)
        if self.probe is not None:
            self.probe.used_bytes += self._bytes(key)

    def stop(self, key: str) -> None:
        self.calls.append(("stop", key))
        if key in self.fail_stop:
            raise EngineControlError(f"stub: sudo -n systemctl stop llama-server@{key} failed (exit 1): sudo: a "
                                     "password is required (test)")
        self.active.discard(key)
        if self.probe is not None and not self.leak_on_stop:
            self.probe.used_bytes = max(0, self.probe.used_bytes - self._bytes(key))

    def is_active(self, key: str) -> bool:
        return key in self.active
