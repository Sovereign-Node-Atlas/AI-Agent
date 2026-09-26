"""The orchestrator API with TestClient: /v1/models lists three; a chat completion streams through the REAL router
(stub classifier), the REAL prompt builder, the REAL Arbiter over stub controller/probe and a StubLlama; the approval
flow runs the REAL ApprovalQueue over StubSender; vault, strike and arbiter endpoints. No live service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas import vault as vault_mod
from atlas.api import AppDeps, NoChannelSender, build_app
from atlas.approval import ApprovalQueue, StubNotifier, StubSender
from atlas.config import load_config
from atlas.ledger import Ledger
from atlas.memory import MemoryStore, StubChroma, stub_embedding_fn
from atlas.personas import PersonaRegistry
from atlas.prompts import build_system_prompt
from atlas.router import ClassifierVerdict, Router, StubClassifier
from atlas.vault import SessionTags, VaultController
from stubs import FakeVaultRunner, StubLlamaFactory, make_arbiter

# The real config/router-rules.json carries these; the shared fixture lists only the routing prefixes.
COMMAND_OVERRIDES = {
    "[LOG STRIKE:": "ouroboros-strike",
    "[EXECUTE AEGIS BACKUP]": "aegis",
    "[VAULT]": "vault-session",
    "[DEEP THINK:QUICK]": "deep-think:quick",
}


def classify(text: str) -> ClassifierVerdict:
    """Eleanor's stub: corporate/Ren unless the text smells of the estate (the hard rules overrule it anyway)."""
    if "estate" in text.lower() or "family" in text.lower():
        return ClassifierVerdict(hemisphere="estate", persona="arthur")
    return ClassifierVerdict(hemisphere="corporate", persona="ren")


@pytest.fixture
def harness(config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    config = load_config()
    config.router_rules = config.router_rules.model_copy(
        update={"overrides": {**config.router_rules.overrides, **COMMAND_OVERRIDES}}
    )
    ledger = Ledger(":memory:")
    ledger.init_db()
    arbiter, controller, _probe = make_arbiter(config.engines, ledger=ledger)
    sessions = SessionTags(tmp_path / "sessions.json")
    chroma = StubChroma()
    memory = MemoryStore(chroma, stub_embedding_fn, sessions=sessions, freeze_flag=None)
    runner = FakeVaultRunner()
    monkeypatch.setattr(vault_mod, "is_mounted_here", lambda mount_dir, mountinfo="x": runner.state == "open")
    vault = VaultController(helper="/usr/local/bin/atlas-vault", mount_dir=str(tmp_path / "open"), runner=runner)
    llama = StubLlamaFactory()
    personas = PersonaRegistry(config.personas, config.engines)
    router = Router(config, StubClassifier(classify), ledger, personas=personas)
    sender, notifier = StubSender(), StubNotifier()
    approval = ApprovalQueue(ledger, sender, notifier, personas=personas)
    deps = AppDeps(
        config=config,
        ledger=ledger,
        arbiter=arbiter,
        router=router,
        build_system_prompt=lambda d, cards=(), memory=None: build_system_prompt(d, cards, memory, personas=personas),
        streamer_for=llama,
        vault=vault,
        sessions=sessions,
        approval=approval,
        memory=memory,
        load_wait_s=5.0,
        generation_wait_s=5.0,
        enqueue=lambda name, kw: "celery-task-1",
    )
    app = build_app(deps)
    return {
        "client": TestClient(app),
        "deps": deps,
        "ledger": ledger,
        "arbiter": arbiter,
        "controller": controller,
        "llama": llama,
        "router": router,
        "chroma": chroma,
        "sessions": sessions,
        "runner": runner,
        "config": config,
        "sender": sender,
        "notifier": notifier,
    }


def _sse_chunks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


def _chat(client: TestClient, content: str, model: str = "atlas", **extra: Any) -> Any:
    return client.post(
        "/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": content}], **extra}
    )


# --- models and health ------------------------------------------------------------------------------------------------


def test_models_lists_atlas_ren_arthur(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == ["atlas", "ren", "arthur"]
    assert client.get("/health").status_code == 200
    assert _chat(client, "x", model="gpt-4").status_code == 404


# --- chat -------------------------------------------------------------------------------------------------------------


def test_chat_streams_sse_through_router_prompt_arbiter_and_llama(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "atlas",
            "stream": True,
            "messages": [{"role": "user", "content": "Draft the AEC bid summary."}],
            "atlas_session": "chat-1",
        },
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert body.rstrip().endswith("data: [DONE]")
    chunks = _sse_chunks(body)
    head = chunks[0]["atlas"]
    assert head["persona"] == "ren" and head["engine"] == "gpt-oss-120b" and head["hemisphere"] == "corporate"
    text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)
    assert text == "Stub answer from the engine."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and "timings" in chunks[-1]
    # The Arbiter loaded Ren's engine through the controller and released the generation lock.
    assert ("start", "gpt-oss-120b") in harness["controller"].calls
    assert "gpt-oss-120b" in harness["arbiter"].resident and harness["arbiter"].generating is None
    # The layered prompt (persona core + governance block, 4.4) went to the engine first.
    req = harness["llama"].made[0].requests[0]
    assert req["messages"][0]["role"] == "system"
    assert "Ren Ackerman" in req["messages"][0]["content"] and "approval" in req["messages"][0]["content"].lower()
    assert req["messages"][-1] == {"role": "user", "content": "Draft the AEC bid summary."}
    # Ledger: the task is done, the routing decision is logged (7.2 rule 5).
    task_id = head["task_id"]
    row = harness["ledger"].get_task(task_id)
    assert row["status"] == "done" and row["engine"] == "gpt-oss-120b" and row["persona"] == "ren"
    assert harness["ledger"].list_routing_decisions(task_id=task_id)
    # The exchange was remembered in the corporate collection (not vault-tagged).
    assert harness["chroma"].collections["corporate"].count() == 1


def test_chat_non_stream_returns_one_completion(harness: dict[str, Any]) -> None:
    r = _chat(harness["client"], "Summarise Q3.", stream=False)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Stub answer from the engine."
    assert data["atlas"]["hemisphere"] == "corporate" and data["timings"]["predicted_n"] == 5


def test_ren_and_arthur_force_the_hemisphere_and_hard_rules_win(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = _chat(client, "hi", model="arthur")
    assert r.json()["atlas"]["persona"] == "arthur" and r.json()["atlas"]["engine"] == "nemotron-3-super"
    assert r.json()["atlas"]["override"] == "[ARTHUR]"
    r = _chat(client, "hi", model="ren")
    assert r.json()["atlas"]["persona"] == "ren" and r.json()["atlas"]["override"] == "[REN]"
    # The engine never sees the forcing prefix.
    assert harness["llama"].made[-1].requests[0]["messages"][-1]["content"] == "hi"
    # "atlas" leaves it to the router: a medical message lands on Arthur although Eleanor said Ren (7.2 rule 1, V16).
    r = _chat(client, "medical results to review")
    assert r.json()["atlas"]["persona"] == "arthur" and r.json()["atlas"]["hard_keyword_hit"] == "medical"
    assert "hard-rule:medical" in r.json()["atlas"]["reason"]


def test_vault_tagged_session_is_not_remembered(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = _chat(client, "the will says", model="arthur", atlas_session="chat-vault", atlas_vault=True)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"]
    assert harness["sessions"].is_vault("chat-vault")
    cols = harness["chroma"].collections
    assert "estate" not in cols or cols["estate"].count() == 0  # retrieval may create it; the write never lands
    assert harness["deps"].memory.dropped[-1]["session_id"] == "chat-vault"
    # The [VAULT] prefix from the filter does the same through the router's command.
    r = _chat(client, "[VAULT] open the estate papers", atlas_session="chat-2", atlas_override="[VAULT]")
    assert "vault-tagged" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "vault-session" and r.json()["atlas"]["persona"] == "arthur"
    assert harness["sessions"].is_vault("chat-2")


def test_engine_failure_records_a_strike_and_streams_an_error(harness: dict[str, Any]) -> None:
    harness["llama"].fail = "llama-server HTTP 500: boom"
    client: TestClient = harness["client"]
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "atlas", "stream": True, "messages": [{"role": "user", "content": "Plan the bid."}]},
    ) as r:
        body = "".join(r.iter_text())
    chunks = _sse_chunks(body)
    err = next(c for c in chunks if "error" in c)
    assert err["error"]["status"] == 502 and "boom" in err["error"]["message"]
    task_id = err["error"]["task_id"]
    assert harness["ledger"].get_task(task_id)["status"] == "failed"
    strikes = harness["ledger"].list_strikes(task_id=task_id)
    assert len(strikes) == 1 and strikes[0]["kind"] == "failed-generation"
    assert harness["chroma"].collections["scars"].count() == 1
    assert harness["arbiter"].generating is None  # the lock was released on failure
    r = _chat(client, "x")
    assert r.status_code == 502 and r.json()["error"]["status"] == 502


def test_log_strike_prefix_records_a_manual_strike(harness: dict[str, Any]) -> None:
    r = _chat(harness["client"], "[LOG STRIKE: wrong-tax-rate] used 30% not 25%")
    assert r.status_code == 200 and "Strike #1" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "ouroboros-strike"
    strike = harness["ledger"].list_strikes()[0]
    assert strike["kind"] == "manual" and "wrong-tax-rate" in strike["description"]
    assert harness["chroma"].collections["scars"].count() == 1


def test_execute_aegis_backup_prefix_enqueues_the_manual_trigger(harness: dict[str, Any]) -> None:
    r = _chat(harness["client"], "[EXECUTE AEGIS BACKUP]")
    assert "celery-task-1" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "aegis"


def test_internal_generate_route_and_plan(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = client.post(
        "/internal/v1/chat/completions",
        json={"model": "router-qwen3.5-4b", "messages": [{"role": "user", "content": "BLUF this"}]},
    )
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"]
    assert ("start", "router-qwen3.5-4b") in harness["controller"].calls
    assert harness["arbiter"].resident == {}  # resident small models are never in the engine ledger (4.1)
    bad = client.post(
        "/internal/v1/chat/completions", json={"model": "nope", "messages": [{"role": "user", "content": "x"}]}
    )
    assert bad.status_code == 404
    r = client.post("/internal/route", json={"message": "family medical file"})
    assert r.json()["hemisphere"] == "estate" and r.json()["persona"] == "arthur"
    r = client.post("/internal/deep-think/plan", json={"tier": "deep"})
    assert r.status_code == 200 and r.json()["requested"] == "deep"
    assert r.json()["granted"] in ("deep", "standard", "quick")


def test_deep_think_quick_runs_through_the_engine(harness: dict[str, Any]) -> None:
    r = _chat(harness["client"], "[DEEP THINK:QUICK] best exit structure?", atlas_override="[DEEP THINK:QUICK]")
    assert r.status_code == 200
    assert "Stub answer" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "deep-think" and r.json()["atlas"]["deep_think"] == "quick"
    # quick: generator + adversary + refine on ONE engine, several calls, no second engine loaded.
    engines = {s.spec.key for s in harness["llama"].made}
    assert engines == {"gpt-oss-120b"} and len(harness["llama"].made) >= 4


# --- approvals (16.2) -------------------------------------------------------------------------------------------------


def test_approval_flow(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    ledger: Ledger = harness["ledger"]
    held = ledger.insert_approval(
        task_id="t1",
        tier="standard",
        kind="email",
        status="held",
        persona="silas",
        recipient="cfo@example.com",
        subject="Q3 numbers",
        draft="Please find the numbers attached.",
    )
    auto = ledger.insert_approval(task_id="t2", tier="routine", kind="email", status="auto-sent", persona="eleanor")
    r = client.get("/approvals")
    assert r.status_code == 200 and [i["id"] for i in r.json()["items"]] == [held]
    assert client.get("/approvals", params={"status": "all"}).json()["count"] == 2
    r = client.post(f"/approvals/{held}/approve", json={"decided_by": "principal", "note": "go"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["dispatched"] is True
    row = ledger.get_approval(held)
    assert row["status"] == "sent" and "approved by principal" in row["note"]
    assert client.post(f"/approvals/{held}/approve").status_code == 409
    assert client.post(f"/approvals/{auto}/reject").status_code == 409
    assert client.post("/approvals/999/reject").status_code == 404
    sensitive = ledger.insert_approval(
        task_id="t3", tier="sensitive", kind="email", status="held", persona="gideon", recipient="x@y", draft="d"
    )
    # 16.2: a sensitive item needs the strong cross-check before the Principal can approve it.
    assert client.post(f"/approvals/{sensitive}/approve").status_code == 409
    r = client.post(f"/approvals/{sensitive}/reject", json={"note": "not now"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and "not now" in r.json()["note"]


def test_no_channel_sender_fails_the_send_loudly(harness: dict[str, Any]) -> None:
    """Production has no Section 13 channel yet: approving records the decision and the send fails, never pretends."""
    ledger: Ledger = harness["ledger"]
    harness["deps"].approval.sender = NoChannelSender()
    held = ledger.insert_approval(
        task_id="t9", tier="standard", kind="email", status="held", persona="silas", recipient="a@b", draft="d"
    )
    r = harness["client"].post(f"/approvals/{held}/approve")
    assert r.status_code == 502 and "no outbound channel" in r.json()["detail"]
    row = ledger.get_approval(held)
    assert row["status"] == "approved" and "send failed" in row["note"]
    assert harness["client"].get("/health").json()["outbound_channel"] is False


# --- vault, strike, arbiter -------------------------------------------------------------------------------------------


def test_vault_endpoints(harness: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    client: TestClient = harness["client"]
    assert client.get("/vault/status").json()["state"] == "locked"
    with caplog.at_level("DEBUG"):
        r = client.post("/vault/open", json={"passphrase": "hunter2-very-secret"})
    assert r.status_code == 200 and r.json()["state"] == "open"
    assert harness["runner"].calls[-1][1] == "hunter2-very-secret\n"
    assert "hunter2" not in caplog.text
    assert client.get("/vault/status").json()["state"] == "open"
    assert client.post("/vault/lock").json()["state"] == "locked"
    assert client.get("/vault/status").json()["state"] == "locked"
    harness["runner"].refuse = True
    assert client.post("/vault/open", json={"passphrase": "wrong"}).status_code == 403


def test_strike_and_arbiter_endpoints(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = client.post(
        "/strike",
        json={
            "persona": "silas",
            "domain": "TF_GAMMA",
            "context": "valuation",
            "error": "used pre-tax figure",
            "correction": "use after-tax",
        },
    )
    assert r.status_code == 200 and r.json()["scar_written"] and r.json()["strike_id"] == 1
    assert client.post("/strike", json={"persona": "x", "error": "e", "kind": "bogus"}).status_code == 422
    r = client.post("/arbiter/register", json={"engine": "gpt-oss-120b", "total_bytes": 70_000_000_000})
    assert r.status_code == 200 and harness["arbiter"].measured["gpt-oss-120b"] == 70_000_000_000
    assert client.post("/arbiter/register", json={"engine": "nope", "total_bytes": 1}).status_code == 404
    st = client.get("/arbiter/status").json()
    assert st["budget_bytes"] == 170 * 1024**3 and st["resident"] == [] and st["halted"] is None
