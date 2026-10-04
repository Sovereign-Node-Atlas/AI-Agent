"""Memory: the Vector Cortex (ChromaDB, Section 10.1) and the graph layer (LightRAG, Section 10.2, D7).

Rules this module is the code path for (rule §7.7: code, not prompt instructions):
  * Hemisphere binding (10.1, 7.3): each collection is bound to a hemisphere; a read OR write from a dispatch whose
    hemisphere is not permitted for that collection raises `HemisphereViolation`. Chroma 1.x has no auth or ACL
    (services-tools.md §2.1 VERIFIED), so this class is the only enforcement.
  * Vault tag (10.5, 11; V18): any write from a session that atlas.vault.SessionTags marks `vault` is DROPPED and
    logged, never queued, never summarised, unless `remember=True` (the Principal's explicit "remember this").
  * AEGIS freeze (9.5): while the freeze flag file exists, writes wait (up to `freeze_wait_s`); a write still
    frozen after that is SPOOLED to a JSONL file under `spool_dir` (MEMORY_SPOOL_DIR, default
    /srv/atlas/data/orchestrator/spool) and replayed by `replay_spool()` from the AEGIS thaw task ("then writes
    resume", 9.5 Freeze row; fix round: a nightly backup runs minutes to hours and no chat turn, scar or BLUF is lost
    to it). The graph layer spools the same way (fix round 2: `graph-insert` / `graph-delete` ops, replayed through
    LightRAGStore). With no spool configured (tests) the write raises MemoryFrozen as before. A spooled write has
    already passed the hemisphere and vault checks; the replay commits it as it was. Spool files hold memory CONTENT
    in plaintext, so the directory is 0700 and every file is created 0600 (CONVENTIONS.md §2), and they sit in
    restic's include set exactly like the Chroma persist dir they are destined for.
  * Telemetry (rule §7.1, 12.1): the chromadb client is built with Settings(anonymized_telemetry=False) so no PostHog
    beacon is attempted on client start (chromadb-client 1.5.9 defaults it on; VERIFIED in the fix-round review).
  * Embeddings come from the resident bge-m3 llama-server (EMBEDDING_URL, /v1/embeddings; gguf-models.md §9) and are
    always passed explicitly to Chroma: the client's default embedding function would download a model from the
    internet, which rule §7.1 forbids.
  * The temporal flag (9.6): every document carries `ts` and `temporal`; `expires_at` when the writer gave a TTL.
    tasks/prune.py sweeps on those fields. Scars (`scars`) are exempt and never carry `temporal`.

Backends are injected so the tests run with a stub (CONVENTIONS.md §7.8): `ChromaLike` / `CollectionLike` are the
subset of the chromadb API this module uses (VERIFIED names: get_or_create_collection, add, query, get, delete, count,
upsert). LightRAG is imported lazily inside `LightRAGStore` so nothing here needs it at import time. The graph layer
keeps ONE LightRAG PER HEMISPHERE (`workspace="corporate"|"estate"` under the same working_dir; LightRAG 1.5.7's
workspace field isolates the storages, VERIFIED in the fix-round wheel review), so the 10.1 binding holds for the
graph as it does for Chroma, and drives every coroutine on ONE long-lived event loop per store (a daemon thread):
LightRAG's shared asyncio locks bind to the loop of their first contended acquire, and a fresh asyncio.run() per
call would trip "bound to a different event loop" on the second insert.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from atlas.vault import SessionTags

log = logging.getLogger("atlas.memory")

# CONVENTIONS.md §8: the six collections and the hemispheres allowed to touch each (Section 10.1 binding).
COLLECTIONS: tuple[str, ...] = ("corporate", "estate", "scars", "documents_corporate", "documents_estate", "sentinel")
COLLECTION_HEMISPHERES: dict[str, frozenset[str]] = {
    "corporate": frozenset({"corporate"}),
    "documents_corporate": frozenset({"corporate"}),
    "estate": frozenset({"estate"}),
    "documents_estate": frozenset({"estate"}),
    # Scars are tagged per persona and domain (9.4) and injected before any task in either hemisphere; every scar
    # document carries `hemisphere`, and tasks.ouroboros.retrieve_scars filters on it (fix round 2).
    "scars": frozenset({"corporate", "estate"}),
    # Sentinel sits under Arthur (9.3, C12: "not Ren"), so its collection is ESTATE-bound like every other collection
    # ("each collection is bound to a hemisphere", 10.1; fix round 2). Silas owns the market anomalies as a FIELD of
    # the BLUF (owners), not as a Ren-side read: a corporate dispatch never reads Sentinel output.
    "sentinel": frozenset({"estate"}),
}
DEFAULT_FREEZE_FLAG = "/run/atlas/aegis-freeze"
DEFAULT_GRAPH_INDEX = "atlas-graph-index.sqlite3"
DEFAULT_SPOOL_DIR = "/srv/atlas/data/orchestrator/spool"
GRAPH_HEMISPHERES: tuple[str, ...] = ("corporate", "estate")
# LightRAG's tokenizer: "gpt-4" maps to cl100k_base, the one table phase2/04-memory.sh seeds into TIKTOKEN_CACHE_DIR
# (the 1.5.7 default "gpt-4o-mini" needs o200k_base, which is not cached and whose host is not allowlisted).
TIKTOKEN_MODEL_NAME = "gpt-4"
TIKTOKEN_BLOB_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"

__all__ = [
    "COLLECTIONS",
    "COLLECTION_HEMISPHERES",
    "ChromaLike",
    "CollectionLike",
    "EmbeddingFn",
    "HemisphereViolation",
    "Hit",
    "LightRAGStore",
    "MemoryFrozen",
    "MemoryStore",
    "MemoryStoreError",
    "StubChroma",
    "StubCollection",
    "WriteResult",
    "build_memory_store",
    "check_chroma_proxy",
    "llama_embedding_fn",
    "tiktoken_cache_file",
]


class MemoryStoreError(RuntimeError):
    """A memory backend is unreachable or misconfigured; raised loudly (rule §7.4)."""


class HemisphereViolation(MemoryStoreError):
    """A dispatch touched a collection its hemisphere is not bound to (Sections 7.3, 10.1)."""


class MemoryFrozen(MemoryStoreError):
    """AEGIS freeze in force longer than the writer was prepared to wait (Section 9.5)."""


# --- backend protocols ------------------------------------------------------------------------------------------------


class CollectionLike(Protocol):
    name: str

    def add(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...

    def upsert(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: Mapping[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> Mapping[str, Any]: ...

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include: Sequence[str] | None = None,
    ) -> Mapping[str, Any]: ...

    def delete(self, *, ids: Sequence[str]) -> None: ...

    def count(self) -> int: ...


class ChromaLike(Protocol):
    def get_or_create_collection(self, name: str, **kw: Any) -> CollectionLike: ...

    def heartbeat(self) -> int: ...


EmbeddingFn = Callable[[Sequence[str]], list[list[float]]]


@dataclass(frozen=True)
class Hit:
    id: str
    document: str
    metadata: dict[str, Any]
    distance: float | None


@dataclass(frozen=True)
class WriteResult:
    written: bool
    dropped: bool = False
    reason: str = ""
    ids: tuple[str, ...] = ()
    spooled: bool = False  # deferred to the AEGIS spool; committed by replay_spool() at thaw


# --- stub backend (tests, and atlas-admin dry runs) -------------------------------------------------------------------


class StubCollection:
    """In-memory CollectionLike: exact-match `where` on top-level keys, cosine-free 'distance' = insertion order."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, tuple[str, dict[str, Any], list[float]]] = {}

    def add(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        for i, d, m, e in zip(ids, documents, metadatas, embeddings, strict=True):
            if i in self.rows:
                raise ValueError(f"duplicate id {i}")
            self.rows[i] = (d, dict(m), list(e))

    def upsert(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        for i, d, m, e in zip(ids, documents, metadatas, embeddings, strict=True):
            self.rows[i] = (d, dict(m), list(e))

    def _match(self, meta: Mapping[str, Any], where: Mapping[str, Any] | None) -> bool:
        if not where:
            return True
        for k, v in where.items():
            if k == "$and":
                if not all(self._match(meta, w) for w in v):
                    return False
                continue
            if isinstance(v, Mapping):
                ((op, val),) = v.items()
                cur = meta.get(k)
                if cur is None:
                    return False
                ok = {
                    "$lt": cur < val,
                    "$lte": cur <= val,
                    "$gt": cur > val,
                    "$gte": cur >= val,
                    "$eq": cur == val,
                    "$ne": cur != val,
                }.get(op)
                if not ok:
                    return False
            elif meta.get(k) != v:
                return False
        return True

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: Mapping[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        q = list(query_embeddings[0]) if query_embeddings else []
        scored: list[tuple[float, str]] = []
        for i, (_d, m, e) in self.rows.items():
            if not self._match(m, where):
                continue
            dist = sum((a - b) ** 2 for a, b in zip(q, e, strict=False)) ** 0.5 if q and e else 0.0
            scored.append((dist, i))
        scored.sort()
        top = scored[:n_results]
        return {
            "ids": [[i for _, i in top]],
            "documents": [[self.rows[i][0] for _, i in top]],
            "metadatas": [[self.rows[i][1] for _, i in top]],
            "distances": [[d for d, _ in top]],
        }

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        keys = [i for i in self.rows if (ids is None or i in ids) and self._match(self.rows[i][1], where)]
        keys = keys[offset or 0 :]
        if limit is not None:
            keys = keys[:limit]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys], "metadatas": [self.rows[i][1] for i in keys]}

    def delete(self, *, ids: Sequence[str]) -> None:
        for i in ids:
            self.rows.pop(i, None)

    def count(self) -> int:
        return len(self.rows)


class StubChroma:
    def __init__(self) -> None:
        self.collections: dict[str, StubCollection] = {}

    def get_or_create_collection(self, name: str, **kw: Any) -> StubCollection:
        return self.collections.setdefault(name, StubCollection(name))

    def heartbeat(self) -> int:
        return int(time.time() * 1e9)


def stub_embedding_fn(texts: Sequence[str]) -> list[list[float]]:
    """Deterministic 8-dim bag-of-bytes embedding for tests; never used in production."""
    out: list[list[float]] = []
    for t in texts:
        v = [0.0] * 8
        for i, ch in enumerate(t.encode("utf-8")):
            v[i % 8] += ch / 255.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


# --- the real embedding function: bge-m3 on llama-server --------------------------------------------------------------


def llama_embedding_fn(
    embedding_url: str, model: str = "embed-bge-m3", *, timeout_s: float = 120.0, batch: int = 16
) -> EmbeddingFn:
    """POST {EMBEDDING_URL}/embeddings (OpenAI shape, VERIFIED gguf-models.md §9); EMBEDDING_URL ends in /v1."""
    from atlas.engines import EngineError, LlamaClient

    base = embedding_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    client = LlamaClient(base, timeout_s=timeout_s)

    def embed(texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = list(texts[i : i + batch])
            try:
                out.extend(client.embeddings(chunk, model=model))
            except EngineError as exc:
                raise MemoryStoreError(
                    f"embedding request to {base} failed: {exc} (is llama-server@{model} active?)"
                ) from exc
        if len(out) != len(texts):
            raise MemoryStoreError(f"embedding server returned {len(out)} vectors for {len(texts)} inputs")
        return out

    return embed


# --- the Vector Cortex ------------------------------------------------------------------------------------------------


class MemoryStore:
    def __init__(
        self,
        backend: ChromaLike,
        embed: EmbeddingFn,
        *,
        sessions: SessionTags | None = None,
        freeze_flag: str | Path | None = DEFAULT_FREEZE_FLAG,
        freeze_wait_s: float = 120.0,
        graph: LightRAGStore | None = None,
        spool_dir: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.embed = embed
        self.sessions = sessions or SessionTags()
        self.freeze_flag = Path(freeze_flag) if freeze_flag else None
        self.freeze_wait_s = freeze_wait_s
        self.graph = graph
        self.spool_dir = Path(spool_dir) if spool_dir else None
        self._spool_file: Path | None = None
        self._clock = clock
        self._sleep = sleep
        self._collections: dict[str, CollectionLike] = {}
        self._lock = threading.RLock()
        self.dropped: list[dict[str, Any]] = []  # audit of dropped writes (vault rule); bounded below

    # --- rules -----------------------------------------------------------------------------------------------------

    @staticmethod
    def check_access(collection: str, hemisphere: str, op: str = "read") -> None:
        if collection not in COLLECTION_HEMISPHERES:
            raise MemoryStoreError(f"unknown collection {collection!r}; Section 10.1 names {COLLECTIONS}")
        if hemisphere not in COLLECTION_HEMISPHERES[collection]:
            raise HemisphereViolation(
                f"{op} of collection {collection!r} is not permitted for hemisphere "
                f"{hemisphere!r} (Section 10.1 binding; allowed: "
                f"{sorted(COLLECTION_HEMISPHERES[collection])})"
            )

    def is_frozen(self) -> bool:
        return self.freeze_flag is not None and self.freeze_flag.exists()

    def _wait_thaw(self) -> bool:
        """True when the store is writable now; False when still frozen after the wait and a spool exists (the
        caller spools); MemoryFrozen when still frozen and there is nowhere to spool."""
        if not self.is_frozen():
            return True
        deadline = self._clock() + self.freeze_wait_s
        log.info("memory write waits: AEGIS freeze flag %s present (Section 9.5)", self.freeze_flag)
        while self.is_frozen():
            if self._clock() >= deadline:
                if self.spool_dir is not None:
                    return False
                raise MemoryFrozen(
                    f"AEGIS freeze flag {self.freeze_flag} still present after {self.freeze_wait_s:.0f}s and no "
                    "spool directory is configured; the write is refused (Section 9.5)"
                )
            self._sleep(1.0)
        return True

    # --- the AEGIS spool (9.5 "then writes resume") --------------------------------------------------------------

    def _spool(self, op: dict[str, Any]) -> None:
        assert self.spool_dir is not None
        with self._lock:
            if self._spool_file is None:
                _private_dir(self.spool_dir)
                name = f"spool-{socket.gethostname()}-{os.getpid()}-{int(self._clock() * 1000)}.jsonl"
                self._spool_file = self.spool_dir / name
            line = json.dumps({**op, "spooled_at": self._clock()}, ensure_ascii=False, default=str)
            _append_private(self._spool_file, line + "\n")
        log.warning(
            "memory %s SPOOLED (AEGIS freeze): collection=%s docs=%d -> %s (replayed at thaw, Section 9.5)",
            op.get("op"),
            op.get("collection"),
            len(op.get("documents") or op.get("ids") or []),
            self._spool_file,
        )

    def spooled_count(self) -> int:
        if self.spool_dir is None or not self.spool_dir.is_dir():
            return 0
        n = 0
        for f in self.spool_dir.glob("spool-*.jsonl"):
            with f.open(encoding="utf-8") as fh:
                n += sum(1 for ln in fh if ln.strip())
        return n

    def replay_spool(self) -> dict[str, Any]:
        """Commit every spooled op (the thaw task calls this after the flag is down). A line that fails is kept in
        `<file>.failed.jsonl` with the error, never dropped silently; the summary names the counts and the files."""
        out: dict[str, Any] = {"replayed": 0, "failed": 0, "files": []}
        if self.spool_dir is None or not self.spool_dir.is_dir():
            out["skipped"] = "no spool directory"
            return out
        if self.is_frozen():
            raise MemoryFrozen(f"replay refused: the freeze flag {self.freeze_flag} is still present")
        with self._lock:
            self._spool_file = None  # a later spool starts a new file; this one is being drained
        for f in sorted(self.spool_dir.glob("spool-*.jsonl")):
            work = f.with_suffix(".replaying")
            try:
                f.rename(work)
            except OSError:
                continue  # another process took it
            failed: list[str] = []
            with work.open(encoding="utf-8") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        op = json.loads(ln)
                        self._commit(op)
                        out["replayed"] += 1
                    except Exception as exc:  # recorded per line; the replay goes on
                        out["failed"] += 1
                        failed.append(json.dumps({"error": f"{type(exc).__name__}: {exc}", "line": ln.rstrip()}))
                        log.error("spool replay: %s", exc)
            if failed:
                keep = f.with_name(f.name.replace(".jsonl", ".failed.jsonl"))
                _append_private(keep, "\n".join(failed) + "\n")  # content stays private (0600), kept for the operator
                out.setdefault("failed_files", []).append(str(keep))
            work.unlink()
            out["files"].append(str(f))
        log.info("spool replay: %d committed, %d failed, %d file(s)", out["replayed"], out["failed"], len(out["files"]))
        return out

    def _commit(self, op: dict[str, Any]) -> None:
        kind = op.get("op")
        if kind == "write":
            self._commit_write(
                str(op["collection"]),
                list(op["documents"]),
                [dict(m) for m in op["metadatas"]],
                list(op["ids"]),
                bool(op.get("upsert")),
            )
        elif kind == "delete":
            self._commit_delete(str(op["collection"]), list(op["ids"]))
        elif kind in ("graph-insert", "graph-delete"):
            if self.graph is None:
                raise MemoryStoreError(f"spooled {kind} but this store has no graph layer to replay it into")
            self.graph.commit_spooled(op)
        else:
            raise MemoryStoreError(f"unknown spooled op {kind!r}")

    def _commit_write(
        self, collection: str, docs: list[str], metas: list[dict[str, Any]], out_ids: list[str], upsert: bool
    ) -> WriteResult:
        vectors = self.embed(docs)
        col = self.collection(collection)
        try:
            if upsert:
                col.upsert(ids=out_ids, documents=docs, metadatas=metas, embeddings=vectors)
            else:
                col.add(ids=out_ids, documents=docs, metadatas=metas, embeddings=vectors)
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB write to {collection!r} failed: {exc}") from exc
        log.info("memory write: collection=%s docs=%d", collection, len(docs))
        return WriteResult(written=True, ids=tuple(out_ids))

    def _commit_delete(self, collection: str, ids: list[str]) -> int:
        try:
            self.collection(collection).delete(ids=ids)
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB delete on {collection!r} failed: {exc}") from exc
        return len(ids)

    def _drop(self, collection: str, session_id: str | None, n: int, reason: str) -> WriteResult:
        log.warning(
            "memory write DROPPED: collection=%s session=%s docs=%d reason=%s", collection, session_id, n, reason
        )
        with self._lock:
            self.dropped.append(
                {"ts": self._clock(), "collection": collection, "session_id": session_id, "docs": n, "reason": reason}
            )
            del self.dropped[:-200]
        return WriteResult(written=False, dropped=True, reason=reason)

    def collection(self, name: str) -> CollectionLike:
        with self._lock:
            col = self._collections.get(name)
            if col is None:
                if name not in COLLECTION_HEMISPHERES:
                    raise MemoryStoreError(f"unknown collection {name!r}")
                try:
                    # embedding_function=None: vectors are ALWAYS supplied by this module (every add/upsert/query call
                    # carries `embeddings`; tests/test_vault_tagging.py asserts it). UNVERIFIED: that chromadb-client
                    # 1.5.9 records "no embedding function" for None and raises on a documents-only call instead of
                    # instantiating its default ONNX function (whose model download the allowlist would deny, so the
                    # failure would be loud either way). A custom EmbeddingFunction subclass was considered and not
                    # adopted: its persisted-config interface (name/get_config/build_from_config) is UNVERIFIED for
                    # 1.5.9 and a mismatch would break collection creation at Phase 2 step 4 (rule §7.4: do not
                    # automate what may fail).
                    col = self.backend.get_or_create_collection(name, embedding_function=None)
                except TypeError:
                    col = self.backend.get_or_create_collection(name)
                except Exception as exc:
                    raise MemoryStoreError(f"ChromaDB get_or_create_collection({name!r}) failed: {exc}") from exc
                self._collections[name] = col
            return col

    # --- writes ----------------------------------------------------------------------------------------------------

    def write(
        self,
        collection: str,
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]] | None = None,
        *,
        hemisphere: str,
        session_id: str | None = None,
        ids: Sequence[str] | None = None,
        remember: bool = False,
        temporal: bool = False,
        ttl_hours: float | None = None,
        upsert: bool = False,
    ) -> WriteResult:
        """Write documents. Order of checks: hemisphere (raises) -> vault tag (drops) -> freeze (waits) -> embed."""
        self.check_access(collection, hemisphere, "write")
        docs = [d for d in documents if d and d.strip()]
        if not docs:
            return WriteResult(written=False, reason="nothing to write")
        if self.sessions.is_vault(session_id) and not remember:
            return self._drop(collection, session_id, len(docs), "session is vault-tagged (Section 10.5)")
        if collection == "scars" and temporal:
            raise MemoryStoreError("scars are permanent (Section 9.4); a scar cannot be temporal")
        now = self._clock()
        metas: list[dict[str, Any]] = []
        for i, _ in enumerate(docs):
            m = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
            m.setdefault("ts", now)
            m.setdefault("hemisphere", hemisphere)
            m["temporal"] = bool(temporal or m.get("temporal", False))
            if ttl_hours is not None:
                m["expires_at"] = now + ttl_hours * 3600.0
            if session_id is not None:
                m.setdefault("session_id", session_id)
            m = {k: v for k, v in m.items() if isinstance(v, (str, int, float, bool))}  # Chroma metadata scalars only
            metas.append(m)
        # Generated ids carry a random suffix (fix round 2): the orchestrator and the gpu worker write to the same
        # collection within one millisecond, and Chroma refuses `add` on an existing id (the turn would be lost).
        out_ids = (
            list(ids)
            if ids is not None
            else [f"{collection}-{int(now * 1000)}-{i}-{uuid.uuid4().hex[:8]}" for i in range(len(docs))]
        )
        if len(out_ids) != len(docs):
            raise MemoryStoreError(f"{len(out_ids)} ids for {len(docs)} documents")
        if not self._wait_thaw():
            self._spool(
                {
                    "op": "write",
                    "collection": collection,
                    "documents": docs,
                    "metadatas": metas,
                    "ids": out_ids,
                    "upsert": upsert,
                    "hemisphere": hemisphere,
                    "session_id": session_id,
                }
            )
            return WriteResult(
                written=False, spooled=True, reason="AEGIS freeze: spooled, committed at thaw (Section 9.5)"
            )
        return self._commit_write(collection, docs, metas, out_ids, upsert)

    # --- reads -----------------------------------------------------------------------------------------------------

    def query(
        self, collection: str, text: str, k: int = 4, *, hemisphere: str, where: Mapping[str, Any] | None = None
    ) -> list[Hit]:
        self.check_access(collection, hemisphere, "read")
        if not text.strip() or k < 1:
            return []
        vec = self.embed([text])[0]
        col = self.collection(collection)
        try:
            res = col.query(
                query_embeddings=[vec],
                n_results=k,
                where=dict(where) if where else None,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB query on {collection!r} failed: {exc}") from exc
        return _hits(res, nested=True)

    def get(
        self,
        collection: str,
        *,
        hemisphere: str,
        where: Mapping[str, Any] | None = None,
        ids: Sequence[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Hit]:
        self.check_access(collection, hemisphere, "read")
        col = self.collection(collection)
        try:
            res = col.get(
                ids=list(ids) if ids else None,
                where=dict(where) if where else None,
                limit=limit,
                offset=offset,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB get on {collection!r} failed: {exc}") from exc
        return _hits(res, nested=False)

    def delete(self, collection: str, ids: Sequence[str], *, hemisphere: str) -> int:
        self.check_access(collection, hemisphere, "write")
        if collection == "scars":
            # Scars are retired by the Principal's curation only (9.4); the prune never reaches here.
            log.info("retiring %d scar(s) by explicit request", len(ids))
        if not ids:
            return 0
        if not self._wait_thaw():
            self._spool({"op": "delete", "collection": collection, "ids": list(ids), "hemisphere": hemisphere})
            return 0
        return self._commit_delete(collection, list(ids))

    def count(self, collection: str) -> int:
        try:
            return int(self.collection(collection).count())
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB count on {collection!r} failed: {exc}") from exc

    def heartbeat(self) -> bool:
        try:
            self.backend.heartbeat()
            return True
        except Exception as exc:
            log.error("ChromaDB heartbeat failed: %s", exc)
            return False


def _private_dir(path: Path) -> None:
    """mkdir -p with mode 0700 whatever the umask; the spool holds memory content in plaintext (CONVENTIONS.md §2)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _append_private(path: Path, text: str) -> None:
    """Append to a file created 0600 (no 0644 window between create and chmod)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _hits(res: Mapping[str, Any], *, nested: bool) -> list[Hit]:
    ids = res.get("ids") or []
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    dists = res.get("distances") or []
    if nested:
        ids, docs, metas = (ids[0] if ids else []), (docs[0] if docs else []), (metas[0] if metas else [])
        dists = dists[0] if dists else []
    out: list[Hit] = []
    for i, id_ in enumerate(ids):
        out.append(
            Hit(
                id=str(id_),
                document=str(docs[i]) if i < len(docs) and docs[i] is not None else "",
                metadata=dict(metas[i]) if i < len(metas) and metas[i] else {},
                distance=float(dists[i]) if i < len(dists) and dists[i] is not None else None,
            )
        )
    return out


# --- the graph layer (LightRAG, D7) -----------------------------------------------------------------------------------


@dataclass
class GraphIndexRow:
    doc_id: str
    ts: float
    temporal: bool
    hemisphere: str
    expires_at: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class _LoopThread:
    """One long-lived asyncio loop on a daemon thread; every LightRAG coroutine runs on it (module docstring)."""

    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def run(self, coro: Any, timeout_s: float | None = None) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout_s)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5.0)


def tiktoken_cache_file(cache_dir: str | Path) -> Path:
    """Where tiktoken keeps cl100k_base under TIKTOKEN_CACHE_DIR.
    # UNVERIFIED: the cache file name is sha1(blob URL).hexdigest() — tiktoken/load.py read_file_cached as read in
    # the fix round, not in the research; a wrong assumption makes _build_rag fail loudly with the seeding command,
    # never reach for the network."""
    return Path(cache_dir) / hashlib.sha1(TIKTOKEN_BLOB_URL.encode("utf-8")).hexdigest()


class LightRAGStore:
    """LightRAG with insert/query on the local endpoints plus the same vault/freeze/hemisphere rules.

    One LightRAG instance per hemisphere (`workspace=`), built lazily; `rag_factory(hemisphere)` injects a double.
    LLM calls go to the orchestrator's internal OpenAI-shaped endpoint (ORCH_URL/internal/v1) on the resident router
    model (R6: extraction on Eleanor's model); every such call holds the orchestrator's ONE generation slot like any
    weight-bearing generation (atlas.api.GenerationSlot, 4.2 rule 3, 9.7 C15), so an extraction never runs beside a
    chat generation and a chat queues behind an extraction in FIFO order. Embeddings go straight to the resident
    bge-m3 server.

    AEGIS freeze (9.5, fix round 2): an insert or delete during the freeze waits `freeze_wait_s`, then is SPOOLED to
    `spool_dir` (the same directory as MemoryStore's; ops `graph-insert` / `graph-delete`) and replayed by
    MemoryStore.replay_spool through `commit_spooled` at thaw. With no spool_dir it raises MemoryFrozen after the wait.

    LightRAG 1.5.7 API (services-tools.md §2.2 verified `openai_complete_if_cache`, `initialize_storages` and the
    env keys; the fix-round wheel review confirmed the rest): `LightRAG(working_dir=, workspace=, llm_model_func=,
    embedding_func=EmbeddingFunc(embedding_dim, max_token_size, func), tiktoken_model_name=)`, `ainsert(input,
    ids=[...])`, `aquery(q, param=QueryParam(mode=...))`, `adelete_by_doc_id(id)`, `finalize_storages()`.
    """

    def __init__(
        self,
        working_dir: str | Path,
        *,
        llm_base_url: str,
        llm_model: str,
        embedding_url: str,
        embedding_model: str,
        embedding_dim: int,
        sessions: SessionTags | None = None,
        freeze_flag: str | Path | None = DEFAULT_FREEZE_FLAG,
        freeze_wait_s: float = 120.0,
        spool_dir: str | Path | None = None,
        index_path: str | Path | None = None,
        rag_factory: Callable[[str], Any] | None = None,
        tiktoken_cache_dir: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.embedding_url = embedding_url
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.sessions = sessions or SessionTags()
        self.freeze_flag = Path(freeze_flag) if freeze_flag else None
        self.freeze_wait_s = freeze_wait_s
        self.spool_dir = Path(spool_dir) if spool_dir else None
        self._spool_file: Path | None = None
        self.index_path = Path(index_path) if index_path else self.working_dir / DEFAULT_GRAPH_INDEX
        self._rag_factory = rag_factory
        self.tiktoken_cache_dir = Path(tiktoken_cache_dir) if tiktoken_cache_dir else None
        self._rags: dict[str, Any] = {}
        self._loop: _LoopThread | None = None
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._index_ready = False

    # --- our own index of what is in the graph (doc ids, temporal flags, hemisphere) -----------------------------

    def _index(self) -> sqlite3.Connection:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.index_path, timeout=30)
        if not self._index_ready:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS docs (doc_id TEXT PRIMARY KEY, ts REAL NOT NULL, temporal INTEGER "
                "NOT NULL, hemisphere TEXT NOT NULL, expires_at REAL, meta_json TEXT)"
            )
            conn.commit()
            self._index_ready = True
        return conn

    def index_rows(self, *, temporal_only: bool = False) -> list[GraphIndexRow]:
        conn = self._index()
        try:
            sql = "SELECT doc_id, ts, temporal, hemisphere, expires_at, meta_json FROM docs"
            if temporal_only:
                sql += " WHERE temporal = 1"
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        return [GraphIndexRow(r[0], r[1], bool(r[2]), r[3], r[4], json.loads(r[5] or "{}")) for r in rows]

    def hemisphere_of(self, doc_id: str) -> str | None:
        conn = self._index()
        try:
            row = conn.execute("SELECT hemisphere FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else None

    # --- LightRAG instances ----------------------------------------------------------------------------------------

    def _run(self, coro: Any) -> Any:
        with self._lock:
            if self._loop is None:
                self._loop = _LoopThread(f"atlas-lightrag-{id(self)}")
            loop = self._loop
        return loop.run(coro)

    def _check_tokenizer_cache(self) -> None:
        """The graph must never reach for the network (rule §7.1): cl100k_base must already be in the cache."""
        cache_dir = self.tiktoken_cache_dir or (
            Path(os.environ["TIKTOKEN_CACHE_DIR"]) if os.environ.get("TIKTOKEN_CACHE_DIR") else None
        )
        if cache_dir is None:
            raise MemoryStoreError(
                "TIKTOKEN_CACHE_DIR is not set (memory.env, phase2/04-memory.sh): LightRAG's tokenizer would fetch "
                "its table from openaipublic.blob.core.windows.net, which the allowlist denies"
            )
        f = tiktoken_cache_file(cache_dir)
        if not f.is_file():
            raise MemoryStoreError(
                f"tiktoken table cl100k_base is not cached at {f}. The table is seeded by phase2/04-memory.sh ONLY "
                "after the Principal adds openaipublic.blob.core.windows.net to config/allowlist.txt under a "
                "'build-time, one-time' group (16.3 item 6: the allowlist is the Principal's to change) and the proxy "
                "is re-rendered; config/allowlist.txt does not carry that host today. Never fetched at run time: the "
                "graph refuses to start instead"
            )
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dir))

    def _build_rag(self, hemisphere: str) -> Any:
        if self._rag_factory is not None:
            return self._rag_factory(hemisphere)
        try:
            from lightrag import LightRAG  # type: ignore[import-not-found]
            from lightrag.llm.openai import openai_complete_if_cache  # type: ignore[import-not-found]
            from lightrag.utils import EmbeddingFunc  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MemoryStoreError(
                f"lightrag-hku is not installed in this venv ({exc}); phase2/04-memory.sh installs "
                "lightrag-hku[api]==1.5.7"
            ) from exc
        import numpy as np  # a lightrag dependency (services-tools.md §2.2)

        self._check_tokenizer_cache()
        embed = llama_embedding_fn(self.embedding_url, self.embedding_model)
        llm_base, llm_model = self.llm_base_url, self.llm_model

        async def llm_func(
            prompt: str, system_prompt: str | None = None, history_messages: list[Any] | None = None, **kw: Any
        ) -> str:
            kw.pop("hashing_kv", None)
            return await openai_complete_if_cache(
                llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                base_url=llm_base,
                api_key="atlas-local",
                **kw,
            )

        async def embed_func(texts: list[str]) -> Any:
            return np.array(embed(texts), dtype=np.float32)

        self.working_dir.mkdir(parents=True, exist_ok=True)
        rag = LightRAG(
            working_dir=str(self.working_dir),
            workspace=hemisphere,  # 10.1 binding for the graph: one isolated storage set per hemisphere
            llm_model_func=llm_func,
            embedding_func=EmbeddingFunc(embedding_dim=self.embedding_dim, max_token_size=8192, func=embed_func),
            tiktoken_model_name=TIKTOKEN_MODEL_NAME,
        )
        self._run(rag.initialize_storages())  # README: forgetting this is the common mistake (VERIFIED)
        return rag

    def rag(self, hemisphere: str) -> Any:
        if hemisphere not in GRAPH_HEMISPHERES:
            raise HemisphereViolation(f"the graph is bound per hemisphere; got {hemisphere!r}")
        with self._lock:
            if hemisphere not in self._rags:
                self._rags[hemisphere] = self._build_rag(hemisphere)
            return self._rags[hemisphere]

    def close(self) -> None:
        """finalize_storages() on every instance, then stop the loop (a worker's shutdown hook)."""
        with self._lock:
            rags, self._rags = dict(self._rags), {}
            loop, self._loop = self._loop, None
        for hemi, rag in rags.items():
            fin = getattr(rag, "finalize_storages", None)
            if fin is None or loop is None:
                continue
            try:
                loop.run(fin(), timeout_s=60.0)
            except Exception as exc:
                log.error("graph %s: finalize_storages failed: %s", hemi, exc)
        if loop is not None:
            loop.stop()

    def is_frozen(self) -> bool:
        return self.freeze_flag is not None and self.freeze_flag.exists()

    def _wait_thaw(self) -> bool:
        """Mirror of MemoryStore._wait_thaw: True = write now; False = still frozen, spool it; MemoryFrozen when
        still frozen and there is no spool."""
        if not self.is_frozen():
            return True
        deadline = self._clock() + self.freeze_wait_s
        log.info("graph write waits: AEGIS freeze flag %s present (Section 9.5)", self.freeze_flag)
        while self.is_frozen():
            if self._clock() >= deadline:
                if self.spool_dir is not None:
                    return False
                raise MemoryFrozen(
                    f"AEGIS freeze flag {self.freeze_flag} still present after {self.freeze_wait_s:.0f}s and no "
                    "spool directory is configured; the graph write is refused (Section 9.5)"
                )
            self._sleep(1.0)
        return True

    def _spool(self, op: dict[str, Any]) -> None:
        assert self.spool_dir is not None
        with self._lock:
            if self._spool_file is None:
                _private_dir(self.spool_dir)
                name = f"spool-graph-{socket.gethostname()}-{os.getpid()}-{int(self._clock() * 1000)}.jsonl"
                self._spool_file = self.spool_dir / name
            _append_private(self._spool_file, json.dumps({**op, "spooled_at": self._clock()}, default=str) + "\n")
        log.warning(
            "graph %s SPOOLED (AEGIS freeze): doc=%s -> %s (replayed at thaw)", op["op"], op["doc_id"], self._spool_file
        )

    def commit_spooled(self, op: Mapping[str, Any]) -> None:
        """Replay one spooled graph op (called by MemoryStore._commit from replay_spool; the flag is already down)."""
        kind = op.get("op")
        if kind == "graph-insert":
            self._commit_insert(
                str(op["text"]),
                doc_id=str(op["doc_id"]),
                hemisphere=str(op["hemisphere"]),
                metadata=dict(op.get("metadata") or {}),
                temporal=bool(op.get("temporal")),
                ttl_hours=float(op["ttl_hours"]) if op.get("ttl_hours") is not None else None,
            )
        elif kind == "graph-delete":
            self._commit_delete(str(op["doc_id"]))
        else:
            raise MemoryStoreError(f"unknown spooled graph op {kind!r}")

    # --- API -------------------------------------------------------------------------------------------------------

    def insert(
        self,
        text: str,
        *,
        doc_id: str,
        hemisphere: str,
        metadata: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        remember: bool = False,
        temporal: bool = False,
        ttl_hours: float | None = None,
    ) -> WriteResult:
        if hemisphere not in GRAPH_HEMISPHERES:
            raise HemisphereViolation(f"graph insert needs a hemisphere, got {hemisphere!r}")
        if not text.strip():
            return WriteResult(written=False, reason="nothing to insert")
        if self.sessions.is_vault(session_id) and not remember:
            log.warning(
                "graph insert DROPPED: doc=%s session=%s reason=vault-tagged (Section 10.5)", doc_id, session_id
            )
            return WriteResult(written=False, dropped=True, reason="session is vault-tagged (Section 10.5)")
        if not self._wait_thaw():
            self._spool(
                {
                    "op": "graph-insert",
                    "text": text,
                    "doc_id": doc_id,
                    "hemisphere": hemisphere,
                    "metadata": dict(metadata or {}),
                    "temporal": temporal,
                    "ttl_hours": ttl_hours,
                    "session_id": session_id,
                }
            )
            return WriteResult(
                written=False, spooled=True, reason="AEGIS freeze: spooled, committed at thaw (Section 9.5)"
            )
        return self._commit_insert(
            text, doc_id=doc_id, hemisphere=hemisphere, metadata=metadata, temporal=temporal, ttl_hours=ttl_hours
        )

    def _commit_insert(
        self,
        text: str,
        *,
        doc_id: str,
        hemisphere: str,
        metadata: Mapping[str, Any] | None,
        temporal: bool,
        ttl_hours: float | None,
    ) -> WriteResult:
        rag = self.rag(hemisphere)
        self._run(rag.ainsert(text, ids=[doc_id]))
        now = self._clock()
        conn = self._index()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    now,
                    1 if temporal else 0,
                    hemisphere,
                    now + ttl_hours * 3600.0 if ttl_hours is not None else None,
                    json.dumps(dict(metadata or {}), default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return WriteResult(written=True, ids=(doc_id,))

    def query(self, question: str, *, hemisphere: str, mode: str = "hybrid") -> str:
        if hemisphere not in GRAPH_HEMISPHERES:
            raise HemisphereViolation(f"graph query needs a hemisphere, got {hemisphere!r}")
        rag = self.rag(hemisphere)  # only this hemisphere's graph is consulted (10.1, 7.3)
        try:
            from lightrag import QueryParam  # type: ignore[import-not-found]

            param = QueryParam(mode=mode)
        except ImportError:
            param = None
        result = self._run(rag.aquery(question, param=param) if param is not None else rag.aquery(question))
        return str(result)

    def delete_document(self, doc_id: str) -> bool:
        """True when deleted now; False when spooled for the thaw (the index row stays until the replay)."""
        if not self._wait_thaw():
            self._spool({"op": "graph-delete", "doc_id": doc_id})
            return False
        self._commit_delete(doc_id)
        return True

    def _commit_delete(self, doc_id: str) -> None:
        hemisphere = self.hemisphere_of(doc_id)
        if hemisphere is None:
            raise MemoryStoreError(f"graph document {doc_id!r} is not in the index; which hemisphere holds it?")
        rag = self.rag(hemisphere)
        fn = getattr(rag, "adelete_by_doc_id", None)
        if fn is None:
            raise MemoryStoreError(
                "this LightRAG has no adelete_by_doc_id(); the prune cannot remove graph documents "
                "(services-tools.md §2.2)"
            )
        self._run(fn(doc_id))
        conn = self._index()
        try:
            conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()


# --- production wiring ------------------------------------------------------------------------------------------------


def check_chroma_proxy(chroma_url: str, env: Mapping[str, str]) -> None:
    """Fail fast (fix round) when CHROMA_URL is not loopback while HTTPS_PROXY is set and NO_PROXY does not name the
    host literally: httpx honours the proxy env (trust_env) but not CIDR entries, so every Chroma call would go to
    squid and be denied. phase2/04-memory.sh falls back to the container bridge address; this is where that shows."""
    from urllib.parse import urlsplit

    host = (urlsplit(chroma_url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1", ""):
        return
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or env.get("HTTP_PROXY") or env.get("http_proxy")
    if not proxy:
        return
    no_proxy = [h.strip().lower() for h in (env.get("NO_PROXY") or env.get("no_proxy") or "").split(",") if h.strip()]
    if host in no_proxy or "*" in no_proxy:
        return
    raise MemoryStoreError(
        f"CHROMA_URL={chroma_url} is not loopback and NO_PROXY ({env.get('NO_PROXY') or env.get('no_proxy') or ''!r}) "
        f"does not name {host!r} literally (CIDR entries are not understood by httpx): every ChromaDB call would be "
        "sent to the allowlist proxy and denied. Fix memory.env (phase2/04-memory.sh): publish 8000 on loopback or "
        f"add {host} to NO_PROXY"
    )


def build_memory_store(
    env: Mapping[str, str] | None = None, *, sessions: SessionTags | None = None, with_graph: bool = True
) -> MemoryStore:
    """From /etc/atlas/memory.env (phase2/04-memory.sh contract: CHROMA_URL, EMBEDDING_URL, EMBEDDING_MODEL,
    EMBEDDING_DIM, LIGHTRAG_WORKING_DIR, TIKTOKEN_CACHE_DIR) and orchestrator.env (ORCH_URL, MEMORY_SPOOL_DIR)."""
    env = dict(os.environ if env is None else env)
    chroma_url = env.get("CHROMA_URL", "").strip()
    embedding_url = env.get("EMBEDDING_URL", "").strip()
    if not chroma_url or not embedding_url:
        raise MemoryStoreError("CHROMA_URL / EMBEDDING_URL are not set (memory.env is written by phase2/04-memory.sh)")
    check_chroma_proxy(chroma_url, env)
    try:
        import chromadb  # type: ignore[import-not-found]
        from chromadb.config import Settings  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MemoryStoreError(
            f"chromadb-client is not installed ({exc}); pyproject.toml pins chromadb-client==1.5.9"
        ) from exc
    from urllib.parse import urlsplit

    u = urlsplit(chroma_url)
    host, port = u.hostname or "127.0.0.1", u.port or 8000
    try:
        # VERIFIED signature (§2.1); anonymized_telemetry=False: no PostHog beacon on client start (rule §7.1).
        backend = chromadb.HttpClient(
            host=host, port=port, ssl=(u.scheme == "https"), settings=Settings(anonymized_telemetry=False)
        )
    except Exception as exc:
        raise MemoryStoreError(f"ChromaDB client for {chroma_url} could not be created: {exc}") from exc
    sessions = sessions or SessionTags.from_env(env)
    embed = llama_embedding_fn(embedding_url, env.get("EMBEDDING_MODEL") or "embed-bge-m3")
    freeze_flag = env.get("AEGIS_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG
    spool_dir = env.get("MEMORY_SPOOL_DIR") or DEFAULT_SPOOL_DIR
    graph: LightRAGStore | None = None
    if with_graph and env.get("LIGHTRAG_WORKING_DIR"):
        try:
            dim = int(env.get("EMBEDDING_DIM") or 1024)  # bge-m3 dense dimension (gguf-models.md; UNVERIFIED here)
        except ValueError as exc:
            raise MemoryStoreError(f"EMBEDDING_DIM={env.get('EMBEDDING_DIM')!r} is not an integer") from exc
        orch = (env.get("ORCH_URL") or f"http://127.0.0.1:{env.get('ORCH_PORT') or 8800}").rstrip("/")
        graph = LightRAGStore(
            env["LIGHTRAG_WORKING_DIR"],
            llm_base_url=f"{orch}/internal/v1",
            llm_model=env.get("ATLAS_CLASSIFIER_ENGINE") or "router-qwen3.5-4b",
            embedding_url=embedding_url,
            embedding_model=env.get("EMBEDDING_MODEL") or "embed-bge-m3",
            embedding_dim=dim,
            sessions=sessions,
            freeze_flag=freeze_flag,
            spool_dir=spool_dir,
            tiktoken_cache_dir=env.get("TIKTOKEN_CACHE_DIR") or None,
        )
    return MemoryStore(backend, embed, sessions=sessions, freeze_flag=freeze_flag, graph=graph, spool_dir=spool_dir)
