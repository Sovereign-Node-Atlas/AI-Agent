"""V15: the approval gate (Sections 16.1 rule 3, 16.2, 6.2, 9.2; CONVENTIONS.md §6 Phase 2 gate).

Claim proved (verify/v15-approval-gate.sh): a standard-tier email is held and not sent; a routine-tier
acknowledgement is sent through the (stubbed) Sender and logged in the ledger; a sensitive item cannot be approved
without a cross-check record. No live service: Ledger(':memory:'), StubSender, StubNotifier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.approval import (
    ApprovalItem,
    ApprovalQueue,
    CrossCheckRecord,
    CrossCheckRequired,
    NotPending,
    SendError,
    StubNotifier,
    StubSender,
)
from atlas.governance import Register
from atlas.ledger import Ledger
from atlas.personas import PersonaRegistry, load_persona_registry

FIXTURES = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture
def ledger() -> Ledger:
    db = Ledger(":memory:")
    db.init_db()
    yield db  # type: ignore[misc]
    db.close()


@pytest.fixture(scope="module")
def personas() -> PersonaRegistry:
    return load_persona_registry(FIXTURES)


@pytest.fixture
def gate(ledger: Ledger, personas: PersonaRegistry) -> tuple[ApprovalQueue, StubSender, StubNotifier]:
    sender, notifier = StubSender(), StubNotifier()
    return ApprovalQueue(ledger, sender, notifier, personas=personas), sender, notifier


Gate = tuple[ApprovalQueue, StubSender, StubNotifier]


def _email(tier: str, persona: str, body: str, recipient: str = "counterpart@example.com",
           **kw: object) -> ApprovalItem:
    return ApprovalItem(tier=tier, persona=persona, recipient=recipient, body=body, kind="email",
                        **kw)  # type: ignore[arg-type]


# --- V15 --------------------------------------------------------------------------------------------------------------


def test_standard_tier_email_is_held_and_not_sent(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("standard", "helena", "Thank you for the enquiry; our position on the licence is...",
                               subject="Licence position", reason="substantive reply"))
    assert item.status == "held" and item.is_pending and item.id is not None
    assert sender.sent == []  # nothing left the node
    assert len(notifier.notices) == 1 and f"approval #{item.id}" in notifier.notices[0][0]
    row = ledger.get_approval(item.id)
    assert row is not None and row["status"] == "held" and row["tier"] == "standard" and row["persona"] == "helena"
    assert row["draft"].startswith("Thank you for the enquiry")
    assert [p.id for p in queue.pending()] == [item.id]


def test_routine_acknowledgement_is_sent_and_logged(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("routine", "eleanor", "Confirming Tuesday 10:00 at the office.", subject="Re: Tuesday"))
    assert item.status == "auto-sent" and item.was_sent and item.delivery_ref == "stub-1"
    assert [s.body for s in sender.sent] == ["Confirming Tuesday 10:00 at the office."]
    assert notifier.notices == []  # routine items are logged for review, not announced
    row = ledger.get_approval(item.id)
    assert row is not None and row["status"] == "auto-sent" and row["decided_at"] is not None
    assert "delivery stub-1" in row["note"]
    task = ledger.get_task(item.task_id)
    assert task is not None and task["kind"] == "approval" and task["status"] == "done"


def test_sensitive_item_needs_a_cross_check_before_approval(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("sensitive", "gideon", "Our client's position on the indemnity clause is...",
                               recipient="opposing.counsel@example.com", subject="Indemnity",
                               reason="Clause 12 exposes the Principal to uncapped liability."))
    assert item.status == "held"
    assert "reasoning: Clause 12" in notifier.notices[0][0]  # 16.2: flagged with the director's reasoning
    with pytest.raises(CrossCheckRequired):
        queue.approve(item.id)
    assert sender.sent == []
    assert ledger.get_approval(item.id)["status"] == "held"

    checked = queue.attach_cross_check(item.id, CrossCheckRecord(engine="nemotron-3-super", persona="arthur",
                                                                 verdict="agree", notes="Clause reading confirmed."))
    assert checked.cross_check is not None and checked.cross_check.task_id
    # The record is in the ledger, not in memory: a fresh queue over the same ledger still sees it.
    fresh = ApprovalQueue(ledger, sender, notifier)
    assert fresh.get(item.id).cross_check is not None
    sent = fresh.approve(item.id, decided_by="principal", note="go")
    assert sent.status == "sent" and sent.delivery_ref == "stub-1"
    assert [s.id for s in sender.sent] == [item.id]
    row = ledger.get_approval(item.id)
    assert row["status"] == "sent" and row["decided_by"] == "principal" and "approved by principal" in row["note"]


# --- the rest of the gate --------------------------------------------------------------------------------------------


def test_reject_and_double_decisions(gate: Gate, ledger: Ledger) -> None:
    queue, sender, _ = gate
    item = queue.submit(_email("standard", "silas", "Attached is the revised forecast."))
    rejected = queue.reject(item.id, note="not yet")
    assert rejected.status == "rejected" and "not yet" in rejected.note
    assert ledger.get_task(item.task_id)["status"] == "cancelled"
    with pytest.raises(NotPending):
        queue.approve(item.id)
    with pytest.raises(NotPending):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree"))
    assert sender.sent == []


def test_persona_external_tier_is_a_floor(gate: Gate) -> None:
    queue, sender, _ = gate
    # Gideon speaks externally at sensitive tier (6.2); a "routine" item from him is raised, not auto-sent.
    item = queue.submit(_email("routine", "gideon", "Acknowledging receipt of the term sheet."))
    assert item.tier == "sensitive" and item.status == "held"
    assert "6.2 floor" in item.note
    assert sender.sent == []
    # Victor is routine tier (6.2): a routine acknowledgement goes out.
    ok = queue.submit(_email("routine", "victor", "Car confirmed for 06:30."))
    assert ok.status == "auto-sent"


def test_register_is_set_on_submit(gate: Gate) -> None:
    queue, _, _ = gate
    ext = queue.submit(_email("standard", "helena", "Dear Ms Frost, ..."))
    assert ext.register is Register.EXTERNAL
    internal = queue.submit(ApprovalItem(tier="standard", persona="silas", recipient="ren", body="Result: ok.",
                                         kind="relay", audience="director"))
    assert internal.register is Register.INTERNAL


def test_disclosure_never_auto_sends(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("routine", "eleanor", "As an AI assistant I confirm Tuesday at 10:00."))
    assert item.status == "held" and item.tier == "standard"
    assert item.disclosure is not None and item.disclosure.disclosed
    assert "disclosure" in item.note and "16.1 rule 4" in item.note
    assert sender.sent == []
    assert "disclosure" in notifier.notices[0][0]
    assert ledger.get_approval(item.id)["status"] == "held"


def test_routine_send_failure_is_recorded_and_raised(ledger: Ledger, personas: PersonaRegistry) -> None:
    sender, notifier = StubSender(fail=RuntimeError("smtp down")), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas)
    with pytest.raises(SendError, match="smtp down"):
        queue.submit(_email("routine", "victor", "Car confirmed."))
    rows = ledger.list_approvals("held")
    assert len(rows) == 1 and "routine send failed" in rows[0]["note"]
    assert notifier.notices and notifier.notices[0][0].startswith("SEND FAILED ")


def test_bad_inputs_fail_loudly(gate: Gate) -> None:
    queue, _, _ = gate
    with pytest.raises(Exception, match="tier"):
        queue.submit(_email("urgent", "helena", "x"))
    with pytest.raises(Exception, match="recipient"):
        queue.submit(_email("standard", "helena", "x", recipient=" "))
    with pytest.raises(ValueError, match="verdict"):
        CrossCheckRecord(engine="e", persona="p", verdict="maybe")


def test_queue_without_personas_applies_no_floor(ledger: Ledger) -> None:
    sender = StubSender()
    queue = ApprovalQueue(ledger, sender)
    item = queue.submit(_email("routine", "gideon", "Received, thank you."))
    assert item.status == "auto-sent" and len(sender.sent) == 1
