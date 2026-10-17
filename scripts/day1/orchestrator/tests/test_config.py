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

GLM = "glm-5.3-flash"
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
    """Section 4.1 (S44, S48): the host reserve defaults to 16 GiB, ATLAS_ARBITER_HEADROOM_GIB overrides it in whole
    GiB. A bad value never raises here (every task, the backup freeze and Sentinel read these settings): it leaves the
    default and its reason in arbiter_headroom_error, which build_arbiter turns into a ConfigError."""
    base = {"ATLAS_ETC": str(tmp_path)}
    assert DEFAULT_ARBITER_HEADROOM_GIB == 16
    s = Settings.from_env(base)
    assert s.arbiter_headroom_bytes == 16 * 1024**3 and s.arbiter_headroom_error is None
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": ""}).arbiter_headroom_bytes == 16 * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": " 24 "}).arbiter_headroom_bytes == 24 * 1024**3
    lo, hi = ARBITER_HEADROOM_RANGE_GIB
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": str(lo)}).arbiter_headroom_bytes == lo * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": str(hi)}).arbiter_headroom_bytes == hi * 1024**3
    for bad in ("abc", "12.5", "-1", "0", str(lo - 1), str(hi + 1)):
        s = Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": bad})
        assert s.arbiter_headroom_bytes == 16 * 1024**3
        assert s.arbiter_headroom_error is not None and "ATLAS_ARBITER_HEADROOM_GIB" in s.arbiter_headroom_error
    # atlas.env is the documented fallback for every setting; the process environment wins over it.
    (tmp_path / "atlas.env").write_text("ATLAS_ARBITER_HEADROOM_GIB=20\n")
    assert Settings.from_env(base).arbiter_headroom_bytes == 20 * 1024**3
    assert Settings.from_env({**base, "ATLAS_ARBITER_HEADROOM_GIB": "18"}).arbiter_headroom_bytes == 18 * 1024**3


def test_settings_arbiter_max_resident_default_override_and_bounds(tmp_path: Path) -> None:
    """D-32 (approved 2026-10-16): the LLM-seat residency limit is a setting, default 1 (every seat runs alone), 2 the
    option (b) pairing; a bad value never raises here and leaves the default plus its reason for build_arbiter."""
    from atlas.config import ARBITER_MAX_RESIDENT_RANGE, DEFAULT_ARBITER_MAX_RESIDENT

    base = {"ATLAS_ETC": str(tmp_path)}
    assert DEFAULT_ARBITER_MAX_RESIDENT == 1 and ARBITER_MAX_RESIDENT_RANGE == (1, 2)
    s = Settings.from_env(base)
    assert s.arbiter_max_resident == 1 and s.arbiter_max_resident_error is None
    assert Settings.from_env({**base, "ATLAS_ARBITER_MAX_RESIDENT": " 2 "}).arbiter_max_resident == 2
    for bad in ("two", "1.5", "-1", "0", "3"):
        s = Settings.from_env({**base, "ATLAS_ARBITER_MAX_RESIDENT": bad})
        assert s.arbiter_max_resident == 1
        assert s.arbiter_max_resident_error is not None and "ATLAS_ARBITER_MAX_RESIDENT" in s.arbiter_max_resident_error


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
    # The seven Day 1 llama-server engines (CONVENTIONS.md §8, v0.4.0) come first and take the ports 8101..8107; the
    # `external` list (Section 4.2 class external: chatterbox, in-process, budgeted by the Arbiter but no unit) is
    # appended with port 0.
    units = {k: e for k, e in engines.items() if not e.is_external}
    assert list(units) == list(ENGINE_KEYS)
    assert [e.port for e in units.values()] == list(range(8101, 8101 + len(ENGINE_KEYS)))
    assert list(engines) == [*ENGINE_KEYS, "chatterbox"]
    # The abliterated twin keeps its v0.3.5 port (8102), and the uncensored entry is otherwise untouched.
    assert engines["gpt-oss-120b"].port == 8101 and engines["gpt-oss-120b-abliterated"].port == 8102
    ext = engines["chatterbox"]
    assert ext.is_external and ext.port == 0 and ext.index == 0 and not ext.is_resident and not ext.is_phase4
    assert ext.footprint_gb == 4 and ext.kv_class == "none" and ext.hf_repo == "" and ext.files == []
    assert engines["deepseek-v4-flash"].is_apex and engines["deepseek-v4-flash"].exclusive
    assert engines["router-qwen3.5-4b"].is_resident and engines["router-qwen3.5-4b"].placement == "gpu"
    assert engines["embed-qwen3-4b"].placement == engines["rerank-qwen3-4b"].placement == "cpu"  # D-14 (e)
    assert engines["glm-5.3-flash"].systemd_unit == "llama-server@glm-5.3-flash"
    assert {k for k, e in engines.items() if e.is_llm_seat} == {
        "gpt-oss-120b", "gpt-oss-120b-abliterated", "glm-5.3-flash", "deepseek-v4-flash"}
    assert load_engines(config_dir, 9000)["gpt-oss-120b"].port == 9001


def test_footprint_is_the_exact_file_bytes_in_gib(config_dir: Path) -> None:
    """D-31 / S-20: the Arbiter budgets the Hugging Face tree sizes (files[].bytes, mmproj included), not footprint_gb
    read as GiB; an entry without byte counts (the unchanged abliterated twin) keeps the v0.3.5 upper bound."""
    engines = load_engines(config_dir, 8100)
    glm, dsv4 = engines["glm-5.3-flash"], engines["deepseek-v4-flash"]
    assert glm.footprint_bytes == 156_822_111_200 + 1_128_047_296 and glm.footprint_source == "files"
    assert dsv4.footprint_bytes == 161_869_615_520  # 150.75 GiB, where footprint_gb read as GiB was 161.87
    assert round(dsv4.footprint_bytes / 1024**3, 2) == 150.75 < dsv4.footprint_gb
    assert engines["gpt-oss-120b"].footprint_bytes == 65_369_017_728
    abl = engines["gpt-oss-120b-abliterated"]
    assert abl.files == [] and abl.footprint_source == "footprint_gb" and abl.footprint_bytes == 63 * 1024**3
    # A file without a byte count falls back to the upper bound rather than under-count the engine.
    partial = glm.model_copy(update={"files": [glm.files[0].model_copy(update={"bytes": None}), *glm.files[1:]]})
    assert partial.footprint_source == "footprint_gb" and partial.footprint_bytes == int(glm.footprint_gb * 1024**3)


def test_mmproj_and_placement_rules(config_dir: Path, tmp_path: Path) -> None:
    """D-31: with exact bytes the mmproj must be one of files[] (else its ~1 GiB would be left out of the budget);
    D-14 (e): only an always-resident companion may run on the CPU; placement is gpu or cpu."""
    real = json.loads((config_dir / "engines.json").read_text(encoding="utf-8"))
    glm = next(e for e in real["engines"] if e["key"] == "glm-5.3-flash")
    cases = (
        ({**glm, "files": [f for f in glm["files"] if "mmproj" not in f["name"]]}, r"mmproj 'mmproj-F16\.gguf' is not"),
        ({**glm, "placement": "cpu"}, r"placement 'cpu' is for always-resident companions"),
        ({**glm, "placement": "npu"}, r"placement 'npu' is not one of"),
    )
    for entry, msg in cases:
        bad = {**real, "engines": [entry if e["key"] == "glm-5.3-flash" else e for e in real["engines"]]}
        (tmp_path / "engines.json").write_text(json.dumps(bad))
        with pytest.raises(ConfigError, match=msg):
            load_engines(tmp_path, 8100)


def test_port_range_is_bounded(config_dir: Path, tmp_path: Path) -> None:
    """S-33: engines[] takes LLAMA_PORT_BASE + 1 .. + ENGINE_PORT_SLOTS (8101..8120); one more is refused, by name."""
    from atlas.config import ENGINE_PORT_SLOTS

    real = json.loads((config_dir / "engines.json").read_text(encoding="utf-8"))
    extra = [{**real["engines"][0], "key": f"phased-{i}", "files": []} for i in range(ENGINE_PORT_SLOTS)]
    (tmp_path / "engines.json").write_text(json.dumps({**real, "engines": [*real["engines"], *extra]}))
    with pytest.raises(ConfigError, match=r"exceed the 20-port range 8101\.\.8120"):
        load_engines(tmp_path, 8100)
    fits = extra[: ENGINE_PORT_SLOTS - len(real["engines"])]
    (tmp_path / "engines.json").write_text(json.dumps({**real, "engines": [*real["engines"], *fits]}))
    assert max(e.port for e in load_engines(tmp_path, 8100).values()) == 8100 + ENGINE_PORT_SLOTS


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


def test_kv_classes_are_the_conventions_set_and_llm_engines_name_their_cache(tmp_path: Path) -> None:
    # D-29 (a): f16 where the cache is cheap (GLM, DeepSeek V4), q8_0 elsewhere; `none` is for embedding/reranking.
    assert KV_CLASSES == {"f16", "q8_0", "q4_0", "none"}
    base = '{"key": "x", "arbiter_class": "core", "footprint_gb": 1, "ctx_size": 4096, "mode": "%s", "kv_class": "%s"}'
    for mode, kv, msg in (("chat", "q3_k", "kv_class 'q3_k'"), ("chat", "none", "needs a kv_class"),
                          ("vision", "none", "needs a kv_class")):
        (tmp_path / "engines.json").write_text('{"engines": [%s]}' % (base % (mode, kv)))
        with pytest.raises(ConfigError, match=msg):
            load_engines(tmp_path, 8100)
    for mode, kv in (("embedding", "none"), ("chat", "f16")):  # both validate; the key set is what is missing
        (tmp_path / "engines.json").write_text('{"engines": [%s]}' % (base % (mode, kv)))
        with pytest.raises(ConfigError, match="missing from CONVENTIONS"):
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
    # v0.4.0 Day 1 counts (CONVENTIONS.md §8): four LLM seats (gpt-oss-120b, its unchanged abliterated twin,
    # GLM-5.3-Flash, DeepSeek-V4-Flash-0731) and three always-resident companions (the router on the iGPU, the Qwen3
    # retrieval pair on the CPU) are the seven llama-server units (arbiter_class != external); engines.json's `external`
    # list adds chatterbox (Section 4.2 "Chatterbox when invoked": Arbiter-budgeted, in-process, no unit, no port).
    units = [k for k, e in cfg.engines.items() if not e.is_external]
    assert len(cfg.domain_cards) == 36 and len(cfg.task_forces) == 23 and len(units) == len(ENGINE_KEYS) == 7
    assert units == list(ENGINE_KEYS) and len(cfg.engines) == 8 and cfg.engines["chatterbox"].is_external
    assert [k for k in units if cfg.engines[k].is_llm_seat] == ["gpt-oss-120b", "gpt-oss-120b-abliterated",
                                                                "glm-5.3-flash", "deepseek-v4-flash"]
    assert [k for k in units if cfg.engines[k].placement == "cpu"] == ["embed-qwen3-4b", "rerank-qwen3-4b"]  # D-14
    # D-31: every Day 1 engine but the unchanged uncensored entry carries exact file bytes, so the Arbiter budgets them.
    assert [k for k in units if cfg.engines[k].footprint_source != "files"] in ([], ["gpt-oss-120b-abliterated"])
    assert len(cfg.phase4_engines) >= 16
    drift = [ln for ln in caplog.text.splitlines() if "not in the CONVENTIONS.md §8 form" in ln]
    try:
        load_domain_cards(tree, strict=True)
    except ConfigError as exc:
        pytest.fail(f"the real tree loads (the service starts) but {len(drift)} domain card(s) drift from the "
                    f"CONVENTIONS.md §8 H1 triple; fix the cards (another writer's files):\n{exc}")
    assert drift == []


def test_real_config_is_the_approved_day1_map(tmp_path: Path) -> None:
    """v0.4.0 (approved 2026-10-16): the real tree carries the Day 1 map, and every engine reference in config/
    resolves to an engines.json key. S-32 / D-40 (a) / P-10 persona bindings, S-30 routes, S-29 presets, S-42 / D-29 (a)
    KV types, D-33 (c) decode bands, S-11 retirements, the sentinel unit list, and the exclusion: the uncensored entry,
    its routes and prefixes and Valerie's default engine are exactly as in v0.3.5."""
    tree = _real_config_tree()
    if tree is None:
        pytest.skip("the real config tree is not beside this checkout and ATLAS_CONFIG_DIR is unset")
    cfg = load_config(Settings.from_env({"ATLAS_CONFIG_DIR": str(tree), "ATLAS_ETC": str(tmp_path)}))
    glm, ds, gpt, abl = "glm-5.3-flash", "deepseek-v4-flash", "gpt-oss-120b", "gpt-oss-120b-abliterated"
    bindings = {k: (p.default_engine, tuple(p.override_engines), p.fallback_engine) for k, p in cfg.personas.items()}
    assert bindings == {
        "ren": (glm, (abl, ds), gpt), "arthur": (ds, (glm, abl), glm), "gideon": (glm, (ds,), ds),
        "silas": (glm, (), gpt), "helena": (glm, (), ds), "minerva": (glm, (), gpt), "victor": (glm, (), gpt),
        "alaric": (glm, (), gpt), "eleanor": (glm, (), gpt), "valerie": (abl, (), None),
    }
    eleanor = (tree / "personas" / "eleanor.md").read_text(encoding="utf-8")
    assert "resident_model: router-qwen3.5-4b" in eleanor and cfg.router_rules.classifier_engine == "router-qwen3.5-4b"
    assert cfg.router_rules.routes == {"ren": glm, "arthur": ds, "ren-abliterated": abl, "arthur-long": glm,
                                       "arthur-abliterated": abl, "deep-think:deep": ds}
    assert cfg.router_rules.overrides["[ARTHUR:LONG]"] == "arthur-long"
    assert cfg.router_rules.overrides["[REN:UNCENSORED]"] == "ren-abliterated"
    assert cfg.router_rules.overrides["[ARTHUR:UNCENSORED]"] == "arthur-abliterated"
    assert cfg.router_rules.long_document_tokens == 24000  # D-38: a measured to-do, unchanged until Phase 3
    assert {c: t.engine for c, t in cfg.task_forces.items() if t.engine} == {"TF_OMEGA": ds}
    # Engines: KV per D-29 (a), the Apex ladder never reaching q4_0 (S-42), projections not bands (D-33 (c)).
    e = cfg.engines
    assert {k: e[k].kv_class for k in ENGINE_KEYS} == {
        gpt: "q8_0", abl: "q4_0", glm: "f16", ds: "f16", "router-qwen3.5-4b": "q8_0", "embed-qwen3-4b": "q8_0",
        "rerank-qwen3-4b": "q8_0"}
    assert e[ds].kv_ladder == ["f16", "q8_0"] and e[ds].ctx_size_f16_cap is None and e[ds].exclusive
    assert e[glm].mmproj == "mmproj-F16.gguf" and e[glm].mode == "vision" and e[glm].kv_types_must_match
    assert [k for k in ENGINE_KEYS if e[k].expected_decode_tok_s is not None] == [abl]
    raw = json.loads((tree / "engines.json").read_text(encoding="utf-8"))
    entries = {x["key"]: x for x in raw["engines"]}
    old_abl = {
        "key": abl, "mode": "chat", "arbiter_class": "core", "kv_class": "q4_0",
        "hf_repo": "noctrex/Huihui-gpt-oss-120b-abliterated-MXFP4_MOE-GGUF", "files": [],
        "enumerate_pattern": "*MXFP4_MOE*.gguf", "footprint_gb": 63, "ctx_size": 262144, "parallel": 8}
    assert {k: entries[abl].get(k) for k in old_abl} == old_abl  # the exclusion: unchanged from v0.3.5
    assert all(x.get("projected_decode_tok_s") for k, x in entries.items() if e[k].is_llm_seat and k != abl)
    # S-11: the retired keys appear nowhere a reference could resolve; phased engines are records, not keys.
    retired = {"nemotron-3-super", "qwen3.5-122b", "qwen2.5-vl-72b", "meditron-70b", "embed-bge-m3",
               "rerank-bge-v2-m3"}
    assert not retired & set(e)
    planned = {x["planned_key"] for x in raw["phased"]}
    # D-19 (c) and D-60 (b), approved 2026-10-17: Qwen3-Coder-Next and EmbeddingGemma 2 left the phased list.
    assert planned == {"qwen3.8-flash-next", "nemotron-3-super", "intern-s1", "ling-3.0-flash-fin",
                       "baichuan-m3-235b"} and not planned & set(e)
    refs = {x for b in bindings.values() for x in (b[0], *b[1], b[2]) if x} | set(cfg.router_rules.routes.values())
    assert refs <= set(e) and not refs & planned
    # Sentinel's services feed names units that exist (llama-server@<engines.json key>).
    feeds = json.loads((tree / "sentinel-feeds.json").read_text(encoding="utf-8"))
    services = next(f for f in feeds["telemetry"] if f.get("id") == "services")["source"].split()
    units = [u.split("@", 1)[1] for u in services if u.startswith("llama-server@")]
    assert units == ["router-qwen3.5-4b", "embed-qwen3-4b", "rerank-qwen3-4b"]
    assert all(e[u].is_resident for u in units)
    # D-19 (c), D-60 (b): no file of the config tree names either dropped key (the research notes keep their record).
    for path in sorted(p for p in tree.rglob("*") if p.is_file() and p.suffix in (".json", ".md")):
        text = path.read_text(encoding="utf-8")
        assert "qwen3-coder-next" not in text and "embeddinggemma-2" not in text, path


def test_real_engines_json_speed_floors(tmp_path: Path) -> None:
    """D-33 and D-47 (approved 2026-10-17): the per-seat floors in engines.json, the schema phase3/loadtest.py reads
    with spec.get() (check_band, v10_line, raise_decode_floor_todo; config.EngineSpec ignores these keys). Decode floors
    at 8k: gpt-oss-120b 25, glm-5.3-flash 6, deepseek-v4-flash 4, router-qwen3.5-4b 20 tok/s. Prefill floors at 8k:
    200 / 40 / 40, none for the router. Every floor is a positive number or null (null or absent: record only), below
    its seat's projection; gpt-oss-120b's projection is the F16-trunk figure [41, 44] and the router's [45, 50]. The
    unchanged abliterated entry carries no floor field, and no phased entry carries one."""
    tree = _real_config_tree()
    if tree is None:
        pytest.skip("the real config tree is not beside this checkout and ATLAS_CONFIG_DIR is unset")
    raw = json.loads((tree / "engines.json").read_text(encoding="utf-8"))
    entries = {x["key"]: x for x in raw["engines"]}
    gpt, abl, glm, ds, router = ("gpt-oss-120b", "gpt-oss-120b-abliterated", "glm-5.3-flash", "deepseek-v4-flash",
                                 "router-qwen3.5-4b")
    floors = ("decode_floor_tok_s", "prefill_floor_tok_s")
    assert {k: x["decode_floor_tok_s"] for k, x in entries.items() if x.get("decode_floor_tok_s") is not None} == {
        gpt: 25, glm: 6, ds: 4, router: 20}
    assert {k: x["prefill_floor_tok_s"] for k, x in entries.items() if x.get("prefill_floor_tok_s") is not None} == {
        gpt: 200, glm: 40, ds: 40}
    assert "prefill_floor_tok_s" not in entries[router]
    assert not any(f in entries[abl] for f in floors)  # the uncensored entry is left as it is
    assert entries[abl].get("expected_decode_tok_s") == [30, 55]
    assert entries[gpt]["projected_decode_tok_s"] == [41, 44] and "F16-trunk" in entries[gpt]["projected_decode_source"]
    assert entries[router]["projected_decode_tok_s"] == [45, 50]
    assert "INFERRED" in entries[router]["projected_decode_source"]
    for key, x in entries.items():
        for f in floors:
            v = x.get(f)
            assert v is None or (isinstance(v, int | float) and not isinstance(v, bool) and v > 0), (key, f, v)
        proj = x.get("projected_decode_tok_s")
        if proj is not None:
            assert (isinstance(proj, list) and len(proj) == 2 and all(isinstance(p, int | float) for p in proj)
                    and 0 < proj[0] <= proj[1]), (key, proj)
        if x.get("decode_floor_tok_s") is not None:
            assert proj is not None and x["decode_floor_tok_s"] < proj[0], key  # a floor never above the projection
    assert not any(f in x for x in raw["phased"] for f in floors)
    rule = raw["_meta"]["decode_rule"]
    for phrase in ("stated at 8k context", "slower of the 512 and 8k decode points",
                   "Only the absolute floor fails V10", "70% of the projection", "never fails", "prefill_floor_tok_s",
                   "recorded only"):
        assert phrase in rule, phrase
    # The orchestrator loads the file with the new key (EngineSpec ignores unknown keys; the service starts).
    cfg = load_config(Settings.from_env({"ATLAS_CONFIG_DIR": str(tree), "ATLAS_ETC": str(tmp_path)}))
    assert set(entries) <= set(cfg.engines)


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
    assert cfg.personas["ren"].default_engine == "glm-5.3-flash" and cfg.personas["ren"].directors[0] == "gideon"
    # D-40: every Day 1 seat names a Day 1 fallback (gpt-oss-120b where the arsenal's runner-up is phased); P-10:
    # Eleanor's own work runs on GLM-5.3-Flash, the router stays her classifier (resident_model).
    assert cfg.personas["ren"].fallback_engine == "gpt-oss-120b" and cfg.personas["arthur"].fallback_engine == GLM
    assert cfg.personas["eleanor"].default_engine == GLM
    assert cfg.personas["eleanor"].extra["resident_model"] == "router-qwen3.5-4b"
    assert cfg.personas["ren"].extra["kokoro_voice"] == "am_onyx"
    # The fixture front matter is config/personas/*.md verbatim (Sections 6.1, 6.2): names and approval tiers.
    assert cfg.personas["arthur"].name == "Arthur Sterling" and cfg.personas["ren"].name == "Ren Ackerman"
    tiers = {k: p.speaks_externally_tier for k, p in cfg.personas.items()}
    assert tiers == {"ren": "sensitive", "arthur": "sensitive", "gideon": "sensitive", "silas": "standard",
                     "valerie": "standard", "helena": "standard", "eleanor": "routine", "alaric": "sensitive",
                     "minerva": "sensitive", "victor": "routine"}
    assert cfg.engine(GLM).port == 8103
    with pytest.raises(ConfigError, match="unknown engine"):
        cfg.engine("nope")
    assert cfg.router_rules.hard_keywords[-2:] == list(FAMILY)


def test_persona_fallback_engine_must_be_an_engines_json_key(config_dir: Path, tmp_path: Path) -> None:
    """D-40: fallback_engine is validated like default_engine and override_engines (every engine key referenced
    anywhere must exist at Day 1), and it is recorded, never an engine the persona is bound to."""
    from atlas.config import load_personas
    from atlas.personas import PersonaRegistry

    engines = load_engines(config_dir, 8100)
    personas = load_personas(config_dir, engines)
    registry = PersonaRegistry(personas, engines)
    binding = registry.binding("silas")
    assert binding.fallback_engine == "gpt-oss-120b" and "gpt-oss-120b" not in binding.all_engines
    with pytest.raises(ConfigError, match="not bound"):
        registry.engine_for("silas", "gpt-oss-120b")
    pdir = tmp_path / "personas"
    pdir.mkdir()
    for f in (config_dir / "personas").glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if f.stem == "silas":
            text = text.replace("fallback_engine: gpt-oss-120b", "fallback_engine: qwen3.8-flash-next")
        (pdir / f.name).write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"silas\.md: engine 'qwen3\.8-flash-next' is not a key of engines\.json"):
        load_personas(tmp_path, engines)
    bad = {**personas, "silas": personas["silas"].model_copy(update={"fallback_engine": "qwen3.8-flash-next"})}
    with pytest.raises(ConfigError, match=r"qwen3\.8-flash-next"):
        PersonaRegistry(bad, engines)
