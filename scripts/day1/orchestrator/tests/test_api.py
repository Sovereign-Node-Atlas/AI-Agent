"""The orchestrator API with TestClient: /v1/models lists three; a chat completion streams through the REAL router
(stub classifier), the REAL prompt builder, the REAL Arbiter over stub controller/probe and a StubLlama; the approval
flow runs the REAL ApprovalQueue over StubSender; vault, strike and arbiter endpoints; the admin token and the
loopback rule; the never-delegate pass; the Phase 3 load-test contract (/arbiter/load, engine-keyed generation on
/internal only); the one generation slot shared by resident and weight-bearing models; the hemisphere scope of scars;
Open WebUI task calls; Deep Think on the gpu queue. No live service."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas import vault as vault_mod
from atlas.api import (
    ADMIN_TOKEN_HEADER,
    CHAT_TURN_TTL_HOURS,
    AppDeps,
    NoChannelSender,
    build_app,
    history_has_vault_prefix,
    is_owui_task_call,
)
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
    "[DEEP THINK:STANDARD]": "deep-think:standard",
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
    enqueued: list[tuple[str, dict[str, Any]]] = []

    def enqueue(name: str, kw: dict[str, Any]) -> str:
        enqueued.append((name, kw))
        return "celery-task-1"

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
        enqueue=enqueue,
        trusted_hosts=frozenset({"127.0.0.1", "::1", "testclient"}),  # TestClient's client host
    )
    app = build_app(deps)
    return {
        "client": TestClient(app),
        "deps": deps,
        "enqueued": enqueued,
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
    assert _chat(client, "x", model="gpt-4").json()["detail"].startswith("model 'gpt-4' is not served")
    # An engine key is NOT served on the public route (fix round 2): /internal, loopback only, is the Phase 3 path.
    r = _chat(client, "x", model="gpt-oss-120b")
    assert r.status_code == 404 and "/internal/v1/chat/completions" in r.json()["detail"]
    assert harness["arbiter"].resident == {} and harness["llama"].made == []


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
    # The exchange was remembered in the corporate collection (not vault-tagged), temporal with D9's 90-day life.
    rows = harness["chroma"].collections["corporate"].rows
    assert len(rows) == 1
    (_doc, meta, _vec) = next(iter(rows.values()))
    assert meta["kind"] == "chat-turn" and meta["temporal"] is True
    assert abs((meta["expires_at"] - meta["ts"]) - CHAT_TURN_TTL_HOURS * 3600) < 5 and CHAT_TURN_TTL_HOURS == 24 * 90


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
    assert r.json()["atlas"]["override"] == "[ARTHUR]" and r.json()["atlas"]["forced_lead"] == "arthur"
    r = _chat(client, "hi", model="ren")
    assert r.json()["atlas"]["persona"] == "ren" and r.json()["atlas"]["override"] == "[REN]"
    # The engine never sees the forcing prefix.
    assert harness["llama"].made[-1].requests[0]["messages"][-1]["content"] == "hi"
    # "atlas" leaves it to the router: a medical message lands on Arthur although Eleanor said Ren (7.2 rule 1, V16).
    r = _chat(client, "medical results to review")
    assert r.json()["atlas"]["persona"] == "arthur" and r.json()["atlas"]["hard_keyword_hit"] == "medical"
    assert "hard-rule:medical" in r.json()["atlas"]["reason"]
    assert r.json()["atlas"]["forced_lead"] is None


def test_model_picker_ren_cannot_bypass_the_privacy_membrane(harness: dict[str, Any]) -> None:
    """7.2 rule 1 over the UI model picker: `model=ren` + a hard keyword goes to Arthur, the Principal is told why,
    and the hit is LOGGED (the routing decision) but is no 9.4 strike (fix round 2: the hard rule doing its job is not
    an error and would only swell the scar pile)."""
    client: TestClient = harness["client"]
    r = _chat(client, "my medical results came back", model="ren")
    assert r.status_code == 200, r.text
    head = r.json()["atlas"]
    assert head["persona"] == "arthur" and head["engine"] == "nemotron-3-super" and head["hemisphere"] == "estate"
    assert head["hard_keyword_hit"] == "medical" and head["forced_lead"] == "ren"
    assert "overruled-by-hard-rule:medical" in head["reason"]
    text = r.json()["choices"][0]["message"]["content"]
    assert text.startswith("[ATLAS] Routed to Arthur") and "'medical'" in text and "Stub answer" in text
    assert harness["ledger"].list_strikes(task_id=head["task_id"]) == []
    decisions = harness["ledger"].list_routing_decisions(task_id=head["task_id"])
    assert decisions and any("overruled-by-hard-rule" in json.dumps(d) for d in decisions)
    assert "scars" not in harness["chroma"].collections or harness["chroma"].collections["scars"].count() == 0
    # The turn remembered under the ESTATE hemisphere (Arthur's), never corporate.
    cols = harness["chroma"].collections
    assert cols["estate"].count() == 1 and ("corporate" not in cols or cols["corporate"].count() == 0)


def test_vault_tagged_session_is_not_remembered(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = _chat(client, "the will says", model="arthur", atlas_session="chat-vault", atlas_vault=True)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"]
    assert harness["sessions"].is_vault("chat-vault")
    cols = harness["chroma"].collections
    assert "estate" not in cols or cols["estate"].count() == 0  # retrieval may create it; the write never lands
    assert harness["deps"].memory.dropped[-1]["session_id"] == "chat-vault"
    # The [VAULT] prefix from the filter does the same through the router's command, and the message itself is still
    # answered (fix round 2: the tag is the rule, not the loss of the first vault-tagged message).
    made_before = len(harness["llama"].made)
    r = _chat(client, "[VAULT] open the estate papers", atlas_session="chat-2", atlas_override="[VAULT]")
    text = r.json()["choices"][0]["message"]["content"]
    assert text.startswith("[ATLAS] This session is now vault-tagged") and "Stub answer" in text
    assert r.json()["atlas"]["command"] == "vault-session" and r.json()["atlas"]["persona"] == "arthur"
    assert harness["sessions"].is_vault("chat-2")
    assert len(harness["llama"].made) == made_before + 1
    assert harness["llama"].made[-1].requests[0]["messages"][-1]["content"] == "open the estate papers"
    assert harness["deps"].memory.dropped[-1]["session_id"] == "chat-2"  # tagged BEFORE the turn's memory write
    # An empty body gets the notice alone: nothing is dispatched.
    r = _chat(client, "[VAULT]", atlas_session="chat-3")
    assert "vault-tagged" in r.json()["choices"][0]["message"]["content"]
    assert len(harness["llama"].made) == made_before + 1 and harness["sessions"].is_vault("chat-3")


def test_vault_prefix_earlier_in_the_history_tags_the_session(harness: dict[str, Any]) -> None:
    """10.5 'for the life of that session' survives a filter/Open WebUI restart: the history carries [VAULT]."""
    client: TestClient = harness["client"]
    sessions: SessionTags = harness["sessions"]
    history = [
        {"role": "user", "content": "[VAULT] here is the trust deed"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "summarise the trustee powers"},
    ]
    assert history_has_vault_prefix(history)
    assert not sessions.is_vault("chat-restarted")
    r = client.post(
        "/v1/chat/completions", json={"model": "arthur", "messages": history, "atlas_session": "chat-restarted"}
    )
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"]
    assert sessions.is_vault("chat-restarted")
    cols = harness["chroma"].collections
    assert "estate" not in cols or cols["estate"].count() == 0
    assert harness["deps"].memory.dropped[-1]["session_id"] == "chat-restarted"
    # The X-OpenWebUI-Chat-Id header is a session channel too (ENABLE_FORWARD_USER_INFO_HEADERS).
    r = client.post(
        "/v1/chat/completions",
        json={"model": "arthur", "messages": [{"role": "user", "content": "[VAULT] x"}]},
        headers={"x-openwebui-chat-id": "owui-77"},
    )
    assert r.status_code == 200 and sessions.is_vault("owui-77")


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
    assert "Plan the bid." in strikes[0]["description"]
    assert harness["chroma"].collections["scars"].count() == 1
    assert harness["arbiter"].generating is None  # the lock was released on failure
    r = _chat(client, "x")
    assert r.status_code == 502 and r.json()["error"]["status"] == 502


def test_engine_failure_in_a_vault_session_leaks_nothing(harness: dict[str, Any]) -> None:
    """Blocker of the fix round (10.5): the automatic strike of a failed generation in a vault-tagged session writes
    no scar and withholds the Principal's text from the ledger (the ledger is in the restic include set)."""
    harness["llama"].fail = "llama-server HTTP 500: boom"
    client: TestClient = harness["client"]
    secret = "the will leaves the vineyard to Mara"
    r = _chat(client, secret, model="arthur", atlas_session="chat-vault-fail", atlas_vault=True)
    assert r.status_code == 502
    task_id = r.json()["error"]["task_id"]
    strikes = harness["ledger"].list_strikes(task_id=task_id)
    assert len(strikes) == 1 and strikes[0]["kind"] == "failed-generation"
    assert "vineyard" not in strikes[0]["description"] and "context withheld" in strikes[0]["description"]
    assert strikes[0]["scar_id"] is None
    cols = harness["chroma"].collections
    assert "scars" not in cols or cols["scars"].count() == 0
    assert "vineyard" not in json.dumps(harness["ledger"].list_strikes())


def test_log_strike_prefix_records_a_manual_strike_that_the_next_dispatch_sees(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = _chat(client, "[LOG STRIKE: wrong-tax-rate] used 30% not 25% on the AEC bid")
    assert r.status_code == 200 and "Strike #1" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "ouroboros-strike"
    strike = harness["ledger"].list_strikes()[0]
    assert strike["kind"] == "manual" and "wrong-tax-rate" in strike["description"]
    assert harness["chroma"].collections["scars"].count() == 1
    scar_meta = next(iter(harness["chroma"].collections["scars"].rows.values()))[1]
    assert scar_meta["persona"] == "ren" and scar_meta["source"] == "principal"  # tagged to the routed persona
    # 9.4 Injection: the scar is layer 4 of the NEXT dispatch's system prompt, whoever logged it (no persona filter).
    r = _chat(client, "tax rate on the AEC bid, again")
    assert r.status_code == 200
    system = harness["llama"].made[-1].requests[0]["messages"][0]["content"]
    assert "wrong-tax-rate" in system and "used 30% not 25%" in system


def test_execute_aegis_backup_prefix_enqueues_the_manual_trigger(harness: dict[str, Any]) -> None:
    r = _chat(harness["client"], "[EXECUTE AEGIS BACKUP]")
    assert "celery-task-1" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["atlas"]["command"] == "aegis"
    # Redis down (kombu OperationalError): a plain reply and a ledger note, never a 500 or a broken stream.

    def boom(name: str, kw: dict[str, Any]) -> str:
        raise ConnectionError("Error 111 connecting to 127.0.0.1:6379")

    harness["deps"].enqueue = boom
    r = _chat(harness["client"], "[EXECUTE AEGIS BACKUP]")
    assert r.status_code == 200 and "could not be enqueued" in r.json()["choices"][0]["message"]["content"]
    assert harness["ledger"].get_task(r.json()["atlas"]["task_id"])["status"] == "done"


def test_never_delegate_pass_rewrites_the_stream(harness: dict[str, Any]) -> None:
    """16.1 rule 5 as a code path between synthesis and output: the Principal never reads a task-shaped request."""
    harness["llama"].text = "The lease is ready. Please send me the signed copy. Can you find the Q3 bank statements?"
    client: TestClient = harness["client"]
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "atlas", "stream": True, "messages": [{"role": "user", "content": "Lease status?"}]},
    ) as r:
        body = "".join(r.iter_text())
    chunks = _sse_chunks(body)
    text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)
    assert text.startswith("The lease is ready. Decision needed: shall ATLAS send you the signed copy?")
    assert "Please send me" not in text and "Can you find" not in text
    assert text.endswith("Decision needed: shall ATLAS find the Q3 bank statements?")
    row = harness["ledger"].get_task(chunks[0]["atlas"]["task_id"])
    gov = json.loads(row["result_json"])["governance"]
    assert gov["register"] == "principal" and gov["rewrites"] == 2 and gov["flagged"] == []
    # Non-stream: the same text; the memory holds the rewritten turn.
    r = _chat(client, "Lease status?", stream=False)
    assert r.json()["choices"][0]["message"]["content"] == text
    docs = [row[0] for row in harness["chroma"].collections["corporate"].rows.values()]
    assert all("Please send me" not in d for d in docs) and any("Decision needed" in d for d in docs)


def test_engine_key_model_runs_the_internal_pipeline_and_queues(harness: dict[str, Any]) -> None:
    """Phase 3 contract (phase3/loadtest.py ORCH_CHAT_PATH): `model: <engine key>` on /internal/v1/chat/completions
    goes through the Arbiter's load and the generation slot, no router, no memory write."""
    client: TestClient = harness["client"]
    r = client.post(
        "/internal/v1/chat/completions",
        json={
            "model": "gpt-oss-120b",
            "messages": [{"role": "user", "content": "Write a 250-word essay about lighthouses."}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Stub answer from the engine."
    assert r.json()["atlas"]["engine"] == "gpt-oss-120b" and "timings" in r.json()
    assert "gpt-oss-120b" in harness["arbiter"].resident and harness["arbiter"].generating is None
    row = harness["ledger"].get_task(r.json()["atlas"]["task_id"])
    assert row["kind"] == "internal-generate" and row["status"] == "done" and row["engine"] == "gpt-oss-120b"
    assert not harness["ledger"].list_routing_decisions(task_id=row["id"])  # no router on the engine path
    assert "corporate" not in harness["chroma"].collections  # nothing remembered
    assert [m["id"] for m in client.get("/v1/models").json()["data"]] == ["atlas", "ren", "arthur"]


def test_arbiter_load_unload_and_remeasure_routes(harness: dict[str, Any]) -> None:
    client: TestClient = harness["client"]
    r = client.post("/arbiter/load", json={"engine": "gpt-oss-120b", "ctx": 32768, "parallel": 1, "task_id": "p3"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "granted" and r.json()["granted"] and r.json()["projected_bytes"] > 0
    assert "gpt-oss-120b" in harness["arbiter"].resident
    assert client.post("/arbiter/load", json={"engine": "nope"}).status_code == 404
    # Resident: re-measuring is refused (4.1: the resident set is what is there with NO engine loaded).
    assert client.post("/arbiter/remeasure").status_code == 409
    r = client.post("/arbiter/unload", json={"engine": "gpt-oss-120b", "task_id": "p3"})
    assert r.status_code == 200 and r.json()["decision"] == "granted"
    assert harness["arbiter"].resident == {}
    assert client.post("/arbiter/unload", json={"engine": "gpt-oss-120b"}).json()["decision"] == "granted"
    # Nothing resident: the resident set is re-read from the counter (the small models came up after start).
    budget_before = harness["arbiter"].budget_bytes
    harness["arbiter"].probe.used_bytes += 7 * 1024**3
    r = client.post("/arbiter/remeasure")
    assert r.status_code == 200 and r.json()["budget_bytes"] == budget_before - 7 * 1024**3
    # ... and lazily on the next load when nothing is resident.
    harness["arbiter"].probe.used_bytes += 1024**3
    client.post("/arbiter/load", json={"engine": "gpt-oss-120b"})
    assert harness["arbiter"].budget_bytes == budget_before - 8 * 1024**3


def test_admin_token_guards_every_route_but_health_and_v1(harness: dict[str, Any]) -> None:
    deps: AppDeps = harness["deps"]
    deps.admin_token = "s3cret-token"
    deps.trusted_hosts = frozenset({"127.0.0.1"})  # TestClient is not loopback now
    client = TestClient(build_app(deps))
    assert client.get("/health").status_code == 200 and client.get("/v1/models").status_code == 200
    # Off loopback in token mode the chat path needs the token too (fix round 2), as X-Atlas-Token or as the
    # Authorization: Bearer header Open WebUI sends OPENAI_API_KEY in.
    assert _chat(client, "hello").status_code == 401
    body = {"model": "atlas", "messages": [{"role": "user", "content": "hello"}]}
    assert client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer nope"}).status_code == 403
    assert (
        client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer s3cret-token"}).status_code
        == 200
    )
    assert (
        client.post("/v1/chat/completions", json=body, headers={ADMIN_TOKEN_HEADER: "s3cret-token"}).status_code == 200
    )
    # The passphrase never crosses a wire in the clear: off loopback /vault/open needs TLS even with the token.
    r = client.post("/vault/open", json={"passphrase": "x"}, headers={ADMIN_TOKEN_HEADER: "s3cret-token"})
    assert r.status_code == 403 and "TLS" in r.json()["detail"]
    tls = TestClient(build_app(deps), base_url="https://testserver")
    r = tls.post("/vault/open", json={"passphrase": "hunter2"}, headers={ADMIN_TOKEN_HEADER: "s3cret-token"})
    assert r.status_code == 200 and r.json()["state"] == "open"
    for method, path in (
        ("GET", "/vault/status"),
        ("POST", "/vault/lock"),
        ("GET", "/approvals"),
        ("GET", "/arbiter/status"),
        ("POST", "/arbiter/remeasure"),
    ):
        r = client.request(method, path)
        assert r.status_code == 401, (path, r.text)
        assert client.request(method, path, headers={ADMIN_TOKEN_HEADER: "wrong"}).status_code == 403
    assert client.get("/vault/status", headers={ADMIN_TOKEN_HEADER: "s3cret-token"}).status_code == 200
    assert client.get("/arbiter/status", headers={ADMIN_TOKEN_HEADER: "s3cret-token"}).status_code == 200
    # /internal/* is loopback-only whatever the token says.
    r = client.post("/internal/route", json={"message": "x"}, headers={ADMIN_TOKEN_HEADER: "s3cret-token"})
    assert r.status_code == 403 and "loopback" in r.json()["detail"]
    # Without a token: loopback only.
    deps.admin_token = None
    client = TestClient(build_app(deps))
    assert client.get("/vault/status").status_code == 403
    deps.trusted_hosts = frozenset({"testclient"})
    client = TestClient(build_app(deps))
    assert (
        client.get("/vault/status").status_code == 200
        and client.post("/internal/route", json={"message": "x"}).status_code == 200
    )


def test_internal_generation_gets_a_child_ledger_row(harness: dict[str, Any]) -> None:
    """A Celery task's own id is the PARENT: the generation made on its behalf must not mark it done or failed."""
    client: TestClient = harness["client"]
    ledger: Ledger = harness["ledger"]
    ledger.insert_task("sentinel-bluf", task_id="celery-parent-1", status="running", queue="gpu")
    r = client.post(
        "/internal/v1/chat/completions",
        json={
            "model": "router-qwen3.5-4b",
            "messages": [{"role": "user", "content": "BLUF this"}],
            "atlas_task_id": "celery-parent-1",
        },
    )
    assert r.status_code == 200
    child = r.json()["atlas"]["task_id"]
    assert child != "celery-parent-1" and r.json()["atlas"]["parent_task_id"] == "celery-parent-1"
    assert ledger.get_task("celery-parent-1")["status"] == "running"
    row = ledger.get_task(child)
    assert (
        row["parent_task_id"] == "celery-parent-1" and row["status"] == "done" and row["engine"] == "router-qwen3.5-4b"
    )


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
    # quick: generator + adversary + refine on ONE engine, several calls, no second engine loaded, nothing enqueued.
    engines = {s.spec.key for s in harness["llama"].made}
    assert engines == {"gpt-oss-120b"} and len(harness["llama"].made) >= 4
    assert harness["enqueued"] == []


def test_deep_think_standard_goes_to_the_gpu_queue_and_the_interface_returns(harness: dict[str, Any]) -> None:
    """9.7: long work runs under Celery and "the chat interface returns to the Principal immediately"; 9.1: nothing is
    hard-capped. Standard/deep are enqueued as atlas.tasks.deep_think; the reply names the task and where the answer
    lands. A vault-tagged session runs inline instead (deep_think_task refuses it, 10.5)."""
    client: TestClient = harness["client"]
    r = _chat(client, "[DEEP THINK:STANDARD] best exit structure?", atlas_session="dt-1")
    assert r.status_code == 200, r.text
    head = r.json()["atlas"]
    assert head["command"] == "deep-think" and head["deep_think"] == "standard"
    assert head["deep_think_task_id"] == "celery-task-1"
    text = r.json()["choices"][0]["message"]["content"]
    assert "celery-task-1" in text and "ledger" in text
    assert harness["llama"].made == []  # nothing generated inline
    name, kw = harness["enqueued"][-1]
    assert name == "atlas.tasks.deep_think"
    assert kw["tier"] == "standard" and kw["problem"] == "best exit structure?" and kw["session_id"] == "dt-1"
    assert kw["parent_task_id"] == head["task_id"] and kw["audience"] == "principal"
    row = harness["ledger"].get_task(head["task_id"])
    assert row["status"] == "done" and json.loads(row["result_json"])["celery_task_id"] == "celery-task-1"
    # Vault-tagged: inline, with the engines, nothing enqueued.
    r = _chat(client, "[DEEP THINK:STANDARD] the will", atlas_session="dt-vault", atlas_vault=True)
    assert r.status_code == 200, r.text
    assert "Stub answer" in r.json()["choices"][0]["message"]["content"] and len(harness["enqueued"]) == 1
    assert (
        harness["llama"].made
        and json.loads(harness["ledger"].get_task(r.json()["atlas"]["task_id"])["result_json"])["inline"]
    )


def test_resident_model_generation_holds_the_one_slot(harness: dict[str, Any]) -> None:
    """4.2 rule 3 ("exactly one may generate at any moment ... including background work"): a router-qwen3.5-4b
    generation through /internal holds the slot, so a gpt-oss chat that arrives meanwhile QUEUES behind it (9.7 C15)
    and starts only when the 4B stream has finished. Fix round 4: the chat's ROUTING (the classifier verdict, itself a
    generation) and its Arbiter LOAD both happen inside the slot, so while queued nothing of the chat has happened yet:
    no routing row, no engine loaded, no stream; and a second weight-bearing request on an EXCLUSIVE engine queued at
    the same time cannot evict the first one's engine before it generates: each stream is built with its engine
    resident."""
    client: TestClient = harness["client"]
    deps: AppDeps = harness["deps"]
    gate = threading.Event()
    harness["llama"].gates["router-qwen3.5-4b"] = gate
    results: dict[str, Any] = {}
    resident_at_stream: list[tuple[str, frozenset[str]]] = []
    inner = deps.streamer_for

    def recording_streamer(spec: Any) -> Any:
        resident_at_stream.append((spec.key, frozenset(harness["arbiter"].resident)))
        return inner(spec)

    deps.streamer_for = recording_streamer

    def small() -> None:
        results["small"] = client.post(
            "/internal/v1/chat/completions",
            json={"model": "router-qwen3.5-4b", "messages": [{"role": "user", "content": "BLUF this"}]},
        )

    def big() -> None:
        results["big"] = _chat(client, "Draft the AEC bid summary.", atlas_session="queued-chat")

    def apex() -> None:
        results["apex"] = client.post(
            "/internal/v1/chat/completions",
            json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "deep plan"}]},
        )

    t_small = threading.Thread(target=small)
    t_small.start()
    deadline = time.monotonic() + 5
    while not any(s.spec.key == "router-qwen3.5-4b" and s.started.is_set() for s in harness["llama"].made):
        assert time.monotonic() < deadline, "the 4B stream never started"
        time.sleep(0.02)
    assert deps.slot.holder is not None and deps.slot.holder[0] == "router-qwen3.5-4b"
    t_big = threading.Thread(target=big)
    t_big.start()
    deadline = time.monotonic() + 5
    while len(deps.slot.queue) < 1:
        assert time.monotonic() < deadline, "the gpt-oss request never queued for the slot"
        time.sleep(0.02)
    t_apex = threading.Thread(target=apex)
    t_apex.start()
    deadline = time.monotonic() + 5
    while len(deps.slot.queue) < 2:
        assert time.monotonic() < deadline, "the apex request never queued for the slot"
        time.sleep(0.02)
    # Queued = nothing happened yet: the classifier has not run (its verdict is a generation), no weight-bearing engine
    # is loaded (the load happens inside the slot), nothing streamed; the Arbiter's own lock is free.
    assert harness["arbiter"].resident == {}
    assert not any(s.spec.key in ("gpt-oss-120b", "deepseek-v4-flash") for s in harness["llama"].made)
    assert harness["arbiter"].generating is None  # the Arbiter's own lock is taken only inside the slot
    assert harness["ledger"].list_routing_decisions() == []
    gate.set()
    t_small.join(timeout=10)
    t_big.join(timeout=10)
    t_apex.join(timeout=10)
    assert results["small"].status_code == 200 and results["big"].status_code == 200, results["big"].text
    assert results["apex"].status_code == 200, results["apex"].text
    assert results["big"].json()["choices"][0]["message"]["content"] == "Stub answer from the engine."
    # Every stream was built with its own engine resident: the exclusive apex load and the gpt-oss load each happened
    # inside their own slot hold, so whichever went first had generated before the other's swap evicted it (the chat
    # releases the slot between its classifier hold and its generation hold, so either order is a valid FIFO).
    weight_bearing = [(k, r) for k, r in resident_at_stream if k != "router-qwen3.5-4b"]
    assert sorted(k for k, _ in weight_bearing) == ["deepseek-v4-flash", "gpt-oss-120b"]
    assert all(k in r for k, r in weight_bearing), resident_at_stream
    first = weight_bearing[0][0]
    assert ("stop", first) in harness["controller"].calls  # the second load swapped the first out, after its stream
    assert deps.slot.holder is None and deps.slot.queue == () and harness["arbiter"].generating is None
    assert client.get("/health").json()["generating"] is None


def test_internal_failure_without_a_declared_hemisphere_writes_no_scar(harness: dict[str, Any]) -> None:
    """Fix round 4 (7.3, 10.1): an /internal generation's prompt (a corporate document chunk here) must never become
    an estate scar that Arthur's next dispatch carries across the membrane. Undeclared hemisphere: ledger row with the
    prompt withheld, no scar. Declared: the scar is bound to THAT hemisphere. A bad declaration is 422."""
    harness["llama"].fail = "llama-server HTTP 500: boom"
    client: TestClient = harness["client"]
    marker = "Zyxquorb Holdings acquisition memo"
    r = client.post(
        "/internal/v1/chat/completions",
        json={"model": "router-qwen3.5-4b", "messages": [{"role": "user", "content": f"Extract entities: {marker}"}]},
    )
    assert r.status_code == 502
    task_id = r.json()["error"]["task_id"]
    strikes = harness["ledger"].list_strikes(task_id=task_id)
    assert len(strikes) == 1 and strikes[0]["kind"] == "failed-generation" and strikes[0]["scar_id"] is None
    assert "Zyxquorb" not in strikes[0]["description"] and "prompt withheld" in strikes[0]["description"]
    assert "scars" not in harness["chroma"].collections or harness["chroma"].collections["scars"].count() == 0
    # An estate dispatch sees no trace of it (the prompt's text is not in the system prompt).
    harness["llama"].fail = None
    r = _chat(client, "Zyxquorb Holdings and the family trust", model="arthur")
    assert r.status_code == 200 and r.json()["atlas"]["hemisphere"] == "estate"
    assert "Zyxquorb Holdings acquisition memo" not in harness["llama"].made[-1].requests[0]["messages"][0]["content"]
    # Declared corporate: the scar is written, bound to corporate, so only a corporate dispatch can retrieve it.
    harness["llama"].fail = "llama-server HTTP 500: boom"
    r = client.post(
        "/internal/v1/chat/completions",
        json={
            "model": "router-qwen3.5-4b",
            "messages": [{"role": "user", "content": f"Summarise: {marker}"}],
            "atlas_hemisphere": "corporate",
        },
    )
    assert r.status_code == 502
    strikes = harness["ledger"].list_strikes(task_id=r.json()["error"]["task_id"])
    assert len(strikes) == 1 and strikes[0]["scar_id"] and "Zyxquorb" in strikes[0]["description"]
    rows = harness["chroma"].collections["scars"].rows
    assert len(rows) == 1 and next(iter(rows.values()))[1]["hemisphere"] == "corporate"
    bad = client.post(
        "/internal/v1/chat/completions",
        json={"model": "router-qwen3.5-4b", "messages": [{"role": "user", "content": "x"}], "atlas_hemisphere": "ren"},
    )
    assert bad.status_code == 422 and "atlas_hemisphere" in bad.text
    # Deep Think enqueues carry the chat's hemisphere for the task's own /internal generations.
    harness["llama"].fail = None
    r = _chat(client, "[DEEP THINK:STANDARD] the family trust exit", atlas_session="dt-h")
    assert r.status_code == 200 and harness["enqueued"][-1][1]["hemisphere"] == "estate"


def test_internal_token_guards_internal_routes_when_configured(harness: dict[str, Any]) -> None:
    """/internal/* takes its own shared secret on top of loopback when ORCH_INTERNAL_TOKEN_FILE is configured (fix
    round 4): X-Atlas-Internal-Token or Authorization: Bearer (what LightRAG's OpenAI client sends)."""
    client: TestClient = harness["client"]
    deps: AppDeps = harness["deps"]
    deps.internal_token = "s3cret"
    body = {"model": "router-qwen3.5-4b", "messages": [{"role": "user", "content": "x"}], "atlas_hemisphere": "estate"}
    assert client.post("/internal/v1/chat/completions", json=body).status_code == 401
    assert client.post("/internal/route", json={"message": "x"}).status_code == 401
    url = "/internal/v1/chat/completions"
    assert client.post(url, json=body, headers={"X-Atlas-Internal-Token": "nope"}).status_code == 403
    assert client.post(url, json=body, headers={"X-Atlas-Internal-Token": "s3cret"}).status_code == 200
    bearer = {"Authorization": "Bearer s3cret"}
    assert client.post("/internal/route", json={"message": "x"}, headers=bearer).status_code == 200
    assert _chat(client, "hello").status_code == 200  # the public route is untouched by the internal secret
    deps.internal_token = None
    assert client.post("/internal/route", json={"message": "x"}).status_code == 200


def test_estate_scar_context_never_reaches_a_corporate_prompt(harness: dict[str, Any]) -> None:
    """9.4 scars are scoped to the dispatch's hemisphere (7.3, 10.1): a strike logged in an estate session carries
    the Principal's words as context; the next corporate dispatch must not see them, the next estate one does."""
    client: TestClient = harness["client"]
    r = _chat(client, "[LOG STRIKE: trustee-names] the trust deed names Mara as trustee", model="arthur")
    assert r.status_code == 200 and "Strike #1" in r.json()["choices"][0]["message"]["content"]
    scar_meta = next(iter(harness["chroma"].collections["scars"].rows.values()))[1]
    assert scar_meta["hemisphere"] == "estate" and scar_meta["persona"] == "arthur"
    r = _chat(client, "the trust deed names the trustee, again", model="arthur")
    assert r.json()["atlas"]["hemisphere"] == "estate"
    estate_system = harness["llama"].made[-1].requests[0]["messages"][0]["content"]
    assert "trustee-names" in estate_system and "Mara" in estate_system
    r = _chat(client, "the trust deed names the trustee, again, for the AEC bid")
    assert r.json()["atlas"]["hemisphere"] == "estate"  # 'trust' is a hard keyword: still Arthur
    r = _chat(client, "Draft the AEC bid summary, names the Mara trustee deed", model="ren")
    # 'trust' is not in this text; the picker holds, the dispatch is corporate.
    assert r.json()["atlas"]["hemisphere"] == "corporate", r.json()["atlas"]
    corporate_system = harness["llama"].made[-1].requests[0]["messages"][0]["content"]
    assert "trustee-names" not in corporate_system and "Mara as trustee" not in corporate_system


def test_openwebui_task_calls_are_chores_not_principal_turns(harness: dict[str, Any]) -> None:
    """Open WebUI 0.11.4 title/tags/follow-up generation: `### Task:` + the embedded history. Routed engine, but no
    retrieval, no never-delegate rewrite, no memory write, no strike on failure; and [VAULT] anywhere in the embedded
    history tags the session (10.5 fail-closed)."""
    client: TestClient = harness["client"]
    harness["llama"].text = "Please send me the title. Vault Papers"
    task = (
        "### Task:\nGenerate a concise, 3-5 word title with an emoji summarizing the chat history.\n"
        "### Chat History:\n<chat_history>\nUSER: [VAULT] the will says the vineyard goes to Mara\n"
        "ASSISTANT: Noted.\n</chat_history>"
    )
    assert is_owui_task_call([{"role": "user", "content": task}])
    assert history_has_vault_prefix([{"role": "user", "content": task}])
    r = client.post(
        "/v1/chat/completions",
        json={"model": "atlas", "messages": [{"role": "user", "content": task}]},
        headers={"x-openwebui-chat-id": "owui-task-chat"},
    )
    assert r.status_code == 200, r.text
    head = r.json()["atlas"]
    assert head["owui_task"] is True and head["engine"] in ("gpt-oss-120b", "nemotron-3-super")
    # No rewrite: the UI parses what comes back.
    assert r.json()["choices"][0]["message"]["content"] == "Please send me the title. Vault Papers"
    assert harness["sessions"].is_vault("owui-task-chat")
    cols = harness["chroma"].collections
    assert all(cols[c].count() == 0 for c in cols)  # nothing remembered, no scar
    row = harness["ledger"].get_task(head["task_id"])
    assert row["kind"] == "owui-task" and row["status"] == "done"
    assert "Lessons from earlier mistakes" not in harness["llama"].made[-1].requests[0]["messages"][0]["content"]
    # A failing chore is a failed ledger row, not a 9.4 strike.
    harness["llama"].fail = "llama-server HTTP 500: boom"
    r = client.post("/v1/chat/completions", json={"model": "atlas", "messages": [{"role": "user", "content": task}]})
    assert r.status_code == 502
    assert harness["ledger"].list_strikes() == []
    assert "scars" not in cols or cols["scars"].count() == 0


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
    # decided_by is required (fix round): the ledger records WHO decided, never an assumed "principal".
    assert client.post(f"/approvals/{held}/approve").status_code == 422
    assert client.post(f"/approvals/{held}/approve", json={"note": "go"}).status_code == 422
    r = client.post(f"/approvals/{held}/approve", json={"decided_by": "principal", "note": "go"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["dispatched"] is True
    row = ledger.get_approval(held)
    assert row["status"] == "sent" and "approved by principal" in row["note"]
    by = {"decided_by": "principal"}
    assert client.post(f"/approvals/{held}/approve", json=by).status_code == 409
    assert client.post(f"/approvals/{auto}/reject", json=by).status_code == 409
    assert client.post("/approvals/999/reject", json=by).status_code == 404
    sensitive = ledger.insert_approval(
        task_id="t3", tier="sensitive", kind="email", status="held", persona="gideon", recipient="x@y", draft="d"
    )
    # 16.2: a sensitive item needs the strong cross-check before the Principal can approve it.
    assert client.post(f"/approvals/{sensitive}/approve", json=by).status_code == 409
    r = client.post(f"/approvals/{sensitive}/reject", json={"decided_by": "principal", "note": "not now"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and "not now" in r.json()["note"]


def test_no_channel_sender_fails_the_send_loudly(harness: dict[str, Any]) -> None:
    """Production has no Section 13 channel yet: approving records the decision and the send fails, never pretends."""
    ledger: Ledger = harness["ledger"]
    harness["deps"].approval.sender = NoChannelSender()
    held = ledger.insert_approval(
        task_id="t9", tier="standard", kind="email", status="held", persona="silas", recipient="a@b", draft="d"
    )
    r = harness["client"].post(f"/approvals/{held}/approve", json={"decided_by": "principal"})
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
    # The Arbiter (another writer) accepts a bare unit-style key as a Phase 4 engine with a logged warning (its
    # register_measured docstring); only a malformed key is UnknownEngine -> 404.
    assert client.post("/arbiter/register", json={"engine": "no key!", "total_bytes": 1}).status_code == 404
    st = client.get("/arbiter/status").json()
    assert st["budget_bytes"] == 170 * 1024**3 and st["resident"] == [] and st["halted"] is None
