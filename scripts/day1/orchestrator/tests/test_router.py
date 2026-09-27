"""V16: the 4-Way Router (Sections 7.1, 7.2, 8.2, 8.3, 8.4, 8.5, 9.1, 4.4, 16.2; CONVENTIONS.md §6 Phase 2 gate).

Claim proved (verify/v16-router-hard-rule.sh): a message containing "medical" routes to Arthur even when the
classifier says Ren, and the decision is logged with the hard-rule reason. Also: the hard rule overrules a typed
[REN*] override and both facts are logged (7.2 rule 1); task-force detection picks TF_UPSILON for a longevity-protocol
request with <= 3 cards and dispatches on the owning director's engine (8.5); a Tier C card is not loaded without an
explicit match and never displaces a preset card (8.4); the tier is never routine on an abliterated engine (R13), is
sensitive on a security-adjacent card (8.2), a sensitive privacy tag or a payment word (16.2); the prompt layers (4.4)
keep a stable prefix hash per dispatch persona and keep the other hemisphere's memory out (7.3).

No live service: the classifier is StubClassifier, the ledger is Ledger(':memory:'), and the config tree is built in
a temp dir from tests/fixtures/config (the fixtures carry every persona and engine; the task forces and the cards
needed here are written by `make_config`). ATLAS_CONFIG_DIR/CONFIG_DIR are ignored by `make_config` on purpose so the
hermetic tests are the same on the node and in a checkout; only the shipped-tree test at the end reads them.

The family names in the fixture settings are invented placeholders: the Principal's real names live only in the
node's /etc/atlas/atlas.env (CONVENTIONS.md §3, §7.2) and never in the repository.
"""

from __future__ import annotations

import json
import os
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
    SENSITIVE_DOMAINS,
    ClassifierError,
    ClassifierVerdict,
    LlamaClassifier,
    OverrideSyntaxError,
    Router,
    RouterError,
    StubClassifier,
    card_explicitly_named,
    detect_task_force,
    find_hard_keywords,
    find_sensitive_keywords,
    parse_override,
)

FIXTURES = Path(__file__).parent / "fixtures" / "config"
# Invented placeholder family names (never the Principal's; those live only in /etc/atlas/atlas.env).
FAMILY_NAMES = ("Testwood", "Placeholdername")


def _shipped_config_dir() -> Path | None:
    """The shipped config tree (the 23 presets of Section 8.3), wherever this test runs.

    /etc/atlas/orchestrator.env exports ATLAS_CONFIG_DIR=/opt/atlas/day1/config on the node (phase2/02-orchestrator.sh,
    which installs the package at /opt/atlas/orchestrator, not beside the config); a checkout has it at
    scripts/day1/config beside the package; CONFIG_DIR is the test-only override. A candidate that is not the shipped
    tree (the fixtures, say) is passed over: the shipped tree is the one that carries TF_UPSILON.
    """
    candidates = [
        os.environ.get("ATLAS_CONFIG_DIR"),
        os.environ.get("CONFIG_DIR"),
        str(Path(__file__).resolve().parents[2] / "config"),
        str(Path(os.environ.get("ATLAS_OPT", "/opt/atlas")) / "day1" / "config"),
    ]
    for c in candidates:
        if not c:
            continue
        path = Path(c) / "task-forces.json"
        if not path.is_file():
            continue
        try:
            codes = {tf.get("code") for tf in json.loads(path.read_text(encoding="utf-8")).get("task_forces", [])}
        except (OSError, ValueError):
            continue
        if "TF_UPSILON" in codes:
            return Path(c)
    return None


REPO_CONFIG = _shipped_config_dir()

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
    {"code": "TF_EPSILON", "name": "Venture & Seed Investments", "group": "CORP-CAPITAL", "hemisphere": "corporate",
     "owners": ["Silas", "Gideon"], "default_tier": "sensitive",
     "domain_cards": [{"domain": 23}, {"domain": 21}, {"domain": 6}],
     "triggers": ["venture", "term sheet", "cap table"]},
    {"code": "TF_KAPPA", "name": "Contract & Vendor Negotiation", "group": "CORP-CONTRACTS", "hemisphere": "corporate",
     "owners": ["Gideon", "Ren"], "default_tier": "standard",
     "domain_cards": [{"domain": 5}], "triggers": ["contract", "vendor", "negotiation"]},
    {"code": "TF_NU", "name": "Hardware & Cryptographic Security", "group": "CORP-INFRA", "hemisphere": "corporate",
     "owners": ["Valerie"], "default_tier": "standard",
     "domain_cards": [{"domain": 31}, {"domain": 32}],
     "triggers": ["tpm", "secure boot", "firmware", "encryption"]},
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

# Extra cards the presets above need (H1 in the CONVENTIONS.md §8 form; bodies are test stubs).
EXTRA_CARDS = {
    "10-chief-medical-officer-risk-underwriter.md":
        "# 10. Chief Medical Officer & Risk Underwriter  (Estate, Minerva, Tier A)\n\n**Frame:** Test card 10.\n",
    "15-chief-longevity-officer-performance-physiologist.md":
        "# 15. Chief Longevity Officer & Performance Physiologist "
        "(incl. bioinformatics, genomics and drug discovery)  (Estate, Minerva, Tier A)\n\n**Frame:** Test card 15.\n",
    "21-chief-investment-officer-quant-strategist.md":
        "# 21. Chief Investment Officer & Quant Strategist (incl. financial fraud and risk modelling)  "
        "(Corporate, Silas, Tier A)\n\n**Frame:** Test card 21.\n",
    "23-venture-partner-private-equity-director.md":
        "# 23. Venture Partner & Private Equity Director  (Corporate, Silas with Gideon, Tier B)\n\n"
        "**Frame:** Test card 23.\n",
    "31-embedded-systems-mobile-security.md":
        "# 31. Embedded Systems & Mobile Security (firmware, bare-metal OS internals, mobile platforms)  "
        "(Corporate, Valerie, Tier B)\n\n**Frame:** Test card 31.\n",
    "32-communications-telecom-security.md":
        "# 32. Communications & Telecom Security (cryptographic protocols, network infrastructure, signals)  "
        "(Corporate, Valerie with Alaric, Tier B)\n\n**Frame:** Test card 32.\n",
    "33-automotive-vehicle-systems-engineering.md":
        "# 33. Automotive & Vehicle Systems Engineering  (Corporate, Valerie, Tier C)\n\n**Frame:** Test card 33.\n",
}

REN = ClassifierVerdict(hemisphere="corporate", persona="ren")
ARTHUR = ClassifierVerdict(hemisphere="estate", persona="arthur")


def make_config(tmp_path: Path) -> AtlasConfig:
    cfg = tmp_path / "config"
    shutil.copytree(FIXTURES, cfg)
    (cfg / "router-rules.json").write_text(json.dumps(ROUTER_RULES), encoding="utf-8")
    (cfg / "task-forces.json").write_text(json.dumps(TASK_FORCES), encoding="utf-8")
    cards = cfg / "domains" / "cards"
    for filename, text in EXTRA_CARDS.items():
        (cards / filename).write_text(text, encoding="utf-8")
    settings = Settings(config_dir=cfg, etc_dir=tmp_path, db_path=tmp_path / "ledger.sqlite3",
                        engines_env_dir=tmp_path, llama_port_base=8100, family_names=FAMILY_NAMES)
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
    assert d.engine == "nemotron-3-super"  # the synthesising lead's engine
    assert d.hard_keyword_hits == ("medical",)
    assert "hard-rule:medical" in d.reason
    assert d.classifier_route == "ren"  # the disagreement is on the record
    assert "medical" in d.privacy_tags
    assert d.tier == "sensitive" and "tier:sensitive(hard-rule)" in d.reason
    # "medical" is also a TF_UPSILON trigger: the dispatch is Minerva's session on her engine (8.5), Arthur speaks.
    assert d.task_force == "TF_UPSILON" and d.dispatch_persona == "minerva" and d.dispatch_engine == "gpt-oss-120b"
    rows = ledger.list_routing_decisions("t1")
    assert len(rows) == 1 and rows[0]["id"] == d.ledger_id
    row = rows[0]
    assert row["route"] == "arthur" and row["engine"] == d.dispatch_engine  # the engine the Arbiter loads
    assert row["hard_keyword_hit"] == "medical" and row["classifier_route"] == "ren"
    assert "hard-rule:medical" in row["reason"] and row["tier"] == "sensitive"
    assert row["message_sha256"] == d.message_sha256 and len(row["message_sha256"]) == 64
    assert "clinic" not in row["reason"]  # the ledger stores the hash, never the message (7.2 rule 5)


def test_family_name_from_atlas_env_is_a_hard_rule(config: AtlasConfig, ledger: Ledger) -> None:
    assert "Testwood" in config.router_rules.hard_keywords
    assert "FAMILY_NAMES_PLACEHOLDER" not in config.router_rules.hard_keywords
    d = make_router(config, ledger, REN).route("Book a table for Testwood on Friday.")
    assert d.persona == "arthur" and "hard-rule:Testwood" in d.reason


def test_hard_keywords_match_whole_words_only() -> None:
    kws = ["will", "estate", "Testwood"]
    assert find_hard_keywords("The William Street lease is ready.", kws) == []
    assert find_hard_keywords("Update the will and the estate plan for testwood.", kws) == ["will", "estate",
                                                                                            "Testwood"]
    assert find_hard_keywords("Real-estate agents called.", kws) == ["estate"]


# --- overrides (7.2 rule 4) and the hard rule over them (7.2 rule 1) --------------------------------------------------


def test_hard_rule_overrules_a_ren_override_and_logs_both(config: AtlasConfig, ledger: Ledger) -> None:
    # 7.2 rule 1: "a hit routes to Arthur regardless of anything else"; Appendix A orders hard rules first.
    d = make_router(config, ledger, ARTHUR).route("[REN] Draft the press line on the medical device launch.")
    assert d.persona == "arthur" and d.route == "arthur" and d.engine == "nemotron-3-super"
    assert d.override == "[REN]" and d.override_action == "ren"  # the override stays on the record
    assert d.reason.startswith("override:[REN]") and "hard-rule:medical" in d.reason  # both facts on the record
    assert "overruled-by-hard-rule:medical(7.2 rule 1)" in d.reason
    assert d.body.startswith("Draft the press line")
    row = ledger.list_routing_decisions()[0]
    assert row["override"] == "[REN]" and row["route"] == "arthur" and "overruled-by-hard-rule" in row["reason"]
    # An [ARTHUR:*] override on a hit is unaffected; a [REN] override with no hit is honoured.
    long = make_router(config, ledger, REN).route("[ARTHUR:LONG] Summarise the medical file.")
    assert long.route == "arthur-qwen" and long.engine == "qwen3.5-122b" and "overruled" not in long.reason
    plain = make_router(config, ledger, ARTHUR).route("[REN] Draft the press line on the device launch.")
    assert plain.persona == "ren" and plain.engine == "gpt-oss-120b"


def test_override_variants_pick_their_engines(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    assert router.route("[ARTHUR:LONG] read this").engine == "qwen3.5-122b"
    assert router.route("[ARTHUR:UNCENSORED] read this").engine == "gpt-oss-120b-abliterated"
    assert router.route("[REN:UNCENSORED] read this").engine == "gpt-oss-120b-abliterated"
    vault = router.route("[VAULT]")
    assert vault.command == "vault-session" and vault.persona == "arthur"


def test_abliterated_engine_is_never_routine_tier(config: AtlasConfig, ledger: Ledger) -> None:
    # 6.1 C6 addendum / R13: "Abliterated output always passes the same gate, never routine tier."
    d = make_router(config, ledger, ARTHUR).route("[REN:UNCENSORED] Make a hotel reservation in Kyoto.")
    assert d.task_force == "TF_CHI" and d.route == "ren-abliterated" and d.engine == "gpt-oss-120b-abliterated"
    assert d.tier == "standard" and "tier:standard(abliterated, R13)" in d.reason
    plain = make_router(config, ledger, ARTHUR).route("Make a hotel reservation in Kyoto.")
    assert plain.task_force == "TF_CHI" and plain.tier == "routine"  # the same preset without the engine


def test_overrides_are_ignored_on_text_the_principal_did_not_type(config: AtlasConfig, ledger: Ledger) -> None:
    # A stored transcript or an inbound document that begins with a prefix is text, not an order (7.2 rule 4).
    router = make_router(config, ledger, ARTHUR)
    d = router.route("[REN:UNCENSORED] Make a hotel reservation in Kyoto.", allow_overrides=False)
    assert d.override is None and d.route == "arthur" and d.engine == "nemotron-3-super"
    assert "override-ignored:[REN:UNCENSORED]" in d.reason and d.body.startswith("[REN:UNCENSORED]")
    vault = router.route("[VAULT] open it", allow_overrides=False)
    assert vault.command is None and "override-ignored:[VAULT]" in vault.reason


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


def test_classifier_task_weight_triggers_deep_think(config: AtlasConfig, ledger: Ledger) -> None:
    # 9.1: "Trigger: [DEEP THINK: problem] or the router's task-weight estimate. Eleanor's classifier picks a depth."
    weighed = Router(config, StubClassifier(ClassifierVerdict(hemisphere="corporate", deep_think_depth="standard")),
                     ledger).route("Should we enter the Singapore market?")
    assert weighed.command == "deep-think" and weighed.deep_think_depth == "standard"
    assert "deep-think:standard(classifier task-weight, 9.1)" in weighed.reason
    assert weighed.engine == "gpt-oss-120b" and weighed.override is None
    apex = Router(config, StubClassifier(ClassifierVerdict(hemisphere="corporate", deep_think_depth="deep")),
                  ledger).route("Should we enter the Singapore market?")
    assert apex.command == "deep-think" and apex.engine == "deepseek-v4-flash"
    assert make_router(config, ledger, REN).route("Should we enter the Singapore market?").command is None


def test_parse_override_longest_key_wins() -> None:
    ov = ROUTER_RULES["overrides"]
    assert parse_override("[DEEP THINK:DEEP] x", ov).action == "deep-think:deep"
    assert parse_override("[DEEP THINK: x] y", ov).payload == "x"
    assert parse_override("[ren] lowercase is not an override", ov) is None
    assert parse_override("no prefix", ov) is None


# --- classifier (7.2 rule 2) ------------------------------------------------------------------------------------------


def test_classifier_decides_what_the_keywords_miss(config: AtlasConfig, ledger: Ledger) -> None:
    message = "My mother's company needs a new supplier agreement."
    plain = make_router(config, ledger, REN).route(message)
    assert plain.persona == "ren" and plain.engine == "gpt-oss-120b" and plain.hard_keyword_hits == ()
    assert "classifier:corporate" in plain.reason and plain.tier == "standard"
    # A privacy tag keeps the corporate route (the classifier prompt says so) but makes the matter sensitive (16.2).
    tagged = ClassifierVerdict(hemisphere="corporate", persona="ren", privacy_tags=("family",))
    d = make_router(config, ledger, tagged).route(message)
    assert d.persona == "ren" and d.hard_keyword_hits == () and d.privacy_tags == ("family",)
    assert "classifier:corporate(tags family)" in d.reason
    assert d.tier == "sensitive" and "tier:sensitive(privacy-tags family, 16.2)" in d.reason


def test_classifier_failure_is_loud_by_default_and_defensive_on_opt_in(config: AtlasConfig, ledger: Ledger) -> None:
    # 4.1 keeps the 4B router model resident; a dead classifier is an outage (a 5xx and a strike), not a routed answer.
    with pytest.raises(RouterError, match="no route"):
        Router(config, StubClassifier(fail=ClassifierError("503 loading")), ledger).route("Plan the quarter.")
    degraded = Router(config, StubClassifier(fail=ClassifierError("503 loading")), ledger,
                      on_classifier_error="arthur")
    d = degraded.route("Plan the quarter.", task_id="deg")
    assert d.persona == "arthur" and "classifier-unavailable(ClassifierError):default-arthur" in d.reason
    assert "503 loading" not in d.reason  # the category, never the exception text, reaches the ledger
    assert "503 loading" not in ledger.list_routing_decisions("deg")[0]["reason"]
    with pytest.raises(ValueError, match="on_classifier_error"):
        Router(config, StubClassifier(REN), ledger, on_classifier_error="bogus")


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

    with pytest.raises(ClassifierError, match="no JSON object") as info:
        LlamaClassifier(_fake_llama(prose)).classify("x")
    assert "I think this is for Ren" not in str(info.value)  # model text never rides in an exception message

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"hemisphere": "estate", }'}}]})

    with pytest.raises(ClassifierError, match="invalid JSON") as info2:
        LlamaClassifier(_fake_llama(broken)).classify("x")
    assert "estate" not in str(info2.value)


def test_long_document_moves_arthur_to_qwen(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ClassifierVerdict(hemisphere="estate", persona="arthur", long_document=True))
    d = router.route("Read the attached family trust deed and summarise it.")
    assert d.route == "arthur-qwen" and d.engine == "qwen3.5-122b" and d.long_document
    big = "word " * (ROUTER_RULES["long_document_tokens"] * 4 // 5 + 10)
    by_size = make_router(config, ledger, ARTHUR).route("Summarise the estate ledger: " + big)
    assert by_size.route == "arthur-qwen"


# --- task forces, dispatch and domain cards (7.2 rule 3, 8.3, 8.4, 8.5) ----------------------------------------------


def test_longevity_request_picks_tf_upsilon_and_dispatches_on_the_director(config: AtlasConfig,
                                                                             ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    d = router.route("Design a longevity protocol around my supplement stack.", task_id="u")
    assert d.task_force == "TF_UPSILON" and d.directors == ("minerva",)
    assert d.domain_cards == (10, 15) and len(d.domain_cards) <= config.router_rules.max_domain_cards
    assert d.dropped_cards == ()
    assert d.tier == "sensitive" and "task-force:TF_UPSILON" in d.reason
    # 8.5: the director is the sole inference session; step 4 loads the director's engine (6.2: Minerva on
    # gpt-oss-120b). Arthur stays the synthesising lead who speaks (16.1 rule 1, 8.5 step 7).
    assert d.dispatch_persona == "minerva" and d.dispatch_engine == "gpt-oss-120b"
    assert d.persona == "arthur" and d.engine == "nemotron-3-super"
    assert "dispatch:minerva@gpt-oss-120b(8.5 step 4)" in d.reason
    row = ledger.list_routing_decisions("u")[0]
    assert row["task_force"] == "TF_UPSILON" and row["engine"] == "gpt-oss-120b"
    assert d.to_dict()["dispatch_persona"] == "minerva"
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
    # A named Tier C card queues after the preset's own cards: the preset never loses a card to an inference.
    named = router.route("Prepare the merger due diligence checklist; the fine art curator's valuation matters.")
    assert named.domain_cards == (5, 7, 14) and named.dropped_cards == (26,)
    assert "cards-dropped:[26]" in named.reason
    explicit = router.route("Prepare the merger due diligence checklist.", explicit_domains=[26])
    assert explicit.domain_cards == (26, 5, 7) and explicit.dropped_cards == (14,)
    with pytest.raises(RouterError, match="explicit domain 99"):
        router.route("x", explicit_domains=[99])


def test_single_word_of_a_tier_c_name_is_not_an_explicit_match(config: AtlasConfig, ledger: Ledger) -> None:
    # 8.4 rule 4: "automotive" in an ordinary sentence does not load card 33; the preset keeps its General Counsel.
    router = make_router(config, ledger, REN)
    d = router.route("The automotive supplier wants a term sheet.")
    assert d.task_force == "TF_EPSILON" and d.domain_cards == (23, 21, 6) and d.dropped_cards == ()
    assert d.dispatch_persona == "silas" and d.directors == ("silas", "gideon")
    card33 = config.domain_cards[33]
    assert card33.is_tier_c
    assert not card_explicitly_named("The automotive supplier wants a term sheet.", card33)
    assert card_explicitly_named("Brief on vehicle systems engineering for the bid.", card33)
    assert card_explicitly_named("Load domain 33 for this one.", card33)
    named = router.route("The vehicle systems engineering startup sent a term sheet.")
    assert named.domain_cards == (23, 21, 6) and named.dropped_cards == (33,)


def test_explicit_task_force_and_omega(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, REN)
    d = router.route("Everything at once.", task_force="TF_OMEGA")
    assert d.task_force == "TF_OMEGA" and d.dual_sign_off and set(d.directors) == {"ren", "arthur"}
    assert d.tier == "sensitive" and d.domain_cards == (14, 5)
    # 6.1 "Apex escalation: DeepSeek V4 Flash, on ... TF_OMEGA"; the preset's `engine` is the one loaded.
    assert d.engine == "deepseek-v4-flash" and d.dispatch_engine == "deepseek-v4-flash"
    assert d.dispatch_persona == "ren" and "engine:deepseek-v4-flash(apex, 6.1 TF_OMEGA)" in d.reason
    assert ledger.list_routing_decisions()[0]["engine"] == "deepseek-v4-flash"
    with pytest.raises(RouterError, match="unknown task force"):
        router.route("x", task_force="TF_NOPE")


def test_tier_is_the_max_of_preset_hard_rule_and_lexicon(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    routine = router.route("Make a hotel reservation in Kyoto.")
    assert routine.task_force == "TF_CHI" and routine.tier == "routine" and routine.sensitive_hits == ()
    raised = router.route("Make a hotel reservation in Kyoto for the children.")
    assert raised.task_force == "TF_CHI" and raised.tier == "sensitive" and raised.persona == "arthur"
    # 16.2 "any payment": a payment word on a routine preset is sensitive (16.3 rule 1: money never moves alone).
    paid = router.route("Make a hotel reservation in Kyoto and pay the deposit today.")
    assert paid.task_force == "TF_CHI" and paid.tier == "sensitive"
    assert {"pay", "deposit"} <= set(paid.sensitive_hits) and "lexicon" in paid.reason and "16.2" in paid.reason
    assert find_sensitive_keywords("Wire the invoices to the bank account.", router.sensitive_keywords) == [
        "invoice", "wire", "bank account"]
    assert find_sensitive_keywords("Run the payrolls on Friday.", ("payroll",)) == ["payroll"]  # plural-tolerant
    assert find_sensitive_keywords("The prepayroll checks are done.", ("payroll",)) == []  # whole tokens only


def test_security_adjacent_cards_make_the_dispatch_sensitive(config: AtlasConfig, ledger: Ledger) -> None:
    # 8.2 posture: domains 31, 32 and 34 inherit domain 1's framing and "the approval gate treats anything they
    # produce as sensitive tier", whatever the preset's default says (TF_NU is standard).
    assert SENSITIVE_DOMAINS == frozenset({31, 32, 34})
    router = make_router(config, ledger, REN)
    d = router.route("Set up secure boot and the firmware update path with the TPM.")
    assert d.task_force == "TF_NU" and d.domain_cards == (31, 32) and d.sensitive_hits == ()
    assert d.tier == "sensitive" and "tier:sensitive(domain 31/32, 8.2 posture)" in d.reason
    assert d.dispatch_persona == "valerie"
    explicit = router.route("Plan the quarter.", explicit_domains=[31])
    assert explicit.task_force is None and explicit.domain_cards == (31,) and explicit.tier == "sensitive"


# --- prompt layering (4.4) and memory isolation (7.3) ----------------------------------------------------------------


def test_prompt_layers_are_stable_first(config: AtlasConfig, ledger: Ledger) -> None:
    router = make_router(config, ledger, ARTHUR)
    d1 = router.route("Design a longevity protocol around my supplement stack.")  # TF_UPSILON: Minerva's dispatch
    builder = PromptBuilder(config.personas)
    cards1 = [config.domain_cards[n] for n in d1.domain_cards]
    memory = {"memory": ["Prefers morning appointments."], "scars": ["Never book Sundays."]}
    p1 = builder.build_system_prompt(d1, cards1, memory)
    p2 = builder.build_system_prompt(d1, cards1[:1], [])  # same dispatch persona, other layers changed
    text, prefix_hash = p1  # unpacks as (text, stable_prefix_hash)
    # 8.4 / 8.5 step 3: the cards are compiled into the owning director's prompt, not the lead's.
    persona_body = config.personas["minerva"].body.strip()
    assert text.startswith(persona_body) and p1.persona == "minerva"
    assert text.index(GOVERNANCE_BLOCK) > text.index(persona_body)
    assert text.index(cards1[0].text.strip()) > text.index(GOVERNANCE_BLOCK)
    assert text.index("Prefers morning appointments.") > text.index(cards1[1].text.strip())
    assert text.index("Never book Sundays.") > text.index("Prefers morning appointments.")
    assert prefix_hash == p2.stable_prefix_hash == builder.stable_prefix("minerva")[1]  # same persona, same prefix
    assert p1.cards_hash != p2.cards_hash and p1.domain_cards == (10, 15)
    assert p1.stable_prefix == p2.stable_prefix and p2.text.startswith(p1.stable_prefix)
    # Outside a task force the lead's own core is layer 1; another director's dispatch has its own prefix.
    d3 = router.route("Update the plan for the estate.")
    p3 = builder.build_system_prompt(d3, [], None)
    assert d3.task_force is None and p3.persona == "arthur"
    assert p3.text.startswith(config.personas["arthur"].body.strip())
    assert p3.stable_prefix_hash == builder.stable_prefix("arthur")[1] != prefix_hash
    d4 = router.route("Make a hotel reservation in Kyoto for the children.")  # TF_CHI: Victor's dispatch
    assert builder.build_system_prompt(d4, [], None).stable_prefix_hash == builder.stable_prefix("victor")[1]
    ren = build_system_prompt("ren", personas=config.personas)
    assert ren.stable_prefix_hash != prefix_hash
    assert "never delegates" in GOVERNANCE_BLOCK.lower() or "never delegate" in GOVERNANCE_BLOCK.lower()
    assert "never disclose" in GOVERNANCE_BLOCK.lower() and "- sensitive:" in GOVERNANCE_BLOCK


def test_memory_layer_is_hemisphere_isolated(config: AtlasConfig, ledger: Ledger) -> None:
    # 7.3: isolation "enforced by separate memory collections, separate context windows"; the builder is the last
    # component that knows which hemisphere speaks and never lets the other one in (and never drops it silently).
    builder = PromptBuilder(config.personas)
    corporate = make_router(config, ledger, REN).route("Plan the quarter.")
    assert corporate.hemisphere == "corporate"
    ok = builder.build_system_prompt(corporate, [], {"corporate": ["Q3 target is 12%."], "documents_corporate": ["MSA"],
                                                      "sentinel": ["CVE note"], "scars": ["s1"], "estate": []})
    assert "Q3 target is 12%." in ok.text and "MSA" in ok.text and "CVE note" in ok.text and "s1" in ok.text
    with pytest.raises(ValueError, match="estate"):
        builder.build_system_prompt(corporate, [], {"corporate": ["x"], "estate": ["The trust deed says..."]})
    with pytest.raises(ValueError, match="documents_estate"):
        builder.build_system_prompt("ren", [], {"documents_estate": ["deed.pdf"]})
    with pytest.raises(ValueError, match="not admitted"):
        builder.build_system_prompt(corporate, [], {"somewhere_else": ["x"]})
    estate = make_router(config, ledger, ARTHUR).route("Update the plan for the estate.")
    p = builder.build_system_prompt(estate, [], {"estate": ["The trust deed says..."], "corporate": []})
    assert "The trust deed says..." in p.text
    with pytest.raises(ValueError, match="corporate"):
        builder.build_system_prompt(estate, [], {"corporate": ["Q3 target"]})


# --- the shipped config tree (ATLAS_CONFIG_DIR on the node, scripts/day1/config in a checkout) ----------------------


@pytest.mark.skipif(REPO_CONFIG is None, reason="the shipped config tree was not found (ATLAS_CONFIG_DIR, "
                                                "CONFIG_DIR, scripts/day1/config, $ATLAS_OPT/day1/config)")
def test_shipped_presets_respect_the_card_rules() -> None:
    assert REPO_CONFIG is not None
    data = json.loads((REPO_CONFIG / "task-forces.json").read_text(encoding="utf-8"))
    rules = json.loads((REPO_CONFIG / "router-rules.json").read_text(encoding="utf-8"))
    tier_c = {11, 13, 26, 28, 30, 33, 34, 35, 36}  # Section 8.2
    assert len(data["task_forces"]) == 23  # Section 8.3
    for tf in data["task_forces"]:
        cards = [c["domain"] for c in tf["domain_cards"]]
        assert len(cards) <= rules["max_domain_cards"], tf["code"]
        assert not tier_c.intersection(cards), tf["code"]  # 8.4 rule 4
    # (config/README.md says no trigger repeats a hard keyword; the shipped TF_UPSILON lists "medical" and "health".
    # Harmless: the hard rule fires first and detection still matches, so it is not asserted here.)
    upsilon = next(tf for tf in data["task_forces"] if tf["code"] == "TF_UPSILON")
    assert "longevity" in upsilon["triggers"] and upsilon["owners"] == ["Minerva"]
    omega = next(tf for tf in data["task_forces"] if tf["code"] == "TF_OMEGA")
    assert omega.get("engine") == "deepseek-v4-flash" and omega.get("trigger") == "principal-only"  # 6.1, 8.3
    assert "FAMILY_NAMES_PLACEHOLDER" in rules["hard_keywords"]  # the real names never sit in the tree


def test_missing_route_is_a_config_error(config: AtlasConfig, ledger: Ledger) -> None:
    broken = config.router_rules.model_copy(update={"routes": {"ren": "gpt-oss-120b"}})
    cfg = AtlasConfig(settings=config.settings, engines=config.engines, router_rules=broken,
                      task_forces=config.task_forces, personas=config.personas, domain_cards=config.domain_cards)
    with pytest.raises(ConfigError, match="arthur"):
        Router(cfg, StubClassifier(REN), ledger)
