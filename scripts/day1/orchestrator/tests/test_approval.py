"""V15: the approval gate (Sections 16.1 rules 3-5, 16.2, 16.3 rule 3, 6.2, 6.4, 8.5, 9.2; CONVENTIONS.md §6 gate).

Claim proved (verify/v15-approval-gate.sh): a standard-tier email is held and not sent until the Principal approves
it, and then it is sent exactly once; a routine-tier confirmation is sent through the (stubbed) Sender and logged in
the ledger; a sensitive item is cross-checked automatically at submit and cannot be approved without the record.
Also: the 6.2 floor and the pre-approved routine categories are code, not labels; a disclosure or a self-reference
never auto-sends; the never-delegate rewrite runs at the gate on Principal-facing text; internal relays are dispatches,
not approvals; decisions are human-only and claimed atomically; the push notification is a minimal announcement.

No live service: Ledger(':memory:'), StubSender, StubNotifier, and a stub cross-checker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.approval import (
    HUMAN_ACTORS,
    ROUTINE_KINDS,
    ApprovalError,
    ApprovalItem,
    ApprovalQueue,
    CrossCheckRecord,
    CrossCheckRequired,
    NotPending,
    NullNotifier,
    SendError,
    StubNotifier,
    StubSender,
)
from atlas.governance import DECISION_PREFIX, Register
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


def _email(tier: str, persona: str, body: str, recipient: str = "counterpart@example.com", kind: str = "email",
           **kw: object) -> ApprovalItem:
    return ApprovalItem(tier=tier, persona=persona, recipient=recipient, body=body, kind=kind,
                        **kw)  # type: ignore[arg-type]


def _arthur_check(item: ApprovalItem) -> CrossCheckRecord:
    """A stub 9.2 cross-checker: Arthur on nemotron-3-super (a second persona on a different engine)."""
    return CrossCheckRecord(engine="nemotron-3-super", persona="arthur", verdict="agree",
                            notes=f"Clause reading confirmed for #{item.id}.")


# --- V15 --------------------------------------------------------------------------------------------------------------


def test_standard_tier_email_is_held_until_approved_then_sent_once(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("standard", "helena", "Thank you for the enquiry; our position on the licence is...",
                               subject="Licence position", reason="substantive reply"))
    assert item.status == "held" and item.is_pending and item.id is not None
    assert sender.sent == []  # nothing left the node
    assert len(notifier.notices) == 1 and f"approval #{item.id}" in notifier.notices[0][0]
    # 16.2 "announces items waiting": the push is minimal; recipient, subject and draft stay in the ledger row.
    notice = notifier.notices[0][0]
    assert notice == f"[standard] approval #{item.id} waiting: helena (email)"
    assert "Licence position" not in notice and "Thank you" not in notice and "example.com" not in notice
    row = ledger.get_approval(item.id)
    assert row is not None and row["status"] == "held" and row["tier"] == "standard" and row["persona"] == "helena"
    assert row["draft"].startswith("Thank you for the enquiry") and row["subject"] == "Licence position"
    assert [p.id for p in queue.pending()] == [item.id]
    assert ledger.get_task(item.task_id)["status"] == "queued" and ledger.get_task(item.task_id)["engine"]
    # ... until approved (16.3 rule 3): then it goes out exactly once.
    sent = queue.approve(item.id, decided_by="principal")
    assert sent.status == "sent" and sent.was_sent and sent.delivery_ref == "stub-1"
    assert [s.id for s in sender.sent] == [item.id] and sender.sent[0].body == item.body
    assert ledger.get_approval(item.id)["status"] == "sent" and "approved by principal" in sent.note
    assert ledger.get_task(item.task_id)["status"] == "done"
    assert queue.pending() == []
    with pytest.raises(NotPending):
        queue.approve(item.id, decided_by="principal")  # never twice
    assert len(sender.sent) == 1


def test_routine_confirmation_is_sent_and_logged(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("routine", "eleanor", "Confirming Tuesday 10:00 at the office.", subject="Re: Tuesday",
                               kind="confirmation"))
    assert item.status == "auto-sent" and item.was_sent and item.delivery_ref == "stub-1"
    assert [s.body for s in sender.sent] == ["Confirming Tuesday 10:00 at the office."]
    assert notifier.notices == []  # routine items are logged for review, not announced
    row = ledger.get_approval(item.id)
    assert row is not None and row["status"] == "auto-sent" and row["decided_at"] is not None
    assert row["decided_by"] == "gate" and "delivery stub-1" in row["note"]
    task = ledger.get_task(item.task_id)
    assert task is not None and task["kind"] == "approval" and task["status"] == "done"


def test_sensitive_item_is_cross_checked_automatically_and_needs_the_record(ledger: Ledger,
                                                                              personas: PersonaRegistry) -> None:
    # 16.2 sensitive: "strong cross-check applied automatically"; 9.2: a second persona on a different engine.
    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=_arthur_check)
    item = queue.submit(_email("sensitive", "gideon", "Our client's position on the indemnity clause is...",
                               recipient="opposing.counsel@example.com", subject="Indemnity",
                               reason="Clause 12 exposes the Principal to uncapped liability."))
    assert item.status == "held" and item.engine == "gpt-oss-120b"  # the drafting engine (fixture gideon)
    assert item.cross_check is not None and item.cross_check.verdict == "agree" and item.cross_check.task_id
    assert "cross-check: arthur@nemotron-3-super says agree" in item.note
    notice = notifier.notices[0][0]
    assert notice.startswith(f"[sensitive] approval #{item.id} waiting: gideon (email)")
    assert "cross-check: arthur@nemotron-3-super says agree" in notice
    assert "Clause 12" not in notice  # the director's reasoning is flagged in the ledger row, not pushed to a phone
    assert ledger.get_approval(item.id)["reasoning"].startswith("Clause 12")
    assert queue.needs_cross_check() == []
    # The record is in the ledger, not in memory: a fresh queue over the same ledger still sees it and can approve.
    fresh = ApprovalQueue(ledger, sender, notifier, personas=personas)
    assert fresh.get(item.id).cross_check is not None
    sent = fresh.approve(item.id, decided_by="principal", note="go")
    assert sent.status == "sent" and sent.delivery_ref == "stub-1"
    assert [s.id for s in sender.sent] == [item.id]
    row = ledger.get_approval(item.id)
    assert row["status"] == "sent" and row["decided_by"] == "principal" and "approved by principal" in row["note"]


def test_sensitive_item_without_a_cross_check_cannot_be_approved(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate  # no cross_checker wired: the backstop
    item = queue.submit(_email("sensitive", "gideon", "Our client's position on the indemnity clause is...",
                               recipient="opposing.counsel@example.com", subject="Indemnity",
                               reason="Clause 12 exposes the Principal to uncapped liability."))
    assert item.status == "held" and item.cross_check is None and "cross-check: pending" in item.note
    assert [i.id for i in queue.needs_cross_check()] == [item.id]
    with pytest.raises(CrossCheckRequired):
        queue.approve(item.id, decided_by="principal")
    assert sender.sent == [] and ledger.get_approval(item.id)["status"] == "held"
    # 9.2: the record must be a second persona on a different engine.
    with pytest.raises(ApprovalError, match="second persona"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="nemotron-3-super", persona="gideon",
                                                           verdict="agree"))
    with pytest.raises(ApprovalError, match="different engine"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="gpt-oss-120b", persona="arthur", verdict="agree"))
    checked = queue.attach_cross_check(item.id, CrossCheckRecord(engine="nemotron-3-super", persona="arthur",
                                                                 verdict="amended", notes="Cap the indemnity."))
    assert checked.cross_check is not None and checked.cross_check.task_id
    assert "cross-check: arthur@nemotron-3-super says amended: Cap the indemnity." in checked.note
    assert "says amended" in notifier.notices[-1][0] and len(notifier.notices) == 2  # announced again with the verdict
    assert queue.needs_cross_check() == []
    sent = queue.approve(item.id, decided_by="principal")
    assert sent.status == "sent" and [s.id for s in sender.sent] == [item.id]


def test_failed_automatic_cross_check_holds_without_a_record(ledger: Ledger, personas: PersonaRegistry) -> None:
    def broken(item: ApprovalItem) -> CrossCheckRecord:
        raise RuntimeError("meditron slot busy")

    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=broken)
    item = queue.submit(_email("sensitive", "gideon", "Our position is...", recipient="counsel@example.com"))
    assert item.status == "held" and item.cross_check is None
    assert "cross-check failed: RuntimeError: meditron slot busy" in item.note
    with pytest.raises(CrossCheckRequired):
        queue.approve(item.id, decided_by="principal")
    assert [i.id for i in queue.needs_cross_check()] == [item.id] and sender.sent == []


# --- the rest of the gate --------------------------------------------------------------------------------------------


def test_reject_and_double_decisions(gate: Gate, ledger: Ledger) -> None:
    queue, sender, _ = gate
    item = queue.submit(_email("standard", "silas", "Attached is the revised forecast."))
    rejected = queue.reject(item.id, decided_by="principal", note="not yet")
    assert rejected.status == "rejected" and "not yet" in rejected.note
    assert ledger.get_task(item.task_id)["status"] == "cancelled"
    with pytest.raises(NotPending):
        queue.approve(item.id, decided_by="principal")
    with pytest.raises(NotPending):
        queue.reject(item.id, decided_by="principal")
    with pytest.raises(NotPending):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree"))
    assert sender.sent == []


def test_decisions_are_human_only(gate: Gate) -> None:
    # 16.3 rule 3 / 16.2: nothing external executes until the Principal taps approve; no default actor.
    queue, sender, _ = gate
    item = queue.submit(_email("standard", "silas", "Attached is the revised forecast."))
    assert HUMAN_ACTORS == frozenset({"principal"})
    with pytest.raises(ApprovalError, match="not a human actor"):
        queue.approve(item.id, decided_by="celery-worker")
    with pytest.raises(ApprovalError, match="not a human actor"):
        queue.reject(item.id, decided_by="gate")
    with pytest.raises(TypeError):
        queue.approve(item.id)  # type: ignore[call-arg]
    assert sender.sent == [] and queue.get(item.id).status == "held"


def test_approve_claims_the_row_atomically(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two processes over the same SQLite file may both read "held"; only the conditional UPDATE decides who sends.
    queue, sender, _ = gate
    item = queue.submit(_email("standard", "silas", "Attached is the revised forecast."))
    original_get = queue.get

    def racing_get(approval_id: int) -> ApprovalItem:
        read = original_get(approval_id)
        queue._set(approval_id, status="rejected", decided_by="principal")  # the other process decided in between
        return read

    monkeypatch.setattr(queue, "get", racing_get)
    with pytest.raises(NotPending, match="concurrently"):
        queue.approve(item.id, decided_by="principal")
    assert sender.sent == []


def test_persona_external_tier_is_a_floor(gate: Gate) -> None:
    queue, sender, _ = gate
    # Gideon speaks externally at sensitive tier (6.2); a "routine" item from him is raised, not auto-sent.
    item = queue.submit(_email("routine", "gideon", "Acknowledging receipt of the term sheet.", kind="acknowledgement"))
    assert item.tier == "sensitive" and item.status == "held"
    assert "6.2 floor" in item.note
    assert sender.sent == []
    # Victor is routine tier (6.2): a routine confirmation goes out.
    ok = queue.submit(_email("routine", "victor", "Car confirmed for 06:30.", kind="confirmation"))
    assert ok.status == "auto-sent"


def test_routine_is_a_pre_approved_category_not_a_label(gate: Gate, ledger: Ledger) -> None:
    # 16.2: "Meeting scheduling, confirmations, acknowledgements. Pre-approved categories"; a substantive "email"
    # labelled routine by a pipeline step is raised to standard and held.
    queue, sender, notifier = gate
    assert ROUTINE_KINDS == frozenset({"acknowledgement", "scheduling", "confirmation"})
    item = queue.submit(_email("routine", "victor", "Here is our counter-proposal on the villa rate.", kind="email"))
    assert item.status == "held" and item.tier == "standard"
    assert "tier raised routine->standard" in item.note and "not a pre-approved category" in item.note
    assert sender.sent == [] and len(notifier.notices) == 1
    assert ledger.get_approval(item.id)["tier"] == "standard"
    ok = queue.submit(_email("routine", "victor", "Tuesday 10:00 works; booked.", kind="scheduling"))
    assert ok.status == "auto-sent" and [s.id for s in sender.sent] == [ok.id]


def test_register_is_set_on_submit_and_internal_relays_are_not_gated(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    ext = queue.submit(_email("standard", "helena", "Dear Ms Frost, ..."))
    assert ext.register is Register.EXTERNAL and ext.status == "held"
    # 8.5 step 6: a director-to-lead relay is its own dispatch with its own task ID and log entry, not an approval.
    internal = queue.submit(ApprovalItem(tier="standard", persona="silas", recipient="ren", body="Result: ok.",
                                         kind="relay", audience="director", task_id=ext.task_id))
    assert internal.register is Register.INTERNAL and internal.status == "relayed" and internal.id is None
    assert not internal.is_pending and "not an outbound item" in internal.note
    assert sender.sent == [] and len(notifier.notices) == 1  # only the external item was announced
    assert [p.id for p in queue.pending()] == [ext.id]
    relay = ledger.get_task(internal.task_id)
    assert relay is not None and relay["kind"] == "relay" and relay["status"] == "done"
    assert relay["parent_task_id"] == ext.task_id and relay["persona"] == "silas"
    assert len(ledger.list_approvals()) == 1


def test_disclosure_never_auto_sends(gate: Gate, ledger: Ledger) -> None:
    queue, sender, notifier = gate
    item = queue.submit(_email("routine", "eleanor", "As an AI assistant I confirm Tuesday at 10:00.",
                               kind="confirmation"))
    assert item.status == "held" and item.tier == "standard"
    assert item.disclosure is not None and item.disclosure.disclosed
    assert "disclosure" in item.note and "16.1 rule 4" in item.note
    assert sender.sent == []
    notice = notifier.notices[0][0]
    assert notice == f"[standard] approval #{item.id} waiting: eleanor (confirmation)"  # hits stay in the ledger
    assert "As an AI" not in notice and "disclosure" in ledger.get_approval(item.id)["note"]
    assert ledger.get_approval(item.id)["status"] == "held"
    # The system naming itself to an outside recipient is a self-reference the gate stops (16.1 rule 4).
    named = queue.submit(_email("routine", "victor", "Booked. ATLAS will confirm the car on Monday.",
                                kind="confirmation"))
    assert named.status == "held" and "ATLAS" in named.note and sender.sent == []


def test_never_delegate_rewrite_runs_at_the_gate_for_the_principal(gate: Gate, ledger: Ledger) -> None:
    # 16.1 rule 5 / Appendix A: the rewrite pass sits before the gate, on the code path, for Principal-facing text.
    queue, sender, notifier = gate
    item = queue.submit(ApprovalItem(tier="routine", persona="ren", recipient="principal", audience="principal",
                                     kind="reply", body="The lease is ready. Please send me the signed copy."))
    assert item.register is Register.PRINCIPAL and item.status == "auto-sent"
    assert item.rewrite is not None and len(item.rewrite.rewrites) == 1 and item.rewrite.clean
    assert item.body == f"The lease is ready. {DECISION_PREFIX} shall ATLAS send you the signed copy?"
    assert sender.sent[-1].body == item.body and ledger.get_approval(item.id)["draft"] == item.body
    assert "never-delegate: 1 rewrite(s) (16.1 rule 5)" in item.note
    # A class-2 phrasing the pass can only flag holds the item for a human, never auto-sent.
    flagged = queue.submit(ApprovalItem(tier="routine", persona="ren", recipient="principal", audience="principal",
                                        kind="reply",
                                        body="Let me know when you have gathered the receipts from the accountant."))
    assert flagged.status == "held" and flagged.tier == "standard" and flagged.rewrite is not None
    assert flagged.rewrite.flagged == ("Let me know when you have gathered the receipts from the accountant.",)
    assert "task-shaped sentence(s) flagged" in flagged.note and "16.1 rule 5" in flagged.note
    assert [s.id for s in sender.sent] == [item.id] and notifier.notices[-1][1].id == flagged.id
    # External correspondence is not rewritten (asking a counterpart is ordinary) and never names ATLAS by the pass.
    ext = queue.submit(_email("standard", "helena", "Could you please forward the lease to the accountant?"))
    assert ext.rewrite is None and ext.body == "Could you please forward the lease to the accountant?"


def test_routine_send_failure_is_recorded_and_raised(ledger: Ledger, personas: PersonaRegistry) -> None:
    sender = StubSender(fail=RuntimeError("smtp down Bearer abc.def.123\nsecond line with token=xyz"))
    notifier = StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas)
    with pytest.raises(SendError, match="smtp down") as info:
        queue.submit(_email("routine", "victor", "Car confirmed.", kind="confirmation"))
    rows = ledger.list_approvals("held")
    assert len(rows) == 1 and "routine send failed: RuntimeError: smtp down" in rows[0]["note"]
    # The Sender's text is redacted and truncated to its first line before it reaches the ledger or an HTTP body.
    assert "abc.def.123" not in rows[0]["note"] and "[redacted]" in rows[0]["note"]
    assert "second line" not in rows[0]["note"] and "xyz" not in str(info.value)
    assert notifier.notices and notifier.notices[0][0].startswith("SEND FAILED ")
    task = ledger.get_task(rows[0]["task_id"])
    assert task["status"] == "failed" and "routine send failed" in task["error"]  # no ghost "queued" task


def test_approved_but_undelivered_items_are_stalled_and_resendable(ledger: Ledger,
                                                                    personas: PersonaRegistry) -> None:
    sender, notifier = StubSender(fail=RuntimeError("smtp down")), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas)
    item = queue.submit(_email("standard", "helena", "Dear Ms Frost, ..."))
    with pytest.raises(SendError, match="after approval"):
        queue.approve(item.id, decided_by="principal")
    row = ledger.get_approval(item.id)
    assert row["status"] == "approved" and "send failed" in row["note"] and "delivery" not in row["note"]
    assert queue.pending() == [] and [i.id for i in queue.stalled()] == [item.id]
    queue.sender = StubSender()  # the channel is back
    sent = queue.resend(item.id, decided_by="principal")
    assert sent.status == "sent" and ledger.get_approval(item.id)["status"] == "sent" and queue.stalled() == []
    with pytest.raises(NotPending):
        queue.resend(item.id, decided_by="principal")


def test_bad_inputs_fail_loudly(gate: Gate) -> None:
    queue, _, _ = gate
    with pytest.raises(Exception, match="tier"):
        queue.submit(_email("urgent", "helena", "x"))
    with pytest.raises(Exception, match="recipient"):
        queue.submit(_email("standard", "helena", "x", recipient=" "))
    with pytest.raises(ValueError, match="verdict"):
        CrossCheckRecord(engine="e", persona="p", verdict="maybe")
    with pytest.raises(ValueError, match="unknown audience"):
        queue.submit(_email("standard", "helena", "x", audience="everyone"))


def test_gate_controls_cannot_be_left_out(ledger: Ledger, personas: PersonaRegistry,
                                          caplog: pytest.LogCaptureFixture) -> None:
    # 16.1 rule 3: the 6.2 floor and the 16.2 push are gate controls, not options a caller may waive.
    sender, notifier = StubSender(), StubNotifier()
    with pytest.raises(TypeError):
        ApprovalQueue(ledger, sender, notifier)  # type: ignore[call-arg]
    with pytest.raises(ApprovalError, match="persona registry"):
        ApprovalQueue(ledger, sender, notifier, personas=None)  # type: ignore[arg-type]
    with pytest.raises(ApprovalError, match="Notifier"):
        ApprovalQueue(ledger, sender, None, personas=personas)  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="atlas.approval"):
        queue = ApprovalQueue(ledger, sender, NullNotifier(), personas=personas)
        assert any("no ntfy notifier configured" in r.message for r in caplog.records)
        item = queue.submit(_email("standard", "helena", "Dear Ms Frost, ..."))
        assert item.status == "held" and any("nobody was pushed" in r.message for r in caplog.records)
