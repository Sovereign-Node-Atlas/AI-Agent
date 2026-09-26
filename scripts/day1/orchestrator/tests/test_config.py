"""config/: engines.json ports, router rules placeholder, task forces, personas, domain cards."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import (
    ENGINE_KEYS,
    ConfigError,
    Settings,
    load_config,
    load_domain_cards,
    load_engines,
    load_router_rules,
    load_task_forces,
    parse_card_heading,
    parse_env_file,
)


def test_settings_from_env_reads_atlas_env_and_process_env(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings.from_env()
    assert s.config_dir == config_dir
    assert s.llama_port_base == 8100
    assert s.family_names == ("Rida", "Moussa")  # quotes stripped from FAMILY_NAMES="Rida Moussa"
    monkeypatch.setenv("LLAMA_PORT_BASE", "9100")
    monkeypatch.setenv("ATLAS_DB_PATH", "/tmp/x.sqlite3")
    s2 = Settings.from_env()
    assert s2.llama_port_base == 9100 and s2.db_path == Path("/tmp/x.sqlite3")
    monkeypatch.setenv("LLAMA_PORT_BASE", "abc")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_parse_env_file(tmp_path: Path) -> None:
    f = tmp_path / "e.env"
    f.write_text("# c\nA=1\nexport B='two words'\nC=\"q\"\nbad line\n9X=nope\n")
    assert parse_env_file(f) == {"A": "1", "B": "two words", "C": "q"}
    assert parse_env_file(tmp_path / "missing.env") == {}


def test_engines_ports_follow_the_port_rule(config_dir: Path) -> None:
    engines = load_engines(config_dir, 8100)
    assert list(engines) == list(ENGINE_KEYS)
    assert [e.port for e in engines.values()] == list(range(8101, 8111))
    assert engines["deepseek-v4-flash"].is_apex and engines["deepseek-v4-flash"].exclusive
    assert engines["router-qwen3.5-4b"].is_resident
    assert engines["gpt-oss-120b"].systemd_unit == "llama-server@gpt-oss-120b"
    assert engines["qwen2.5-vl-72b"].parallel_coresident == 2 and engines["qwen2.5-vl-72b"].ctx_size_coresident == 65536
    assert load_engines(config_dir, 9000)["gpt-oss-120b"].port == 9001


def test_engines_missing_key_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "engines.json").write_text('{"engines": [{"key": "only-one", "arbiter_class": "core", '
                                           '"footprint_gb": 1, "ctx_size": 4096}]}')
    with pytest.raises(ConfigError, match=r"missing from CONVENTIONS\.md"):
        load_engines(tmp_path, 8100)
    (tmp_path / "engines.json").write_text('{"engines": [{"key": "x", "arbiter_class": "weird", "footprint_gb": 1, '
                                           '"ctx_size": 4096}]}')
    with pytest.raises(ConfigError, match="arbiter_class"):
        load_engines(tmp_path, 8100)


def test_router_rules_family_names(config_dir: Path) -> None:
    raw = load_router_rules(config_dir)
    assert "FAMILY_NAMES_PLACEHOLDER" in raw.hard_keywords
    rules = load_router_rules(config_dir, ("Rida", "Moussa"))
    assert "FAMILY_NAMES_PLACEHOLDER" not in rules.hard_keywords
    assert rules.hard_keywords[-2:] == ["Rida", "Moussa"]
    assert rules.routes["deep-think:deep"] == "deepseek-v4-flash" and rules.hard_keyword_route == "arthur"
    empty = load_router_rules(config_dir, ())
    assert "FAMILY_NAMES_PLACEHOLDER" not in empty.hard_keywords and "medical" in empty.hard_keywords


def test_task_forces_ignore_unknown_keys_and_cap_cards(config_dir: Path, tmp_path: Path) -> None:
    tfs = load_task_forces(config_dir)
    assert set(tfs) == {"TF_ALPHA", "TF_OMEGA"}
    assert tfs["TF_OMEGA"].engine == "deepseek-v4-flash" and tfs["TF_OMEGA"].trigger == "principal-only"
    assert tfs["TF_ALPHA"].domain_cards[0].domain == 5
    (tmp_path / "task-forces.json").write_text(
        '{"task_forces": [{"code": "TF_X", "name": "x", "owners": ["Ren"], "default_tier": "standard", '
        '"domain_cards": [{"domain": 1}, {"domain": 2}, {"domain": 3}, {"domain": 4}]}]}')
    with pytest.raises(ConfigError, match="limit is 3"):
        load_task_forces(tmp_path)


def test_domain_card_headings() -> None:
    h5 = "# 05. CFO & Controller (incl. financial fraud and risk modelling)  (Corporate, Silas, Tier A)"
    assert parse_card_heading(h5) == (5, "CFO & Controller (incl. financial fraud and risk modelling)", "Corporate",
                                      "Silas", "A")
    h7 = "# 07. Development Director, Property & Real Estate  (Corporate | Valerie, with Silas | Tier A)"
    assert parse_card_heading(h7) == (7, "Development Director, Property & Real Estate", "Corporate",
                                      "Valerie, with Silas", "A")
    assert parse_card_heading("# 26. Cultural Asset & Fine Art Curator  (Estate, Alaric, with Silas, Tier C)") == (
        26, "Cultural Asset & Fine Art Curator", "Estate", "Alaric, with Silas", "C")
    with pytest.raises(ConfigError):
        parse_card_heading("# Not a card")


def test_load_domain_cards(config_dir: Path) -> None:
    cards = load_domain_cards(config_dir)
    assert set(cards) == {5, 7, 14, 26}
    assert cards[26].is_tier_c and not cards[5].is_tier_c
    assert cards[7].owner == "Valerie, with Silas" and cards[14].hemisphere == "Estate"
    assert cards[5].text.startswith("# 05.")


def test_load_config_cross_checks(config_dir: Path) -> None:
    cfg = load_config()
    assert set(cfg.personas) == {"ren", "arthur", "gideon", "silas", "valerie", "helena", "eleanor", "alaric",
                                 "minerva", "victor"}
    assert cfg.personas["ren"].default_engine == "gpt-oss-120b" and cfg.personas["ren"].directors[0] == "gideon"
    assert cfg.personas["ren"].extra["kokoro_voice"] == "am_onyx"
    assert cfg.engine("nemotron-3-super").port == 8103
    with pytest.raises(ConfigError, match="unknown engine"):
        cfg.engine("nope")
    assert cfg.router_rules.hard_keywords[-2:] == ["Rida", "Moussa"]
