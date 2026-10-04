"""V18's memory half (Sections 10.5, 11): a vault-tagged session's write never reaches a collection or the graph;
plus the memory rules around it: the graph's per-hemisphere binding, the AEGIS freeze/spool/thaw path (9.5) for both
layers with the aegis task's queue discipline and its sudo-free manual trigger, the vault session tags' durable store,
the vault passphrase backoff, the chromadb client's telemetry opt-out and explicit embeddings, the D9 rules in
retention and the prune, the Sentinel's firewall telemetry file and its estate-bound BLUF, the beat timezone."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from atlas import celery_app
from atlas import vault as vault_mod
from atlas.memory import (
    COLLECTION_HEMISPHERES,
    HemisphereViolation,
    LightRAGStore,
    MemoryFrozen,
    MemoryStore,
    MemoryStoreError,
    StubChroma,
    StubCollection,
    build_memory_store,
    check_chroma_proxy,
    stub_embedding_fn,
)
from atlas.tasks import aegis, prune, retention, sentinel
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
    # scars are shared (9.4); sentinel sits under Arthur (9.3, C12) and is estate-bound like every other collection
    # (10.1 "each collection is bound to a hemisphere"; fix round 2).
    store.check_access("scars", "corporate")
    store.check_access("sentinel", "estate")
    with pytest.raises(HemisphereViolation):
        store.check_access("sentinel", "corporate")
    assert COLLECTION_HEMISPHERES["sentinel"] == frozenset({"estate"})
    assert all(len(COLLECTION_HEMISPHERES[c]) == 1 for c in prune.SWEPT_COLLECTIONS)
    assert prune._hemisphere_for("sentinel") == "estate"
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


def test_aegis_freeze_pauses_both_queues_and_thaw_readds_them(tmp_path: Path) -> None:
    """9.5 Freeze row: "the orchestrator pauses the Celery queues", both of them (fix round 2), so no cpu task writes
    into the include set mid-snapshot. The thaw stays deliverable because atlas-aegis.service re-adds the consumers
    over celery's broadcast channel before enqueueing it (the unit's stated contract); thaw() re-adds both again."""
    flag = tmp_path / "aegis-freeze"
    control = FakeCeleryControl()
    out = aegis.freeze(control=control, settle_s=0, env={"AEGIS_FREEZE_FLAG": str(flag)})
    assert flag.exists() and set(out["paused"]) == {"cpu", "gpu"}
    assert sorted(control.cancelled) == ["cpu", "gpu"] and aegis.FREEZE_QUEUES == ("cpu", "gpu")
    assert aegis.THAW_QUEUE in aegis.THAW_QUEUES
    out = aegis.thaw(control=control, env={"AEGIS_FREEZE_FLAG": str(flag)})
    assert not flag.exists() and control.added == ["cpu", "gpu"] and out["spool"]["skipped"] == "no memory store"


def test_graph_writes_during_the_freeze_wait_then_spool_and_the_thaw_replays_them(tmp_path: Path) -> None:
    """9.5 "then writes resume" for the graph layer too (fix round 2): a LightRAG insert during the backup is not
    lost to an exception; it waits, spools beside the vector writes, and MemoryStore.replay_spool commits it."""
    flag = tmp_path / "aegis-freeze"
    flag.write_text("1")
    spool = tmp_path / "spool"
    clock = {"t": 0.0}
    rags = GraphDoubles()
    graph = LightRAGStore(
        tmp_path / "graph",
        llm_base_url="http://127.0.0.1:8800/internal/v1",
        llm_model="router-qwen3.5-4b",
        embedding_url="http://127.0.0.1:8109/v1",
        embedding_model="embed-bge-m3",
        embedding_dim=8,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=5.0,
        spool_dir=spool,
        rag_factory=rags,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    store = MemoryStore(
        StubChroma(),
        stub_embedding_fn,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=5.0,
        spool_dir=spool,
        graph=graph,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    wr = graph.insert("the trust deed", doc_id="g1", hemisphere="estate", metadata={"kind": "doc"}, temporal=True)
    assert wr.spooled and not wr.written and rags.inserted == [] and clock["t"] >= 5.0
    assert graph.delete_document("g0") is False  # spooled too
    assert store.spooled_count() == 2
    with pytest.raises(MemoryFrozen):
        store.replay_spool()
    flag.unlink()
    out = store.replay_spool()
    assert out["replayed"] == 1 and out["failed"] == 1  # g0 was never in the index: the delete fails loudly, kept
    assert rags.rags["estate"].inserted == [("the trust deed", ["g1"])]
    assert graph.hemisphere_of("g1") == "estate" and [r.doc_id for r in graph.index_rows(temporal_only=True)] == ["g1"]
    assert out["failed_files"] and "g0" in Path(out["failed_files"][0]).read_text()
    # Spool content is memory content: directory 0700, files 0600, the failed file too.
    assert stat.S_IMODE(spool.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in spool.iterdir())
    # No spool directory: the graph write still raises after the wait, never silently.
    graph.spool_dir = None
    flag.write_text("1")
    with pytest.raises(MemoryFrozen):
        graph.insert("x", doc_id="g2", hemisphere="estate")


def test_session_tags_survive_a_reboot_and_a_failed_mirror_write(tmp_path: Path) -> None:
    """10.5 "for the life of that session" (fix round 2): the tmpfs mirror is gone after a reboot; the durable store
    still holds the tag. A mirror write that fails keeps the in-process tag and is retried; a load merges, never
    replaces, so no other reader can untag a session by rewriting the file."""
    run_file = tmp_path / "run" / "vault-sessions.json"
    durable = tmp_path / "data" / "orchestrator" / "vault-sessions.json"
    a = SessionTags(run_file, durable)
    a.tag_vault("chat-flagged")
    assert stat.S_IMODE(durable.stat().st_mode) == 0o600 and stat.S_IMODE(run_file.stat().st_mode) == 0o600
    run_file.unlink()  # reboot: tmpfs is empty
    b = SessionTags(run_file, durable)  # the orchestrator starting again
    assert b.is_vault("chat-flagged") and run_file.is_file()  # the mirror is rebuilt from the durable store
    assert SessionTags(run_file).is_vault("chat-flagged")  # a worker that only knows the mirror sees it too
    # Mirror write failure: parent is a FILE, so mkdir fails; the tag holds and the durable store has it.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    c = SessionTags(blocked / "sessions.json", tmp_path / "durable2.json")
    c.tag_vault("chat-x")
    assert c.is_vault("chat-x") and SessionTags(None, tmp_path / "durable2.json").is_vault("chat-x")
    # Merge, not replace: a stale mirror rewritten by another process cannot drop an in-process tag.
    run_file.write_text(json.dumps({"vault": {}}))
    assert b.is_vault("chat-flagged")
    b.clear("chat-flagged")
    assert not SessionTags(run_file, durable).is_vault("chat-flagged")
    # from_env wires both files.
    t = SessionTags.from_env({"VAULT_SESSION_FILE": str(run_file), "VAULT_SESSION_STORE": str(durable)})
    assert t.path == run_file and t.durable == durable


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


class StubSystemctl:
    """Stands in for subprocess.run on `systemctl is-active`: a scripted sequence of states; records every argv so the
    test can prove nothing else (sudo least of all) was ever run."""

    def __init__(self, states: list[str]) -> None:
        self.states = list(states)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kw: Any) -> Any:
        import subprocess

        self.calls.append(list(argv))
        assert argv[:2] == ["systemctl", "is-active"], argv
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return subprocess.CompletedProcess(argv, 0 if state == "active" else 3, state + "\n", "")


def test_aegis_manual_trigger_creates_the_request_file_and_never_runs_sudo(tmp_path: Path) -> None:
    """Section 9.5 manual trigger WITHOUT sudo (fix round 4; CONVENTIONS.md §8, phase2/07-restic.sh's contract): the
    task creates /run/atlas/aegis-request (AEGIS_REQUEST_FILE) and confirms with `systemctl is-active`; no argv ever
    starts with sudo; a unit that never starts is a loud failure naming the re-arm command."""
    req = tmp_path / "run" / "atlas" / "aegis-request"
    runner = StubSystemctl(["inactive", "activating", "active"])
    out = aegis.trigger_unit(runner=runner, sleep=lambda s: None, env={"AEGIS_REQUEST_FILE": str(req)})
    assert req.is_file() and stat.S_IMODE(req.stat().st_mode) == 0o640
    assert out["started"] and out["state"] == "activating" and out["request_file"] == str(req) and out["sudo"] is False
    assert runner.calls and all(c[0] != "sudo" for c in runner.calls)
    assert all(c[:2] == ["systemctl", "is-active"] for c in runner.calls)
    # A planted symlink at the request path is never followed (O_NOFOLLOW).
    link = tmp_path / "run" / "atlas" / "planted"
    link.symlink_to(tmp_path / "victim")
    with pytest.raises(RuntimeError, match="cannot create the AEGIS request file"):
        aegis.trigger_unit(runner=runner, sleep=lambda s: None, env={"AEGIS_REQUEST_FILE": str(link)})
    assert not (tmp_path / "victim").exists()
    # The unit never comes up (StartLimitBurst hit): RuntimeError with the re-arm command, the request left for the
    # operator to see.
    req2 = tmp_path / "run" / "atlas" / "aegis-request-2"
    with pytest.raises(RuntimeError, match=r"reset-failed atlas-aegis\.service atlas-aegis-trigger\.path"):
        aegis.trigger_unit(
            runner=StubSystemctl(["failed"]), sleep=lambda s: None, confirm_s=0.0, env={"AEGIS_REQUEST_FILE": str(req2)}
        )
    assert req2.exists()
    # A tiny include set that finished inside the window: the ExecStartPre removed the request, the unit is inactive
    # again; that is a completed run, not a failure.
    req3 = tmp_path / "run" / "atlas" / "aegis-request-3"

    class Finisher(StubSystemctl):
        def __call__(self, argv: list[str], **kw: Any) -> Any:
            req3.unlink(missing_ok=True)
            return super().__call__(argv, **kw)

    out = aegis.trigger_unit(runner=Finisher(["inactive"]), sleep=lambda s: None, env={"AEGIS_REQUEST_FILE": str(req3)})
    assert out["state"] == "finished"
    # The module never builds a sudo command (phase2/07-restic.sh greps the installed file for exactly this).
    src = Path(aegis.__file__).read_text(encoding="utf-8")
    assert '["sudo"' not in src and "aegis-request" in src


def test_aegis_thaw_replays_graph_lines_with_a_graph_equipped_store(tmp_path: Path) -> None:
    """The production thaw must build its store WITH the graph layer (fix round 4): a graph-insert spooled during the
    freeze replays with failed == 0; the same spool against a graph-less store lands in .failed.jsonl, which is the bug
    the reviewers found."""
    flag = tmp_path / "aegis-freeze"
    spool = tmp_path / "spool"
    flag.write_text("1")
    clock = {"t": 0.0}
    rags = GraphDoubles()
    graph = LightRAGStore(
        tmp_path / "graph",
        llm_base_url="http://127.0.0.1:8800/internal/v1",
        llm_model="router-qwen3.5-4b",
        embedding_url="http://127.0.0.1:8109/v1",
        embedding_model="embed-bge-m3",
        embedding_dim=8,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=1.0,
        spool_dir=spool,
        rag_factory=rags,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    assert graph.insert("the trust deed", doc_id="g1", hemisphere="estate").spooled
    with_graph = MemoryStore(StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=flag, spool_dir=spool,
                             graph=graph)
    # The production builder is called with NO with_graph=False (the default keeps the graph).
    seen: list[dict[str, Any]] = []

    def builder(**kw: Any) -> MemoryStore:
        seen.append(kw)
        return with_graph

    store = aegis.thaw_memory_store(builder)
    assert store is with_graph and seen == [{}]
    out = aegis.thaw(control=FakeCeleryControl(), env={"AEGIS_FREEZE_FLAG": str(flag)}, memory=store)
    assert out["spool"]["replayed"] == 1 and out["spool"]["failed"] == 0
    assert rags.rags["estate"].inserted == [("the trust deed", ["g1"])]
    # Counter-example: the graph-less store cannot replay a graph line (what with_graph=False did on the node).
    flag.write_text("1")
    assert graph.insert("another", doc_id="g2", hemisphere="estate").spooled
    flag.unlink()
    no_graph = MemoryStore(StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=flag, spool_dir=spool)
    out = no_graph.replay_spool()
    assert out["failed"] == 1 and out["failed_files"]
    # A builder that raises (before Phase 2 step 4) gives None, said in the log, never an exception.
    def broken(**kw: Any) -> MemoryStore:
        raise MemoryStoreError("CHROMA_URL not set")

    assert aegis.thaw_memory_store(broken) is None


def test_restic_forget_line_matches_the_forget_unit() -> None:
    """Two typed copies of the D9 retention line must agree (CONVENTIONS.md §8): the package's and the unit's."""
    unit = Path(__file__).resolve().parents[2] / "systemd" / "atlas-aegis-forget.service"
    exec_start = next(ln for ln in unit.read_text(encoding="utf-8").splitlines() if ln.startswith("ExecStart="))
    assert exec_start.split("=", 1)[1].split()[1:] == aegis.restic_forget_command()[1:]
    assert aegis.restic_forget_command()[:4] == ["restic", "forget", "--keep-within", "1d"]


def test_sentinel_firewall_telemetry_comes_from_the_root_exporter_file(tmp_path: Path) -> None:
    """D6 'firewall' (fix round 4): read FIREWALL_TELEMETRY_FILE; missing, stale (ts older than 2 h) or a null count is
    'telemetry unreadable', never a healthy zero; a fresh file yields the counts."""
    f = tmp_path / "firewall.json"
    env = {"FIREWALL_TELEMETRY_FILE": str(f)}
    now = 1_700_000_000.0
    with pytest.raises(OSError, match="missing"):
        sentinel._read_firewall(env, now=now)
    fresh = {"source": "atlas-sentinel-telemetry", "ts": now - 60, "window_s": 3600, "ufw_block": 12,
             "docker_egress_denied": 3, "squid_denied": 7, "denied_per_hour": 22, "errors": []}
    f.write_text(json.dumps(fresh))
    v = sentinel._read_firewall(env, now=now)
    assert v == {"denied_per_hour": 22.0, "ufw_block": 12.0, "docker_egress_denied": 3.0, "squid_denied": 7.0,
                 "window_s": 3600.0}
    f.write_text(json.dumps({**fresh, "ts": now - 7201}))
    with pytest.raises(OSError, match="stale"):
        sentinel._read_firewall(env, now=now)
    f.write_text(json.dumps({**fresh, "squid_denied": None, "denied_per_hour": None,
                             "errors": ["/var/log/squid/access.log absent"]}))
    with pytest.raises(OSError, match=r"could not count.*access\.log absent"):
        sentinel._read_firewall(env, now=now)
    f.write_text("not json")
    with pytest.raises(OSError, match="not JSON"):
        sentinel._read_firewall(env, now=now)
    # Through read_telemetry: the id is listed unreadable, the reading says why, no anomaly is invented.
    src = [{"id": "firewall", "thresholds": {"denied_per_hour_max": 200}}]
    import os
    import time

    f.write_text(json.dumps({**fresh, "ts": time.time() - 60, "denied_per_hour": 500}))  # read_telemetry uses the clock

    os.environ["FIREWALL_TELEMETRY_FILE"] = str(f)
    try:
        readings, anomalies, unreadable = sentinel.read_telemetry(src)
    finally:
        del os.environ["FIREWALL_TELEMETRY_FILE"]
    assert unreadable == [] and readings["firewall"]["ok"] and [a.metric for a in anomalies] == ["denied_per_hour"]
    assert anomalies[0].owner == "alaric" and anomalies[0].value == 500.0
    # The 12-month window is 366 days, not 400.
    assert sentinel.SENTINEL_KEEP_DAYS == 366
    import inspect

    assert inspect.signature(sentinel.SentinelHistory.prune).parameters["keep_days"].default == 366
    assert inspect.signature(sentinel.prune_log_files).parameters["keep_days"].default == 366


def test_sentinel_bluf_is_estate_bound_whatever_the_feed_hemisphere_says() -> None:
    """10.1 / C12: the BLUF goes to the `sentinel` collection as estate even when every anomaly is a corporate-tagged
    market feed (Silas's division is a field, not a binding); memory.py and the feeds file cannot drift apart."""
    anomalies = [
        sentinel.Anomaly("asx200", "abs_pct_change_1d", -6.0, 5.0, "ASX200 -6%", "silas", "corporate", "asx").as_dict(),
        sentinel.Anomaly("coindesk-news", "keyword:hack", 1.0, 1.0, "exchange hack", "silas", "corporate", "crypto")
        .as_dict(),
    ]
    entry = sentinel.compose_bluf(anomalies, 1_700_000_000.0, "Markets fell. Impact: x. Evidence: y. Action: Silas.")
    assert entry["hemispheres"] == ["corporate"] and entry["owners"] == ["silas"] and entry["under"] == "arthur"
    store = MemoryStore(StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=None)
    wr = sentinel.bluf_memory_write(store, entry, anomalies, 1_700_000_000.0)
    assert wr.written and store.count("sentinel") == 1
    hit = store.get("sentinel", hemisphere="estate", ids=list(wr.ids))[0]
    assert hit.metadata["hemisphere"] == "estate" and hit.metadata["owner_hemispheres"] == "corporate"
    assert hit.metadata["expires_at"] - hit.metadata["ts"] == pytest.approx(366 * 86400, rel=1e-6)
    assert COLLECTION_HEMISPHERES["sentinel"] == frozenset({"estate"}) == frozenset({sentinel.SENTINEL_HEMISPHERE})
    with pytest.raises(HemisphereViolation):
        store.get("sentinel", hemisphere="corporate")  # a corporate dispatch never reads Sentinel output


def test_celery_beat_timezone_is_resolved_from_the_system_never_hard_coded(tmp_path: Path) -> None:
    """The 04:15 retention follows the node-local 02:30 AEGIS timer: TZ first, then /etc/timezone, then the
    /etc/localtime symlink, then UTC with a warning (fix round 4)."""
    assert celery_app.resolve_timezone({"TZ": "Europe/Paris"}) == "Europe/Paris"
    etc_tz = tmp_path / "timezone"
    etc_tz.write_text("Australia/Brisbane\n")
    assert celery_app.resolve_timezone({}, etc_timezone=etc_tz, localtime=tmp_path / "none") == "Australia/Brisbane"
    zi = tmp_path / "usr" / "share" / "zoneinfo" / "Pacific" / "Auckland"
    zi.parent.mkdir(parents=True)
    zi.write_bytes(b"TZif2")
    link = tmp_path / "localtime"
    link.symlink_to(zi)
    assert celery_app.resolve_timezone({}, etc_timezone=tmp_path / "none", localtime=link) == "Pacific/Auckland"
    assert celery_app.resolve_timezone({}, etc_timezone=tmp_path / "none", localtime=tmp_path / "none2") == "UTC"
    assert celery_app.TIMEZONE and "Sydney" not in Path(celery_app.__file__).read_text(encoding="utf-8")


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


def test_refusal_counter_survives_a_restart_and_the_client_never_sees_helper_stderr(tmp_path: Path) -> None:
    """Fix round 2: the counter is persisted (an orchestrator restart does not hand a guesser a fresh window) and a
    contract error's stderr goes to the log, not into the HTTP body."""
    refusals = tmp_path / "vault-refusals.json"
    runner = FakeVaultRunner(refuse=True)
    ctl = VaultController(
        helper="/usr/local/bin/atlas-vault",
        mount_dir=str(tmp_path / "open"),
        runner=runner,
        sudo=False,
        sleep=lambda s: None,
        refusals_file=refusals,
    )
    for _ in range(4):
        ctl.open("wrong")
    assert ctl.refusals == 4 and json.loads(refusals.read_text())["refusals"] == 4
    assert stat.S_IMODE(refusals.stat().st_mode) == 0o600
    again = VaultController(
        helper="/usr/local/bin/atlas-vault",
        mount_dir=str(tmp_path / "open"),
        runner=runner,
        sudo=False,
        sleep=lambda s: None,
        refusals_file=refusals,
    )
    assert again.refusals == 4 and again.backoff_s() == 4.0

    def contract_error(argv: list[str], **kw: Any) -> Any:
        import subprocess

        return subprocess.CompletedProcess(argv, 2, "", "gocryptfs: /srv/atlas/vault/cipher: not initialised")

    broken = VaultController(
        helper="/usr/local/bin/atlas-vault", mount_dir=str(tmp_path / "open"), runner=contract_error, sudo=False
    )
    with pytest.raises(vault_mod.VaultError) as exc:
        broken.open("x")
    assert "not initialised" not in str(exc.value) and "journal" in str(exc.value)


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
    monkeypatch.setenv("VAULT_SESSION_STORE", str(tmp_path / "sessions-durable.json"))
    monkeypatch.setenv("VAULT_REFUSALS_FILE", str(tmp_path / "refusals.json"))
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


# --- explicit embeddings, unique ids (10.1) --------------------------------------------------------------------------


class _AssertingCollection(StubCollection):
    """Every backend call must carry explicit vectors: the chromadb default embedding function (a model download,
    rule 16.3 item 8) is never given a chance."""

    def add(self, **kw: Any) -> None:
        assert kw.get("embeddings"), "add() without embeddings"
        super().add(**kw)

    def upsert(self, **kw: Any) -> None:
        assert kw.get("embeddings"), "upsert() without embeddings"
        super().upsert(**kw)

    def query(self, **kw: Any) -> dict[str, Any]:
        assert kw.get("query_embeddings"), "query() without query_embeddings"
        return super().query(**kw)


class _AssertingChroma(StubChroma):
    def __init__(self) -> None:
        super().__init__()
        self.kwargs: list[dict[str, Any]] = []

    def get_or_create_collection(self, name: str, **kw: Any) -> StubCollection:
        self.kwargs.append(kw)
        return self.collections.setdefault(name, _AssertingCollection(name))


def test_every_backend_call_carries_explicit_embeddings_and_ids_do_not_collide() -> None:
    chroma = _AssertingChroma()
    store = MemoryStore(chroma, stub_embedding_fn, sessions=SessionTags(), freeze_flag=None, clock=lambda: 1000.0)
    assert store.write("estate", ["a"], hemisphere="estate").written
    assert store.write("estate", ["b"], hemisphere="estate").written  # same millisecond, distinct generated ids
    assert store.write("estate", ["c"], hemisphere="estate", ids=["fixed"], upsert=True).written
    assert len(store.query("estate", "a", k=3, hemisphere="estate")) == 3
    assert chroma.collections["estate"].count() == 3
    assert chroma.kwargs and all(kw.get("embedding_function", "missing") is None for kw in chroma.kwargs)


# --- D9 in retention and the prune ----------------------------------------------------------------------------------


class _FakeOWUI:
    def __init__(self, chats: list[dict[str, Any]]) -> None:
        self.chats = chats
        self.deleted: list[str] = []

    def all_chats(self) -> list[dict[str, Any]]:
        return [c for c in self.chats if c["id"] not in self.deleted]

    def delete_chat(self, chat_id: str) -> None:
        self.deleted.append(chat_id)


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.generated: list[str] = []
        self.hemispheres: list[str] = []

    def route(self, message: str) -> dict[str, Any]:
        return {"hemisphere": "estate" if "trust" in message else "corporate", "route": "x"}

    def generate(self, engine: str, messages: list[dict[str, str]], *, hemisphere: str, **kw: Any) -> str:
        self.generated.append(messages[-1]["content"])
        self.hemispheres.append(hemisphere)
        return "summary line"


def _chat(cid: str, text: str, updated: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "title": cid,
        "updated_at": updated,
        "chat": {"messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]},
        **extra,
    }


def test_retention_follows_d9_for_pinned_chats_and_honours_every_vault_signal(tmp_path: Path) -> None:
    """D9 (10.4) by default (fix round 4): a pinned chat older than 90 days is summarised AND its Open WebUI record
    deleted like any other; vault chats are deleted without summary whether the token sits at the start, in the
    middle, in meta.tags or only in the orchestrator's session tags (fix round 2). Every summary generation declares
    the routed hemisphere to the orchestrator (atlas_hemisphere)."""
    sessions = SessionTags(tmp_path / "sessions.json")
    sessions.tag_vault("flagged")
    now = 100 * 86400.0
    old = now - 91 * 86400.0
    chats = [
        _chat("pinned", "the trust deed and the vineyard", old, pinned=True),
        _chat("plain", "AEC bid numbers", old),
        _chat("vault-mid", "what about [VAULT] the will", old),
        _chat("vault-tag", "hello", old, meta={"tags": ["Vault"]}),
        _chat("flagged", "tagged through the X-Atlas-Vault header", old, pinned=True),
        _chat("fresh", "today", now - 10),
    ]
    owui, orch = _FakeOWUI(chats), _FakeOrchestrator()
    store = MemoryStore(StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=None)
    res = retention.run_retention(owui, orch, store, now=now, sessions=sessions)
    assert res.errors == []
    assert sorted(owui.deleted) == ["flagged", "pinned", "plain", "vault-mid", "vault-tag"]
    assert res.vault_deleted == 3 and res.kept_pinned == 0 and res.pinned_summarised == 1 and res.summarised == 2
    assert res.deleted == 5 and not res.keep_pinned
    assert store.get("estate", hemisphere="estate", ids=["chat-summary-pinned"])
    assert store.get("corporate", hemisphere="corporate", ids=["chat-summary-plain"])
    assert orch.hemispheres == ["estate", "corporate"]  # the trust deed routes estate, the bid corporate


def test_retention_keep_pinned_opt_in_summarises_once_per_version(tmp_path: Path) -> None:
    """OPENWEBUI_KEEP_PINNED=1 (the recorded opt-in deviation): the pinned record stays, D9's memory half still runs
    once per version of the chat, and the ledger counts it under kept_pinned."""
    now = 100 * 86400.0
    old = now - 91 * 86400.0
    chats = [_chat("pinned", "the trust deed and the vineyard", old, pinned=True), _chat("plain", "AEC bid", old)]
    owui, orch = _FakeOWUI(chats), _FakeOrchestrator()
    store = MemoryStore(StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=None)
    res = retention.run_retention(owui, orch, store, now=now, keep_pinned=True)
    assert res.errors == [] and owui.deleted == ["plain"]
    assert res.keep_pinned and res.kept_pinned == 1 and res.pinned_summarised == 1 and res.summarised == 2
    # Second night: the pinned chat is unchanged, so no second generation; a new version is summarised again.
    res2 = retention.run_retention(owui, orch, store, now=now + 86400, keep_pinned=True)
    assert res2.pinned_summarised == 0 and len(orch.generated) == 2 and owui.deleted == ["plain"]
    chats[0]["updated_at"] = old + 3600
    res3 = retention.run_retention(owui, orch, store, now=now + 86400, keep_pinned=True)
    assert res3.pinned_summarised == 1 and len(orch.generated) == 3


def test_retention_feeds_the_graph_layer_and_never_blocks_the_purge_on_it(tmp_path: Path) -> None:
    """The D7 graph's one Day 1 feeder (memory.py docstring): each summary is inserted into the hemisphere's LightRAG as
    a permanent document; a graph failure is counted and logged, the D9 purge still happens."""
    now = 100 * 86400.0
    old = now - 91 * 86400.0
    store, _chroma, rags, _sessions = make_store(tmp_path)
    chats = [_chat("a", "the trust deed", old), _chat("b", "AEC bid", old)]
    owui, orch = _FakeOWUI(chats), _FakeOrchestrator()
    res = retention.run_retention(owui, orch, store, now=now)
    assert res.errors == [] and res.graph_written == 2 and res.graph_failed == 0 and sorted(owui.deleted) == ["a", "b"]
    assert [ids for _t, ids in rags.rags["estate"].inserted] == [["chat-summary-a"]]
    assert [ids for _t, ids in rags.rags["corporate"].inserted] == [["chat-summary-b"]]
    assert {r.doc_id for r in store.graph.index_rows()} == {"chat-summary-a", "chat-summary-b"}  # type: ignore[union-attr]
    assert not [r for r in store.graph.index_rows(temporal_only=True)]  # type: ignore[union-attr]  # permanent

    class _Broken:
        def insert(self, *a: Any, **kw: Any) -> Any:
            raise MemoryStoreError("tiktoken table not cached")

    store.graph = _Broken()  # type: ignore[assignment]
    chats.append(_chat("c", "another bid", old))
    res = retention.run_retention(owui, orch, store, now=now)
    assert res.errors == [] and res.graph_failed == 1 and "c" in owui.deleted  # purged; the graph gap is reported


def test_prune_purges_expired_chat_turns_without_archiving_them(tmp_path: Path) -> None:
    """D9: a purged chat must not live on under /srv/cold (restic's include set). Expired `chat-turn` documents are
    deleted; other temporal data is archived first, as 9.6 says."""
    clock = {"t": 1_000_000.0}
    store = MemoryStore(
        StubChroma(), stub_embedding_fn, sessions=SessionTags(), freeze_flag=None, clock=lambda: clock["t"]
    )
    store.write(
        "estate",
        ["User: the vineyard\nArthur: noted"],
        [{"kind": "chat-turn"}],
        hemisphere="estate",
        temporal=True,
        ttl_hours=1,
    )
    store.write(
        "sentinel", ["pulse: load high"], [{"kind": "sentinel-pulse"}], hemisphere="estate", temporal=True, ttl_hours=1
    )
    store.write("estate", ["permanent fact"], [{"kind": "fact"}], hemisphere="estate")
    clock["t"] += 2 * 3600
    res = prune.run_sweep(store, tmp_path / "cold", now=clock["t"])
    assert res.errors == [] and res.purged == {"estate": 1} and res.moved == {"sentinel": 1}
    assert store.count("estate") == 1 and store.count("sentinel") == 0
    archive = Path(res.archive)
    assert archive.is_file()
    import tarfile

    with tarfile.open(archive, mode="r:*") as tar:
        names = tar.getnames()
        blob = b"".join((tar.extractfile(n) or io_empty()).read() for n in names)
    assert "sentinel.json" in names and "estate.json" not in names
    assert b"vineyard" not in blob and b"load high" in blob
    # The archive holds memory content: 0640 in a 0750 directory, no umask window (fix round 4).
    assert stat.S_IMODE(archive.stat().st_mode) == 0o640 and stat.S_IMODE(archive.parent.stat().st_mode) == 0o750
    assert res.spooled == {}


def test_prune_reports_deletions_spooled_behind_the_aegis_freeze_as_still_active(tmp_path: Path) -> None:
    """MemoryStore.delete returns 0 and spools when the freeze outlasts freeze_wait_s; the ledger must not call those
    documents purged or moved (fix round 4): they stay active until the thaw replays the spool."""
    clock = {"t": 1_000_000.0}
    flag = tmp_path / "aegis-freeze"
    store = MemoryStore(
        StubChroma(),
        stub_embedding_fn,
        sessions=SessionTags(),
        freeze_flag=flag,
        freeze_wait_s=2.0,
        spool_dir=tmp_path / "spool",
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    store.write(
        "estate", ["User: x\nArthur: y"], [{"kind": "chat-turn"}], hemisphere="estate", temporal=True, ttl_hours=1
    )
    store.write("sentinel", ["pulse"], [{"kind": "sentinel-pulse"}], hemisphere="estate", temporal=True, ttl_hours=1)
    clock["t"] += 2 * 3600
    flag.write_text("1")  # the backup is running and outlasts the wait
    res = prune.run_sweep(store, tmp_path / "cold", now=clock["t"])
    assert res.errors == [] and res.purged == {} and res.moved == {}
    assert res.spooled == {"estate": 1, "sentinel": 1} and res.total == 0
    assert store.count("estate") == 1 and store.count("sentinel") == 1  # still active, honestly
    assert Path(res.archive).is_file()  # the archive was verified before the (spooled) delete, as 9.6 requires
    flag.unlink()
    out = store.replay_spool()
    assert out["replayed"] == 2 and out["failed"] == 0 and store.count("estate") == 0 and store.count("sentinel") == 0


def io_empty() -> Any:
    import io

    return io.BytesIO(b"")
