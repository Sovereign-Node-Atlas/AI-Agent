"""config/: engines.json ports, router rules placeholder, task forces, personas, domain cards."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from atlas.config import (
    ENGINE_KEYS,
    KV_CLASSES,
    SETTINGS_EXTRA_KEYS,
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

# Invented placeholder names (tests/fixtures/config/atlas.env); the real family names never enter the repository.
FAMILY = ("Ava", "Testwood")


def test_settings_from_env_reads_atlas_env_and_process_env(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings.from_env()
    assert s.config_dir == config_dir
    assert s.llama_port_base == 8100
    assert s.family_names == FAMILY  # quotes stripped from FAMILY_NAMES="Ava Testwood"
    # Only the allowlisted keys are retained; the merged environment is never kept on the object (asdict is safe).
    assert s.extra == {"PRINCIPAL_USER": "tester", "TZ": "Australia/Sydney", "ORCH_PORT": "8800"}
    assert set(s.extra) <= set(SETTINGS_EXTRA_KEYS) and "LLAMA_PORT_BASE" not in s.extra
    assert not hasattr(s, "env") and "FAMILY_NAMES" not in str(dataclasses.asdict(s).get("extra"))
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


def test_parse_env_file_unreadable_falls_back_to_the_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    # /etc/atlas/atlas.env is root:atlas 640 (§2): a user outside group atlas gets the documented fallback, not a
    # traceback. (chmod 000 is no test when pytest runs as root, so the read itself is made to fail.)
    f = tmp_path / "atlas.env"
    f.write_text("PRINCIPAL_USER=x\n")
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        assert parse_env_file(f) == {}
    assert "not readable" in caplog.text
    monkeypatch.setenv("ATLAS_ETC", str(tmp_path))
    assert Settings.from_env({"ATLAS_ETC": str(tmp_path), "LLAMA_PORT_BASE": "8100"}).llama_port_base == 8100


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
                                           '"footprint_gb": 1, "ctx_size": 4096, "kv_class": "q8_0"}]}')
    with pytest.raises(ConfigError, match=r"missing from CONVENTIONS\.md"):
        load_engines(tmp_path, 8100)
    (tmp_path / "engines.json").write_text('{"engines": [{"key": "x", "arbiter_class": "weird", "footprint_gb": 1, '
                                           '"ctx_size": 4096, "kv_class": "q8_0"}]}')
    with pytest.raises(ConfigError, match="arbiter_class"):
        load_engines(tmp_path, 8100)
    # A file with only _meta (or a bare {}) is a ConfigError naming the file, not a KeyError traceback (rule §7.4).
    (tmp_path / "engines.json").write_text('{"_meta": {"purpose": "x"}}')
    with pytest.raises(ConfigError, match=r"engines\.json: no engines\[\] list"):
        load_engines(tmp_path, 8100)


def test_kv_classes_are_the_conventions_set_and_llm_engines_are_quantised(tmp_path: Path) -> None:
    assert KV_CLASSES == {"q8_0", "q4_0", "none"}  # CONVENTIONS.md §8; f16 is what V4 exists to catch
    base = '{"key": "x", "arbiter_class": "core", "footprint_gb": 1, "ctx_size": 4096, "mode": "%s", "kv_class": "%s"}'
    for mode, kv, msg in (("chat", "f16", "kv_class 'f16'"), ("chat", "none", "quantised kv_class"),
                          ("vision", "none", "quantised kv_class")):
        (tmp_path / "engines.json").write_text('{"engines": [%s]}' % (base % (mode, kv)))
        with pytest.raises(ConfigError, match=msg):
            load_engines(tmp_path, 8100)
    (tmp_path / "engines.json").write_text('{"engines": [%s]}' % (base % ("embedding", "none")))
    with pytest.raises(ConfigError, match="missing from CONVENTIONS"):  # `none` on an embedding engine validates
        load_engines(tmp_path, 8100)


def test_router_rules_family_names(config_dir: Path) -> None:
    raw = load_router_rules(config_dir)
    assert "FAMILY_NAMES_PLACEHOLDER" in raw.hard_keywords
    rules = load_router_rules(config_dir, FAMILY)
    assert "FAMILY_NAMES_PLACEHOLDER" not in rules.hard_keywords
    assert rules.hard_keywords[-2:] == list(FAMILY)
    assert rules.routes["deep-think:deep"] == "deepseek-v4-flash" and rules.hard_keyword_route == "arthur"
    empty = load_router_rules(config_dir, ())
    assert "FAMILY_NAMES_PLACEHOLDER" not in empty.hard_keywords and "medical" in empty.hard_keywords


def test_task_forces_ignore_unknown_keys_and_cap_cards(config_dir: Path, tmp_path: Path) -> None:
    tfs = load_task_forces(config_dir)
    assert set(tfs) == {"TF_ALPHA", "TF_OMEGA"}
    assert tfs["TF_OMEGA"].engine == "deepseek-v4-flash" and tfs["TF_OMEGA"].trigger == "principal-only"
    # The fixture row is Section 8.3's: Absolute Apex Contingency, Ren and Arthur jointly, dual sign-off, both halves.
    assert tfs["TF_OMEGA"].name == "Absolute Apex Contingency" and tfs["TF_OMEGA"].hemisphere == "both"
    assert tfs["TF_OMEGA"].owners == ["Ren", "Arthur"] and tfs["TF_OMEGA"].dual_sign_off
    assert [c.domain for c in tfs["TF_OMEGA"].domain_cards] == [22, 14, 6]
    assert tfs["TF_ALPHA"].domain_cards[0].domain == 5
    (tmp_path / "task-forces.json").write_text(
        '{"task_forces": [{"code": "TF_X", "name": "x", "owners": ["Ren"], "default_tier": "standard", '
        '"domain_cards": [{"domain": 1}, {"domain": 2}, {"domain": 3}, {"domain": 4}]}]}')
    with pytest.raises(ConfigError, match="limit is 3"):
        load_task_forces(tmp_path)


def test_domain_card_headings(caplog: pytest.LogCaptureFixture) -> None:
    h5 = "# 05. CFO & Controller (incl. financial fraud and risk modelling)  (Corporate, Silas, Tier A)"
    assert parse_card_heading(h5) == (5, "CFO & Controller (incl. financial fraud and risk modelling)", "Corporate",
                                      "Silas", "A")
    # CONVENTIONS.md §8: comma-separated triple, the owner drops 8.2's inner comma. Silent.
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        assert parse_card_heading("# 07. Development Director, Property & Real Estate  (Corporate, Valerie with Silas, "
                                  "Tier A)") == (7, "Development Director, Property & Real Estate", "Corporate",
                                                 "Valerie with Silas", "A")
        assert parse_card_heading("# 14. Private Family Advisor & Estate Guardian  (Estate, Arthur — tagged to 14, "
                                  "Tier A)")[3] == "Arthur — tagged to 14"
    assert caplog.text == ""
    # Non-conforming spellings still in the real cards (07, 11, 26, 28, 32, 35): accepted, normalised, and reported.
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        h7 = "# 07. Development Director, Property & Real Estate  (Corporate | Valerie, with Silas | Tier A)"
        assert parse_card_heading(h7, source="cards/07.md") == (7, "Development Director, Property & Real Estate",
                                                                "Corporate", "Valerie with Silas", "A")
        assert parse_card_heading("# 26. Cultural Asset & Fine Art Curator  (Estate, Alaric, with Silas, Tier C)") == (
            26, "Cultural Asset & Fine Art Curator", "Estate", "Alaric with Silas", "C")
    assert "cards/07.md" in caplog.text and "pipe-separated" in caplog.text and "inner comma" in caplog.text
    assert "'(Corporate, Valerie with Silas, Tier A)'" in caplog.text
    with pytest.raises(ConfigError):
        parse_card_heading("# Not a card")


def test_load_domain_cards(config_dir: Path) -> None:
    cards = load_domain_cards(config_dir)
    assert set(cards) == {5, 6, 7, 14, 22, 26}
    assert cards[26].is_tier_c and not cards[5].is_tier_c
    assert cards[7].owner == "Valerie with Silas" and cards[26].owner == "Alaric with Silas"  # §8 spellings
    assert cards[14].hemisphere == "Estate" and cards[22].owner == "Alaric"
    assert cards[5].text.startswith("# 05.")


def test_load_config_cross_checks(config_dir: Path) -> None:
    cfg = load_config()
    assert set(cfg.personas) == {"ren", "arthur", "gideon", "silas", "valerie", "helena", "eleanor", "alaric",
                                 "minerva", "victor"}
    assert cfg.personas["ren"].default_engine == "gpt-oss-120b" and cfg.personas["ren"].directors[0] == "gideon"
    assert cfg.personas["ren"].extra["kokoro_voice"] == "am_onyx"
    # The fixture front matter is config/personas/*.md verbatim (Sections 6.1, 6.2): names and approval tiers.
    assert cfg.personas["arthur"].name == "Arthur Sterling" and cfg.personas["ren"].name == "Ren Ackerman"
    tiers = {k: p.speaks_externally_tier for k, p in cfg.personas.items()}
    assert tiers == {"ren": "sensitive", "arthur": "sensitive", "gideon": "sensitive", "silas": "standard",
                     "valerie": "standard", "helena": "standard", "eleanor": "routine", "alaric": "sensitive",
                     "minerva": "sensitive", "victor": "routine"}
    assert cfg.engine("nemotron-3-super").port == 8103
    with pytest.raises(ConfigError, match="unknown engine"):
        cfg.engine("nope")
    assert cfg.router_rules.hard_keywords[-2:] == list(FAMILY)
