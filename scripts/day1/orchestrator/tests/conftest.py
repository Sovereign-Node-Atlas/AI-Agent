"""Shared fixtures: a CONFIG_DIR under tests/fixtures and no live services (CONVENTIONS.md §7.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import EngineSpec, load_engines

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_DIR = FIXTURES / "config"


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("ATLAS_ETC", str(CONFIG_DIR))  # atlas.env lives there in the fixture tree
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    monkeypatch.delenv("FAMILY_NAMES", raising=False)
    monkeypatch.delenv("LLAMA_PORT_BASE", raising=False)
    return CONFIG_DIR


@pytest.fixture
def engines(config_dir: Path) -> dict[str, EngineSpec]:
    return load_engines(config_dir, 8100)
