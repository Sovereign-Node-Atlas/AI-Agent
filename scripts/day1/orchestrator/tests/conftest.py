"""Shared fixtures: a CONFIG_DIR under tests/fixtures and no live services (CONVENTIONS.md §7.8)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Rule §7.1: no telemetry beacon from a test run on a developer machine either (chromadb-client posts to PostHog on
# Client() unless opted out). Set before atlas is imported; atlas/__init__.py repeats the same setdefault calls.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")

from atlas.config import EngineSpec, load_engines  # after the telemetry opt-out on purpose

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_DIR = FIXTURES / "config"


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("ATLAS_ETC", str(CONFIG_DIR))  # atlas.env lives there in the fixture tree
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    monkeypatch.delenv("FAMILY_NAMES", raising=False)
    monkeypatch.delenv("LLAMA_PORT_BASE", raising=False)
    monkeypatch.delenv("ATLAS_ENGINES_ENV_DIR", raising=False)
    return CONFIG_DIR


@pytest.fixture
def engines(config_dir: Path) -> dict[str, EngineSpec]:
    return load_engines(config_dir, 8100)
