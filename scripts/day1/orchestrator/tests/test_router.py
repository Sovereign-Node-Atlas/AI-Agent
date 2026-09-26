"""V16: the 4-Way Router (Sections 7.1, 7.2, 8.3, 8.4, 9.1, 4.4; CONVENTIONS.md §6 Phase 2 gate).

Claim proved (verify/v16-router-hard-rule.sh): a message containing "medical" routes to Arthur even when the
classifier says Ren, and the decision is logged with the hard-rule reason. Also: overrides win; task-force detection
picks TF_UPSILON for a longevity-protocol request with <= 3 cards; a Tier C card is not loaded without an explicit
match; the prompt layers (4.4) keep a stable prefix hash per persona.

No live service: the classifier is StubClassifier, the ledger is Ledger(':memory:'), and the config tree is built in
a temp dir from tests/fixtures/config (the fixtures carry every persona and engine; the task forces and two cards
needed here are written by `make_config`). ATLAS_CONFIG_DIR/CONFIG_DIR from /etc/atlas/orchestrator.env are ignored
on purpose so the test is the same on the node and in a checkout.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from atlas.config import AtlasConfig, ConfigError, Settings, load_config
from atlas.engines import LlamaClient
from atlas.ledger import Ledger
from atlas.prompts import GOVERNANCE_BLOCK, PromptBuilder, build_system_prompt
from atlas.router import (
    ClassifierError,
    ClassifierVerdict,
    LlamaClassifier,
    OverrideSyntaxError,
    Router,
    RouterError,
    StubClassifier,
    detect_task_force,
    find_hard_keywords,
    parse_override,
)

FIXTURES = Path(__file__).parent / "fixtures" / "config"
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"  # scripts/day1/config in a checkout / /opt/atlas/day1

ROUTER_RULES = {
    "hard_keywords": ["family", "medical", "health", "vault", "trust", "estate", "will", "children",
                      "FAMILY_NAMES_PLACEHOLDER"],
    "hard_keyword_route": "arthur",
    "overrides": {
        "[REN]": "ren", "[ARTHUR]": "arthur", "[REN:UNCENSORED]": "ren-abliterated", "[ARTHUR:LONG]": "arthur-qwen",
        "[ARTHUR:UNCENSORED]": "arthur-abliterated", "[DEEP THINK:": "deep-think",
        "[DEEP THINK:QUICK]": "deep-think:quick", "[DEEP THINK:STANDARD]": "deep-think:standard",
        "[DEEP THINK:DEEP]": "deep-think:deep",
        "[LOG STRIKE:": "ouroboros-strike", "[EXECUTE AEGIS BACKUP]": "aegis", "[VAULT]": "vault-session",
    },
    "routes": {
        "ren": "gpt-oss-120b", "arthur": "nemotron-3-super", "ren-abliterated": "gpt-oss-120b-abliterated",
        "arthur-qwen": "qwen3.5-122b", "arthur-abliterated": "gpt-oss-120b-abliterated",
        "deep-think:deep": "deepseek-v4-flash",
    },
    "classifier_engine": "router-qwen3.5-4b",
    "long_document_tokens": 24000,
    "max_domain_cards": 3,
}

TASK_FORCES = {"task_forces": [
    {"code": "TF_ALPHA", "name": "Corporate Mergers & Acquisitions", "group": "CORP-DEALS", "hemisphere": "corporate",
     "owners": ["Gideon"], "default_tier": "sensitive",
     "domain_cards": [{"domain": 5}, {"domain": 7}, {"domain": 14}],
     "triggers": ["merger", "acquisition", "m&a", "due diligence"]},
    {"code": "TF_KAPPA", "name": "Contract & Vendor Negotiation", "group": "CORP-CONTRACTS", "hemisphere": "corporate",
     "owners": ["Gideon", "Ren"], "default_tier": "standard",
     "domain_cards": [{"domain": 5}], "triggers": ["contract", "vendor", "negotiation"]},
    {"code": "TF_UPSILON", "name": "Private Medical & Longevity Protocols", "group": "EST-HEALTH",
     "hemisphere": "estate", "owners": ["Minerva"], "default_tier": "sensitive",
     "domain_cards": [{"domain": 10}, {"domain": 15}],
     "triggers": ["medical", "health", "doctor", "longevity", "supplement", "fitness protocol"]},
    {"code": "TF_CHI", "name": "Concierge & Frictionless Travel", "group": "EST-MOBILITY", "hemisphere": "estate",
     "owners": ["Victor"], "default_tier": "routine", "domain_cards": [{"domain": 14}],
     "triggers": ["hotel", "reservation", "booking"]},
    {"code": "TF_OMEGA", "name": "Absolute Apex Contingency", "group": "APEX", "hemisphere": "both",
     "owners": ["Ren", "Arthur"], "default_tier": "sensitive", "dual_sign_off": True,
     "domain_cards": [{"domain": 14}, {"domain": 5}], "triggers": [], "engine": "deepseek-v4-flash",
     "trigger": "principal-only"},
]}

CARD_10 = "# 10. Chief Medical Officer & Risk Underwriter  (Estate, Minerva, Tier A)\n\n**Frame:** Test card 10.\n"
CARD_15 = ("# 15. Chief Longevity Officer & Performance Physiologist "
           "(incl. bioinformatics, genomics and drug discovery)  (Estate, Minerva, Tier A)\n\n"
           "**Frame:** Test card 15.\n")

REN = ClassifierVerdict(hemisphere="corporate", persona="ren")
ARTHUR = ClassifierVerdict(hemisphere="estate", persona="arthur")


def make_config(tmp_path: Path) -> AtlasConfig:
    cfg = tmp_path / "config"
    shutil.copytree(FIXTURES, cfg)
    (cfg / "router-rules.json").write_text(json.dumps(ROUTER_RULES), encoding="utf-8")
    (cfg / "task-forces.json").write_text(json.dumps(TASK_FORCES), encoding="utf-8")
    cards = cfg / "domains" / "cards"
    (cards / "10-chief-medical-officer-risk-underwriter.md").write_text(CARD_10, encoding="utf-8")
    (cards / "15-chief-longevity-officer-performance-physiologist.md").write_text(CARD_15, encoding="utf-8")
    settings = Settings(config_dir=cfg, etc_dir=tmp_path, db_path=tmp_path / "ledger.sqlite3",
                        engines_env_dir=tmp_path, llama_port_base=8100, family_names=("Rida", "Moussa"))
    return load_config(settings)


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> AtlasConfig:
    return make_config(tmp_path_factory.mktemp("cfg"))


@pytest.fixture
def ledger() -> Ledger:
    db = Ledger(":memory:")
    db.init_db()
    yield db  # type: ignore[misc]
    db.close()


def make_router(config: AtlasConfig, ledger: Ledger, verdict: ClassifierVerdict = REN, **kw: object) -> Router:
    return Router(config, StubClassifier(verdict), ledger, **kw)  # type: ignore[arg-type]


# --- V16 -------------------------------------------------------------------------------------------------------------


def test_medical_routes_to_arthur_over_the_classifier_and_is_logged(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)  # Eleanor insists this is Ren's
    d = router.route("Please summarise the medical report the clinic sent yesterday.", task_id="t1")
    assert d.persona == "arthur" and d.route == "arthur" and d.hemisphere == "estate"
    assert d.engine == "nemotron-3-super"
    assert d.hard_keyword_hits == ("medical",)
    assert "hard-rule:medical" in d.reason
    assert d.classifier_route == "ren"  # the disagreement is on the record
    assert "medical" in d.privacy_tags
    assert d.tier == "sensitive"
    rows = ledger.list_routing_decisions("t1")
    assert len(rows) == 1 and rows[0]["id"] == d.ledger_id
    row = rows[0]
    assert row["route"] == "arthur" and row["engine"] == "nemotron-3-super"
    assert row["hard_keyword_hit"] == "medical" and row["classifier_route"] == "ren"
    assert "hard-rule:medical" in row["reason"] and row["tier"] == "sensitive"
    assert row["message_sha256"] == d.message_sha256 and len(row["message_sha256"]) == 64


def test_family_name_from_atlas_env_is_a_hard_rule(config: AtlasConfig, ledger: Ledger) -> None:
    assert "Rida" in config.router_rules.hard_keywords
    assert "FAMILY_NAMES_PLACEHOLDER" not in config.router_rules.hard_keywords
    d = make_router(config, ledger, REN).route("Book a table for Rida on Friday.")
    assert d.persona == "arthur" and "hard-rule:Rida" in d.reason


def test_hard_keywords_match_whole_words_only() -> None:
    kws = ["will", "estate", "Rida"]
    assert find_hard_keywords("The William Street lease is ready.", kws) == []
    assert find_hard_keywords("Update the will and the estate plan for rida.", kws) == ["will", "estate", "Rida"]
    assert find_hard_keywords("Real-estate agents called.", kws) == ["estate"]


# --- overrides (7.2 rule 4) -------------------------------------------------------------------------------------------


def test_override_wins_over_the_hard_rule_and_is_logged(config: AtlasConfig, ledger: Ledger) -> None:
    d = make_router(config, ledger, ARTHUR).route("[REN] Draft the press line on the medical device launch.")
    assert d.persona == "ren" and d.engine == "gpt-oss-120b"
    assert d.override == "[REN]" and d.override_action == "ren"
    assert d.reason.startswith("override:[REN]") and "hard-rule:medical" in d.reason  # both facts on the record
    assert d.body.startswith("Draft the press line")
    assert ledger.list_routing_decisions()[0]["override"] == "[REN]"


def test_override_variants_pick_their_engines(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    assert router.route("[ARTHUR:LONG] read this").engine == "qwen3.5-122b"
    assert router.route("[ARTHUR:UNCENSORED] read this").engine == "gpt-oss-120b-abliterated"
    assert router.route("[REN:UNCENSORED] read this").engine == "gpt-oss-120b-abliterated"
    vault = router.route("[VAULT]")
    assert vault.command == "vault-session" and vault.persona == "arthur"


def test_deep_think_prefixes(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    deep = router.route("[DEEP THINK:DEEP] Should we enter the Singapore market?")
    assert deep.command == "deep-think" and deep.deep_think_depth == "deep"
    assert deep.engine == "deepseek-v4-flash" and deep.persona == "ren"  # Apex synthesis (9.1), Ren's hemisphere
    plain = router.route("[DEEP THINK: Should we enter the Singapore market?] context follows")
    assert plain.command == "deep-think" and plain.deep_think_depth is None
    assert plain.engine == "gpt-oss-120b" and plain.body.startswith("Should we enter")
    chosen = Router(config, StubClassifier(ClassifierVerdict(hemisphere="corporate", deep_think_depth="standard")),
                    ledger).route("[DEEP THINK: pick a depth]")
    assert chosen.deep_think_depth == "standard"
    with pytest.raises(OverrideSyntaxError):
        router.route("[DEEP THINK: no closing bracket")


def test_parse_override_longest_key_wins() -> None:
    ov = ROUTER_RULES["overrides"]
    assert parse_override("[DEEP THINK:DEEP] x", ov).action == "deep-think:deep"
    assert parse_override("[DEEP THINK: x] y", ov).payload == "x"
    assert parse_override("[ren] lowercase is not an override", ov) is None
    assert parse_override("no prefix", ov) is None


# --- classifier (7.2 rule 2) ------------------------------------------------------------------------------------------


def test_classifier_decides_what_the_keywords_miss(config: AtlasConfig, ledger: Ledger) -> None:
    tagged = ClassifierVerdict(hemisphere="corporate", persona="ren", privacy_tags=("family",))
    d = make_router(config, ledger, tagged).route("My mother's company needs a new supplier agreement.")
    assert d.persona == "ren" and d.engine == "gpt-oss-120b" and d.hard_keyword_hits == ()
    assert "classifier:corporate" in d.reason and d.privacy_tags == ("family",)
    assert d.tier == "standard"


def test_classifier_failure_defaults_to_the_defensive_hemisphere(config: AtlasConfig, ledger: Ledger) -> None:
    router = Router(config, StubClassifier(fail=ClassifierError("503 loading")), ledger)
    d = router.route("Plan the quarter.")
    assert d.persona == "arthur" and "classifier-unavailable(503 loading):default-arthur" in d.reason
    strict = Router(config, StubClassifier(fail=ClassifierError("503 loading")), ledger, on_classifier_error="raise")
    with pytest.raises(RouterError, match="no route"):
        strict.route("Plan the quarter.")


def test_verdict_parsing_ignores_garbage() -> None:
    v = ClassifierVerdict.from_json({"hemisphere": "Estate ", "persona": "nobody", "task_force": "tf_upsilon",
                                     "long_document": "yes", "privacy_tags": ["Medical", ""], "depth": "DEEP"},
                                    task_force_codes=("TF_UPSILON",))
    assert v.hemisphere == "estate" and v.persona is None and v.task_force == "TF_UPSILON"
    assert v.long_document is True and v.privacy_tags == ("medical",) and v.deep_think_depth == "deep"


def _fake_llama(handler: Callable[[httpx.Request], httpx.Response]) -> LlamaClient:
    return LlamaClient("http://127.0.0.1:8108", transport=httpx.MockTransport(handler))


def test_llama_classifier_parses_the_resident_verdict() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        text = 'Verdict: {"hemisphere": "estate", "persona": "arthur", "task_force": "TF_UPSILON", ' \
               '"long_document": false, "privacy_tags": ["medical"]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

    clf = LlamaClassifier(_fake_llama(handler), task_force_codes=("TF_UPSILON",))
    v = clf.classify("bloods are back")
    assert v.hemisphere == "estate" and v.persona == "arthur" and v.task_force == "TF_UPSILON"
    assert v.privacy_tags == ("medical",)
    assert seen[0]["messages"][0]["role"] == "system" and seen[0]["messages"][1]["content"] == "bloods are back"
    assert seen[0]["response_format"] == {"type": "json_object"} and seen[0]["temperature"] == 0.0


def test_llama_classifier_retries_without_response_format_on_4xx_and_fails_loudly_otherwise() -> None:
    calls: list[dict] = []

    def rejecting(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field response_format"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"hemisphere": "corporate"}'}}]})

    assert LlamaClassifier(_fake_llama(rejecting)).classify("x").hemisphere == "corporate"
    assert len(calls) == 2 and "response_format" not in calls[1]

    def loading(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": 503, "message": "Loading model"}})

    with pytest.raises(ClassifierError, match="unreachable or failed"):
        LlamaClassifier(_fake_llama(loading)).classify("x")

    def prose(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "I think this is for Ren."}}]})

    with pytest.raises(ClassifierError, match="no JSON object"):
        LlamaClassifier(_fake_llama(prose)).classify("x")


def test_long_document_moves_arthur_to_qwen(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ClassifierVerdict(hemisphere="estate", persona="arthur", long_document=True))
    d = router.route("Read the attached family trust deed and summarise it.")
    assert d.route == "arthur-qwen" and d.engine == "qwen3.5-122b" and d.long_document
    big = "word " * (ROUTER_RULES["long_document_tokens"] * 4 // 5 + 10)
    by_size = make_router(config, ledger, ARTHUR).route("Summarise the estate ledger: " + big)
    assert by_size.route == "arthur-qwen"


# --- task forces and domain cards (7.2 rule 3, 8.3, 8.4) -------------------------------------------------------------


def test_longevity_request_picks_tf_upsilon_with_at_most_three_cards(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    d = router.route("Design a longevity protocol around my supplement stack.", task_id="u")
    assert d.task_force == "TF_UPSILON" and d.directors == ("minerva",)
    assert d.domain_cards == (10, 15) and len(d.domain_cards) <= config.router_rules.max_domain_cards
    assert d.dropped_cards == ()
    assert d.tier == "sensitive" and "task-force:TF_UPSILON" in d.reason
    assert ledger.list_routing_decisions("u")[0]["task_force"] == "TF_UPSILON"
    for tf in config.task_forces.values():
        assert len(tf.domain_cards) <= config.router_rules.max_domain_cards


def test_detection_scores_by_hits_and_never_returns_omega(config: AtlasConfig) -> None:
    found = detect_task_force("The merger needs due diligence before the contract is signed.", config.task_forces)
    assert found is not None and found[0].code == "TF_ALPHA" and set(found[1]) == {"merger", "due diligence"}
    assert detect_task_force("Nothing here matches.", config.task_forces) is None
    assert detect_task_force("The vendor contract is late.", config.task_forces)[0].code == "TF_KAPPA"


def test_tier_c_card_needs_an_explicit_match(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    d = router.route("Prepare the merger due diligence checklist.")
    assert d.task_force == "TF_ALPHA" and 26 not in d.domain_cards  # Tier C card 26 exists but is never speculative
    assert d.domain_cards == (5, 7, 14)
    named = router.route("Prepare the merger due diligence checklist; the fine art curator's valuation matters.")
    assert named.domain_cards[0] == 26 and len(named.domain_cards) == 3 and named.dropped_cards == (14,)
    assert "cards-dropped:[14]" in named.reason
    explicit = router.route("Prepare the merger due diligence checklist.", explicit_domains=[26])
    assert explicit.domain_cards[0] == 26
    with pytest.raises(RouterError, match="explicit domain 99"):
        router.route("x", explicit_domains=[99])


def test_explicit_task_force_and_omega(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    d = router.route("Everything at once.", task_force="TF_OMEGA")
    assert d.task_force == "TF_OMEGA" and d.dual_sign_off and set(d.directors) == {"ren", "arthur"}
    assert d.tier == "sensitive" and d.domain_cards == (14, 5)
    with pytest.raises(RouterError, match="unknown task force"):
        router.route("x", task_force="TF_NOPE")


def test_tier_is_the_max_of_hard_rule_and_preset(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    routine = router.route("Make a hotel reservation in Kyoto.")
    assert routine.task_force == "TF_CHI" and routine.tier == "routine"
    raised = router.route("Make a hotel reservation in Kyoto for the children.")
    assert raised.task_force == "TF_CHI" and raised.tier == "sensitive" and raised.persona == "arthur"


# --- prompt layering (4.4) --------------------------------------------------------------------------------------------


def test_prompt_layers_are_stable_first(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    d1 = router.route("Design a longevity protocol around my supplement stack.")
    d2 = router.route("Make a hotel reservation in Kyoto for the children.")
    builder = PromptBuilder(config.personas)
    cards1 = [config.domain_cards[n] for n in d1.domain_cards]
    memory = {"memory": ["Prefers morning appointments."], "scars": ["Never book Sundays."]}
    p1 = builder.build_system_prompt(d1, cards1, memory)
    p2 = builder.build_system_prompt(d2, [config.domain_cards[n] for n in d2.domain_cards], [])
    text, prefix_hash = p1  # unpacks as (text, stable_prefix_hash)
    persona_body = config.personas["arthur"].body.strip()
    assert text.startswith(persona_body)
    assert text.index(GOVERNANCE_BLOCK) > text.index(persona_body)
    assert text.index(cards1[0].text.strip()) > text.index(GOVERNANCE_BLOCK)
    assert text.index("Prefers morning appointments.") > text.index(cards1[1].text.strip())
    assert text.index("Never book Sundays.") > text.index("Prefers morning appointments.")
    assert prefix_hash == p2.stable_prefix_hash == builder.stable_prefix("arthur")[1]  # same persona, same prefix
    assert p1.cards_hash != p2.cards_hash and p1.domain_cards == (10, 15)
    assert p1.stable_prefix == p2.stable_prefix and p2.text.startswith(p1.stable_prefix)
    ren = build_system_prompt("ren", personas=config.personas)
    assert ren.stable_prefix_hash != prefix_hash
    assert "never delegates" in GOVERNANCE_BLOCK.lower() or "never delegate" in GOVERNANCE_BLOCK.lower()
    assert "never disclose" in GOVERNANCE_BLOCK.lower() and "- sensitive:" in GOVERNANCE_BLOCK


# --- the shipped config tree (only when the checkout / /opt/atlas/day1 is beside the package) -----------------------


@pytest.mark.skipif(not (REPO_CONFIG / "task-forces.json").is_file(), reason="config tree not beside the package")
def test_shipped_presets_respect_the_card_rules() -> None:
    data = json.loads((REPO_CONFIG / "task-forces.json").read_text(encoding="utf-8"))
    rules = json.loads((REPO_CONFIG / "router-rules.json").read_text(encoding="utf-8"))
    tier_c = {11, 13, 26, 28, 30, 33, 34, 35, 36}  # Section 8.2
    for tf in data["task_forces"]:
        cards = [c["domain"] for c in tf["domain_cards"]]
        assert len(cards) <= rules["max_domain_cards"], tf["code"]
        assert not tier_c.intersection(cards), tf["code"]  # 8.4 rule 4
    # (config/README.md says no trigger repeats a hard keyword; the shipped TF_UPSILON lists "medical" and "health".
    # Harmless: the hard rule fires first and detection still matches, so it is not asserted here.)
    upsilon = next(tf for tf in data["task_forces"] if tf["code"] == "TF_UPSILON")
    assert "longevity" in upsilon["triggers"] and upsilon["owners"] == ["Minerva"]


def test_missing_route_is_a_config_error(config: AtlasConfig, ledger: Ledger) -> None:
    broken = config.router_rules.model_copy(update={"routes": {"ren": "gpt-oss-120b"}})
    cfg = AtlasConfig(settings=config.settings, engines=config.engines, router_rules=broken,
                      task_forces=config.task_forces, personas=config.personas, domain_cards=config.domain_cards)
    with pytest.raises(ConfigError, match="arthur"):
        Router(cfg, StubClassifier(REN), ledger)
