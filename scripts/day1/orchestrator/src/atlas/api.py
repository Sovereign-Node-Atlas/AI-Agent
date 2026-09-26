"""The orchestrator's HTTP face (Section 12.1; Appendix A; systemd/atlas-orchestrator.service; console script
`atlas-orchestrator --host H --port P`, also `atlas-api`).

Endpoints
    GET  /health                       200 {"status":"ok"} when the app is wired (arbiter measured, ledger open)
    GET  /v1/models                    {"data":[{"id":"atlas"},{"id":"ren"},{"id":"arthur"}]} (12.1)
    POST /v1/chat/completions          OpenAI shape, stream true/false. "atlas" runs the 4-Way Router; "ren"/"arthur"
                                       force the hemisphere (the router's own [REN]/[ARTHUR] override, 7.2 rule 4).
                                       Pipeline (Appendix A): router -> layered prompt (prompts.build_system_prompt,
                                       4.4) -> ledger task -> Engine Arbiter load (waits in its queue) -> generation
                                       lock -> llama-server SSE relayed as OpenAI chunks -> memory write (unless
                                       vault-tagged) -> ledger done; on failure an Ouroboros strike. The router's
                                       `command` (deep-think, ouroboros-strike, aegis, vault-session) is honoured.
    POST /vault/open {passphrase}      pipes it to atlas-vault; never logged (Section 11); /vault/lock; /vault/status
    GET  /approvals[?status=held]      the queue (16.2); POST /approvals/{id}/approve|reject {decided_by, note}
    POST /arbiter/register             {engine, total_bytes, task_id}: Phase 3/4 record a measured footprint (rule 1)
    GET  /arbiter/status               the Arbiter's ledger view (4.2 rule 9)
    POST /strike                       {persona, domain, context, error, correction, kind}: Ouroboros (9.4)
    POST /internal/v1/chat/completions {model: <engine key>}: generation for the Celery tasks through the Arbiter
                                       (9.7 C15); loopback only, never listed in /v1/models
    POST /internal/route               {message} -> the router's decision as JSON (retention needs the hemisphere)
    POST /internal/deep-think/plan     {tier} -> the Arbiter's plan (4.2 rule 8)

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
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas import __version__, deep_think
from atlas.approval import ApprovalError, ApprovalItem, ApprovalQueue, CrossCheckRequired, NotPending, SendError
from atlas.arbiter import APEX_KEY, DEEP_THINK_TIERS, Arbiter, ArbiterError, Decision, ReleaseTimeout, UnknownEngine
from atlas.config import HEMISPHERES, AtlasConfig, ConfigError, EngineSpec
from atlas.engines import EngineError, _sse_events
from atlas.ledger import Ledger, new_task_id
from atlas.memory import MemoryStore, MemoryStoreError
from atlas.router import RouterError, RoutingDecision, parse_override
from atlas.tasks.ouroboros import record_strike, retrieve_scars
from atlas.vault import SessionTags, VaultController, VaultError

log = logging.getLogger("atlas.api")

MODELS: tuple[str, ...] = ("atlas", "ren", "arthur")  # Section 12.1
LEAD_OVERRIDE: dict[str, str] = {"ren": "[REN]", "arthur": "[ARTHUR]"}  # router-rules.json override keys
DEFAULT_LOAD_WAIT_S = 900.0  # a swap is 15-45 s (4.2 rule 4); a queued load waits behind a running generation
DEFAULT_GENERATION_WAIT_S = 1800.0
DEFAULT_MAX_TOKENS = 4096

__all__ = [
    "AppDeps",
    "EngineStreamer",
    "HttpEngineStreamer",
    "NoChannelSender",
    "NtfyNotifier",
    "RouteInfo",
    "RouterLike",
    "build_app",
    "build_production_deps",
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
            message=str(get("body") or get("message") or fallback_message),
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
    chat_turn_ttl_hours: float = 24 * 30  # 10.4: operational data 30 days hot; the 72 h prune archives on expiry
    enqueue: Callable[[str, dict[str, Any]], str | None] | None = None  # Celery send (aegis manual trigger)
    ready: bool = True
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
    metadata: dict[str, Any] | None = None


class VaultOpenRequest(BaseModel):
    passphrase: str = Field(repr=False)


class ApprovalDecision(BaseModel):
    decided_by: str = "principal"
    note: str | None = None


class RegisterRequest(BaseModel):
    engine: str
    total_bytes: int = Field(ge=0)
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


def _session_of(req: ChatRequest, request: Request | None) -> tuple[str, bool, str | None]:
    meta = (req.metadata or {}).get("atlas") if isinstance(req.metadata, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    headers = request.headers if request is not None else {}
    session = (
        req.atlas_session
        or meta.get("atlas_session")
        or headers.get("x-atlas-session")
        or (req.metadata or {}).get("chat_id")
        or uuid.uuid4().hex
    )
    vault = bool(req.atlas_vault or meta.get("atlas_vault") or headers.get("x-atlas-vault", "").lower() == "true")
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


# --- the app ----------------------------------------------------------------------------------------------------------


def build_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(title="A.T.L.A.S. orchestrator", version=__version__)
    app.state.deps = deps

    @app.get("/health")
    def health() -> JSONResponse:
        status = {
            "status": "ok" if deps.ready else "starting",
            "version": __version__,
            "arbiter_halted": deps.arbiter.status().get("halted"),
            "memory": deps.memory is not None,
            "outbound_channel": not isinstance(deps.approval.sender, NoChannelSender),
        }
        return JSONResponse(status, status_code=200 if deps.ready else 503)

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        now = int(time.time())
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": now, "owned_by": "atlas"} for m in MODELS],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest, request: Request) -> Any:
        if req.model not in MODELS:
            raise HTTPException(404, f"model {req.model!r} is not served; /v1/models lists {list(MODELS)}")
        if not req.messages:
            raise HTTPException(422, "messages[] is empty")
        session_id, vault_flag, override = _session_of(req, request)
        if vault_flag:
            deps.sessions.tag_vault(session_id)
        gen = _chat_pipeline(deps, req, session_id=session_id, override=override, internal=False)
        return _respond(gen, req.stream)

    @app.post("/internal/v1/chat/completions")
    def internal_completions(req: ChatRequest) -> Any:
        if req.model not in deps.config.engines:
            raise HTTPException(404, f"unknown engine {req.model!r} (CONVENTIONS.md §8 keys)")
        gen = _chat_pipeline(deps, req, session_id=req.atlas_session or "internal", override=None, internal=True)
        return _respond(gen, req.stream)

    @app.post("/internal/route")
    def internal_route(req: RouteRequest) -> dict[str, Any]:
        task_id = new_task_id()
        text = _routed_message(deps.config, req.force_persona or "atlas", req.message, None)
        try:
            info = RouteInfo.from_decision(deps.router.route(text, task_id=task_id), req.message)
        except (ConfigError, RouterError, RuntimeError) as exc:
            raise HTTPException(500, f"router: {exc}") from exc
        return {"task_id": task_id, **info.as_dict()}

    @app.post("/internal/deep-think/plan")
    def internal_plan(req: PlanRequest) -> dict[str, Any]:
        if req.tier not in DEEP_THINK_TIERS:
            raise HTTPException(422, f"tier must be one of {DEEP_THINK_TIERS}")
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

    @app.post("/vault/open")
    def vault_open(req: VaultOpenRequest) -> JSONResponse:
        try:
            res = deps.vault.open(req.passphrase)
        except VaultError as exc:
            raise HTTPException(500, str(exc)) from exc
        return JSONResponse(res.as_dict(), status_code=200 if res.ok else 403)

    @app.post("/vault/lock")
    def vault_lock() -> dict[str, Any]:
        try:
            return deps.vault.lock().as_dict()
        except VaultError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.get("/vault/status")
    def vault_status() -> dict[str, Any]:
        res = deps.vault.status()
        return {"state": res.state, "message": res.message, "vault_sessions": deps.sessions.vault_sessions()}

    # --- approvals (16.2) -----------------------------------------------------------------------------------------

    @app.get("/approvals")
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

    @app.post("/approvals/{approval_id}/approve")
    def approve(approval_id: int, body: ApprovalDecision | None = None) -> dict[str, Any]:
        return _decide(approval_id, "approve", body or ApprovalDecision())

    @app.post("/approvals/{approval_id}/reject")
    def reject(approval_id: int, body: ApprovalDecision | None = None) -> dict[str, Any]:
        return _decide(approval_id, "reject", body or ApprovalDecision())

    # --- arbiter (4.2) --------------------------------------------------------------------------------------------

    @app.post("/arbiter/register")
    def arbiter_register(req: RegisterRequest) -> dict[str, Any]:
        try:
            deps.arbiter.register_measured(req.engine, req.total_bytes, task_id=req.task_id)
        except UnknownEngine as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"engine": req.engine, "total_bytes": req.total_bytes, "registered": True}

    @app.get("/arbiter/status")
    def arbiter_status() -> dict[str, Any]:
        return deps.arbiter.status()

    # --- ouroboros (9.4) ------------------------------------------------------------------------------------------

    @app.post("/strike")
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
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


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


def _generation_lock(deps: AppDeps, spec: EngineSpec, task_id: str) -> contextlib.AbstractContextManager[None]:
    """The single generation slot (4.2 rules 3-4) for weight-bearing engines. The three resident small models are
    outside the Arbiter's ledger by design (4.1, 5.3: never budgeted; the router classifies before every big
    generation), so a request on one of them does not queue behind the running generation."""
    if spec.is_resident:
        return contextlib.nullcontext()
    return deps.arbiter.acquire_generation(spec.key, task_id=task_id, timeout_s=deps.generation_wait_s)


def _retrieve(deps: AppDeps, info: RouteInfo) -> dict[str, list[str]]:
    """Layer 4 (4.4): the closest scars (9.4) and the hemisphere's own memory; a retrieval failure is logged, not
    hidden, and the dispatch continues without it."""
    out: dict[str, list[str]] = {"memory": [], "scars": []}
    if deps.memory is None:
        return out
    try:
        out["scars"] = [
            s.as_prompt_line()
            for s in retrieve_scars(info.message, memory=deps.memory, hemisphere=info.hemisphere, persona=info.persona)
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


def _chat_pipeline(
    deps: AppDeps, req: ChatRequest, *, session_id: str, override: str | None, internal: bool
) -> Iterator[dict[str, Any]]:
    """A generator of OpenAI chunks; sync on purpose (Starlette iterates it in a worker thread, so the Arbiter waits
    never block the event loop)."""
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    message = _last_user_text(req.messages)
    task_id = req.atlas_task_id or new_task_id()
    ledger = deps.ledger
    kind = "internal-generate" if internal else "chat"
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
            },
        )
    else:
        ledger.update_task(task_id, status="running")
    info: RouteInfo | None = None
    try:
        if internal:
            spec = deps.config.engine(req.model)
            info = RouteInfo(
                persona=req.atlas_user or "internal",
                engine=spec.key,
                hemisphere="estate",
                tier="routine",
                route="internal",
                message=message,
            )
            messages: list[dict[str, Any]] = list(req.messages)
            yield _chunk(
                cid, req.model, created, role="assistant", extra={"atlas": {"task_id": task_id, "engine": spec.key}}
            )
        else:
            routed = _routed_message(deps.config, req.model, message, override)
            decision = deps.router.route(routed, task_id=task_id, context={"session_id": session_id})
            info = RouteInfo.from_decision(decision, message)
            ledger.update_task(task_id, persona=info.persona, engine=info.engine, tier=info.tier)
            if info.command in ("ouroboros-strike", "aegis", "vault-session"):
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
            built = deps.build_system_prompt(info.decision or info.persona, cards, _retrieve(deps, info))
            system_prompt = _prompt_text(built)
            messages = [{"role": "system", "content": system_prompt}] + [
                dict(m) for m in req.messages if m.get("role") != "system"
            ]
            if info.message != message:
                for m in reversed(messages):
                    if m.get("role") == "user":
                        m["content"] = info.message  # the override prefix is the router's, not the engine's
                        break
            yield _chunk(
                cid, req.model, created, role="assistant", extra={"atlas": {"task_id": task_id, **info.as_dict()}}
            )
            if info.command == "deep-think":
                text = _run_deep_think(deps, info.deep_think or "standard", info, task_id)
                yield _chunk(cid, req.model, created, content=text, finish="stop")
                ledger.update_task(task_id, status="done", result={"deep_think": info.deep_think, "chars": len(text)})
                _remember_turn(deps, info, session_id, message, text)
                return
        # Engine Arbiter: load (waits in its queue behind a running generation), then the single generation lock.
        decision_load = deps.arbiter.request_load(spec.key, task_id=task_id, wait_s=deps.load_wait_s)
        if not decision_load.granted:
            status = 503 if decision_load.decision is Decision.QUEUED else 507
            raise ArbiterError(f"engine {spec.key} not loaded: {decision_load.reason}", status)
        streamer = deps.streamer_for(spec)
        parts: list[str] = []
        timings: dict[str, Any] | None = None
        finish: str | None = None
        temperature = req.temperature
        if temperature is None and info.persona in deps.config.personas:
            t = deps.config.personas[info.persona].sampling_temperature
            # arthur.md says "low-not-zero" (9.1: reasoning models loop at exactly zero).
            temperature = t if isinstance(t, (int, float)) else (0.15 if isinstance(t, str) else None)
        with _generation_lock(deps, spec, task_id):
            for chunk in streamer.stream(
                messages, temperature=temperature, max_tokens=req.max_tokens or DEFAULT_MAX_TOKENS
            ):
                for c in chunk.get("choices") or []:
                    d = (c.get("delta") or {}).get("content")
                    if d:
                        parts.append(d)
                        yield _chunk(cid, req.model, created, content=d)
                    if c.get("finish_reason"):
                        finish = c["finish_reason"]
                if isinstance(chunk.get("timings"), dict):
                    timings = chunk["timings"]
        text = "".join(parts)
        yield _chunk(cid, req.model, created, finish=finish or "stop", extra={"timings": timings} if timings else None)
        ledger.update_task(task_id, status="done", result={"chars": len(text), "finish": finish, "timings": timings})
        if not internal:
            _remember_turn(deps, info, session_id, message, text)
    except ReleaseTimeout as exc:
        # Rule 5 failure: the Arbiter is halted; nothing more loads until a human looks. Say so, loudly.
        _fail(deps, task_id, info, message, exc, status=503)
        yield _error_chunk(cid, req.model, created, 503, f"engine memory not released; arbiter halted: {exc}", task_id)
    except ArbiterError as exc:
        status = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], int) else 503
        _fail(deps, task_id, info, message, exc, status=status)
        yield _error_chunk(cid, req.model, created, status, str(exc.args[0]), task_id)
    except (EngineError, ConfigError, RouterError, RuntimeError, httpx.HTTPError) as exc:
        _fail(deps, task_id, info, message, exc, status=502)
        yield _error_chunk(cid, req.model, created, 502, f"{type(exc).__name__}: {exc}", task_id)


def _fail(
    deps: AppDeps, task_id: str, info: RouteInfo | None, message: str, exc: BaseException, *, status: int
) -> None:
    err = f"{type(exc).__name__}: {exc.args[0] if exc.args else exc}"
    log.error("chat task %s failed (%d): %s", task_id, status, err)
    deps.ledger.update_task(task_id, status="failed", error=err)
    persona = info.persona if info else "atlas"
    domain = (info.task_force or info.route) if info else "routing"
    try:
        # 9.4 automatic strike: a failed generation is a strike; the scar is dropped by memory if the session is vault.
        record_strike(
            persona,
            domain,
            message[:500],
            err,
            ledger=deps.ledger,
            memory=deps.memory,
            kind="failed-generation",
            source="api",
            task_id=task_id,
            hemisphere=info.hemisphere if info else None,
        )
    except Exception:
        log.exception("strike not recorded for task %s", task_id)


def _remember_turn(deps: AppDeps, info: RouteInfo, session_id: str, user_text: str, answer: str) -> None:
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
    plan = deep_think.plan(tier, deps.arbiter, task_id=task_id)

    def call(
        engine: str, persona: str, messages: Sequence[Mapping[str, str]], *, temperature: float, max_tokens: int
    ) -> str:
        spec = deps.config.engine(engine)
        dec = deps.arbiter.request_load(spec.key, task_id=task_id, wait_s=deps.load_wait_s)
        if not dec.granted:
            raise ArbiterError(f"deep think: {engine} not loaded: {dec.reason}")
        persona_prompt = _prompt_text(deps.build_system_prompt(persona, (), None))
        full = [{"role": "system", "content": persona_prompt}, *messages]
        streamer = deps.streamer_for(spec)
        parts: list[str] = []
        with _generation_lock(deps, spec, task_id):
            for chunk in streamer.stream(full, temperature=temperature, max_tokens=max_tokens):
                for c in chunk.get("choices") or []:
                    d = (c.get("delta") or {}).get("content")
                    if d:
                        parts.append(d)
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
            "This session is now vault-tagged: nothing said here is written to memory (Section 10.5). "
            f"Vault is {st.state}." + ("" if st.state == "open" else " Open it with the vault button.")
        )
    if info.command == "ouroboros-strike":
        # The router's body is "<name>] <context>" for "[LOG STRIKE: <name>] <context>" (the prefix ends at the colon).
        name, _, rest = body.partition("]")
        name = name.strip(" [")
        out = record_strike(
            "principal",
            info.task_force or "manual",
            rest.strip() or "(no context given)",
            name or body,
            ledger=deps.ledger,
            memory=deps.memory,
            kind="manual",
            source="principal",
            task_id=task_id,
            session_id=session_id,
        )
        scar = f"scar {out['scar_id']}" if out.get("scar_written") else f"no scar ({out.get('reason')})"
        return f"Strike #{out['strike_id']} logged; {scar}."
    if info.command == "aegis":
        if deps.enqueue is None:
            return "AEGIS manual backup cannot be enqueued: Celery is not wired into this process."
        tid = deps.enqueue("atlas.tasks.aegis_manual_backup", {"requested_by": "principal"})
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
    from atlas.tasks import notify
    from atlas.vault import build_vault_controller

    config = load_config()
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
    sessions = SessionTags(os.environ.get("VAULT_SESSION_FILE") or "/run/atlas/vault-sessions.json")
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
        vault=build_vault_controller(sessions=sessions),
        sessions=sessions,
        approval=approval,
        memory=memory,
        notify=notify,
        enqueue=enqueue,
        load_wait_s=float(os.environ.get("ATLAS_LOAD_WAIT_S") or DEFAULT_LOAD_WAIT_S),
        generation_wait_s=float(os.environ.get("ATLAS_GENERATION_WAIT_S") or DEFAULT_GENERATION_WAIT_S),
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

    uvicorn.run(build_app(deps), host=args.host, port=args.port, log_level="debug" if args.verbose else "info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
