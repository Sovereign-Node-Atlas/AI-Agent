"""Celery tasks (Section 9.7) and what they share: the ledger row per task id, ntfy, the orchestrator's loopback API.

Modules: sentinel (9.3), prune (9.6), aegis (9.5), ouroboros (9.4), retention (D9), deep_think_task (9.1).

Every task runs in a Celery worker process, NOT in the orchestrator's process. The Engine Arbiter is an object inside
the orchestrator, so a task that needs a model calls the orchestrator's loopback API (`OrchestratorClient`): its
/internal/v1/chat/completions loads a weight-bearing engine through the Arbiter (4.2 rule 2) and generates under the
orchestrator's ONE generation slot (rule 3: "exactly one may generate at any moment ... including background work").
Every generation holds that slot, the three resident small models (router-qwen3.5-4b, bge-m3's chat use, the
reranker) included (fix round 2, atlas.api.GenerationSlot): a 4B call for a BLUF or a summary queues behind a running
chat generation AND a chat generation queues behind the 4B call, strict FIFO (rule 4; 9.7 C15 in both directions).
The only model call outside the slot is the router's classifier verdict (7.2 rule 2), which precedes every generation.
A client may therefore wait up to ATLAS_GENERATION_WAIT_S (atlas.api.DEFAULT_GENERATION_WAIT_S, 1800 s) in the FIFO
before its own generation even starts, so the client's read timeout is that value PLUS a generation
(DEFAULT_TIMEOUT_SLACK_S); the two are sized together, never equal (fix round 2: an equal timeout fired the moment
the orchestrator began generating, orphaning the child row).
Only 127.0.0.1 is ever dialled; trust_env=False keeps the allowlist proxy out of loopback traffic. When the
orchestrator's admin routes carry a token (ORCH_ADMIN_TOKEN_FILE, atlas.api), the client sends it as X-Atlas-Token.
`generate(..., audience="principal")` asks the orchestrator to run the never-delegate rewrite (16.1 rule 5) on the
answer, for text that reaches the Principal (a Sentinel BLUF pushed to the phone).

Ledger rule: `atlas-admin enqueue` inserts the task row with the Celery task id BEFORE sending (admin.py); the worker
finds that row and updates it. Beat-scheduled and chained tasks have no row yet, so `TaskRecord.start()` inserts one.
An internal generation made on a task's behalf gets its own CHILD row (parent_task_id = the Celery id) in the
orchestrator, so the parent row is never marked done or failed mid-flight by the generation it asked for.
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

__all__ = [
    "DEFAULT_TIMEOUT_SLACK_S",
    "OrchestratorClient",
    "TaskRecord",
    "admin_token",
    "generation_timeout_s",
    "notify",
    "open_task_ledger",
    "read_secret_line",
    "settings",
]


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


def _owner_mode(path: str | Path) -> str:
    """`uid:gid mode` of a path (and of its parent when the path itself cannot be stat'ed), for error messages that
    must name the cause (rule §7.4): a secret file inside a non-traversable directory shows as the parent's mode."""
    import stat

    for p in (Path(path), Path(path).parent):
        try:
            st = p.stat()
        except OSError:
            continue
        return f"{p}={st.st_uid}:{st.st_gid} {stat.filemode(st.st_mode)}"
    return f"{path}=unstat-able"


def admin_token(env: Mapping[str, str] | None = None) -> str | None:
    """The orchestrator's admin token (ORCH_ADMIN_TOKEN_FILE, atlas.api `require_admin`), or None when the node runs
    without one (loopback-only admin routes). A configured but unreadable file is an error, never a silent None."""
    env = dict(os.environ if env is None else env)
    path = env.get("ORCH_ADMIN_TOKEN_FILE")
    if not path:
        return None
    try:
        return read_secret_line(path, "ORCH_ADMIN_TOKEN")
    except RuntimeError as exc:
        raise RuntimeError(f"ORCH_ADMIN_TOKEN_FILE={path} is set but unreadable ({exc}; {_owner_mode(path)})") from exc


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
    if token_file:
        # No silent `is_file()` gate (fix round): a token file this worker cannot traverse to is an ERROR in the
        # journal (CONVENTIONS.md §2 keeps /etc/atlas/secrets root:root 700, so an atlas-read secret belongs in an
        # atlas-owned subdirectory, /etc/atlas/secrets/atlas/ 700 with files 600, and NTFY_TOKEN_FILE points there;
        # phase2/02-orchestrator.sh currently widens the directory to root:atlas 750 instead, a cross-writer item),
        # and the push still goes out so the server's refusal is visible too; ntfy's default-deny auth would drop an
        # unauthenticated push.
        try:
            headers["Authorization"] = f"Bearer {read_secret_line(token_file, 'NTFY_TOKEN')}"
        except RuntimeError as exc:
            log.error(
                "ntfy token file %s unreadable (%s; owner:mode %s); pushing without auth, which ntfy will refuse "
                "under default-deny",
                token_file,
                exc,
                _owner_mode(token_file),
            )
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


DEFAULT_GENERATION_WAIT_S = 1800.0  # must equal atlas.api.DEFAULT_GENERATION_WAIT_S (ATLAS_GENERATION_WAIT_S)
DEFAULT_TIMEOUT_SLACK_S = 900.0  # one generation on top of the slot wait


def generation_timeout_s(env: Mapping[str, str] | None = None) -> float:
    """The client's read timeout: the orchestrator's slot wait (ATLAS_GENERATION_WAIT_S) plus a generation."""
    env = dict(os.environ if env is None else env)
    try:
        wait = float(env.get("ATLAS_GENERATION_WAIT_S") or DEFAULT_GENERATION_WAIT_S)
    except ValueError:
        wait = DEFAULT_GENERATION_WAIT_S
    return wait + DEFAULT_TIMEOUT_SLACK_S


class OrchestratorClient:
    """Loopback client for the orchestrator (atlas.api): generation through the Arbiter, routing, strikes."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        env = os.environ
        self.base_url = (base_url or env.get("ORCH_URL") or f"http://127.0.0.1:{env.get('ORCH_PORT') or 8800}").rstrip(
            "/"
        )
        headers: dict[str, str] = {}
        token = admin_token()
        if token:
            headers["X-Atlas-Token"] = token
        read_s = generation_timeout_s() if timeout_s is None else timeout_s
        self.timeout_s = read_s
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=read_s, write=60.0, pool=60.0),
            trust_env=False,
            transport=transport,
            headers=headers,
        )
        self.last_child_task_id: str | None = None  # the orchestrator's child ledger row of the last generate()

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
        audience: str | None = None,
    ) -> str:
        """POST /internal/v1/chat/completions {model: <engine key>} -> the assistant text (generation-slot held).
        `audience="principal"`: the orchestrator runs the never-delegate rewrite on the answer (16.1 rule 5)."""
        body: dict[str, Any] = {
            "model": engine,
            "messages": list(messages),
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if task_id:
            body["atlas_task_id"] = task_id
        if audience:
            body["atlas_audience"] = audience
        r = self._http.post("/internal/v1/chat/completions", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"orchestrator generate on {engine} -> HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        self.last_child_task_id = str((data.get("atlas") or {}).get("task_id") or "") or None
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

    def done(self, result: Any = None, *, returned: Any = None) -> dict[str, Any]:
        """Mark the row done with `result` in the ledger; the Celery return value is `returned` when given (so a task
        can keep bulk text in the ledger and hand Redis only a small summary, fix round: the result backend keeps
        values for result_expires days)."""
        self.ledger.update_task(self.task_id, status="done", result=result)
        log.info("task %s (%s) done in %.1fs", self.task_id, self.kind, self.elapsed_s)
        self._close()
        out = result if returned is None else returned
        return out if isinstance(out, dict) else {"result": out}

    def failed(self, error: str) -> None:
        self.ledger.update_task(self.task_id, status="failed", error=error[:2000])
        log.error("task %s (%s) FAILED after %.1fs: %s", self.task_id, self.kind, self.elapsed_s, error)
        self._close()

    def _close(self) -> None:
        if self._own_ledger:
            self.ledger.close()


def json_line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
