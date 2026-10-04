"""Chat retention (D9, Section 10.4): Open WebUI chats older than 90 days are summarised into the Vector Cortex and
deleted. Nightly under Celery beat (atlas.celery_app, "chat-retention-nightly", gpu queue).

Facts typed from services-tools.md §1.6 (VERIFIED Open WebUI 0.11.4 source): no built-in retention setting;
`GET /api/v1/chats/all/db` (admin, ENABLE_ADMIN_EXPORT=true) returns every chat with `updated_at` in epoch seconds,
`pinned`, `archived`, `title`, `chat` (JSON with `messages`); `DELETE /api/v1/chats/{id}` as admin deletes any chat
and cascades chat_message / shared_chat. The SQLite fallback of §1.6 is for a dead API with the container stopped and
is NOT automated here (rule "do not automate anything that will fail": the DB is root-owned and live).

Settings (orchestrator.env, phase2/02 and 03): OPENWEBUI_URL, OPENWEBUI_ADMIN_TOKEN_FILE (the bare API key or JWT on
one line, phase2/03-openwebui.sh), OPENWEBUI_CHAT_RETENTION_DAYS (90).

Rules. D9 (10.4) is binding without exception: "chats are retained 90 days in Open WebUI, then summarised into the
Vector Cortex and purged". PINNED chats follow D9 by default (fix round 4): an expired pinned chat is summarised and
its Open WebUI record deleted like any other. Keeping the record of a pinned chat is an OPT-IN deviation,
OPENWEBUI_KEEP_PINNED=1 in orchestrator.env, which no Day 1 step writes: with it, the pinned chat is still summarised
at 90 days (once per version of the chat: the summary document records `chat_updated_at`, and a chat whose summary is
already current is skipped) and only its Open WebUI record is kept, counted as `kept_pinned` so the deviation is
visible in the ledger. The opt-in is recorded for scripts/day1/README.md "Baseline deviations" and a Section 23 row
(the Principal confirms or reverses it); phase2/03-openwebui.sh's header still says the task "keeps pinned chats" and
is asked to adopt the D9 wording (cross-writer).
Vault-tagged chats (the `[VAULT]` token anywhere in a user message, `meta.tags` containing "vault", or a session the
orchestrator's SessionTags marks vault through the durable store, fix round 2) are deleted WITHOUT a summary: "vault
sessions are never retained" (10.4, 10.5). Every summary goes through the orchestrator: /internal/route decides the
hemisphere with the router's hard rules, /internal/v1/chat/completions on the resident router model writes the
summary; that call holds the orchestrator's one generation slot like any other generation (atlas.api.GenerationSlot,
4.2 rule 3, 9.7 C15). The summary lands in that hemisphere's collection as a permanent (non-temporal) entry.

The admin token file (OPENWEBUI_ADMIN_TOKEN_FILE, atlas:atlas 600) must be readable by the gpu worker: a failure names
the path and the owner:mode of the file and its directory (fix round), because the directory's mode is the usual
cause and the journal must say so. The implemented layout (phase2/02-orchestrator.sh header, phase2/06c, 09b;
README-contracts.md §3 item 10): /etc/atlas/secrets is root:atlas 710 (traversable by atlas, nothing listed) and every
file inside is 600 owned by its one reader; atlas-celery-gpu.service's ExecStartPre asserts exactly that before the
worker starts. CONVENTIONS.md §2's `root:root 700` row cannot hold beside its own atlas:atlas entries and is the item
asked to be amended (not a layout this package recommends at run time; fix round 4).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from celery import shared_task

log = logging.getLogger("atlas.retention")

DEFAULT_DAYS = 90
VAULT_PREFIX = "[VAULT]"
MAX_CHARS_PER_CHAT = 24000  # keep the summary prompt inside the resident model's context

__all__ = [
    "OpenWebUIClient",
    "RetentionResult",
    "chat_retention",
    "is_vault_chat",
    "run_retention",
    "select_expired",
    "transcript",
]


@dataclass
class RetentionResult:
    days: int
    cutoff: float
    scanned: int = 0
    expired: int = 0
    summarised: int = 0
    deleted: int = 0
    vault_deleted: int = 0
    keep_pinned: bool = False  # the OPENWEBUI_KEEP_PINNED=1 opt-in (module docstring); False = D9 as written
    kept_pinned: int = 0  # expired pinned chats whose Open WebUI record was kept (opt-in mode only)
    pinned_summarised: int = 0  # ... of which a (new or refreshed) summary was written this run
    graph_written: int = 0  # summaries also inserted into the D7 graph layer (store.graph), when one is configured
    graph_failed: int = 0  # graph inserts that failed; logged, never blocks the D9 purge (the summary IS in Chroma)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "cutoff": self.cutoff,
            "scanned": self.scanned,
            "expired": self.expired,
            "summarised": self.summarised,
            "deleted": self.deleted,
            "vault_deleted": self.vault_deleted,
            "keep_pinned": self.keep_pinned,
            "kept_pinned": self.kept_pinned,
            "pinned_summarised": self.pinned_summarised,
            "graph_written": self.graph_written,
            "graph_failed": self.graph_failed,
            "errors": self.errors,
        }


class OpenWebUIClient:
    def __init__(
        self, base_url: str, token: str, *, timeout_s: float = 120.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            trust_env=False,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def all_chats(self) -> list[dict[str, Any]]:
        r = self._http.get("/api/v1/chats/all/db")
        if r.status_code >= 400:
            raise RuntimeError(
                f"GET /api/v1/chats/all/db -> HTTP {r.status_code}: {r.text[:200]} (ENABLE_ADMIN_EXPORT "
                "must be true and the token must be an admin's)"
            )
        data = r.json()
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    def delete_chat(self, chat_id: str) -> None:
        r = self._http.delete(f"/api/v1/chats/{chat_id}")
        if r.status_code >= 400:
            raise RuntimeError(f"DELETE /api/v1/chats/{chat_id} -> HTTP {r.status_code}: {r.text[:200]}")


def _messages(chat: Mapping[str, Any]) -> list[dict[str, Any]]:
    inner = chat.get("chat") if isinstance(chat.get("chat"), dict) else {}
    msgs = inner.get("messages")
    if isinstance(msgs, list):
        return [m for m in msgs if isinstance(m, dict)]
    hist = inner.get("history") if isinstance(inner.get("history"), dict) else {}
    hm = hist.get("messages")
    return [m for m in hm.values() if isinstance(m, dict)] if isinstance(hm, dict) else []


def is_vault_chat(chat: Mapping[str, Any], sessions: Any | None = None) -> bool:
    """atlas.api.history_has_vault_prefix's rule (the token ANYWHERE in a user message, fix round 2), the `vault` tag,
    or the orchestrator's session tags (`sessions.is_vault(chat id)`: a session tagged through the atlas_vault flag or
    the X-Atlas-Vault header, kept in the durable store across reboots)."""
    meta = chat.get("meta") if isinstance(chat.get("meta"), dict) else {}
    tags = meta.get("tags") or []
    if any(str(t).lower() == "vault" for t in tags):
        return True
    for m in _messages(chat):
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if m.get("role") == "user" and VAULT_PREFIX in str(content or "").upper():
            return True
    cid = chat.get("id")
    return bool(sessions is not None and cid and sessions.is_vault(str(cid)))


def transcript(chat: Mapping[str, Any], max_chars: int = MAX_CHARS_PER_CHAT) -> str:
    lines: list[str] = []
    for m in _messages(chat):
        role = str(m.get("role", "?"))
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        text = str(content or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    out = "\n".join(lines)
    return out[-max_chars:] if len(out) > max_chars else out


def select_expired(chats: Sequence[Mapping[str, Any]], cutoff: float, result: RetentionResult) -> list[dict[str, Any]]:
    """Every chat older than the cutoff, pinned ones included (D9 makes no exception; run_retention)."""
    out: list[dict[str, Any]] = []
    for c in chats:
        result.scanned += 1
        try:
            updated = float(c.get("updated_at") or 0)
        except (TypeError, ValueError):
            continue
        if updated >= cutoff:
            continue
        out.append(dict(c))
    result.expired = len(out)
    return out


def _summary_is_current(memory: Any, cid: str, updated_at: float) -> bool:
    """True when either hemisphere collection already holds `chat-summary-<cid>` for this version of the chat."""
    for collection in ("estate", "corporate"):
        try:
            hits = memory.get(collection, hemisphere=collection, ids=[f"chat-summary-{cid}"])
        except Exception as exc:  # a read failure means "summarise again", never "skip silently"
            log.warning("retention: cannot read %s for chat %s (%s); summarising", collection, cid, exc)
            return False
        for h in hits:
            try:
                if float(h.metadata.get("chat_updated_at") or 0) >= updated_at:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _graph_insert(
    memory: Any, cid: str, title: str, summary: str, hemisphere: str, updated_at: float, result: RetentionResult
) -> None:
    """The D7 graph layer's one Day 1 feeder (fix round 4; memory.py module docstring): each chat summary goes into
    the hemisphere's LightRAG as a permanent document, when the store carries a graph (LIGHTRAG_WORKING_DIR set). A
    failure (lightrag missing, the tokenizer table not seeded, the orchestrator down) is logged and counted; it never
    blocks the D9 purge, because the summary is already in the Vector Cortex. The extraction generations queue behind
    the orchestrator's one slot like every other (LightRAGStore docstring)."""
    graph = getattr(memory, "graph", None)
    if graph is None:
        return
    try:
        wr = graph.insert(
            f"Chat summary ({title}):\n{summary.strip()}",
            doc_id=f"chat-summary-{cid}",
            hemisphere=hemisphere,
            metadata={"kind": "chat-summary", "chat_id": cid, "chat_updated_at": updated_at},
            temporal=False,
        )
        if wr.written or wr.spooled:
            result.graph_written += 1
        else:
            result.graph_failed += 1
            log.error("retention: graph insert of chat %s not written: %s", cid, wr.reason)
    except Exception as exc:  # the summary is in Chroma; the graph is reported, not pretended
        result.graph_failed += 1
        log.error("retention: graph insert of chat %s failed: %s: %s", cid, type(exc).__name__, exc)


def summary_messages(title: str, text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Summarise this chat for long-term memory. Keep: decisions taken, facts about the Principal's affairs, "
                "names, figures, dates, open items. Drop: pleasantries, restated context, anything the Principal "
                "asked to "
                "forget. Write 5 to 12 compressed lines of plain text. No preamble."
            ),
        },
        {"role": "user", "content": f"Chat title: {title}\n\nTranscript:\n{text}"},
    ]


def run_retention(
    owui: OpenWebUIClient,
    orchestrator: Any,
    memory: Any,
    *,
    days: int = DEFAULT_DAYS,
    engine: str = "router-qwen3.5-4b",
    now: float | None = None,
    task_id: str | None = None,
    dry_run: bool = False,
    sessions: Any | None = None,
    keep_pinned: bool = False,
) -> RetentionResult:
    """`orchestrator` is atlas.tasks.OrchestratorClient (route + generate); `memory` an atlas.memory.MemoryStore;
    `sessions` an atlas.vault.SessionTags (vault tags set through the flag/header, module docstring); `keep_pinned`
    the OPENWEBUI_KEEP_PINNED opt-in (False = D9 as written: pinned chats are purged after their summary too)."""
    now = now or time.time()
    result = RetentionResult(days=days, cutoff=now - days * 86400.0, keep_pinned=keep_pinned)
    chats = owui.all_chats()
    for chat in select_expired(chats, result.cutoff, result):
        cid = str(chat.get("id"))
        title = str(chat.get("title") or "untitled")
        pinned = bool(chat.get("pinned"))
        try:
            if is_vault_chat(chat, sessions):
                if not dry_run:
                    owui.delete_chat(cid)  # pinned or not: "vault sessions are never retained" (10.4)
                result.vault_deleted += 1
                result.deleted += 1
                log.info("retention: vault-tagged chat %s deleted without summary (10.4, 10.5)", cid)
                continue
            keep = pinned and keep_pinned
            if keep:
                # The opt-in deviation (module docstring): the record stays, the memory half of D9 still happens.
                result.kept_pinned += 1
                if _summary_is_current(memory, cid, float(chat.get("updated_at") or 0)):
                    continue
            text = transcript(chat)
            if text.strip():
                route = orchestrator.route(text[:4000])
                hemisphere = str(route.get("hemisphere") or "estate")  # the safer default when routing is unsure
                collection = "estate" if hemisphere == "estate" else "corporate"
                # The summary generation declares the chat's hemisphere (fix round 4): a failure's scar then lands
                # in that hemisphere's scars, never as an estate scar carrying a corporate transcript.
                summary = orchestrator.generate(
                    engine,
                    summary_messages(title, text),
                    max_tokens=600,
                    temperature=0.2,
                    task_id=task_id,
                    hemisphere=hemisphere,
                )
                if not summary.strip():
                    raise RuntimeError("empty summary from the resident model; chat kept")
                if not dry_run:
                    wr = memory.write(
                        collection,
                        [f"Chat summary ({title}):\n{summary.strip()}"],
                        [
                            {
                                "kind": "chat-summary",
                                "chat_id": cid,
                                "title": title[:200],
                                "chat_updated_at": float(chat.get("updated_at") or 0),
                                "route": str(route.get("route") or ""),
                            }
                        ],
                        ids=[f"chat-summary-{cid}"],
                        hemisphere=hemisphere,
                        upsert=True,
                    )
                    if not wr.written:
                        raise RuntimeError(f"summary not written ({wr.reason}); chat kept")
                    _graph_insert(memory, cid, title, summary, hemisphere, float(chat.get("updated_at") or 0), result)
                result.summarised += 1
                if pinned:
                    result.pinned_summarised += 1
            if keep:
                log.info("retention: pinned chat %s summarised, Open WebUI record kept (OPENWEBUI_KEEP_PINNED)", cid)
                continue
            if not dry_run:
                owui.delete_chat(cid)
            result.deleted += 1
        except Exception as exc:
            result.errors.append(f"{cid}: {type(exc).__name__}: {exc}")
            log.error("retention: chat %s not processed: %s", cid, exc)
    log.info(
        "retention: scanned %d, expired %d, summarised %d (graph %d, graph failed %d), deleted %d (vault %d), pinned "
        "kept %d (%d summarised; keep_pinned=%s), errors %d",
        result.scanned,
        result.expired,
        result.summarised,
        result.graph_written,
        result.graph_failed,
        result.deleted,
        result.vault_deleted,
        result.kept_pinned,
        result.pinned_summarised,
        keep_pinned,
        len(result.errors),
    )
    return result


@shared_task(name="atlas.tasks.chat_retention", bind=True)
def chat_retention(self: Any) -> dict[str, Any]:
    from atlas.memory import build_memory_store
    from atlas.tasks import OrchestratorClient, TaskRecord, _owner_mode, read_secret_line

    rec = TaskRecord(self.request.id, "chat-retention", queue="gpu")
    env = os.environ
    try:
        days = int(env.get("OPENWEBUI_CHAT_RETENTION_DAYS") or DEFAULT_DAYS)
        url = env.get("OPENWEBUI_URL") or f"http://127.0.0.1:{env.get('OPENWEBUI_PORT') or 3000}"
        token_file = env.get("OPENWEBUI_ADMIN_TOKEN_FILE") or "/etc/atlas/secrets/openwebui-admin.token"
        try:
            token = read_secret_line(token_file)
        except RuntimeError as exc:
            raise RuntimeError(
                f"OPENWEBUI_ADMIN_TOKEN_FILE={token_file} unreadable by this worker ({exc}; "
                f"{_owner_mode(token_file)}). /etc/atlas/secrets must be traversable by atlas (root:atlas 710 per "
                "phase2/02-orchestrator.sh and phase2/06c; 750 also traverses) and the token file atlas:atlas 600; "
                "see the ExecStartPre check in atlas-celery-gpu.service"
            ) from exc
        owui = OpenWebUIClient(url, token)
        orch = OrchestratorClient()
        try:
            # WITH the graph layer (fix round 4): the chat summary is the D7 graph's Day 1 feeder (_graph_insert).
            store = build_memory_store()
            result = run_retention(
                owui,
                orch,
                store,
                days=days,
                engine=env.get("ATLAS_CLASSIFIER_ENGINE") or "router-qwen3.5-4b",
                task_id=self.request.id,
                sessions=store.sessions,
                keep_pinned=(env.get("OPENWEBUI_KEEP_PINNED") or "").strip() == "1",
            )
        finally:
            owui.close()
            orch.close()
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    out = result.as_dict()
    if result.errors:
        rec.failed(f"{len(result.errors)} chat(s) failed: " + "; ".join(result.errors[:5]))
        raise RuntimeError(f"chat retention finished with {len(result.errors)} error(s): {result.errors[:3]}")
    return rec.done(out)
