"""V18's memory half (Sections 10.5, 11): a vault-tagged session's write never reaches a collection or the graph;
plus the memory rules around it: the graph's per-hemisphere binding, the AEGIS freeze/spool/thaw path (9.5) with the
aegis task's queue discipline, the vault passphrase backoff, and the chromadb client's telemetry opt-out."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from atlas import vault as vault_mod
from atlas.memory import (
    HemisphereViolation,
    LightRAGStore,
    MemoryFrozen,
    MemoryStore,
    MemoryStoreError,
    StubChroma,
    build_memory_store,
    check_chroma_proxy,
    stub_embedding_fn,
)
from atlas.tasks import aegis
from atlas.vault import SessionTags, VaultController, is_mounted_here, vault_session_test
from stubs import FakeVaultRunner


class FakeRag:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, list[str]]] = []
        self.deleted: list[str] = []
        self.loops: set[int] = set()
        self.finalized = False

    async def ainsert(self, text: str, ids: list[str] | None = None) -> None:
        self.loops.add(id(asyncio.get_running_loop()))
        self.inserted.append((text, ids or []))

    async def aquery(self, q: str, param: Any = None) -> str:
        self.loops.add(id(asyncio.get_running_loop()))
        return f"graph answer to {q}"

    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    async def finalize_storages(self) -> None:
        self.finalized = True


class GraphDoubles:
    """One FakeRag per hemisphere, the way LightRAGStore builds its instances (workspace per hemisphere)."""

    def __init__(self) -> None:
        self.rags: dict[str, FakeRag] = {}

    def __call__(self, hemisphere: str) -> FakeRag:
        return self.rags.setdefault(hemisphere, FakeRag())

    @property
    def inserted(self) -> list[tuple[str, list[str]]]:
        return [row for rag in self.rags.values() for row in rag.inserted]


def make_store(tmp_path: Path) -> tuple[MemoryStore, StubChroma, GraphDoubles, SessionTags]:
    sessions = SessionTags(tmp_path / "sessions.json")
    chroma = StubChroma()
    rags = GraphDoubles()
    graph = LightRAGStore(
        tmp_path / "graph",
        llm_base_url="http://127.0.0.1:8800/internal/v1",
        llm_model="router-qwen3.5-4b",
        embedding_url="http://127.0.0.1:8109/v1",
        embedding_model="embed-bge-m3",
        embedding_dim=8,
        sessions=sessions,
        freeze_flag=None,
        rag_factory=rags,
    )
    store = MemoryStore(chroma, stub_embedding_fn, sessions=sessions, freeze_flag=None, graph=graph)
    return store, chroma, rags, sessions


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


def _frozen_store(tmp_path: Path, flag: Path, spool: Path | None) -> MemoryStore:
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    def sleep(s: float) -> None:
        clock["t"] += s

    return MemoryStore(
        StubChroma(),
        stub_embedding_fn,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=5.0,
        spool_dir=spool,
        clock=now,
        sleep=sleep,
    )


def test_aegis_freeze_flag_blocks_writes_without_a_spool(tmp_path: Path) -> None:
    flag = tmp_path / "aegis-freeze"
    flag.write_text("1")
    store = _frozen_store(tmp_path, flag, None)
    with pytest.raises(MemoryFrozen):
        store.write("estate", ["x"], hemisphere="estate")
    flag.unlink()
    assert store.write("estate", ["x"], hemisphere="estate").written


def test_aegis_freeze_spools_writes_and_thaw_replays_them(tmp_path: Path) -> None:
    """9.5 'then writes resume': a write refused by the freeze is spooled, nothing is lost, the thaw commits it."""
    flag = tmp_path / "aegis-freeze"
    flag.write_text("1")
    spool = tmp_path / "spool"
    store = _frozen_store(tmp_path, flag, spool)
    wr = store.write("estate", ["a turn during the backup"], [{"kind": "chat-turn"}], hemisphere="estate")
    assert wr.spooled and not wr.written and "spooled" in wr.reason
    assert store.delete("estate", ["old-1"], hemisphere="estate") == 0  # spooled too
    assert store.count("estate") == 0 and store.spooled_count() == 2
    lines = [json.loads(ln) for f in spool.glob("spool-*.jsonl") for ln in f.read_text().splitlines()]
    assert [ln["op"] for ln in lines] == ["write", "delete"] and lines[0]["metadatas"][0]["kind"] == "chat-turn"
    # Still frozen: a replay is refused, never half-done.
    with pytest.raises(MemoryFrozen):
        store.replay_spool()
    # The aegis thaw task: flag down, consumers back, then the spool replayed through the store it was given.
    control = FakeCeleryControl()
    out = aegis.thaw(control=control, env={"AEGIS_FREEZE_FLAG": str(flag)}, memory=store)
    assert out["was_frozen"] and not flag.exists()
    assert out["spool"]["replayed"] == 2 and out["spool"]["failed"] == 0
    assert store.count("estate") == 1 and store.spooled_count() == 0 and not list(spool.glob("*.replaying"))
    assert store.write("estate", ["after the thaw"], hemisphere="estate").written


class FakeCeleryControl:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.added: list[str] = []

    def cancel_consumer(self, queue: str, **kw: Any) -> list[dict[str, str]]:
        self.cancelled.append(queue)
        return [{"cpu@host": "ok"}]

    def add_consumer(self, queue: str, **kw: Any) -> list[dict[str, str]]:
        self.added.append(queue)
        return [{"cpu@host": "ok"}]


def test_aegis_freeze_never_cancels_the_queue_that_carries_the_thaw(tmp_path: Path) -> None:
    """Blocker of the fix round: the thaw is a Celery task on `cpu`; cancelling that consumer left the flag up
    forever. freeze() pauses gpu only and refuses to pause cpu whatever the caller asks."""
    flag = tmp_path / "aegis-freeze"
    control = FakeCeleryControl()
    out = aegis.freeze(control=control, settle_s=0, env={"AEGIS_FREEZE_FLAG": str(flag)})
    assert flag.exists() and out["paused"] == {"gpu": [{"cpu@host": "ok"}]}
    assert control.cancelled == ["gpu"] and aegis.THAW_QUEUE not in control.cancelled
    assert aegis.THAW_QUEUE == "cpu" and "cpu" not in aegis.FREEZE_QUEUES
    with pytest.raises(ValueError, match="never cancel"):
        aegis.freeze(control=control, queues=("cpu", "gpu"), settle_s=0, env={"AEGIS_FREEZE_FLAG": str(flag)})
    # The thaw is deliverable (its queue was never cancelled) and re-adds both consumers.
    out = aegis.thaw(control=control, env={"AEGIS_FREEZE_FLAG": str(flag)})
    assert not flag.exists() and control.added == ["cpu", "gpu"] and out["spool"]["skipped"] == "no memory store"


def test_restic_backup_command_excludes_the_vault_plaintext_mount(tmp_path: Path) -> None:
    inc = tmp_path / "restic-include.txt"
    exc = tmp_path / "restic-exclude.txt"
    inc.write_text("/srv/atlas/data\n")
    exc.write_text("/srv/atlas/models\n")
    env = {
        "RESTIC_INCLUDE_FILE": str(inc),
        "RESTIC_EXCLUDE_FILE": str(exc),
        "VAULT_MOUNT_DIR": str(tmp_path / "vault" / "open"),
    }
    argv = aegis.restic_backup_command(env)
    assert argv[:2] == ["restic", "backup"] and "--one-file-system" in argv
    assert argv[argv.index("--exclude") + 1] == str(tmp_path / "vault" / "open")
    assert argv[argv.index("--exclude-file") + 1] == str(exc)
    # No exclude file: refused (an unfiltered include set is never backed up).
    with pytest.raises(FileNotFoundError, match="exclude"):
        aegis.restic_backup_command({**env, "RESTIC_EXCLUDE_FILE": str(tmp_path / "missing")})


def test_graph_is_bound_per_hemisphere_on_one_loop(tmp_path: Path) -> None:
    """10.1 for the graph layer: an estate insert never reaches the corporate instance; every coroutine of a store
    runs on the same long-lived loop (LightRAG's shared locks bind to the loop of their first acquire)."""
    store, _chroma, rags, _sessions = make_store(tmp_path)
    graph = store.graph
    assert graph is not None
    assert graph.insert("the will names the trustees", doc_id="e1", hemisphere="estate").written
    assert graph.insert("Q3 bid numbers", doc_id="c1", hemisphere="corporate").written
    assert graph.insert("the trust deed", doc_id="e2", hemisphere="estate").written
    assert [i for _, ids in rags.rags["estate"].inserted for i in ids] == ["e1", "e2"]
    assert [i for _, ids in rags.rags["corporate"].inserted for i in ids] == ["c1"]
    assert graph.query("who are the trustees?", hemisphere="corporate") == "graph answer to who are the trustees?"
    assert rags.rags["corporate"].inserted == [("Q3 bid numbers", ["c1"])]  # the query touched corporate only
    with pytest.raises(HemisphereViolation):
        graph.query("x", hemisphere="both")
    # The index knows the hemisphere, so a delete goes to the right instance.
    graph.delete_document("e1")
    assert rags.rags["estate"].deleted == ["e1"] and rags.rags["corporate"].deleted == []
    assert graph.hemisphere_of("e1") is None and graph.hemisphere_of("c1") == "corporate"
    loops = rags.rags["estate"].loops | rags.rags["corporate"].loops
    assert len(loops) == 1
    graph.close()
    assert all(r.finalized for r in rags.rags.values())


def test_build_memory_store_disables_chromadb_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule §7.1: the chromadb client is built with anonymized_telemetry=False (no PostHog beacon on start)."""
    seen: dict[str, Any] = {}

    class FakeSettings:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    def http_client(**kw: Any) -> StubChroma:
        seen.update(kw)
        return StubChroma()

    chromadb = types.ModuleType("chromadb")
    chromadb.HttpClient = http_client  # type: ignore[attr-defined]
    config = types.ModuleType("chromadb.config")
    config.Settings = FakeSettings  # type: ignore[attr-defined]
    chromadb.config = config  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)
    monkeypatch.setitem(sys.modules, "chromadb.config", config)
    store = build_memory_store(
        {"CHROMA_URL": "http://127.0.0.1:8000", "EMBEDDING_URL": "http://127.0.0.1:8109/v1"},
        sessions=SessionTags(),
        with_graph=False,
    )
    assert isinstance(store, MemoryStore)
    assert (seen["host"], seen["port"], seen["ssl"]) == ("127.0.0.1", 8000, False)
    assert seen["settings"].kw == {"anonymized_telemetry": False}
    assert store.spool_dir is not None  # production stores always spool during a freeze (9.5)


def test_chroma_behind_the_proxy_fails_fast() -> None:
    check_chroma_proxy("http://127.0.0.1:8000", {"HTTPS_PROXY": "http://127.0.0.1:3128"})
    check_chroma_proxy("http://172.18.0.2:8000", {})
    check_chroma_proxy("http://172.18.0.2:8000", {"HTTPS_PROXY": "http://x:3128", "NO_PROXY": "127.0.0.1,172.18.0.2"})
    with pytest.raises(MemoryStoreError, match="NO_PROXY"):
        check_chroma_proxy(
            "http://172.18.0.2:8000", {"HTTPS_PROXY": "http://x:3128", "NO_PROXY": "127.0.0.1,172.16.0.0/12"}
        )


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


def test_passphrase_guessing_backs_off_and_notifies(tmp_path: Path) -> None:
    slept: list[float] = []
    notices: list[str] = []
    runner = FakeVaultRunner(refuse=True)
    ctl = VaultController(
        helper="/usr/local/bin/atlas-vault",
        mount_dir=str(tmp_path / "open"),
        runner=runner,
        sudo=False,
        sleep=slept.append,
        notify=lambda msg, **kw: notices.append(msg),
    )
    for _ in range(6):
        assert not ctl.open("wrong").ok
    assert ctl.refusals == 6
    # No delay for the first three attempts, then 2, 4, 8 s (2^(n-2), capped at 60).
    assert slept == [2.0, 4.0, 8.0]
    assert len(notices) == 1 and "refused 5 times" in notices[0]
    for _ in range(10):
        ctl.open("wrong")
    assert max(slept) == 60.0
    # A lock resets the counter; so does a successful open.
    ctl.lock()
    assert ctl.refusals == 0 and ctl.backoff_s() == 0.0


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
    # One signature, the one admin.py calls: vault_session_test(idle_seconds, file); no file -> exit 2, one JSON line.
    assert vault_session_test(5, None) == 2
    rc = vault_session_test(5, str(secret))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(out)
    assert rc == 0
    assert data["read"] is True and data["chars"] > 0
    assert data["vault_tagged"] is True
    assert data["memory_write_attempted"] is True and data["memory_write_suppressed"] is True
    assert chroma.collections == {} and rag.inserted == []
