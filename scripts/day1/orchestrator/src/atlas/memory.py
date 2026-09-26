"""Memory: the Vector Cortex (ChromaDB, Section 10.1) and the graph layer (LightRAG, Section 10.2, D7).

Rules this module is the code path for (rule §7.7: code, not prompt instructions):
  * Hemisphere binding (10.1, 7.3): each collection is bound to a hemisphere; a read OR write from a dispatch whose
    hemisphere is not permitted for that collection raises `HemisphereViolation`. Chroma 1.x has no auth or ACL
    (services-tools.md §2.1 VERIFIED), so this class is the only enforcement.
  * Vault tag (10.5, 11; V18): any write from a session that atlas.vault.SessionTags marks `vault` is DROPPED and
    logged, never queued, never summarised, unless `remember=True` (the Principal's explicit "remember this").
  * AEGIS freeze (9.5): while the freeze flag file exists, writes wait (up to `freeze_wait_s`) and then raise, so
    the restic snapshot sees a quiescent store from every writer in this package.
  * Embeddings come from the resident bge-m3 llama-server (EMBEDDING_URL, /v1/embeddings; gguf-models.md §9) and are
    always passed explicitly to Chroma: the client's default embedding function would download a model from the
    internet, which rule §7.1 forbids.
  * The temporal flag (9.6): every document carries `ts` and `temporal`; `expires_at` when the writer gave a TTL.
    tasks/prune.py sweeps on those fields. Scars (`scars`) are exempt and never carry `temporal`.

Backends are injected so the tests run with a stub (CONVENTIONS.md §7.8): `ChromaLike` / `CollectionLike` are the
subset of the chromadb API this module uses (VERIFIED names: get_or_create_collection, add, query, get, delete, count,
upsert). LightRAG is imported lazily inside `LightRAGStore` so nothing here needs it at import time.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
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
    # Scars are tagged per persona and domain (9.4) and injected before any task in either hemisphere.
    "scars": frozenset({"corporate", "estate"}),
    # Sentinel output sits under Arthur (9.3) but its BLUF entries concern both hemispheres (markets are Silas's).
    "sentinel": frozenset({"corporate", "estate"}),
}
DEFAULT_FREEZE_FLAG = "/run/atlas/aegis-freeze"
DEFAULT_GRAPH_INDEX = "atlas-graph-index.sqlite3"

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
    "llama_embedding_fn",
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
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.embed = embed
        self.sessions = sessions or SessionTags()
        self.freeze_flag = Path(freeze_flag) if freeze_flag else None
        self.freeze_wait_s = freeze_wait_s
        self.graph = graph
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

    def _wait_thaw(self) -> None:
        if not self.is_frozen():
            return
        deadline = self._clock() + self.freeze_wait_s
        log.info("memory write waits: AEGIS freeze flag %s present (Section 9.5)", self.freeze_flag)
        while self.is_frozen():
            if self._clock() >= deadline:
                raise MemoryFrozen(
                    f"AEGIS freeze flag {self.freeze_flag} still present after {self.freeze_wait_s:.0f}s; "
                    "the write is refused, not queued (Section 9.5)"
                )
            self._sleep(1.0)

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
                    # embedding_function=None: vectors are always supplied by us (UNVERIFIED that chromadb-client 1.5.9
                    # never instantiates its default ONNX function when None is passed; a network attempt would be
                    # refused by the allowlist and surface here as an error, never as a silent download).
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
        self._wait_thaw()
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
        out_ids = list(ids) if ids is not None else [f"{collection}-{int(now * 1000)}-{i}" for i in range(len(docs))]
        if len(out_ids) != len(docs):
            raise MemoryStoreError(f"{len(out_ids)} ids for {len(docs)} documents")
        vectors = self.embed(docs)
        col = self.collection(collection)
        try:
            if upsert:
                col.upsert(ids=out_ids, documents=docs, metadatas=metas, embeddings=vectors)
            else:
                col.add(ids=out_ids, documents=docs, metadatas=metas, embeddings=vectors)
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB write to {collection!r} failed: {exc}") from exc
        log.info(
            "memory write: collection=%s docs=%d hemisphere=%s temporal=%s", collection, len(docs), hemisphere, temporal
        )
        return WriteResult(written=True, ids=tuple(out_ids))

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
        self._wait_thaw()
        try:
            self.collection(collection).delete(ids=list(ids))
        except Exception as exc:
            raise MemoryStoreError(f"ChromaDB delete on {collection!r} failed: {exc}") from exc
        return len(ids)

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


class LightRAGStore:
    """LightRAG with insert/query on the local endpoints plus the same vault/freeze/hemisphere rules.

    LLM calls go to the orchestrator's internal OpenAI-shaped endpoint (ORCH_URL/internal/v1), which holds the single
    generation lock of the Engine Arbiter (Section 4.2 rule 3, 9.7 C15) and serves the resident router model (R6:
    extraction on Eleanor's model). Embeddings go straight to the resident bge-m3 server.

    UNVERIFIED LightRAG 1.5.7 API details (services-tools.md §2.2 verified only `openai_complete_if_cache`,
    `initialize_storages` and the env keys): `LightRAG(working_dir=, llm_model_func=, embedding_func=EmbeddingFunc())`,
    `ainsert(text, ids=[...])`, `aquery(q, param=QueryParam(mode=...))`, `adelete_by_doc_id(id)`. Each is called
    exactly as named; a mismatch raises at the first call with LightRAG's own error, never a silent skip.
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
        index_path: str | Path | None = None,
        rag_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.embedding_url = embedding_url
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.sessions = sessions or SessionTags()
        self.freeze_flag = Path(freeze_flag) if freeze_flag else None
        self.index_path = Path(index_path) if index_path else self.working_dir / DEFAULT_GRAPH_INDEX
        self._rag_factory = rag_factory
        self._rag: Any = None
        self._clock = clock
        self._lock = threading.RLock()
        self._index_ready = False

    # --- our own index of what is in the graph (doc ids, temporal flags) ----------------------------------------

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

    # --- LightRAG instance ---------------------------------------------------------------------------------------

    def _build_rag(self) -> Any:
        if self._rag_factory is not None:
            return self._rag_factory()
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
            llm_model_func=llm_func,
            embedding_func=EmbeddingFunc(embedding_dim=self.embedding_dim, max_token_size=8192, func=embed_func),
        )
        _run(rag.initialize_storages())  # README: forgetting this is the common mistake (VERIFIED)
        return rag

    def rag(self) -> Any:
        with self._lock:
            if self._rag is None:
                self._rag = self._build_rag()
            return self._rag

    def _wait_thaw(self) -> None:
        if self.freeze_flag is not None and self.freeze_flag.exists():
            raise MemoryFrozen(f"AEGIS freeze flag {self.freeze_flag} present; graph writes are refused (Section 9.5)")

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
        if hemisphere not in ("corporate", "estate"):
            raise HemisphereViolation(f"graph insert needs a hemisphere, got {hemisphere!r}")
        if not text.strip():
            return WriteResult(written=False, reason="nothing to insert")
        if self.sessions.is_vault(session_id) and not remember:
            log.warning(
                "graph insert DROPPED: doc=%s session=%s reason=vault-tagged (Section 10.5)", doc_id, session_id
            )
            return WriteResult(written=False, dropped=True, reason="session is vault-tagged (Section 10.5)")
        self._wait_thaw()
        rag = self.rag()
        _run(rag.ainsert(text, ids=[doc_id]))
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
        if hemisphere not in ("corporate", "estate"):
            raise HemisphereViolation(f"graph query needs a hemisphere, got {hemisphere!r}")
        rag = self.rag()
        try:
            from lightrag import QueryParam  # type: ignore[import-not-found]

            param = QueryParam(mode=mode)
        except ImportError:
            param = None
        result = _run(rag.aquery(question, param=param) if param is not None else rag.aquery(question))
        return str(result)

    def delete_document(self, doc_id: str) -> None:
        self._wait_thaw()
        rag = self.rag()
        fn = getattr(rag, "adelete_by_doc_id", None)
        if fn is None:
            raise MemoryStoreError(
                "this LightRAG has no adelete_by_doc_id(); the prune cannot remove graph documents "
                "(UNVERIFIED API, services-tools.md §2.2)"
            )
        _run(fn(doc_id))
        conn = self._index()
        try:
            conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()


def _run(coro: Any) -> Any:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside an event loop (the FastAPI thread pool never is; Celery prefork never is): run apart.
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            result["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


# --- production wiring ------------------------------------------------------------------------------------------------


def build_memory_store(
    env: Mapping[str, str] | None = None, *, sessions: SessionTags | None = None, with_graph: bool = True
) -> MemoryStore:
    """From /etc/atlas/memory.env (phase2/04-memory.sh contract: CHROMA_URL, EMBEDDING_URL, EMBEDDING_MODEL,
    EMBEDDING_DIM, LIGHTRAG_WORKING_DIR) and orchestrator.env (ORCH_URL)."""
    env = dict(os.environ if env is None else env)
    chroma_url = env.get("CHROMA_URL", "").strip()
    embedding_url = env.get("EMBEDDING_URL", "").strip()
    if not chroma_url or not embedding_url:
        raise MemoryStoreError("CHROMA_URL / EMBEDDING_URL are not set (memory.env is written by phase2/04-memory.sh)")
    try:
        import chromadb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MemoryStoreError(
            f"chromadb-client is not installed ({exc}); pyproject.toml pins chromadb-client==1.5.9"
        ) from exc
    from urllib.parse import urlsplit

    u = urlsplit(chroma_url)
    host, port = u.hostname or "127.0.0.1", u.port or 8000
    try:
        backend = chromadb.HttpClient(host=host, port=port, ssl=(u.scheme == "https"))  # VERIFIED signature (§2.1)
    except Exception as exc:
        raise MemoryStoreError(f"ChromaDB client for {chroma_url} could not be created: {exc}") from exc
    sessions = sessions or SessionTags(env.get("VAULT_SESSION_FILE") or "/run/atlas/vault-sessions.json")
    embed = llama_embedding_fn(embedding_url, env.get("EMBEDDING_MODEL") or "embed-bge-m3")
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
            freeze_flag=env.get("AEGIS_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG,
        )
    return MemoryStore(
        backend, embed, sessions=sessions, freeze_flag=env.get("AEGIS_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG, graph=graph
    )
