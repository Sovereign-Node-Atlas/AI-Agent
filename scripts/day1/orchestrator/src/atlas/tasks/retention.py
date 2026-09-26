"""Chat retention (D9, Section 10.4): Open WebUI chats older than 90 days are summarised into the Vector Cortex and
deleted. Nightly under Celery beat (atlas.celery_app, "chat-retention-nightly", gpu queue).

Facts typed from services-tools.md §1.6 (VERIFIED Open WebUI 0.11.4 source): no built-in retention setting;
`GET /api/v1/chats/all/db` (admin, ENABLE_ADMIN_EXPORT=true) returns every chat with `updated_at` in epoch seconds,
`pinned`, `archived`, `title`, `chat` (JSON with `messages`); `DELETE /api/v1/chats/{id}` as admin deletes any chat
and cascades chat_message / shared_chat. The SQLite fallback of §1.6 is for a dead API with the container stopped and
is NOT automated here (rule "do not automate anything that will fail": the DB is root-owned and live).

Settings (orchestrator.env, phase2/02 and 03): OPENWEBUI_URL, OPENWEBUI_ADMIN_TOKEN_FILE (the bare API key or JWT on
one line, phase2/03-openwebui.sh), OPENWEBUI_CHAT_RETENTION_DAYS (90).

Rules: pinned chats are kept (phase2/03-openwebui.sh contract). Vault-tagged chats (any message starting with the
`[VAULT]` override, or `meta.tags` containing "vault") are deleted WITHOUT a summary: "vault sessions are never
retained" (10.4, 10.5). Every summary goes through the orchestrator: /internal/route decides the hemisphere with the
router's hard rules, /internal/v1/chat/completions on the resident router model writes the summary under the
Arbiter's generation lock; the summary lands in that hemisphere's collection as a permanent (non-temporal) entry.
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
    kept_pinned: int = 0
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
            "kept_pinned": self.kept_pinned,
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


def is_vault_chat(chat: Mapping[str, Any]) -> bool:
    meta = chat.get("meta") if isinstance(chat.get("meta"), dict) else {}
    tags = meta.get("tags") or []
    if any(str(t).lower() == "vault" for t in tags):
        return True
    for m in _messages(chat):
        if m.get("role") == "user" and str(m.get("content", "")).lstrip().upper().startswith(VAULT_PREFIX):
            return True
    return False


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
    out: list[dict[str, Any]] = []
    for c in chats:
        result.scanned += 1
        try:
            updated = float(c.get("updated_at") or 0)
        except (TypeError, ValueError):
            continue
        if updated >= cutoff:
            continue
        if c.get("pinned"):
            result.kept_pinned += 1
            continue
        out.append(dict(c))
    result.expired = len(out)
    return out


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
) -> RetentionResult:
    """`orchestrator` is atlas.tasks.OrchestratorClient (route + generate); `memory` an atlas.memory.MemoryStore."""
    now = now or time.time()
    result = RetentionResult(days=days, cutoff=now - days * 86400.0)
    chats = owui.all_chats()
    for chat in select_expired(chats, result.cutoff, result):
        cid = str(chat.get("id"))
        title = str(chat.get("title") or "untitled")
        try:
            if is_vault_chat(chat):
                if not dry_run:
                    owui.delete_chat(cid)
                result.vault_deleted += 1
                result.deleted += 1
                log.info("retention: vault-tagged chat %s deleted without summary (10.4, 10.5)", cid)
                continue
            text = transcript(chat)
            if text.strip():
                route = orchestrator.route(text[:4000])
                hemisphere = str(route.get("hemisphere") or "estate")  # the safer default when routing is unsure
                collection = "estate" if hemisphere == "estate" else "corporate"
                summary = orchestrator.generate(
                    engine, summary_messages(title, text), max_tokens=600, temperature=0.2, task_id=task_id
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
                result.summarised += 1
            if not dry_run:
                owui.delete_chat(cid)
            result.deleted += 1
        except Exception as exc:
            result.errors.append(f"{cid}: {type(exc).__name__}: {exc}")
            log.error("retention: chat %s not processed: %s", cid, exc)
    log.info(
        "retention: scanned %d, expired %d, summarised %d, deleted %d (vault %d), pinned kept %d, errors %d",
        result.scanned,
        result.expired,
        result.summarised,
        result.deleted,
        result.vault_deleted,
        result.kept_pinned,
        len(result.errors),
    )
    return result


@shared_task(name="atlas.tasks.chat_retention", bind=True)
def chat_retention(self: Any) -> dict[str, Any]:
    from atlas.memory import build_memory_store
    from atlas.tasks import OrchestratorClient, TaskRecord, read_secret_line

    rec = TaskRecord(self.request.id, "chat-retention", queue="gpu")
    env = os.environ
    try:
        days = int(env.get("OPENWEBUI_CHAT_RETENTION_DAYS") or DEFAULT_DAYS)
        url = env.get("OPENWEBUI_URL") or f"http://127.0.0.1:{env.get('OPENWEBUI_PORT') or 3000}"
        token_file = env.get("OPENWEBUI_ADMIN_TOKEN_FILE") or "/etc/atlas/secrets/openwebui-admin.token"
        token = read_secret_line(token_file)
        owui = OpenWebUIClient(url, token)
        orch = OrchestratorClient()
        try:
            store = build_memory_store(with_graph=False)
            result = run_retention(
                owui,
                orch,
                store,
                days=days,
                engine=env.get("ATLAS_CLASSIFIER_ENGINE") or "router-qwen3.5-4b",
                task_id=self.request.id,
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
