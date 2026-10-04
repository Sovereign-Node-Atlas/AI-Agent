"""The approval gate (Sections 16.1 rules 3-5, 16.2, 16.3 rule 3, 6.2, 6.4, 8.5, 9.2; CONVENTIONS.md §7.7, V15).

Every outbound item passes through `ApprovalQueue.submit()`; it is a code path, not a prompt instruction:

    routine     pre-approved categories only (ROUTINE_KINDS: acknowledgement, scheduling, confirmation; 16.2): sent at
                once through the Sender, logged in ledger.approvals as auto-sent. Any other kind labelled routine is
                raised to standard, in BOTH the external and the Principal register: the label alone never sends
                anything (16.3 rule 3).
    standard    held (status "held") until the Principal approves; a Notifier announces it (16.2 "push through ntfy")
    sensitive   held and flagged with the director's reasoning; the strong cross-check (9.2, a second persona on a
                different engine) is run automatically at submit through `cross_checker` and attached, whatever the
                register (9.2 "applies the strong version automatically to the sensitive tier"); `approve()` refuses
                (CrossCheckRequired) while no record is attached, the backstop when no checker is wired or it failed.
                `needs_cross_check()` lists what is waiting for one. An item the Principal marked `important` is
                cross-checked too (9.2 "and to anything the Principal marks important").

The gate floors the tier itself for the categories 16.2 names sensitive (SENSITIVE_KINDS: payment, transfer, invoice,
contract, signature, dns, legal, medical, estate, security, vault): a caller's "standard" on a payment is raised to
sensitive (16.3 rule 1: money never moves without the Principal; the gate is the control, not the label).

Register (6.4) decides what the gate does with a draft:
  * EXTERNAL (audience external/recipient): the full gate: the persona's Section 6.2 "speaks externally" tier is a
    floor; an admission of AI nature or a self-reference (governance.disclosure_check, 16.1 rule 4) is never
    auto-sent, it is held with the hits in the note.
  * PRINCIPAL (audience principal): only a hemisphere lead may address the Principal (16.1 rule 1 "The Principal
    speaks only to Ren or Arthur. Shadow Cabinet output is never surfaced directly"): a director's item is refused
    with the lead to relay to. The recipient must be the Principal's own channel (PRINCIPAL_RECIPIENTS, or the
    queue's `principal_recipients`): a Principal-register item addressed elsewhere is refused, since the register
    is what the gate keys on and one mislabelled field must not send external mail. The never-delegate rewrite pass
    (16.1 rule 5; governance.never_delegate_rewrite) is applied and the rewritten text is what is stored and sent;
    a task-shaped sentence the pass could only flag holds the item (never auto-sent) with the sentences in the note.
  * INTERNAL (director/lead/internal/system): the 8.5 step 6-7 relays are dispatches, not outbound items: they are
    recorded as their own `tasks` row (kind "relay", parent = the item's task) and returned with status "relayed";
    nothing is sent, nothing is announced, nothing is held.

Decisions are human-only: `approve()`/`reject()` take `decided_by` from HUMAN_ACTORS (16.3 rule 3) and claim the row
atomically (UPDATE ... WHERE status = 'held'), so two processes over the same SQLite file cannot both send. A row
approved but not delivered (a crash between the claim and the send) stays "approved" without a "delivery" note and
is listed by `stalled()` for `resend()`. A rejected draft is a 9.4 automatic strike input: `reject()` writes the
`strikes` row (kind "rejected-draft", as tasks/ouroboros.STRIKE_KINDS spells it). A `dual_sign_off` item (TF_OMEGA,
8.3 "Sensitive, dual sign-off", Ren and Arthur jointly) is approvable only when its cross-check record is the other
hemisphere lead's: both leads must have signed before the Principal sees it.

Storage is the ledger only (no in-memory state that a restart would lose): approvals rows for the items, a `tasks`
row of kind "approval" per item that the gate itself inserts (carrying the drafting engine, the audience, the register
and the flags; the caller's own task id, when given, is its parent_task_id, so a re-read item always finds them), and
a `tasks` row of kind "cross-check" (parent = the approval's task id) for each cross-check record. A record attached
by hand must name, in `task_id`, the done "cross-check" tasks row of the inference that produced it, with the same
persona and engine (provenance: a fabricated "agree" is refused); the automatic path records the inference itself.
Only the Ledger's public API is used (`transaction()` for the conditional claims and note updates). Sending and
notifying go through the Sender and Notifier protocols; tests use the stubs (StubSender, StubNotifier) so nothing
leaves the node. The push notification is a minimal announcement (tier, id, persona, kind, cross-check verdict), and
the item handed to the Notifier is stripped the same way (no recipient, subject, draft, reasoning, disclosure or
cross-check notes): what the ledger row holds for the interface's queue view never reaches ntfy's cache or the
journal, whatever a concrete notifier does with its second argument.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from atlas.config import TIERS, ConfigError
from atlas.governance import (
    DisclosureResult,
    Register,
    RewriteResult,
    disclosure_check,
    never_delegate_rewrite,
    register,
)
from atlas.ledger import Ledger, new_task_id
from atlas.personas import PersonaRegistry

log = logging.getLogger("atlas.approval")

__all__ = [
    "HUMAN_ACTORS",
    "PRINCIPAL_RECIPIENTS",
    "ROUTINE_KINDS",
    "SENSITIVE_KINDS",
    "ApprovalError",
    "ApprovalItem",
    "ApprovalQueue",
    "CrossCheckRecord",
    "CrossCheckRequired",
    "CrossChecker",
    "NotPending",
    "Notifier",
    "NullNotifier",
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
STATUS_RELAYED = "relayed"  # ApprovalItem status for an internal relay: a tasks row, never an approvals row
# 16.2 routine: "Meeting scheduling, confirmations, acknowledgements. Pre-approved categories". Nothing else auto-sends.
ROUTINE_KINDS: frozenset[str] = frozenset({"acknowledgement", "scheduling", "confirmation"})
# 16.2 sensitive: "Legal, financial, medical, estate, security, any payment"; 16.3 rules 1, 2, 4, 5 (money, contracts,
# DNS, the vault). An item of one of these kinds is sensitive whatever tier the caller typed.
SENSITIVE_KINDS: frozenset[str] = frozenset(
    {
        "payment",
        "transfer",
        "invoice",
        "contract",
        "signature",
        "dns",
        "legal",
        "medical",
        "estate",
        "security",
        "vault",
    }
)
# 16.3 rule 3 / 16.2 "nothing external executes until the Principal taps approve": the only actor that decides.
HUMAN_ACTORS: frozenset[str] = frozenset({"principal"})
# The Principal's own channel: the only recipient a PRINCIPAL-register item may name (module docstring). The pipeline
# may widen it per queue (`principal_recipients`) with the Principal's real addresses; it is never widened from the
# item itself.
PRINCIPAL_RECIPIENTS: frozenset[str] = frozenset({"principal"})
GATE_ACTOR = "gate"  # decided_by on rows the gate itself moved (routine auto-approval)
_DELIVERY_MARK = "delivery "  # note fragment that proves a send completed
# What a Sender's or cross-checker's exception text may carry and must not reach a ledger note, an HTTP body or the
# journal: bearer tokens, auth headers (with their scheme word), key=value / "key": "value" credentials, and basic-auth
# in a URL (://user:pass@host).
_REDACT_RE = re.compile(
    r"(Bearer\s+\S+"
    r"|(?:Authorization|X-Atlas-Token|Cookie|Set-Cookie|Proxy-Authorization)\s*[:=]\s*(?:(?:Basic|Bearer|Digest|Token)"
    r"\s+)?\S+"
    r"|(?<![A-Za-z0-9_])\"?(?:key|token|secret|password|passphrase|apikey|api_key|access_token|refresh_token)\"?"
    r"\s*[:=]\s*\"?\S+"
    r"|://[^/\s:@]+:[^@\s]+@)",
    re.IGNORECASE,
)


class ApprovalError(RuntimeError):
    """The gate refused; the message says exactly why."""


class CrossCheckRequired(ApprovalError):
    """A sensitive item was approved without a cross-check record (16.2 "strong cross-check applied automatically")."""


class NotPending(ApprovalError):
    """approve()/reject() on an item that is not held (or was decided concurrently)."""


class SendError(ApprovalError):
    """The Sender failed; the ledger row says so and nothing pretends the item went out."""


# --- protocols and stubs ----------------------------------------------------------------------------------------------


@dataclass
class ApprovalItem:
    """One outbound item. `reason` is the director's reasoning (16.2 sensitive: "flagged with the director's
    reasoning"); `body` the draft; `audience` sets the register (6.4); `engine` the engine that drafted it (filled
    from the persona's default at submit when not given; the cross-check must run elsewhere, 9.2); `task_id` the
    caller's task (the routing decision's), kept as the parent of the gate's own tasks row; `dual_sign_off` carries
    RoutingDecision.dual_sign_off (TF_OMEGA, 8.3); `important` is the Principal's mark (9.2: cross-checked too)."""

    tier: str
    persona: str
    recipient: str
    body: str
    reason: str = ""
    kind: str = "email"
    subject: str | None = None
    audience: str = "external"
    task_id: str | None = None
    engine: str | None = None
    dual_sign_off: bool = False
    important: bool = False
    id: int | None = None
    status: str | None = None
    register: Register | None = None
    note: str | None = None
    delivery_ref: str | None = None
    disclosure: DisclosureResult | None = field(default=None, repr=False)
    rewrite: RewriteResult | None = field(default=None, repr=False)
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
    """Announces waiting items (16.2: "A push notification through ntfy announces items waiting").

    `item` is the announcement view of the item (id, tier, persona, kind, status, cross-check verdict), never the
    full row: the gate strips recipient, subject, body, reasoning, note, disclosure and cross-check notes before the
    call (`_notice_item`), so a concrete notifier cannot leak them whatever it does with its second argument."""

    def notify(self, message: str, item: ApprovalItem) -> None: ...


# The strong cross-check (9.2): given the held item, run a second persona on a different engine and return the
# record. The pipeline wires it (a Celery task on the `gpu` queue, 9.7: e.g. arthur@nemotron-3-super for a corporate
# draft, ren@gpt-oss-120b for an estate one, meditron-70b for Minerva per 6.2). It may raise; the gate then holds the
# item without a record and approve() refuses until attach_cross_check() is called.
CrossChecker = Callable[[ApprovalItem], CrossCheckRecord]


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
    """For contexts with no ntfy configured (a repair shell, a test rig). Never silent: it warns at construction and
    on every held item, because 16.2 wants a push and the ledger row alone announces nothing."""

    def __init__(self) -> None:
        log.warning("approval queue: no ntfy notifier configured; held items will not be announced (16.2)")

    def notify(self, message: str, item: ApprovalItem) -> None:
        log.warning("approval notice (no notifier configured, nobody was pushed): %s", message)


# --- helpers ---------------------------------------------------------------------------------------------------------


def _max_tier(a: str, b: str) -> str:
    return a if TIER_ORDER[a] >= TIER_ORDER[b] else b


def _describe_error(exc: BaseException) -> str:
    """Type name plus the first line of the message, truncated and with credentials redacted: what may go into a
    ledger note or an HTTP error body. Even the DEBUG line carries only the redacted first line, never the whole
    exception (a DEBUG journal would otherwise hold a Sender's credential verbatim)."""
    first = (str(exc).splitlines() or [""])[0]
    first = _REDACT_RE.sub("[redacted]", first)[:200]
    log.debug("approval gate: underlying error %s: %s", type(exc).__name__, first)
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _join(*parts: str | None) -> str | None:
    return "; ".join(p for p in parts if p) or None


def _notice_item(item: ApprovalItem) -> ApprovalItem:
    """The announcement view handed to the Notifier (module docstring): identity and tier only."""
    cc = item.cross_check
    return replace(
        item,
        recipient="",
        subject=None,
        body="",
        reason="",
        note=None,
        disclosure=None,
        rewrite=None,
        cross_check=replace(cc, notes="") if cc is not None else None,
    )


def _other_lead(personas: PersonaRegistry, persona: str) -> str:
    """The hemisphere lead who is not `persona`'s own (8.3 TF_OMEGA: Ren and Arthur jointly)."""
    own = personas.lead_of(persona).key
    for key in personas:
        if personas.is_lead(key) and key != own:
            return key
    raise ApprovalError(f"no second hemisphere lead besides {own!r} in the persona registry (6.1)")


# --- the queue ------------------------------------------------------------------------------------------------------


class ApprovalQueue:
    """The gate. One instance per process over the shared ledger; every method is a single ledger transaction chain.

    `personas` (the Section 6.2 floor) and `notifier` (the 16.2 push) are required: both are gate controls, not
    options a caller may leave out (16.1 rule 3). `cross_checker` is the automatic 9.2 strong cross-check for
    sensitive items; without it they are held and approve() refuses until a record is attached.
    """

    def __init__(
        self,
        ledger: Ledger,
        sender: Sender,
        notifier: Notifier,
        *,
        personas: PersonaRegistry,
        cross_checker: CrossChecker | None = None,
        clock: Callable[[], float] = time.time,
        principal_recipients: frozenset[str] = PRINCIPAL_RECIPIENTS,
    ) -> None:
        if personas is None:  # type: ignore[unreachable]  # defensive: a caller passing None must fail loudly
            raise ApprovalError(
                "ApprovalQueue needs the persona registry: the Section 6.2 external-tier floor is a "
                "gate control, not an option (16.1 rule 3)"
            )
        if notifier is None:  # type: ignore[unreachable]
            raise ApprovalError(
                "ApprovalQueue needs a Notifier: 16.2 announces held items through ntfy; pass "
                "NullNotifier() explicitly for a context without one"
            )
        self.ledger = ledger
        self.sender = sender
        self.notifier: Notifier = notifier
        self.personas = personas
        self.cross_checker = cross_checker
        self._clock = clock
        self.principal_recipients = frozenset(r.strip().lower() for r in principal_recipients if r.strip())
        if not self.principal_recipients:
            raise ApprovalError(
                "ApprovalQueue needs at least one Principal recipient (PRINCIPAL_RECIPIENTS): a "
                "Principal-register item must be addressable to the Principal and nobody else"
            )

    # --- submit (16.2) ------------------------------------------------------------------------------------------------

    def submit(self, item: ApprovalItem) -> ApprovalItem:
        """Route one outbound item through the gate. Returns the item with id, status, register and note set."""
        if item.tier not in TIER_ORDER:
            raise ApprovalError(f"tier {item.tier!r} is not one of {TIERS} (CONVENTIONS.md §8)")
        if not item.recipient.strip():
            raise ApprovalError("an outbound item needs a recipient")
        if not item.body.strip():
            raise ApprovalError("an outbound item needs a body")
        # 6.4: the register is set here, on the draft, as a property.
        draft = register(item.body, item.audience)
        try:
            engine = item.engine or self.personas.engine_for(item.persona)
        except ConfigError as exc:
            raise ApprovalError(f"cannot submit for persona {item.persona!r}: {exc}") from exc
        if draft.register is Register.INTERNAL:
            return self._relay(item, draft.register, engine)

        notes: list[str] = []
        tier = item.tier
        body = item.body
        rewrite: RewriteResult | None = None
        disclosure: DisclosureResult | None = None
        hold = False  # never auto-send, whatever the tier
        # 16.2 "Sensitive: Legal, financial, medical, estate, security, any payment"; 16.3 rule 1. The gate is the
        # control: the caller's tier on one of these kinds is a label, and the router's lexicon does not reach here.
        if item.kind in SENSITIVE_KINDS and tier != "sensitive":
            notes.append(f"tier raised {tier}->sensitive: kind {item.kind!r} is a 16.2 sensitive category")
            tier = "sensitive"
        if draft.register is Register.PRINCIPAL:
            # 16.1 rule 1: "The Principal speaks only to Ren or Arthur. Shadow Cabinet output is never surfaced
            # directly; the hemisphere synthesises and speaks." A director's item is relayed to its lead, not sent.
            if not self.personas.is_lead(item.persona):
                lead = self.personas.lead_of(item.persona).key
                raise ApprovalError(
                    f"persona {item.persona!r} may not address the Principal: only a hemisphere lead "
                    f"speaks to the Principal (16.1 rule 1); relay to {lead}"
                )
            # The register is what this gate keys on, so a PRINCIPAL item must go to the Principal and nowhere else:
            # a mislabelled audience on an external address would otherwise skip the 6.2 floor and the disclosure
            # check and, at routine tier, auto-send external mail (16.1 rule 3, 16.3 rule 3).
            if item.recipient.strip().lower() not in self.principal_recipients:
                raise ApprovalError(
                    f"principal-register item addressed to a non-Principal recipient "
                    f"{item.recipient!r}; the Principal's channel is "
                    f"{sorted(self.principal_recipients)} (6.4 register, 16.1 rule 3)"
                )
            # 16.1 rule 5: the never-delegate rewrite pass, on the code path (Appendix A: before the outbound gate).
            rewrite = never_delegate_rewrite(body, item.audience)
            body = rewrite.text
            if rewrite.rewrites:
                notes.append(f"never-delegate: {len(rewrite.rewrites)} rewrite(s) (16.1 rule 5)")
            if rewrite.flagged:
                notes.append(
                    f"never-delegate: {len(rewrite.flagged)} task-shaped sentence(s) flagged for a human "
                    f"(16.1 rule 5; held, never auto-sent): " + " | ".join(rewrite.flagged)
                )
                hold = True
        else:
            # 6.2 floor: a persona never speaks externally below its declared tier.
            floor = self.personas.external_tier(item.persona)
            if TIER_ORDER[floor] > TIER_ORDER[tier]:
                notes.append(f"tier raised {tier}->{floor} (Section 6.2 floor for {item.persona})")
                tier = floor
            # 16.1 rule 4: an external admission of AI nature (or the system naming itself) never auto-sends.
            disclosure = disclosure_check(body)
            if disclosure.disclosed:
                notes.append(f"disclosure: {', '.join(disclosure.hits)} (16.1 rule 4; held, never auto-sent)")
                hold = True
        # 16.2: routine is a pre-approved category, not a label a pipeline step may attach to substance; the rule is
        # the same for the Principal's register (a substantive reply to the Principal is not a confirmation either).
        if tier == "routine" and item.kind not in ROUTINE_KINDS:
            notes.append(
                f"tier raised routine->standard: kind {item.kind!r} is not a pre-approved category "
                f"{sorted(ROUTINE_KINDS)} (16.2)"
            )
            tier = "standard"
        if hold:
            tier = _max_tier(tier, "standard")
        # The gate's own tasks row, always (a caller's row may carry no audience or engine, or not exist): what
        # _from_row reads back; the caller's task is the parent (8.6: "reports ... under a task ID").
        task_id = self.ledger.insert_task(
            "approval",
            task_id=new_task_id(),
            status="queued",
            persona=item.persona,
            engine=engine,
            tier=tier,
            parent_task_id=item.task_id,
            payload={
                "kind": item.kind,
                "recipient": item.recipient,
                "subject": item.subject,
                "audience": draft.audience,
                "register": str(draft.register),
                "dual_sign_off": bool(item.dual_sign_off),
                "important": bool(item.important),
                "caller_task_id": item.task_id,
            },
        )
        item = replace(
            item,
            tier=tier,
            body=body,
            task_id=task_id,
            engine=engine,
            register=draft.register,
            disclosure=disclosure,
            rewrite=rewrite,
        )

        if tier == "routine" and not hold:
            return self._auto_send(item, notes)
        # standard / sensitive (or a hold): held, cross-checked when sensitive (or marked important), announced.
        approval_id = self.ledger.insert_approval(
            task_id=task_id,
            tier=tier,
            kind=item.kind,
            status=STATUS_HELD,
            persona=item.persona,
            recipient=item.recipient,
            subject=item.subject,
            draft=body,
            reasoning=item.reason or None,
            note="; ".join(notes) or None,
        )
        item = replace(item, id=approval_id, status=STATUS_HELD, note="; ".join(notes) or None)
        if tier == "sensitive":
            # 9.2 "applies the strong version automatically to the sensitive tier": every register, so a sensitive
            # Principal-facing item (an estate or medical answer) is not stranded behind approve()'s backstop.
            item = self._run_cross_check(item)
        elif item.important:
            # 9.2 "... and to anything the Principal marks important."
            item = self._run_cross_check(item, why="Principal marked important (9.2)")
        self._notify_held(item)
        log.info(
            "approval #%s held: tier=%s persona=%s kind=%s cross_check=%s",
            approval_id,
            tier,
            item.persona,
            item.kind,
            item.cross_check.verdict if item.cross_check else None,
        )
        return item

    def _relay(self, item: ApprovalItem, reg: Register, engine: str) -> ApprovalItem:
        """8.5 step 6-7: an internal relay is its own dispatch with its own task ID and log entry, not an approval."""
        relay_id = self.ledger.insert_task(
            "relay", status="done", persona=item.persona, engine=engine, tier=item.tier,
            parent_task_id=item.task_id,
            payload={"kind": item.kind, "to": item.recipient, "audience": item.audience, "subject": item.subject,
                     "body": item.body, "reasoning": item.reason or None})
        note = "internal relay: not an outbound item (8.5 step 6); recorded, not gated"
        log.info("relay task %s: %s -> %s (%s)", relay_id, item.persona, item.recipient, item.kind)
        return replace(item, task_id=relay_id, engine=engine, status=STATUS_RELAYED, register=reg, note=note)

    def _auto_send(self, item: ApprovalItem, notes: list[str]) -> ApprovalItem:
        # The row exists before the send (16.2 "sent automatically, logged for review"): a crash between the send
        # and the update leaves an "approved" row with no delivery mark, which stalled() lists.
        pre_note = "; ".join([*notes, "routine: pre-approved category, auto-approved; sending"])
        approval_id = self.ledger.insert_approval(
            task_id=item.task_id, tier=item.tier, kind=item.kind, status=STATUS_APPROVED, persona=item.persona,
            recipient=item.recipient, subject=item.subject, draft=item.body, reasoning=item.reason or None,
            note=pre_note)
        # The Sender sees the item as the ledger knows it (id, "approved"), exactly as approve() hands it over.
        item = replace(item, id=approval_id, status=STATUS_APPROVED, note=pre_note)
        try:
            ref = self.sender.send(item)
        except Exception as exc:  # record the failure, then re-raise loudly
            err = _describe_error(exc)
            held_note = "; ".join([*notes, f"routine send failed: {err}; held for a human"])
            self._set(approval_id, status=STATUS_HELD, note=held_note, decided_at=None, decided_by=None)
            if item.task_id:
                self.ledger.update_task(item.task_id, status="failed", error=f"routine send failed: {err}")
            failed = replace(item, id=approval_id, status=STATUS_HELD, note=held_note)
            self._notify_held(failed, prefix="SEND FAILED ")
            raise SendError(f"approval #{approval_id}: routine send to {item.recipient} failed: {err}") from exc
        final_note = "; ".join([*notes, f"{_DELIVERY_MARK}{ref}"])
        self.ledger.decide_approval(approval_id, STATUS_AUTO_SENT, decided_by=GATE_ACTOR, note=final_note)
        if item.task_id:
            self.ledger.update_task(item.task_id, status="done", result={"approval_id": approval_id, "delivery": ref})
        log.info("approval #%s auto-sent (routine %s): persona=%s delivery=%s", approval_id, item.kind, item.persona,
                 ref)
        return replace(item, id=approval_id, status=STATUS_AUTO_SENT, delivery_ref=ref, note=final_note)

    def _run_cross_check(self, item: ApprovalItem, why: str | None = None) -> ApprovalItem:
        """9.2 / 16.2: the strong cross-check, applied automatically to a held sensitive (or important) item."""
        assert item.id is not None
        if why:
            note = _join(item.note, f"cross-check: {why}")
            self._set(item.id, note=note)
            item = replace(item, note=note)
        if self.cross_checker is None:
            note = _join(
                item.note,
                "cross-check: pending (no cross-checker configured; approve() refuses until attach_cross_check(), 9.2)",
            )
            log.warning("approval #%s needs a cross-check and no cross-checker is configured (9.2, 16.2)", item.id)
            self._set(item.id, note=note)
            return replace(item, note=note)
        try:
            record = self.cross_checker(item)
        except Exception as exc:
            err = _describe_error(exc)
            note = _join(item.note, f"cross-check failed: {err}; approve() refuses until one is attached (9.2)")
            log.error("approval #%s: automatic cross-check failed: %s", item.id, err)
            self._set(item.id, note=note)
            return replace(item, note=note)
        # The gate ran the inference itself, so it vouches for the record (provenance: the automatic path). A record
        # that breaks the 9.2 rules (same persona, same engine) is refused like a failed check: held, no record,
        # approve() refuses; the item itself is never lost to the refusal.
        try:
            attached = self._attach(item.id, record, notify=False, automatic=True)
        except ApprovalError as exc:
            note = _join(item.note, f"cross-check refused: {exc}; approve() refuses until one is attached (9.2)")
            log.error("approval #%s: automatic cross-check record refused: %s", item.id, exc)
            self._set(item.id, note=note)
            return replace(item, note=note)
        # Keep the submitted item's register, rewrite and disclosure (the row re-read carries only what is stored).
        return replace(item, cross_check=attached.cross_check, note=attached.note)

    def _notify_held(self, item: ApprovalItem, prefix: str = "") -> None:
        # Minimal on purpose (16.2 "announces items waiting"): ntfy caches and forwards this to a phone, so no
        # recipient, subject, draft head, reasoning or disclosure text leaves the ledger; the item handed over is the
        # announcement view (_notice_item), not the row.
        msg = f"{prefix}[{item.tier}] approval #{item.id} waiting: {item.persona} ({item.kind})"
        if item.cross_check is not None:
            cc = item.cross_check
            msg += f" | cross-check: {cc.persona}@{cc.engine} says {cc.verdict}"
        try:
            self.notifier.notify(msg, _notice_item(item))
        except Exception as exc:  # a notifier outage must not lose the held item (it is in the ledger)
            log.error("notifier failed for approval #%s: %s", item.id, _describe_error(exc))

    # --- decisions ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _human(decided_by: str) -> str:
        if decided_by not in HUMAN_ACTORS:
            raise ApprovalError(f"decided_by {decided_by!r} is not a human actor {sorted(HUMAN_ACTORS)}; nothing "
                                f"external executes until the Principal taps approve (16.2, 16.3 rule 3)")
        return decided_by

    def approve(self, approval_id: int, *, decided_by: str, note: str | None = None) -> ApprovalItem:
        """The Principal taps approve: the item is sent. Sensitive items need a cross-check record first."""
        decided_by = self._human(decided_by)
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        if item.tier == "sensitive" and item.cross_check is None:
            raise CrossCheckRequired(
                f"approval #{approval_id} is sensitive tier and has no cross-check record "
                f"(16.2: strong cross-check applied before the Principal approves)"
            )
        if item.dual_sign_off:
            # 8.3 TF_OMEGA "Sensitive, dual sign-off", Ren and Arthur jointly: the record must be the other lead's.
            other = _other_lead(self.personas, item.persona)
            if item.cross_check is None or item.cross_check.persona != other:
                have = item.cross_check.persona if item.cross_check else None
                raise ApprovalError(
                    f"approval #{approval_id}: TF_OMEGA needs both leads (8.3 dual sign-off): the "
                    f"cross-check must be {other}'s, not {have!r}"
                )
        decision_note = _join(item.note, note, f"approved by {decided_by}") or ""
        if not self._claim(approval_id, STATUS_HELD, STATUS_APPROVED, decided_by=decided_by, note=decision_note):
            raise NotPending(f"approval #{approval_id} was decided concurrently; it is no longer held")
        return self._deliver(item, decision_note, decided_by)

    def _deliver(self, item: ApprovalItem, decision_note: str, decided_by: str) -> ApprovalItem:
        assert item.id is not None
        try:
            ref = self.sender.send(item)
        except Exception as exc:
            err = _describe_error(exc)
            self._set(item.id, note=f"{decision_note}; send failed: {err}")  # stays "approved": stalled() lists it
            raise SendError(f"approval #{item.id}: send to {item.recipient} failed after approval: {err}") from exc
        final_note = f"{decision_note}; {_DELIVERY_MARK}{ref}"
        self.ledger.decide_approval(item.id, STATUS_SENT, decided_by=decided_by, note=final_note)
        if item.task_id:
            self.ledger.update_task(item.task_id, status="done", result={"approval_id": item.id, "delivery": ref})
        log.info("approval #%s approved by %s and sent: delivery=%s", item.id, decided_by, ref)
        return replace(item, status=STATUS_SENT, delivery_ref=ref, note=final_note, decided_by=decided_by)

    def reject(self, approval_id: int, *, decided_by: str, note: str | None = None) -> ApprovalItem:
        decided_by = self._human(decided_by)
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        final_note = _join(item.note, note, f"rejected by {decided_by}") or ""
        if not self._claim(approval_id, STATUS_HELD, STATUS_REJECTED, decided_by=decided_by, note=final_note):
            raise NotPending(f"approval #{approval_id} was decided concurrently; it is no longer held")
        if item.task_id:
            self.ledger.update_task(item.task_id, status="cancelled", error=f"rejected: {note or ''}".strip())
        # 9.4 "Strike input ... automatic: ... rejected draft"; 8.6 "subject to the same tiered approval and Ouroboros
        # logging". The gate is the only component that knows a draft was rejected, so it writes the strike. The kind
        # is the one tasks/ouroboros.STRIKE_KINDS spells (not imported: that module pulls Celery and the memory store).
        self.ledger.insert_strike(
            task_id=item.task_id,
            kind="rejected-draft",
            source="approval-gate",
            description=f"approval #{approval_id} ({item.persona}, {item.kind}, {item.tier}) rejected by {decided_by}: "
            f"{note or ''}".rstrip(": "),
        )
        log.info("approval #%s rejected by %s (strike: rejected-draft, 9.4)", approval_id, decided_by)
        return replace(item, status=STATUS_REJECTED, note=final_note, decided_by=decided_by)

    def resend(self, approval_id: int, *, decided_by: str) -> ApprovalItem:
        """Deliver an item that was approved but never delivered (see stalled()); the claim is atomic."""
        decided_by = self._human(decided_by)
        item = self.get(approval_id)
        if item.status != STATUS_APPROVED or (item.note and _DELIVERY_MARK in item.note):
            raise NotPending(f"approval #{approval_id} is not an approved, undelivered item")
        note = f"{item.note or ''}; resend by {decided_by}".strip("; ")
        with self.ledger.transaction() as cur:
            cur.execute("UPDATE approvals SET note = ? WHERE id = ? AND status = ? "
                        "AND (note IS NULL OR note NOT LIKE ?)",
                        (note, approval_id, STATUS_APPROVED, f"%{_DELIVERY_MARK}%"))
            claimed = cur.rowcount == 1
        if not claimed:
            raise NotPending(f"approval #{approval_id} was delivered or decided concurrently")
        return self._deliver(item, note, decided_by)

    def attach_cross_check(self, approval_id: int, record: CrossCheckRecord, *, notify: bool = True) -> ApprovalItem:
        """Attach the strong cross-check (9.2) to a held item: a tasks row of kind "cross-check", the verdict in the
        approval's note, and (by default) a fresh announcement carrying the verdict.

        Refused when the record is not a second persona on a different engine (9.2), or when it has no provenance:
        `record.task_id` must name the done "cross-check" tasks row of the inference that produced it, with the same
        persona and engine, so a fabricated verdict cannot satisfy approve()'s backstop (16.2 "strong cross-check").
        """
        return self._attach(approval_id, record, notify=notify, automatic=False)

    def _attach(self, approval_id: int, record: CrossCheckRecord, *, notify: bool, automatic: bool) -> ApprovalItem:
        item = self.get(approval_id)
        if item.status != STATUS_HELD:
            raise NotPending(f"approval #{approval_id} is {item.status!r}, not held")
        if not item.task_id:
            raise ApprovalError(f"approval #{approval_id} has no task id; cannot attach a cross-check")
        if record.persona == item.persona:
            raise ApprovalError(
                f"cross-check for approval #{approval_id} must be a second persona, not the drafting "
                f"persona {item.persona!r} (9.2)"
            )
        if item.engine and record.engine == item.engine:
            raise ApprovalError(
                f"cross-check for approval #{approval_id} must run on a different engine than the "
                f"draft's {item.engine!r} (9.2 strong version: an engine swap)"
            )
        inference_task: str | None = None
        if record.task_id is not None:
            prov = self.ledger.get_task(record.task_id)
            if (
                prov is None
                or prov.get("kind") != "cross-check"
                or prov.get("status") != "done"
                or (prov.get("engine") or "") != record.engine
                or (prov.get("persona") or "") != record.persona
            ):
                raise ApprovalError(
                    f"cross-check for approval #{approval_id} names task {record.task_id!r}, which is "
                    f"not a done 'cross-check' task by {record.persona}@{record.engine} (9.2 "
                    f"provenance: the record must come from an inference that ran)"
                )
            inference_task = record.task_id
        elif not automatic:
            raise ApprovalError(
                f"cross-check for approval #{approval_id} has no task_id: a record attached by hand "
                f"must name the done 'cross-check' tasks row of the inference that produced it (9.2 "
                f"provenance); only the gate's own automatic cross-check may omit it"
            )
        cc_task = self.ledger.insert_task(
            "cross-check",
            status="done",
            persona=record.persona,
            engine=record.engine,
            tier=item.tier,
            parent_task_id=item.task_id,
            payload={
                "approval_id": approval_id,
                "verdict": record.verdict,
                "notes": record.notes,
                "inference_task_id": inference_task,
                "source": "gate" if automatic else "attached",
            },
        )
        stored = replace(record, task_id=cc_task)
        summary = f"cross-check: {record.persona}@{record.engine} says {record.verdict}"
        if record.notes:
            summary += f": {record.notes}"
        note = _join(item.note, summary)
        self._set(approval_id, note=note)
        item = replace(item, cross_check=stored, note=note)
        log.info(
            "approval #%s cross-check attached: %s on %s says %s",
            approval_id,
            record.persona,
            record.engine,
            record.verdict,
        )
        if notify:
            self._notify_held(item)
        return item

    # --- ledger writes (public Ledger API: transaction()) ------------------------------------------------------------

    def _claim(self, approval_id: int, from_status: str, to_status: str, *, decided_by: str, note: str) -> bool:
        """Move a row from one status to another only if it is still in `from_status`; True when this call won."""
        with self.ledger.transaction() as cur:
            cur.execute("UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, note = ? "
                        "WHERE id = ? AND status = ?",
                        (to_status, self._clock(), decided_by, note, approval_id, from_status))
            return cur.rowcount == 1

    def _set(self, approval_id: int, **fields: Any) -> None:
        """Update named approvals columns (status, note, decided_at, decided_by) without touching the others."""
        allowed = {"status", "note", "decided_at", "decided_by"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"approval columns {sorted(bad)} are not updatable here")
        sets = ", ".join(f"{col} = ?" for col in fields)
        with self.ledger.transaction() as cur:
            cur.execute(f"UPDATE approvals SET {sets} WHERE id = ?", (*fields.values(), approval_id))

    # --- reads --------------------------------------------------------------------------------------------------------

    def get(self, approval_id: int) -> ApprovalItem:
        row = self.ledger.get_approval(approval_id)
        if row is None:
            raise ApprovalError(f"approval #{approval_id} does not exist")
        return self._from_row(row)

    def pending(self, limit: int = 100) -> list[ApprovalItem]:
        return [self._from_row(r) for r in self.ledger.list_approvals(STATUS_HELD, limit)]

    def needs_cross_check(self, limit: int = 100) -> list[ApprovalItem]:
        """Held sensitive items with no cross-check record: what the pipeline must run and attach (9.2, 16.2)."""
        return [i for i in self.pending(limit) if i.tier == "sensitive" and i.cross_check is None]

    def stalled(self, limit: int = 100) -> list[ApprovalItem]:
        """Approved but never delivered (a crash or a failed send after the claim): candidates for resend()."""
        rows = self.ledger.query(
            "SELECT * FROM approvals WHERE status = ? AND (note IS NULL OR note NOT LIKE ?) ORDER BY id DESC LIMIT ?",
            (STATUS_APPROVED, f"%{_DELIVERY_MARK}%", limit))
        return [self._from_row(r) for r in rows]

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
        task_id = row.get("task_id")
        task = self.ledger.get_task(task_id) if task_id else None
        payload: dict[str, Any] = {}
        if task and task.get("payload_json"):
            payload = json.loads(task["payload_json"])
        reg = payload.get("register")
        return ApprovalItem(
            register=Register(reg) if reg in {r.value for r in Register} else None,
            tier=row["tier"],
            persona=row.get("persona") or "",
            recipient=row.get("recipient") or "",
            body=row.get("draft") or "",
            reason=row.get("reasoning") or "",
            kind=row.get("kind") or "email",
            subject=row.get("subject"),
            audience=payload.get("audience") or "external",
            task_id=task_id,
            engine=(task or {}).get("engine"),
            dual_sign_off=bool(payload.get("dual_sign_off")),
            important=bool(payload.get("important")),
            id=approval_id,
            status=row.get("status"),
            note=row.get("note"),
            decided_by=row.get("decided_by"),
            cross_check=self._cross_check_for(task_id, approval_id),
        )
