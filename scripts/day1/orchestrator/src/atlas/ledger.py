"""The SQLite task ledger (Section 9.7 "task ID", 4.2 rule 9, 7.2 rule 5, 16.2, 9.4, 9.3).

One file, ATLAS_DB_PATH (/srv/atlas/data/orchestrator/atlas.sqlite3, phase2/02-orchestrator.sh), created by
`atlas-admin init-db`. Six tables:

    tasks              one row per Celery/orchestrator task id (Appendix A: "Celery task, task ID, ledger entry")
    arbiter_decisions  every Engine Arbiter decision with its task id (4.2 rule 9)
    routing_decisions  every 4-Way Router decision with its reason (7.2 rule 5)
    approvals          the approval queue (16.2): held / approved / rejected / auto-sent, with the tier
    strikes            Ouroboros strikes (9.4) and their scar id
    sentinel_pulses    one row per Sentinel pulse (9.3)

Helpers are deliberately small: insert/update/get/list per table plus `record_json` (one JSON line for
`atlas-admin enqueue --wait`, contract in phase2/02-orchestrator.sh). Thread-safe through one connection and a lock;
WAL mode so the API and the Celery workers can read while one writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

TABLES: dict[str, str] = {
    "meta": """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    "tasks": """
        CREATE TABLE IF NOT EXISTS tasks (
            id             TEXT PRIMARY KEY,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL,
            kind           TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'queued',
            hemisphere     TEXT,
            persona        TEXT,
            engine         TEXT,
            tier           TEXT,
            parent_task_id TEXT,
            queue          TEXT,
            payload_json   TEXT,
            result_json    TEXT,
            error          TEXT
        )""",
    "arbiter_decisions": """
        CREATE TABLE IF NOT EXISTS arbiter_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL NOT NULL,
            task_id         TEXT,
            action          TEXT NOT NULL,
            engine          TEXT,
            decision        TEXT NOT NULL,
            projected_bytes INTEGER,
            budget_bytes    INTEGER,
            free_bytes      INTEGER,
            resident_json   TEXT,
            reason          TEXT
        )""",
    "routing_decisions": """
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts               REAL NOT NULL,
            task_id          TEXT,
            message_sha256   TEXT,
            route            TEXT NOT NULL,
            engine           TEXT,
            hard_keyword_hit TEXT,
            override         TEXT,
            classifier_route TEXT,
            task_force       TEXT,
            tier             TEXT,
            reason           TEXT NOT NULL
        )""",
    "approvals": """
        CREATE TABLE IF NOT EXISTS approvals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            task_id     TEXT,
            tier        TEXT NOT NULL,
            kind        TEXT NOT NULL,
            persona     TEXT,
            recipient   TEXT,
            subject     TEXT,
            draft       TEXT,
            reasoning   TEXT,
            status      TEXT NOT NULL,
            decided_at  REAL,
            decided_by  TEXT,
            note        TEXT
        )""",
    "strikes": """
        CREATE TABLE IF NOT EXISTS strikes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            task_id     TEXT,
            kind        TEXT NOT NULL,
            source      TEXT,
            description TEXT NOT NULL,
            resolution  TEXT,
            scar_id     TEXT,
            resolved_at REAL
        )""",
    "sentinel_pulses": """
        CREATE TABLE IF NOT EXISTS sentinel_pulses (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             REAL NOT NULL,
            task_id        TEXT,
            status         TEXT NOT NULL,
            feeds_json     TEXT,
            anomalies_json TEXT,
            alerted        INTEGER NOT NULL DEFAULT 0,
            duration_s     REAL,
            error          TEXT
        )""",
}

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_arbiter_task ON arbiter_decisions(task_id)",
    "CREATE INDEX IF NOT EXISTS ix_arbiter_ts ON arbiter_decisions(ts)",
    "CREATE INDEX IF NOT EXISTS ix_routing_task ON routing_decisions(task_id)",
    "CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals(status)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS ix_strikes_task ON strikes(task_id)",
)

TASK_STATUSES: frozenset[str] = frozenset({"queued", "running", "done", "failed", "cancelled"})
APPROVAL_STATUSES: frozenset[str] = frozenset({"held", "approved", "rejected", "auto-sent", "sent"})


def new_task_id() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Ledger:
    """SQLite-backed ledger. `Ledger(':memory:')` is fine for tests."""

    def __init__(self, path: str | Path, *, timeout_s: float = 30.0) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=timeout_s, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    # --- lifecycle ---------------------------------------------------------------------------------------------------

    def init_db(self) -> None:
        """Create every table and index; idempotent (phase2/02-orchestrator.sh runs it on every re-run)."""
        with self.transaction() as cur:
            for ddl in TABLES.values():
                cur.execute(ddl)
            for ddl in INDEXES:
                cur.execute(ddl)
            cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (str(time.time()),))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def tables(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                          "ORDER BY name")
        return [r["name"] for r in rows]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                yield cur
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

    # --- generic -----------------------------------------------------------------------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            try:
                return [dict(r) for r in cur.fetchall()]
            finally:
                cur.close()

    def _insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.transaction() as cur:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
            return int(cur.lastrowid or 0)

    def _update(self, table: str, key_col: str, key: Any, fields: dict[str, Any]) -> int:
        if not fields:
            return 0
        sets = ", ".join(f"{c} = ?" for c in fields)
        with self.transaction() as cur:
            cur.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = ?", (*fields.values(), key))
            return cur.rowcount

    def _get(self, table: str, key_col: str, key: Any) -> dict[str, Any] | None:
        rows = self.query(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,))
        return rows[0] if rows else None

    def record_json(self, table: str, key: Any, key_col: str = "id") -> str:
        """One JSON object line for a row (the `atlas-admin enqueue --wait` output contract)."""
        row = self._get(table, key_col, key)
        if row is None:
            raise KeyError(f"{table}.{key_col} = {key!r} not found")
        return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)

    # --- tasks -------------------------------------------------------------------------------------------------------

    def insert_task(self, kind: str, *, task_id: str | None = None, status: str = "queued",
                    hemisphere: str | None = None, persona: str | None = None, engine: str | None = None,
                    tier: str | None = None,
                    parent_task_id: str | None = None, queue: str | None = None, payload: Any = None) -> str:
        if status not in TASK_STATUSES:
            raise ValueError(f"task status {status!r} not in {sorted(TASK_STATUSES)}")
        task_id = task_id or new_task_id()
        now = time.time()
        self._insert("tasks", {
            "id": task_id, "created_at": now, "updated_at": now, "kind": kind, "status": status,
            "hemisphere": hemisphere, "persona": persona, "engine": engine, "tier": tier,
            "parent_task_id": parent_task_id, "queue": queue, "payload_json": _json(payload),
        })
        return task_id

    def update_task(self, task_id: str, *, status: str | None = None, result: Any = None, error: str | None = None,
                    engine: str | None = None, persona: str | None = None, tier: str | None = None) -> int:
        fields: dict[str, Any] = {"updated_at": time.time()}
        if status is not None:
            if status not in TASK_STATUSES:
                raise ValueError(f"task status {status!r} not in {sorted(TASK_STATUSES)}")
            fields["status"] = status
        if result is not None:
            fields["result_json"] = _json(result)
        if error is not None:
            fields["error"] = error
        if engine is not None:
            fields["engine"] = engine
        if persona is not None:
            fields["persona"] = persona
        if tier is not None:
            fields["tier"] = tier
        return self._update("tasks", "id", task_id, fields)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._get("tasks", "id", task_id)

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is None:
            return self.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit))

    # --- arbiter decisions (4.2 rule 9) -------------------------------------------------------------------------------

    def insert_arbiter_decision(self, *, task_id: str | None, action: str, engine: str | None, decision: str,
                                projected_bytes: int | None = None, budget_bytes: int | None = None,
                                free_bytes: int | None = None, resident: Sequence[str] | None = None,
                                reason: str = "") -> int:
        return self._insert("arbiter_decisions", {
            "ts": time.time(), "task_id": task_id, "action": action, "engine": engine, "decision": decision,
            "projected_bytes": projected_bytes, "budget_bytes": budget_bytes, "free_bytes": free_bytes,
            "resident_json": _json(list(resident) if resident is not None else None), "reason": reason,
        })

    def list_arbiter_decisions(self, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if task_id is None:
            return self.query("SELECT * FROM arbiter_decisions ORDER BY id DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM arbiter_decisions WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                          (task_id, limit))

    # --- routing decisions (7.2 rule 5) -------------------------------------------------------------------------------

    def insert_routing_decision(self, *, task_id: str | None, route: str, reason: str, engine: str | None = None,
                                message_sha256: str | None = None, hard_keyword_hit: str | None = None,
                                override: str | None = None, classifier_route: str | None = None,
                                task_force: str | None = None, tier: str | None = None) -> int:
        return self._insert("routing_decisions", {
            "ts": time.time(), "task_id": task_id, "message_sha256": message_sha256, "route": route, "engine": engine,
            "hard_keyword_hit": hard_keyword_hit, "override": override, "classifier_route": classifier_route,
            "task_force": task_force, "tier": tier, "reason": reason,
        })

    def list_routing_decisions(self, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if task_id is None:
            return self.query("SELECT * FROM routing_decisions ORDER BY id DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM routing_decisions WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                          (task_id, limit))

    # --- approvals (16.2) ---------------------------------------------------------------------------------------------

    def insert_approval(self, *, task_id: str | None, tier: str, kind: str, status: str, persona: str | None = None,
                        recipient: str | None = None, subject: str | None = None, draft: str | None = None,
                        reasoning: str | None = None, note: str | None = None) -> int:
        if status not in APPROVAL_STATUSES:
            raise ValueError(f"approval status {status!r} not in {sorted(APPROVAL_STATUSES)}")
        return self._insert("approvals", {
            "ts": time.time(), "task_id": task_id, "tier": tier, "kind": kind, "persona": persona,
            "recipient": recipient, "subject": subject, "draft": draft, "reasoning": reasoning, "status": status,
            "decided_at": time.time() if status in {"auto-sent", "sent"} else None, "note": note,
        })

    def decide_approval(self, approval_id: int, status: str, *, decided_by: str = "principal",
                        note: str | None = None) -> int:
        if status not in APPROVAL_STATUSES:
            raise ValueError(f"approval status {status!r} not in {sorted(APPROVAL_STATUSES)}")
        return self._update("approvals", "id", approval_id,
                            {"status": status, "decided_at": time.time(), "decided_by": decided_by, "note": note})

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        return self._get("approvals", "id", approval_id)

    def list_approvals(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is None:
            return self.query("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM approvals WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))

    # --- strikes (9.4) ------------------------------------------------------------------------------------------------

    def insert_strike(self, *, task_id: str | None, kind: str, description: str, source: str | None = None,
                      resolution: str | None = None, scar_id: str | None = None) -> int:
        return self._insert("strikes", {
            "ts": time.time(), "task_id": task_id, "kind": kind, "source": source, "description": description,
            "resolution": resolution, "scar_id": scar_id, "resolved_at": None,
        })

    def resolve_strike(self, strike_id: int, resolution: str, scar_id: str | None = None) -> int:
        fields: dict[str, Any] = {"resolution": resolution, "resolved_at": time.time()}
        if scar_id is not None:
            fields["scar_id"] = scar_id
        return self._update("strikes", "id", strike_id, fields)

    def list_strikes(self, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if task_id is None:
            return self.query("SELECT * FROM strikes ORDER BY id DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM strikes WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit))

    # --- sentinel pulses (9.3) ----------------------------------------------------------------------------------------

    def insert_sentinel_pulse(self, *, task_id: str | None, status: str, feeds: Any = None, anomalies: Any = None,
                              alerted: bool = False, duration_s: float | None = None, error: str | None = None) -> int:
        return self._insert("sentinel_pulses", {
            "ts": time.time(), "task_id": task_id, "status": status, "feeds_json": _json(feeds),
            "anomalies_json": _json(anomalies), "alerted": 1 if alerted else 0, "duration_s": duration_s,
            "error": error,
        })

    def list_sentinel_pulses(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sentinel_pulses ORDER BY id DESC LIMIT ?", (limit,))


def open_ledger(path: str | Path | None = None) -> Ledger:
    """Open ATLAS_DB_PATH (or the given path) and make sure the schema exists."""
    if path is None:
        from atlas.config import Settings

        path = Settings.from_env().db_path
    ledger = Ledger(path)
    ledger.init_db()
    return ledger
