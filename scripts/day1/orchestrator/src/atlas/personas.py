"""Personas: the ten Shadow Cabinet files with their engine bindings and tiers (Sections 6.1, 6.2, 6.4, 16.2).

The persona files are config/personas/<key>.md (CONVENTIONS.md §1, §8), parsed by atlas.config.load_personas into
atlas.config.Persona (front matter + body). This module adds what the router, the prompt builder and the approval
queue need on top of the raw records:

  * `PersonaRegistry`: the ten personas keyed by CONVENTIONS.md §8 key, with the hemisphere leads (ren, arthur) and
    each director's lead resolved (6.1 "Directors" row / front-matter `reports_to`);
  * `EngineBinding`: which engine a persona runs on by default and which overrides it may switch to (6.1, 6.2), with
    `engine_for(persona, override)` refusing an engine the persona is not bound to (6.1 C6: overrides are explicit,
    never a default);
  * `external_tier(persona)`: the approval tier at which the persona speaks externally (6.2 "Speaks externally"
    column, 16.2), the floor the approval queue applies to that persona's outbound items.

Nothing here reads a live service; loading is one pass over the config tree.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from atlas.config import (
    HEMISPHERES,
    PERSONA_KEYS,
    TIERS,
    ConfigError,
    EngineSpec,
    Persona,
    load_engines,
    load_personas,
)

log = logging.getLogger("atlas.personas")

__all__ = [
    "EngineBinding",
    "Persona",
    "PersonaRegistry",
    "external_tier",
    "load_persona_registry",
]

# Section 6.1: the two hemisphere leads. Every other persona is a director reporting to one of them.
HEMISPHERE_LEADS: dict[str, str] = {"corporate": "ren", "estate": "arthur"}

# Section 6.2 "Speaks externally" column, the authoritative tier per director. ren/arthur are not in the 6.2 table;
# their files carry an inference (config/README.md) which the registry honours, falling back to `sensitive`
# (16.1 rule 2: the leads are reserved for matters that warrant them, which are never routine).
SECTION_6_2_EXTERNAL_TIER: dict[str, str] = {
    "gideon": "sensitive",
    "silas": "standard",
    "valerie": "standard",
    "helena": "standard",
    "eleanor": "routine",
    "alaric": "sensitive",
    "minerva": "sensitive",
    "victor": "routine",
}


@dataclass(frozen=True)
class EngineBinding:
    """A persona's engine bindings (6.1 / 6.2 "Default engine" and "Override" columns)."""

    persona: str
    default_engine: str
    override_engines: tuple[str, ...]

    @property
    def all_engines(self) -> tuple[str, ...]:
        return (self.default_engine, *self.override_engines)

    def allows(self, engine: str) -> bool:
        return engine in self.all_engines


class PersonaRegistry(Mapping[str, Persona]):
    """The ten personas with leads, bindings and tiers resolved. Read-only mapping key -> Persona."""

    def __init__(self, personas: Mapping[str, Persona], engines: Mapping[str, EngineSpec] | None = None) -> None:
        missing = [k for k in PERSONA_KEYS if k not in personas]
        if missing:
            raise ConfigError(f"persona registry: files missing for {missing} (CONVENTIONS.md §8)")
        self._personas: dict[str, Persona] = dict(personas)
        self._engines = dict(engines) if engines is not None else None
        for key, p in self._personas.items():
            if p.hemisphere not in HEMISPHERES:
                raise ConfigError(f"persona {key}: hemisphere {p.hemisphere!r} is not one of {HEMISPHERES}")
            if p.speaks_externally_tier not in TIERS:
                raise ConfigError(f"persona {key}: speaks_externally_tier {p.speaks_externally_tier!r} "
                                  f"is not one of {TIERS}")
            if self._engines is not None:
                for ek in (p.default_engine, *p.override_engines):
                    if ek not in self._engines:
                        raise ConfigError(f"persona {key}: engine {ek!r} is not a key of engines.json")

    # --- Mapping -----------------------------------------------------------------------------------------------------

    def __getitem__(self, key: str) -> Persona:
        try:
            return self._personas[key]
        except KeyError as exc:
            raise ConfigError(f"unknown persona key {key!r} (CONVENTIONS.md §8: {PERSONA_KEYS})") from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self._personas)

    def __len__(self) -> int:
        return len(self._personas)

    # --- structure (6.1) ---------------------------------------------------------------------------------------------

    def lead(self, hemisphere: str) -> Persona:
        """The hemisphere lead: ren for corporate, arthur for estate (6.1)."""
        try:
            return self[HEMISPHERE_LEADS[hemisphere]]
        except KeyError as exc:
            raise ConfigError(f"unknown hemisphere {hemisphere!r} (CONVENTIONS.md §8: {HEMISPHERES})") from exc

    def lead_of(self, key: str) -> Persona:
        """The lead a director reports to (front-matter `reports_to`, else the lead of its hemisphere).

        A lead returns itself: 16.1 rule 1, the Principal speaks only to Ren or Arthur, so the lead of a lead
        is the lead.
        """
        p = self[key]
        if key in HEMISPHERE_LEADS.values():
            return p
        if p.reports_to:
            return self[p.reports_to]
        return self.lead(p.hemisphere)

    def is_lead(self, key: str) -> bool:
        return key in HEMISPHERE_LEADS.values()

    def directors_of(self, lead_key: str) -> list[Persona]:
        """The directors under a lead (6.1 "Directors" row; the lead's front-matter `directors` list wins when set)."""
        lead = self[lead_key]
        if lead.directors:
            return [self[d] for d in lead.directors]
        return [p for k, p in self._personas.items() if k != lead_key and self.lead_of(k).key == lead_key]

    # --- engines (6.1, 6.2) ------------------------------------------------------------------------------------------

    def binding(self, key: str) -> EngineBinding:
        p = self[key]
        return EngineBinding(persona=key, default_engine=p.default_engine, override_engines=tuple(p.override_engines))

    def engine_for(self, key: str, override: str | None = None) -> str:
        """The engine key a persona runs on: its default, or `override` when the persona is bound to it.

        An override the persona is not bound to is refused loudly (6.1 C6: the abliterated engine is a manual
        escalation on explicit command, never a default; 16.3 rule 6 forbids widening bindings at run time).
        """
        b = self.binding(key)
        if override is None:
            return b.default_engine
        if not b.allows(override):
            raise ConfigError(f"persona {key} is not bound to engine {override!r}; bound engines: {b.all_engines}")
        return override

    # --- tiers (6.2, 16.2) -------------------------------------------------------------------------------------------

    def external_tier(self, key: str) -> str:
        return external_tier(self[key])

    # --- resolution helpers ------------------------------------------------------------------------------------------

    def key_for_name(self, name: str) -> str:
        """Resolve a display name as task-forces.json writes owners ("Gideon", "Silas") to a persona key."""
        needle = name.strip().lower()
        if needle in self._personas:
            return needle
        for key, p in self._personas.items():
            first = p.name.strip().split()[0].lower() if p.name.strip() else key
            if needle in {first, p.name.strip().lower()}:
                return key
        raise ConfigError(f"no persona matches owner name {name!r} (task-forces.json owners must name a persona)")


def external_tier(persona: Persona) -> str:
    """The tier at which a persona speaks externally (6.2 column; the file's value must agree with 6.2)."""
    expected = SECTION_6_2_EXTERNAL_TIER.get(persona.key)
    declared = persona.speaks_externally_tier
    if expected is not None and declared != expected:
        # The document wins over the file (CONVENTIONS.md preamble); say so rather than silently pick one.
        log.warning("persona %s declares speaks_externally_tier=%s but Section 6.2 says %s; using 6.2",
                    persona.key, declared, expected)
        return expected
    if declared not in TIERS:
        raise ConfigError(f"persona {persona.key}: speaks_externally_tier {declared!r} is not one of {TIERS}")
    return declared


def load_persona_registry(cfg_dir: Path | None = None, engines: Mapping[str, EngineSpec] | None = None,
                          *, validate_engines: bool = True) -> PersonaRegistry:
    """Load config/personas/*.md (and engines.json when `validate_engines`) into a PersonaRegistry."""
    eng: dict[str, EngineSpec] | None
    if engines is not None:
        eng = dict(engines)
    elif validate_engines:
        eng = load_engines(cfg_dir)
    else:
        eng = None
    personas = load_personas(cfg_dir, eng)
    return PersonaRegistry(personas, eng)
