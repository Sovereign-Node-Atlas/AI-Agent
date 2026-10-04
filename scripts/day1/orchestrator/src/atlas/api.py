"""The orchestrator's HTTP face (Section 12.1; Appendix A; systemd/atlas-orchestrator.service; console script
`atlas-orchestrator --host H --port P`, also `atlas-api`).

Endpoints
    GET  /health                       200 {"status":"ok"} when the app is wired (arbiter measured, ledger open)
    GET  /v1/models                    {"data":[{"id":"atlas"},{"id":"ren"},{"id":"arthur"}]} (12.1)
    POST /v1/chat/completions          OpenAI shape, stream true/false. "atlas" runs the 4-Way Router; "ren"/"arthur"
                                       force the hemisphere (the router's own [REN]/[ARTHUR] override, 7.2 rule 4;
                                       a hard-keyword hit still wins, 7.2 rule 1: the redirect is said to the
                                       Principal and logged as the router's routing decision, nothing more, because
                                       the hard rule doing what 7.2 rule 1 mandates is not a 9.4 mistake). Anything
                                       else is 404: ENGINE KEYS are NOT served here (fix round 2). The Phase 3
                                       load-test contract is POST /internal/v1/chat/completions (phase3/loadtest.py
                                       ORCH_CHAT_PATH), loopback only, so the public route never offers a path around
                                       the router, the layered prompt, the scars, the vault rule and the never-delegate
                                       pass (7.1 C8: the same router governs every entry point).
                                       Pipeline (Appendix A): router -> layered prompt (prompts.build_system_prompt,
                                       4.4) -> ledger task -> Engine Arbiter load (waits in its queue) -> generation
                                       slot -> llama-server SSE relayed as OpenAI chunks, each sentence through the
                                       never-delegate rewrite (16.1 rule 5; governance.never_delegate_rewrite) with
                                       the PRINCIPAL register set -> memory write (unless vault-tagged) -> ledger
                                       done; on failure an Ouroboros strike. The router's `command` (deep-think,
                                       ouroboros-strike, aegis, vault-session) is honoured: a vault-session message
                                       tags the session AND is answered (the tag, not the loss of the message, is the
                                       10.5 rule); Deep Think standard/deep is enqueued on the Celery gpu queue
                                       (atlas.tasks.deep_think) and the reply names the task id, quick runs inline
                                       (9.7: the interface returns at once; 9.1: nothing is hard-capped).
                                       Open WebUI task calls (0.11.4 title/tags/follow-up generation: the last user
                                       message starts with "### Task:" and embeds the whole chat) run on the routed
                                       engine but WITHOUT retrieval, the never-delegate rewrite, a memory write or a
                                       strike on failure: they are UI chores, not Principal turns. Cross-writer:
                                       docker/core/compose.yml should set ENABLE_TITLE_GENERATION,
                                       ENABLE_TAGS_GENERATION, ENABLE_FOLLOW_UP_GENERATION and
                                       ENABLE_AUTOCOMPLETE_GENERATION to "false" so they do not happen at all.
    POST /vault/open {passphrase}      pipes it to atlas-vault; never logged (Section 11); /vault/lock; /vault/status
    GET  /approvals[?status=held]      the queue (16.2); POST /approvals/{id}/approve|reject {decided_by, note}
    POST /arbiter/register             {engine, total_bytes, task_id}: Phase 3/4 record a measured footprint (rule 1)
    POST /arbiter/load                 {engine, ctx, parallel, task_id} -> {decision: granted|queued|refused, reason,
                                       projected_bytes}: a load through the Arbiter (4.2 rule 2; phase3/loadtest.py)
    POST /arbiter/unload               {engine, task_id} -> the same shape (rule 5 release check inside)
    POST /arbiter/remeasure            re-read the resident set (4.1) while NO engine is resident: Phase 2 step 4/5
                                       add the small models after the orchestrator measured at step 2. The chat path
                                       and /arbiter/load also re-measure lazily whenever nothing is resident.
    GET  /arbiter/status               the Arbiter's ledger view (4.2 rule 9)
    POST /strike                       {persona, domain, context, error, correction, kind}: Ouroboros (9.4)
    POST /internal/v1/chat/completions {model: <engine key>}: generation for the Celery tasks through the Arbiter
                                       (9.7 C15); loopback only, never listed in /v1/models. Gets its own CHILD ledger
                                       row (parent_task_id = the caller's atlas_task_id); the child id is returned in
                                       the `atlas` object so the caller can correlate. `atlas_hemisphere` (fix round
                                       4) declares the hemisphere of the text (one of HEMISPHERES, 422 otherwise):
                                       OrchestratorClient.generate sends it (Sentinel BLUF = estate, retention = the
                                       routed hemisphere, Deep Think = the chat's, LightRAG = its workspace). A failed
                                       internal generation is a 9.4 strike; its scar is written to the DECLARED
                                       hemisphere only. Without the declaration (phase3/loadtest.py, any older
                                       caller) the strike is ledger-only with the prompt withheld: a document chunk
                                       or transcript must never become an estate scar that Arthur's prompts then
                                       carry across the membrane (7.3, 10.1).
    POST /internal/route               {message} -> the router's decision as JSON (retention needs the hemisphere)
    POST /internal/deep-think/plan     {tier} -> the Arbiter's plan (4.2 rule 8)

Authentication (fix round). Every route except GET /health and GET /v1/models is an admin route: it needs the token
in ORCH_ADMIN_TOKEN_FILE (a file under /etc/atlas/secrets, `ORCH_ADMIN_TOKEN=...` or the bare token;
atlas.tasks.admin_token reads the same file for the Celery side), given as the header `X-Atlas-Token` or as
`Authorization: Bearer <token>` (the header Open WebUI sends OPENAI_API_KEY in; cross-writer: phase2/03-openwebui.sh
should write the admin token into openwebui.env as OPENAI_API_KEY instead of the dummy `atlas-local`). When no token
file is configured (the Day 1 state: phase2/02-orchestrator.sh does not write one yet, see the notes), the admin
routes accept LOOPBACK clients only. POST /v1/chat/completions (fix round 2) is open to loopback clients always and,
off loopback, needs the token whenever one is configured: without that, any LAN device reaching ORCH_PORT could read
estate memory into prompts, log strikes, tag sessions and start an AEGIS run. /internal/* is loopback-only in both
modes. LOOPBACK TRUST INCLUDES EVERY HOST-NETWORKED CONTAINER: Open WebUI runs with network_mode: host
(docker/core/compose.yml), so it reaches /internal/* and, without a token, every admin route as 127.0.0.1;
ORCH_HOST=127.0.0.1 (the default) remains the supported bind, and Section 11 ("opened by a button") and 16.2 ("until
the Principal taps approve") reach these routes THROUGH the host-networked Open WebUI over loopback. POST /vault/open
carries the passphrase in clear HTTP, so off loopback it is refused unless the request arrived over TLS
(request.url.scheme == "https"); a LAN/WireGuard bind therefore needs TLS terminated in front of the orchestrator.
That check covers only the Open WebUI -> orchestrator hop: the passphrase's real wire leg is browser -> Open WebUI,
which docker/core/compose.yml binds on 0.0.0.0:$OPENWEBUI_PORT in plain HTTP, so the passphrase is protected in
transit ONLY when Open WebUI itself is reached over TLS or through WireGuard (cross-writer: compose/ufw; fix round 4).
LOAD-BEARING UVICORN SETTINGS (fix round 4): main() passes proxy_headers=True and forwarded_allow_ips="127.0.0.1"
EXPLICITLY. A TLS terminator on the node connects from 127.0.0.1, so without X-Forwarded-For rewriting
request.client every remote client would arrive as a trusted loopback client and reach /internal/* and the token-less
routes; with it, uvicorn trusts the forwarded address only from a proxy on 127.0.0.1. A terminator MUST run on the
node and set X-Forwarded-For and X-Forwarded-Proto (the latter decides request.url.scheme == "https" above).
/internal/* additionally takes its own shared secret when ORCH_INTERNAL_TOKEN_FILE is configured (a file under
/etc/atlas/secrets, `ORCH_INTERNAL_TOKEN=...` or the bare token; header X-Atlas-Internal-Token or Authorization:
Bearer, which is what LightRAG's OpenAI client sends its api_key as): address trust alone is not the guard then.
OrchestratorClient (atlas.tasks) and LightRAGStore (atlas.memory) read the same file. No Day 1 step writes it yet
(cross-writer: phase2/02-orchestrator.sh), so the default is loopback-only, as before.

Not implemented here, stated so no gate or writer relies on it (fix round, rule §7.4): Section 8.5 task-force
dispatch (director dispatch, sequential relay, synthesis by the lead). The router's `directors` are echoed in the
`atlas` object and the hemisphere lead answers with the preset's cards. Phase 2 step 2 calls this a scaffold. The D7
graph layer (atlas.memory.LightRAGStore) is installed but fed on Day 1 by ONE writer only, the nightly chat
summaries (tasks/retention.py); the chat path writes turns to Chroma, not to the graph, and no Docling ingestion task
exists yet, so no gate relies on graph content.

The one generation slot (GenerationSlot, 4.2 rule 3) covers the router's classifier call too (fix round 4): Eleanor's
verdict on router-qwen3.5-4b IS a generation, and rule 3 "applies everywhere". Every chat turn and /internal/route
takes the slot for the classifier, releases it, then takes it again for the generation (the engine load of 4.2 rule 2
happens INSIDE that second hold, so the Arbiter's swap of rules 4/6 is serialised in the one FIFO and an engine granted
to a queued request can never be evicted by a later request before it generates).

Wiring: `build_app(deps)` takes an `AppDeps` so tests inject doubles for llama-server, the engine controller, the
memory probe, docker and the vault helper (CONVENTIONS.md §7.8); the router, the prompt builder and the approval
queue are the real modules (atlas.router, atlas.prompts, atlas.approval). `main()` builds the production deps.
Outbound delivery: no Section 13 channel is part of this package yet, so the production approval queue carries
`NoChannelSender`: approving an item records the decision and then FAILS the send loudly (HTTP 502, ledger note
"send failed"), never pretends to have sent (rule §7.4).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas import __version__, deep_think
from atlas.approval import ApprovalError, ApprovalItem, ApprovalQueue, CrossCheckRequired, NotPending, SendError
from atlas.arbiter import APEX_KEY, DEEP_THINK_TIERS, Arbiter, ArbiterError, Decision, ReleaseTimeout, UnknownEngine
from atlas.config import HEMISPHERES, AtlasConfig, ConfigError, EngineSpec
from atlas.engines import EngineError, _sse_events
from atlas.governance import RewriteResult, never_delegate_rewrite, register
from atlas.ledger import Ledger, new_task_id
from atlas.memory import MemoryStore, MemoryStoreError
from atlas.router import RouterError, RoutingDecision, parse_override
from atlas.tasks.ouroboros import record_strike, retrieve_scars
from atlas.vault import SessionTags, VaultController, VaultError

log = logging.getLogger("atlas.api")

MODELS: tuple[str, ...] = ("atlas", "ren", "arthur")  # Section 12.1
LEAD_OVERRIDE: dict[str, str] = {"ren": "[REN]", "arthur": "[ARTHUR]"}  # router-rules.json override keys
DEFAULT_LOAD_WAIT_S = 900.0  # a swap is 15-45 s (4.2 rule 4); a queued load waits behind a running generation
# How long a request waits for the single generation slot (ATLAS_GENERATION_WAIT_S). A Celery task that generates
# through /internal (atlas.tasks.OrchestratorClient) may spend this whole time queued behind a running generation
# BEFORE its own generation starts, so that client's read timeout is this value plus a generation
# (OrchestratorClient.DEFAULT_TIMEOUT_SLACK_S); the two are sized together (fix round 2).
DEFAULT_GENERATION_WAIT_S = 1800.0
DEFAULT_MAX_TOKENS = 4096
# D9 (10.4): a chat lives 90 days in Open WebUI, then its SUMMARY replaces it in the Vector Cortex (retention.py). The
# raw turn written per request (Appendix A "memory writes") is therefore temporal with the same 90-day life, and the
# prune deletes expired chat turns WITHOUT archiving them (prune.py): a purged chat does not live on under /srv/cold.
CHAT_TURN_TTL_HOURS = 24.0 * 90
ADMIN_TOKEN_HEADER = "x-atlas-token"
INTERNAL_TOKEN_HEADER = "x-atlas-internal-token"  # /internal/* secret (ORCH_INTERNAL_TOKEN_FILE; module docstring)
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})
# The router's vault-session override key. A session is vault-tagged when the token appears ANYWHERE in a user message
# (fix round 2), not only at its start: an Open WebUI task call embeds the whole chat history inside one user message
# ("USER: [VAULT] ..."), and fail-closed is the only safe reading of 10.5. retention.is_vault_chat uses the same rule.
VAULT_PREFIX = "[VAULT]"
# Open WebUI 0.11.4's own background generations (title, tags, follow-ups, autocomplete) arrive as a user message that
# starts with this marker (backend/open_webui/utils/task.py templates; VERIFIED in the fix-round-2 review).
OWUI_TASK_MARKER = "### Task:"
# Sentence boundary for the streaming never-delegate pass: the same rule as governance._SENTENCE_SPLIT, so a chunk
# flushed here is rewritten exactly as the whole text would be (the rewrite is per sentence).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")

__all__ = [
    "AppDeps",
    "EngineStreamer",
    "GenerationSlot",
    "HttpEngineStreamer",
    "NoChannelSender",
    "NtfyNotifier",
    "RouteInfo",
    "RouterLike",
    "build_app",
    "build_production_deps",
    "history_has_vault_prefix",
    "is_owui_task_call",
    "main",
]


# --- contracts --------------------------------------------------------------------------------------------------------


class RouterLike(Protocol):
    """atlas.router.Router: route() with the ledger write inside (7.2 rule 5)."""

    def route(
        self,
        message: str,
        *,
        task_id: str | None = None,
        task_force: str | None = None,
        explicit_domains: Sequence[int] = (),
        context: Mapping[str, Any] | None = None,
    ) -> RoutingDecision: ...


class EngineStreamer(Protocol):
    """One llama-server: yields OpenAI-shaped chunks (choices[0].delta.content; a final chunk may carry timings)."""

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        **params: Any,
    ) -> Iterator[dict[str, Any]]: ...


# atlas.prompts.build_system_prompt(decision | persona key, cards, memory) -> SystemPrompt (or a str)
PromptBuilderFn = Callable[..., Any]


@dataclass(frozen=True)
class RouteInfo:
    """The router's decision as the pipeline uses it (RoutingDecision's fields; `body` is the prefix-stripped
    message)."""

    persona: str
    engine: str
    hemisphere: str
    tier: str = "standard"
    route: str = ""
    task_force: str | None = None
    domain_cards: tuple[int, ...] = ()
    override: str | None = None
    hard_keyword_hit: str | None = None
    reason: str = ""
    message: str = ""
    command: str | None = None
    deep_think: str | None = None
    directors: tuple[str, ...] = ()
    decision: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_decision(cls, d: Any, fallback_message: str) -> RouteInfo:
        get = (
            (lambda k, default=None: d.get(k, default))
            if isinstance(d, Mapping)
            else (lambda k, default=None: getattr(d, k, default))
        )
        persona, engine, hemisphere = get("persona"), get("engine"), get("hemisphere")
        if not persona or not engine or hemisphere not in HEMISPHERES:
            raise RuntimeError(f"router decision lacks persona/engine/hemisphere: {d!r} (atlas.router contract)")
        hits = tuple(get("hard_keyword_hits") or ())
        return cls(
            persona=str(persona),
            engine=str(engine),
            hemisphere=str(hemisphere),
            tier=str(get("tier") or "standard"),
            route=str(get("route") or persona),
            task_force=get("task_force"),
            domain_cards=tuple(int(c) for c in (get("domain_cards") or ())),
            override=get("override"),
            hard_keyword_hit=str(hits[0]) if hits else get("hard_keyword_hit"),
            reason=str(get("reason") or ""),
            # The router's `body` is the prefix-stripped message and may be EMPTY on purpose ("[VAULT]" alone): an
            # explicit body wins over the raw message; only its absence falls back.
            message=str(body if (body := get("body")) is not None else (get("message") or fallback_message)),
            command=get("command"),
            deep_think=get("deep_think_depth") or get("deep_think"),
            directors=tuple(get("directors") or ()),
            decision=d,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "engine": self.engine,
            "hemisphere": self.hemisphere,
            "tier": self.tier,
            "route": self.route,
            "task_force": self.task_force,
            "domain_cards": list(self.domain_cards),
            "override": self.override,
            "hard_keyword_hit": self.hard_keyword_hit,
            "reason": self.reason,
            "command": self.command,
            "deep_think": self.deep_think,
            "directors": list(self.directors),
        }


class NoChannelSender:
    """atlas.approval.Sender with no outbound channel wired: every send fails loudly (Section 13 channels are not in
    this package; a Gmail/Xero sender replaces this object, nothing else changes)."""

    def send(self, item: ApprovalItem) -> str:
        raise RuntimeError(
            f"no outbound channel is wired for kind {item.kind!r} to {item.recipient!r} (Section 13 integrations); "
            "the approval is recorded, nothing was sent"
        )


class NtfyNotifier:
    """atlas.approval.Notifier over ntfy (16.2: 'a push notification through ntfy announces items waiting')."""

    def __init__(self, push: Callable[..., Any]) -> None:
        self._push = push

    def notify(self, message: str, item: ApprovalItem) -> None:
        self._push(message, title=f"ATLAS approval #{item.id} ({item.tier})", priority="high", tags=["inbox_tray"])


class HttpEngineStreamer:
    """Streams /v1/chat/completions of one llama-server (engines.py facts: SSE `data:` lines, final `timings`)."""

    def __init__(
        self, spec: EngineSpec, *, timeout_s: float = 3600.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.spec = spec
        self._http = httpx.Client(base_url=spec.base_url, timeout=timeout_s, trust_env=False, transport=transport)

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        body: dict[str, Any] = {"model": self.spec.key, "messages": list(messages), "stream": True, **params}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        with self._http.stream("POST", "/v1/chat/completions", json=body) as r:
            if r.status_code >= 400:
                detail = r.read().decode("utf-8", "replace")[:500]
                raise EngineError(f"{self.spec.key}: llama-server HTTP {r.status_code}: {detail}")
            yield from _sse_events(r.iter_lines())

    def close(self) -> None:
        self._http.close()


class GenerationSlot:
    """The single generation slot of Section 4.2 rule 3 ("exactly one may generate at any moment ... it applies
    everywhere, including background work") for EVERY generation this orchestrator makes, the three resident small
    models included (fix round 2: a 4B call for a Sentinel BLUF, a retention summary or a LightRAG extraction used to
    run beside a weight-bearing generation because it took no ticket in the Arbiter's FIFO).

    Why it is here and not only in the Arbiter: the Arbiter's lock is bound to its residency ledger, and the resident
    small models are outside that ledger by design (4.1, 5.3: never budgeted, always loaded), so they cannot hold it.
    This slot is the superset: a strict FIFO (rule 4) over all generations; a weight-bearing generation takes this
    slot FIRST and then the Arbiter's lock inside it (which is therefore always free), so the Arbiter still records
    every weight-bearing generation for rule 6 (never preempt mid-generation) and its ledger view. The classifier
    call inside Router.route takes the slot too (fix round 4, `_classifier_slot`): a 4B verdict is a generation and
    rule 3 makes no exemption for it; it is a separate, short hold before the generation's own.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._cv = threading.Condition(threading.Lock())
        self._queue: deque[str] = deque()
        self._holder: tuple[str, str] | None = None  # (engine key, task id)
        self._clock = clock

    @property
    def holder(self) -> tuple[str, str] | None:
        with self._cv:
            return self._holder

    @property
    def queue(self) -> tuple[str, ...]:
        with self._cv:
            return tuple(self._queue)

    @contextlib.contextmanager
    def acquire(self, key: str, *, task_id: str, timeout_s: float | None = None) -> Iterator[None]:
        deadline = None if timeout_s is None else self._clock() + timeout_s
        with self._cv:
            self._queue.append(task_id)
            try:
                while self._holder is not None or self._queue[0] != task_id:
                    remaining = None if deadline is None else deadline - self._clock()
                    if remaining is not None and remaining <= 0:
                        held = self._holder
                        raise ArbiterError(
                            f"task {task_id}: {key} waited {timeout_s:.0f}s for the generation slot behind "
                            f"{held[0] if held else '?'} (task {held[1] if held else '?'}); "
                            f"{len(self._queue) - 1} ahead in the FIFO (Section 4.2 rule 4)"
                        )
                    self._cv.wait(timeout=None if remaining is None else min(remaining, 1.0))
                self._queue.popleft()
                self._holder = (key, task_id)
            except BaseException:
                if task_id in self._queue:
                    self._queue.remove(task_id)
                self._cv.notify_all()
                raise
        try:
            yield
        finally:
            with self._cv:
                self._holder = None
                self._cv.notify_all()


@dataclass
class AppDeps:
    config: AtlasConfig
    ledger: Ledger
    arbiter: Arbiter
    router: RouterLike
    build_system_prompt: PromptBuilderFn
    streamer_for: Callable[[EngineSpec], EngineStreamer]
    vault: VaultController
    sessions: SessionTags
    approval: ApprovalQueue
    memory: MemoryStore | None = None
    notify: Callable[..., Any] | None = None
    load_wait_s: float = DEFAULT_LOAD_WAIT_S
    generation_wait_s: float = DEFAULT_GENERATION_WAIT_S
    write_chat_turns: bool = True
    chat_turn_ttl_hours: float = CHAT_TURN_TTL_HOURS  # D9: the raw turn lives as long as the chat it came from
    enqueue: Callable[[str, dict[str, Any]], str | None] | None = None  # Celery send (aegis trigger, Deep Think)
    ready: bool = True
    # The ONE generation slot of 4.2 rule 3 for every generation this process makes, resident small models included
    # (see GenerationSlot). Built per AppDeps so tests get their own.
    slot: GenerationSlot = field(default_factory=lambda: GenerationSlot())
    # Admin routes (everything but /health and /v1/*): `admin_token` from ORCH_ADMIN_TOKEN_FILE, compared in constant
    # time against X-Atlas-Token; with no token only `trusted_hosts` (loopback) may call them. /internal/* is always
    # limited to `trusted_hosts`.
    admin_token: str | None = None
    # /internal/*'s own shared secret (ORCH_INTERNAL_TOKEN_FILE; fix round 4): required on top of loopback when set.
    internal_token: str | None = None
    trusted_hosts: frozenset[str] = LOOPBACK_HOSTS
    extra: dict[str, Any] = field(default_factory=dict)


# --- request models ---------------------------------------------------------------------------------------------------


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    atlas_override: str | None = None
    atlas_vault: bool = False
    atlas_session: str | None = None
    atlas_user: str | None = None
    atlas_task_id: str | None = None
    # /internal only: "principal" when the text will reach the Principal (a Sentinel BLUF pushed to the phone), so the
    # never-delegate rewrite (16.1 rule 5) runs on it too; None/"internal" for text another process consumes.
    atlas_audience: str | None = None
    # /internal only (fix round 4): the hemisphere of the text being generated on (HEMISPHERES), so a failure's scar
    # is bound to it; absent = undeclared, strike ledger-only with the prompt withheld (module docstring).
    atlas_hemisphere: str | None = None
    metadata: dict[str, Any] | None = None


class VaultOpenRequest(BaseModel):
    passphrase: str = Field(repr=False)


class ApprovalDecision(BaseModel):
    # No default (fix round): the ledger must record WHO decided; "approved by principal" is never assumed.
    decided_by: str = Field(min_length=1)
    note: str | None = None


class RegisterRequest(BaseModel):
    engine: str
    total_bytes: int = Field(ge=0)
    task_id: str | None = None


class LoadRequest(BaseModel):
    engine: str
    ctx: int | None = Field(default=None, ge=1)
    parallel: int | None = Field(default=None, ge=1)
    task_id: str | None = None


class UnloadRequest(BaseModel):
    engine: str
    task_id: str | None = None


class StrikeRequest(BaseModel):
    persona: str
    domain: str = "general"
    context: str = ""
    error: str
    correction: str = ""
    kind: str = "manual"
    source: str | None = None
    task_id: str | None = None
    hemisphere: str | None = None
    session_id: str | None = None


class RouteRequest(BaseModel):
    message: str
    force_persona: str | None = None


class PlanRequest(BaseModel):
    tier: str = "standard"
    task_id: str | None = None


# --- helpers ----------------------------------------------------------------------------------------------------------


def _last_user_text(messages: Sequence[Mapping[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
            return str(c or "")
    return ""


def _chunk(
    cid: str,
    model: str,
    created: int,
    *,
    content: str | None = None,
    role: str | None = None,
    finish: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    out: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if extra:
        out.update(extra)
    return out


def _sse(obj: Mapping[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def _text_of(content: Any) -> str:
    if isinstance(content, list):  # multimodal: text parts only
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def history_has_vault_prefix(messages: Sequence[Mapping[str, Any]]) -> bool:
    """True when [VAULT] appears in ANY user message of the chat (10.5: tagged for the life of the session). The same
    rule as retention.is_vault_chat, so a filter-less client and a restarted Open WebUI (whose filter forgets its
    in-process set) are covered by the history Open WebUI resends with every turn. Anywhere in the text, not only at
    its start (fix round 2): an Open WebUI task call carries the chat history INSIDE one user message."""
    return any(m.get("role") == "user" and VAULT_PREFIX in _text_of(m.get("content")).upper() for m in messages)


def is_owui_task_call(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Open WebUI 0.11.4 fires title/tags/follow-up/autocomplete generations after every turn as a plain chat
    completion on the chat's model whose last user message starts with "### Task:" (OWUI_TASK_MARKER) and embeds the
    whole history. They are UI chores, not Principal turns: the pipeline runs them on the routed engine with no
    retrieval, no never-delegate rewrite, no memory write and no strike on failure."""
    return _last_user_text(messages).lstrip().startswith(OWUI_TASK_MARKER)


def _session_of(req: ChatRequest, request: Request | None) -> tuple[str, bool, str | None]:
    meta = (req.metadata or {}).get("atlas") if isinstance(req.metadata, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    headers = request.headers if request is not None else {}
    # Channels, in order: the filter's top-level fields (VERIFIED to survive Open WebUI 0.11.4, fix round); the
    # X-OpenWebUI-Chat-Id header (sent when ENABLE_FORWARD_USER_INFO_HEADERS=true, env.py); the metadata mirror
    # (dead for Open WebUI, which pops `metadata` before the backend call; kept for other clients).
    session = (
        req.atlas_session
        or headers.get("x-atlas-session")
        or headers.get("x-openwebui-chat-id")
        or meta.get("atlas_session")
        or (req.metadata or {}).get("chat_id")
        or uuid.uuid4().hex
    )
    vault = bool(
        req.atlas_vault
        or meta.get("atlas_vault")
        or headers.get("x-atlas-vault", "").lower() == "true"
        or history_has_vault_prefix(req.messages)
    )
    override = req.atlas_override or meta.get("atlas_override") or headers.get("x-atlas-override") or None
    return str(session), vault, override


def _routed_message(config: AtlasConfig, model: str, message: str, override: str | None) -> str:
    """What the router sees. "ren"/"arthur" force the hemisphere through the router's own override prefix (7.2 rule
    4) unless the message already carries one; a prefix the filter forwarded but the message lost is put back."""
    text = message.strip()
    if override and override.startswith("[") and parse_override(text, config.router_rules.overrides) is None:
        text = f"{override} {text}"
    forced = LEAD_OVERRIDE.get(model)
    if forced and parse_override(text, config.router_rules.overrides) is None:
        text = f"{forced} {text}"
    return text


def _prompt_text(built: Any) -> str:
    return built if isinstance(built, str) else str(getattr(built, "text", built))


def _item_dict(item: ApprovalItem) -> dict[str, Any]:
    d = dataclasses.asdict(item)
    if item.register is not None:
        d["register"] = str(item.register)
    return d


def _client_host(request: Request) -> str:
    return request.client.host if request.client is not None else ""


class _Rewriter:
    """The never-delegate pass over a stream (16.1 rule 5). Text is buffered to sentence boundaries; every complete
    sentence goes through governance.never_delegate_rewrite with the PRINCIPAL register, so the Principal reads the
    rewritten sentence, never the raw one. `flush()` at the end handles the tail. Rewrites and flags are kept for
    the ledger row."""

    def __init__(self, audience: str = "principal") -> None:
        self.audience = audience
        self.buffer = ""
        self.rewrites: list[tuple[str, str]] = []
        self.flagged: list[str] = []
        self.register = str(register("", audience).register)

    def _apply(self, text: str) -> str:
        if not text:
            return text
        res: RewriteResult = never_delegate_rewrite(text, self.audience)
        self.rewrites.extend(res.rewrites)
        self.flagged.extend(res.flagged)
        return res.text

    def feed(self, delta: str) -> str:
        self.buffer += delta
        matches = list(_SENTENCE_END.finditer(self.buffer))
        if not matches:
            return ""
        # Cut BEFORE the separator: the rewrite keeps a piece's leading whitespace but not a trailing one, so the
        # separator travels at the head of the next chunk and the author's layout survives.
        cut = matches[-1].start()
        ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return self._apply(ready)

    def flush(self) -> str:
        ready, self.buffer = self.buffer, ""
        return self._apply(ready)

    def summary(self) -> dict[str, Any]:
        return {
            "register": self.register,
            "rewrites": len(self.rewrites),
            "flagged": list(self.flagged)[:20],
        }


# --- the app ----------------------------------------------------------------------------------------------------------


def build_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(title="A.T.L.A.S. orchestrator", version=__version__)
    app.state.deps = deps

    # --- authentication (fix round) -------------------------------------------------------------------------------

    def require_loopback(request: Request) -> None:
        """/internal/*: loopback only, whatever the bind and whatever the admin token (engine-keyed generation); and
        the internal shared secret on top when ORCH_INTERNAL_TOKEN_FILE is configured (fix round 4)."""
        host = _client_host(request)
        if host not in deps.trusted_hosts:
            raise HTTPException(403, f"internal routes accept loopback clients only (got {host or 'unknown'})")
        if deps.internal_token:
            given = request.headers.get(INTERNAL_TOKEN_HEADER)
            if not given:
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    given = auth[7:].strip()
            if not given:
                raise HTTPException(
                    401,
                    f"missing {INTERNAL_TOKEN_HEADER} / Authorization: Bearer (ORCH_INTERNAL_TOKEN_FILE is configured)",
                )
            if not hmac.compare_digest(given.encode("utf-8"), deps.internal_token.encode("utf-8")):
                raise HTTPException(403, "the internal token does not match")

    def check_token(request: Request) -> None:
        """X-Atlas-Token, or Authorization: Bearer <token> (what Open WebUI sends OPENAI_API_KEY as)."""
        assert deps.admin_token
        given = request.headers.get(ADMIN_TOKEN_HEADER)
        if not given:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                given = auth[7:].strip()
        if not given:
            raise HTTPException(
                401, f"missing {ADMIN_TOKEN_HEADER} / Authorization: Bearer (ORCH_ADMIN_TOKEN_FILE is configured)"
            )
        if not hmac.compare_digest(given.encode("utf-8"), deps.admin_token.encode("utf-8")):
            raise HTTPException(403, "the admin token does not match")

    def require_admin(request: Request) -> None:
        """Admin routes: the token when one is configured, else loopback only (module docstring)."""
        if deps.admin_token:
            check_token(request)
            return
        host = _client_host(request)
        if host not in deps.trusted_hosts:
            raise HTTPException(
                403,
                f"admin routes accept loopback clients only until ORCH_ADMIN_TOKEN_FILE is configured "
                f"(got {host or 'unknown'})",
            )

    def require_chat(request: Request) -> None:
        """/v1/chat/completions (fix round 2): loopback always; off loopback the token, whenever one is configured.
        Without a token the route stays as open as the bind (ORCH_HOST defaults to 127.0.0.1)."""
        if deps.admin_token and _client_host(request) not in deps.trusted_hosts:
            check_token(request)

    def require_vault_transport(request: Request) -> None:
        """The passphrase crosses the wire in the request body (Section 11): off loopback only over TLS."""
        if _client_host(request) in deps.trusted_hosts or request.url.scheme == "https":
            return
        raise HTTPException(
            403,
            "POST /vault/open accepts the passphrase from loopback (the host-networked Open WebUI) or over TLS only; "
            "a LAN/WireGuard bind needs TLS terminated in front of the orchestrator (module docstring)",
        )

    admin = [Depends(require_admin)]
    internal = [Depends(require_loopback)]
    chat = [Depends(require_chat)]

    @app.get("/health")
    def health() -> JSONResponse:
        status = {
            "status": "ok" if deps.ready else "starting",
            "version": __version__,
            "arbiter_halted": deps.arbiter.status().get("halted"),
            "memory": deps.memory is not None,
            "outbound_channel": not isinstance(deps.approval.sender, NoChannelSender),
            "admin_token": bool(deps.admin_token),
            "generating": deps.slot.holder,
            "generation_queue": list(deps.slot.queue),
        }
        return JSONResponse(status, status_code=200 if deps.ready else 503)

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        now = int(time.time())
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": now, "owned_by": "atlas"} for m in MODELS],
        }

    @app.post("/v1/chat/completions", dependencies=chat)
    def chat_completions(req: ChatRequest, request: Request) -> Any:
        if not req.messages:
            raise HTTPException(422, "messages[] is empty")
        if req.model not in MODELS:
            # Engine keys included (fix round 2): generation on a named engine is /internal/v1/chat/completions,
            # loopback only, so no client of the public route can step around the router and the governance path.
            hint = (
                "; engine keys are served on POST /internal/v1/chat/completions (loopback only)"
                if req.model in deps.config.engines
                else ""
            )
            raise HTTPException(404, f"model {req.model!r} is not served; /v1/models lists {list(MODELS)}{hint}")
        session_id, vault_flag, override = _session_of(req, request)
        if vault_flag:
            deps.sessions.tag_vault(session_id)
        gen = _chat_pipeline(
            deps,
            req,
            session_id=session_id,
            override=override,
            internal=False,
            owui_task=is_owui_task_call(req.messages),
        )
        return _respond(gen, req.stream)

    @app.post("/internal/v1/chat/completions", dependencies=internal)
    def internal_completions(req: ChatRequest) -> Any:
        if req.model not in deps.config.engines:
            raise HTTPException(404, f"unknown engine {req.model!r} (CONVENTIONS.md §8 keys)")
        if not req.messages:
            raise HTTPException(422, "messages[] is empty")
        if req.atlas_hemisphere is not None and req.atlas_hemisphere not in HEMISPHERES:
            raise HTTPException(
                422, f"atlas_hemisphere must be one of {sorted(HEMISPHERES)} (got {req.atlas_hemisphere!r})"
            )
        gen = _chat_pipeline(deps, req, session_id=req.atlas_session or "internal", override=None, internal=True)
        return _respond(gen, req.stream)

    @app.post("/internal/route", dependencies=internal)
    def internal_route(req: RouteRequest) -> dict[str, Any]:
        task_id = new_task_id()
        text = _routed_message(deps.config, req.force_persona or "atlas", req.message, None)
        try:
            with _classifier_slot(deps, task_id):  # the verdict is a generation (4.2 rule 3; module docstring)
                decision = deps.router.route(text, task_id=task_id)
            info = RouteInfo.from_decision(decision, req.message)
        except (ConfigError, RouterError, RuntimeError, ArbiterError) as exc:
            raise HTTPException(500, f"router: {exc}") from exc
        return {"task_id": task_id, **info.as_dict()}

    @app.post("/internal/deep-think/plan", dependencies=internal)
    def internal_plan(req: PlanRequest) -> dict[str, Any]:
        if req.tier not in DEEP_THINK_TIERS:
            raise HTTPException(422, f"tier must be one of {DEEP_THINK_TIERS}")
        _maybe_remeasure(deps)
        plan = deep_think.plan(req.tier, deps.arbiter, task_id=req.task_id)
        resident = [k for k in deps.arbiter.resident if k != APEX_KEY]
        return {
            "requested": plan.requested,
            "granted": plan.granted,
            "engines": list(plan.engines),
            "required_bytes": plan.required_bytes,
            "budget_bytes": plan.budget_bytes,
            "reason": plan.reason,
            "resident_engine": resident[0] if resident else None,
        }

    # --- vault (Section 11; README-contracts.md) ----------------------------------------------------------------

    @app.post("/vault/open", dependencies=[*admin, Depends(require_vault_transport)])
    def vault_open(req: VaultOpenRequest) -> JSONResponse:
        try:
            res = deps.vault.open(req.passphrase)
        except VaultError as exc:
            # The helper's stderr stays in the journal (vault.py logs it); the client gets a fixed message.
            log.error("vault open: %s", exc)
            raise HTTPException(500, "atlas-vault open failed; see the orchestrator journal") from exc
        return JSONResponse(res.as_dict(), status_code=200 if res.ok else 403)

    @app.post("/vault/lock", dependencies=admin)
    def vault_lock() -> dict[str, Any]:
        try:
            return deps.vault.lock().as_dict()
        except VaultError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.get("/vault/status", dependencies=admin)
    def vault_status() -> dict[str, Any]:
        res = deps.vault.status()
        return {"state": res.state, "message": res.message, "vault_sessions": deps.sessions.vault_sessions()}

    # --- approvals (16.2) -----------------------------------------------------------------------------------------

    @app.get("/approvals", dependencies=admin)
    def approvals(status: str = "held", limit: int = 100) -> dict[str, Any]:
        rows = deps.ledger.list_approvals(status=None if status == "all" else status, limit=limit)
        return {"status": status, "count": len(rows), "items": rows}

    def _decide(approval_id: int, verb: str, body: ApprovalDecision) -> dict[str, Any]:
        fn = deps.approval.approve if verb == "approve" else deps.approval.reject
        try:
            item = fn(approval_id, decided_by=body.decided_by, note=body.note)
        except (NotPending, CrossCheckRequired) as exc:
            raise HTTPException(409, str(exc)) from exc
        except SendError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ApprovalError as exc:
            raise HTTPException(404, str(exc)) from exc
        out = _item_dict(item)
        out["dispatched"] = item.was_sent
        return out

    @app.post("/approvals/{approval_id}/approve", dependencies=admin)
    def approve(approval_id: int, body: ApprovalDecision) -> dict[str, Any]:
        return _decide(approval_id, "approve", body)

    @app.post("/approvals/{approval_id}/reject", dependencies=admin)
    def reject(approval_id: int, body: ApprovalDecision) -> dict[str, Any]:
        return _decide(approval_id, "reject", body)

    # --- arbiter (4.2) --------------------------------------------------------------------------------------------

    def _decision_dict(dec: Any) -> dict[str, Any]:
        return {
            "engine": dec.engine,
            "decision": dec.decision.value,
            "granted": bool(dec.granted),
            "reason": dec.reason,
            "projected_bytes": int(getattr(dec, "projected_bytes", 0) or 0),
            "task_id": dec.task_id,
        }

    @app.post("/arbiter/register", dependencies=admin)
    def arbiter_register(req: RegisterRequest) -> dict[str, Any]:
        try:
            deps.arbiter.register_measured(req.engine, req.total_bytes, task_id=req.task_id)
        except UnknownEngine as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"engine": req.engine, "total_bytes": req.total_bytes, "registered": True}

    @app.post("/arbiter/load", dependencies=admin)
    def arbiter_load(req: LoadRequest) -> dict[str, Any]:
        """A load through the Arbiter (4.2 rule 2): granted means resident and serving when this returns; queued
        after `load_wait_s` and refused are real decisions the caller must honour (phase3/loadtest.py does)."""
        if req.engine not in deps.config.engines:
            raise HTTPException(404, f"unknown engine {req.engine!r} (CONVENTIONS.md §8 keys)")
        _maybe_remeasure(deps)
        try:
            dec = deps.arbiter.request_load(
                req.engine, req.ctx, req.parallel, task_id=req.task_id, wait_s=deps.load_wait_s
            )
        except UnknownEngine as exc:
            raise HTTPException(404, str(exc)) from exc
        except ReleaseTimeout as exc:
            raise HTTPException(503, f"arbiter halted: {exc}") from exc
        except ArbiterError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _decision_dict(dec)

    @app.post("/arbiter/unload", dependencies=admin)
    def arbiter_unload(req: UnloadRequest) -> dict[str, Any]:
        if req.engine not in deps.config.engines:
            raise HTTPException(404, f"unknown engine {req.engine!r} (CONVENTIONS.md §8 keys)")
        try:
            dec = deps.arbiter.request_unload(req.engine, task_id=req.task_id, wait_s=deps.load_wait_s)
        except UnknownEngine as exc:
            raise HTTPException(404, str(exc)) from exc
        except ReleaseTimeout as exc:
            raise HTTPException(503, f"memory not released; arbiter halted: {exc}") from exc
        except ArbiterError as exc:
            raise HTTPException(500, str(exc)) from exc
        return _decision_dict(dec)

    @app.post("/arbiter/remeasure", dependencies=admin)
    def arbiter_remeasure() -> dict[str, Any]:
        """Re-read the resident set (4.1) once the small models are up; refused while an engine is resident."""
        if deps.arbiter.resident:
            raise HTTPException(409, f"cannot re-measure while resident: {sorted(deps.arbiter.resident)}")
        try:
            measured = deps.arbiter.measure_resident_set()
        except ArbiterError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"resident_set_bytes": measured, "budget_bytes": deps.arbiter.budget_bytes}

    @app.get("/arbiter/status", dependencies=admin)
    def arbiter_status() -> dict[str, Any]:
        return deps.arbiter.status()

    # --- ouroboros (9.4) ------------------------------------------------------------------------------------------

    @app.post("/strike", dependencies=admin)
    def strike(req: StrikeRequest) -> dict[str, Any]:
        try:
            return record_strike(
                req.persona,
                req.domain,
                req.context,
                req.error,
                req.correction,
                ledger=deps.ledger,
                memory=deps.memory,
                kind=req.kind,
                source=req.source,
                task_id=req.task_id,
                hemisphere=req.hemisphere,
                session_id=req.session_id,
                vault=deps.sessions.is_vault(req.session_id),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


def _maybe_remeasure(deps: AppDeps) -> None:
    """Section 4.1: the budget is measured against the resident set 'before anything depends on it'. The orchestrator
    measures at start (Phase 2 step 2), before the three small models (step 4) and the voice models (step 5) exist,
    so whenever NO engine is resident the counter is re-read: with nothing loaded, GTT in use IS the resident set.
    A concurrent load makes measure_resident_set() raise; that is the race resolving itself, not an error."""
    if deps.arbiter.resident:
        return
    try:
        deps.arbiter.measure_resident_set()
    except ArbiterError as exc:
        log.debug("resident set not re-measured: %s", exc)


def _respond(gen: Iterator[dict[str, Any]], stream: bool) -> Any:
    if stream:

        def body() -> Iterator[bytes]:
            for chunk in gen:
                yield _sse(chunk)
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    parts: list[str] = []
    first: dict[str, Any] | None = None
    last: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    for chunk in gen:
        if "error" in chunk:
            error = chunk["error"]
            continue
        first = first or chunk
        last = chunk
        for c in chunk.get("choices") or []:
            d = (c.get("delta") or {}).get("content")
            if d:
                parts.append(d)
    if error is not None:
        return JSONResponse({"error": error}, status_code=int(error.get("status", 502)))
    if first is None:
        raise HTTPException(500, "no response produced")
    finish = next((c.get("finish_reason") for c in last.get("choices") or [] if c.get("finish_reason")), "stop")
    out: dict[str, Any] = {
        "id": first["id"],
        "object": "chat.completion",
        "created": first["created"],
        "model": first["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(parts)}, "finish_reason": finish}],
    }
    if "atlas" in first:
        out["atlas"] = first["atlas"]
    if "timings" in last:
        out["timings"] = last["timings"]
    return out


# --- the chat pipeline (Appendix A) -----------------------------------------------------------------------------------


def _error_chunk(cid: str, model: str, created: int, status: int, message: str, task_id: str) -> dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "error": {"status": status, "message": message, "task_id": task_id, "type": "atlas_error"},
        "choices": [{"index": 0, "delta": {"content": f"\n[ATLAS] {message}\n"}, "finish_reason": "error"}],
    }


def _classifier_key(deps: AppDeps) -> str:
    rules = getattr(deps.config, "router_rules", None)
    return str(getattr(rules, "classifier_engine", None) or "router-qwen3.5-4b")


@contextlib.contextmanager
def _classifier_slot(deps: AppDeps, task_id: str) -> Iterator[None]:
    """The slot for the router's classifier verdict (7.2 rule 2 on router-qwen3.5-4b): a generation like any other
    under 4.2 rule 3 (fix round 4), held only for the verdict and released before the generation's own hold. Routing
    therefore queues behind a running generation exactly as the generation it precedes would have."""
    with deps.slot.acquire(_classifier_key(deps), task_id=task_id, timeout_s=deps.generation_wait_s):
        yield


@contextlib.contextmanager
def _generation_lock(deps: AppDeps, spec: EngineSpec, task_id: str) -> Iterator[None]:
    """The single generation slot (4.2 rules 3-4) for EVERY generation, resident small models included (fix round 2):
    `deps.slot` is one strict FIFO over all of them, so a 4B call (Sentinel BLUF, retention summary, LightRAG
    extraction) holds the slot exactly like gpt-oss does and a weight-bearing request queues behind it, and the other
    way round (9.7 C15 in both directions). The Arbiter LOAD of 4.2 rule 2 happens INSIDE the slot (fix round 4): a
    load granted before the slot could be evicted by a later request's load while the first still waited in the FIFO
    (the Arbiter protects a generating engine, rule 6, not a merely resident one), and the first would then stream to
    a stopped unit. Inside the slot the Arbiter's own lock is always free, so request_load returns or swaps at once
    and the swap of rules 4/6 is serialised in the one FIFO. A weight-bearing engine additionally takes the Arbiter's
    own lock, so the Arbiter still knows which engine is generating (rule 6, never preempted; its ledger view)."""
    with deps.slot.acquire(spec.key, task_id=task_id, timeout_s=deps.generation_wait_s):
        _maybe_remeasure(deps)
        decision_load = deps.arbiter.request_load(spec.key, task_id=task_id, wait_s=deps.load_wait_s)
        if not decision_load.granted:
            status = 503 if decision_load.decision is Decision.QUEUED else 507
            raise ArbiterError(f"engine {spec.key} not loaded: {decision_load.reason}", status)
        if spec.is_resident:
            yield  # outside the Arbiter's residency ledger by design (4.1, 5.3), inside the one slot
            return
        with deps.arbiter.acquire_generation(spec.key, task_id=task_id, timeout_s=deps.generation_wait_s):
            yield


def _retrieve(deps: AppDeps, info: RouteInfo) -> dict[str, list[str]]:
    """Layer 4 (4.4): the closest scars (9.4) and the hemisphere's own memory; a retrieval failure is logged, not
    hidden, and the dispatch continues without it. Scars carry NO persona filter (fix round): 9.4 injects 'the
    closest few scars above a similarity threshold', so the Principal's own [LOG STRIKE:] scars and automatic ones
    from any persona reach every dispatch; similarity decides, not authorship. They DO carry the hemisphere (fix
    round 2, inside retrieve_scars): a scar records the Principal's message as its context, and an estate scar in a
    corporate prompt would carry estate words across the membrane (7.3, 10.1)."""
    out: dict[str, list[str]] = {"memory": [], "scars": []}
    if deps.memory is None:
        return out
    try:
        out["scars"] = [
            s.as_prompt_line() for s in retrieve_scars(info.message, memory=deps.memory, hemisphere=info.hemisphere)
        ]
    except MemoryStoreError as exc:
        log.error("scar retrieval failed: %s", exc)
    try:
        out["memory"] = [
            h.document for h in deps.memory.query(info.hemisphere, info.message, k=4, hemisphere=info.hemisphere)
        ]
    except MemoryStoreError as exc:
        log.error("memory retrieval failed: %s", exc)
    return out


def _forced_lead_redirect(req_model: str, info: RouteInfo, *, task_id: str) -> str:
    """7.2 rule 1 over the model picker (fix round): `model=ren` is a preference the router's hard keywords overrule
    (router.py records "overruled-by-hard-rule" in the routing_decisions row, which is the logging 7.2 rule 1 asks
    for). The Principal is told why. No strike (fix round 2): 9.4's "overridden routing decision" is a router
    decision the Principal had to override, something to learn from; the hard rule doing exactly what rule 1 mandates
    is not an error and has no correction, and every such scar would be one more of the irrelevant pile 9.4 warns
    about. Returns the note to show (empty when nothing happened)."""
    forced = LEAD_OVERRIDE.get(req_model)
    if forced is None or info.hard_keyword_hit is None or info.persona == req_model:
        return ""
    log.info(
        "task %s: model picker %r overruled by hard keyword %r -> %s (7.2 rule 1)",
        task_id,
        req_model,
        info.hard_keyword_hit,
        info.persona,
    )
    return (
        f"[ATLAS] Routed to {info.persona.title()} on {info.engine}: the hard keyword "
        f"{info.hard_keyword_hit!r} (7.2 rule 1) overrides the {req_model!r} model selection.\n\n"
    )


def _chat_pipeline(
    deps: AppDeps,
    req: ChatRequest,
    *,
    session_id: str,
    override: str | None,
    internal: bool,
    owui_task: bool = False,
) -> Iterator[dict[str, Any]]:
    """A generator of OpenAI chunks; sync on purpose (Starlette iterates it in a worker thread, so the Arbiter waits
    never block the event loop). `owui_task`: an Open WebUI title/tags/follow-up call (is_owui_task_call): routed
    engine, no retrieval, no rewrite, no memory write, no strike."""
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    message = _last_user_text(req.messages)
    ledger = deps.ledger
    kind = "internal-generate" if internal else ("owui-task" if owui_task else "chat")
    if internal:
        # A child row (fix round): the caller's atlas_task_id is the PARENT (a Celery task still running); this
        # generation must never mark that row done or failed mid-flight.
        task_id = new_task_id()
        ledger.insert_task(
            kind,
            task_id=task_id,
            status="running",
            queue="api",
            parent_task_id=req.atlas_task_id,
            payload={"model": req.model, "session": session_id, "chars": len(message)},
        )
    else:
        task_id = req.atlas_task_id or new_task_id()
        if ledger.get_task(task_id) is None:
            ledger.insert_task(
                kind,
                task_id=task_id,
                status="running",
                queue="api",
                payload={
                    "model": req.model,
                    "session": session_id,
                    "chars": len(message),
                    "vault": deps.sessions.is_vault(session_id),
                    "owui_task": owui_task,
                },
            )
        else:
            ledger.update_task(task_id, status="running")
    info: RouteInfo | None = None
    streamer: Any = None
    rewriter: _Rewriter | None = None
    # The never-delegate pass (16.1 rule 5) runs on everything the PRINCIPAL will read: every chat turn, and an
    # internal generation whose caller declared the audience "principal" (a Sentinel BLUF pushed to the phone).
    # Open WebUI task calls produce JSON/titles the UI parses, never Principal prose: no pass (it would mangle them).
    write_turn = not internal and not owui_task
    # /internal: the caller's declared hemisphere (fix round 4); "" = undeclared, so a failure records no scar and
    # withholds the prompt (module docstring; _fail).
    hemisphere_declared = not internal or req.atlas_hemisphere is not None
    try:
        if internal:
            spec = deps.config.engine(req.model)
            info = RouteInfo(
                persona=req.atlas_user or "internal",
                engine=spec.key,
                hemisphere=req.atlas_hemisphere or "",
                tier="routine",
                route="internal",
                message=message,
            )
            ledger.update_task(task_id, engine=spec.key, persona=info.persona, tier=info.tier)
            messages: list[dict[str, Any]] = list(req.messages)
            if (req.atlas_audience or "").strip().lower() == "principal":
                rewriter = _Rewriter("principal")
            yield _chunk(
                cid,
                req.model,
                created,
                role="assistant",
                extra={"atlas": {"task_id": task_id, "parent_task_id": req.atlas_task_id, "engine": spec.key}},
            )
        else:
            routed = _routed_message(deps.config, req.model, message, override)
            with _classifier_slot(deps, task_id):  # the classifier verdict is a generation (4.2 rule 3)
                decision = deps.router.route(routed, task_id=task_id, context={"session_id": session_id})
            info = RouteInfo.from_decision(decision, message)
            ledger.update_task(task_id, persona=info.persona, engine=info.engine, tier=info.tier)
            note = ""
            if info.command == "vault-session":
                # 10.5 / 11: the tag is the rule, not the loss of the message (fix round 2). Tag first, so the memory
                # write at the end of THIS turn is already dropped; an empty body gets the notice alone.
                note = _command(deps, info, session_id=session_id, task_id=task_id)
                if not info.message.strip():
                    yield _chunk(
                        cid,
                        req.model,
                        created,
                        role="assistant",
                        content=note,
                        finish="stop",
                        extra={"atlas": {"task_id": task_id, **info.as_dict()}},
                    )
                    ledger.update_task(task_id, status="done", result={"command": info.command})
                    return
                note += "\n\n"
            elif info.command in ("ouroboros-strike", "aegis"):
                text = _command(deps, info, session_id=session_id, task_id=task_id)
                yield _chunk(
                    cid,
                    req.model,
                    created,
                    role="assistant",
                    content=text,
                    finish="stop",
                    extra={"atlas": {"task_id": task_id, **info.as_dict()}},
                )
                ledger.update_task(task_id, status="done", result={"command": info.command})
                return
            spec = deps.config.engine(info.engine)
            cards = [deps.config.domain_cards[n] for n in info.domain_cards if n in deps.config.domain_cards]
            retrieved = None if owui_task else _retrieve(deps, info)
            built = deps.build_system_prompt(info.decision or info.persona, cards, retrieved)
            system_prompt = _prompt_text(built)
            messages = [{"role": "system", "content": system_prompt}] + [
                dict(m) for m in req.messages if m.get("role") != "system"
            ]
            if info.message != message:
                for m in reversed(messages):
                    if m.get("role") == "user":
                        m["content"] = info.message  # the override prefix is the router's, not the engine's
                        break
            note += _forced_lead_redirect(req.model, info, task_id=task_id)
            head = {"task_id": task_id, **info.as_dict()}
            head["forced_lead"] = req.model if req.model in LEAD_OVERRIDE else None
            head["owui_task"] = owui_task
            if not owui_task:
                rewriter = _Rewriter("principal")
            if info.command == "deep-think":
                tier = info.deep_think or "standard"
                tid = _enqueue_deep_think(deps, tier, info, task_id=task_id, session_id=session_id)
                if tid is not None:
                    # 9.7: the interface returns at once; 9.1: nothing is hard-capped. The answer lands in the
                    # ledger row of the Celery task (result column) and ntfy reports it (deep_think_task).
                    text = (
                        f"{note}Deep Think ({tier}) is running in the background as task {tid} on the gpu queue; "
                        f"it takes {'5 to 8' if tier == 'standard' else '15 to 30'} minutes and swaps engines "
                        f"(9.1). The answer is stored on that task's ledger row (sqlite3 "
                        f"{deps.config.settings.db_path}: tasks.id = {tid!r}) and pushed through ntfy when done."
                    )
                    yield _chunk(
                        cid,
                        req.model,
                        created,
                        role="assistant",
                        content=text,
                        finish="stop",
                        extra={"atlas": {**head, "deep_think_task_id": tid}},
                    )
                    ledger.update_task(
                        task_id, status="done", result={"deep_think": tier, "celery_task_id": tid, "enqueued": True}
                    )
                    return
                yield _chunk(cid, req.model, created, role="assistant", content=note or None, extra={"atlas": head})
                text = _run_deep_think(deps, tier, info, task_id)
                text = rewriter.feed(text) + rewriter.flush() if rewriter is not None else text
                yield _chunk(cid, req.model, created, content=text, finish="stop")
                ledger.update_task(
                    task_id,
                    status="done",
                    result={
                        "deep_think": tier,
                        "chars": len(text),
                        "inline": True,
                        "governance": rewriter.summary() if rewriter is not None else None,
                    },
                )
                if write_turn:
                    _remember_turn(deps, info, session_id, message, text)
                return
            yield _chunk(cid, req.model, created, role="assistant", content=note or None, extra={"atlas": head})
        # The single generation slot, then INSIDE it the Engine Arbiter load (4.2 rule 2; _generation_lock) and the
        # stream: the streamer is built once the engine is resident, never before.
        parts: list[str] = []
        timings: dict[str, Any] | None = None
        finish: str | None = None
        temperature = req.temperature
        if temperature is None and info.persona in deps.config.personas:
            t = deps.config.personas[info.persona].sampling_temperature
            # arthur.md says "low-not-zero" (9.1: reasoning models loop at exactly zero).
            temperature = t if isinstance(t, (int, float)) else (0.15 if isinstance(t, str) else None)
        with _generation_lock(deps, spec, task_id):
            streamer = deps.streamer_for(spec)
            for chunk in streamer.stream(
                messages, temperature=temperature, max_tokens=req.max_tokens or DEFAULT_MAX_TOKENS
            ):
                for c in chunk.get("choices") or []:
                    d = (c.get("delta") or {}).get("content")
                    if d:
                        out = rewriter.feed(d) if rewriter is not None else d
                        if out:
                            parts.append(out)
                            yield _chunk(cid, req.model, created, content=out)
                    if c.get("finish_reason"):
                        finish = c["finish_reason"]
                if isinstance(chunk.get("timings"), dict):
                    timings = chunk["timings"]
        tail = rewriter.flush() if rewriter is not None else ""
        if tail:
            parts.append(tail)
            yield _chunk(cid, req.model, created, content=tail)
        text = "".join(parts)
        yield _chunk(cid, req.model, created, finish=finish or "stop", extra={"timings": timings} if timings else None)
        result: dict[str, Any] = {"chars": len(text), "finish": finish, "timings": timings}
        if rewriter is not None:
            result["governance"] = rewriter.summary()
            if rewriter.flagged:
                log.warning("task %s: never-delegate pass flagged %d sentence(s)", task_id, len(rewriter.flagged))
        ledger.update_task(task_id, status="done", result=result)
        if write_turn:
            _remember_turn(deps, info, session_id, message, text)
    except ReleaseTimeout as exc:
        # Rule 5 failure: the Arbiter is halted; nothing more loads until a human looks. Say so, loudly.
        _fail(deps, task_id, info, message, exc, status=503, session_id=session_id, strike=not owui_task,
              internal=internal, hemisphere_declared=hemisphere_declared)
        yield _error_chunk(cid, req.model, created, 503, f"engine memory not released; arbiter halted: {exc}", task_id)
    except ArbiterError as exc:
        status = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], int) else 503
        _fail(deps, task_id, info, message, exc, status=status, session_id=session_id, strike=not owui_task,
              internal=internal, hemisphere_declared=hemisphere_declared)
        yield _error_chunk(cid, req.model, created, status, str(exc.args[0]), task_id)
    except (EngineError, ConfigError, RouterError, RuntimeError, httpx.HTTPError) as exc:
        _fail(deps, task_id, info, message, exc, status=502, session_id=session_id, strike=not owui_task,
              internal=internal, hemisphere_declared=hemisphere_declared)
        yield _error_chunk(cid, req.model, created, 502, f"{type(exc).__name__}: {exc}", task_id)
    finally:
        _close_quietly(streamer)


def _enqueue_deep_think(deps: AppDeps, tier: str, info: RouteInfo, *, task_id: str, session_id: str) -> str | None:
    """Standard and deep Deep Think go to the Celery gpu queue (atlas.tasks.deep_think; 9.7 "the chat interface
    returns to the Principal immediately"); quick stays inline (one engine, 1-2 minutes). Returns the Celery task id,
    or None when the tier runs inline: quick; a vault-tagged session (deep_think_task refuses it, 10.5: the answer
    would persist in the ledger and the result backend outside the vault, so the inline stream is the only honest
    path); or no Celery in this process (said in the log, never pretended)."""
    if tier not in ("standard", "deep"):
        return None
    if deps.sessions.is_vault(session_id):
        log.info("task %s: Deep Think %s runs inline: session %s is vault-tagged (10.5)", task_id, tier, session_id)
        return None
    if deps.enqueue is None:
        log.warning("task %s: Deep Think %s runs inline: Celery is not wired into this process", task_id, tier)
        return None
    try:
        tid = deps.enqueue(
            "atlas.tasks.deep_think",
            {
                "problem": info.message,
                "tier": tier,
                "parent_task_id": task_id,
                "session_id": session_id,
                "audience": "principal",
                "hemisphere": info.hemisphere,  # declared on every /internal generation the task makes (fix round 4)
            },
        )
    except Exception as exc:  # kombu OperationalError when Redis is down: run inline and say so
        log.error("task %s: Deep Think %s not enqueued (%s); running inline", task_id, tier, exc)
        deps.ledger.update_task(task_id, error=f"enqueue failed: {type(exc).__name__}: {exc}"[:500])
        return None
    return str(tid) if tid else None


def _close_quietly(streamer: Any) -> None:
    """HttpEngineStreamer holds an httpx.Client per request (fix round: closed here, never left to the GC)."""
    close = getattr(streamer, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # a close failure is worth a line, never a failed request
            log.warning("engine streamer close failed: %s", exc)


def _fail(
    deps: AppDeps,
    task_id: str,
    info: RouteInfo | None,
    message: str,
    exc: BaseException,
    *,
    status: int,
    session_id: str | None = None,
    strike: bool = True,
    internal: bool = False,
    hemisphere_declared: bool = True,
) -> None:
    err = f"{type(exc).__name__}: {exc.args[0] if exc.args else exc}"
    log.error("chat task %s failed (%d): %s", task_id, status, err)
    deps.ledger.update_task(task_id, status="failed", error=err)
    if not strike:
        return  # an Open WebUI title/tags chore that failed is a ledger row, not a 9.4 strike
    persona = info.persona if info else "atlas"
    domain = (info.task_force or info.route) if info else "routing"
    memory: MemoryStore | None = deps.memory
    context = message[:500]
    hemisphere = (info.hemisphere or None) if info else None
    if internal and not hemisphere_declared:
        # Fix round 4 (7.3, 10.1): an /internal prompt is a document chunk, a transcript or the Principal's problem
        # whose hemisphere this process does not know. Ledger row only, prompt withheld, no scar: a scar would be
        # bound to a guessed hemisphere and injected into that hemisphere's prompts (mirrors the vault branch).
        memory = None
        hemisphere = None
        context = "(internal generation: prompt withheld, no atlas_hemisphere declared; Sections 7.3, 10.1)"
    try:
        # 9.4 automatic strike: a failed generation is a strike. The session id travels with it (fix round): in a
        # vault-tagged session record_strike withholds the context from the ledger and writes no scar (10.5).
        record_strike(
            persona,
            domain,
            context,
            err,
            ledger=deps.ledger,
            memory=memory,
            kind="failed-generation",
            source="api",
            task_id=task_id,
            hemisphere=hemisphere,
            session_id=session_id,
            vault=deps.sessions.is_vault(session_id),
        )
    except Exception:
        log.exception("strike not recorded for task %s", task_id)


def _remember_turn(deps: AppDeps, info: RouteInfo, session_id: str, user_text: str, answer: str) -> None:
    """Appendix A "memory writes (unless vault-tagged)": the turn goes to the hemisphere collection as a TEMPORAL
    document with D9's 90-day life (CHAT_TURN_TTL_HOURS): the chat is summarised into the Vector Cortex at 90 days
    (retention.py) and the raw turn expires with it; prune.py deletes expired `chat-turn` documents without archiving
    them, so the purge of 10.4 holds (fix round 2). MemoryStore.write drops the write for a vault-tagged session."""
    if deps.memory is None or not deps.write_chat_turns or not answer.strip():
        return
    doc = f"User: {user_text.strip()[:2000]}\n{info.persona.title()}: {answer.strip()[:6000]}"
    try:
        deps.memory.write(
            info.hemisphere,
            [doc],
            [{"kind": "chat-turn", "persona": info.persona, "route": info.route, "task_force": info.task_force or ""}],
            hemisphere=info.hemisphere,
            session_id=session_id,
            temporal=True,
            ttl_hours=deps.chat_turn_ttl_hours,
        )
    except MemoryStoreError as exc:
        log.error("chat turn not remembered: %s", exc)


def _run_deep_think(deps: AppDeps, tier: str, info: RouteInfo, task_id: str) -> str:
    """Section 9.1 through the Arbiter: plan (rule 8), then every call loads and takes the generation lock."""
    if tier not in DEEP_THINK_TIERS:
        tier = "standard"
    _maybe_remeasure(deps)
    plan = deep_think.plan(tier, deps.arbiter, task_id=task_id)

    def call(
        engine: str, persona: str, messages: Sequence[Mapping[str, str]], *, temperature: float, max_tokens: int
    ) -> str:
        spec = deps.config.engine(engine)
        persona_prompt = _prompt_text(deps.build_system_prompt(persona, (), None))
        full = [{"role": "system", "content": persona_prompt}, *messages]
        streamer: Any = None
        parts: list[str] = []
        try:
            with _generation_lock(deps, spec, task_id):  # the load (rule 2, the swap of 9.1) happens inside the slot
                streamer = deps.streamer_for(spec)
                for chunk in streamer.stream(full, temperature=temperature, max_tokens=max_tokens):
                    for c in chunk.get("choices") or []:
                        d = (c.get("delta") or {}).get("content")
                        if d:
                            parts.append(d)
        finally:
            _close_quietly(streamer)
        return "".join(parts)

    resident = [k for k in deps.arbiter.resident if k != APEX_KEY]
    res = deep_think.run(
        plan.granted,
        info.message,
        call,
        resident_engine=resident[0] if resident else info.engine,
        resident_persona=info.persona,
        plan_used=plan,
    )
    header = "" if not plan.downgraded else f"[Deep Think ran at {plan.granted}: {plan.reason}]\n\n"
    return header + res.answer


def _command(deps: AppDeps, info: RouteInfo, *, session_id: str, task_id: str) -> str:
    body = info.message
    if info.command == "vault-session":
        deps.sessions.tag_vault(session_id)
        st = deps.vault.status()
        return (
            "[ATLAS] This session is now vault-tagged: nothing said here is written to memory (Section 10.5). "
            f"Vault is {st.state}." + ("" if st.state == "open" else " Open it with the vault button.")
        )
    if info.command == "ouroboros-strike":
        # The router's body is "<name>] <context>" for "[LOG STRIKE: <name>] <context>" (the prefix ends at the colon).
        # The scar is tagged to the ROUTED persona (fix round): the Principal logs the strike against whoever was
        # working; retrieval is by similarity anyway (_retrieve), so the tag is a label, not a filter.
        name, _, rest = body.partition("]")
        name = name.strip(" [")
        out = record_strike(
            info.persona,
            info.task_force or "manual",
            rest.strip() or "(no context given)",
            name or body,
            ledger=deps.ledger,
            memory=deps.memory,
            kind="manual",
            source="principal",
            task_id=task_id,
            hemisphere=info.hemisphere,
            session_id=session_id,
            vault=deps.sessions.is_vault(session_id),
        )
        scar = f"scar {out['scar_id']}" if out.get("scar_written") else f"no scar ({out.get('reason')})"
        return f"Strike #{out['strike_id']} logged; {scar}."
    if info.command == "aegis":
        if deps.enqueue is None:
            return "AEGIS manual backup cannot be enqueued: Celery is not wired into this process."
        try:
            tid = deps.enqueue("atlas.tasks.aegis_manual_backup", {"requested_by": "principal"})
        except Exception as exc:  # kombu.exceptions.OperationalError when Redis is down: say so, do not 500
            log.error("AEGIS manual backup not enqueued: %s", exc)
            deps.ledger.update_task(task_id, error=f"enqueue failed: {type(exc).__name__}: {exc}"[:500])
            return f"AEGIS backup could not be enqueued: {type(exc).__name__}: {exc} (is Redis up?)"
        return f"AEGIS backup requested (task {tid}); ntfy reports when the unit starts and the journal when it ends."
    return f"unknown command {info.command}"


# --- production wiring ------------------------------------------------------------------------------------------------


def build_production_deps() -> AppDeps:
    from atlas.arbiter import build_arbiter
    from atlas.config import load_config
    from atlas.engines import LlamaClient, SystemdEngineController
    from atlas.ledger import open_ledger
    from atlas.memory import build_memory_store
    from atlas.personas import PersonaRegistry
    from atlas.prompts import build_system_prompt
    from atlas.router import LlamaClassifier, Router
    from atlas.tasks import admin_token, internal_token, notify
    from atlas.vault import build_vault_controller

    config = load_config()
    # ORCH_ADMIN_TOKEN_FILE (fix round): a configured but unreadable file is a start-up failure, never a silent
    # loopback-only fallback; an unset variable means "loopback only" and is logged as such.
    token = admin_token()
    if token is None:
        log.warning(
            "ORCH_ADMIN_TOKEN_FILE is not set: admin routes (/vault, /approvals, /arbiter, /strike) accept loopback "
            "clients only; an interface on LAN/WireGuard needs the token (module docstring)"
        )
    ledger = open_ledger(config.settings.db_path)
    arbiter = build_arbiter(config.engines, ledger=ledger)
    # The Arbiter must own every weight-bearing load (4.2). A unit still active from a previous orchestrator life
    # cannot be accounted for, so it is stopped before the resident set is measured; the log says which.
    controller = SystemdEngineController(engines=config.engines)
    for spec in config.engines.values():
        if spec.is_resident:
            continue
        try:
            if controller.is_active(spec.key):
                log.warning(
                    "startup: %s is active from an earlier run; stopping it so the Arbiter owns the budget", spec.key
                )
                controller.stop(spec.key)
        except EngineError as exc:
            raise RuntimeError(f"startup: cannot query/stop {spec.systemd_unit}: {exc}") from exc
    arbiter.measure_resident_set()
    sessions = SessionTags.from_env()  # tmpfs mirror + the durable store (vault.py; survives a reboot)
    memory: MemoryStore | None = None
    try:
        memory = build_memory_store(sessions=sessions)
    except MemoryStoreError as exc:
        log.warning("memory store not available yet (%s); chat runs without retrieval until step 4 has run", exc)
    enqueue: Callable[[str, dict[str, Any]], str | None] | None = None
    try:
        from atlas.celery_app import app as celery_app

        def enqueue(name: str, kwargs: dict[str, Any]) -> str | None:
            return str(celery_app.send_task(name, kwargs=kwargs).id)
    except Exception as exc:
        log.warning("celery app not importable (%s); manual AEGIS trigger disabled", exc)
    personas = PersonaRegistry(config.personas, config.engines)
    classifier_spec = config.engines[config.router_rules.classifier_engine]
    # Eleanor's resident classifier (7.2 rule 2): the router itself tolerates it being down (defaults to Arthur).
    classifier = LlamaClassifier(LlamaClient.for_engine(classifier_spec), task_force_codes=tuple(config.task_forces))
    router = Router(config, classifier, ledger, personas=personas)
    approval = ApprovalQueue(ledger, NoChannelSender(), NtfyNotifier(notify), personas=personas)
    log.warning("approval queue: no outbound channel is wired (Section 13); approving records it and fails the send")

    def prompt_builder(decision: Any, cards: Sequence[Any] = (), memory: Any = None) -> Any:
        return build_system_prompt(decision, cards, memory, personas=personas)

    return AppDeps(
        config=config,
        ledger=ledger,
        arbiter=arbiter,
        router=router,
        build_system_prompt=prompt_builder,
        streamer_for=HttpEngineStreamer,
        vault=build_vault_controller(sessions=sessions, notify=notify),
        sessions=sessions,
        approval=approval,
        memory=memory,
        notify=notify,
        enqueue=enqueue,
        load_wait_s=float(os.environ.get("ATLAS_LOAD_WAIT_S") or DEFAULT_LOAD_WAIT_S),
        generation_wait_s=float(os.environ.get("ATLAS_GENERATION_WAIT_S") or DEFAULT_GENERATION_WAIT_S),
        admin_token=token,
        internal_token=internal_token(),  # None = loopback trust alone on /internal/* (module docstring)
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="atlas-orchestrator", description="A.T.L.A.S. orchestrator API")
    p.add_argument("--host", default=os.environ.get("ORCH_HOST") or "127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("ORCH_PORT") or 8800))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        deps = build_production_deps()
    except (ConfigError, RuntimeError, ArbiterError) as exc:
        print(f"atlas-orchestrator: cannot start: {exc}", file=sys.stderr)
        return 1
    import uvicorn

    # proxy_headers / forwarded_allow_ips are LOAD-BEARING for the loopback trust (module docstring, fix round 4):
    # typed explicitly, never left to uvicorn's defaults.
    uvicorn.run(
        build_app(deps),
        host=args.host,
        port=args.port,
        log_level="debug" if args.verbose else "info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
