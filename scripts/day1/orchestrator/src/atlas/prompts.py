"""Prompt-cache layering (Section 4.4) and the fixed governance block (Sections 16.1-16.3).

The system prompt is built in fixed layers, most stable first, so llama-server's slot cache keeps the prefix
resident across dispatches and a change in layer 3 costs only its own prefill (4.4):

    1. persona core          config/personas/<key>.md body of the persona that runs the dispatch: the owning
                             director for a task force (decision.dispatch_persona; 8.4 "injected into the running
                             director's system prompt", 8.5 step 3), else the hemisphere lead; never changes
                             within a session
    2. governance block      GOVERNANCE_BLOCK below; static text from 16.1 (standing rules), 16.2 (tiers), 16.3 (D8)
    3. domain cards          the <= 3 cards the router selected (8.4), verbatim (8.4 rule 1)
    4. memory and scars      retrieved memory (10.1) and scars (9.4), appended last
    5. conversation          the messages; not part of the system prompt

`build_system_prompt(decision, cards, memory)` returns the layered string and `stable_prefix_hash`, the sha256 of
layers 1+2, which is what the slot cache should be keyed on (a persona's prefix is identical for every dispatch).
Layers 1-2 are also byte-identical across calls for the same persona: no timestamps, no ordering that depends on
the request, so the cached prefix survives (4.4 "every dispatch that changes the system prompt from the top
invalidates the cached prefix").

Layer 4 is hemisphere-isolated (7.3: "enforced by separate memory collections, separate context windows"): the
builder is the last component that knows whose context window this is, so a memory mapping that carries the other
hemisphere's collection (`estate` / `documents_estate` into a corporate prompt, or the reverse) is a ValueError,
never silently merged and never silently dropped. The check can only run on text that names its collection, so
UNTYPED memory (a flat list, or the generic keys "memory" / "documents") is admitted but never silently: every
non-empty untyped bucket is a WARNING naming the persona and the count, so the by-pass is visible in the journal.
Callers should pass the 10.1 collection names (api.py `_retrieve`: `{info.hemisphere: [...], "scars": [...]}` and
`documents_<hemisphere>` for document hits; contract for the API writer).
The hemisphere is the layer-1 persona's own (the persona whose
context window the prompt is): for a task-force dispatch that is the owning director, which the router keeps in the
lead's hemisphere (8.1 preset tag) except on a hard hit or a typed override; when the decision's lead is in the other
hemisphere the builder logs the 7.3 tension and still keys on the dispatch persona. The `sentinel` collection is
admitted only into a prompt of its 9.3 owners, Alaric (threats) and Silas (markets) under Arthur; C12 resolved
"Sentinel fed to Ren" as a contradiction, so a Ren (or any other) prompt carrying it is a ValueError.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from atlas.config import HEMISPHERES, DomainCard, Persona
from atlas.personas import PersonaRegistry, load_persona_registry
from atlas.router import RoutingDecision

log = logging.getLogger("atlas.prompts")

__all__ = [
    "GOVERNANCE_BLOCK",
    "LAYER_SEPARATOR",
    "SENTINEL_READERS",
    "PromptBuilder",
    "SystemPrompt",
    "build_system_prompt",
    "governance_hash",
]

LAYER_SEPARATOR = "\n\n"
_HEADER_GOVERNANCE = "## Governance (binding; enforced in code, restated here so you write to it)"
_HEADER_CARDS = "## Domain cards for this dispatch (Section 8.4)"
_HEADER_MEMORY = "## Retrieved memory"
_HEADER_SCARS = "## Scars (past failures to avoid; Section 9.4)"

# Fixed text. Sections 16.1, 16.2 and 16.3 of docs/ATLAS_FRAMEWORK_REVIEW.md, restated for the model. Do not make it
# dynamic: it is layer 2 of the cached prefix (4.4). Changing it is a change to every persona's prefix.
GOVERNANCE_BLOCK = f"""{_HEADER_GOVERNANCE}

Standing rules (Section 16.1):
1. The Principal speaks only to Ren or Arthur. Shadow Cabinet output is never surfaced directly; the hemisphere \
lead synthesises and speaks.
2. Directors correspond externally under their own identities for routine and standard matters; Ren and Arthur \
are reserved for matters that warrant them.
3. Every outbound action passes the approval gate in code. It is a hard code path, not an instruction you can \
waive: nothing external executes until the Principal approves it in the queue.
4. Never disclose the AI nature of ATLAS, or of yourself, externally. Not in a signature, not in an aside, not \
when asked directly. A draft that admits it is stopped by the gate.
5. Never delegate work back to the Principal. Do the gathering, the drafting, the finding and the sending \
yourself; a rewrite pass removes any task-shaped request to the Principal. Only decisions and approvals may be \
asked of the Principal, phrased as "Decision needed: ...".
6. The register differs between the Principal and external recipients; the orchestrator sets it on every \
outbound draft and you write to it.
7. Cross-domain requests run their task-force preset; that rule is mandatory, not advisory.
8. Money never moves without the Principal. Two-factor prompts are never automated.

Approval tiers (Section 16.2):
- routine: meeting scheduling, confirmations, acknowledgements. Pre-approved categories; sent automatically and \
logged for review.
- standard: replies with substance, requests, negotiations, content updates, DNS changes. Drafted and held until \
the Principal approves.
- sensitive: legal, financial, medical, estate, security, any payment, anything under a sensitive-tier task \
force. Drafted, held, flagged with the director's reasoning; a strong cross-check on a second engine is applied \
automatically before the Principal sees it.

What ATLAS may never do without the Principal (Section 16.3, binding):
1. Move money, initiate a payment, or change a payment method.
2. Sign, accept, or bind to a contract or terms.
3. Send external correspondence above the routine tier.
4. Change DNS, domain, or Cloudflare security settings.
5. Delete, move, or modify vault contents or backups.
6. Modify its own code, configuration, approval tiers, router rules, or the allowlist. Domain 20 proposes; the \
Principal approves.
7. Enable continuous vision or audio capture.
8. Install software or pull models from outside the allowlist.
9. Share any Principal data with a third party not already connected under Section 13.
10. Create new external identities, accounts, or mailboxes."""


@dataclass(frozen=True)
class SystemPrompt:
    """The layered system prompt and the hashes the slot cache is keyed on. Unpacks as (text, stable_prefix_hash)."""

    text: str
    stable_prefix_hash: str  # sha256 of layers 1+2 (4.4: what stays resident)
    cards_hash: str = ""  # sha256 of layer 3 (changes per task force)
    persona: str = ""
    domain_cards: tuple[int, ...] = ()

    def __iter__(self) -> Iterator[str]:
        """`text, stable_prefix_hash = build_system_prompt(...)` (the contract in the writer brief)."""
        yield self.text
        yield self.stable_prefix_hash

    @property
    def stable_prefix(self) -> str:
        """Layers 1+2 exactly as they appear at the top of `text`."""
        idx = self.text.find(LAYER_SEPARATOR + _HEADER_CARDS)
        if idx < 0:
            idx = self.text.find(LAYER_SEPARATOR + _HEADER_MEMORY)
        if idx < 0:
            idx = self.text.find(LAYER_SEPARATOR + _HEADER_SCARS)
        return self.text if idx < 0 else self.text[:idx]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def governance_hash() -> str:
    return _sha256(GOVERNANCE_BLOCK)


# Keys a memory mapping may carry (10.1 collections plus the pipeline's generic "memory"/"documents" buckets). The
# hemisphere-specific ones are admitted only for the speaking hemisphere; `sentinel` only for its 9.3 owners. The
# generic buckets carry no collection name, so they pass no 7.3 check: they are admitted with a WARNING (module
# docstring), not refused, because the chat pipeline hands its hemisphere-queried hits over under "memory" today.
_SHARED_MEMORY_KEYS: tuple[str, ...] = ("memory", "documents")
SENTINEL_KEY = "sentinel"
# 9.3 "Owners: Alaric for threats, Silas for markets, under Arthur. RESOLVED (C12): not Ren."
SENTINEL_READERS: frozenset[str] = frozenset({"arthur", "alaric", "silas"})


def _split_memory(
    memory: Sequence[str] | Mapping[str, Any] | None, hemisphere: str, persona_key: str = ""
) -> tuple[list[str], list[str]]:
    """Accept a flat list of memory snippets, or a mapping of 10.1 collections ({"memory": [...], "scars": [...],
    "<hemisphere>": [...], "documents_<hemisphere>": [...], "sentinel": [...]}) restricted to `hemisphere` and, for
    `sentinel`, to a persona in SENTINEL_READERS (9.3 / C12).

    The other hemisphere's collections present and non-empty, an unknown key, or `sentinel` for a persona who does
    not own it raise ValueError (7.3 isolation; CONVENTIONS.md §7.4 never silent). Untyped memory (a flat list or a
    generic key) is admitted with a WARNING per non-empty bucket: it carries no collection name to check."""
    if memory is None:
        return [], []
    if hemisphere not in HEMISPHERES:
        raise ValueError(f"unknown hemisphere {hemisphere!r} (CONVENTIONS.md §8: {HEMISPHERES})")
    who = persona_key or hemisphere
    if not isinstance(memory, Mapping):
        flat = [str(m) for m in memory if str(m).strip()]
        if flat:
            log.warning(
                "prompt for %s (%s): untyped memory list (%d snippets) admitted without a collection check (7.3); "
                "pass a mapping keyed by 10.1 collection",
                who, hemisphere, len(flat),
            )
        return flat, []
    allowed = (*_SHARED_MEMORY_KEYS, hemisphere, f"documents_{hemisphere}")
    for generic in _SHARED_MEMORY_KEYS:
        count = sum(1 for m in (memory.get(generic) or ()) if str(m).strip())
        if count:
            log.warning(
                "prompt for %s (%s): generic memory key %r (%d snippets) admitted without a collection check "
                "(7.3); pass %r / %r instead",
                who, hemisphere, generic, count, hemisphere, f"documents_{hemisphere}",
            )
    if persona_key in SENTINEL_READERS:
        allowed = (*allowed, SENTINEL_KEY)
    for key, value in memory.items():
        if key == "scars" or key in allowed:
            continue
        if not value:
            continue  # an empty bucket for the other hemisphere carries nothing and is tolerated
        if key == SENTINEL_KEY:
            raise ValueError(
                f"memory key 'sentinel' is not admitted into a prompt for {persona_key or hemisphere!r} "
                f"(9.3: Sentinel's owners are {sorted(SENTINEL_READERS)} under Arthur; C12 resolved "
                f"'Sentinel fed to Ren' as a contradiction)"
            )
        other = [h for h in HEMISPHERES if h != hemisphere]
        raise ValueError(
            f"memory key {key!r} is not admitted into a {hemisphere} prompt (7.3: the {other} "
            f"collections stay in their own context window; allowed keys: {sorted(allowed)} + scars)"
        )
    mem: list[str] = []
    for key in allowed:
        mem.extend(str(m) for m in (memory.get(key) or ()) if str(m).strip())
    scars = [str(s) for s in (memory.get("scars") or ()) if str(s).strip()]
    return mem, scars


class PromptBuilder:
    """Builds layered system prompts for a persona registry (one per orchestrator process)."""

    def __init__(self, personas: Mapping[str, Persona] | PersonaRegistry,
                 governance_block: str = GOVERNANCE_BLOCK) -> None:
        self.personas = personas
        self.governance_block = governance_block
        self._prefix_cache: dict[str, tuple[str, str]] = {}

    def stable_prefix(self, persona_key: str) -> tuple[str, str]:
        """(layers 1+2 text, sha256) for a persona; cached because it never changes within a process."""
        hit = self._prefix_cache.get(persona_key)
        if hit is not None:
            return hit
        persona = self.personas[persona_key]
        core = persona.body.strip()
        if not core:
            raise ValueError(f"persona {persona_key} has an empty body (config/personas/{persona_key}.md)")
        text = core + LAYER_SEPARATOR + self.governance_block.strip()
        out = (text, _sha256(text))
        self._prefix_cache[persona_key] = out
        return out

    @staticmethod
    def persona_key_for(decision: RoutingDecision | str) -> str:
        """Layer 1's persona: the dispatch persona of a decision (the owning director for a task force, 8.4/8.5
        step 3; else the lead), or the bare key given."""
        if isinstance(decision, str):
            return decision
        dispatch = getattr(decision, "dispatch_persona", None)
        if dispatch:
            return str(dispatch)
        if decision.task_force and decision.directors:
            return decision.directors[0]
        return decision.persona

    def build_system_prompt(
        self,
        decision: RoutingDecision | str,
        cards: Sequence[DomainCard] = (),
        memory: Sequence[str] | Mapping[str, Any] | None = None,
    ) -> SystemPrompt:
        """Layers 1-4 for the decision's dispatch persona (or a persona key), the given cards (verbatim) and the
        speaking hemisphere's retrieved memory."""
        persona_key = self.persona_key_for(decision)
        prefix, prefix_hash = self.stable_prefix(persona_key)
        # Layer 4 is the layer-1 persona's own hemisphere (7.3 "separate context windows": the collections that may
        # enter a context window are those of the persona whose window it is). The router keeps the dispatch in the
        # lead's hemisphere (8.1 preset tag); when a hard hit (7.2 rule 1) or a typed override (7.2 rule 4) left the
        # lead in the other hemisphere, that is said out loud here rather than letting the lead's collections leak
        # into a director's window (or the director's be refused).
        hemisphere = self.personas[persona_key].hemisphere
        if not isinstance(decision, str) and decision.hemisphere != hemisphere:
            log.warning(
                "prompt for %s (%s) under lead %s (%s): layer-4 memory keyed on the dispatch persona's "
                "hemisphere %s (7.3); reason=%s",
                persona_key,
                hemisphere,
                decision.persona,
                decision.hemisphere,
                hemisphere,
                decision.reason,
            )
        parts = [prefix]
        # Layer 3: cards in the router's order, verbatim (8.4 rule 1), capped by the router (8.4 rule 2).
        card_numbers = tuple(c.number for c in cards)
        cards_text = ""
        if cards:
            cards_text = _HEADER_CARDS + LAYER_SEPARATOR + LAYER_SEPARATOR.join(c.text.strip() for c in cards)
            parts.append(cards_text)
        # Layer 4: memory, then scars, last.
        mem, scars = _split_memory(memory, hemisphere, persona_key)
        if mem:
            parts.append(_HEADER_MEMORY + LAYER_SEPARATOR + LAYER_SEPARATOR.join(m.strip() for m in mem))
        if scars:
            parts.append(_HEADER_SCARS + LAYER_SEPARATOR + LAYER_SEPARATOR.join(s.strip() for s in scars))
        return SystemPrompt(
            text=LAYER_SEPARATOR.join(parts),
            stable_prefix_hash=prefix_hash,
            cards_hash=_sha256(cards_text) if cards_text else "",
            persona=persona_key,
            domain_cards=card_numbers,
        )


@lru_cache(maxsize=1)
def _default_builder() -> PromptBuilder:
    return PromptBuilder(load_persona_registry())


def build_system_prompt(decision: RoutingDecision | str, cards: Sequence[DomainCard] = (),
                        memory: Sequence[str] | Mapping[str, Any] | None = None, *,
                        personas: Mapping[str, Persona] | PersonaRegistry | None = None) -> SystemPrompt:
    """Module-level convenience: `personas` given -> a builder over them; None -> the installed config tree."""
    builder = PromptBuilder(personas) if personas is not None else _default_builder()
    return builder.build_system_prompt(decision, cards, memory)
