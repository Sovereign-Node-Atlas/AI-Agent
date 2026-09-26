"""Governance code paths: never-delegate rewrite, register, disclosure check (Sections 6.4, 16.1, 16.5, Appendix A).

These are code, not prompt instructions (16.1 rule 3 "hard code path"): a persona may forget a rule in its prompt,
the pipeline may not. Appendix A places them between synthesis and the outbound gate:

    Director output -> Ren/Arthur synthesis -> never-delegate rewrite pass; register set -> outbound gate

  never_delegate_rewrite(text)   16.1 rule 5: "A rewrite pass removes any task-shaped request to the Principal; only
                                 decisions and approvals may be asked." Imperative patterns on the rule list are
                                 rewritten into a decision request; anything else that looks task-shaped is flagged,
                                 never mangled, and the caller (the approval queue) holds a flagged draft.
  register(text, audience)       6.4: the register is a property the orchestrator sets on the outbound draft.
  disclosure_check(text)         16.1 rule 4 / 16.5 / R12: any admission of AI nature in an external draft is flagged;
                                 the approval gate never auto-sends a draft that admits it.

Everything is deterministic and needs no model; the unit tests (tests/test_governance.py) fix the behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "AUDIENCES",
    "DisclosureResult",
    "Draft",
    "Register",
    "RewriteResult",
    "disclosure_check",
    "never_delegate_rewrite",
    "register",
]

# The system's name as it refers to itself in a decision request. Fixed text, not a persona name: a decision request
# is spoken by the hemisphere lead on the system's behalf (16.1 rule 1).
SELF_NAME = "ATLAS"
DECISION_PREFIX = "Decision needed:"

# --- never-delegate (16.1 rule 5) -------------------------------------------------------------------------------------

# Rule list, class 1: imperative leads that leave a base-form verb phrase behind them. The phrase after the lead is
# kept verbatim (pronouns swapped) inside "Decision needed: shall ATLAS <phrase>?", so nothing is invented.
# Order matters only for readability; matching takes the longest lead that fits at the sentence start.
REWRITE_LEADS: tuple[str, ...] = (
    r"please\s+(?:kindly\s+)?",
    r"kindly\s+",
    r"can\s+you\s+(?:please\s+)?",
    r"could\s+you\s+(?:please\s+)?",
    r"would\s+you\s+(?:please\s+)?(?:be\s+able\s+to\s+)?",
    r"will\s+you\s+(?:please\s+)?",
    r"you\s+will\s+need\s+to\s+",
    r"you'll\s+need\s+to\s+",
    r"you\s+need\s+to\s+",
    r"you\s+will\s+have\s+to\s+",
    r"you'll\s+have\s+to\s+",
    r"you\s+have\s+to\s+",
    r"you\s+should\s+",
    r"you\s+must\s+",
    r"you\s+ought\s+to\s+",
    r"i\s+need\s+you\s+to\s+",
    r"i'll\s+need\s+you\s+to\s+",
    r"i\s+will\s+need\s+you\s+to\s+",
    r"i\s+would\s+like\s+you\s+to\s+",
    r"i'd\s+like\s+you\s+to\s+",
    r"make\s+sure\s+(?:that\s+)?you\s+",
    r"be\s+sure\s+to\s+",
    r"don't\s+forget\s+to\s+",
    r"do\s+not\s+forget\s+to\s+",
    r"remember\s+to\s+",
)
# Verbs that make a lead a task (not a decision): "please confirm" / "please approve" / "please decide" are the
# permitted decision requests and are left alone.
DECISION_VERBS: frozenset[str] = frozenset({
    "approve", "reject", "confirm", "decide", "choose", "pick", "select", "authorise", "authorize", "sign", "advise",
    "consider", "review", "tell", "let", "say", "indicate", "note", "see", "find", "expect", "accept", "decline",
    "allow", "permit", "agree", "disagree", "prefer", "rank", "rate", "vote", "answer", "reply", "respond",
})
# "find" is a decision verb only in "please find attached"; "can you find the invoices" is a task. Handled below.
FIND_TASK = re.compile(r"^find\s+(?!attached\b|enclosed\b|below\b|here\b)", re.IGNORECASE)

# Rule list, class 2: task-shaped phrasings whose remainder is not a base-form verb phrase (a participle, a noun, a
# clause). Rewriting them mechanically would mangle grammar, so they are flagged for the approval gate instead.
FLAG_PATTERNS: tuple[str, ...] = (
    r"\blet\s+me\s+know\s+(?:when|once|after|as\s+soon\s+as)\s+you(?:'ve|\s+have)\b",
    r"\blet\s+me\s+know\s+(?:when|once|after)\s+(?:it|that|this|they)\s+(?:is|are|has|have)\b",
    r"\b(?:when|once|after)\s+you(?:'ve|\s+have)\s+(?:gathered|collected|sent|found|located|compiled|prepared|"
    r"uploaded|forwarded|scanned|printed|signed\s+and\s+returned|dug\s+out|pulled)\b",
    r"\bget\s+back\s+to\s+me\s+with\b",
    r"\b(?:send|forward|email|upload|attach|give|bring|get|fetch|dig\s+out|pull|scan|print|photograph)\s+"
    r"(?:me|us)\s+(?:the|a|an|your|those|these|all|any|every|copies|scans|photos)\b",
    r"\bsend\s+(?:it|them|those|these|that|this|the\s+\w+)\s+(?:over\s+)?to\s+(?:me|us)\b",
    r"\bit\s+is\s+(?:up\s+to|on)\s+you\s+to\s+(?!decide|approve|choose|confirm|say)\w+",
    r"\bit's\s+(?:up\s+to|on)\s+you\s+to\s+(?!decide|approve|choose|confirm|say)\w+",
    r"\byou\s+(?:are|'re)\s+(?:going\s+to\s+)?(?:responsible|expected|required|going)\s+(?:for|to)\b",
    r"\b(?:your|the)\s+(?:homework|to-do|action\s+items?|next\s+steps?)\s+(?:is|are|will\s+be)\s+to\b",
    r"\bon\s+your\s+(?:end|side)\s*,?\s*(?:please\s+)?(?:you\s+)?(?:will\s+)?(?:need|have)\s+to\b",
)
_REWRITE_LEAD_RE = re.compile(r"^\s*(?P<lead>" + "|".join(REWRITE_LEADS) + r")(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)
_FLAG_RE = tuple(re.compile(p, re.IGNORECASE) for p in FLAG_PATTERNS)
# Sentence splitter: end punctuation followed by whitespace, or a line break. Bullets and numbering survive.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
_PRONOUN_SWAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bto\s+me\b", re.IGNORECASE), "to you"),
    (re.compile(r"\bfor\s+me\b", re.IGNORECASE), "for you"),
    (re.compile(r"\bwith\s+me\b", re.IGNORECASE), "with you"),
    (re.compile(r"\bme\b", re.IGNORECASE), "you"),
    (re.compile(r"\bmyself\b", re.IGNORECASE), "yourself"),
    (re.compile(r"\bmy\b", re.IGNORECASE), "your"),
    (re.compile(r"\bmine\b", re.IGNORECASE), "yours"),
    (re.compile(r"\bour\b", re.IGNORECASE), "your"),
    (re.compile(r"\bus\b", re.IGNORECASE), "you"),
)


@dataclass(frozen=True)
class RewriteResult:
    """What never_delegate_rewrite did. `text` is safe to send; `flagged` holds what a human must look at."""

    text: str
    original: str
    rewrites: tuple[tuple[str, str], ...] = ()  # (original sentence, rewritten sentence)
    flagged: tuple[str, ...] = ()  # sentences left in place because a mechanical rewrite would mangle them

    @property
    def changed(self) -> bool:
        return self.text != self.original

    @property
    def clean(self) -> bool:
        """True when nothing task-shaped remains for a human to look at."""
        return not self.flagged


def _swap_pronouns(phrase: str) -> str:
    out = phrase
    for pat, rep in _PRONOUN_SWAP:
        out = pat.sub(rep, out)
    return out


def _rewrite_sentence(sentence: str) -> str | None:
    """Rewrite one sentence when it starts with a class-1 lead followed by a task verb; None when not applicable."""
    m = _REWRITE_LEAD_RE.match(sentence)
    if not m:
        return None
    rest = m.group("rest").strip()
    if not rest:
        return None
    first_word = re.split(r"[\s,.;:!?]", rest, maxsplit=1)[0].lower()
    if first_word in DECISION_VERBS and not (first_word == "find" and FIND_TASK.match(rest)):
        return None  # a decision or approval request: permitted (16.1 rule 5)
    if not re.match(r"^[a-z]+", first_word):
        return None  # not a verb phrase; leave for the flag pass
    body = rest.rstrip(" .!?").rstrip()
    body = _swap_pronouns(body)
    # Keep any trailing parenthetical or clause verbatim; only the lead changes and the request becomes a question.
    return f"{DECISION_PREFIX} shall {SELF_NAME} {body}?"


def never_delegate_rewrite(text: str) -> RewriteResult:
    """16.1 rule 5. Rewrites rule-list imperatives to the Principal into decision requests; flags the rest.

    Returns the rewritten text plus the list of rewrites and flags. The text is never mangled: a sentence is either
    rewritten by a fixed template (lead removed, pronouns swapped, "Decision needed: shall ATLAS ...?") or left
    exactly as it was and listed in `flagged`.
    """
    if not text.strip():
        return RewriteResult(text=text, original=text)
    pieces: list[str] = []
    rewrites: list[tuple[str, str]] = []
    flagged: list[str] = []
    pos = 0
    # Walk the sentences keeping the separators so the output keeps the author's layout.
    for m in _SENTENCE_SPLIT.finditer(text):
        pieces.append(text[pos:m.start()])
        pieces.append(text[m.start():m.end()])
        pos = m.end()
    pieces.append(text[pos:])
    out: list[str] = []
    for i, piece in enumerate(pieces):
        if i % 2 == 1 or not piece.strip():  # separator, or whitespace-only
            out.append(piece)
            continue
        lead_ws = piece[: len(piece) - len(piece.lstrip())]
        core = piece.strip()
        # Preserve a bullet or numbering prefix.
        bm = re.match(r"^(?P<bullet>(?:[-*•]|\d+[.)])\s+)(?P<body>.*)$", core, re.DOTALL)
        bullet, body = (bm.group("bullet"), bm.group("body")) if bm else ("", core)
        rewritten = _rewrite_sentence(body)
        if rewritten is not None:
            rewrites.append((body, rewritten))
            out.append(f"{lead_ws}{bullet}{rewritten}")
            continue
        if any(p.search(body) for p in _FLAG_RE):
            flagged.append(body)
        out.append(piece)
    return RewriteResult(text="".join(out), original=text, rewrites=tuple(rewrites), flagged=tuple(flagged))


# --- register (6.4) -------------------------------------------------------------------------------------------------


class Register(StrEnum):
    """6.4: Ren and Arthur speak differently to the Principal than to external recipients; directors are terse
    internally and professional externally."""

    PRINCIPAL = "principal"  # candid, compressed, first-name, no ceremony (persona files "Register" sections)
    EXTERNAL = "external"  # polished, professional, role-appropriate, in character
    INTERNAL = "internal"  # terse: result, significance, action (director -> lead, lead -> director)


# Audience names the pipeline may set, and the register each one gets. "director"/"lead"/"system" are the internal
# relays of 8.5 step 6 (output relays to the next director) and step 7 (reports to Ren or Arthur).
AUDIENCES: dict[str, Register] = {
    "principal": Register.PRINCIPAL,
    "external": Register.EXTERNAL,
    "recipient": Register.EXTERNAL,
    "internal": Register.INTERNAL,
    "director": Register.INTERNAL,
    "lead": Register.INTERNAL,
    "system": Register.INTERNAL,
}


@dataclass(frozen=True)
class Draft:
    """An outbound draft with its register set as a property (6.4), never as a prompt instruction."""

    text: str
    audience: str
    register: Register
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_external(self) -> bool:
        return self.register is Register.EXTERNAL


def register(text: str, audience: str) -> Draft:
    """Set the register property on a draft from its audience. Unknown audiences fail loudly (never guessed)."""
    key = audience.strip().lower()
    if key not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; expected one of {sorted(AUDIENCES)} (Section 6.4)")
    return Draft(text=text, audience=key, register=AUDIENCES[key])


# --- disclosure (16.1 rule 4) ---------------------------------------------------------------------------------------

# Admissions of AI nature. Word-boundary, case-insensitive unless noted. Deliberately broad: a false positive costs one
# human look at the approval queue; a false negative is the R12 legal exposure.
DISCLOSURE_PATTERNS: tuple[str, ...] = (
    r"\bas\s+an?\s+(?:ai|a\.i\.|artificial\s+intelligence|language\s+model|llm|machine|bot|chatbot|"
    r"virtual\s+assistant|digital\s+assistant|automated\s+(?:system|assistant|agent))\b",
    r"\bi\s*(?:am|'m)\s+(?:an?\s+|not\s+(?:a\s+)?)?(?:ai|a\.i\.|artificial\s+intelligence|language\s+model|llm|"
    r"bot|chatbot|robot|machine|virtual\s+assistant|digital\s+assistant|automated\s+(?:system|assistant|agent)|"
    r"computer\s+program|software|algorithm|neural\s+network)\b",
    r"\bi\s*(?:am|'m)\s+not\s+(?:a\s+)?(?:human|person|real\s+person|human\s+being)\b",
    r"\b(?:large\s+)?language\s+model\b",
    r"\bartificial\s+intelligence\b",
    r"\bmachine[\s-]learning\s+model\b",
    r"\bneural\s+network\b",
    r"\b(?:ai|machine)[\s-]generated\b",
    r"\bgenerated\s+by\s+(?:an?\s+)?(?:ai|machine|model|llm|bot)\b",
    r"\b(?:my|our)\s+(?:training\s+data|model\s+weights|context\s+window|system\s+prompt|knowledge\s+cutoff)\b",
    r"\bi\s+(?:was|am|have\s+been)\s+(?:trained|programmed|prompted|fine-tuned)\b",
    r"\b(?:an?\s+)?(?:ai|virtual|digital|automated)\s+(?:assistant|agent|persona|system)\b",
    r"\b(?:chatbot|chat\s+bot)\b",
    r"\bthis\s+(?:message|email|reply|response)\s+(?:was|is)\s+(?:written|generated|drafted|composed)\s+"
    r"(?:automatically|by\s+(?:an?\s+)?(?:ai|machine|model|bot|computer))\b",
    r"\b(?:gpt-?oss|gpt-?\d|chatgpt|claude|llama|qwen|nemotron|deepseek|mistral|gemini|openai|anthropic)\b",
)
_DISCLOSURE_RE = tuple(re.compile(p, re.IGNORECASE) for p in DISCLOSURE_PATTERNS)
_LLM_UPPER_RE = re.compile(r"\bLLMs?\b")  # case-sensitive: the acronym, not a word that happens to spell it
_AI_UPPER_RE = re.compile(r"\bA\.?I\.?\b")  # "AI" / "A.I." as a standalone capitalised token


@dataclass(frozen=True)
class DisclosureResult:
    hits: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.hits

    @property
    def disclosed(self) -> bool:
        return bool(self.hits)


def disclosure_check(text: str) -> DisclosureResult:
    """Flag every admission of AI nature in a draft (16.1 rule 4). The approval gate never auto-sends a hit."""
    hits: list[str] = []
    for pat in _DISCLOSURE_RE:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    for m in _LLM_UPPER_RE.finditer(text):
        hits.append(m.group(0))
    for m in _AI_UPPER_RE.finditer(text):
        # "AI" alone is a hit only when it is clearly self-referential ("I am AI", "as an AI", "we are an AI");
        # "the AI Engineer role" in an external job note is not an admission.
        window = text[max(0, m.start() - 24): m.end() + 24].lower()
        if re.search(r"\b(?:i\s*(?:am|'m)|we\s*(?:are|'re)|myself|this\s+system|as\s+an?|by\s+an?)\b", window):
            hits.append(m.group(0))
    # De-duplicate preserving order.
    seen: set[str] = set()
    uniq = [h for h in hits if not (h.lower() in seen or seen.add(h.lower()))]
    return DisclosureResult(hits=tuple(uniq))
