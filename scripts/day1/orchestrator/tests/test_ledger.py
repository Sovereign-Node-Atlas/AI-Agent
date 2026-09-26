"""The SQLite ledger: schema, idempotent init, insert/query helpers per table, the one-line JSON record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.ledger import Ledger, open_ledger

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
