"""The SQLite ledger: schema, idempotent init, insert/query helpers per table, the one-line JSON record."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import stat
import sys
import time
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from atlas.ledger import DAY_S, PERMANENT_TABLES, RETENTION_S, Ledger, open_ledger

EXPECTED_TABLES = {"tasks", "arbiter_decisions", "routing_decisions", "approvals", "strikes", "sentinel_pulses", "meta"}


def test_init_db_creates_every_table_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "sub" / "atlas.sqlite3"
    ledger = Ledger(db)
    ledger.init_db()
    ledger.init_db()
    assert db.is_file()
    assert set(ledger.tables()) == EXPECTED_TABLES
    assert ledger.query("SELECT value FROM meta WHERE key = 'schema_version'")[0]["value"] == "1"
    ledger.close()
    again = open_ledger(db)
    assert set(again.tables()) == EXPECTED_TABLES
    again.close()


def test_ledger_files_are_group_readable_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              caplog: pytest.LogCaptureFixture) -> None:
    # Sensitive-tier Principal data (approval drafts, payloads, family-name hits): 750 directories, 640 files, whatever
    # the caller's umask and wherever ATLAS_DB_PATH points (`atlas-admin init-db --db` loses the installer's guard).
    # Every level of a deep path is born 750, not only the leaf (mkdir(parents=True) applies `mode` to the leaf only;
    # the umask does the rest, so it is tightened before the mkdir).
    old_umask = os.umask(0o022)
    try:
        db = tmp_path / "x" / "y" / "deep" / "atlas.sqlite3"
        ledger = Ledger(db)
        ledger.init_db()
        ledger.insert_task("chat")  # forces the -wal file into existence
        assert stat.S_IMODE(db.stat().st_mode) == 0o640
        for d in (db.parent, db.parent.parent, db.parent.parent.parent):
            assert stat.S_IMODE(d.stat().st_mode) == 0o750, d
        for suffix in ("-wal", "-shm"):
            side = Path(str(db) + suffix)
            if side.exists():
                assert stat.S_IMODE(side.stat().st_mode) == 0o640, suffix
        ledger.close()
        # A pre-existing database created under a looser umask (and its side files) is tightened on open.
        loosened = [db, *(Path(str(db) + sfx) for sfx in ("-wal", "-shm") if Path(str(db) + sfx).exists())]
        for f in loosened:
            f.chmod(0o644)
        Ledger(db).close()
        for f in loosened:
            assert stat.S_IMODE(f.stat().st_mode) == 0o640, f
        # When it cannot be tightened (another owner), the ledger says so instead of passing silently (rule §7.4).
        db.chmod(0o644)

        def refuse(path: Any, mode: int, *a: Any, **kw: Any) -> None:
            raise PermissionError(1, "Operation not permitted", str(path))

        monkeypatch.setattr(os, "chmod", refuse)
        with caplog.at_level(logging.WARNING, logger="atlas.ledger"):
            Ledger(db).close()
        assert "could not be tightened to 640" in caplog.text and str(db) in caplog.text
        assert f"owned by uid {os.getuid()} with mode 644" in caplog.text
    finally:
        os.umask(old_umask)


def test_sql_identifiers_are_guarded() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    with pytest.raises(ValueError, match="not a ledger table"):
        ledger.record_json("sqlite_master", "x", key_col="name")
    with pytest.raises(ValueError, match="not a bare identifier"):
        ledger.record_json("tasks", "x", key_col="id = '' OR 1=1 --")
    with pytest.raises(ValueError, match="not a ledger table"):
        ledger._insert("tasks; DROP TABLE tasks", {"id": "x"})
    with pytest.raises(ValueError, match="not a bare identifier"):
        ledger._update("tasks", "id", "x", {"status = 'done' --": 1})


def test_open_ledger_reads_atlas_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "env.sqlite3"
    monkeypatch.setenv("ATLAS_DB_PATH", str(db))
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    ledger = open_ledger()
    assert Path(ledger.path) == db and db.is_file()
    ledger.close()


def test_tasks_roundtrip() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    tid = ledger.insert_task("chat", hemisphere="corporate", persona="ren", engine="gpt-oss-120b", tier="standard",
                             queue="gpu", payload={"prompt": "hi"})
    row = ledger.get_task(tid)
    assert row is not None and row["status"] == "queued" and json.loads(row["payload_json"]) == {"prompt": "hi"}
    assert ledger.update_task(tid, status="done", result={"ok": True}) == 1
    row = ledger.get_task(tid)
    assert row is not None and row["status"] == "done" and json.loads(row["result_json"]) == {"ok": True}
    assert [t["id"] for t in ledger.list_tasks(status="done")] == [tid]
    with pytest.raises(ValueError):
        ledger.update_task(tid, status="bogus")
    explicit = ledger.insert_task("sentinel", task_id="fixed-id")
    assert explicit == "fixed-id"


def test_arbiter_and_routing_decisions() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    ledger.insert_arbiter_decision(task_id="t1", action="load", engine="gpt-oss-120b", decision="granted",
                                   projected_bytes=10, budget_bytes=100, free_bytes=90, resident=["gpt-oss-120b"],
                                   reason="loaded")
    ledger.insert_arbiter_decision(task_id="t2", action="load", engine="nemotron-3-super", decision="refused",
                                   reason="over budget")
    assert [r["decision"] for r in ledger.list_arbiter_decisions()] == ["refused", "granted"]
    only = ledger.list_arbiter_decisions(task_id="t1")
    assert len(only) == 1 and json.loads(only[0]["resident_json"]) == ["gpt-oss-120b"]
    ledger.insert_routing_decision(task_id="t3", route="arthur", engine="nemotron-3-super", reason="hard keyword",
                                   hard_keyword_hit="medical", classifier_route="ren", tier="sensitive")
    rows = ledger.list_routing_decisions(task_id="t3")
    assert rows[0]["route"] == "arthur" and rows[0]["hard_keyword_hit"] == "medical"


def test_approvals_strikes_and_pulses() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    held = ledger.insert_approval(task_id="t1", tier="standard", kind="email", status="held", persona="helena",
                                  recipient="x@example.com", subject="Re:", draft="...", reasoning="substantive")
    auto = ledger.insert_approval(task_id="t2", tier="routine", kind="email", status="auto-sent")
    assert [a["id"] for a in ledger.list_approvals(status="held")] == [held]
    assert ledger.get_approval(auto)["decided_at"] is not None
    assert ledger.decide_approval(held, "approved", note="ok") == 1
    assert ledger.get_approval(held)["status"] == "approved"
    with pytest.raises(ValueError):
        ledger.insert_approval(task_id=None, tier="routine", kind="email", status="maybe")
    sid = ledger.insert_strike(task_id="t9", kind="tool-failure", description="Docling timed out", source="ouroboros")
    assert ledger.resolve_strike(sid, "retry with smaller batch", scar_id="scar-1") == 1
    assert ledger.list_strikes(task_id="t9")[0]["scar_id"] == "scar-1"
    ledger.insert_sentinel_pulse(task_id="p1", status="done", feeds=["coindesk"], anomalies=[], alerted=False,
                                 duration_s=1.5)
    pulses = ledger.list_sentinel_pulses()
    assert len(pulses) == 1 and pulses[0]["alerted"] == 0 and json.loads(pulses[0]["feeds_json"]) == ["coindesk"]


def test_record_json_is_one_line() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    tid = ledger.insert_task("sentinel", status="done")
    line = ledger.record_json("tasks", tid)
    assert "\n" not in line and json.loads(line)["id"] == tid
    with pytest.raises(KeyError):
        ledger.record_json("tasks", "nope")


def test_concurrent_writers_share_one_connection() -> None:
    import threading

    ledger = Ledger(":memory:")
    ledger.init_db()

    def work(n: int) -> None:
        for i in range(20):
            ledger.insert_arbiter_decision(task_id=f"w{n}-{i}", action="load", engine="e", decision="granted")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ledger.query("SELECT COUNT(*) AS n FROM arbiter_decisions")[0]["n"] == 80


# --- retention (10.4, D9) ---------------------------------------------------------------------------------------------


def test_purge_applies_the_d9_windows_and_never_touches_the_permanent_tables(caplog: pytest.LogCaptureFixture) -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    now = time.time()
    for i in range(3):
        ledger.insert_arbiter_decision(task_id=f"a{i}", action="load", engine="e", decision="granted")
        ledger.insert_routing_decision(task_id=f"r{i}", route="ren", engine="e", reason="x")
    ledger.insert_sentinel_pulse(task_id="p1", status="done", feeds=[], anomalies=[], alerted=False, duration_s=1.0)
    done = ledger.insert_task("sentinel", status="done")
    open_task = ledger.insert_task("chat", status="running")
    held = ledger.insert_approval(task_id="t1", tier="standard", kind="email", status="held")
    decided = ledger.insert_approval(task_id="t2", tier="routine", kind="email", status="auto-sent", draft="hi")
    ledger.insert_strike(task_id="t1", kind="tool-failure", description="x", source="ouroboros")
    archived: dict[str, list[dict[str, Any]]] = {}

    def archive(table: str, rows: list[dict[str, Any]]) -> None:
        archived.setdefault(table, []).extend(rows)

    # The windows: 30 days for the operational logs (approvals included, D9 names scars as the one permanent category),
    # 365 for Sentinel; nothing is young enough to go at 29 days.
    assert RETENTION_S["arbiter_decisions"] == RETENTION_S["routing_decisions"] == 30 * DAY_S
    assert RETENTION_S["approvals"] == 30 * DAY_S
    assert RETENTION_S["sentinel_pulses"] == 365 * DAY_S and PERMANENT_TABLES == {"strikes", "meta"}
    assert ledger.purge("arbiter_decisions", 30 * DAY_S, now=now + 29 * DAY_S, archive=archive) == 0
    assert ledger.purge_expired(now=now + 31 * DAY_S, archive=archive) == {
        "arbiter_decisions": 3, "routing_decisions": 3, "tasks": 1, "approvals": 1, "sentinel_pulses": 0}
    assert ledger.list_arbiter_decisions() == [] and ledger.get_task(done) is None
    assert ledger.get_task(open_task) is not None  # an open task is never purged whatever its age
    # A held approval is a question the Principal has not answered: never purged; the decided row went to the archive
    # (draft included) before the delete.
    assert ledger.get_approval(held) is not None and ledger.get_approval(decided) is None
    assert [r["id"] for r in archived["approvals"]] == [decided] and archived["approvals"][0]["draft"] == "hi"
    assert ledger.purge_expired(now=now + 366 * DAY_S, archive=archive)["sentinel_pulses"] == 1
    assert ledger.list_sentinel_pulses() == [] and len(archived["sentinel_pulses"]) == 1
    assert ledger.get_approval(held) is not None  # still held a year on
    # Scars are permanent (9.4); the schema row too.
    assert len(ledger.list_strikes(task_id="t1")) == 1
    for table in ("strikes", "meta"):
        with pytest.raises(ValueError, match="permanent"):
            ledger.purge(table, 1.0, archive=archive)
    with pytest.raises(ValueError, match="not a ledger table"):
        ledger.purge("sqlite_master", 1.0, archive=archive)
    with pytest.raises(ValueError, match="positive"):
        ledger.purge("tasks", 0, archive=archive)
    # The time-based sweep cannot discard: archive is a required argument (D9 "then archived"), and a per-table purge
    # without one says so in the log (rule §7.4, never silently).
    with pytest.raises(TypeError):
        ledger.purge_expired(now=now + 400 * DAY_S)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="archive callback"):
        ledger.purge_expired(now=now + 400 * DAY_S, archive=None)  # type: ignore[arg-type]
    ledger.insert_routing_decision(task_id="r9", route="ren", engine="e", reason="x")
    with caplog.at_level(logging.WARNING, logger="atlas.ledger"):
        assert ledger.purge("routing_decisions", 1.0, now=time.time() + 10.0) == 1
    assert "routing_decisions: 1 row(s)" in caplog.text and "WITHOUT an archive" in caplog.text


def test_transaction_reports_the_lock_not_a_failed_rollback(tmp_path: Path) -> None:
    # Under write contention BEGIN IMMEDIATE itself fails after the busy timeout. The error the operator must see is
    # "database is locked", not "cannot rollback - no transaction is active" from a ROLLBACK with nothing to roll back
    # (which pointed atlas-admin's message at permissions instead of the lock; rule §7.4).
    db = tmp_path / "atlas.sqlite3"
    first = Ledger(db)
    first.init_db()
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            Ledger(db, timeout_s=0.2).insert_task("chat")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    # Released: the same ledger writes again (no connection state was left half-open by the failed BEGIN).
    second = Ledger(db, timeout_s=0.2)
    assert second.get_task(second.insert_task("chat")) is not None
    second.close()
    first.close()


def test_purge_archives_before_it_deletes_and_keeps_rows_when_the_archive_fails() -> None:
    ledger = Ledger(":memory:")
    ledger.init_db()
    for i in range(4):
        ledger.insert_arbiter_decision(task_id=f"a{i}", action="load", engine="e", decision="granted")
    archived: list[tuple[str, list[dict[str, Any]]]] = []

    def boom(table: str, rows: list[dict[str, Any]]) -> None:
        raise OSError("cold storage unwritable")

    with pytest.raises(OSError, match="cold storage"):
        ledger.purge("arbiter_decisions", 1.0, now=time.time() + 10.0, archive=boom)
    assert len(ledger.list_arbiter_decisions()) == 4  # the delete is in the same transaction as the archive
    n = ledger.purge("arbiter_decisions", 1.0, now=time.time() + 10.0, archive=lambda t, r: archived.append((t, r)),
                     vacuum=True)
    assert n == 4 and archived[0][0] == "arbiter_decisions" and [r["task_id"] for r in archived[0][1]] == [
        "a0", "a1", "a2", "a3"]
    assert ledger.list_arbiter_decisions() == []
    assert ledger.purge("arbiter_decisions", 1.0, now=time.time() + 10.0,
                        archive=lambda t, r: archived.append((t, r))) == 0
    assert len(archived) == 1  # nothing to archive, nothing called


# --- atlas-admin enqueue: what reaches the journal and the ledger ---------------------------------------------------


def test_enqueue_redacts_broker_credentials_from_stderr_and_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    # Task results and error strings are printed to the journal and stored in the tasks row (contract next to
    # ENQUEUE_TASKS); kombu names the broker URL, credentials included, in its connection errors.
    from atlas import admin

    assert admin._redact("Error 111 connecting to redis://:hunter2@127.0.0.1:6379/0. Connection refused.") == (
        "Error 111 connecting to redis://:***@127.0.0.1:6379/0. Connection refused.")
    assert admin._redact("amqp://atlas:s3cr3t@broker:5672//") == "amqp://atlas:***@broker:5672//"
    assert admin._redact("https://ntfy.example/topic no credentials") == "https://ntfy.example/topic no credentials"
    # An unencoded '/' or '+' inside the password (a base64 secret) and the ?password= query form are masked too.
    assert admin._redact("redis://:abc/def+ghi==@127.0.0.1:6379/0 refused") == "redis://:***@127.0.0.1:6379/0 refused"
    assert admin._redact("rediss://u:p%2Fw@h:1/0?ssl=1 and redis://h:6379/0?password=hunter2&db=0") == (
        "rediss://u:***@h:1/0?ssl=1 and redis://h:6379/0?password=***&db=0")
    assert admin._redact("redis://h/0 then TOKEN=x? no url") == "redis://h/0 then TOKEN=x? no url"
    # A SUCCESS result is masked the same way before it is stored or printed (the task writer's contract is not relied
    # on); a result the mask would make unparseable is stored as the masked text.
    assert admin._redacted_result({"broker": "redis://:hunter2@h:6379/0", "n": 1}) == {
        "broker": "redis://:***@h:6379/0", "n": 1}
    assert admin._redacted_result(["ok", "amqp://a:b@c//"]) == ["ok", "amqp://a:***@c//"]

    class FakeApp:
        class conf:  # mirrors celery's lowercase attribute
            task_default_queue = "cpu"

        amqp = types.SimpleNamespace(router=types.SimpleNamespace(route=lambda *_a: {"queue": "cpu"}))

        def send_task(self, name: str, **kw: Any) -> Any:
            raise ConnectionError(f"cannot send {name}: Error 111 connecting to redis://:hunter2@127.0.0.1:6379/0")

    monkeypatch.setitem(sys.modules, "atlas.celery_app", types.SimpleNamespace(app=FakeApp()))
    db = tmp_path / "atlas.sqlite3"
    monkeypatch.setenv("ATLAS_DB_PATH", str(db))
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    rc = admin.cmd_enqueue(argparse.Namespace(task="sentinel", queue=None, wait=None))
    assert rc == 1
    err = capsys.readouterr().err
    assert "hunter2" not in err and "redis://:***@127.0.0.1:6379/0" in err
    ledger = Ledger(db)
    rows = ledger.list_tasks(status="failed")
    assert len(rows) == 1 and rows[0]["queue"] == "cpu"
    assert "hunter2" not in rows[0]["error"] and "redis://:***@127.0.0.1:6379/0" in rows[0]["error"]
    assert "hunter2" not in json.dumps(dict(rows[0]))
    ledger.close()


def test_enqueue_wait_masks_a_success_result_before_the_ledger_and_stdout(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch,
                                                                            capsys: pytest.CaptureFixture[str]) -> None:
    from atlas import admin

    class FakeResult:
        state = "SUCCESS"
        result: ClassVar[dict[str, str]] = {"status": "pulse ok", "broker": "redis://:hunter2@127.0.0.1:6379/0"}

        def get(self, timeout: float, propagate: bool) -> Any:
            return self.result

    class FakeApp:
        class conf:
            task_default_queue = "cpu"

        amqp = types.SimpleNamespace(router=types.SimpleNamespace(route=lambda *_a: {"queue": "cpu"}))

        def send_task(self, name: str, **kw: Any) -> Any:
            return FakeResult()

    monkeypatch.setitem(sys.modules, "atlas.celery_app", types.SimpleNamespace(app=FakeApp()))
    db = tmp_path / "atlas.sqlite3"
    monkeypatch.setenv("ATLAS_DB_PATH", str(db))
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    assert admin.cmd_enqueue(argparse.Namespace(task="sentinel", queue=None, wait=5.0)) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "hunter2" not in out[0]  # ONE JSON line, masked (contract in phase2/02-orchestrator.sh)
    line = json.loads(out[0])
    assert line["status"] == "done" and json.loads(line["result_json"])["broker"] == "redis://:***@127.0.0.1:6379/0"
    ledger = Ledger(db)
    row = ledger.list_tasks(status="done")[0]
    assert "hunter2" not in json.dumps(dict(row)) and "redis://:***@127.0.0.1:6379/0" in row["result_json"]
    ledger.close()


def test_admin_refuses_root_on_a_ledger_owned_by_another_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    # The Principal running `sudo atlas-admin init-db|enqueue|arbiter status` by hand would leave root-owned -wal/-shm
    # files next to the atlas-owned database and the service would fail on its next write (rule §7.4): refused with
    # the runuser line, before anything is opened. The uid lookups are stubbed; the suite may itself run as root.
    from atlas import admin

    db = tmp_path / "atlas.sqlite3"
    assert admin._root_owner_conflict(db, euid=0) is None  # nothing there yet: a fresh init-db as root is root's call
    db.write_bytes(b"")
    monkeypatch.setattr(admin, "_file_uid", lambda _p: 999)
    assert admin._root_owner_conflict(db, euid=999) is None  # the owner itself
    assert admin._root_owner_conflict(db, euid=1000) is None  # not root: the ledger's own permission errors apply
    msg = admin._root_owner_conflict(db, euid=0)
    assert msg is not None and "owned by uid 999" in msg and "runuser -u atlas -- atlas-admin" in msg
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    assert admin.main(["init-db", "--db", str(db)]) == 2
    assert "owned by uid 999" in capsys.readouterr().err and db.read_bytes() == b""  # untouched
    monkeypatch.setenv("ATLAS_DB_PATH", str(db))
    assert admin.main(["arbiter", "status", "--json", "--limit", "1"]) == 2
    monkeypatch.setattr(admin, "_file_uid", lambda _p: 0)
    assert admin.main(["init-db", "--db", str(db)]) == 0  # root's own file: fine
    assert "ledger ready" in capsys.readouterr().out


def test_admin_config_check_runs_the_strict_card_pass(config_dir: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    # For the Phase 2 gate: exit 0 + one line when the tree loads and every card carries the §8 triple; exit 1 naming
    # the drifting cards (the service itself still starts on that tree: load_config is lenient).
    import shutil

    from atlas import admin
    from atlas.config import load_config

    tree = tmp_path / "config"
    shutil.copytree(config_dir, tree)
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tree))
    monkeypatch.setenv("ATLAS_ETC", str(tree))
    assert admin.main(["config", "check"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("config ok:") and "cards=6" in out and out.count("\n") == 1
    card = tree / "domains" / "cards" / "07-development-director-property-real-estate.md"
    card.write_text(card.read_text().replace("(Corporate, Valerie with Silas, Tier A)",
                                             "(Corporate | Valerie, with Silas | Tier A)"))
    assert load_config().domain_cards[7].owner == "Valerie with Silas"  # the service would start
    assert admin.main(["config", "check"]) == 1
    err = capsys.readouterr().err
    assert "1 domain card(s) do not carry" in err and "07-development-director" in err
    assert "'(Corporate, Valerie with Silas, Tier A)'" in err
