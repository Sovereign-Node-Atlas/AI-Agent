"""config/: engines.json ports, router rules placeholder, task forces, personas, domain cards."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.config import (
    ARBITER_HEADROOM_RANGE_GIB,
    DEFAULT_ARBITER_HEADROOM_GIB,
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
    # The family names are Principal data (7.2 rule 1): a `%r` of the settings in a log line must not carry them.
    assert "Testwood" not in repr(s) and "Ava" not in str(s) and "family_names" not in repr(s)
    monkeypatch.setenv("LLAMA_PORT_BASE", "9100")
    monkeypatch.setenv("ATLAS_DB_PATH", "/tmp/x.sqlite3")
    s2 = Settings.from_env()
    assert s2.llama_port_base == 9100 and s2.db_path == Path("/tmp/x.sqlite3")
    monkeypatch.setenv("LLAMA_PORT_BASE", "abc")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_settings_arbiter_headroom_default_override_and_bounds(tmp_path: Path) -> None:
    """Section 4.1 (S44): the host reserve defaults to 16 GiB, ATLAS_ARBITER_HEADROOM_GIB overrides it in whole GiB, and
    a value that is not a number or leaves the bounded range stops the orchestrator instead of being guessed."""
    base = {"ATLAS_ETC": str(tmp_path)}
    assert DEFAULT_ARBITER_HEADROOM_GIB == 16
    assert Settings.from_env(base).arbiter_headroom_bytes == 16 * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": ""}).arbiter_headroom_bytes == 16 * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": " 24 "}).arbiter_headroom_bytes == 24 * 1024**3
    lo, hi = ARBITER_HEADROOM_RANGE_GIB
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": str(lo)}).arbiter_headroom_bytes == lo * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": str(hi)}).arbiter_headroom_bytes == hi * 1024**3
    for bad in ("abc", "12.5", "-1", "0", str(lo - 1), str(hi + 1)):
        with pytest.raises(ConfigError, match="ATLAS_ARBITER_HEADROOM_GIB"):
            Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": bad})
    # atlas.env is the documented fallback for every setting; the process environment wins over it.
    (tmp_path / "atlas.env").write_text("ATLAS_ARBITER_HEADROOM_GIB=20\n")
    assert Settings.from_env(base).arbiter_headroom_bytes == 20 * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": "18"}).arbiter_headroom_bytes == 18 * 1024**3


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
    # The ten llama-server engines (CONVENTIONS.md §8) come first and take the ports; the `external` list (Section 4.2
    # class external: chatterbox, in-process, budgeted by the Arbiter but no unit) is appended with port 0.
    units = {k: e for k, e in engines.items() if not e.is_external}
    assert list(units) == list(ENGINE_KEYS)
    assert [e.port for e in units.values()] == list(range(8101, 8111))
    assert list(engines) == [*ENGINE_KEYS, "chatterbox"]
    ext = engines["chatterbox"]
    assert ext.is_external and ext.port == 0 and ext.index == 0 and not ext.is_resident and not ext.is_phase4
    assert ext.footprint_gb == 4 and ext.kv_class == "none" and ext.hf_repo == "" and ext.files == []
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


def test_external_list_is_the_only_home_of_class_external(config_dir: Path, tmp_path: Path) -> None:
    """Section 4.2 class external (chatterbox) lives in engines.json's `external` list: inside engines[] it would take a
    port, a unit, a sudoers line and an env file it cannot have (phase2/01-llama.sh counts ten, engine-env.py renders
    engines[]); and only class external may be listed there. Both mistakes are a ConfigError naming the entry."""
    real = json.loads((config_dir / "engines.json").read_text(encoding="utf-8"))
    bad = dict(real)
    bad["engines"] = [*real["engines"], real["external"][0]]
    bad.pop("external")
    (tmp_path / "engines.json").write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match=r"chatterbox.*belongs in the top-level `external` list"):
        load_engines(tmp_path, 8100)
    bad = dict(real)
    bad["external"] = [{**real["external"][0], "arbiter_class": "core", "kv_class": "q8_0"}]
    (tmp_path / "engines.json").write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match=r"external engine 'chatterbox': arbiter_class must be 'external'"):
        load_engines(tmp_path, 8100)
    bad = dict(real)
    bad["external"] = [{**real["external"][0], "key": "gpt-oss-120b"}]
    (tmp_path / "engines.json").write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match=r"duplicate engine key 'gpt-oss-120b'"):
        load_engines(tmp_path, 8100)
    bad = dict(real)
    bad["external"] = {"key": "chatterbox"}
    (tmp_path / "engines.json").write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match=r"`external` must be a list"):
        load_engines(tmp_path, 8100)
    # Without the list the ten engines load as before (an older engines.json keeps working).
    bad = dict(real)
    bad.pop("external")
    (tmp_path / "engines.json").write_text(json.dumps(bad))
    assert list(load_engines(tmp_path, 8100)) == list(ENGINE_KEYS)


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
    # CONVENTIONS.md §8: comma-separated triple, the owner drops 8.2's inner comma. Silent. Domain 13 is the one whose
    # §8 owner carries the em-dash ("Arthur — tagged to 14"); domain 14's owner is plain "Arthur".
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        assert parse_card_heading("# 07. Development Director, Property & Real Estate  (Corporate, Valerie with Silas, "
                                  "Tier A)") == (7, "Development Director, Property & Real Estate", "Corporate",
                                                 "Valerie with Silas", "A")
        assert parse_card_heading("# 13. Dean of Academia & Pedagogy  (Estate, Arthur — tagged to 14, Tier C)") == (
            13, "Dean of Academia & Pedagogy", "Estate", "Arthur — tagged to 14", "C")
    assert caplog.text == ""
    # Non-conforming spellings (pipes, 8.2's inner comma): the lenient parse normalises and warns with the H1 the card
    # should carry; strict=True (load_domain_cards(strict=True), `atlas-admin config check`) raises the same message.
    h7 = "# 07. Development Director, Property & Real Estate  (Corporate | Valerie, with Silas | Tier A)"
    h26 = "# 26. Cultural Asset & Fine Art Curator  (Estate, Alaric, with Silas, Tier C)"
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        assert parse_card_heading(h7, source="cards/07.md") == (7, "Development Director, Property & Real Estate",
                                                                "Corporate", "Valerie with Silas", "A")
        assert parse_card_heading(h26) == (26, "Cultural Asset & Fine Art Curator", "Estate", "Alaric with Silas", "C")
    assert "cards/07.md" in caplog.text and "pipe-separated" in caplog.text and "inner comma" in caplog.text
    assert "'(Corporate, Valerie with Silas, Tier A)'" in caplog.text
    with pytest.raises(ConfigError, match=r"pipe-separated.*should say '\(Corporate, Valerie with Silas, Tier A\)'"):
        parse_card_heading(h7, source="cards/07.md", strict=True)
    with pytest.raises(ConfigError, match="inner comma"):
        parse_card_heading(h26, strict=True)
    with pytest.raises(ConfigError):
        parse_card_heading("# Not a card")


def _card_tree(tmp_path: Path) -> Path:
    cards = tmp_path / "domains" / "cards"
    cards.mkdir(parents=True)
    return cards


def test_load_domain_cards_warns_by_default_and_refuses_when_strict(tmp_path: Path,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    # Fix round 3 (blocker): the service must START on the tree the node has, so the default load normalises a
    # non-§8 H1 and warns with the H1 the card should carry; the §8 agreement is enforced by strict=True (the gate,
    # `atlas-admin config check`), where ONE ConfigError names every offending card (rule §7.4).
    cards = _card_tree(tmp_path)
    (cards / "05-cfo.md").write_text("# 05. CFO  (Corporate, Silas, Tier A)\n\nbody\n")
    (cards / "07-dev.md").write_text("# 07. Dev  (Corporate | Valerie, with Silas | Tier A)\n\nbody\n")
    (cards / "26-art.md").write_text("# 26. Art  (Estate, Alaric, with Silas, Tier C)\n\nbody\n")
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        loaded = load_domain_cards(tmp_path)
    assert [c.owner for c in loaded.values()] == ["Silas", "Valerie with Silas", "Alaric with Silas"]  # §8 spellings
    assert caplog.text.count("not in the CONVENTIONS.md §8 form") == 2 and "05-cfo" not in caplog.text
    assert "'(Corporate, Valerie with Silas, Tier A)'" in caplog.text
    with pytest.raises(ConfigError) as ei:
        load_domain_cards(tmp_path, strict=True)
    msg = str(ei.value)
    assert msg.startswith("2 domain card(s) do not carry the CONVENTIONS.md §8 H1 triple")
    assert str(cards / "07-dev.md") in msg and str(cards / "26-art.md") in msg and "05-cfo" not in msg
    assert "'(Corporate, Valerie with Silas, Tier A)'" in msg and "'(Estate, Alaric with Silas, Tier C)'" in msg
    # Fixed cards load in both modes; the hemisphere comes back as the §8 key, never the H1 capitalisation.
    (cards / "07-dev.md").write_text("# 07. Dev  (Corporate, Valerie with Silas, Tier A)\n\nbody\n")
    (cards / "26-art.md").write_text("# 26. Art  (Both, Alaric with Silas, Tier C)\n\nbody\n")
    assert [c.hemisphere for c in load_domain_cards(tmp_path, strict=True).values()] == ["corporate", "corporate",
                                                                                           "both"]
    # Everything that is not the §8 triple drift stays a hard error in BOTH modes.
    (cards / "30-x.md").write_text("# 30. X  (Personal, Ren, Tier A)\n")
    for strict in (False, True):
        with pytest.raises(ConfigError, match=r"30-x\.md: hemisphere 'Personal' is not one of"):
            load_domain_cards(tmp_path, strict=strict)


def _real_config_tree() -> Path | None:
    """scripts/day1/config when this checkout (or ATLAS_CONFIG_DIR on the node) has it; None otherwise."""
    for candidate in (os.environ.get("ATLAS_CONFIG_DIR", ""), str(Path(__file__).resolve().parents[2] / "config")):
        tree = Path(candidate) if candidate else None
        if tree is not None and (tree / "engines.json").is_file() and (tree / "domains" / "cards").is_dir():
            return tree
    return None


def test_real_config_tree_loads(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # CONVENTIONS.md §8 asks for names that agree across every file; the fixtures alone cannot prove that, so this
    # runs the loaders against the REAL tree (scripts/day1/config, or ATLAS_CONFIG_DIR on the node) when it is there.
    # Two claims: (1) the service starts on it (load_config, lenient on the cards: the Phase 2 step 2 blocker);
    # (2) every domain card carries the §8 H1 triple (strict). Until cards 07, 11, 26, 28, 32 and 35 read
    # '(Corporate, Valerie with Silas, Tier A)', '(Corporate, Helena with Valerie, Tier C)', '(Estate, Alaric with
    # Silas, Tier C)', '(Corporate, Helena with Minerva, Tier C)', '(Corporate, Valerie with Alaric, Tier B)' and
    # '(Corporate, Valerie with Minerva, Tier C)', claim 2 FAILS here by design (fix round 3; the cards are another
    # writer's files). ATLAS_ETC is a scratch dir so no real /etc/atlas/atlas.env (family names) is read.
    tree = _real_config_tree()
    if tree is None:
        pytest.skip("the real config tree is not beside this checkout and ATLAS_CONFIG_DIR is unset")
    settings = Settings.from_env({"ATLAS_CONFIG_DIR": str(tree), "ATLAS_ETC": str(tmp_path)})
    with caplog.at_level(logging.WARNING, logger="atlas.config"):
        cfg = load_config(settings)
    assert set(cfg.personas) == {"ren", "arthur", "gideon", "silas", "valerie", "helena", "eleanor", "alaric",
                                 "minerva", "victor"}
    # §1 counts: "the 7 GGUF engines + 3 resident small models" are the ten llama-server units (arbiter_class !=
    # external); engines.json's `external` list adds chatterbox (Section 4.2 "Chatterbox when invoked":
    # Arbiter-budgeted, in-process, no unit, no port), so the whole map holds 11 and the unit count stays an honest 10.
    units = [k for k, e in cfg.engines.items() if not e.is_external]
    assert len(cfg.domain_cards) == 36 and len(cfg.task_forces) == 23 and len(units) == 10
    assert units == list(ENGINE_KEYS) and len(cfg.engines) == 11 and cfg.engines["chatterbox"].is_external
    assert len(cfg.phase4_engines) >= 16
    drift = [ln for ln in caplog.text.splitlines() if "not in the CONVENTIONS.md §8 form" in ln]
    try:
        load_domain_cards(tree, strict=True)
    except ConfigError as exc:
        pytest.fail(f"the real tree loads (the service starts) but {len(drift)} domain card(s) drift from the "
                    f"CONVENTIONS.md §8 H1 triple; fix the cards (another writer's files):\n{exc}")
    assert drift == []


def test_load_domain_cards(config_dir: Path) -> None:
    cards = load_domain_cards(config_dir)
    assert set(cards) == {5, 6, 7, 14, 22, 26}
    assert cards[26].is_tier_c and not cards[5].is_tier_c
    assert cards[7].owner == "Valerie with Silas" and cards[26].owner == "Alaric with Silas"  # §8 spellings
    assert cards[14].hemisphere == "estate" and cards[5].hemisphere == "corporate"  # §8 keys, lowercase
    assert cards[22].owner == "Alaric"
    assert cards[5].text.startswith("# 05.")


def _tree_copy(config_dir: Path, tmp_path: Path) -> Path:
    import shutil

    dst = tmp_path / "config"
    shutil.copytree(config_dir, dst)
    return dst


def _settings_for(tree: Path) -> Settings:
    return Settings.from_env({"ATLAS_CONFIG_DIR": str(tree), "ATLAS_ETC": str(tree)})


def _edit_task_forces(tree: Path, edit: Callable[[list[dict[str, Any]]], None]) -> None:
    path = tree / "task-forces.json"
    data = json.loads(path.read_text())
    edit(data["task_forces"])
    path.write_text(json.dumps(data, indent=2))


def _preset(entries: list[dict[str, Any]], code: str) -> dict[str, Any]:
    return next(e for e in entries if e["code"] == code)


def test_tiers_and_owners_are_validated_at_load(config_dir: Path, tmp_path: Path) -> None:
    # Rule §7.4: a preset typed `Sensitive` or an owner that is not a persona fails at load, not inside the router or
    # the approval gate at dispatch time. TIERS is the §8 set (routine/standard/sensitive), lowercase.
    tree = _tree_copy(config_dir, tmp_path)
    good = (tree / "task-forces.json").read_text()
    _edit_task_forces(tree, lambda tfs: _preset(tfs, "TF_ALPHA").__setitem__("default_tier", "Sensitive"))
    with pytest.raises(ConfigError, match=r"(?s)preset 'TF_ALPHA'.*default_tier 'Sensitive' is not one of "
                                           r"\('routine', 'standard', 'sensitive'\)"):
        load_task_forces(tree)
    (tree / "task-forces.json").write_text(good)
    _edit_task_forces(tree, lambda tfs: _preset(tfs, "TF_OMEGA").__setitem__("owners", ["Ren", "Cornelius"]))
    with pytest.raises(ConfigError, match=r"TF_OMEGA owners entry 'Cornelius' is not a persona"):
        load_config(_settings_for(tree))
    (tree / "task-forces.json").write_text(good)
    _edit_task_forces(tree, lambda tfs: _preset(tfs, "TF_ALPHA").__setitem__("relay", ["Nobody, tagged to 14"]))
    with pytest.raises(ConfigError, match=r"TF_ALPHA relay entry 'Nobody, tagged to 14' is not a persona"):
        load_config(_settings_for(tree))
    (tree / "task-forces.json").write_text(good)
    assert load_config(_settings_for(tree)).task_forces["TF_OMEGA"].owners == ["Ren", "Arthur"]  # the copy is sound
    victor = tree / "personas" / "victor.md"
    victor.write_text(victor.read_text().replace("speaks_externally_tier: routine", "speaks_externally_tier: Routine"))
    with pytest.raises(ConfigError, match=r"(?s)victor\.md: .*speaks_externally_tier 'Routine' is not one of"):
        load_config(_settings_for(tree))


def test_presets_never_name_a_tier_c_card(config_dir: Path, tmp_path: Path) -> None:
    # Section 8.4 rule 4: Tier C loads only on an explicit match; a preset would load it on every dispatch.
    tree = _tree_copy(config_dir, tmp_path)
    _edit_task_forces(tree, lambda tfs: _preset(tfs, "TF_ALPHA")["domain_cards"].__setitem__(
        0, {"domain": 26, "why": "a Tier C card named by a preset"}))
    with pytest.raises(ConfigError, match=r"TF_ALPHA names Tier C domain 26; 8\.4 rule 4 forbids speculative"):
        load_config(_settings_for(tree))


def test_telemetry_opt_out_is_forced_not_defaulted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rule §7.1 is absolute: a shell profile or unit drop-in that re-enables chromadb's PostHog beacon loses on import.
    import importlib
    import os

    import atlas

    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "true")
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "0")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    importlib.reload(atlas)
    assert os.environ["ANONYMIZED_TELEMETRY"] == "false"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1" and os.environ["DO_NOT_TRACK"] == "1"
    assert atlas.TELEMETRY_OPT_OUT["ANONYMIZED_TELEMETRY"] == "false"


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
