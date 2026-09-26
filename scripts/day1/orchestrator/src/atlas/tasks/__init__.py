"""Celery tasks (Section 9.7) and what they share: the ledger row per task id, ntfy, the orchestrator's loopback API.

Modules: sentinel (9.3), prune (9.6), aegis (9.5), ouroboros (9.4), retention (D9), deep_think_task (9.1).

Every task runs in a Celery worker process, NOT in the orchestrator's process. The Engine Arbiter is an object inside
the orchestrator, so a task that needs a model calls the orchestrator's loopback API (`OrchestratorClient`): its
/internal/v1/chat/completions holds the single generation lock (4.2 rule 3) and loads through the Arbiter (rule 2), so
"a background task that calls an LLM partway through still queues behind the resident engine" (9.7, C15) is true by
construction. Only 127.0.0.1 is ever dialled; trust_env=False keeps the allowlist proxy out of loopback traffic.

Ledger rule: `atlas-admin enqueue` inserts the task row with the Celery task id BEFORE sending (admin.py); the worker
finds that row and updates it. Beat-scheduled and chained tasks have no row yet, so `TaskRecord.start()` inserts one.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from atlas.config import Settings
from atlas.ledger import Ledger

log = logging.getLogger("atlas.tasks")

__all__ = ["OrchestratorClient", "TaskRecord", "notify", "open_task_ledger", "read_secret_line", "settings"]


def settings() -> Settings:
    return Settings.from_env()


def open_task_ledger() -> Ledger:
    ledger = Ledger(settings().db_path)
    ledger.init_db()
    return ledger


def read_secret_line(path: str | Path, key: str | None = None) -> str:
    """A secret file (CONVENTIONS.md §2): either `KEY=value` lines or the bare value on the first line."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"secret file {p} unreadable: {exc}") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if key and line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
        if "=" not in line or not key:
            return line
    raise RuntimeError(f"secret file {p} carries no {key or 'value'}")


def notify(
    message: str,
    *,
    title: str | None = None,
    priority: str = "default",
    tags: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    timeout_s: float = 10.0,
) -> bool:
    """ntfy push (Section 9.3 alert row; 12.2): never raises, returns whether the push was accepted.

    NTFY_URL, NTFY_TOPIC, NTFY_TOKEN_FILE from orchestrator.env; the token file holds `NTFY_TOKEN=tk_...`
    (phase1/07-remote.sh). Loopback only (trust_env=False).
    """
    env = dict(os.environ if env is None else env)
    url = (env.get("NTFY_URL") or "http://127.0.0.1:8090").rstrip("/")
    topic = env.get("NTFY_TOPIC") or "atlas"
    headers: dict[str, str] = {"Priority": priority}
    if title:
        headers["Title"] = title
    if tags:
        headers["Tags"] = ",".join(tags)
    token_file = env.get("NTFY_TOKEN_FILE")
    if token_file and Path(token_file).is_file():
        try:
            headers["Authorization"] = f"Bearer {read_secret_line(token_file, 'NTFY_TOKEN')}"
        except RuntimeError as exc:
            log.warning("ntfy token not read (%s); pushing without auth", exc)
    try:
        with httpx.Client(trust_env=False, timeout=timeout_s) as c:
            r = c.post(f"{url}/{topic}", content=message.encode("utf-8"), headers=headers)
        if r.status_code >= 400:
            log.error("ntfy push refused: HTTP %s %s", r.status_code, r.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.error("ntfy push failed: %s (email fallback is the Google integration's, Section 9.3)", exc)
        return False


class OrchestratorClient:
    """Loopback client for the orchestrator (atlas.api): generation through the Arbiter, routing, strikes."""

    def __init__(
        self, base_url: str | None = None, *, timeout_s: float = 1800.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        env = os.environ
        self.base_url = (base_url or env.get("ORCH_URL") or f"http://127.0.0.1:{env.get('ORCH_PORT') or 8800}").rstrip(
            "/"
        )
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, trust_env=False, transport=transport)

    def close(self) -> None:
        self._http.close()

    def health(self) -> bool:
        try:
            return self._http.get("/health", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def generate(
        self,
        engine: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        task_id: str | None = None,
    ) -> str:
        """POST /internal/v1/chat/completions {model: <engine key>} -> the assistant text (Arbiter-locked)."""
        body: dict[str, Any] = {
            "model": engine,
            "messages": list(messages),
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if task_id:
            body["atlas_task_id"] = task_id
        r = self._http.post("/internal/v1/chat/completions", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"orchestrator generate on {engine} -> HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        return str((choice.get("message") or {}).get("content") or "")

    def route(self, message: str) -> dict[str, Any]:
        r = self._http.post("/internal/route", json={"message": message})
        if r.status_code >= 400:
            raise RuntimeError(f"orchestrator route -> HTTP {r.status_code}: {r.text[:300]}")
        return dict(r.json())

    def strike(self, **fields: Any) -> dict[str, Any]:
        r = self._http.post("/strike", json=fields)
        if r.status_code >= 400:
            raise RuntimeError(f"orchestrator strike -> HTTP {r.status_code}: {r.text[:300]}")
        return dict(r.json())


class TaskRecord:
    """The ledger row for one Celery task (Appendix A "task ID, ledger entry"; 4.2 rule 9)."""

    def __init__(
        self,
        task_id: str,
        kind: str,
        *,
        ledger: Ledger | None = None,
        queue: str = "cpu",
        parent_task_id: str | None = None,
        payload: Any = None,
    ) -> None:
        self.task_id = task_id
        self.kind = kind
        self.ledger = ledger or open_task_ledger()
        self._own_ledger = ledger is None
        self.started = time.time()
        row = self.ledger.get_task(task_id)
        if row is None:
            self.ledger.insert_task(
                kind, task_id=task_id, status="running", queue=queue, parent_task_id=parent_task_id, payload=payload
            )
        else:
            self.ledger.update_task(task_id, status="running")
        log.info("task %s (%s) running", task_id, kind)

    @property
    def elapsed_s(self) -> float:
        return time.time() - self.started

    def done(self, result: Any = None) -> dict[str, Any]:
        self.ledger.update_task(self.task_id, status="done", result=result)
        log.info("task %s (%s) done in %.1fs", self.task_id, self.kind, self.elapsed_s)
        self._close()
        return result if isinstance(result, dict) else {"result": result}

    def failed(self, error: str) -> None:
        self.ledger.update_task(self.task_id, status="failed", error=error[:2000])
        log.error("task %s (%s) FAILED after %.1fs: %s", self.task_id, self.kind, self.elapsed_s, error)
        self._close()

    def _close(self) -> None:
        if self._own_ledger:
            self.ledger.close()


def json_line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
