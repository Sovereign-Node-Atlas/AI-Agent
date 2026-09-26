"""The 72-hour semantic pruning sweep (Section 9.6; atlas-prune.timer -> `atlas-admin enqueue prune`).

Rule (9.6): permanent facts stay anchored. Temporal operational data (a mutex notification from Tuesday, a chat turn,
a Sentinel pulse) is removed from the active vector collections and the graph and compressed into a dated archive
under /srv/cold. Scars are exempt (9.4). Vault-tagged content is never written to memory in the first place (10.5),
so it is never here: the sweep ASSERTS that (a document carrying a vault marker is an invariant breach, recorded as a
strike, never archived quietly).

What "temporal" means in code (atlas.memory sets the fields on every write): `temporal == True` and either
`expires_at < now` (the writer gave a TTL) or `ts < now - PRUNE_WINDOW_HOURS` (default 72). Collections swept:
corporate, estate, documents_corporate, documents_estate, sentinel. Graph documents come from the LightRAG index the
memory wrapper keeps (doc ids with the same fields).

Archive: `/srv/cold/prune/<UTC stamp>.tar.zst` holding one JSON file per collection and one for the graph, written
with Python's `compression.zstd` (3.14+) when present, else `tar --zstd` (needs the zstd package; UNVERIFIED that
Ubuntu 26.04's tar/zstd are installed by default: the sweep fails loudly when neither path works, nothing is
deleted before the archive exists and verifies).
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from celery import shared_task

from atlas.ledger import Ledger
from atlas.memory import COLLECTION_HEMISPHERES, Hit, MemoryStore, MemoryStoreError

log = logging.getLogger("atlas.prune")

SWEPT_COLLECTIONS: tuple[str, ...] = ("corporate", "estate", "documents_corporate", "documents_estate", "sentinel")
EXEMPT_COLLECTIONS: tuple[str, ...] = ("scars",)  # Section 9.4
DEFAULT_WINDOW_HOURS = 72.0
PAGE = 500

__all__ = ["PruneResult", "archive_path", "is_expired", "prune_sweep", "run_sweep", "write_archive"]


@dataclass
class PruneResult:
    ts: float
    window_hours: float
    archive: str = ""
    moved: dict[str, int] = field(default_factory=dict)
    graph_moved: int = 0
    invariant_breaches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.moved.values()) + self.graph_moved

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "window_hours": self.window_hours,
            "archive": self.archive,
            "moved": self.moved,
            "graph_moved": self.graph_moved,
            "total": self.total,
            "invariant_breaches": self.invariant_breaches,
            "errors": self.errors,
        }


def is_expired(meta: dict[str, Any], now: float, window_hours: float) -> bool:
    if not meta.get("temporal"):
        return False
    exp = meta.get("expires_at")
    if isinstance(exp, (int, float)):
        return float(exp) < now
    ts = meta.get("ts")
    return isinstance(ts, (int, float)) and float(ts) < now - window_hours * 3600.0


def _vault_marked(meta: dict[str, Any]) -> bool:
    return bool(meta.get("vault")) or str(meta.get("tags", "")).lower().find("vault") >= 0


def _hemisphere_for(collection: str) -> str:
    return sorted(COLLECTION_HEMISPHERES[collection])[0]


def _collect_expired(
    store: MemoryStore, collection: str, now: float, window_hours: float, result: PruneResult
) -> list[Hit]:
    hemi = _hemisphere_for(collection)
    expired: list[Hit] = []
    offset = 0
    while True:
        page = store.get(collection, hemisphere=hemi, where={"temporal": True}, limit=PAGE, offset=offset)
        if not page:
            break
        for h in page:
            if _vault_marked(h.metadata):
                result.invariant_breaches.append(f"{collection}:{h.id}")
                continue
            if is_expired(h.metadata, now, window_hours):
                expired.append(h)
        if len(page) < PAGE:
            break
        offset += len(page)
    return expired


def archive_path(cold_dir: str | Path, now: float) -> Path:
    stamp = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%dT%H%M%SZ")
    return Path(cold_dir) / "prune" / f"prune-{stamp}.tar.zst"


def write_archive(path: Path, members: dict[str, Any]) -> Path:
    """members: {name.json: json-serialisable}. Verified by reading the tar listing back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, obj in members.items():
            data = json.dumps(obj, ensure_ascii=False, indent=1, default=str).encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    raw = buf.getvalue()
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+

        path.write_bytes(zstd.compress(raw))
        with zstd.open(path, "rb") as fh:
            back = fh.read()
    except ImportError:
        if not shutil.which("tar") or not shutil.which("zstd"):
            raise RuntimeError(
                "neither Python's compression.zstd (3.14+) nor the zstd binary is available; install "
                "the `zstd` package so the prune can write tar.zst archives (Section 9.6)"
            ) from None
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "archive.tar"
            plain.write_bytes(raw)
            proc = subprocess.run(
                ["zstd", "-q", "-f", "-o", str(path), str(plain)],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"zstd failed: {proc.stderr.strip()[:300]}") from None
            back = subprocess.run(
                ["zstd", "-d", "-q", "-c", str(path)], capture_output=True, timeout=600, check=False
            ).stdout
    if back != raw:
        raise RuntimeError(f"archive {path} does not read back identically; nothing was deleted")
    with tarfile.open(fileobj=io.BytesIO(back), mode="r") as tar:
        names = set(tar.getnames())
    if names != set(members):
        raise RuntimeError(f"archive {path} lists {sorted(names)}, expected {sorted(members)}")
    os.chmod(path, 0o640)
    return path


def run_sweep(
    store: MemoryStore,
    cold_dir: str | Path,
    *,
    ledger: Ledger | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: float | None = None,
    task_id: str | None = None,
    collections: Sequence[str] = SWEPT_COLLECTIONS,
) -> PruneResult:
    now = now or time.time()
    result = PruneResult(ts=now, window_hours=window_hours)
    to_move: dict[str, list[Hit]] = {}
    for col in collections:
        if col in EXEMPT_COLLECTIONS:
            continue
        try:
            to_move[col] = _collect_expired(store, col, now, window_hours, result)
        except MemoryStoreError as exc:
            result.errors.append(f"{col}: {exc}")
            log.error("prune: cannot read %s: %s", col, exc)
    graph_rows = []
    graph = store.graph
    if graph is not None:
        try:
            graph_rows = [
                r
                for r in graph.index_rows(temporal_only=True)
                if (r.expires_at is not None and r.expires_at < now)
                or (r.expires_at is None and r.ts < now - window_hours * 3600.0)
            ]
        except Exception as exc:
            result.errors.append(f"graph index: {exc}")
            log.error("prune: cannot read the graph index: %s", exc)
    if result.invariant_breaches:
        # 10.5: vault content must never be in memory. Record a strike (9.4 automatic input), do not archive it.
        msg = f"vault-marked documents found in active memory: {result.invariant_breaches[:10]}"
        log.error("prune INVARIANT BREACH: %s", msg)
        if ledger is not None:
            ledger.insert_strike(task_id=task_id, kind="prune-invariant", description=msg, source="prune")
    if not any(to_move.values()) and not graph_rows:
        log.info("prune: nothing older than %.0f h to move", window_hours)
        return result
    members: dict[str, Any] = {}
    for col, hits in to_move.items():
        if hits:
            members[f"{col}.json"] = [{"id": h.id, "document": h.document, "metadata": h.metadata} for h in hits]
    if graph_rows:
        members["graph.json"] = [
            {"doc_id": r.doc_id, "ts": r.ts, "hemisphere": r.hemisphere, "expires_at": r.expires_at, "meta": r.meta}
            for r in graph_rows
        ]
    members["manifest.json"] = {
        "ts": now,
        "window_hours": window_hours,
        "collections": {c: len(h) for c, h in to_move.items()},
        "graph": len(graph_rows),
        "task_id": task_id,
    }
    path = write_archive(archive_path(cold_dir, now), members)
    result.archive = str(path)
    # Only now, with the archive verified, remove from the active stores.
    for col, hits in to_move.items():
        if not hits:
            continue
        try:
            store.delete(col, [h.id for h in hits], hemisphere=_hemisphere_for(col))
            result.moved[col] = len(hits)
        except MemoryStoreError as exc:
            result.errors.append(f"delete {col}: {exc} (archived at {path}, still active)")
            log.error("prune: %s", result.errors[-1])
    for r in graph_rows:
        try:
            graph.delete_document(r.doc_id)  # type: ignore[union-attr]
            result.graph_moved += 1
        except Exception as exc:
            result.errors.append(f"graph delete {r.doc_id}: {exc}")
            log.error("prune: %s", result.errors[-1])
    log.info(
        "prune: moved %d vector docs and %d graph docs older than %.0f h to %s",
        sum(result.moved.values()),
        result.graph_moved,
        window_hours,
        path,
    )
    return result


@shared_task(name="atlas.tasks.prune_sweep", bind=True)
def prune_sweep(self: Any) -> dict[str, Any]:
    from atlas.memory import build_memory_store
    from atlas.tasks import TaskRecord

    rec = TaskRecord(self.request.id, "prune")
    env = os.environ
    try:
        window = float(env.get("PRUNE_WINDOW_HOURS") or DEFAULT_WINDOW_HOURS)
        store = build_memory_store()
        result = run_sweep(
            store, env.get("COLD_DIR") or "/srv/cold", ledger=rec.ledger, window_hours=window, task_id=self.request.id
        )
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    if result.errors:
        rec.failed("; ".join(result.errors))
        raise RuntimeError("prune finished with errors: " + "; ".join(result.errors))
    return rec.done(result.as_dict())
