"""Ouroboros Protocol (Section 9.4): strikes in, scars out, scars injected before any task.

  record_strike(persona, domain, context, error, correction)   a strike row in the ledger (9.4 "strike input": manual
        `[LOG STRIKE: name]` or automatic: failed tool call, rejected draft, failing sandbox test, overridden routing
        decision, a failed generation) AND a scar in the `scars` collection tagged to persona and domain, holding
        context, error and the correction that worked. Scars are permanent (never temporal; exempt from the prune).
  retrieve_scars(query, k, threshold)   the closest few scars above a similarity threshold, for layer 4 of the prompt
        (4.4). Not all scars: "a pile of irrelevant corrections degrades quality within months".

Boundary (9.4): a scar changes behaviour through retrieved guidance only. Nothing here edits code or configuration;
that is a proposal for the Principal (16.3 rule 6).

`atlas.tasks.record_strike` is the Celery form (cpu queue) for automatic strikes raised by background work.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from celery import shared_task

from atlas.ledger import Ledger
from atlas.memory import Hit, MemoryStore, MemoryStoreError

log = logging.getLogger("atlas.ouroboros")

STRIKE_KINDS: frozenset[str] = frozenset(
    {
        "manual",
        "failed-tool-call",
        "rejected-draft",
        "failing-sandbox-test",
        "overridden-routing",
        "failed-generation",
        "task-failure",
        "prune-invariant",
    }
)
DEFAULT_K = 3
# Chroma distances are L2 (default space) on bge-m3 unit-length vectors: 0 = identical, ~1.41 = orthogonal. The
# threshold is a distance ceiling; 0.9 keeps clearly related scars and drops the unrelated pile. TODO tune on the node
# against real scars (the value is a defensible default, not a measured one).
DEFAULT_THRESHOLD = 0.9

__all__ = ["DEFAULT_K", "DEFAULT_THRESHOLD", "STRIKE_KINDS", "Scar", "format_scars", "record_strike", "retrieve_scars"]


@dataclass(frozen=True)
class Scar:
    id: str
    persona: str
    domain: str
    context: str
    error: str
    correction: str
    distance: float | None = None
    ts: float | None = None

    def as_prompt_line(self) -> str:
        fix = f" Correction: {self.correction}" if self.correction else " No correction recorded yet."
        return f"- [{self.persona} / {self.domain}] {self.error} Context: {self.context}{fix}"


def scar_text(persona: str, domain: str, context: str, error: str, correction: str) -> str:
    return (
        f"Persona: {persona}\nDomain: {domain}\nError: {error}\nContext: {context}\n"
        f"Correction: {correction or '(none yet)'}"
    )


def record_strike(
    persona: str,
    domain: str,
    context: str,
    error: str,
    correction: str = "",
    *,
    ledger: Ledger,
    memory: MemoryStore | None,
    kind: str = "manual",
    source: str | None = None,
    task_id: str | None = None,
    hemisphere: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Write the strike row and the scar. Returns {strike_id, scar_id, scar_written, reason}.

    The scar write is a normal memory write: a vault-tagged session's strike is recorded in the ledger (it is an
    operational fact) but its scar text, which could carry vault content, is dropped by atlas.memory (10.5).
    """
    if kind not in STRIKE_KINDS:
        raise ValueError(f"strike kind {kind!r} not in {sorted(STRIKE_KINDS)} (Section 9.4 strike input)")
    persona = (persona or "unknown").strip().lower()
    domain = (domain or "general").strip()
    description = f"{error.strip()[:500]} | {context.strip()[:500]}"
    strike_id = ledger.insert_strike(
        task_id=task_id, kind=kind, description=description, source=source, resolution=correction or None
    )
    result: dict[str, Any] = {"strike_id": strike_id, "scar_id": None, "scar_written": False, "reason": ""}
    if memory is None:
        result["reason"] = "no memory store configured; strike recorded in the ledger only"
        log.warning("strike #%d recorded without a scar: %s", strike_id, result["reason"])
        return result
    text = scar_text(persona, domain, context, error, correction)
    scar_id = "scar-" + hashlib.sha256(f"{persona}|{domain}|{error}|{context}".encode()).hexdigest()[:20]
    hemi = hemisphere or ("estate" if persona in ("arthur", "alaric", "minerva", "victor") else "corporate")
    try:
        wr = memory.write(
            "scars",
            [text],
            [
                {
                    "kind": "scar",
                    "persona": persona,
                    "domain": domain,
                    "strike_kind": kind,
                    "error": error[:200],
                    "strike_id": strike_id,
                    "has_correction": bool(correction),
                    "source": source or "",
                }
            ],
            ids=[scar_id],
            hemisphere=hemi,
            session_id=session_id,
            upsert=True,
        )
    except MemoryStoreError as exc:
        result["reason"] = f"scar not written: {exc}"
        log.error("strike #%d: %s", strike_id, result["reason"])
        return result
    if wr.written:
        ledger.resolve_strike(strike_id, correction or "(scar recorded, no correction yet)", scar_id=scar_id)
        result.update({"scar_id": scar_id, "scar_written": True})
        log.info("strike #%d -> scar %s (%s/%s)", strike_id, scar_id, persona, domain)
    else:
        result["reason"] = wr.reason
    return result


def retrieve_scars(
    query: str,
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    memory: MemoryStore,
    hemisphere: str,
    persona: str | None = None,
    domain: str | None = None,
) -> list[Scar]:
    """The closest scars within the distance threshold, optionally narrowed to a persona or domain tag."""
    if not query.strip() or k < 1:
        return []
    where: dict[str, Any] | None = None
    clauses: list[dict[str, Any]] = []
    if persona:
        clauses.append({"persona": persona.lower()})
    if domain:
        clauses.append({"domain": domain})
    if len(clauses) == 1:
        where = clauses[0]
    elif clauses:
        where = {"$and": clauses}
    try:
        hits = memory.query("scars", query, k=max(k * 2, k), hemisphere=hemisphere, where=where)
    except MemoryStoreError as exc:
        log.error("scar retrieval failed (continuing without scars, which is logged, not hidden): %s", exc)
        return []
    out: list[Scar] = []
    for h in hits:
        if h.distance is not None and h.distance > threshold:
            continue
        out.append(_scar_from_hit(h))
        if len(out) >= k:
            break
    return out


def _scar_from_hit(h: Hit) -> Scar:
    fields: dict[str, str] = {}
    for line in h.document.splitlines():
        key, _, value = line.partition(":")
        if _ and key.strip() in ("Persona", "Domain", "Error", "Context", "Correction"):
            fields[key.strip()] = value.strip()
    m = h.metadata
    return Scar(
        id=h.id,
        persona=str(m.get("persona") or fields.get("Persona", "")),
        domain=str(m.get("domain") or fields.get("Domain", "")),
        context=fields.get("Context", ""),
        error=str(fields.get("Error") or m.get("error", "")),
        correction=fields.get("Correction", ""),
        distance=h.distance,
        ts=float(m["ts"]) if isinstance(m.get("ts"), (int, float)) else None,
    )


def format_scars(scars: list[Scar]) -> str:
    """Layer 4 text (4.4): appended last so the cached prefix survives."""
    if not scars:
        return ""
    lines = ["Lessons from earlier mistakes (Ouroboros scars; apply the corrections, do not mention them):"]
    lines += [s.as_prompt_line() for s in scars]
    return "\n".join(lines)


# --- Celery form ------------------------------------------------------------------------------------------------------


@shared_task(name="atlas.tasks.record_strike", bind=True)
def record_strike_task(
    self: Any,
    persona: str,
    domain: str,
    context: str,
    error: str,
    correction: str = "",
    kind: str = "task-failure",
    source: str | None = None,
    parent_task_id: str | None = None,
) -> dict[str, Any]:
    from atlas.memory import build_memory_store
    from atlas.tasks import TaskRecord

    rec = TaskRecord(self.request.id, "record-strike", parent_task_id=parent_task_id)
    memory: MemoryStore | None
    try:
        memory = build_memory_store(with_graph=False)
    except MemoryStoreError as exc:
        log.error("record_strike: memory store unavailable (%s); ledger only", exc)
        memory = None
    try:
        out = record_strike(
            persona,
            domain,
            context,
            error,
            correction,
            ledger=rec.ledger,
            memory=memory,
            kind=kind,
            source=source,
            task_id=parent_task_id or self.request.id,
        )
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    return rec.done(out)


def strike_on_exception(ledger_factory: Callable[[], Ledger]) -> Callable[[str, str, str, BaseException], None]:
    """Small helper for callers that want a one-line 'record an automatic strike for this exception'."""

    def _record(persona: str, domain: str, context: str, exc: BaseException) -> None:
        ledger = ledger_factory()
        try:
            record_strike(
                persona,
                domain,
                context,
                f"{type(exc).__name__}: {exc}",
                ledger=ledger,
                memory=None,
                kind="task-failure",
                source="exception",
            )
        finally:
            ledger.close()

    return _record
