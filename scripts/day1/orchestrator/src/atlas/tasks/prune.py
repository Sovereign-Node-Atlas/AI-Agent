"""The 72-hour semantic pruning sweep (Section 9.6; atlas-prune.timer -> `atlas-admin enqueue prune`).

Rule (9.6): permanent facts stay anchored. Temporal operational data (a mutex notification from Tuesday, a chat turn,
a Sentinel pulse) is removed from the active vector collections and the graph and compressed into a dated archive
under /srv/cold. Scars are exempt (9.4). Vault-tagged content is never written to memory in the first place (10.5),
so it is never here: the sweep ASSERTS that. A document carrying a vault marker is an invariant breach: it is
recorded as a strike, DELETED from the collection without being archived (10.5: such content must not be in memory
at all, and an archive under /srv/cold is in the restic include set), and the Principal is told through ntfy
("ATLAS prune: vault invariant breach"); fix round.

What "temporal" means in code (atlas.memory sets the fields on every write): `temporal == True` and either
`expires_at < now` (the writer gave a TTL) or `ts < now - PRUNE_WINDOW_HOURS` (default 72). Collections swept:
corporate, estate, documents_corporate, documents_estate, sentinel. Graph documents come from the LightRAG index the
memory wrapper keeps (doc ids with the same fields). Expired `chat-turn` documents (the raw turns atlas.api writes
with D9's 90-day TTL) are DELETED WITHOUT ARCHIVE (fix round 2): D9 says summarised then purged, and /srv/cold is in
restic's include set, so an archived turn would outlive the purge; the chat summary is the retained form.

Archive: `/srv/cold/prune/prune-<UTC stamp>.tar.zst` holding one JSON file per collection and one for the graph,
written with Python's `compression.zstd` (3.14+) when present, else the `zstd` binary, else (fix round) stdlib
`tarfile` in xz mode as `.tar.xz` (no Day 1 step installs zstd; Section 9.6 fixes the place, not the format). Every
path reads the archive back and compares before anything is deleted; nothing is deleted before the archive verifies.
The archive holds memory CONTENT in plaintext, so (fix round 4) /srv/cold/prune is created 0750 and every archive is
CREATED 0640 through os.open(O_CREAT|O_EXCL) or a 0600 temporary file renamed into place: no process-umask window.

Counts are what the store reports (fix round 4): MemoryStore.delete returns 0 and SPOOLS the delete when the AEGIS
freeze flag has been up longer than freeze_wait_s, so a sweep that overlaps a long backup records those documents
under `spooled` ("still active until the thaw replays them"), never under `purged`/`moved`.
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
from collections.abc import Callable, Sequence
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
    purged: dict[str, int] = field(default_factory=dict)  # expired chat turns deleted WITHOUT archive (D9)
    spooled: dict[str, int] = field(default_factory=dict)  # deletions spooled behind the AEGIS freeze, replayed at thaw
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
            "purged": self.purged,
            "spooled": self.spooled,
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
    """The ONE hemisphere a swept collection is bound to (10.1). Every swept collection is single-bound (sentinel is
    estate-only since fix round 2; scars, the only shared collection, is exempt), so an ambiguous binding here is a
    configuration error worth stopping on, never a silent pick."""
    bound = COLLECTION_HEMISPHERES[collection]
    if len(bound) != 1:
        raise MemoryStoreError(f"collection {collection!r} is bound to {sorted(bound)}; the sweep needs one hemisphere")
    return next(iter(bound))


def _purge_without_archive(meta: dict[str, Any]) -> bool:
    """D9 (10.4): a chat is summarised at 90 days and PURGED. The raw `chat-turn` documents atlas.api writes carry
    that same 90-day TTL, so when they expire they are deleted, never archived under /srv/cold (which restic backs
    up): the summary is the retained form (fix round 2)."""
    return str(meta.get("kind", "")) == "chat-turn"


def _collect_expired(
    store: MemoryStore,
    collection: str,
    now: float,
    window_hours: float,
    result: PruneResult,
    breaches: dict[str, list[str]],
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
                breaches.setdefault(collection, []).append(h.id)
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


def _zstd_available() -> bool:
    try:
        import compression.zstd  # type: ignore[import-not-found]  # noqa: F401  # Python 3.14+
    except ImportError:
        return bool(shutil.which("zstd"))
    return True


ARCHIVE_DIR_MODE = 0o750
ARCHIVE_FILE_MODE = 0o640


def _private_archive_dir(path: Path) -> None:
    """The archive directory, created with its final mode (the umask may only tighten it) and re-chmodded when it
    already existed wider: the archives hold memory content (CONVENTIONS.md §2)."""
    path.mkdir(mode=ARCHIVE_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, ARCHIVE_DIR_MODE)


def _open_new_private(path: Path) -> int:
    """O_CREAT|O_EXCL 0640: the archive is never world-readable for even an instant, and an existing file (a second
    sweep in the same second) is a loud error, never overwritten."""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, ARCHIVE_FILE_MODE)
    except FileExistsError as exc:
        raise RuntimeError(f"archive {path} already exists; refusing to overwrite it") from exc


def write_archive(path: Path, members: dict[str, Any]) -> Path:
    """members: {name.json: json-serialisable}. Returns the path written (`.tar.xz` when no zstd path exists).
    Verified by reading the archive back byte for byte and comparing the tar listing. Created 0640 in a 0750 directory
    (module docstring)."""
    _private_archive_dir(path.parent)
    if not _zstd_available():
        return _write_archive_xz(path.with_name(path.name.removesuffix(".zst") + ".xz"), members)
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

        with os.fdopen(_open_new_private(path), "wb") as out:
            out.write(zstd.compress(raw))
        with zstd.open(path, "rb") as fh:
            back = fh.read()
    except ImportError:
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "archive.tar"
            plain.write_bytes(raw)
            # zstd writes its output with the process umask: write to a 0600 temporary file IN the archive directory
            # and rename it into place (same filesystem; the final name never exists in a wider mode).
            fd, tmp_name = tempfile.mkstemp(prefix=".prune-", suffix=".tmp", dir=str(path.parent))
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                proc = subprocess.run(
                    ["zstd", "-q", "-f", "-o", str(tmp), str(plain)],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"zstd failed: {proc.stderr.strip()[:300]}") from None
                os.chmod(tmp, ARCHIVE_FILE_MODE)
                if path.exists():
                    raise RuntimeError(f"archive {path} already exists; refusing to overwrite it")
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            back = subprocess.run(
                ["zstd", "-d", "-q", "-c", str(path)], capture_output=True, timeout=600, check=False
            ).stdout
    if back != raw:
        raise RuntimeError(f"archive {path} does not read back identically; nothing was deleted")
    with tarfile.open(fileobj=io.BytesIO(back), mode="r") as tar:
        names = set(tar.getnames())
    if names != set(members):
        raise RuntimeError(f"archive {path} lists {sorted(names)}, expected {sorted(members)}")
    os.chmod(path, ARCHIVE_FILE_MODE)
    return path


def _write_archive_xz(path: Path, members: dict[str, Any]) -> Path:
    """The stdlib fallback: tar + xz through `tarfile` on a descriptor opened 0640 (O_EXCL), then a full read-back of
    every member."""
    _private_archive_dir(path.parent)
    with os.fdopen(_open_new_private(path), "wb") as out, tarfile.open(fileobj=out, mode="w:xz") as tar:
        for name, obj in members.items():
            data = json.dumps(obj, ensure_ascii=False, indent=1, default=str).encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    with tarfile.open(path, mode="r:xz") as tar:
        names = set(tar.getnames())
        for name, obj in members.items():
            fh = tar.extractfile(name)
            back = fh.read() if fh is not None else b""
            if back != json.dumps(obj, ensure_ascii=False, indent=1, default=str).encode("utf-8"):
                raise RuntimeError(f"archive {path}: member {name} does not read back identically; nothing was deleted")
    if names != set(members):
        raise RuntimeError(f"archive {path} lists {sorted(names)}, expected {sorted(members)}")
    os.chmod(path, ARCHIVE_FILE_MODE)
    return path


def _delete_counted(
    store: MemoryStore, col: str, hits: Sequence[Hit], result: PruneResult, counter: dict[str, int], what: str
) -> int:
    """store.delete's RETURN VALUE is the truth (fix round 4): 0 with hits present means the delete was spooled behind
    the AEGIS freeze and the documents are still active until the thaw replays it; said in the log and in
    `result.spooled`, never counted as done."""
    n = store.delete(col, [h.id for h in hits], hemisphere=_hemisphere_for(col))
    if n == 0 and hits:
        result.spooled[col] = result.spooled.get(col, 0) + len(hits)
        log.warning(
            "prune: %d %s deletion(s) in %s spooled behind the AEGIS freeze, replayed at thaw (active until then)",
            len(hits),
            what,
            col,
        )
        return 0
    counter[col] = counter.get(col, 0) + n
    return n


def run_sweep(
    store: MemoryStore,
    cold_dir: str | Path,
    *,
    ledger: Ledger | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: float | None = None,
    task_id: str | None = None,
    collections: Sequence[str] = SWEPT_COLLECTIONS,
    notify: Callable[..., Any] | None = None,
) -> PruneResult:
    now = now or time.time()
    result = PruneResult(ts=now, window_hours=window_hours)
    to_move: dict[str, list[Hit]] = {}
    to_purge: dict[str, list[Hit]] = {}
    breaches: dict[str, list[str]] = {}
    for col in collections:
        if col in EXEMPT_COLLECTIONS:
            continue
        try:
            expired = _collect_expired(store, col, now, window_hours, result, breaches)
        except MemoryStoreError as exc:
            result.errors.append(f"{col}: {exc}")
            log.error("prune: cannot read %s: %s", col, exc)
            continue
        to_move[col] = [h for h in expired if not _purge_without_archive(h.metadata)]
        to_purge[col] = [h for h in expired if _purge_without_archive(h.metadata)]
    for col, hits in to_purge.items():
        if not hits:
            continue
        try:
            n = _delete_counted(store, col, hits, result, result.purged, "chat-turn")
            if n:
                log.info("prune: %d expired chat turn(s) purged from %s without archive (D9, 10.4)", n, col)
        except MemoryStoreError as exc:
            result.errors.append(f"purge {col}: {exc}")
            log.error("prune: %s", result.errors[-1])
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
        # 10.5: vault content must never be in memory. Record a strike (9.4 automatic input), DELETE it without an
        # archive (an archive would carry it into /srv/cold and the backup), and tell the Principal.
        msg = f"vault-marked documents found in active memory: {result.invariant_breaches[:10]}"
        log.error("prune INVARIANT BREACH: %s", msg)
        if ledger is not None:
            ledger.insert_strike(task_id=task_id, kind="prune-invariant", description=msg, source="prune")
        purged = 0
        for col, ids in breaches.items():
            try:
                purged += store.delete(col, ids, hemisphere=_hemisphere_for(col))
            except MemoryStoreError as exc:
                result.errors.append(f"vault breach purge {col}: {exc}")
                log.error("prune: %s", result.errors[-1])
        if notify is None:
            from atlas.tasks import notify as _notify

            notify = _notify
        try:
            notify(
                f"{len(result.invariant_breaches)} vault-marked document(s) were found in active memory and "
                f"{purged} deleted without archive; strike recorded. Ids: {', '.join(result.invariant_breaches[:5])}",
                title="ATLAS prune: vault invariant breach",
                priority="high",
                tags=["warning"],
            )
        except Exception as exc:  # the breach is already in the ledger and the log
            log.error("prune: ntfy notice not sent: %s", exc)
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
            _delete_counted(store, col, hits, result, result.moved, "archived")
        except MemoryStoreError as exc:
            result.errors.append(f"delete {col}: {exc} (archived at {path}, still active)")
            log.error("prune: %s", result.errors[-1])
    for r in graph_rows:
        try:
            if graph.delete_document(r.doc_id):  # type: ignore[union-attr]  # False = spooled for the AEGIS thaw
                result.graph_moved += 1
            else:
                result.spooled["graph"] = result.spooled.get("graph", 0) + 1
        except Exception as exc:
            result.errors.append(f"graph delete {r.doc_id}: {exc}")
            log.error("prune: %s", result.errors[-1])
    log.info(
        "prune: moved %d vector docs and %d graph docs older than %.0f h to %s (%d deletion(s) spooled behind the "
        "AEGIS freeze)",
        sum(result.moved.values()),
        result.graph_moved,
        window_hours,
        path,
        sum(result.spooled.values()),
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
