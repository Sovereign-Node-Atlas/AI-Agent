"""The approval gate (Sections 16.1 rule 3, 16.2, 16.3 rule 3, 6.2, 9.2; CONVENTIONS.md §7.7, V15).

Every outbound item passes through `ApprovalQueue.submit()`; it is a code path, not a prompt instruction:

    routine     pre-approved categories: sent at once through the Sender, logged in ledger.approvals as auto-sent
    standard    held (status "held") until the Principal approves; a Notifier announces it (16.2 "push through ntfy")
    sensitive   held and flagged with the director's reasoning; `approve()` refuses until a strong cross-check
                record (9.2, a second persona on a different engine) is attached with `attach_cross_check()`

Two more controls live here because they are gate controls, not persona instructions:
  * a persona's Section 6.2 "speaks externally" tier is a floor: an item cannot be submitted below it;
  * an external draft that admits AI nature (governance.disclosure_check, 16.1 rule 4) is never auto-sent; it is
    held whatever its tier, with the hits in the note, for the Principal to see.

Storage is the ledger only (no in-memory state that a restart would lose): approvals rows for the items, and a
`tasks` row of kind "cross-check" (parent_task_id = the approval's task id) for each cross-check record. Only the
Ledger's public API is used. Sending and notifying go through the Sender and Notifier protocols; tests use the
stubs (StubSender, StubNotifier) so nothing leaves the node.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from atlas.config import TIERS
from atlas.governance import DisclosureResult, Register, disclosure_check, register
from atlas.ledger import Ledger, new_task_id
from atlas.personas import PersonaRegistry

log = logging.getLogger("atlas.approval")

__all__ = [
    "ApprovalError",
    "ApprovalItem",
    "ApprovalQueue",
    "CrossCheckRecord",
    "CrossCheckRequired",
    "NotPending",
    "Notifier",
    "SendError",
    "Sender",
    "StubNotifier",
    "StubSender",
]

TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(TIERS)}
CROSS_CHECK_VERDICTS: frozenset[str] = frozenset({"agree", "amended", "disagree"})
STATUS_HELD = "held"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_SENT = "auto-sent"
STATUS_SENT = "sent"


class ApprovalError(RuntimeError):
    """The gate refused; the message says exactly why."""


class CrossCheckRequired(ApprovalError):
    """A sensitive item was approved without a cross-check record (16.2 "strong cross-check applied automatically")."""


class NotPending(ApprovalError):
    """approve()/reject() on an item that is not held."""


class SendError(ApprovalError):
    """The Sender failed; the ledger row says so and nothing pretends the item went out."""


# --- protocols and stubs ----------------------------------------------------------------------------------------------


@dataclass
class ApprovalItem:
    """One outbound item. `reason` is the director's reasoning (16.2 sensitive: "flagged with the director's
    reasoning"); `body` the draft; `audience` sets the register (6.4)."""

    tier: str
    persona: str
    recipient: str
    body: str
    reason: str = ""
    kind: str = "email"
    subject: str | None = None
    audience: str = "external"
    task_id: str | None = None
    id: int | None = None
    status: str | None = None
    register: Register | None = None
    note: str | None = None
    delivery_ref: str | None = None
    disclosure: DisclosureResult | None = field(default=None, repr=False)
    cross_check: CrossCheckRecord | None = None
    decided_by: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_HELD

    @property
    def was_sent(self) -> bool:
        return self.status in {STATUS_AUTO_SENT, STATUS_SENT}


@dataclass(frozen=True)
class CrossCheckRecord:
    """A strong cross-check (9.2): a second persona on a different engine judged the draft."""

    engine: str
    persona: str
    verdict: str  # agree | amended | disagree
    notes: str = ""
    task_id: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in CROSS_CHECK_VERDICTS:
            raise ValueError(f"cross-check verdict {self.verdict!r} not in {sorted(CROSS_CHECK_VERDICTS)}")


class Sender(Protocol):
    """Delivers an approved (or routine) item; returns a delivery reference (message id, path, ...)."""

    def send(self, item: ApprovalItem) -> str: ...


class Notifier(Protocol):
    """Announces waiting items (16.2: "A push notification through ntfy announces items waiting")."""

    def notify(self, message: str, item: ApprovalItem) -> None: ...


class StubSender:
    """Test double: records what would have been sent; `fail` makes every send raise it."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.sent: list[ApprovalItem] = []
        self._fail = fail

    def send(self, item: ApprovalItem) -> str:
        if self._fail is not None:
            raise self._fail
        self.sent.append(item)
        return f"stub-{len(self.sent)}"


class StubNotifier:
    def __init__(self) -> None:
        self.notices: list[tuple[str, ApprovalItem]] = []

    def notify(self, message: str, item: ApprovalItem) -> None:
        self.notices.append((message, item))


class NullNotifier:
    """For contexts with no ntfy configured; the ledger row is still the record."""

    def notify(self, message: str, item: ApprovalItem) -> None:
        log.info("approval notice (no notifier configured): %s", message)


# --- the queue ------------------------------------------------------------------------------------------------------


def _max_tier(a: str, b: str) -> str:
    return a if TIER_ORDER[a] >= TIER_ORDER[b] else b


class ApprovalQueue:
    """The gate. One instance per process over the shared ledger; every method is a single ledger transaction chain."""

    def __init__(self, ledger: Ledger, sender: Sender, notifier: Notifier | None = None, *,
                 personas: PersonaRegistry | None = None, disclosure_guard: bool = True,
                 clock: Callable[[], float] = time.time) -> None:
        self.ledger = ledger
        self.sender = sender
        self.notifier: Notifier = notifier if notifier is not None else NullNotifier()
        self.personas = personas
        self.disclosure_guard = disclosure_guard
        self._clock = clock

    # --- submit (16.2) ------------------------------------------------------------------------------------------------

    def submit(self, item: ApprovalItem) -> ApprovalItem:
        """Route one outbound item through the gate. Returns the item with id, status, register and note set."""
        if item.tier not in TIER_ORDER:
            raise ApprovalError(f"tier {item.tier!r} is not one of {TIERS} (CONVENTIONS.md §8)")
        if not item.recipient.strip():
            raise ApprovalError("an outbound item needs a recipient")
        if not item.body.strip():
            raise ApprovalError("an outbound item needs a body")
        notes: list[str] = []
        tier = item.tier
        # 6.2 floor: a persona never speaks externally below its declared tier.
        if self.personas is not None and item.audience != "principal":
            floor = self.personas.external_tier(item.persona)
            if TIER_ORDER[floor] > TIER_ORDER[tier]:
                notes.append(f"tier raised {tier}->{floor} (Section 6.2 floor for {item.persona})")
                tier = floor
        # 6.4: the register is set here, on the draft, as a property.
        draft = register(item.body, item.audience)
        # 16.1 rule 4: an external admission of AI nature never auto-sends.
        disclosure = disclosure_check(item.body) if (self.disclosure_guard and draft.is_external) else None
        hold_for_disclosure = bool(disclosure and disclosure.disclosed)
        if hold_for_disclosure:
            assert disclosure is not None
            notes.append(f"disclosure: {', '.join(disclosure.hits)} (16.1 rule 4; held, never auto-sent)")
            tier = _max_tier(tier, "standard")
        task_id = item.task_id or new_task_id()
        if item.task_id is None:
            self.ledger.insert_task("approval", task_id=task_id, status="queued", persona=item.persona, tier=tier,
                                    payload={"kind": item.kind, "recipient": item.recipient, "subject": item.subject})
        item = replace(item, tier=tier, task_id=task_id, register=draft.register, disclosure=disclosure)

        if tier == "routine" and not hold_for_disclosure:
            return self._auto_send(item, notes)
        # standard / sensitive (or a disclosure hold): held, announced.
        approval_id = self.ledger.insert_approval(
            task_id=task_id, tier=tier, kind=item.kind, status=STATUS_HELD, persona=item.persona,
            recipient=item.recipient, subject=item.subject, draft=item.body, reasoning=item.reason or None,
            note="; ".join(notes) or None)
        item = replace(item, id=approval_id, status=STATUS_HELD, note="; ".join(notes) or None)
        self._notify_held(item)
        log.info("approval #%s held: tier=%s persona=%s recipient=%s kind=%s", approval_id, tier, item.persona,
                 item.recipient, item.kind)
        return item

    def _auto_send(self, item: ApprovalItem, notes: list[str]) -> ApprovalItem:
        try:
            ref = self.sender.send(item)
        except Exception as exc:  # record the failure, then re-raise loudly
            approval_id = self.ledger.insert_approval(
                task_id=item.task_id, tier=item.tier, kind=item.kind, status=STATUS_HELD, persona=item.persona,
                recipient=item.recipient, subject=item.subject, draft=item.body, reasoning=item.reason or None,
                note="; ".join([*notes, f"routine send failed: {type(exc).__name__}: {exc}; held for a human"]))
            failed = replace(item, id=approval_id, status=STATUS_HELD)
            self._notify_held(failed, prefix="SEND FAILED ")
            raise SendError(f"approval #{approval_id}: routine send to {item.recipient} failed: {exc}") from exc
        approval_id = self.ledger.insert_approval(
            task_id=item.task_id, tier=item.tier, kind=item.kind, status=STATUS_AUTO_SENT, persona=item.persona,
            recipient=item.recipient, subject=item.subject, draft=item.body, reasoning=item.reason or None,
            note="; ".join([*notes, f"delivery {ref}"]))
        if item.task_id:
            self.ledger.update_task(item.task_id, status="done", result={"approval_id": approval_id, "delivery": ref})
        log.info("approval #%s auto-sent (routine): persona=%s recipient=%s delivery=%s", approval_id, item.persona,
                 item.recipient, ref)
        return replace(item, id=approval_id, status=STATUS_AUTO_SENT, delivery_ref=ref, note="; ".join(notes) or None)

    def _notify_held(self, item: ApprovalItem, prefix: str = "") -> None:
        head = item.subject or item.body.strip().splitlines()[0][:80]
        msg = (f"{prefix}[{item.tier}] approval #{item.id} waiting: {item.persona} -> {item.recipient} "
               f"({item.kind}): {head}")
        if item.tier == "sensitive" and item.reason:
            msg += f" | reasoning: {item.reason}"
        if item.note:
            msg += f" | {item.note}"
        try:
            self.notifier.notify(msg, item)
        except Exception as exc:  # a notifier outage must not lose the held item (it is in the ledger)
            log.error("notifier failed for approval #%s: %s", item.id, exc)

    # --- decisions ----------------------------------------------------------------------------------------------------

    def approve(self, approval_id: int, *, decided_by: str = "principal", note: str | None = None) -> ApprovalItem:
        """The Principal taps approve: the item is sent. Sensitive items need a cross-check record first."""
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        if item.tier == "sensitive" and item.cross_check is None:
            raise CrossCheckRequired(f"approval #{approval_id} is sensitive tier and has no cross-check record "
                                     f"(16.2: strong cross-check applied before the Principal approves)")
        decision_note = "; ".join(n for n in (item.note, note, f"approved by {decided_by}") if n)
        self.ledger.decide_approval(approval_id, STATUS_APPROVED, decided_by=decided_by, note=decision_note)
        try:
            ref = self.sender.send(item)
        except Exception as exc:
            self.ledger.decide_approval(approval_id, STATUS_APPROVED, decided_by=decided_by,
                                        note=f"{decision_note}; send failed: {type(exc).__name__}: {exc}")
            raise SendError(f"approval #{approval_id}: send to {item.recipient} failed after approval: {exc}") from exc
        final_note = f"{decision_note}; delivery {ref}"
        self.ledger.decide_approval(approval_id, STATUS_SENT, decided_by=decided_by, note=final_note)
        if item.task_id:
            self.ledger.update_task(item.task_id, status="done", result={"approval_id": approval_id, "delivery": ref})
        log.info("approval #%s approved by %s and sent: delivery=%s", approval_id, decided_by, ref)
        return replace(item, status=STATUS_SENT, delivery_ref=ref, note=final_note, decided_by=decided_by)

    def reject(self, approval_id: int, *, decided_by: str = "principal", note: str | None = None) -> ApprovalItem:
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        final_note = "; ".join(n for n in (item.note, note, f"rejected by {decided_by}") if n)
        self.ledger.decide_approval(approval_id, STATUS_REJECTED, decided_by=decided_by, note=final_note)
        if item.task_id:
            self.ledger.update_task(item.task_id, status="cancelled", error=f"rejected: {note or ''}".strip())
        log.info("approval #%s rejected by %s", approval_id, decided_by)
        return replace(item, status=STATUS_REJECTED, note=final_note, decided_by=decided_by)

    def attach_cross_check(self, approval_id: int, record: CrossCheckRecord) -> ApprovalItem:
        """Attach the strong cross-check (9.2) to a held item; stored as a tasks row of kind "cross-check"."""
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        if not item.task_id:
            raise ApprovalError(f"approval #{approval_id} has no task id; cannot attach a cross-check")
        cc_task = self.ledger.insert_task(
            "cross-check", task_id=record.task_id, status="done", persona=record.persona, engine=record.engine,
            tier=item.tier, parent_task_id=item.task_id,
            payload={"approval_id": approval_id, "verdict": record.verdict, "notes": record.notes})
        stored = replace(record, task_id=cc_task)
        log.info("approval #%s cross-check attached: %s on %s says %s", approval_id, record.persona, record.engine,
                 record.verdict)
        return replace(item, cross_check=stored)

    # --- reads --------------------------------------------------------------------------------------------------------

    def get(self, approval_id: int) -> ApprovalItem:
        row = self.ledger.get_approval(approval_id)
        if row is None:
            raise ApprovalError(f"approval #{approval_id} does not exist")
        return self._from_row(row)

    def pending(self, limit: int = 100) -> list[ApprovalItem]:
        return [self._from_row(r) for r in self.ledger.list_approvals(STATUS_HELD, limit)]

    def _cross_check_for(self, task_id: str | None, approval_id: int) -> CrossCheckRecord | None:
        if not task_id:
            return None
        rows = self.ledger.query(
            "SELECT * FROM tasks WHERE kind = 'cross-check' AND parent_task_id = ? AND status = 'done' "
            "ORDER BY created_at DESC", (task_id,))
        for r in rows:
            payload: dict[str, Any] = json.loads(r["payload_json"]) if r.get("payload_json") else {}
            if payload.get("approval_id") == approval_id:
                return CrossCheckRecord(engine=r.get("engine") or "", persona=r.get("persona") or "",
                                        verdict=payload.get("verdict", "agree"), notes=payload.get("notes", ""),
                                        task_id=r["id"])
        return None

    def _from_row(self, row: dict[str, Any]) -> ApprovalItem:
        approval_id = int(row["id"])
        return ApprovalItem(
            tier=row["tier"], persona=row.get("persona") or "", recipient=row.get("recipient") or "",
            body=row.get("draft") or "", reason=row.get("reasoning") or "", kind=row.get("kind") or "email",
            subject=row.get("subject"), task_id=row.get("task_id"), id=approval_id, status=row.get("status"),
            note=row.get("note"), decided_by=row.get("decided_by"),
            cross_check=self._cross_check_for(row.get("task_id"), approval_id),
        )
