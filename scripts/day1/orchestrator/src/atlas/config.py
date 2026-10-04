"""Settings and the config/ tree (CONVENTIONS.md §1, §3, §8; config/README.md).

Two sources, kept apart on purpose:
  * the process environment, as written by phase2/02-orchestrator.sh into /etc/atlas/orchestrator.env (ATLAS_CONFIG_DIR,
    ATLAS_DB_PATH, ATLAS_ENGINES_ENV_DIR, LLAMA_PORT_BASE, FAMILY_NAMES, ...), plus /etc/atlas/atlas.env (§3) when
    readable;
  * the config directory (default /opt/atlas/day1/config, overridable with ATLAS_CONFIG_DIR, or CONFIG_DIR for tests):
    engines.json, phase4-engines.json, router-rules.json, task-forces.json, personas/*.md, domains/cards/*.md.

Loaders validate what the orchestrator depends on and ignore unknown keys (config/README.md: "Unknown keys must be
ignored by the loader; these are informational").
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger("atlas.config")

DEFAULT_CONFIG_DIR = Path("/opt/atlas/day1/config")
DEFAULT_ETC_DIR = Path("/etc/atlas")
DEFAULT_DB_PATH = Path("/srv/atlas/data/orchestrator/atlas.sqlite3")
DEFAULT_LLAMA_PORT_BASE = 8100

# CONVENTIONS.md §8: the names that must agree across every file.
ENGINE_KEYS: tuple[str, ...] = (
    "gpt-oss-120b",
    "gpt-oss-120b-abliterated",
    "nemotron-3-super",
    "qwen3.5-122b",
    "deepseek-v4-flash",
    "qwen2.5-vl-72b",
    "meditron-70b",
    "router-qwen3.5-4b",
    "embed-bge-m3",
    "rerank-bge-v2-m3",
)
PERSONA_KEYS: tuple[str, ...] = (
    "ren", "arthur", "gideon", "silas", "valerie", "helena", "eleanor", "alaric", "minerva", "victor",
)
ARBITER_CLASSES: frozenset[str] = frozenset({"core", "apex", "vision", "crosscheck", "resident", "phase4"})
# CONVENTIONS.md §8 names q8_0 and q4_0 only; `none` is the embedding/reranker entry (no KV cache to quantise). f16 is
# NOT a class: Section 4.3 wants a quantised cache on every LLM engine, and V4 exists to catch a silent f16 fallback.
# (The Arbiter's KV_CLASS_FACTOR still knows f16 for projections passed explicitly, e.g. the DeepSeek KV ladder.)
KV_CLASSES: frozenset[str] = frozenset({"q8_0", "q4_0", "none"})
LLM_MODES: frozenset[str] = frozenset({"chat", "vision"})
# config/phase4-engines.json entries (Section 15.2) carry this mode and the Arbiter class `phase4` (§8): containers,
# never llama-server units, so the Arbiter records their measured footprint (Phase 4 step 5) and refuses to load them.
PHASE4_MODE = "phase4"
PHASE4_TIERS: frozenset[str] = frozenset({"green", "yellow", "verify", "deferred"})
HEMISPHERES: tuple[str, ...] = ("corporate", "estate")
# A task force or a domain card may serve both halves (Section 8.3 TF_OMEGA, 8.2 domain 24); a persona never does.
CARD_HEMISPHERES: tuple[str, ...] = (*HEMISPHERES, "both")
TIERS: tuple[str, ...] = ("routine", "standard", "sensitive")
FAMILY_NAMES_PLACEHOLDER = "FAMILY_NAMES_PLACEHOLDER"
# Settings.extra carries these keys and nothing else (orchestrator.env keys per phase2/02-orchestrator.sh; no secrets:
# the *_FILE values are paths, the tokens themselves stay in the files).
SETTINGS_EXTRA_KEYS: tuple[str, ...] = (
    "PRINCIPAL_USER", "TZ", "ORCH_HOST", "ORCH_PORT", "OPENWEBUI_PORT", "NTFY_URL", "NTFY_TOPIC", "DOMAIN",
    "OPENWEBUI_ADMIN_TOKEN_FILE", "NTFY_TOKEN_FILE", "HF_TOKEN_FILE",
)


class ConfigError(RuntimeError):
    """A config file is missing or does not say what the orchestrator needs; raised loudly, never worked around."""


# --- environment ------------------------------------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (bash-sourceable, as load_env writes them); quotes around the value are stripped."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # /etc/atlas/atlas.env is root:atlas 640 (§2): a caller outside group atlas (the Principal running atlas-admin
        # by hand, a unit with another User=) gets the documented fallback, the process environment only.
        log.warning("%s not readable (%s); continuing with the process environment only", path, exc)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            out[key] = value
    return out


def config_dir() -> Path:
    """ATLAS_CONFIG_DIR (orchestrator.env) or CONFIG_DIR (tests) or the installed default."""
    for var in ("ATLAS_CONFIG_DIR", "CONFIG_DIR"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value)
    return DEFAULT_CONFIG_DIR


@dataclass(frozen=True)
class Settings:
    """Process settings from the environment (phase2/02-orchestrator.sh _orch_env_write) with atlas.env as fallback."""

    config_dir: Path
    etc_dir: Path
    db_path: Path
    engines_env_dir: Path
    llama_port_base: int
    # The Principal's real family names (7.2 rule 1, 10.5 sensitivity): never in repr()/str(), so a `%r` of the settings
    # or an exception that formats them cannot put the names into the journal. asdict() still carries them; nothing
    # may JSON-dump a Settings object (the API's /config answers from AtlasConfig fields, never from this dataclass).
    family_names: tuple[str, ...] = field(repr=False)
    # An allowlisted subset of the merged environment, copied by name. Never the whole environment: the units inherit
    # proxy.env, memory.env and every later *.env, and asdict()/vars() of this object must not dump them.
    extra: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if environ is None else environ)
        etc_dir = Path(env.get("ATLAS_ETC", str(DEFAULT_ETC_DIR)))
        # /etc/atlas/atlas.env (§3) fills what orchestrator.env does not carry; the process environment wins.
        merged = parse_env_file(etc_dir / "atlas.env")
        merged.update(env)
        cfg = Path(merged["ATLAS_CONFIG_DIR"]) if merged.get("ATLAS_CONFIG_DIR") else (
            Path(merged["CONFIG_DIR"]) if merged.get("CONFIG_DIR") else DEFAULT_CONFIG_DIR
        )
        try:
            port_base = int(merged.get("LLAMA_PORT_BASE") or DEFAULT_LLAMA_PORT_BASE)
        except ValueError as exc:
            raise ConfigError(f"LLAMA_PORT_BASE={merged.get('LLAMA_PORT_BASE')!r} is not an integer") from exc
        names = tuple(n for n in merged.get("FAMILY_NAMES", "").replace(",", " ").split() if n)
        extra = {k: merged[k] for k in SETTINGS_EXTRA_KEYS if merged.get(k)}
        return cls(
            config_dir=cfg,
            etc_dir=etc_dir,
            db_path=Path(merged.get("ATLAS_DB_PATH") or DEFAULT_DB_PATH),
            engines_env_dir=Path(merged.get("ATLAS_ENGINES_ENV_DIR") or (etc_dir / "engines")),
            llama_port_base=port_base,
            family_names=names,
            extra=extra,
        )


# --- engines.json -----------------------------------------------------------------------------------------------------


class EngineFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    sha256: str | None = None
    bytes: int | None = None
    verify: str = "unverified"


class EngineSpec(BaseModel):
    """One entry of config/engines.json (CONVENTIONS.md §8 names; Sections 4.1, 4.3, 5.1, 5.3)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key: str
    display_name: str = ""
    role: str = ""
    mode: str = "chat"  # chat | vision | embedding | reranking | phase4 (config/phase4-engines.json entries)
    arbiter_class: str
    kv_class: str = "none"
    exclusive: bool = False
    hf_repo: str = ""
    subdir: str | None = None
    arch: str = ""
    licence: str = ""
    files: list[EngineFile] = Field(default_factory=list)
    model_file_pattern: str = ""
    mmproj: str | None = None
    quant: str = ""
    footprint_gb: float
    ctx_size: int  # the TOTAL KV pool across slots (research conflict 9)
    ctx_per_slot: int | None = None
    ctx_size_coresident: int | None = None
    ctx_size_f16_cap: int | None = None
    parallel: int = 1
    parallel_coresident: int | None = None
    n_keep: int = 0
    kv_ladder: list[str] = Field(default_factory=list)
    kv_types_must_match: bool = False
    expected_decode_tok_s: tuple[float, float] | None = None
    kv_proof_lines: int = 0
    min_llama_cpp_tag: str | None = None
    known_issue: str | None = None
    notes: str = ""
    # Optional extension (not in the shipped engines.json): f16 bytes of K+V per token, overriding the arbiter table.
    kv_bytes_per_token_f16: int | None = None
    # Filled by the loader: 1-based position in engines.json and the port it implies (§8 port rule).
    index: int = 0
    port: int = 0

    @field_validator("arbiter_class")
    @classmethod
    def _class_known(cls, v: str) -> str:
        if v not in ARBITER_CLASSES:
            raise ValueError(f"arbiter_class {v!r} is not one of {sorted(ARBITER_CLASSES)} (CONVENTIONS.md §8)")
        return v

    @field_validator("kv_class")
    @classmethod
    def _kv_known(cls, v: str) -> str:
        if v not in KV_CLASSES:
            raise ValueError(f"kv_class {v!r} is not one of {sorted(KV_CLASSES)} (CONVENTIONS.md §8; f16 is not a "
                             "class: Section 4.3 quantises the cache of every LLM engine)")
        return v

    @model_validator(mode="after")
    def _llm_engines_have_a_quantised_cache(self) -> EngineSpec:
        # Section 4.3: every chat/vision engine runs a quantised KV cache; `none` is for embedding/reranking only.
        if self.mode in LLM_MODES and self.kv_class == "none":
            raise ValueError(f"mode {self.mode!r} needs a quantised kv_class (q8_0 or q4_0, Section 4.3), not 'none'")
        return self

    @property
    def is_resident(self) -> bool:
        """Resident small models are never counted against the engine budget (Section 4.1)."""
        return self.arbiter_class == "resident"

    @property
    def is_apex(self) -> bool:
        return self.arbiter_class == "apex" or self.exclusive

    @property
    def is_phase4(self) -> bool:
        """A Phase 4 container engine (Section 15.2): registered with the Arbiter, never loaded by it."""
        return self.arbiter_class == "phase4"

    @property
    def footprint_bytes(self) -> int:
        """Weights, in bytes, reading the Section 4.1/5.1 `footprint_gb` figure as GiB (binary).

        The budget side is binary throughout (sysfs mem_info_gtt_* bytes, amdgpu.gttsize in MiB, the 170 GiB budget),
        and the document's figures are GGUF file sizes whose unit it does not state. Reading them as GiB is the upper
        bound (7.4 % above decimal GB), which is the right side for the hard pre-flight of Section 4.3 (the OOM backstop
        errs high); the measured footprint (Arbiter.register_measured / confirm_loaded) replaces it once known.
        """
        return int(self.footprint_gb * 1024**3)

    @property
    def systemd_unit(self) -> str:
        return f"llama-server@{self.key}"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def load_engines(cfg_dir: Path | None = None, port_base: int | None = None) -> dict[str, EngineSpec]:
    """config/engines.json -> {key: EngineSpec} in file order, with port = port_base + 1-based index (§8)."""
    cfg_dir = cfg_dir or config_dir()
    if port_base is None:
        port_base = int(os.environ.get("LLAMA_PORT_BASE") or DEFAULT_LLAMA_PORT_BASE)
    path = cfg_dir / "engines.json"
    data = _read_json(path)
    entries = data.get("engines") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: no engines[] list")
    out: dict[str, EngineSpec] = {}
    for index, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict) or "key" not in raw:
            raise ConfigError(f"{path}: engine #{index} has no key")
        try:
            spec = EngineSpec.model_validate({**raw, "index": index, "port": port_base + index})
        except ValueError as exc:
            raise ConfigError(f"{path}: engine {raw.get('key')!r}: {exc}") from exc
        if spec.key in out:
            raise ConfigError(f"{path}: duplicate engine key {spec.key!r}")
        out[spec.key] = spec
    missing = [k for k in ENGINE_KEYS if k not in out]
    if missing:
        raise ConfigError(f"{path}: engine keys missing from CONVENTIONS.md §8 set: {missing}")
    return out


# --- router-rules.json ------------------------------------------------------------------------------------------------


class RouterRules(BaseModel):
    """config/router-rules.json (Sections 7.1, 7.2, 9.1; config/README.md matching rules)."""

    model_config = ConfigDict(extra="ignore")

    hard_keywords: list[str]
    hard_keyword_route: str = "arthur"
    overrides: dict[str, str]
    routes: dict[str, str]
    classifier_engine: str = "router-qwen3.5-4b"
    long_document_tokens: int = 24000
    max_domain_cards: int = 3

    def resolve_family_names(self, family_names: tuple[str, ...] | list[str]) -> RouterRules:
        """Replace FAMILY_NAMES_PLACEHOLDER with the names from /etc/atlas/atlas.env (config/README.md)."""
        keywords: list[str] = []
        for kw in self.hard_keywords:
            if kw == FAMILY_NAMES_PLACEHOLDER:
                if not family_names:
                    log.warning("router-rules: FAMILY_NAMES is empty; the family-name hard rule (7.2 rule 1) is "
                                "inactive")
                keywords.extend(family_names)
            else:
                keywords.append(kw)
        return self.model_copy(update={"hard_keywords": keywords})


def load_router_rules(cfg_dir: Path | None = None, family_names: tuple[str, ...] | None = None) -> RouterRules:
    cfg_dir = cfg_dir or config_dir()
    path = cfg_dir / "router-rules.json"
    try:
        rules = RouterRules.model_validate(_read_json(path))
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if family_names is not None:
        rules = rules.resolve_family_names(family_names)
    return rules


# --- task-forces.json -------------------------------------------------------------------------------------------------


class DomainCardRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    domain: int
    why: str = ""


class TaskForce(BaseModel):
    """One preset of config/task-forces.json (Section 8.3, 8.4, 8.5)."""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    group: str = ""
    hemisphere: str = ""
    owners: list[str]
    default_tier: str
    dual_sign_off: bool = False
    domain_cards: list[DomainCardRef] = Field(default_factory=list)
    relay: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    engine: str | None = None  # TF_OMEGA only: the Apex engine
    trigger: str | None = None  # TF_OMEGA only: "principal-only"

    @field_validator("default_tier")
    @classmethod
    def _tier_known(cls, v: str) -> str:
        # §8 tiers are lowercase keys; 8.3's "Sensitive" must fail here, not at dispatch (rule §7.4).
        if v not in TIERS:
            raise ValueError(f"default_tier {v!r} is not one of {TIERS} (CONVENTIONS.md §8)")
        return v

    @field_validator("hemisphere")
    @classmethod
    def _hemisphere_known(cls, v: str) -> str:
        if v and v not in CARD_HEMISPHERES:
            raise ValueError(f"hemisphere {v!r} is not one of {CARD_HEMISPHERES} (CONVENTIONS.md §8)")
        return v


def owner_persona_key(owner: str) -> str:
    """task-forces.json names owners and relays by persona name ("Gideon", "Arthur, tagged to 14"); the persona key is
    the lowercased first word (CONVENTIONS.md §8 persona keys)."""
    first = re.split(r"[\s,;]+", owner.strip(), maxsplit=1)[0] if owner.strip() else ""
    return first.lower()


def load_task_forces(cfg_dir: Path | None = None, max_cards: int = 3) -> dict[str, TaskForce]:
    cfg_dir = cfg_dir or config_dir()
    path = cfg_dir / "task-forces.json"
    data = _read_json(path)
    entries = data.get("task_forces") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: no task_forces[] list")
    out: dict[str, TaskForce] = {}
    for raw in entries:
        try:
            tf = TaskForce.model_validate(raw)
        except ValueError as exc:
            raise ConfigError(f"{path}: preset {raw.get('code') if isinstance(raw, dict) else raw!r}: {exc}") from exc
        if len(tf.domain_cards) > max_cards:
            # Section 8.4 rule 2: at most three cards per dispatch; a preset that names more is a config error.
            raise ConfigError(f"{path}: {tf.code} names {len(tf.domain_cards)} domain cards; the limit is {max_cards}")
        out[tf.code] = tf
    return out


# --- personas/<name>.md -----------------------------------------------------------------------------------------------


class Persona(BaseModel):
    """Front matter plus body of config/personas/<key>.md (Section 6.2, 6.4; config/README.md)."""

    model_config = ConfigDict(extra="ignore")

    key: str
    name: str
    role: str = ""
    hemisphere: str
    division: str = ""
    remit: str = ""
    reports_to: str | None = None
    default_engine: str
    override_engines: list[str] = Field(default_factory=list)
    speaks_externally_tier: str = "sensitive"
    directors: list[str] = Field(default_factory=list)
    deep_think_role: str | None = None
    # A number, or a phrase such as arthur.md's "low-not-zero" (Section 9.1: reasoning models loop at exactly zero).
    sampling_temperature: float | str | None = None
    body: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("speaks_externally_tier")
    @classmethod
    def _tier_known(cls, v: str) -> str:
        if v not in TIERS:
            raise ValueError(f"speaks_externally_tier {v!r} is not one of {TIERS} (CONVENTIONS.md §8)")
        return v


def load_personas(cfg_dir: Path | None = None, engines: dict[str, EngineSpec] | None = None) -> dict[str, Persona]:
    cfg_dir = cfg_dir or config_dir()
    pdir = cfg_dir / "personas"
    if not pdir.is_dir():
        raise ConfigError(f"{pdir} does not exist")
    out: dict[str, Persona] = {}
    for path in sorted(pdir.glob("*.md")):
        key = path.stem
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        meta = dict(post.metadata)
        known = set(Persona.model_fields) - {"key", "body", "extra"}
        extra = {k: v for k, v in meta.items() if k not in known}
        try:
            persona = Persona.model_validate({**{k: v for k, v in meta.items() if k in known},
                                              "key": key, "body": post.content, "extra": extra})
        except ValueError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        if persona.hemisphere not in HEMISPHERES:
            raise ConfigError(f"{path}: hemisphere {persona.hemisphere!r} is not one of {HEMISPHERES}")
        if engines is not None:
            for ek in [persona.default_engine, *persona.override_engines]:
                if ek not in engines:
                    raise ConfigError(f"{path}: engine {ek!r} is not a key of engines.json (CONVENTIONS.md §8)")
        out[key] = persona
    missing = [k for k in PERSONA_KEYS if k not in out]
    if missing:
        raise ConfigError(f"{pdir}: persona files missing: {missing}")
    return out


# --- domains/cards/NN-slug.md -----------------------------------------------------------------------------------------

# "# NN. Name  (Hemisphere, Owner, Tier X)" — CONVENTIONS.md §8 "Domain cards": the triple is comma-separated and the
# owner drops 8.2's inner comma ("Eleanor with Silas", "Arthur — tagged to 14"). parse_card_heading() READS a card that
# carries the 8.2 inner comma ("Alaric, with Silas") or a pipe-separated triple ("(Corporate | Valerie, with Silas |
# Tier A)") and normalises the owner to the §8 spelling, logging the H1 the card should carry. load_domain_cards() is
# lenient by default (one WARNING per such card) so the orchestrator STARTS on a tree whose cards drift from §8 — the
# node mirrors scripts/day1/config as it is, and a crash loop at Phase 2 step 2 helps nobody (fix round 3, blocker:
# the real tree carries six such cards, 07, 11, 26, 28, 32, 35; the parse already yields the §8 owner). The §8
# agreement is still enforced, where rule §7.4 wants it seen: load_domain_cards(..., strict=True) raises one
# ConfigError naming every offending card and the H1 it expects; `atlas-admin config check` (exit 1) and
# tests/test_config.py::test_real_config_tree_loads run that strict pass so the Phase 2 gate turns red with a precise
# message instead of a dead service. The name may carry its own parenthetical ("(incl. ...)") and the triple may nest
# one ("phrasing softened (Section 18 C9)"), so the split point is the LAST run of two or more spaces before an opening
# parenthesis, not a regex over parentheses.
_CARD_H1 = re.compile(r"^#\s+(\d{2})\.\s+(.*\S)\s*$")
_CARD_SEP = re.compile(r"\s{2,}\(")
_OWNER_INNER_COMMA = re.compile(r"^([A-Z][a-z]+), (with |and |tagged )")


class DomainCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int
    slug: str
    name: str
    hemisphere: str  # the §8 key: corporate | estate | both (the H1 spells it capitalised)
    owner: str
    tier: str  # "A" | "B" | "C"
    text: str  # the whole card, injected verbatim (Section 8.4 rule 1)
    path: str

    @property
    def is_tier_c(self) -> bool:
        """Tier C cards load only on an explicit match, never speculatively (Section 8.4 rule 4)."""
        return self.tier == "C"


def parse_card_heading(line: str, *, source: str = "", strict: bool = False) -> tuple[int, str, str, str, str]:
    """(number, name, hemisphere, owner, tier) from a card H1; the owner is returned in the §8 spelling.

    A non-§8 triple (pipes, or an owner with 8.2's inner comma) is normalised and logged with the H1 the card should
    carry; with strict=True it raises ConfigError with that message instead (load_domain_cards(strict=True) does).
    """
    m = _CARD_H1.match(line.strip())
    seps = list(_CARD_SEP.finditer(m.group(2))) if m else []
    if not m or not seps or not m.group(2).endswith(")"):
        raise ConfigError(f"domain card H1 does not match '# NN. Name  (Hemisphere, Owner, Tier X)': {line!r}")
    rest = m.group(2)
    number, name, triple = int(m.group(1)), rest[:seps[-1].start()].strip(), rest[seps[-1].end():-1]
    where = source or f"domain card {number}"
    piped = "|" in triple
    parts = [p.strip() for p in (triple.split("|") if piped else triple.split(","))]
    if len(parts) < 3:
        raise ConfigError(f"{where}: triple {triple!r} has fewer than three fields")
    hemisphere, tier = parts[0], parts[-1]
    raw_owner = ", ".join(parts[1:-1])
    owner = _OWNER_INNER_COMMA.sub(r"\1 \2", raw_owner)
    if piped or owner != raw_owner:
        why = "pipe-separated" if piped else "owner carries 8.2's inner comma"
        msg = (f"{where}: H1 triple is not in the CONVENTIONS.md §8 form ({why}); owner read as {owner!r} — the card "
               f"should say '({hemisphere}, {owner}, {tier})'")
        if strict:
            raise ConfigError(msg)
        log.warning("%s", msg)
    tm = re.fullmatch(r"Tier\s+([ABC])", tier)
    if not tm:
        raise ConfigError(f"{where}: tier field {tier!r} is not 'Tier A|B|C'")
    return number, name, hemisphere, owner, tm.group(1)


def load_domain_cards(cfg_dir: Path | None = None, *, strict: bool = False) -> dict[int, DomainCard]:
    """config/domains/cards/*.md -> {number: DomainCard}.

    strict=False (load_config, the running service): a card whose H1 triple is not in the §8 form is normalised and
    logged as a WARNING naming the H1 it should carry. strict=True (`atlas-admin config check`, the gate, the real-tree
    test): the same cards raise ONE ConfigError listing every offender (rule §7.4), after the whole tree has been read
    so the message is complete. Everything else (a missing dir, a bad number, a duplicate, an unknown hemisphere) is a
    ConfigError in both modes.
    """
    cfg_dir = cfg_dir or config_dir()
    cdir = cfg_dir / "domains" / "cards"
    if not cdir.is_dir():
        raise ConfigError(f"{cdir} does not exist")
    out: dict[int, DomainCard] = {}
    non_conforming: list[str] = []
    for path in sorted(cdir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        try:
            number, name, hemisphere, owner, tier = parse_card_heading(first, source=str(path), strict=strict)
        except ConfigError as exc:
            if not strict or "not in the CONVENTIONS.md §8 form" not in str(exc):
                raise
            non_conforming.append(str(exc))  # keep going: one message names every offending card
            number, name, hemisphere, owner, tier = parse_card_heading(first, source=str(path))
        m = re.match(r"^(\d{2})-(.+)$", path.stem)
        if not m or int(m.group(1)) != number:
            raise ConfigError(f"{path}: file number does not match its H1 number {number}")
        if number in out:
            raise ConfigError(f"{path}: duplicate domain number {number}")
        key = hemisphere.strip().lower()
        if key not in CARD_HEMISPHERES:
            raise ConfigError(f"{path}: hemisphere {hemisphere!r} is not one of {CARD_HEMISPHERES} (CONVENTIONS.md §8)")
        out[number] = DomainCard(number=number, slug=m.group(2), name=name, hemisphere=key, owner=owner,
                                 tier=tier, text=text, path=str(path))
    if non_conforming:
        raise ConfigError(f"{len(non_conforming)} domain card(s) do not carry the CONVENTIONS.md §8 H1 triple "
                          "(Hemisphere, Owner, Tier X):\n  " + "\n  ".join(non_conforming))
    return out


# --- phase4-engines.json ----------------------------------------------------------------------------------------------


def load_phase4_engines(cfg_dir: Path | None = None) -> dict[str, EngineSpec]:
    """config/phase4-engines.json -> {key: EngineSpec} with arbiter_class `phase4` (CONVENTIONS.md §8, Section 15.2).

    These are container engines (phase4/engines/<key>.sh), not llama-server units: no port, no KV cache, a nominal
    ctx. The Arbiter knows them so Phase 4 step 5's POST /arbiter/register records their measured footprint under a
    known key (4.2 rule 1) and so request_load() on one of them is refused with a reason instead of raising
    UnknownEngine. `footprint_gb` is the planning figure footprint_gb_expected (0 for the deferred rows, which have
    none); the measurement from the driver replaces it. Fields the loader does not name are ignored (config/README.md).
    """
    cfg_dir = cfg_dir or config_dir()
    path = cfg_dir / "phase4-engines.json"
    data = _read_json(path)
    entries = data.get("engines") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: no engines[] list")
    out: dict[str, EngineSpec] = {}
    for index, raw in enumerate(entries, start=1):
        if not isinstance(raw, dict) or not raw.get("key"):
            raise ConfigError(f"{path}: engine #{index} has no key")
        tier = raw.get("tier")
        if tier not in PHASE4_TIERS:
            raise ConfigError(f"{path}: engine {raw['key']!r}: tier {tier!r} is not one of {sorted(PHASE4_TIERS)}")
        expected = raw.get("footprint_gb_expected")
        if expected is None and tier != "deferred":
            raise ConfigError(f"{path}: engine {raw['key']!r}: footprint_gb_expected missing (tier {tier})")
        try:
            spec = EngineSpec.model_validate({
                "key": raw["key"], "display_name": raw.get("name", ""), "role": raw.get("job", ""),
                "mode": PHASE4_MODE, "arbiter_class": "phase4", "kv_class": "none",
                "footprint_gb": float(expected or 0), "ctx_size": 0, "parallel": 1,
                "licence": raw.get("licence_note") or "", "notes": raw.get("research_note") or "",
                "index": index, "port": 0,
            })
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{path}: engine {raw.get('key')!r}: {exc}") from exc
        if spec.key in out:
            raise ConfigError(f"{path}: duplicate engine key {spec.key!r}")
        out[spec.key] = spec
    return out


# --- everything -------------------------------------------------------------------------------------------------------


@dataclass
class AtlasConfig:
    settings: Settings
    engines: dict[str, EngineSpec]  # the llama-server engines (engines.json): units, ports, the sudoers fragment
    router_rules: RouterRules
    task_forces: dict[str, TaskForce]
    personas: dict[str, Persona]
    domain_cards: dict[int, DomainCard]
    # Kept apart from `engines` on purpose: nothing may build a llama-server@<key> unit or a sudo line from a Phase 4
    # key. The Arbiter merges both maps (arbiter.build_arbiter) so it knows every weight-bearing process (4.2 rule 1).
    phase4_engines: dict[str, EngineSpec] = field(default_factory=dict)

    def engine(self, key: str) -> EngineSpec:
        try:
            return self.engines[key]
        except KeyError as exc:
            raise ConfigError(f"unknown engine key {key!r} (CONVENTIONS.md §8)") from exc

    @property
    def all_engines(self) -> dict[str, EngineSpec]:
        """engines.json first, then the Phase 4 entries: what the Arbiter's ledger may hold."""
        return {**self.engines, **self.phase4_engines}


def load_config(settings: Settings | None = None) -> AtlasConfig:
    """Load the whole tree; every file must exist and validate, or ConfigError says which one (rule §7.4)."""
    settings = settings or Settings.from_env()
    cfg = settings.config_dir
    if not cfg.is_dir():
        raise ConfigError(f"config dir {cfg} does not exist (ATLAS_CONFIG_DIR in /etc/atlas/orchestrator.env)")
    engines = load_engines(cfg, settings.llama_port_base)
    rules = load_router_rules(cfg, settings.family_names)
    for route, ek in rules.routes.items():
        if ek not in engines:
            raise ConfigError(f"router-rules.json: route {route!r} names unknown engine {ek!r}")
    if rules.classifier_engine not in engines:
        raise ConfigError(f"router-rules.json: classifier_engine {rules.classifier_engine!r} is not in engines.json")
    task_forces = load_task_forces(cfg, rules.max_domain_cards)
    personas = load_personas(cfg, engines)
    # Lenient on purpose (see the comment above load_domain_cards): the service must start on the tree the node has;
    # the strict §8 pass is `atlas-admin config check` / the gate, where a red row says which card to fix.
    cards = load_domain_cards(cfg)
    phase4 = load_phase4_engines(cfg)
    clash = sorted(set(phase4) & set(engines))
    if clash:
        raise ConfigError(f"phase4-engines.json reuses engines.json keys {clash} (CONVENTIONS.md §8 names must agree)")
    for tf in task_forces.values():
        for ref in tf.domain_cards:
            if ref.domain not in cards:
                raise ConfigError(f"task-forces.json: {tf.code} names domain {ref.domain}, which has no card")
            if cards[ref.domain].is_tier_c:
                # Section 8.4 rule 4: Tier C loads only on an explicit match; a preset would load it on every dispatch.
                raise ConfigError(f"task-forces.json: {tf.code} names Tier C domain {ref.domain}; 8.4 rule 4 forbids "
                                  "speculative Tier C loads (config/README.md: domain_cards never names a Tier C card)")
        for role, names in (("owners", tf.owners), ("relay", tf.relay)):
            for owner in names:
                if owner_persona_key(owner) not in personas:
                    raise ConfigError(f"task-forces.json: {tf.code} {role} entry {owner!r} is not a persona "
                                      f"({sorted(personas)}; CONVENTIONS.md §8 persona keys)")
        if tf.engine is not None and tf.engine not in engines:
            raise ConfigError(f"task-forces.json: {tf.code} names unknown engine {tf.engine!r}")
    return AtlasConfig(settings=settings, engines=engines, router_rules=rules, task_forces=task_forces,
                       personas=personas, domain_cards=cards, phase4_engines=phase4)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"{path} does not exist")
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
