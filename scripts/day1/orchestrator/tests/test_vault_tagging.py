"""V18's memory half (Sections 10.5, 11): a vault-tagged session's write never reaches a collection or the graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atlas import vault as vault_mod
from atlas.memory import (
    HemisphereViolation,
    LightRAGStore,
    MemoryFrozen,
    MemoryStore,
    StubChroma,
    stub_embedding_fn,
)
from atlas.vault import SessionTags, VaultController, is_mounted_here, vault_session_test
from stubs import FakeVaultRunner


class FakeRag:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, list[str]]] = []
        self.deleted: list[str] = []

    async def ainsert(self, text: str, ids: list[str] | None = None) -> None:
        self.inserted.append((text, ids or []))

    async def aquery(self, q: str, param: Any = None) -> str:
        return f"graph answer to {q}"

    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.deleted.append(doc_id)


def make_store(tmp_path: Path) -> tuple[MemoryStore, StubChroma, FakeRag, SessionTags]:
    sessions = SessionTags(tmp_path / "sessions.json")
    chroma = StubChroma()
    rag = FakeRag()
    graph = LightRAGStore(
        tmp_path / "graph",
        llm_base_url="http://127.0.0.1:8800/internal/v1",
        llm_model="router-qwen3.5-4b",
        embedding_url="http://127.0.0.1:8109/v1",
        embedding_model="embed-bge-m3",
        embedding_dim=8,
        sessions=sessions,
        freeze_flag=None,
        rag_factory=lambda: rag,
    )
    store = MemoryStore(chroma, stub_embedding_fn, sessions=sessions, freeze_flag=None, graph=graph)
    return store, chroma, rag, sessions


def test_vault_tagged_session_write_is_dropped_everywhere(tmp_path: Path) -> None:
    store, chroma, rag, sessions = make_store(tmp_path)
    sessions.tag_vault("s-vault")
    marker = "ATLAS-V18-VAULT-MARKER vermilion lighthouse"
    for col, hemi in (
        ("estate", "estate"),
        ("corporate", "corporate"),
        ("documents_estate", "estate"),
        ("sentinel", "estate"),
        ("scars", "estate"),
    ):
        wr = store.write(col, [marker], [{"kind": "test"}], hemisphere=hemi, session_id="s-vault")
        assert wr.dropped and not wr.written and "vault" in wr.reason
    gr = store.graph.insert(marker, doc_id="d1", hemisphere="estate", session_id="s-vault")
    assert gr.dropped and not gr.written
    # Nothing reached any backend: the collections were never even created.
    assert chroma.collections == {}
    assert rag.inserted == []
    assert store.dropped and all(d["session_id"] == "s-vault" for d in store.dropped)
    # The same content from an untagged session is written: the rule is about the session, not the text.
    wr = store.write("estate", [marker], hemisphere="estate", session_id="s-plain")
    assert wr.written and chroma.collections["estate"].count() == 1
    assert store.query("estate", "lighthouse", k=1, hemisphere="estate")[0].document == marker


def test_remember_this_overrides_the_drop(tmp_path: Path) -> None:
    store, chroma, _rag, sessions = make_store(tmp_path)
    sessions.tag_vault("s-vault")
    wr = store.write("estate", ["keep me"], hemisphere="estate", session_id="s-vault", remember=True)
    assert wr.written and chroma.collections["estate"].count() == 1


def test_hemisphere_binding_raises_on_reads_and_writes(tmp_path: Path) -> None:
    store, _chroma, _rag, _sessions = make_store(tmp_path)
    with pytest.raises(HemisphereViolation):
        store.query("estate", "anything", hemisphere="corporate")
    with pytest.raises(HemisphereViolation):
        store.write("documents_corporate", ["x"], hemisphere="estate")
    # scars and sentinel are shared (9.4, 9.3); the hemisphere collections are not.
    store.check_access("scars", "corporate")
    store.check_access("sentinel", "estate")
    with pytest.raises(HemisphereViolation):
        store.graph.insert("x", doc_id="g", hemisphere="both")


def test_aegis_freeze_flag_blocks_writes(tmp_path: Path) -> None:
    flag = tmp_path / "aegis-freeze"
    flag.write_text("1")
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    def sleep(s: float) -> None:
        clock["t"] += s

    store = MemoryStore(
        StubChroma(),
        stub_embedding_fn,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=5.0,
        clock=now,
        sleep=sleep,
    )
    with pytest.raises(MemoryFrozen):
        store.write("estate", ["x"], hemisphere="estate")
    flag.unlink()
    assert store.write("estate", ["x"], hemisphere="estate").written


def test_session_tags_persist_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "run" / "vault-sessions.json"
    a = SessionTags(path)
    a.tag_vault("chat-1")
    b = SessionTags(path)  # another process (atlas-admin, a Celery worker)
    assert b.is_vault("chat-1") and not b.is_vault("chat-2")
    b.clear("chat-1")
    assert not SessionTags(path).is_vault("chat-1")


def test_is_mounted_here_reads_mountinfo_field_5(tmp_path: Path) -> None:
    mi = tmp_path / "mountinfo"
    mi.write_text(
        "36 35 0:31 / /srv/atlas/vault/open rw,nosuid - fuse.gocryptfs gocryptfs rw\n"
        "37 35 0:32 / /srv/atlas/with\\040space rw - ext4 /dev/x rw\n"
    )
    assert is_mounted_here("/srv/atlas/vault/open", mi)
    assert is_mounted_here("/srv/atlas/with space", mi)
    assert not is_mounted_here("/srv/atlas/vault", mi)


def test_controller_pipes_the_passphrase_on_stdin_and_never_logs_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeVaultRunner()
    mount = tmp_path / "open"
    mount.mkdir()
    ctl = VaultController(helper="/usr/local/bin/atlas-vault", mount_dir=str(mount), runner=runner, sudo=True)
    monkeypatch.setattr(vault_mod, "is_mounted_here", lambda mount_dir, mountinfo="x": runner.state == "open")
    with caplog.at_level("DEBUG"):
        res = ctl.open("correct horse battery staple")
    assert res.ok and res.state == "open"
    argv, stdin = runner.calls[0]
    assert argv == ["sudo", "-n", "/usr/local/bin/atlas-vault", "open"]
    assert stdin == "correct horse battery staple\n"
    assert "correct horse" not in caplog.text
    assert ctl.status().state == "open"
    assert ctl.lock().state == "locked"
    assert ctl.status().state == "locked"
    refused = VaultController(
        helper="/usr/local/bin/atlas-vault", mount_dir=str(mount), runner=FakeVaultRunner(refuse=True), sudo=False
    )
    r = refused.open("wrong")
    assert not r.ok and r.exit_code == 1


def test_vault_session_test_reports_a_suppressed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The atlas-admin vault-session-test path: reads the file, attempts the write, prints one JSON line, exit 0."""
    mount = tmp_path / "vault" / "open"
    mount.mkdir(parents=True)
    secret = mount / "note.txt"
    secret.write_text("Top secret note: ATLAS-V18-VAULT-MARKER vermilion lighthouse\n")
    monkeypatch.setenv("VAULT_MOUNT_DIR", str(mount))
    monkeypatch.setenv("VAULT_SESSION_FILE", str(tmp_path / "sessions.json"))
    store, chroma, rag, _ = make_store(tmp_path)

    def fake_build(env: Any = None, *, sessions: SessionTags | None = None, with_graph: bool = True) -> MemoryStore:
        store.sessions = sessions or store.sessions
        store.graph.sessions = store.sessions
        return store

    monkeypatch.setattr("atlas.memory.build_memory_store", fake_build)
    rc = vault_session_test(5, str(secret))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(out)
    assert rc == 0
    assert data["read"] is True and data["chars"] > 0
    assert data["vault_tagged"] is True
    assert data["memory_write_attempted"] is True and data["memory_write_suppressed"] is True
    assert chroma.collections == {} and rag.inserted == []
