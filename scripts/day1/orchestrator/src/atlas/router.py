"""The 4-Way Router (Sections 7.1, 7.2, 8.3, 8.4, 8.5, 9.1, 16.2, Appendix A; CONVENTIONS.md §7.7, V16).

Order of evaluation, fixed (7.2; Appendix A "keyword hard rules -> Eleanor classifier -> task-force detection"):

  1. Keyword hard rules (7.2 rule 1): any hit on `hard_keywords` (family, medical, health, vault, trust, estate, will,
     children, and FAMILY_NAMES from /etc/atlas/atlas.env) routes to Arthur "regardless of anything else", reason
     "hard-rule:<keyword>". A typed [REN*] override on a hard hit is overruled and both facts are logged; [ARTHUR*]
     overrides and command overrides ([DEEP THINK:, [VAULT], [LOG STRIKE:, [EXECUTE AEGIS BACKUP]) are unaffected.
  2. Eleanor's resident classifier (7.2 rule 2, 5.3): a JSON verdict {hemisphere, persona, task_force, long_document,
     privacy_tags, deep_think_depth} from router-qwen3.5-4b. It decides only what the keywords missed; its verdict is
     still recorded on a hard hit (classifier_route) so the Principal can see the disagreement V16 proves. Its
     `deep_think_depth` is the router's task-weight estimate (9.1 second trigger).
  3. Task-force detection (7.2 rule 3, 8.3): the preset whose `triggers` match, mandatory; the preset fixes the owning
     director(s), the default tier, the domain cards (8.4: at most `max_domain_cards`, Tier C never speculatively)
     and, for TF_OMEGA, the Apex engine (6.1, 6.2). The first owner is the dispatch persona (8.5: "the director is
     the sole lead and the sole inference session"); `dispatch_engine` is that director's engine (8.5 step 4).
     `persona`/`engine` stay the synthesising hemisphere lead (16.1 rule 1, 8.5 step 7).
  4. Manual overrides (7.2 rule 4): a prefix from config/router-rules.json `overrides`, typed at the start of the
     message; longest matching key wins, case-sensitive (config/README.md). An override is the Principal's explicit
     order, so it is honoured only on Principal-typed messages: callers that route stored or inbound content
     (retention re-routing, /internal/route, email ingestion) pass `allow_overrides=False` and the prefix is
     recorded as "override-ignored:<key>" and treated as text.

Tier (16.2) is the maximum of: the preset's default; sensitive on a hard hit, a sensitive privacy tag, a hit on the
sensitive lexicon (payment, legal, financial, security words) or a security-adjacent domain card (8.2 posture:
31, 32, 34); and never below standard on an abliterated engine (6.1 C6 / R13: its output always passes the gate).

Every decision is written to ledger.routing_decisions with its reason (7.2 rule 5). The reason string and the logs
carry categories only, never the message or the classifier's raw output (the ledger stores message_sha256, not text).
The router never talks to a weight-bearing engine; the classifier is the resident 4B model, and tests stub it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.config import (
    HEMISPHERES,
    PERSONA_KEYS,
    TIERS,
    AtlasConfig,
    ConfigError,
    DomainCard,
    RouterRules,
    TaskForce,
)
from atlas.engines import EngineError, LlamaClient
from atlas.ledger import Ledger
from atlas.personas import HEMISPHERE_LEADS, PersonaRegistry

log = logging.getLogger("atlas.router")

__all__ = [
    "SENSITIVE_DOMAINS",
    "SENSITIVE_KEYWORDS",
    "SENSITIVE_TAGS",
    "Classifier",
    "ClassifierError",
    "ClassifierVerdict",
    "LlamaClassifier",
    "OverrideMatch",
    "OverrideSyntaxError",
    "Router",
    "RouterError",
    "RoutingDecision",
    "StubClassifier",
    "detect_task_force",
    "find_hard_keywords",
    "find_sensitive_keywords",
    "matching_override_key",
    "parse_override",
    "select_domain_cards",
]

TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(TIERS)}  # routine < standard < sensitive
DEEP_THINK_DEPTHS: tuple[str, ...] = ("quick", "standard", "deep")  # 9.1
# Override actions that name a persona route (router-rules.json `routes` keys). Every other action is a command.
PERSONA_ACTIONS: frozenset[str] = frozenset({"ren", "arthur", "ren-abliterated", "arthur-qwen", "arthur-abliterated"})
# UNVERIFIED: ~4 characters per token for English prose (a common rule of thumb) — the long-document trigger's
# fallback when the classifier gives no verdict; the classifier's `long_document` field is the primary signal and an
# exact count would need the resident tokenizer (LlamaClient.tokenize), which the router does not call per message.
CHARS_PER_TOKEN_ESTIMATE = 4
# Section 8.2 "Posture for the security-adjacent additions": domains 31, 32 and 34 inherit domain 1's defensive
# framing and "the approval gate treats anything they produce as sensitive tier". A dispatch carrying one of these
# cards is sensitive whatever the preset says.
SENSITIVE_DOMAINS: frozenset[int] = frozenset({31, 32, 34})
# Classifier privacy tags that make a matter sensitive (16.2: legal, financial, medical, estate, security, any payment;
# family and health are the hard-rule words, sensitive for the same reason a hard hit is).
SENSITIVE_TAGS: frozenset[str] = frozenset({"family", "medical", "health", "estate", "legal", "financial",
                                            "security", "payment"})
# The sensitive lexicon (16.2 "any payment"; 16.3 rules 1-2: money never moves and nothing is signed without the
# Principal). Matched like task-force triggers (whole word or phrase, plural-tolerant); a hit raises the tier only,
# never the route. Overridable from config/router-rules.json `sensitive_keywords` once atlas.config.RouterRules
# carries that key (contract for the config writer: a list of strings; until then this default applies).
SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "payment", "pay", "paid", "invoice", "transfer", "wire", "deposit", "refund", "remittance", "direct debit",
    "credit card", "debit card", "card number", "card details", "bank account", "account number", "bsb", "iban",
    "swift code", "payroll", "loan", "mortgage", "guarantee", "indemnity", "contract", "sign", "signature",
    "signing", "tax return", "password", "credential", "two-factor", "2fa", "one-time code", "otp",
)


class RouterError(RuntimeError):
    """The router could not decide; the message says why (rule §7.4: loud, never silent)."""


class OverrideSyntaxError(RouterError):
    """A payload-carrying override prefix ([DEEP THINK:, [LOG STRIKE:) has no closing bracket."""


class ClassifierError(RuntimeError):
    """The resident classifier gave no usable verdict (unreachable, non-JSON, or a schema miss)."""


# --- classifier ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifierVerdict:
    """Eleanor's JSON verdict (7.2 rule 2). Every field is optional: the router validates and ignores what is not."""

    hemisphere: str | None = None
    persona: str | None = None
    task_force: str | None = None
    long_document: bool = False
    privacy_tags: tuple[str, ...] = ()
    deep_think_depth: str | None = None  # 9.1: "Eleanor's classifier picks a depth"
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_json(cls, data: Mapping[str, Any], *, task_force_codes: Sequence[str] = ()) -> ClassifierVerdict:
        hemi = data.get("hemisphere")
        hemi = hemi.strip().lower() if isinstance(hemi, str) else None
        if hemi not in HEMISPHERES:
            hemi = None
        persona = data.get("persona")
        persona = persona.strip().lower() if isinstance(persona, str) else None
        if persona not in PERSONA_KEYS:
            persona = None
        tf = data.get("task_force")
        tf = tf.strip().upper() if isinstance(tf, str) else None
        if tf in {"", "NONE", "NULL"} or (task_force_codes and tf not in task_force_codes):
            tf = None
        tags_raw = data.get("privacy_tags") or ()
        tags = tuple(str(t).strip().lower() for t in tags_raw if str(t).strip()) if isinstance(tags_raw, list) else ()
        depth = data.get("deep_think_depth") or data.get("depth")
        depth = depth.strip().lower() if isinstance(depth, str) else None
        if depth not in DEEP_THINK_DEPTHS:
            depth = None
        return cls(hemisphere=hemi, persona=persona, task_force=tf, long_document=bool(data.get("long_document")),
                   privacy_tags=tags, deep_think_depth=depth, raw=dict(data))


class Classifier(Protocol):
    """What the router needs from Eleanor (7.2 rule 2)."""

    def classify(self, message: str, *, context: Mapping[str, Any] | None = None) -> ClassifierVerdict: ...


class StubClassifier:
    """Test double: returns a fixed verdict (or one computed by `verdict(message)`), or raises `fail`."""

    def __init__(self, verdict: ClassifierVerdict | Callable[[str], ClassifierVerdict] | None = None, *,
                 fail: Exception | None = None) -> None:
        self._verdict = verdict if verdict is not None else ClassifierVerdict()
        self._fail = fail
        self.calls: list[str] = []

    def classify(self, message: str, *, context: Mapping[str, Any] | None = None) -> ClassifierVerdict:
        self.calls.append(message)
        if self._fail is not None:
            raise self._fail
        if callable(self._verdict):
            return self._verdict(message)
        return self._verdict


CLASSIFIER_SYSTEM_PROMPT = """You are Eleanor Croft, the routing classifier of ATLAS. Read the Principal's message and \
answer with ONE JSON object and nothing else, with exactly these keys:
{"hemisphere": "corporate" | "estate",
 "persona": "ren" | "arthur",
 "task_force": one of the task-force codes listed below, or null,
 "long_document": true | false,
 "privacy_tags": [ "family" | "medical" | "health" | "estate" | "legal" | "financial" | "security" ... ],
 "deep_think_depth": "quick" | "standard" | "deep" | null}
Rules: corporate, enterprise, infrastructure, AEC and public-facing matters are "corporate" (persona "ren"); estate, \
private health, logistics, family and anything touching the Principal's private data are "estate" (persona "arthur"). \
Corporate work that brushes family context stays "corporate" with privacy_tags set. "long_document" is true when the \
message carries or clearly asks to process a long document. "task_force" is the single best-matching preset or null.
Task-force codes: %(codes)s"""

# VERIFIED: research/src/tools_server_server-common.cpp accepts `response_format` of type "json_object" (turned into a
# json_schema of {"type": "object"}, so the reply is grammar-constrained to a bare JSON object) and "json_schema";
# any other type is rejected with invalid_argument (HTTP 400). engines.json runs router-qwen3.5-4b with --reasoning
# off, so max_tokens is not consumed by thinking tokens. The 4xx retry below is a defensive fallback for a different
# server build; the JSON-object scan handles a server that ignores the field.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_HTTP_4XX_RE = re.compile(r"\bHTTP 4\d\d\b")


class LlamaClassifier:
    """Eleanor on the resident router model (5.3 router-qwen3.5-4b; engines.json `classifier_engine`)."""

    def __init__(self, client: LlamaClient, *, task_force_codes: Sequence[str] = (), max_tokens: int = 200,
                 temperature: float = 0.0, max_message_chars: int = 12_000) -> None:
        self._client = client
        self._codes = tuple(task_force_codes)
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_message_chars = max_message_chars
        self._system = CLASSIFIER_SYSTEM_PROMPT % {"codes": ", ".join(self._codes) or "(none)"}

    def classify(self, message: str, *, context: Mapping[str, Any] | None = None) -> ClassifierVerdict:
        # A very long message is classified on its head and tail; the router flags it long by size anyway.
        text = message
        if len(text) > self._max_message_chars:
            half = self._max_message_chars // 2
            text = f"{text[:half]}\n[...]\n{text[-half:]}"
        messages = [{"role": "system", "content": self._system}, {"role": "user", "content": text}]
        try:
            try:
                result = self._client.chat(messages, max_tokens=self._max_tokens, temperature=self._temperature,
                                           response_format={"type": "json_object"})
            except EngineError as first:
                # engines._raise_for_status formats "chat: <url> -> HTTP <code> <detail>"; only a 4xx (a rejected
                # parameter) earns the retry; a 5xx (loading, crashed) is a real outage.
                if not _HTTP_4XX_RE.search(str(first)):
                    raise
                log.warning("classifier: retrying without response_format (%s)", first)
                result = self._client.chat(messages, max_tokens=self._max_tokens, temperature=self._temperature)
        except EngineError as exc:
            raise ClassifierError(f"resident classifier unreachable or failed: {exc}") from exc
        text = result.text or ""
        m = _JSON_OBJECT_RE.search(text)
        # The model's raw output can quote the Principal's message; it goes to DEBUG only, never into an exception
        # message (which the router copies into the ledger reason and the ERROR log).
        if not m:
            log.debug("classifier: no JSON object in the reply: %r", text[:500])
            raise ClassifierError(f"resident classifier returned no JSON object (len={len(text)})")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            log.debug("classifier: invalid JSON in the reply: %r", m.group(0)[:500])
            raise ClassifierError(f"resident classifier returned invalid JSON (offset {exc.pos})") from exc
        if not isinstance(data, dict):
            raise ClassifierError(f"resident classifier returned a JSON {type(data).__name__}, not an object")
        return ClassifierVerdict.from_json(data, task_force_codes=self._codes)


# --- overrides (7.2 rule 4) -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideMatch:
    key: str  # the prefix as typed and configured, e.g. "[ARTHUR:LONG]"
    action: str  # the configured action, e.g. "arthur-qwen"
    payload: str | None  # text between a "...:"-style key and its closing "]"
    body: str  # the message with the prefix removed (payload kept for payload keys)


def matching_override_key(message: str, overrides: Mapping[str, str]) -> str | None:
    """The longest configured prefix at the start of the message, case-sensitive (config/README.md), or None."""
    text = message.lstrip()
    matches = [k for k in overrides if text.startswith(k)]
    return max(matches, key=len) if matches else None


def parse_override(message: str, overrides: Mapping[str, str]) -> OverrideMatch | None:
    """Longest configured prefix at the start of the message, case-sensitive (config/README.md matching rule)."""
    text = message.lstrip()
    key = matching_override_key(text, overrides)
    if key is None:
        return None
    rest = text[len(key):]
    if key.endswith(":"):
        close = rest.find("]")
        if close < 0:
            raise OverrideSyntaxError(f"override {key!r} needs a closing ']' (e.g. '{key} problem] ...')")
        payload = rest[:close].strip()
        body = f"{payload} {rest[close + 1:].strip()}".strip()
        return OverrideMatch(key=key, action=overrides[key], payload=payload, body=body)
    return OverrideMatch(key=key, action=overrides[key], payload=None, body=rest.strip())


# --- hard keywords (7.2 rule 1) -------------------------------------------------------------------------------------


def _keyword_regex(keyword: str) -> re.Pattern[str]:
    # Whole-word, case-insensitive; multi-word keywords (a family name with a space) match as a phrase.
    # Note: 7.2 rule 1 lists "will" verbatim; that matches the modal verb too. The document decides the list, and a
    # false positive routes to the defensive hemisphere, which is the safe direction (7.3).
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(keyword.strip()) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def find_hard_keywords(text: str, keywords: Sequence[str]) -> list[str]:
    """Every configured hard keyword (in config order) that occurs in the text as a whole word or phrase."""
    hits: list[str] = []
    for kw in keywords:
        if kw.strip() and _keyword_regex(kw).search(text):
            hits.append(kw)
    return hits


def find_sensitive_keywords(text: str, keywords: Sequence[str]) -> list[str]:
    """Every sensitive-lexicon entry (config order) found as a whole word or phrase, plural-tolerant (16.2)."""
    return [kw for kw in keywords if kw.strip() and _trigger_regex(kw).search(text)]


# --- task-force detection (7.2 rule 3) -------------------------------------------------------------------------------


def _trigger_regex(trigger: str) -> re.Pattern[str]:
    # Whole tokens or phrases (config/README.md), tolerant of a plural "s"/"es"; "m&a", "ci/cd", "earn-out" are safe
    # because the boundaries are alphanumeric lookarounds rather than \b.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(trigger.strip().lower()) + r"(?:e?s)?(?![A-Za-z0-9])",
                      re.IGNORECASE)


def detect_task_force(text: str, task_forces: Mapping[str, TaskForce]) -> tuple[TaskForce, list[str]] | None:
    """The preset with the most distinct trigger hits (tie: longest trigger, then config order); None when no hit.

    Presets whose `trigger` is "principal-only" (TF_OMEGA) are never detected from text; they are explicit only.
    """
    best: tuple[int, int, int, TaskForce, list[str]] | None = None
    for order, tf in enumerate(task_forces.values()):
        if tf.trigger == "principal-only" or not tf.triggers:
            continue
        hits = [t for t in tf.triggers if _trigger_regex(t).search(text)]
        if not hits:
            continue
        score = (len(hits), max(len(h) for h in hits), -order)
        if best is None or score > best[:3]:
            best = (*score, tf, hits)
    if best is None:
        return None
    return best[3], best[4]


# --- domain cards (8.4) --------------------------------------------------------------------------------------------


def _card_phrases(card: DomainCard) -> list[str]:
    """Phrases that count as naming the card's field: the multi-word parts of the H1 name split on '&', ',', '/',
    parentheses. A single word ("automotive", "pedagogy") is ordinary vocabulary, not an explicit match (8.4 rule 4:
    "only on an explicit match, never speculatively")."""
    parts = re.split(r"[&,/()]|\bincl\.\s*", card.name)
    phrases: list[str] = []
    for p in parts:
        p = p.strip(" .-").lower()
        if p and " " in p:
            phrases.append(p)
    return phrases


def card_explicitly_named(text: str, card: DomainCard) -> bool:
    """8.4 rule 4: a Tier C card loads only when the request names the field (or the card by number)."""
    if re.search(rf"\b(?:domain|card)\s*#?\s*0?{card.number}\b", text, re.IGNORECASE):
        return True
    for phrase in _card_phrases(card):
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
            return True
    return False


def select_domain_cards(text: str, task_force: TaskForce | None, cards: Mapping[int, DomainCard],
                        max_cards: int, explicit: Sequence[int] = ()) -> tuple[list[int], list[int]]:
    """(selected, dropped): the caller's explicit cards first, then the preset's cards, then Tier C cards the text
    names, capped at `max_cards` (8.4 rule 2).

    Tier C cards are included only when explicitly named or explicitly requested (8.4 rule 4), never from a preset.
    A text-inferred Tier C card is placed after the preset so a preset never loses one of its own cards to an
    inference. `dropped` lists the cards that did not fit; the caller must split the work into a relay (8.5), not
    widen the cap.
    """
    for n in explicit:
        if n not in cards:
            raise RouterError(f"explicit domain {n} has no card under config/domains/cards (CONVENTIONS.md §1)")
    chosen: list[int] = list(dict.fromkeys(explicit))
    if task_force is not None:
        for ref in task_force.domain_cards:
            card = cards.get(ref.domain)
            if card is None:
                raise RouterError(f"{task_force.code} names domain {ref.domain}, which has no card")
            if card.is_tier_c and ref.domain not in chosen:
                log.warning("%s preset names Tier C card %d; skipped (8.4 rule 4: explicit match only)",
                            task_force.code, ref.domain)
                continue
            if ref.domain not in chosen:
                chosen.append(ref.domain)
    for n, card in cards.items():
        if card.is_tier_c and n not in chosen and card_explicitly_named(text, card):
            chosen.append(n)
    return chosen[:max_cards], chosen[max_cards:]


# --- the decision --------------------------------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    """One routing decision (7.2 rule 5); `reason` is the human-readable why, `ledger_id` the routing_decisions row."""

    route: str  # a `routes` key of router-rules.json: ren, arthur, ren-abliterated, arthur-qwen, ...
    persona: str  # the hemisphere lead that synthesises and speaks: ren | arthur (16.1 rule 1, 8.5 step 7)
    hemisphere: str
    engine: str  # CONVENTIONS.md §8 engine key of the lead's route (or the Apex engine for TF_OMEGA / deep tier)
    tier: str  # routine | standard | sensitive (16.2)
    reason: str
    body: str  # the message with any override prefix removed
    message_sha256: str
    task_force: str | None = None
    directors: tuple[str, ...] = ()  # the preset's owners as persona keys (8.5 step 2)
    dispatch_persona: str = ""  # who runs the dispatch: the first owning director for a task force, else the lead
    dispatch_engine: str = ""  # the engine the Arbiter loads for it (8.5 step 4); == engine outside a task force
    domain_cards: tuple[int, ...] = ()  # <= max_domain_cards (8.4)
    dropped_cards: tuple[int, ...] = ()  # preset cards that did not fit: relay needed (8.5)
    privacy_tags: tuple[str, ...] = ()
    hard_keyword_hits: tuple[str, ...] = ()
    sensitive_hits: tuple[str, ...] = ()  # sensitive-lexicon hits (tier only, 16.2)
    override: str | None = None  # the prefix as typed
    override_action: str | None = None
    command: str | None = None  # deep-think | ouroboros-strike | aegis | vault-session
    deep_think_depth: str | None = None
    long_document: bool = False
    classifier_route: str | None = None  # what Eleanor said, for the record (V16: recorded even when overruled)
    classifier_verdict: ClassifierVerdict | None = field(default=None, repr=False)
    task_id: str | None = None
    ledger_id: int | None = None
    dual_sign_off: bool = False  # TF_OMEGA (8.3)

    def __post_init__(self) -> None:
        self.dispatch_persona = self.dispatch_persona or self.persona
        self.dispatch_engine = self.dispatch_engine or self.engine

    @property
    def hard_rule_hit(self) -> bool:
        return bool(self.hard_keyword_hits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route, "persona": self.persona, "hemisphere": self.hemisphere, "engine": self.engine,
            "tier": self.tier, "reason": self.reason, "task_force": self.task_force, "directors": list(self.directors),
            "dispatch_persona": self.dispatch_persona, "dispatch_engine": self.dispatch_engine,
            "domain_cards": list(self.domain_cards), "dropped_cards": list(self.dropped_cards),
            "privacy_tags": list(self.privacy_tags), "hard_keyword_hits": list(self.hard_keyword_hits),
            "sensitive_hits": list(self.sensitive_hits),
            "override": self.override, "override_action": self.override_action, "command": self.command,
            "deep_think_depth": self.deep_think_depth, "long_document": self.long_document,
            "classifier_route": self.classifier_route, "task_id": self.task_id, "ledger_id": self.ledger_id,
            "message_sha256": self.message_sha256, "dual_sign_off": self.dual_sign_off,
        }


def _max_tier(*tiers: str | None) -> str:
    best = "routine"
    for t in tiers:
        if t is None:
            continue
        if t not in TIER_ORDER:
            raise RouterError(f"unknown tier {t!r}; expected one of {TIERS} (CONVENTIONS.md §8)")
        if TIER_ORDER[t] > TIER_ORDER[best]:
            best = t
    return best


class Router:
    """The 4-Way Router. One instance per orchestrator process; `route()` is pure apart from the ledger write.

    `on_classifier_error`: "raise" (the default) makes a dead resident classifier a RouterError, so the caller
    returns a 5xx and the outage is a strike, never a silently routed answer (4.1 keeps the 4B model resident, V10a
    proves it; CONVENTIONS.md §7.4). "arthur" / "ren" are explicit degraded-mode opt-ins that route to that lead
    with the outage in the reason.
    """

    def __init__(self, config: AtlasConfig, classifier: Classifier, ledger: Ledger, *,
                 personas: PersonaRegistry | None = None, on_classifier_error: str = "raise") -> None:
        if on_classifier_error not in {"arthur", "ren", "raise"}:
            raise ValueError("on_classifier_error must be 'arthur', 'ren' or 'raise'")
        self.config = config
        self.rules: RouterRules = config.router_rules
        self.classifier = classifier
        self.ledger = ledger
        self.personas = personas or PersonaRegistry(config.personas, config.engines)
        self.on_classifier_error = on_classifier_error
        # Every route the rules can produce must resolve to an engine, checked once here rather than per message.
        for route in ("ren", "arthur"):
            if route not in self.rules.routes:
                raise ConfigError(f"router-rules.json routes must contain {route!r} (7.1)")
        for action in self.rules.overrides.values():
            if action in PERSONA_ACTIONS and action not in self.rules.routes:
                raise ConfigError(f"router-rules.json override action {action!r} has no route")
        self._hard_route = self.rules.hard_keyword_route
        if self._hard_route not in self.rules.routes:
            raise ConfigError(f"router-rules.json hard_keyword_route {self._hard_route!r} has no route")
        configured = getattr(self.rules, "sensitive_keywords", None)  # see SENSITIVE_KEYWORDS: config hook
        self.sensitive_keywords: tuple[str, ...] = tuple(configured) if configured else SENSITIVE_KEYWORDS

    # --- public -------------------------------------------------------------------------------------------------------

    def route(self, message: str, *, task_id: str | None = None, task_force: str | None = None,
              explicit_domains: Sequence[int] = (), context: Mapping[str, Any] | None = None,
              allow_overrides: bool = True) -> RoutingDecision:
        """Decide where a message goes and log it. `task_force` and `explicit_domains` are the explicit-command path
        (8.5 step 1 "or an explicit command"); TF_OMEGA can only arrive that way. `allow_overrides=False` is for
        text the Principal did not type (stored transcripts, inbound documents): a prefix is text, not an order."""
        reasons: list[str] = []
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()

        # 1. overrides (7.2 rule 4): parsed here, applied after the hard rules (7.2 rule 1 wins over them)
        ov: OverrideMatch | None = None
        if allow_overrides:
            ov = parse_override(message, self.rules.overrides)
        else:
            ignored = matching_override_key(message, self.rules.overrides)
            if ignored:
                reasons.append(f"override-ignored:{ignored}(not a Principal-typed message)")
        body = ov.body if ov else message.strip()
        command: str | None = None
        depth: str | None = None
        forced_route: str | None = None
        if ov:
            reasons.append(f"override:{ov.key}")
            if ov.action in PERSONA_ACTIONS:
                forced_route = ov.action
            elif ov.action.startswith("deep-think"):
                command = "deep-think"
                _, _, d = ov.action.partition(":")
                depth = d or None
            else:
                command = ov.action

        # 2. hard keywords (7.2 rule 1: "regardless of anything else", so a [REN*] override on a hit is overruled)
        hits = find_hard_keywords(body, self.rules.hard_keywords)
        if hits:
            reasons.append(f"hard-rule:{hits[0]}" + (f"(+{','.join(hits[1:])})" if len(hits) > 1 else ""))
            if forced_route is not None and not forced_route.startswith(self._hard_route):
                reasons.append(f"overruled-by-hard-rule:{hits[0]}(7.2 rule 1)")
                forced_route = None

        # 3. classifier (always consulted; its verdict is overruled by 1 and 2 but recorded, V16). Only the error's
        # category reaches the reason string: a ClassifierError message is built without model text, and a bug's
        # message could quote anything, so it goes to DEBUG.
        verdict: ClassifierVerdict | None = None
        classifier_error: str | None = None
        try:
            verdict = self.classifier.classify(body, context=context)
        except ClassifierError as exc:
            classifier_error = type(exc).__name__
            log.error("router: classifier failed: %s", exc)
        except Exception as exc:  # a classifier bug must not take the router down silently
            classifier_error = type(exc).__name__
            log.error("router: classifier raised %s (details at DEBUG)", type(exc).__name__)
            log.debug("router: classifier exception", exc_info=exc)
        classifier_route = None
        if verdict is not None:
            classifier_route = verdict.persona or (HEMISPHERE_LEADS.get(verdict.hemisphere or "") or None)

        # 4. the route
        if forced_route is not None:
            route = forced_route
        elif command == "vault-session":
            route = self._hard_route  # the vault is estate data (Sections 10.5, 11); its session is Arthur's
            reasons.append("vault-session:arthur")
        elif hits:
            route = self._hard_route
        elif classifier_route is not None:
            route = classifier_route
            reasons.append(f"classifier:{verdict.hemisphere or classifier_route}"
                           + (f"(tags {','.join(verdict.privacy_tags)})" if verdict and verdict.privacy_tags else ""))
        else:
            if self.on_classifier_error == "raise":
                raise RouterError(f"no route: classifier gave no verdict ({classifier_error or 'empty verdict'}); "
                                  f"the resident router model is down (4.1, 7.2 rule 2)")
            route = self.on_classifier_error
            reasons.append(f"classifier-unavailable({classifier_error or 'empty verdict'}):default-{route}")
            log.error("router: classifier unavailable (%s); degraded mode, defaulting to %s", classifier_error, route)
        if classifier_error and (forced_route is not None or hits):
            reasons.append(f"classifier-unavailable({classifier_error})")

        # long-document trigger (7.1 "Arthur, override: ... or long-document trigger")
        est_tokens = len(body) // CHARS_PER_TOKEN_ESTIMATE
        long_doc = bool(verdict and verdict.long_document) or est_tokens >= self.rules.long_document_tokens
        if long_doc:
            if route == "arthur" and "arthur-qwen" in self.rules.routes:
                route = "arthur-qwen"
                reasons.append("long-document:arthur-qwen")
            else:
                # No corporate long-document route exists in router-rules.json (7.1 names it for Arthur only); the
                # engine stays as routed and the flag is carried for the caller.
                reasons.append("long-document")

        persona = "ren" if route.startswith("ren") else "arthur" if route.startswith("arthur") else None
        if persona is None:
            raise RouterError(f"route {route!r} names no hemisphere lead (routes must start with ren/arthur)")
        hemisphere = self.personas[persona].hemisphere

        # Deep Think (9.1): "[DEEP THINK: problem] or the router's task-weight estimate. Eleanor's classifier picks a
        # depth; the Principal can force any depth with a prefix." A typed prefix wins; with no override at all the
        # classifier's depth is the task-weight trigger.
        if command == "deep-think" and depth is None and verdict and verdict.deep_think_depth:
            depth = verdict.deep_think_depth
            reasons.append(f"deep-think:{depth}(classifier)")
        elif command == "deep-think":
            reasons.append(f"deep-think:{depth or 'unset'}")
        elif ov is None and verdict and verdict.deep_think_depth:
            command, depth = "deep-think", verdict.deep_think_depth
            reasons.append(f"deep-think:{depth}(classifier task-weight, 9.1)")

        # engine
        if command == "deep-think" and depth == "deep":
            if "deep-think:deep" not in self.rules.routes:
                raise ConfigError("router-rules.json routes has no 'deep-think:deep' (9.1 deep tier, Apex engine)")
            engine = self.rules.routes["deep-think:deep"]
        else:
            engine = self.rules.routes[route]
        if engine not in self.config.engines:
            raise ConfigError(f"route {route!r} names engine {engine!r}, which is not in engines.json")

        # 5. task force (7.2 rule 3, 8.3, 8.5 steps 1-2)
        tf: TaskForce | None = None
        if task_force is not None:
            tf = self.config.task_forces.get(task_force)
            if tf is None:
                raise RouterError(f"unknown task force {task_force!r} (config/task-forces.json)")
            reasons.append(f"task-force:{tf.code}(explicit)")
        else:
            found = detect_task_force(body, self.config.task_forces)
            if found:
                tf, matched = found
                reasons.append(f"task-force:{tf.code}(trigger {','.join(repr(m) for m in matched[:3])})")
            elif verdict and verdict.task_force and verdict.task_force in self.config.task_forces:
                cand = self.config.task_forces[verdict.task_force]
                if cand.trigger != "principal-only":
                    tf = cand
                    reasons.append(f"task-force:{tf.code}(classifier)")
        if tf is not None and tf.engine:
            # 6.1 "Apex escalation: DeepSeek V4 Flash, on ... TF_OMEGA"; 6.2 Gideon "Apex engine for TF_OMEGA".
            if tf.engine not in self.config.engines:
                raise ConfigError(f"task-forces.json: {tf.code} names engine {tf.engine!r}, not in engines.json")
            engine = tf.engine
            reasons.append(f"engine:{tf.engine}(apex, 6.1 {tf.code})")
        directors: tuple[str, ...] = ()
        if tf is not None:
            directors = tuple(self.personas.key_for_name(o) for o in tf.owners)

        # dispatch (8.5: "the director is the sole lead and the sole inference session for a task force"; step 4
        # "the Engine Arbiter loads the director's engine"). Commands (Deep Think, vault, strike, AEGIS) are the
        # lead's own flows (9.1, 9.4, 9.5, 11) and keep the lead as the dispatch persona.
        dispatch_persona, dispatch_engine = persona, engine
        if tf is not None and directors and command is None:
            dispatch_persona = directors[0]
            # engine_for refuses an engine the director is not bound to (6.1 C6, 16.3 rule 6): a preset naming the
            # Apex engine for a director whose file does not list it is a ConfigError, not a silent widening.
            dispatch_engine = self.personas.engine_for(dispatch_persona, tf.engine or None)
            reasons.append(f"dispatch:{dispatch_persona}@{dispatch_engine}(8.5 step 4)")

        # 6. domain cards (8.4), before the tier: a security-adjacent card makes the dispatch sensitive (8.2)
        selected, dropped = select_domain_cards(body, tf, self.config.domain_cards, self.rules.max_domain_cards,
                                                explicit_domains)
        if dropped:
            reasons.append(f"cards-dropped:{dropped}(8.4 rule 2: relay needed)")

        # 7. tier (16.2): the maximum of the preset default (TF_RHO/TF_CHI are routine, 8.3), sensitive on a hard hit
        # (family/medical/estate are 16.2's sensitive examples), a sensitive privacy tag, a sensitive-lexicon hit
        # (any payment, legal, financial, security), a security-adjacent card (8.2 posture), and never routine on an
        # abliterated engine (6.1 C6 addendum / R13: "its output passes the same approval gate").
        tags = list(dict.fromkeys([h.lower() for h in hits] + list(verdict.privacy_tags if verdict else ())))
        tag_hits = sorted(set(tags) & SENSITIVE_TAGS)
        lexicon_hits = find_sensitive_keywords(body, self.sensitive_keywords)
        domain_hits = sorted(set(selected) & SENSITIVE_DOMAINS)
        abliterated = route.endswith("-abliterated")
        tier = _max_tier(
            tf.default_tier if tf else "standard",
            "sensitive" if hits else None,
            "sensitive" if tag_hits else None,
            "sensitive" if lexicon_hits else None,
            "sensitive" if domain_hits else None,
            "standard" if abliterated else None,
        )
        if hits and tier == "sensitive":
            reasons.append("tier:sensitive(hard-rule)")
        if tag_hits and not hits and tier == "sensitive":
            reasons.append(f"tier:sensitive(privacy-tags {','.join(tag_hits)}, 16.2)")
        if lexicon_hits and tier == "sensitive":
            reasons.append(f"tier:sensitive(lexicon {','.join(repr(h) for h in lexicon_hits[:3])}, 16.2)")
        if domain_hits and tier == "sensitive":
            reasons.append(f"tier:sensitive(domain {'/'.join(str(n) for n in domain_hits)}, 8.2 posture)")
        if abliterated and tier == "standard" and not (tf and tf.default_tier == "standard"):
            reasons.append("tier:standard(abliterated, R13)")
        if tf and not any(r.startswith("tier:") for r in reasons):
            reasons.append(f"tier:{tier}({tf.code})")

        decision = RoutingDecision(
            route=route, persona=persona, hemisphere=hemisphere, engine=engine, tier=tier,
            reason="; ".join(reasons), body=body, message_sha256=digest, task_force=tf.code if tf else None,
            directors=directors, dispatch_persona=dispatch_persona, dispatch_engine=dispatch_engine,
            domain_cards=tuple(selected), dropped_cards=tuple(dropped), privacy_tags=tuple(tags),
            hard_keyword_hits=tuple(hits), sensitive_hits=tuple(lexicon_hits),
            override=ov.key if ov else None, override_action=ov.action if ov else None,
            command=command, deep_think_depth=depth, long_document=long_doc, classifier_route=classifier_route,
            classifier_verdict=verdict, task_id=task_id, dual_sign_off=bool(tf and tf.dual_sign_off),
        )
        self._log(decision)
        return decision

    # --- ledger (7.2 rule 5) ------------------------------------------------------------------------------------------

    def _log(self, d: RoutingDecision) -> None:
        # The engine column carries the engine the Arbiter will load (the director's on a task-force dispatch).
        d.ledger_id = self.ledger.insert_routing_decision(
            task_id=d.task_id, route=d.route, reason=d.reason, engine=d.dispatch_engine,
            message_sha256=d.message_sha256, hard_keyword_hit=",".join(d.hard_keyword_hits) or None,
            override=d.override, classifier_route=d.classifier_route, task_force=d.task_force, tier=d.tier,
        )
        log.info("route=%s persona=%s engine=%s dispatch=%s@%s tier=%s tf=%s cards=%s tags=%s reason=%s ledger=%s",
                 d.route, d.persona, d.engine, d.dispatch_persona, d.dispatch_engine, d.tier, d.task_force,
                 list(d.domain_cards), list(d.privacy_tags), d.reason, d.ledger_id)
