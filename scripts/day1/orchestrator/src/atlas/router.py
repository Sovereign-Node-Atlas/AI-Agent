"""The 4-Way Router (Sections 7.1, 7.2, 8.3, 8.4, 9.1, Appendix A; CONVENTIONS.md §7.7, V16).

Order of evaluation, fixed (7.2; Appendix A "keyword hard rules -> Eleanor classifier -> task-force detection"):

  1. Manual overrides (7.2 rule 4): a prefix from config/router-rules.json `overrides`, typed at the start of the
     message; longest matching key wins, case-sensitive (config/README.md). An override is the Principal's explicit
     order, so it wins over everything else and is logged.
  2. Keyword hard rules (7.2 rule 1): any hit on `hard_keywords` (family, medical, health, vault, trust, estate, will,
     children, and FAMILY_NAMES from /etc/atlas/atlas.env) routes to Arthur regardless of the classifier, reason
     "hard-rule:<keyword>".
  3. Eleanor's resident classifier (7.2 rule 2, 5.3): a JSON verdict {hemisphere, persona, task_force, long_document,
     privacy_tags} from router-qwen3.5-4b. It decides only what the keywords missed; its verdict is still recorded on
     a hard hit (classifier_route) so the Principal can see the disagreement V16 proves.
  4. Task-force detection (7.2 rule 3, 8.3): the preset whose `triggers` match, mandatory; the preset fixes the owning
     director(s), the default tier and the domain cards (8.4: at most `max_domain_cards`, Tier C never speculatively).

Every decision is written to ledger.routing_decisions with its reason (7.2 rule 5). The router never talks to a
weight-bearing engine; the classifier is the resident 4B model, and tests stub it (StubClassifier).
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

# UNVERIFIED: llama-server accepts OpenAI-style `response_format: {"type": "json_object"}` on /v1/chat/completions
# (llama.cpp tools/server README, from memory). The classifier sends it, and on a 4xx retries once without it, then
# parses the first JSON object out of the text either way, so a server that ignores the field still works.
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
        m = _JSON_OBJECT_RE.search(result.text or "")
        if not m:
            raise ClassifierError(f"resident classifier returned no JSON object: {result.text[:200]!r}")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise ClassifierError(f"resident classifier returned invalid JSON: {exc}: {m.group(0)[:200]!r}") from exc
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


def parse_override(message: str, overrides: Mapping[str, str]) -> OverrideMatch | None:
    """Longest configured prefix at the start of the message, case-sensitive (config/README.md matching rule)."""
    text = message.lstrip()
    matches = [k for k in overrides if text.startswith(k)]
    if not matches:
        return None
    key = max(matches, key=len)
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
    """Phrases that count as naming the card's field: the H1 name split on '&', ',', '/', parentheses."""
    parts = re.split(r"[&,/()]|\bincl\.\s*", card.name)
    phrases: list[str] = []
    for p in parts:
        p = p.strip(" .-").lower()
        if not p:
            continue
        if " " in p or len(p) >= 6:  # a single short word ("game") is not an explicit match
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
    """(selected, dropped): explicit matches first, then the preset's cards, capped at `max_cards` (8.4 rule 2).

    Tier C cards are included only when explicitly named or explicitly requested (8.4 rule 4), never from a preset.
    `dropped` lists preset cards that did not fit; the caller must split the work into a relay (8.5), not widen.
    """
    for n in explicit:
        if n not in cards:
            raise RouterError(f"explicit domain {n} has no card under config/domains/cards (CONVENTIONS.md §1)")
    chosen: list[int] = list(dict.fromkeys(explicit))
    for n, card in cards.items():
        if card.is_tier_c and n not in chosen and card_explicitly_named(text, card):
            chosen.append(n)
    preset: list[int] = []
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
                preset.append(ref.domain)
    ordered = chosen + preset
    return ordered[:max_cards], ordered[max_cards:]


# --- the decision --------------------------------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    """One routing decision (7.2 rule 5); `reason` is the human-readable why, `ledger_id` the routing_decisions row."""

    route: str  # a `routes` key of router-rules.json: ren, arthur, ren-abliterated, arthur-qwen, ...
    persona: str  # the hemisphere lead that speaks: ren | arthur (16.1 rule 1)
    hemisphere: str
    engine: str  # CONVENTIONS.md §8 engine key
    tier: str  # routine | standard | sensitive (16.2)
    reason: str
    body: str  # the message with any override prefix removed
    message_sha256: str
    task_force: str | None = None
    directors: tuple[str, ...] = ()  # the preset's owners as persona keys (8.5 step 2)
    domain_cards: tuple[int, ...] = ()  # <= max_domain_cards (8.4)
    dropped_cards: tuple[int, ...] = ()  # preset cards that did not fit: relay needed (8.5)
    privacy_tags: tuple[str, ...] = ()
    hard_keyword_hits: tuple[str, ...] = ()
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

    @property
    def hard_rule_hit(self) -> bool:
        return bool(self.hard_keyword_hits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route, "persona": self.persona, "hemisphere": self.hemisphere, "engine": self.engine,
            "tier": self.tier, "reason": self.reason, "task_force": self.task_force, "directors": list(self.directors),
            "domain_cards": list(self.domain_cards), "dropped_cards": list(self.dropped_cards),
            "privacy_tags": list(self.privacy_tags), "hard_keyword_hits": list(self.hard_keyword_hits),
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
    """The 4-Way Router. One instance per orchestrator process; `route()` is pure apart from the ledger write."""

    def __init__(self, config: AtlasConfig, classifier: Classifier, ledger: Ledger, *,
                 personas: PersonaRegistry | None = None, on_classifier_error: str = "arthur") -> None:
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

    # --- public -------------------------------------------------------------------------------------------------------

    def route(self, message: str, *, task_id: str | None = None, task_force: str | None = None,
              explicit_domains: Sequence[int] = (), context: Mapping[str, Any] | None = None) -> RoutingDecision:
        """Decide where a message goes and log it. `task_force` and `explicit_domains` are the explicit-command path
        (8.5 step 1 "or an explicit command"); TF_OMEGA can only arrive that way."""
        reasons: list[str] = []
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()

        # 1. overrides
        ov = parse_override(message, self.rules.overrides)
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

        # 2. hard keywords
        hits = find_hard_keywords(body, self.rules.hard_keywords)
        if hits:
            reasons.append(f"hard-rule:{hits[0]}" + (f"(+{','.join(hits[1:])})" if len(hits) > 1 else ""))

        # 3. classifier (always consulted; its verdict is overruled by 1 and 2 but recorded, V16)
        verdict: ClassifierVerdict | None = None
        classifier_error: str | None = None
        try:
            verdict = self.classifier.classify(body, context=context)
        except ClassifierError as exc:
            classifier_error = str(exc)
        except Exception as exc:  # a classifier bug must not take the router down silently
            classifier_error = f"{type(exc).__name__}: {exc}"
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
                raise RouterError(f"no route: classifier gave no verdict ({classifier_error or 'empty verdict'})")
            route = self.on_classifier_error
            reasons.append(f"classifier-unavailable({classifier_error or 'empty verdict'}):default-{route}")
            log.error("router: classifier unavailable (%s); defaulting to %s", classifier_error, route)
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

        # engine
        if command == "deep-think" and depth is None and verdict and verdict.deep_think_depth:
            depth = verdict.deep_think_depth
            reasons.append(f"deep-think:{depth}(classifier)")
        elif command == "deep-think":
            reasons.append(f"deep-think:{depth or 'unset'}")
        if command == "deep-think" and depth == "deep":
            if "deep-think:deep" not in self.rules.routes:
                raise ConfigError("router-rules.json routes has no 'deep-think:deep' (9.1 deep tier, Apex engine)")
            engine = self.rules.routes["deep-think:deep"]
        else:
            engine = self.rules.routes[route]
        if engine not in self.config.engines:
            raise ConfigError(f"route {route!r} names engine {engine!r}, which is not in engines.json")

        # 5. task force
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
        directors: tuple[str, ...] = ()
        if tf is not None:
            directors = tuple(self.personas.key_for_name(o) for o in tf.owners)

        # 6. tier (16.2): a preset sets its default (TF_RHO/TF_CHI are routine, 8.3); a hard hit is sensitive
        # (family/medical/estate are 16.2's sensitive examples) and wins; standard when nothing says otherwise.
        tier = _max_tier(tf.default_tier if tf else "standard", "sensitive" if hits else None)
        if tf:
            reasons.append(f"tier:{tier}({tf.code})")
        elif hits:
            reasons.append("tier:sensitive(hard-rule)")

        # 7. domain cards (8.4)
        selected, dropped = select_domain_cards(body, tf, self.config.domain_cards, self.rules.max_domain_cards,
                                                explicit_domains)
        if dropped:
            reasons.append(f"cards-dropped:{dropped}(8.4 rule 2: relay needed)")

        tags = list(dict.fromkeys([h.lower() for h in hits] + list(verdict.privacy_tags if verdict else ())))

        decision = RoutingDecision(
            route=route, persona=persona, hemisphere=hemisphere, engine=engine, tier=tier,
            reason="; ".join(reasons), body=body, message_sha256=digest, task_force=tf.code if tf else None,
            directors=directors, domain_cards=tuple(selected), dropped_cards=tuple(dropped), privacy_tags=tuple(tags),
            hard_keyword_hits=tuple(hits), override=ov.key if ov else None, override_action=ov.action if ov else None,
            command=command, deep_think_depth=depth, long_document=long_doc, classifier_route=classifier_route,
            classifier_verdict=verdict, task_id=task_id, dual_sign_off=bool(tf and tf.dual_sign_off),
        )
        self._log(decision)
        return decision

    # --- ledger (7.2 rule 5) ------------------------------------------------------------------------------------------

    def _log(self, d: RoutingDecision) -> None:
        d.ledger_id = self.ledger.insert_routing_decision(
            task_id=d.task_id, route=d.route, reason=d.reason, engine=d.engine, message_sha256=d.message_sha256,
            hard_keyword_hit=",".join(d.hard_keyword_hits) or None, override=d.override,
            classifier_route=d.classifier_route, task_force=d.task_force, tier=d.tier,
        )
        log.info("route=%s persona=%s engine=%s tier=%s tf=%s cards=%s tags=%s reason=%s ledger=%s",
                 d.route, d.persona, d.engine, d.tier, d.task_force, list(d.domain_cards), list(d.privacy_tags),
                 d.reason, d.ledger_id)
