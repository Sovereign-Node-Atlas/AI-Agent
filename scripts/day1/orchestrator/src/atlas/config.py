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
# Section 4.1 (v0.3.5, S44; S48; S49): memory the Engine Arbiter never budgets to an engine. Its GPU resident set is
# read from mem_info_gtt_used, which counts GPU buffer objects only, so the CPU-side rows of the 4.1 table (Ubuntu/XFCE/
# Docker/Cockpit 4 GB, Open WebUI/ChromaDB/graph store ~3 GB, Kokoro/Whisper/PyAnnote 3 GB, orchestrator/Celery/Redis/
# Sentinel/ntfy/WG-Easy 2 GB: ~12 GB) never show on it, and without a reserve they were budgeted to engines on a node
# with no swap. 16 GiB = those ~12 GiB plus room for transient CPU work (Celery CPU workers, Docling, the Phase 2
# tools) and kernel overhead; measured CPU-side use is logged against it at every measurement, so a node that needs
# more says so (raise the override then). The retrieval pair on the CPU (D-14 (e), v0.4.0: placement "cpu") is NOT in
# this reserve: the Arbiter budgets it as its own host resident set (atlas.arbiter, module docstring "Budget").
# The v0.4.0 fit (D-31, D-32; INFERRED until Phase 3 measures it): one LLM seat runs alone against
# pool - GPU resident set (router only, ~5.5 GiB) - this reserve - the CPU retrieval pair (~9-10 GiB right-sized), about
# 156-157 GiB on the expected node. GLM-5.3-Flash (147.10 GiB of files with its mmproj) and DeepSeek-V4-Flash-0731
# UD-Q8_K_XL (150.75 GiB) both project inside it at their Day 1 profiles with the compute-buffer term of
# atlas.arbiter (DEFAULT_COMPUTE_BUFFER_GIB); before D-31 the 161.87 GB DeepSeek file was read as 161.87 GiB and
# refused. The pool is min(GTT total, MemTotal) (S47), so the margin needs MemTotal at its expected ~187-188 GiB:
# 192 GiB less the 512 MiB UMA carve-out (3.2), the ~3 GiB page map and ~0.5 GiB of firmware and kernel (an estimate
# until V3a records the real figure). Phase 1 step 4 removes kdump-tools' crash-kernel reservation (S48: 4.25 GiB out of
# MemTotal at every boot), which V3a fails on, and puts kho=off on the kernel line (S49): Ubuntu's 7.0 kernel has kexec
# handover on by default (CONFIG_KEXEC_HANDOVER_ENABLE_DEFAULT=y, VERIFIED) and then sets aside about 13 GiB of MemTotal
# (12.8-13.8, INFERRED) as MIGRATE_CMA pageblocks that TTM/GTT pages (no __GFP_MOVABLE) can never use. V3a fails while
# any of that scratch is set aside.
# ATLAS_ARBITER_HEADROOM_GIB in orchestrator.env overrides it (whole GiB, within ARBITER_HEADROOM_RANGE_GIB); a bad
# value stops only the Arbiter's construction (build_arbiter), never another reader of the settings (the backup
# freeze, Sentinel, Celery tasks).
DEFAULT_ARBITER_HEADROOM_GIB = 16
ARBITER_HEADROOM_RANGE_GIB: tuple[int, int] = (4, 64)  # 4 = the OS row of 4.1 alone; 64 would refuse every Day 1 seat
# D-32 (approved 2026-10-16, option (a); S-23): how many LLM seats (mode chat/vision, never a resident companion, a
# class-external or a Phase 4 load) may be resident at once. 1 = every LLM seat runs alone with the whole budget and is
# exclusive; 2 brings back v0.3.5's pairing of seats that fit (option (b)) without a code change, the Apex engine
# staying exclusive. ATLAS_ARBITER_MAX_RESIDENT in orchestrator.env overrides it (whole number within the range); a bad
# value stops only the Arbiter's construction, like the headroom.
DEFAULT_ARBITER_MAX_RESIDENT = 1
ARBITER_MAX_RESIDENT_RANGE: tuple[int, int] = (1, 2)

# CONVENTIONS.md §8: the names that must agree across every file. The v0.4.0 Day 1 set (approved 2026-10-16; D-10,
# D-12, D-14, D-45, D-59), in engines.json order: gpt-oss-120b (fast utility and fallback floor, F16-trunk GGUF) and its
# abliterated twin keep ports 8101 and 8102 as in v0.3.5 (the uncensored entry is unchanged by this revision), then the
# two seats GLM-5.3-Flash (Ren, Gideon, Silas, Helena, Minerva, Victor, Alaric, Eleanor's persona work, the vision
# engine through its glm5v mmproj and the long-document route) and DeepSeek-V4-Flash-0731 (Arthur, the Apex), then the
# always-resident companions: the router on the iGPU and the Qwen3 4B retrieval pair on the CPU (D-14 (e)). Phased
# engines are appended after these when they come in, so no Day 1 port moves (ENGINE_PORT_SLOTS).
ENGINE_KEYS: tuple[str, ...] = (
    "gpt-oss-120b",
    "gpt-oss-120b-abliterated",
    "glm-5.3-flash",
    "deepseek-v4-flash",
    "router-qwen3.5-4b",
    "embed-qwen3-4b",
    "rerank-qwen3-4b",
)
# S-33: the llama-server port range, LLAMA_PORT_BASE + 1 .. LLAMA_PORT_BASE + ENGINE_PORT_SLOTS (8101..8120 at the
# default base). The Day 1 set uses 8101..8107; the full arsenal plus its companions comes to about 15-18 units, so 20
# leaves room for the phased seats without another change to the range. load_engines refuses an engines[] list longer
# than this rather than hand out a port outside it.
ENGINE_PORT_SLOTS = 20
PERSONA_KEYS: tuple[str, ...] = (
    "ren", "arthur", "gideon", "silas", "valerie", "helena", "eleanor", "alaric", "minerva", "victor",
)
ARBITER_CLASSES: frozenset[str] = frozenset({"core", "apex", "vision", "crosscheck", "resident", "phase4", "external"})
# Section 4.2 ("every load ... of any weight-bearing process ... Chatterbox when invoked" passes through the Arbiter):
# class `external` is a weight-bearing process the Arbiter budgets, counts as resident (rule 3) and ledgers (rule 1) but
# never starts or stops itself: the caller runs it in-process (Chatterbox inside phase2/voice_render.py) and POSTs
# /arbiter/load before and /arbiter/unload after. Such entries live in engines.json's `external` list, not `engines[]`:
# they take no port (the §8 port rule counts engines[] only), no llama-server@ unit, no sudoers line
# (phase2/01-llama.sh writes one per engines[] key) and no env file (phase2/engine-env.py renders engines[] only).
EXTERNAL_MODE = "external"
# The KV cache type of an engine (S-17, D-29 option (a) approved 2026-10-16): f16 where the cache is cheap
# (GLM-5.3-Flash 19,712 B/token, DeepSeek-V4-Flash 6,880 B/token), q8_0 elsewhere, q4_0 nowhere new (it stays only on
# the unchanged gpt-oss-120b-abliterated entry). `none` is an entry that renders no --cache-type flag: a non-causal
# embedder has no cache, and a causal one (the Qwen3 retrieval pair) then runs llama.cpp's default f16, which the
# Arbiter projects.
KV_CLASSES: frozenset[str] = frozenset({"f16", "q8_0", "q4_0", "none"})
LLM_MODES: frozenset[str] = frozenset({"chat", "vision"})
# D-14 option (e), approved 2026-10-16 (answers.md section 2): where a llama-server runs. `gpu` (the default) is on the
# iGPU and shows on mem_info_gtt_used; `cpu` (phase2/engine-env.py renders --device none) is host memory the GTT counter
# never shows, so the Arbiter budgets it as a host resident set. Only an always-resident companion may run on the CPU.
PLACEMENTS: frozenset[str] = frozenset({"gpu", "cpu"})
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
    # The Arbiter's host reserve in bytes (DEFAULT_ARBITER_HEADROOM_GIB above; ATLAS_ARBITER_HEADROOM_GIB overrides).
    # A bad override leaves the default here and its reason in arbiter_headroom_error, which build_arbiter raises.
    arbiter_headroom_bytes: int = DEFAULT_ARBITER_HEADROOM_GIB * 1024**3
    arbiter_headroom_error: str | None = None
    # D-32: the LLM-seat residency limit (DEFAULT_ARBITER_MAX_RESIDENT; ATLAS_ARBITER_MAX_RESIDENT overrides). A bad
    # override leaves the default here and its reason in arbiter_max_resident_error, which build_arbiter raises.
    arbiter_max_resident: int = DEFAULT_ARBITER_MAX_RESIDENT
    arbiter_max_resident_error: str | None = None

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
        headroom_raw = (merged.get("ATLAS_ARBITER_HEADROOM_GIB") or "").strip()
        headroom_gib, headroom_error = DEFAULT_ARBITER_HEADROOM_GIB, None
        if headroom_raw:
            lo, hi = ARBITER_HEADROOM_RANGE_GIB
            if not re.fullmatch(r"[0-9]+", headroom_raw):
                headroom_error = f"ATLAS_ARBITER_HEADROOM_GIB={headroom_raw!r} is not a whole number of GiB"
            elif not lo <= int(headroom_raw) <= hi:
                headroom_error = f"ATLAS_ARBITER_HEADROOM_GIB={headroom_raw} is outside {lo}..{hi} GiB (Section 4.1)"
            else:
                headroom_gib = int(headroom_raw)
        resident_raw = (merged.get("ATLAS_ARBITER_MAX_RESIDENT") or "").strip()
        max_resident, max_resident_error = DEFAULT_ARBITER_MAX_RESIDENT, None
        if resident_raw:
            lo, hi = ARBITER_MAX_RESIDENT_RANGE
            if not re.fullmatch(r"[0-9]+", resident_raw):
                max_resident_error = f"ATLAS_ARBITER_MAX_RESIDENT={resident_raw!r} is not a whole number of LLM seats"
            elif not lo <= int(resident_raw) <= hi:
                max_resident_error = (f"ATLAS_ARBITER_MAX_RESIDENT={resident_raw} is outside {lo}..{hi} LLM seats "
                                      "(D-32, Section 4.2 rule 3)")
            else:
                max_resident = int(resident_raw)
        return cls(
            config_dir=cfg,
            etc_dir=etc_dir,
            db_path=Path(merged.get("ATLAS_DB_PATH") or DEFAULT_DB_PATH),
            engines_env_dir=Path(merged.get("ATLAS_ENGINES_ENV_DIR") or (etc_dir / "engines")),
            llama_port_base=port_base,
            family_names=names,
            extra=extra,
            arbiter_headroom_bytes=headroom_gib * 1024**3,
            arbiter_headroom_error=headroom_error,
            arbiter_max_resident=max_resident,
            arbiter_max_resident_error=max_resident_error,
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
    ctx_size_f16_cap: int | None = None
    parallel: int = 1
    # S-22 (v0.4.0): the standing co-resident profile of S52 (ctx_size_coresident, parallel_coresident) is gone with the
    # pairing it served (D-32: one LLM seat resident); an engines.json that still carries those keys loads, and they
    # are ignored like any unknown key.
    n_keep: int = 0
    placement: str = "gpu"  # PLACEMENTS: gpu | cpu (D-14 (e): the Qwen3 retrieval pair runs on the CPU)
    kv_ladder: list[str] = Field(default_factory=list)
    kv_types_must_match: bool = False
    expected_decode_tok_s: tuple[float, float] | None = None
    kv_proof_lines: int = 0
    min_llama_cpp_tag: str | None = None
    known_issue: str | None = None
    notes: str = ""
    # Optional extensions (S-17), each overriding the Arbiter's table for this key (atlas.arbiter): f16 bytes of K+V per
    # token; the per-sequence state that does not grow with context (recurrent and sliding-window caches, compressor
    # state), charged once per slot; and the compute buffers in GiB. A phased engine brings its figures this way.
    kv_bytes_per_token_f16: int | None = None
    kv_state_bytes_per_sequence: int | None = None
    compute_buffer_gib: float | None = None
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
            raise ValueError(f"kv_class {v!r} is not one of {sorted(KV_CLASSES)} (CONVENTIONS.md §8; D-29: f16 where "
                             "the cache is cheap, q8_0 elsewhere)")
        return v

    @field_validator("placement")
    @classmethod
    def _placement_known(cls, v: str) -> str:
        if v not in PLACEMENTS:
            raise ValueError(f"placement {v!r} is not one of {sorted(PLACEMENTS)} (D-14)")
        return v

    @model_validator(mode="after")
    def _llm_engines_name_their_cache(self) -> EngineSpec:
        # Section 4.3 (D-29): every chat/vision engine names its KV cache type (f16, q8_0 or q4_0); `none` is for
        # embedding/reranking entries only.
        if self.mode in LLM_MODES and self.kv_class == "none":
            raise ValueError(f"mode {self.mode!r} needs a kv_class (f16, q8_0 or q4_0, Section 4.3), not 'none'")
        # D-14 (e): the CPU placement is for an always-resident companion; a seat on the CPU would be budgeted by
        # nobody (it is not on the GTT counter and not a resident set).
        if self.placement == "cpu" and self.arbiter_class != "resident":
            raise ValueError(f"placement 'cpu' is for always-resident companions (arbiter_class 'resident'), not "
                             f"{self.arbiter_class!r} (D-14)")
        # D-31: when the files carry exact byte counts, the mmproj must be one of them, or its bytes would be left out
        # of the budget (the vision engine's glm5v mmproj is about 1.05 GiB).
        if self.mmproj and self._exact_bytes() is not None and self.mmproj not in {
                f.name.rsplit("/", 1)[-1] for f in self.files}:
            raise ValueError(f"mmproj {self.mmproj!r} is not in files[]; its bytes must be counted (D-31)")
        return self

    def _exact_bytes(self) -> int | None:
        """The sum of files[].bytes when every file carries a positive count, else None (D-31)."""
        sizes = [f.bytes for f in self.files]
        if not sizes or any(b is None or b <= 0 for b in sizes):
            return None
        return sum(b for b in sizes if b is not None)

    @property
    def is_resident(self) -> bool:
        """Resident small models are never counted against the engine budget (Section 4.1)."""
        return self.arbiter_class == "resident"

    @property
    def is_apex(self) -> bool:
        return self.arbiter_class == "apex" or self.exclusive

    @property
    def is_llm_seat(self) -> bool:
        """An LLM seat (D-32): a chat or vision llama-server unit the Arbiter loads on demand. The residency limit
        (Settings.arbiter_max_resident) counts these only; always-resident companions, class-external loads and Phase 4
        containers never count against it."""
        return self.mode in LLM_MODES and not (self.is_resident or self.is_phase4 or self.is_external)

    @property
    def is_phase4(self) -> bool:
        """A Phase 4 container engine (Section 15.2): registered with the Arbiter, never loaded by it."""
        return self.arbiter_class == "phase4"

    @property
    def is_external(self) -> bool:
        """An in-process weight-bearing load the Arbiter budgets but does not start or stop (Section 4.2: Chatterbox
        when invoked); engines.json `external` list, no unit, no port."""
        return self.arbiter_class == "external"

    @property
    def footprint_bytes(self) -> int:
        """Weights, in bytes (D-31 option (a), S-20, approved 2026-10-16): the exact sum of files[].bytes, the Hugging
        Face tree sizes (lfs.size) the pull also records in <models>/<key>/MANIFEST.json, mmproj included, when every
        file carries its count. The budget side is binary throughout (sysfs mem_info_gtt_* bytes, the ~157 GiB Day 1
        budget), and bytes need no unit. An entry without exact counts keeps the v0.3.5 rule: `footprint_gb` read as
        GiB, the upper bound (7.4 % above decimal GB, the right side for the hard pre-flight of Section 4.3). Either
        way the measured footprint (Arbiter.register_measured / confirm_loaded) replaces it once known.
        """
        exact = self._exact_bytes()
        return exact if exact is not None else int(self.footprint_gb * 1024**3)

    @property
    def footprint_source(self) -> str:
        """Where footprint_bytes comes from: 'files' (exact bytes, D-31) or 'footprint_gb' (read as GiB)."""
        return "files" if self._exact_bytes() is not None else "footprint_gb"

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
    if len(entries) > ENGINE_PORT_SLOTS:
        # S-33: the port range is LLAMA_PORT_BASE + 1 .. + ENGINE_PORT_SLOTS; an engine past it would get a port nothing
        # else (ufw, the bind rule, CONVENTIONS.md §8) expects.
        raise ConfigError(f"{path}: {len(entries)} engines[] entries exceed the {ENGINE_PORT_SLOTS}-port range "
                          f"{port_base + 1}..{port_base + ENGINE_PORT_SLOTS} (CONVENTIONS.md §8 port rule, S-33)")
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
        if spec.is_external:
            # It would take a port, a unit, a sudoers line and an env file it cannot have (EXTERNAL_MODE comment).
            raise ConfigError(f"{path}: engine {spec.key!r}: arbiter_class 'external' belongs in the top-level "
                              "`external` list, not in engines[] (Section 4.2 class external has no llama-server unit)")
        out[spec.key] = spec
    missing = [k for k in ENGINE_KEYS if k not in out]
    if missing:
        raise ConfigError(f"{path}: engine keys missing from CONVENTIONS.md §8 set: {missing}")
    # The `external` list (Section 4.2 "Chatterbox when invoked"; EXTERNAL_MODE comment): Arbiter-budgeted in-process
    # loads with no port (0) and no unit, appended after engines[] so nothing that counts or indexes engines[] changes
    # (phase2/01-llama.sh, phase2/engine-env.py, the §8 port rule).
    extras = data.get("external", []) if isinstance(data, dict) else []
    if not isinstance(extras, list):
        raise ConfigError(f"{path}: `external` must be a list of engine entries")
    for n, raw in enumerate(extras, start=1):
        if not isinstance(raw, dict) or "key" not in raw:
            raise ConfigError(f"{path}: external entry #{n} has no key")
        try:
            spec = EngineSpec.model_validate({**raw, "index": 0, "port": 0})
        except ValueError as exc:
            raise ConfigError(f"{path}: external engine {raw.get('key')!r}: {exc}") from exc
        if not spec.is_external:
            raise ConfigError(f"{path}: external engine {spec.key!r}: arbiter_class must be 'external' "
                              f"(got {spec.arbiter_class!r}); llama-server engines belong in engines[]")
        if spec.key in out:
            raise ConfigError(f"{path}: duplicate engine key {spec.key!r} (engines[] and external)")
        out[spec.key] = spec
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
    # D-38 (approved 2026-10-16): the long-document route now runs on GLM-5.3-Flash; this trigger, set for a 32K slot,
    # is re-set (S-30) once Phase 3 has measured the per-engine slot profile (D-48), a to-do until then.
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
    # D-40 option (a), approved 2026-10-16: the engine that carries this persona's seat if its default fails or defers
    # in Phase 3 (the arsenal's runner-up where it is a Day 1 engine, else gpt-oss-120b, the floor every seat can use).
    # A record for the Principal and Phase 3, never a silent runtime substitute: engine_for() does not pick it.
    fallback_engine: str | None = None
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
            fallback = [persona.fallback_engine] if persona.fallback_engine else []
            for ek in [persona.default_engine, *persona.override_engines, *fallback]:
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
