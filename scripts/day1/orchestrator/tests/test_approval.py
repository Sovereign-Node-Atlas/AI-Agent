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
    PRINCIPAL_RECIPIENTS,
    ROUTINE_KINDS,
    SENSITIVE_KINDS,
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
from atlas.config import ConfigError
from atlas.governance import DECISION_PREFIX, Register
from atlas.ledger import Ledger
from atlas.personas import LEAD_MINIMUM_EXTERNAL_TIER, PersonaRegistry, external_tier, load_persona_registry

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


def _inference(ledger: Ledger, persona: str, engine: str, **kw: object) -> str:
    """The done 'cross-check' tasks row a hand-attached record must name (9.2 provenance): what the Celery task
    that ran the second persona on the other engine would have written."""
    return ledger.insert_task("cross-check", status="done", persona=persona, engine=engine, **kw)  # type: ignore[arg-type]


def _swap_check(item: ApprovalItem) -> CrossCheckRecord:
    """A stub 9.2 cross-checker that always swaps: the other lead, on an engine the draft did not use."""
    if item.persona == "arthur":
        return CrossCheckRecord(engine="gpt-oss-120b", persona="ren", verdict="agree")
    engine = "qwen3.5-122b" if item.engine == "nemotron-3-super" else "nemotron-3-super"
    return CrossCheckRecord(engine=engine, persona="arthur", verdict="agree")


def _record(ledger: Ledger, persona: str, engine: str, verdict: str = "agree", notes: str = "") -> CrossCheckRecord:
    return CrossCheckRecord(engine=engine, persona=persona, verdict=verdict, notes=notes,
                            task_id=_inference(ledger, persona, engine))


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
    checked = queue.attach_cross_check(item.id, _record(ledger, "arthur", "nemotron-3-super", verdict="amended",
                                                        notes="Cap the indemnity."))
    assert checked.cross_check is not None and checked.cross_check.task_id
    assert "cross-check: arthur@nemotron-3-super says amended: Cap the indemnity." in checked.note
    assert "says amended" in notifier.notices[-1][0] and len(notifier.notices) == 2  # announced again with the verdict
    assert queue.needs_cross_check() == []
    sent = queue.approve(item.id, decided_by="principal")
    assert sent.status == "sent" and [s.id for s in sender.sent] == [item.id]


def test_hand_attached_cross_check_needs_provenance(gate: Gate, ledger: Ledger) -> None:
    # 16.2 promises a strong cross-check that actually ran; a record attached from outside the gate must name the
    # done 'cross-check' tasks row of that inference, with the same persona and engine. A fabricated "agree" on
    # meditron-70b with no task, a task of another kind, or a task by someone else is refused.
    queue, sender, _ = gate
    item = queue.submit(_email("sensitive", "gideon", "Our position is...", recipient="counsel@example.com"))
    with pytest.raises(ApprovalError, match="provenance"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree"))
    with pytest.raises(ApprovalError, match="provenance"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree",
                                                           task_id="no-such-task"))
    other_kind = ledger.insert_task("relay", status="done", persona="minerva", engine="meditron-70b")
    with pytest.raises(ApprovalError, match="not a done 'cross-check' task"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree",
                                                           task_id=other_kind))
    by_arthur = _inference(ledger, "arthur", "nemotron-3-super")
    with pytest.raises(ApprovalError, match="minerva@meditron-70b"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree",
                                                           task_id=by_arthur))
    unfinished = ledger.insert_task("cross-check", status="running", persona="minerva", engine="meditron-70b")
    with pytest.raises(ApprovalError, match="provenance"):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree",
                                                           task_id=unfinished))
    assert queue.get(item.id).cross_check is None and sender.sent == []
    real = _inference(ledger, "minerva", "meditron-70b")
    ok = queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree",
                                                            task_id=real))
    assert ok.cross_check is not None and ok.cross_check.task_id != real  # the gate's row, pointing at the inference
    gate_row = ledger.get_task(ok.cross_check.task_id)
    assert gate_row["parent_task_id"] == item.task_id
    assert f'"inference_task_id": "{real}"' in gate_row["payload_json"]
    assert '"source": "attached"' in gate_row["payload_json"]
    assert queue.approve(item.id, decided_by="principal").status == "sent"


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
    # A checker that answers on the draft's own engine (or as the drafting persona) is not a 9.2 strong cross-check:
    # the record is refused, the item is held without one, and submit() still returns the item (never a 5xx).
    def same_engine(item: ApprovalItem) -> CrossCheckRecord:
        return CrossCheckRecord(engine=item.engine or "", persona="arthur", verdict="agree")

    lazy = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=same_engine)
    held = lazy.submit(_email("sensitive", "gideon", "Our position is...", recipient="counsel@example.com"))
    assert held.status == "held" and held.cross_check is None and held.register is Register.EXTERNAL
    assert "cross-check refused:" in held.note and "different engine" in held.note
    with pytest.raises(CrossCheckRequired):
        lazy.approve(held.id, decided_by="principal")
    assert sender.sent == []


# --- the rest of the gate --------------------------------------------------------------------------------------------


def test_reject_and_double_decisions(gate: Gate, ledger: Ledger) -> None:
    queue, sender, _ = gate
    item = queue.submit(_email("standard", "silas", "Attached is the revised forecast."))
    assert ledger.list_strikes() == []
    rejected = queue.reject(item.id, decided_by="principal", note="not yet")
    assert rejected.status == "rejected" and "not yet" in rejected.note
    assert ledger.get_task(item.task_id)["status"] == "cancelled"
    # 9.4: a rejected draft is an automatic Ouroboros strike input; the gate is the component that knows.
    strikes = ledger.list_strikes(item.task_id)
    assert len(strikes) == 1 and strikes[0]["kind"] == "rejected-draft" and strikes[0]["source"] == "approval-gate"
    assert f"approval #{item.id} (silas, email, standard) rejected by principal: not yet" == strikes[0]["description"]
    from atlas.tasks.ouroboros import STRIKE_KINDS

    assert "rejected-draft" in STRIKE_KINDS  # the kind the strike taxonomy spells
    with pytest.raises(NotPending):
        queue.approve(item.id, decided_by="principal")
    with pytest.raises(NotPending):
        queue.reject(item.id, decided_by="principal")
    with pytest.raises(NotPending):
        queue.attach_cross_check(item.id, CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree"))
    assert sender.sent == [] and len(ledger.list_strikes()) == 1  # one strike per rejection, not per attempt


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
                                     kind="confirmation", body="The lease is ready. Please send me the signed copy."))
    assert item.register is Register.PRINCIPAL and item.status == "auto-sent"
    assert item.rewrite is not None and len(item.rewrite.rewrites) == 1 and item.rewrite.clean
    assert item.body == f"The lease is ready. {DECISION_PREFIX} shall ATLAS send you the signed copy?"
    assert sender.sent[-1].body == item.body and ledger.get_approval(item.id)["draft"] == item.body
    assert "never-delegate: 1 rewrite(s) (16.1 rule 5)" in item.note
    # A class-2 phrasing the pass can only flag holds the item for a human, never auto-sent.
    flagged = queue.submit(ApprovalItem(tier="routine", persona="ren", recipient="principal", audience="principal",
                                        kind="confirmation",
                                        body="Let me know when you have gathered the receipts from the accountant."))
    assert flagged.status == "held" and flagged.tier == "standard" and flagged.rewrite is not None
    assert flagged.rewrite.flagged == ("Let me know when you have gathered the receipts from the accountant.",)
    assert "task-shaped sentence(s) flagged" in flagged.note and "16.1 rule 5" in flagged.note
    assert [s.id for s in sender.sent] == [item.id] and notifier.notices[-1][1].id == flagged.id
    # External correspondence is not rewritten (asking a counterpart is ordinary) and never names ATLAS by the pass.
    ext = queue.submit(_email("standard", "helena", "Could you please forward the lease to the accountant?"))
    assert ext.rewrite is None and ext.body == "Could you please forward the lease to the accountant?"


def test_routine_category_rule_applies_to_the_principal_register_too(gate: Gate) -> None:
    # 16.2 pre-approved categories are the same for both registers: a substantive "reply" or "email" to the Principal
    # labelled routine is held at standard, not auto-sent on the label (16.3 rule 3).
    queue, sender, _ = gate
    for kind in ("reply", "email"):
        item = queue.submit(ApprovalItem(tier="routine", persona="arthur", recipient="principal", audience="principal",
                                         kind=kind, body="Here is the full analysis of the trust deed."))
        assert item.status == "held" and item.tier == "standard" and "not a pre-approved category" in item.note
    assert sender.sent == []
    ok = queue.submit(ApprovalItem(tier="routine", persona="arthur", recipient="principal", audience="principal",
                                   kind="acknowledgement", body="Noted; the deed is filed."))
    assert ok.status == "auto-sent" and [s.id for s in sender.sent] == [ok.id]


def test_only_a_lead_addresses_the_principal(gate: Gate, ledger: Ledger) -> None:
    # 16.1 rule 1: "The Principal speaks only to Ren or Arthur. Shadow Cabinet output is never surfaced directly;
    # the hemisphere synthesises and speaks." The gate has the registry in hand and refuses a director's item.
    queue, sender, notifier = gate
    for persona, lead in (("silas", "ren"), ("gideon", "ren"), ("minerva", "arthur"), ("victor", "arthur")):
        with pytest.raises(ApprovalError, match=f"only a hemisphere lead speaks to the Principal.*relay to {lead}"):
            queue.submit(ApprovalItem(tier="routine", persona=persona, recipient="principal", audience="principal",
                                      kind="confirmation", body="Done."))
    assert sender.sent == [] and notifier.notices == [] and ledger.list_approvals() == []
    assert ledger.list_tasks() == []  # refused before anything was recorded
    ok = queue.submit(ApprovalItem(tier="routine", persona="ren", recipient="principal", audience="principal",
                                   kind="confirmation", body="Done."))
    assert ok.status == "auto-sent"
    # A director's result reaches the lead as an internal relay (8.5 step 7), which is the path the message names.
    relay = queue.submit(ApprovalItem(tier="routine", persona="silas", recipient="ren", audience="lead", kind="relay",
                                      body="Result: forecast done."))
    assert relay.status == "relayed" and relay.register is Register.INTERNAL


def test_principal_register_item_must_go_to_the_principal(gate: Gate, ledger: Ledger) -> None:
    # The register is what the gate keys on: audience="principal" with an outside recipient would skip the 6.2 floor
    # and the disclosure check and, at routine tier, auto-send external mail through the same Sender. Refused.
    queue, sender, notifier = gate
    assert PRINCIPAL_RECIPIENTS == frozenset({"principal"})
    with pytest.raises(ApprovalError, match="non-Principal recipient"):
        queue.submit(ApprovalItem(tier="routine", persona="ren", recipient="x@example.com", audience="principal",
                                  kind="confirmation", body="Confirming Tuesday."))
    assert sender.sent == [] and notifier.notices == [] and ledger.list_approvals() == []
    # The pipeline may name the Principal's real channels per queue; the item never widens the set.
    own = ApprovalQueue(ledger, sender, notifier, personas=queue.personas,
                        principal_recipients=frozenset({"principal", "Me@Example.com"}))
    ok = own.submit(ApprovalItem(tier="routine", persona="ren", recipient="me@example.com", audience="principal",
                                 kind="confirmation", body="Confirming Tuesday."))
    assert ok.status == "auto-sent"
    with pytest.raises(ApprovalError, match="at least one Principal recipient"):
        ApprovalQueue(ledger, sender, notifier, personas=queue.personas, principal_recipients=frozenset())


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


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("401 from https://mailer:S3cretPw@smtp.example.com/send", "S3cretPw"),
        ("rejected header Authorization: Basic dXNlcjpwYXNz for the relay", "dXNlcjpwYXNz"),
        ("rejected header X-Atlas-Token: tok_live_9f8e7d", "tok_live_9f8e7d"),
        ('upstream said {"token": "abc123def", "ok": false}', "abc123def"),
        ("Cookie: session=deadbeef; path=/", "deadbeef"),
        ("api_key=sk-live-000 was refused", "sk-live-000"),
        ("Proxy-Authorization: Bearer eyJhbGci.xyz", "eyJhbGci.xyz"),
    ],
)
def test_sender_errors_are_redacted_before_the_ledger_journal_or_http_body(
        ledger: Ledger, personas: PersonaRegistry, caplog: pytest.LogCaptureFixture, message: str, secret: str) -> None:
    sender, notifier = StubSender(fail=RuntimeError(message)), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas)
    with caplog.at_level("DEBUG", logger="atlas.approval"), pytest.raises(SendError) as info:
        queue.submit(_email("routine", "victor", "Car confirmed.", kind="confirmation"))
    note = ledger.list_approvals("held")[0]["note"]
    assert secret not in note and "[redacted]" in note
    assert secret not in str(info.value)
    assert not any(secret in r.getMessage() for r in caplog.records)  # the DEBUG line is the redacted first line too
    assert not any(r.exc_info for r in caplog.records)  # the full exception is never attached to a log record


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


def test_sensitive_kinds_are_floored_by_the_gate(ledger: Ledger, personas: PersonaRegistry) -> None:
    # 16.2 "Sensitive: Legal, financial, medical, estate, security, any payment"; 16.3 rule 1. The caller's tier on
    # such a kind is a label: the gate raises it and the strong cross-check runs.
    assert SENSITIVE_KINDS == frozenset({"payment", "transfer", "invoice", "contract", "signature", "dns", "legal",
                                         "medical", "estate", "security", "vault"})
    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=_swap_check)
    item = queue.submit(_email("standard", "silas", "Release the $40,000 transfer to the vendor today.",
                               kind="payment", recipient="treasury@example.com"))
    assert item.status == "held" and item.tier == "sensitive"
    assert "tier raised standard->sensitive: kind 'payment' is a 16.2 sensitive category" in item.note
    assert item.cross_check is not None and item.cross_check.persona == "arthur"
    assert ledger.get_approval(item.id)["tier"] == "sensitive" and sender.sent == []
    assert notifier.notices[0][0].startswith(f"[sensitive] approval #{item.id} waiting: silas (payment)")
    # Even a "routine" label on a DNS change is sensitive, and so is a Principal-facing estate item.
    dns = queue.submit(_email("routine", "valerie", "Point the MX record at the new relay.", kind="dns"))
    assert dns.tier == "sensitive" and dns.status == "held"
    est = queue.submit(ApprovalItem(tier="standard", persona="arthur", recipient="principal", audience="principal",
                                    kind="estate", body="The trust's position after the sale is..."))
    assert est.tier == "sensitive" and est.cross_check is not None


def test_sensitive_principal_facing_item_is_cross_checked_and_approvable(ledger: Ledger,
                                                                          personas: PersonaRegistry) -> None:
    # 9.2 "applies the strong version automatically to the sensitive tier": every register, so a sensitive answer to
    # the Principal (medical, estate) is cross-checked at submit and can then be approved; it is not stranded behind
    # approve()'s backstop with no record and no way to get one.
    def ren_check(item: ApprovalItem) -> CrossCheckRecord:
        return CrossCheckRecord(engine="gpt-oss-120b", persona="ren", verdict="agree")

    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=ren_check)
    item = queue.submit(ApprovalItem(tier="sensitive", persona="arthur", recipient="principal", audience="principal",
                                     kind="reply", body="The bloods show... Let me know once you have the scans."))
    assert item.register is Register.PRINCIPAL and item.status == "held"
    assert item.cross_check is not None and item.cross_check.persona == "ren"
    assert item.cross_check.engine == "gpt-oss-120b" and "cross-check: ren@gpt-oss-120b says agree" in item.note
    assert item.rewrite is not None and item.rewrite.flagged  # the never-delegate pass still ran (16.1 rule 5)
    sent = queue.approve(item.id, decided_by="principal")
    assert sent.status == "sent" and [s.id for s in sender.sent] == [item.id]
    # Without a checker the item is held with the pending note and approve() still refuses (the backstop).
    bare = ApprovalQueue(ledger, StubSender(), StubNotifier(), personas=personas)
    held = bare.submit(ApprovalItem(tier="sensitive", persona="arthur", recipient="principal", audience="principal",
                                    kind="reply", body="The bloods show..."))
    assert "cross-check: pending" in held.note
    with pytest.raises(CrossCheckRequired):
        bare.approve(held.id, decided_by="principal")


def test_important_items_are_cross_checked(ledger: Ledger, personas: PersonaRegistry) -> None:
    # 9.2 "... and to anything the Principal marks important."
    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=_arthur_check)
    plain = queue.submit(_email("standard", "helena", "Our position on the licence is...", kind="email"))
    assert plain.cross_check is None and not plain.important
    marked = queue.submit(_email("standard", "helena", "Our position on the licence is...", kind="email",
                                 important=True))
    assert marked.tier == "standard" and marked.important and marked.cross_check is not None
    assert "cross-check: Principal marked important (9.2)" in marked.note
    assert "cross-check: arthur@nemotron-3-super says agree" in marked.note
    assert queue.get(marked.id).important  # round-trips through the gate's tasks row
    assert queue.approve(marked.id, decided_by="principal").status == "sent"


def test_dual_sign_off_needs_the_other_lead(ledger: Ledger, personas: PersonaRegistry) -> None:
    # 8.3 TF_OMEGA "Sensitive, dual sign-off", Ren and Arthur jointly: a single approve() must not send an item that
    # only one lead has seen; the cross-check record must be the other hemisphere lead's.
    def minerva_check(item: ApprovalItem) -> CrossCheckRecord:
        return CrossCheckRecord(engine="meditron-70b", persona="minerva", verdict="agree")

    sender, notifier = StubSender(), StubNotifier()
    queue = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=minerva_check)
    item = queue.submit(_email("sensitive", "ren", "Apex contingency: the plan is...", kind="email",
                               recipient="board@example.com", dual_sign_off=True))
    assert item.dual_sign_off and queue.get(item.id).dual_sign_off  # carried in the gate's tasks row
    assert item.cross_check is not None and item.cross_check.persona == "minerva"
    with pytest.raises(ApprovalError, match=r"TF_OMEGA needs both leads \(8\.3 dual sign-off\): the cross-check must "
                                            r"be arthur's, not 'minerva'"):
        queue.approve(item.id, decided_by="principal")
    assert sender.sent == [] and ledger.get_approval(item.id)["status"] == "held"
    # Arthur's own cross-check is the second signature: a fresh gate with him as the checker approves.
    joint = ApprovalQueue(ledger, sender, notifier, personas=personas, cross_checker=_arthur_check)
    both = joint.submit(_email("sensitive", "ren", "Apex contingency: the plan is...", kind="email",
                               recipient="board@example.com", dual_sign_off=True))
    assert both.cross_check is not None and both.cross_check.persona == "arthur"
    assert joint.approve(both.id, decided_by="principal").status == "sent"
    # And the reverse for an Arthur draft: Ren must be the cross-checker.
    arthur_draft = joint.submit(_email("sensitive", "arthur", "Apex contingency: the estate plan is...", kind="email",
                                       recipient="board@example.com", dual_sign_off=True))
    assert arthur_draft.cross_check is None  # _arthur_check is the drafting persona: refused, no record
    ren_row = _inference(ledger, "ren", "gpt-oss-120b")
    joint.attach_cross_check(arthur_draft.id, CrossCheckRecord(engine="gpt-oss-120b", persona="ren", verdict="agree",
                                                               task_id=ren_row))
    assert joint.approve(arthur_draft.id, decided_by="principal").status == "sent"
    # A plain sensitive item (no dual sign-off) is approvable on any second persona's record.
    single = queue.submit(_email("sensitive", "gideon", "Our position is...", recipient="counsel@example.com"))
    assert single.cross_check is not None and single.cross_check.persona == "minerva"
    assert queue.approve(single.id, decided_by="principal").status == "sent"


def test_gate_owns_its_tasks_row_and_items_round_trip(gate: Gate, ledger: Ledger) -> None:
    # A caller's task_id (the routing decision's task) is the parent; the gate's own row carries the audience, the
    # engine, the register and the flags, so a re-read item is the item that was submitted and update_task() hits
    # a row that exists.
    queue, sender, _ = gate
    item = queue.submit(ApprovalItem(tier="standard", persona="ren", recipient="principal", audience="principal",
                                     kind="reply", body="The lease is ready.", task_id="orphan-task"))
    assert item.task_id != "orphan-task" and ledger.get_task("orphan-task") is None
    row = ledger.get_task(item.task_id)
    assert row["kind"] == "approval" and row["parent_task_id"] == "orphan-task" and row["engine"] == "gpt-oss-120b"
    assert '"register": "principal"' in row["payload_json"] and '"caller_task_id": "orphan-task"' in row["payload_json"]
    again = queue.get(item.id)
    assert again.engine == "gpt-oss-120b" and again.audience == "principal" and again.register is Register.PRINCIPAL
    assert again.task_id == item.task_id and not again.dual_sign_off and not again.important
    sent = queue.approve(item.id, decided_by="principal")
    assert sent.recipient == "principal" and sender.sent[0].audience == "principal"
    assert ledger.get_task(item.task_id)["status"] == "done"  # the update hit the gate's row
    # An external item re-read: audience and engine come back too (the 9.2 different-engine rule can be enforced).
    ext = queue.submit(_email("standard", "silas", "Attached is the revised forecast.", task_id="routing-task-7"))
    assert queue.get(ext.id).engine == "nemotron-3-super" and queue.get(ext.id).audience == "external"
    assert ledger.get_task(ext.task_id)["parent_task_id"] == "routing-task-7"
    with pytest.raises(ApprovalError, match="different engine"):
        queue.attach_cross_check(ext.id, _record(ledger, "arthur", "nemotron-3-super"))


def test_notifier_receives_only_the_announcement_view(gate: Gate) -> None:
    # 16.2 "announces items waiting": the Notifier gets the message and an item stripped of everything the ledger
    # row holds for the queue view, so no concrete notifier can push the draft, recipient or reasoning to a phone.
    queue, _, notifier = gate
    item = queue.submit(_email("standard", "helena", "Thank you for the enquiry; our position on the licence is...",
                               subject="Licence position", reason="substantive reply"))
    message, seen = notifier.notices[0]
    assert seen.id == item.id and seen.tier == "standard" and seen.persona == "helena" and seen.kind == "email"
    assert seen.body == "" and seen.recipient == "" and seen.subject is None and seen.reason == ""
    assert seen.note is None and seen.disclosure is None and seen.rewrite is None
    assert "Licence position" not in message and "example.com" not in message
    checked = queue.attach_cross_check(item.id, _record(queue.ledger, "arthur", "nemotron-3-super",
                                                        notes="Clause 12 of the draft says..."))
    assert checked.cross_check is not None and checked.cross_check.notes.startswith("Clause 12")
    _, seen2 = notifier.notices[-1]
    assert seen2.cross_check is not None and seen2.cross_check.verdict == "agree" and seen2.cross_check.notes == ""


def test_arthur_speaks_externally_at_sensitive_tier_at_least(personas: PersonaRegistry) -> None:
    # 16.5: "Arthur's division holds family and health data and should treat every external disclosure as
    # sensitive-tier"; 16.1 rule 2 keeps Ren above routine. A lead file below its minimum is a ConfigError.
    assert LEAD_MINIMUM_EXTERNAL_TIER == {"ren": "standard", "arthur": "sensitive"}
    assert external_tier(personas["arthur"]) == "sensitive" and external_tier(personas["ren"]) == "sensitive"
    with pytest.raises(ConfigError, match=r"arthur.*16\.5.*minimum sensitive"):
        external_tier(personas["arthur"].model_copy(update={"speaks_externally_tier": "standard"}))
    with pytest.raises(ConfigError, match=r"ren.*16\.1 rule 2.*minimum standard"):
        external_tier(personas["ren"].model_copy(update={"speaks_externally_tier": "routine"}))
    assert external_tier(personas["ren"].model_copy(update={"speaks_externally_tier": "standard"})) == "standard"


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
