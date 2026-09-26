"""Settings and the config/ tree (CONVENTIONS.md §1, §3, §8; config/README.md).

Two sources, kept apart on purpose:
  * the process environment, as written by phase2/02-orchestrator.sh into /etc/atlas/orchestrator.env (ATLAS_CONFIG_DIR,
    ATLAS_DB_PATH, ATLAS_ENGINES_ENV_DIR, LLAMA_PORT_BASE, FAMILY_NAMES, ...), plus /etc/atlas/atlas.env (§3) when
    readable;
  * the config directory (default /opt/atlas/day1/config, overridable with ATLAS_CONFIG_DIR, or CONFIG_DIR for tests):
    engines.json, router-rules.json, task-forces.json, personas/*.md, domains/cards/*.md.

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
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
KV_CLASSES: frozenset[str] = frozenset({"q8_0", "q4_0", "f16", "none"})
HEMISPHERES: tuple[str, ...] = ("corporate", "estate")
TIERS: tuple[str, ...] = ("routine", "standard", "sensitive")
FAMILY_NAMES_PLACEHOLDER = "FAMILY_NAMES_PLACEHOLDER"


class ConfigError(RuntimeError):
    """A config file is missing or does not say what the orchestrator needs; raised loudly, never worked around."""


# --- environment ------------------------------------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (bash-sourceable, as load_env writes them); quotes around the value are stripped."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
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
    family_names: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict, repr=False)

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
        return cls(
            config_dir=cfg,
            etc_dir=etc_dir,
            db_path=Path(merged.get("ATLAS_DB_PATH") or DEFAULT_DB_PATH),
            engines_env_dir=Path(merged.get("ATLAS_ENGINES_ENV_DIR") or (etc_dir / "engines")),
            llama_port_base=port_base,
            family_names=names,
            env=merged,
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
    mode: str = "chat"  # chat | vision | embedding | reranking
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
            raise ValueError(f"kv_class {v!r} is not one of {sorted(KV_CLASSES)} (CONVENTIONS.md §8)")
        return v

    @property
    def is_resident(self) -> bool:
        """Resident small models are never counted against the engine budget (Section 4.1)."""
        return self.arbiter_class == "resident"

    @property
    def is_apex(self) -> bool:
        return self.arbiter_class == "apex" or self.exclusive

    @property
    def footprint_bytes(self) -> int:
        """Weights, in bytes, from the Section 4.1/5.1 GB figure (decimal GB as the document counts them)."""
        return int(self.footprint_gb * 1_000_000_000)

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
    entries = data["engines"] if isinstance(data, dict) else data
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


def load_task_forces(cfg_dir: Path | None = None, max_cards: int = 3) -> dict[str, TaskForce]:
    cfg_dir = cfg_dir or config_dir()
    path = cfg_dir / "task-forces.json"
    data = _read_json(path)
    entries = data["task_forces"] if isinstance(data, dict) else data
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

# "# NN. Name  (Hemisphere, Owner, Tier X)" — the triple may also be pipe-separated when the owner carries a comma
# ("(Corporate | Valerie, with Silas | Tier A)"); CONVENTIONS.md §8 "Domain cards". The name may carry its own
# parenthetical ("(incl. ...)") and the triple may nest one ("phrasing softened (Section 18 C9)"), so the split point
# is the LAST run of two or more spaces before an opening parenthesis, not a regex over parentheses.
_CARD_H1 = re.compile(r"^#\s+(\d{2})\.\s+(.*\S)\s*$")
_CARD_SEP = re.compile(r"\s{2,}\(")


class DomainCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int
    slug: str
    name: str
    hemisphere: str
    owner: str
    tier: str  # "A" | "B" | "C"
    text: str  # the whole card, injected verbatim (Section 8.4 rule 1)
    path: str

    @property
    def is_tier_c(self) -> bool:
        """Tier C cards load only on an explicit match, never speculatively (Section 8.4 rule 4)."""
        return self.tier == "C"


def parse_card_heading(line: str) -> tuple[int, str, str, str, str]:
    m = _CARD_H1.match(line.strip())
    seps = list(_CARD_SEP.finditer(m.group(2))) if m else []
    if not m or not seps or not m.group(2).endswith(")"):
        raise ConfigError(f"domain card H1 does not match '# NN. Name  (Hemisphere, Owner, Tier X)': {line!r}")
    rest = m.group(2)
    number, name, triple = int(m.group(1)), rest[:seps[-1].start()].strip(), rest[seps[-1].end():-1]
    parts = [p.strip() for p in (triple.split("|") if "|" in triple else triple.split(","))]
    if len(parts) < 3:
        raise ConfigError(f"domain card {number}: triple {triple!r} has fewer than three fields")
    hemisphere, tier = parts[0], parts[-1]
    owner = ", ".join(parts[1:-1])
    tm = re.fullmatch(r"Tier\s+([ABC])", tier)
    if not tm:
        raise ConfigError(f"domain card {number}: tier field {tier!r} is not 'Tier A|B|C'")
    return number, name, hemisphere, owner, tm.group(1)


def load_domain_cards(cfg_dir: Path | None = None) -> dict[int, DomainCard]:
    cfg_dir = cfg_dir or config_dir()
    cdir = cfg_dir / "domains" / "cards"
    if not cdir.is_dir():
        raise ConfigError(f"{cdir} does not exist")
    out: dict[int, DomainCard] = {}
    for path in sorted(cdir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        number, name, hemisphere, owner, tier = parse_card_heading(first)
        m = re.match(r"^(\d{2})-(.+)$", path.stem)
        if not m or int(m.group(1)) != number:
            raise ConfigError(f"{path}: file number does not match its H1 number {number}")
        if number in out:
            raise ConfigError(f"{path}: duplicate domain number {number}")
        out[number] = DomainCard(number=number, slug=m.group(2), name=name, hemisphere=hemisphere, owner=owner,
                                 tier=tier, text=text, path=str(path))
    return out


# --- everything -------------------------------------------------------------------------------------------------------


@dataclass
class AtlasConfig:
    settings: Settings
    engines: dict[str, EngineSpec]
    router_rules: RouterRules
    task_forces: dict[str, TaskForce]
    personas: dict[str, Persona]
    domain_cards: dict[int, DomainCard]

    def engine(self, key: str) -> EngineSpec:
        try:
            return self.engines[key]
        except KeyError as exc:
            raise ConfigError(f"unknown engine key {key!r} (CONVENTIONS.md §8)") from exc


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
    cards = load_domain_cards(cfg)
    for tf in task_forces.values():
        for ref in tf.domain_cards:
            if ref.domain not in cards:
                raise ConfigError(f"task-forces.json: {tf.code} names domain {ref.domain}, which has no card")
        if tf.engine is not None and tf.engine not in engines:
            raise ConfigError(f"task-forces.json: {tf.code} names unknown engine {tf.engine!r}")
    return AtlasConfig(settings=settings, engines=engines, router_rules=rules, task_forces=task_forces,
                       personas=personas, domain_cards=cards)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"{path} does not exist")
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
