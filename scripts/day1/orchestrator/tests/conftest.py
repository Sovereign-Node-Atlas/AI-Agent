"""Shared fixtures: a CONFIG_DIR under tests/fixtures and no live services (CONVENTIONS.md §7.8)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Rule §7.1: no telemetry beacon from a test run on a developer machine either (chromadb-client posts to PostHog on
# Client() unless opted out). Forced, not defaulted, before atlas is imported; atlas/__init__.py does the same.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from atlas.config import SETTINGS_EXTRA_KEYS, EngineSpec, load_engines  # after the telemetry opt-out on purpose

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_DIR = FIXTURES / "config"


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("ATLAS_ETC", str(CONFIG_DIR))  # atlas.env lives there in the fixture tree
    # Settings.from_env lets the process environment win over the fixture atlas.env, and the node's verify scripts
    # (`set -a; source /etc/atlas/orchestrator.env`) and the Phase 2 driver export TZ, NTFY_*, ORCH_HOST, *_TOKEN_FILE,
    # ATLAS_DB_PATH...; scrub every key the settings would copy so the fixture file alone decides (fix round).
    for key in ("CONFIG_DIR", "FAMILY_NAMES", "LLAMA_PORT_BASE", "ATLAS_ENGINES_ENV_DIR", "ATLAS_DB_PATH",
                *SETTINGS_EXTRA_KEYS):
        monkeypatch.delenv(key, raising=False)
    return CONFIG_DIR


@pytest.fixture
def engines(config_dir: Path) -> dict[str, EngineSpec]:
    return load_engines(config_dir, 8100)
