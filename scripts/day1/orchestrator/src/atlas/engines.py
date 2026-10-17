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

The per-slot window (S52). Every generation request this package sends to a llama-server leaves through ONE method,
LlamaClient._open_chat, which first fits the request to the engine's per-slot window (fit_to_window; HttpEngineStreamer
in atlas.api, the router's classifier and the approval cross-checker all hold a LlamaClient;
tests/test_context_window.py fails when any other code in atlas/ posts to a generation endpoint). Facts, VERIFIED in the
source of the pinned build (llama.cpp build tag b11474 = b9acf138, phase2/01-llama.sh; re-read for v0.4.0 on
2026-10-16, lines moved from v0.4.1 = b29c606 but the behaviour is unchanged) unless marked:
  * ATLAS passes an explicit --parallel, so the KV cache is not unified and each slot is its own stream of
    ctx_size / parallel cells, padded UP to a multiple of 256 (src/llama-context.cpp:292-306); the window a slot
    enforces is that figure capped at the model's training context (tools/server/server-context.cpp:4377-4385,
    n_ctx_slot()). ctx_size // parallel is therefore never above the enforced window while every per-slot figure is
    at or below its model's training context, which engines.json's notes say for every engine (INFERRED).
  * A prompt longer than the slot is REJECTED, never truncated: HTTP 400 {"error": {"type":
    "exceed_context_size_error", "n_prompt_tokens", "n_ctx"}} (server-context.cpp:3407-3426, 4595, 4732 for a stream;
    server-common.cpp:68-69; server-task.cpp:1514-1515).
  * Context shift is turned off per context where the cache cannot shift (server-context.cpp:1307-1322 "ctx_shift is
    not supported by this context" / "by multimodal"; src/llama-kv-cache.cpp:1208-1217, src/llama-kv-cache-dsv4.cpp:
    1451-1455): on every Day 1 chat engine but GLM-5.3-Flash text-only, i.e. gpt-oss-120b (iSWA), the router (IMROPE),
    DeepSeek V4 (dsv4 cache) and GLM-5.3-Flash with its glm5v mmproj (multimodal). There generation stops at the slot
    end with finish "length" once prompt + generated + 1 >= n_ctx (server-context.cpp:2019-2025). The rule below is
    the same for every engine.
  * POST /v1/chat/completions/input_tokens takes a chat-completions body and answers {"input_tokens": N}: the same
    template pass and tokenizer call (add_special, parse_special) as the generation, and on an --mmproj server
    process_mtmd_prompt over placeholder bitmaps, whose size includes every image token (tools/server/server.cpp:
    294-295; server-context.cpp:5950-5999). POST /apply-template and POST /tokenize (server.cpp:290, 292) are the
    fallback when that route answers 404 (a build other than the pin); that pair does not see images, so each image
    part is charged IMAGE_TOKENS_ESTIMATE.
  * n_predict has the aliases max_tokens and max_completion_tokens (server-schema.cpp:44-48); without any of them
    the server's default is -1 (unbounded), so an unclamped request on an engine without context shift runs into the
    slot end.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
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


class ContextWindowExceeded(EngineError):
    """A generation request that does not fit its engine's per-slot window even with every earlier exchange dropped
    (S52; module docstring). It is never sent; atlas.api answers it with status 413 and this message: the HTTP status
    of a non-streamed request; a streamed one (Open WebUI) is already HTTP 200, so the 413 arrives as the error
    chunk's error.status with the '[ATLAS]' text."""

    def __init__(self, message: str, *, engine: str, window: int, prompt_tokens: int) -> None:
        super().__init__(message)
        self.engine = engine
        self.window = window
        self.prompt_tokens = prompt_tokens


# --- the per-slot window (S52; module docstring) ----------------------------------------------------------------------

CHAT_PATH = "/v1/chat/completions"
COUNT_PATH = "/v1/chat/completions/input_tokens"  # server.cpp:294-295 at the pin; 404 on a build without it
# Room kept between prompt + max_tokens and the slot end. The server stops at prompt + generated + 1 >= n_ctx, so 1
# would do with an exact count; the rest covers a count that drifts by a few tokens (none is known at the pin).
CONTEXT_MARGIN_TOKENS = 32
# The smallest reply worth sending a request for, capped by what the caller asked for (a 200-token classifier verdict
# keeps its 200). Below it the request fails instead of being sent with a reply budget that ends mid-sentence. A
# policy figure, not a measurement: gpt-oss at reasoning_effort high may spend more than this on analysis alone.
MIN_REPLY_TOKENS = 512
# Fallback counting only (/apply-template + /tokenize do not see images): the most tokens one image can cost on the
# vision engine at the pin. v0.4.0 (D-20, S-10): the vision engine is GLM-5.3-Flash with its glm5v mmproj, whose header
# gives image_max_pixels 6,272,000, patch_size 14 and merge 2, so one image is at most 6,272,000 / (14 x 14 x 2 x 2) =
# 8,000 tokens, wrapped in <|begin_of_image|> ... <|end_of_image|>, 2 more (tools/mtmd/clip.cpp:1782-1794, 4217-4225;
# mtmd-image.cpp:801-840; mtmd.cpp:871-876; VERIFIED at b11474, research_notes/ATLAS v0.4.0 build record/pin_facts.md).
# engines.json passes no --image-max-tokens. The primary count (COUNT_PATH) includes image tokens exactly.
IMAGE_TOKENS_ESTIMATE = 8000 + 2
PREFIX_ROLES: frozenset[str] = frozenset({"system", "developer"})  # the stable layered prefix (Section 4.4)


@dataclass(frozen=True)
class SlotWindow:
    """The tokens one slot of an engine holds (prompt + reply), and where that figure was read."""

    engine: str
    tokens: int
    source: str


def window_for(spec: EngineSpec, ctx: int | None = None, parallel: int | None = None, *,
               source: str | None = None) -> SlotWindow:
    """The per-slot window of `spec`: ctx // parallel of the rendered unit when given (atlas.api.engine_window passes
    the env file's ATLAS_CTX_SIZE / ATLAS_PARALLEL), else the SMALLER per-slot figure engines.json gives the engine
    (ctx_per_slot, ctx_size / parallel). Without its env file a unit cannot start (llama-server@.service
    EnvironmentFile), so that fallback serves tests and an unreadable env dir."""
    if ctx is not None and parallel is not None:
        if parallel < 1 or ctx < parallel:
            raise EngineError(f"{spec.key}: ctx {ctx} x {parallel} slots is not a usable profile")
        return SlotWindow(spec.key, ctx // parallel, source or f"unit ctx {ctx} x {parallel} slots")
    figures = {f"ctx_size {spec.ctx_size} / parallel {spec.parallel}": spec.ctx_size // max(1, spec.parallel)}
    if spec.ctx_per_slot:
        figures[f"ctx_per_slot {spec.ctx_per_slot}"] = spec.ctx_per_slot
    name, tokens = min(figures.items(), key=lambda kv: kv[1])
    return SlotWindow(spec.key, tokens, source or f"engines.json {name}, no unit env file")


@dataclass(frozen=True)
class WindowFit:
    """What fit_to_window did: the messages and max_tokens to send, and the facts the log line carries."""

    messages: list[dict[str, Any]]
    max_tokens: int
    requested_max_tokens: int | None
    prompt_tokens_before: int
    prompt_tokens: int
    dropped_turns: int
    dropped_messages: int
    window: SlotWindow
    counts: int  # engine round-trips spent counting

    @property
    def changed(self) -> bool:
        return bool(self.dropped_turns) or self.max_tokens != self.requested_max_tokens


def _turns(messages: Sequence[Mapping[str, Any]]) -> tuple[int, list[int], int]:
    """(prefix_end, exchange_starts, tail_start). The prefix is every leading system/developer message; the tail is
    the current turn: the final user message, the user messages straight before it (an image the Principal just sent
    may ride in its own message) and whatever follows it; in between, each exchange starts at a user message and runs
    to the next one, so a user message is never kept without its replies or the other way round (a leading reply
    with no user message before it is an exchange of its own). Without a user message the tail is the last message."""
    n = len(messages)
    prefix = 0
    while prefix < n and messages[prefix].get("role") in PREFIX_ROLES:
        prefix += 1
    users = [i for i in range(prefix, n) if messages[i].get("role") == "user"]
    tail = users[-1] if users else max(prefix, n - 1)
    while tail - 1 >= prefix and messages[tail - 1].get("role") == "user":
        tail -= 1
    starts = [i for i in range(prefix, tail) if i == prefix or messages[i].get("role") == "user"]
    return prefix, starts, tail


def image_parts(messages: Sequence[Mapping[str, Any]]) -> int:
    """OpenAI image_url content parts across `messages` (llama-server's multimodal chat input, server-common.cpp)."""
    return sum(1 for m in messages if isinstance(m.get("content"), list)
               for p in m["content"] if isinstance(p, Mapping) and p.get("type") == "image_url")


def fit_to_window(messages: Sequence[Mapping[str, Any]], max_tokens: int | None, window: SlotWindow,
                  count: Callable[[list[dict[str, Any]]], int], *, margin: int = CONTEXT_MARGIN_TOKENS,
                  min_reply: int = MIN_REPLY_TOKENS) -> WindowFit:
    """Keep a chat request inside `window` (S52). `count(messages)` is the engine's own prompt count for those
    messages; it is called once, and again only while trimming (the floor, then a bisection over how many exchanges
    to drop: 2 + ceil(log2 n) calls for n droppable exchanges).

    Policy: every leading system message (the stable persona and domain-card prefix of Section 4.4, so the prompt
    cache keeps its hits) and the current turn (_turns) stay. History goes only to make room for a BOUNDED reply
    reserve: min(max_tokens, max(min_reply, window // 4)), or min_reply when no max_tokens is given. The oldest
    exchanges go, whole, and only the fewest (found by bisection) that make prompt + reserve + margin fit, so a
    conversation that leaves the reserve is sent whole whatever max_tokens it asks for. With the pipeline's
    4096-token default (atlas.api.DEFAULT_MAX_TOKENS) a 32k slot thus reserves all 4096 and the router's 8192-token
    slot 2048; a max_tokens of 8192 or more (the window, say) on a 32k slot reserves
    8192. When the prefix and the current turn alone cannot take the reserve, every earlier exchange goes (that
    branch does not bisect). max_tokens is then clamped so prompt + max_tokens + margin never passes the window (a
    request without one gets the whole remaining room), so generation never reaches the slot end. When the room left
    is under min(max_tokens, min_reply), nothing is sent: ContextWindowExceeded names the engine, its window and the
    prompt size. One log line whenever anything changed, the reply reserve included.
    """
    msgs = [dict(m) for m in messages]
    requested = max_tokens if max_tokens is not None and max_tokens >= 0 else None
    floor = min(requested, min_reply) if requested is not None else min_reply
    # The reply held back while deciding what history to drop: what was asked for, capped at a quarter of the slot
    # (never under min_reply), so a large max_tokens or the pipeline's 4096 default cannot cost a small slot (the
    # router's, LightRAG's gleaning call) or a fitting chat its history. A policy figure, not a measurement. It is
    # never below `floor`, so whatever fits the reserve also passes the refusal check below.
    reserve = min(requested, max(min_reply, window.tokens // 4)) if requested is not None else min_reply
    limit = window.tokens - margin
    prefix, starts, tail = _turns(msgs)

    def without(j: int) -> list[dict[str, Any]]:  # the first j exchanges dropped
        return msgs[:prefix] + msgs[(starts[j] if j < len(starts) else tail):]

    counts = 1
    before = count(msgs)
    kept, prompt, dropped = msgs, before, 0
    if before + reserve > limit and starts:
        k = len(starts)
        floor_prompt = count(without(k))
        counts += 1
        kept, prompt, dropped = without(k), floor_prompt, k
        if floor_prompt + reserve <= limit:
            # Bisect for the fewest exchanges to drop: `lo` drops too few (measured or the full request), `hi` is a
            # measured fit; only a measured fit is ever sent.
            lo, hi = 0, k
            while hi - lo > 1:
                mid = (lo + hi) // 2
                n_mid = count(without(mid))
                counts += 1
                if n_mid + reserve <= limit:
                    hi, prompt = mid, n_mid
                else:
                    lo = mid
            kept, dropped = without(hi), hi
    room = limit - prompt
    if room < floor:
        cut = f" after dropping all {dropped} earlier exchange(s)" if dropped else ""
        raise ContextWindowExceeded(
            f"{window.engine}: the request does not fit the engine's per-slot context window of {window.tokens} "
            f"tokens ({window.source}): the system prompt and the current message alone are {prompt} tokens{cut}, "
            f"leaving {max(room, 0)} for the reply (minimum {floor}, margin {margin}); shorten it or use document "
            "retrieval (Section 4.3)",
            engine=window.engine, window=window.tokens, prompt_tokens=prompt)
    fit = WindowFit(messages=kept, max_tokens=room if requested is None else min(requested, room),
                    requested_max_tokens=requested, prompt_tokens_before=before, prompt_tokens=prompt,
                    dropped_turns=dropped, dropped_messages=len(msgs) - len(kept), window=window, counts=counts)
    if fit.changed:
        # Counts only, never content (the journal is persistent; router.py keeps the Principal's text out of it too).
        (log.warning if dropped else log.info)(
            "context window engine=%s window=%d (%s): prompt %d -> %d tokens, dropped %d exchange(s) (%d message(s)), "
            "max_tokens %s -> %d (reply reserve %d), %d count(s)", window.engine, window.tokens, window.source, before,
            prompt, dropped, fit.dropped_messages, requested, fit.max_tokens, reserve, counts)
    return fit


def requested_max_tokens(body: Mapping[str, Any]) -> int | None:
    """The reply limit a chat body asks for: n_predict wins over its aliases max_tokens / max_completion_tokens at the
    pin (server-common.cpp:1486-1487, server-schema.cpp:44-48); the smallest one given is taken, so a body carrying
    two never gets the larger one. None (or a negative value, -1 = unbounded) means no limit."""
    given = [int(body[k]) for k in ("n_predict", "max_tokens", "max_completion_tokens")
             if isinstance(body.get(k), int) and not isinstance(body.get(k), bool) and body[k] >= 0]
    return min(given) if given else None


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
    """Synchronous client for one llama-server instance (127.0.0.1:<port>).

    Generation (chat, chat_chunks) needs the engine's per-slot window (S52): `window` is a SlotWindow, or a callable
    read on every request (atlas.api.engine_window follows the unit's env file); for_engine defaults it to engines.json
    (window_for). A client built without one (an embedding or rerank client) refuses to generate."""

    # llama-server binds loopback (CONVENTIONS.md §8 ports); only these hosts may be dialled with the proxy ignored.
    LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

    def __init__(self, base_url: str, *, timeout_s: float = 600.0, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None,
                 window: SlotWindow | Callable[[], SlotWindow] | None = None) -> None:
        self._window = window
        self.last_fit: WindowFit | None = None  # the most recent fit, for callers and tests (S52)
        self._fallback_logged = False
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
        if kw.get("window") is None:
            kw["window"] = window_for(spec)  # engines.json; the production wiring passes the unit's (atlas.api)
        return cls(spec.base_url, **kw)

    def window(self) -> SlotWindow:
        w = self._window() if callable(self._window) else self._window
        if w is None:
            raise EngineError(f"{self.base_url}: no per-slot window configured; generation needs one (S52: build the "
                              "client with LlamaClient.for_engine or pass window=)")
        return w

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

    # --- prompt counting and the per-slot window (S52) ---------------------------------------------------------------

    def count_prompt_tokens(self, body: Mapping[str, Any]) -> int:
        """The prompt tokens this chat body will occupy in a slot, counted by the engine itself: POST COUNT_PATH (the
        generation's own template and tokenizer pass, image tokens included); on a 404 (no such route: not the pinned
        build) /apply-template + /tokenize with the generation's add_special/parse_special, plus
        IMAGE_TOKENS_ESTIMATE per image part, which that pair does not see (module docstring)."""
        probe = {**body, "stream": False}
        r = self._http.post(COUNT_PATH, json=probe)
        if r.status_code == 404:
            return self._count_by_template(probe)
        _raise_for_status(r, "count")
        n = r.json().get("input_tokens")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise EngineError(f"count: {r.request.url} answered input_tokens={n!r}, not a token count")
        return n

    def _count_by_template(self, body: Mapping[str, Any]) -> int:
        if not self._fallback_logged:
            self._fallback_logged = True
            log.warning("%s: %s answered 404 (not the pinned llama.cpp build?); counting with /apply-template + "
                        "/tokenize and %d tokens per image part", self.base_url, COUNT_PATH, IMAGE_TOKENS_ESTIMATE)
        r = self._http.post("/apply-template", json=body)
        _raise_for_status(r, "apply-template")
        prompt = r.json().get("prompt")
        if not isinstance(prompt, str):
            raise EngineError(f"apply-template: {r.request.url} returned no prompt string")
        # add_special=true, parse_special=true is what the generation tokenizes with (VERIFIED at b11474,
        # server-context.cpp:4650; the count route, inside 5950-5999, likewise); /tokenize defaults add_special to
        # false (README).
        r = self._http.post("/tokenize", json={"content": prompt, "add_special": True, "parse_special": True})
        _raise_for_status(r, "tokenize")
        tokens = r.json().get("tokens")
        if not isinstance(tokens, list):
            raise EngineError(f"tokenize: {r.request.url} returned no token list")
        return len(tokens) + IMAGE_TOKENS_ESTIMATE * image_parts(body.get("messages") or [])

    def fit_chat_body(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """The body to send: messages trimmed and max_tokens clamped to the per-slot window (fit_to_window); the
        aliases n_predict / max_completion_tokens are folded into max_tokens so the clamp is the limit the server
        applies. Raises ContextWindowExceeded when nothing that keeps the prefix and the current turn fits."""
        window = self.window()
        fit = fit_to_window(body.get("messages") or [], requested_max_tokens(body), window,
                            lambda msgs: self.count_prompt_tokens({**body, "messages": msgs}))
        self.last_fit = fit
        out = {k: v for k, v in body.items() if k not in ("n_predict", "max_completion_tokens")}
        out["messages"] = fit.messages
        out["max_tokens"] = fit.max_tokens
        return out

    @contextlib.contextmanager
    def _open_chat(self, body: Mapping[str, Any], what: str) -> Iterator[httpx.Response]:
        """THE path of every generation request this package sends to a llama-server (S52): fit to the window, then
        POST. tests/test_context_window.py fails when other code in atlas/ posts to a generation endpoint."""
        fitted = self.fit_chat_body(body)
        with self._http.stream("POST", CHAT_PATH, json=fitted) as r:
            if r.status_code >= 400:
                self._raise_chat_error(r, what)
            yield r

    def _raise_chat_error(self, r: httpx.Response, what: str) -> None:
        """The server's own exceed_context_size_error (a window that was read wrong) becomes ContextWindowExceeded with
        the server's figures; anything else is the usual EngineError."""
        try:
            raw = r.read().decode("utf-8", "replace")
        except httpx.HTTPError:
            raw = ""
        try:
            err = json.loads(raw).get("error") if raw else None
        except (ValueError, AttributeError):
            err = None
        if r.status_code == 400 and isinstance(err, dict) and err.get("type") == "exceed_context_size_error":
            window = self.window()
            n_ctx, n_prompt = int(err.get("n_ctx") or 0), int(err.get("n_prompt_tokens") or 0)
            raise ContextWindowExceeded(
                f"{window.engine}: llama-server rejected the prompt, {n_prompt} tokens against a per-slot context of "
                f"{n_ctx} (the orchestrator expected {window.tokens}: {window.source}); shorten it or use document "
                "retrieval (Section 4.3)", engine=window.engine, window=n_ctx or window.tokens, prompt_tokens=n_prompt)
        raise EngineError(f"{what}: {r.request.url} -> HTTP {r.status_code} {raw[:500]}")

    # --- generation ------------------------------------------------------------------------------------------------

    def chat(self, messages: Sequence[dict[str, Any]], stream: bool = False, *, model: str = "any",
             max_tokens: int | None = None, temperature: float | None = None,
             on_delta: Callable[[str], None] | None = None, **params: Any) -> ChatResult:
        """POST /v1/chat/completions; returns the text and the server's timings object.

        stream=True consumes the SSE stream (calling on_delta per text delta) and still returns the aggregate; the
        timings come from the final chunk, which llama-server sends with "timings" when the generation finishes.
        Either way the request goes through _open_chat (the per-slot window, S52).
        """
        if not stream:
            body: dict[str, Any] = {"model": model, "messages": list(messages), "stream": False, **params}
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            if temperature is not None:
                body["temperature"] = temperature
            with self._open_chat(body, "chat") as r:
                r.read()
                data = r.json()
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            timings = Timings.model_validate(data["timings"]) if isinstance(data.get("timings"), dict) else None
            return ChatResult(text=text, timings=timings, finish_reason=choice.get("finish_reason"), raw=data)
        params.setdefault("timings_per_token", True)
        parts: list[str] = []
        timings: Timings | None = None
        finish: str | None = None
        last: dict[str, Any] = {}
        for chunk in self.chat_chunks(messages, model=model, max_tokens=max_tokens, temperature=temperature,
                                      **params):
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

    def chat_chunks(self, messages: Sequence[Mapping[str, Any]], *, model: str = "any", max_tokens: int | None = None,
                    temperature: float | None = None, **params: Any) -> Iterator[dict[str, Any]]:
        """POST /v1/chat/completions with stream=true and yield the server's SSE events as they arrive (OpenAI chunks;
        the last one carries `timings`): atlas.api.HttpEngineStreamer's transport, through _open_chat (S52)."""
        body: dict[str, Any] = {"model": model, "messages": list(messages), "stream": True, **params}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        with self._open_chat(body, "chat(stream)") as r:
            yield from _sse_events(r.iter_lines())

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
        spec = self.engines.get(key)
        if spec is not None and spec.is_external:
            # Section 4.2 class external (engines.json `external` list): no llama-server@ unit, no sudoers line; the
            # Arbiter budgets it without ever reaching this controller, so a call here is a bug, named as such.
            raise EngineControlError(f"engine key {key!r} is class external (no llama-server unit; the caller runs the "
                                     "process, Section 4.2 'Chatterbox when invoked'); refusing systemctl for it")
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
        spec = self.engines.get(key)
        if spec is not None and spec.is_external:
            return False  # no unit exists for a class-external engine (Section 4.2; engines.json `external` list)
        unit = f"{UNIT_PREFIX}{self._check_key(key)}"
        try:
            proc = self._run([SYSTEMCTL, "is-active", "--quiet", unit], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineControlError(f"systemctl is-active {unit} could not run: {exc}") from exc
        return proc.returncode == 0

    def main_pid(self, key: str) -> int | None:
        """The unit's MainPID (`systemctl show -p MainPID --value`, no sudo), or None when it is not running or cannot
        be read: the Arbiter reads the VmRSS of a CPU-placed companion through it (D-14 (e), the host resident set)."""
        unit = f"{UNIT_PREFIX}{self._check_key(key)}"
        try:
            proc = self._run([SYSTEMCTL, "show", "-p", "MainPID", "--value", unit], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("systemctl show %s could not run: %s", unit, exc)
            return None
        value = (proc.stdout or "").strip()
        if proc.returncode != 0 or not value.isdigit() or int(value) <= 0:
            return None  # "0": the unit is not running
        return int(value)


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
        was_active = key in self.active
        self.active.discard(key)
        # Stopping a unit that is not running frees nothing (the Arbiter stops a unit whose start failed, measure 10).
        if self.probe is not None and not self.leak_on_stop and was_active:
            self.probe.used_bytes = max(0, self.probe.used_bytes - self._bytes(key))

    def is_active(self, key: str) -> bool:
        return key in self.active
