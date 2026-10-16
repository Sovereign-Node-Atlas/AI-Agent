"""S52, the per-slot window: every generation the orchestrator sends to a llama-server is counted by the engine, trimmed
(oldest exchanges, whole, only as far as the bounded reply reserve needs) and clamped to one slot of the profile the
unit runs, or refused with status 413 (the HTTP status when not streamed, the error chunk's when streamed). The policy
over a plain counting function; the client over StubLlamaServer (the count route, the /apply-template + /tokenize
fallback with the image estimate, the server's own exceed-context error); the window read from the unit's env file;
the router's small window; the API's mapping and its reply reserve on real engine windows (a fitting chat with a huge
max_tokens, LightRAG's gleaning call on the router, a long chat on a 32k slot); and the two bypass guards: an AST
scan of atlas/ for any other
request to a generation endpoint, and a run of every generation path against a stub that refuses an uncounted
generation. No live service (CONVENTIONS.md §7.8)."""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import atlas
from atlas.api import AppDeps, build_app, engine_window, production_llama_client, production_streamer_for
from atlas.approval import ApprovalItem, ApprovalQueue, LlamaCrossChecker, StubNotifier, StubSender
from atlas.config import EngineSpec, load_config
from atlas.engines import (
    CONTEXT_MARGIN_TOKENS,
    COUNT_PATH,
    IMAGE_TOKENS_ESTIMATE,
    MIN_REPLY_TOKENS,
    ContextWindowExceeded,
    EngineError,
    LlamaClient,
    SlotWindow,
    fit_to_window,
    requested_max_tokens,
    window_for,
)
from atlas.ledger import Ledger
from atlas.personas import PersonaRegistry
from atlas.prompts import build_system_prompt
from atlas.router import ClassifierError, ClassifierVerdict, LlamaClassifier, Router, StubClassifier
from atlas.vault import SessionTags, VaultController
from stubs import FakeVaultRunner, StubLlamaServer, make_arbiter, stub_prompt_tokens

URL = "http://127.0.0.1:8101"
W = SlotWindow("gpt-oss-120b", 1000, "test")


def _m(role: str, tokens: int, tag: str = "") -> dict[str, Any]:
    """A message worth `tokens` under _count (one token per word); `tag` is its first word."""
    return {"role": role, "content": " ".join([tag or role] + ["w"] * (tokens - 1))}


def _words(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m["content"]).split()) if isinstance(m["content"], str)
               else sum(len(str(p.get("text", "")).split()) for p in m["content"]) for m in messages)


class _Count:
    """The engine's count, as fit_to_window sees it: one token per word plus `per_image` per image part."""

    def __init__(self, per_image: int = 0) -> None:
        self.per_image = per_image
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> int:
        self.calls.append(messages)
        images = sum(1 for m in messages if isinstance(m["content"], list)
                     for p in m["content"] if p.get("type") == "image_url")
        return _words(messages) + self.per_image * images


def _env(env_dir: Path, key: str, ctx: int, parallel: int, kv: str = "q8_0", coresident: int = 0) -> None:
    """The four lines of phase2/engine-env.py's <key>.env that atlas.arbiter.read_unit_profile reads."""
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / f"{key}.env").write_text(
        f"ATLAS_KV_TYPE='{kv}'\nATLAS_CTX_SIZE={ctx}\nATLAS_PARALLEL={parallel}\nATLAS_CORESIDENT={coresident}\n"
    )


def _sse(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]


# --- the policy (engines.fit_to_window) -------------------------------------------------------------------------------


def test_a_request_that_fits_is_sent_unchanged_after_one_count(caplog: pytest.LogCaptureFixture) -> None:
    msgs = [_m("system", 50), _m("user", 100), _m("assistant", 100), _m("user", 50)]
    count = _Count()
    with caplog.at_level(logging.INFO, logger="atlas.engines"):
        fit = fit_to_window(msgs, 200, W, count)
    assert fit.messages == msgs and fit.max_tokens == 200 and fit.prompt_tokens == fit.prompt_tokens_before == 300
    assert len(count.calls) == fit.counts == 1 and not fit.changed and fit.dropped_turns == 0
    assert [r for r in caplog.records if r.name == "atlas.engines"] == []  # nothing changed, nothing logged


def test_the_oldest_exchanges_go_whole_and_the_prefix_and_current_turn_stay(caplog: pytest.LogCaptureFixture) -> None:
    persona, cards = _m("system", 60, "PERSONA"), _m("system", 40, "CARDS")  # both leading system layers (4.4)
    ex = [[_m("user", 100, f"Q{i}"), _m("assistant", 100, f"A{i}")] for i in range(1, 5)]
    now = _m("user", 50, "NOW")
    msgs = [persona, cards, *[m for e in ex for m in e], now]  # 100 + 800 + 50 = 950 tokens
    count = _Count()
    with caplog.at_level(logging.INFO, logger="atlas.engines"):
        fit = fit_to_window(msgs, 300, W, count)
    # The reply asked for is under the reserve's cap (max(MIN_REPLY_TOKENS, 1000 // 4) = 512), so all 300 are reserved.
    # 950 + 300 + 32 > 1000. Dropping 1 exchange leaves 750 (1082: still too much), dropping 2 leaves 550 (882): the
    # fewest exchanges that make room for the reply reserve, oldest first, never half an exchange.
    assert fit.messages == [persona, cards, *ex[2], *ex[3], now]
    assert fit.dropped_turns == 2 and fit.dropped_messages == 4 and fit.prompt_tokens == 550 and fit.max_tokens == 300
    assert 750 + 300 + CONTEXT_MARGIN_TOKENS > W.tokens >= fit.prompt_tokens + 300 + CONTEXT_MARGIN_TOKENS
    assert fit.counts == len(count.calls) == 4  # the request, the floor (all dropped), two bisection steps
    (rec,) = [r for r in caplog.records if r.name == "atlas.engines"]
    line = rec.getMessage()
    assert rec.levelno == logging.WARNING and "engine=gpt-oss-120b window=1000 (test)" in line
    assert "prompt 950 -> 550 tokens, dropped 2 exchange(s) (4 message(s)), max_tokens 300 -> 300 (reply reserve 300)" \
        in line
    assert not any(tag in line for tag in ("PERSONA", "CARDS", "Q1", "A1", "NOW"))  # counts only, never content


def test_exchanges_keep_their_replies_and_the_current_turn_keeps_its_image() -> None:
    sysm = _m("system", 100, "SYS")
    greeting = _m("assistant", 50, "HELLO")  # a reply with no user message before it: an exchange of its own
    u1, a1 = _m("user", 100, "U1"), _m("assistant", 50, "A1")
    tool, a1b = _m("tool", 100, "T1"), _m("assistant", 50, "B1")
    u2, a2 = _m("user", 100, "U2"), _m("assistant", 100, "A2")
    image = {"role": "user", "content": [{"type": "text", "text": "IMG see attached"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
    text = _m("user", 30, "NOW")  # the Principal sent the image, then the question, as two user messages
    msgs = [sysm, greeting, u1, a1, tool, a1b, u2, a2, image, text]
    count = _Count(per_image=300)  # 683 words + 300 for the image = 983
    fit = fit_to_window(msgs, 100, W, count)
    # The tool result goes with the reply that called it; the image message is part of the current turn.
    assert fit.messages == [sysm, u2, a2, image, text] and fit.dropped_turns == 2 and fit.dropped_messages == 5
    tight = fit_to_window(msgs, 100, SlotWindow("glm-5.3-flash", 600, "test"), _Count(per_image=300))
    assert tight.messages == [sysm, image, text] and tight.dropped_turns == 3  # every exchange went, the image did not
    # Dropping the image just sent would make [SYS, NOW] fit a 400-token slot; it is never dropped: the request fails.
    with pytest.raises(ContextWindowExceeded, match="alone are 433 tokens after dropping all 3 earlier exchange"):
        fit_to_window(msgs, 100, SlotWindow("glm-5.3-flash", 400, "test"), _Count(per_image=300))


def test_max_tokens_is_clamped_so_the_reply_never_reaches_the_slot_end() -> None:
    msgs = [_m("system", 100), _m("user", 300)]
    fit = fit_to_window(msgs, 1000, W, _Count())
    assert fit.max_tokens == W.tokens - CONTEXT_MARGIN_TOKENS - 400 == 568 and fit.changed and fit.dropped_turns == 0
    assert fit.prompt_tokens + fit.max_tokens + CONTEXT_MARGIN_TOKENS == W.tokens
    unbounded = fit_to_window(msgs, None, W, _Count())  # no max_tokens: the remaining room, never the server's -1
    assert unbounded.max_tokens == 568 and unbounded.requested_max_tokens is None
    # max_tokens is a ceiling, never a claim on history: a chat that leaves the reply reserve (min(max_tokens,
    # max(MIN_REPLY_TOKENS, window // 4)) = 512 here) keeps every exchange and only the reply is clamped, however large
    # the max_tokens (an Open WebUI per-model "Max Tokens" at or above the window used to cost it all of its history).
    fitting = [_m("system", 100), _m("user", 100, "NAME"), _m("assistant", 100), _m("user", 100, "WHO")]
    for asked in (W.tokens, 32768):
        fit = fit_to_window(fitting, asked, W, _Count())
        assert fit.messages == fitting and fit.dropped_turns == 0 and fit.max_tokens == 1000 - 32 - 400 == 568
    # A chat that does not leave the reserve loses its oldest exchanges until it does (800 + 512 + 32 > 1000, so the
    # one exchange goes), and the clamp takes what is left (at least min(max_tokens, MIN_REPLY_TOKENS)).
    long = [_m("system", 100), _m("user", 200), _m("assistant", 200), _m("user", 300)]
    fit = fit_to_window(long, 1000, W, _Count())
    assert fit.messages == [long[0], long[3]] and fit.max_tokens == 568 and 568 >= MIN_REPLY_TOKENS
    # When even the prefix and the current turn cannot take the reserve (a 4096-token slot reserves 1024 of a
    # 4096 reply), every earlier exchange goes, though both small ones would fit beside a 512-token reply, and the
    # reply is clamped to the room: 4096 - 32 - 3200 = 864, between the minimum reply and the reserve.
    small = SlotWindow("embed-qwen3-4b", 4096, "test")  # a 4096-token slot, as on the retrieval pair
    big = [_m("system", 200), _m("user", 25), _m("assistant", 25), _m("user", 25), _m("assistant", 25),
           _m("user", 3000)]
    fit = fit_to_window(big, 4096, small, _Count())
    assert fit.messages == [big[0], big[5]] and fit.dropped_turns == 2 and fit.max_tokens == 864


@pytest.mark.parametrize(("tokens", "asked", "reserve"), [
    (32768, 4096, 4096),  # a 32k slot keeps the pipeline's default reply (atlas.api.DEFAULT_MAX_TOKENS) whole
    (32768, 32768, 8192),  # a max_tokens at the window reserves a quarter of the slot, not the slot
    (8192, 4096, 2048),  # the router's slot: LightRAG's gleaning call (no max_tokens, so 4096) keeps its input text
    (4096, 4096, 1024),  # a 4096-token slot
    (1000, 1000, MIN_REPLY_TOKENS),  # a quarter of a small slot is under the minimum reply: the minimum
    (32768, 200, 200),  # a reply smaller than the cap (the classifier's verdict) is reserved as asked
    (32768, None, MIN_REPLY_TOKENS),  # no max_tokens at all: the minimum, then the whole remaining room
])
def test_history_goes_only_as_far_as_the_bounded_reply_reserve_needs(
    tokens: int, asked: int | None, reserve: int
) -> None:
    window = SlotWindow("e", tokens, "test")
    ex = [[_m("user", 50, f"Q{i}"), _m("assistant", 50, f"A{i}")] for i in range(tokens // 100)]
    msgs = [_m("system", 100, "SYS"), *[m for e in ex for m in e], _m("user", 50, "NOW")]  # tokens + 150: over
    fit = fit_to_window(msgs, asked, window, _Count())
    room = tokens - CONTEXT_MARGIN_TOKENS - fit.prompt_tokens
    assert fit.messages[0] == msgs[0] and fit.messages[-1] == msgs[-1] and fit.dropped_turns > 0
    # The reserve fits, and one exchange fewer dropped (100 tokens more) would not leave it: the fewest went.
    assert room >= reserve > room - 100
    assert fit.max_tokens == (room if asked is None else min(asked, room))


@pytest.mark.parametrize(("tokens", "asked", "reserve"), [
    (32768, 32768, 8192),  # the cap: a quarter of a 32k slot
    (8192, 4096, 2048),  # the router's slot
    (4096, 4096, 1024),  # a 4096-token slot
    (1000, 300, 300),  # under the cap: the reply asked for
])
def test_the_reserve_boundary_is_exact(tokens: int, asked: int, reserve: int, caplog: pytest.LogCaptureFixture) -> None:
    """S52 fix round: prompt + reserve + margin == window keeps every exchange; one token more drops the oldest."""
    window = SlotWindow("e", tokens, "test")
    last = tokens - CONTEXT_MARGIN_TOKENS - reserve - 300  # system 100 + one exchange 200 + the current turn
    msgs = [_m("system", 100), _m("user", 100), _m("assistant", 100), _m("user", last)]
    fit = fit_to_window(msgs, asked, window, _Count())
    assert fit.messages == msgs and fit.dropped_turns == 0 and fit.max_tokens == min(asked, reserve)
    over = [*msgs[:3], _m("user", last + 1)]
    with caplog.at_level(logging.INFO, logger="atlas.engines"):
        fit = fit_to_window(over, asked, window, _Count())
    assert fit.messages == [over[0], over[3]] and fit.dropped_turns == 1
    assert f"(reply reserve {reserve})" in caplog.text


def test_a_current_turn_that_cannot_fit_fails_clearly() -> None:
    msgs = [_m("system", 100), _m("user", 200), _m("assistant", 200), _m("user", 900)]
    with pytest.raises(ContextWindowExceeded) as info:
        fit_to_window(msgs, 300, W, _Count())
    exc = info.value
    assert exc.engine == "gpt-oss-120b" and exc.window == 1000 and exc.prompt_tokens == 1000
    msg = str(exc)
    assert "per-slot context window of 1000 tokens (test)" in msg and "alone are 1000 tokens" in msg
    assert "after dropping all 1 earlier exchange(s)" in msg and "shorten it or use document retrieval" in msg
    assert isinstance(exc, EngineError)  # the classifier and the cross-checker treat it as any engine fault
    near = [_m("system", 100), _m("user", 700)]  # 168 tokens of room
    with pytest.raises(ContextWindowExceeded, match=r"leaving 168 for the reply \(minimum 300, margin 32\)"):
        fit_to_window(near, 300, W, _Count())
    assert fit_to_window(near, 150, W, _Count()).max_tokens == 150  # a reply that small still fits


def test_requested_max_tokens_reads_every_alias() -> None:
    assert requested_max_tokens({"max_tokens": 64}) == 64
    assert requested_max_tokens({"max_tokens": 4096, "n_predict": 100}) == 100  # n_predict wins at the pin
    assert requested_max_tokens({"max_completion_tokens": 50, "max_tokens": 80}) == 50
    assert requested_max_tokens({"n_predict": -1}) is None and requested_max_tokens({}) is None
    assert requested_max_tokens({"max_tokens": True}) is None


# --- the client (engines.LlamaClient) ---------------------------------------------------------------------------------


def _exchanges(n: int, words: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        out += [{"role": "user", "content": f"question {i} " + "x " * words},
                {"role": "assistant", "content": f"answer {i} " + "y " * words}]
    return out


def test_the_client_counts_with_the_engine_and_sends_the_fitted_body() -> None:
    server = StubLlamaServer("OK")
    client = LlamaClient(URL, transport=server.transport(), window=SlotWindow("gpt-oss-120b", 400, "test"))
    history = [{"role": "system", "content": "be brief"}, *_exchanges(6, 40), {"role": "user", "content": "last one"}]
    r = client.chat(history, max_tokens=150, response_format={"type": "json_object"}, n_predict=100)
    assert r.text == "OK" and server.unguarded == []
    (gen,) = server.generations
    assert gen["max_tokens"] == 100 and "n_predict" not in gen  # the smallest limit asked for, as max_tokens
    assert gen["messages"][0] == history[0] and gen["messages"][-1] == history[-1]
    assert gen["messages"][1]["content"].startswith("question ") and len(gen["messages"]) < len(history)
    assert stub_prompt_tokens(gen) + gen["max_tokens"] + CONTEXT_MARGIN_TOKENS <= 400
    assert all(c["response_format"] == {"type": "json_object"} for c in server.counts)  # the count sees the same body
    assert client.last_fit is not None and client.last_fit.dropped_turns > 0
    chunks = list(client.chat_chunks(history, max_tokens=100))  # the streaming path is the same path
    assert server.unguarded == [] and len(server.generations) == 2
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_image_tokens_are_counted_by_the_engine_or_charged_the_documented_estimate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = {"messages": [
        {"role": "system", "content": "read drawings"},
        {"role": "user", "content": [{"type": "text", "text": "what is this"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
    ]}
    pinned = StubLlamaServer(per_image=1234)
    client = LlamaClient(URL, transport=pinned.transport(), window=SlotWindow("glm-5.3-flash", 32768, "test"))
    assert client.count_prompt_tokens(body) == stub_prompt_tokens(body, per_image=1234)  # image tokens included
    assert [p for p, _ in pinned.requests] == [COUNT_PATH]  # one round trip
    older = StubLlamaServer(count_route=False)  # a build without the count route: /apply-template + /tokenize
    client = LlamaClient(URL, transport=older.transport(), window=SlotWindow("glm-5.3-flash", 32768, "test"))
    with caplog.at_level(logging.WARNING, logger="atlas.engines"):
        n = client.count_prompt_tokens(body)
        client.count_prompt_tokens(body)
    # "<system> read drawings <user> what is this <__media__>" is 8 tokens; the pair does not see the image, so the
    # glm5v ceiling of the v0.4.0 vision engine (8,000 image tokens + <|begin_of_image|>/<|end_of_image|>) is charged
    # per image part.
    assert IMAGE_TOKENS_ESTIMATE == 8000 + 2 and n == 8 + IMAGE_TOKENS_ESTIMATE
    assert [p for p, _ in older.requests[:3]] == [COUNT_PATH, "/apply-template", "/tokenize"]
    assert older.requests[2][1]["add_special"] is True and older.requests[2][1]["parse_special"] is True
    assert len([r for r in caplog.records if "answered 404" in r.getMessage()]) == 1  # said once, not per request


def test_the_servers_own_exceed_context_error_becomes_context_window_exceeded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COUNT_PATH:
            return httpx.Response(200, json={"input_tokens": 10})
        return httpx.Response(400, json={"error": {
            "code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 9000, "n_ctx": 8192,
            "message": "request (9000 tokens) exceeds the available context size (8192 tokens), try increasing it"}})

    client = LlamaClient(URL, transport=httpx.MockTransport(handler),
                         window=SlotWindow("router-qwen3.5-4b", 32768, "a stale env file"))
    for call in (lambda: client.chat([{"role": "user", "content": "x"}], max_tokens=10),
                 lambda: list(client.chat_chunks([{"role": "user", "content": "x"}], max_tokens=10))):
        with pytest.raises(ContextWindowExceeded) as info:
            call()
        assert info.value.window == 8192 and info.value.prompt_tokens == 9000
        assert "expected 32768: a stale env file" in str(info.value)
        assert not re.search(r"\bHTTP 4\d\d\b", str(info.value))  # router._HTTP_4XX_RE must not retry it


def test_a_client_without_a_window_refuses_to_generate(engines: dict[str, EngineSpec]) -> None:
    server = StubLlamaServer()
    bare = LlamaClient(URL, transport=server.transport())  # an embedding/rerank client: no window
    with pytest.raises(EngineError, match="no per-slot window"):
        bare.chat([{"role": "user", "content": "x"}])
    assert server.requests == []
    assert LlamaClient.for_engine(engines["router-qwen3.5-4b"]).window().tokens == 8192  # engines.json by default


# --- the window: the profile the unit runs (api.engine_window) --------------------------------------------------------


def test_the_window_is_the_profile_the_unit_runs(engines: dict[str, EngineSpec], tmp_path: Path) -> None:
    arbiter, _controller, _probe = make_arbiter(engines)
    # No env file: engines.json, its smallest per-slot figure.
    assert engine_window(arbiter, engines["gpt-oss-120b"]).tokens == 32768
    assert engine_window(arbiter, engines["router-qwen3.5-4b"]).tokens == 8192
    assert engine_window(arbiter, engines["deepseek-v4-flash"]).tokens == 131072  # one 128K slot (Day 1 default)
    vision = window_for(engines["glm-5.3-flash"])  # the vision engine: GLM-5.3-Flash with its glm5v mmproj
    assert vision.tokens == 32768 and "no unit env file" in vision.source
    arbiter.engines_env_dir = tmp_path
    _env(tmp_path, "glm-5.3-flash", 65536, 2, coresident=1)  # an env file from before S-22: the line changes nothing
    w = engine_window(arbiter, engines["glm-5.3-flash"])
    assert w.tokens == 32768 and "glm-5.3-flash.env" in w.source and "x 2 slots" in w.source
    _env(tmp_path, "glm-5.3-flash", 49152, 2)  # whatever is rendered is the window, never a fixed figure
    assert engine_window(arbiter, engines["glm-5.3-flash"]).tokens == 24576
    _env(tmp_path, "deepseek-v4-flash", 16384, 1, kv="f16")  # a smaller rendered window is the window
    assert engine_window(arbiter, engines["deepseek-v4-flash"]).tokens == 16384
    _env(tmp_path, "router-qwen3.5-4b", 8192, 4)  # resident: outside the Arbiter's budget, read for the window anyway
    assert engine_window(arbiter, engines["router-qwen3.5-4b"]).tokens == 2048


def test_the_router_classifier_keeps_to_its_small_window(engines: dict[str, EngineSpec], tmp_path: Path) -> None:
    arbiter, _controller, _probe = make_arbiter(engines)
    arbiter.engines_env_dir = tmp_path
    _env(tmp_path, "router-qwen3.5-4b", 8192, 4)  # 2048 tokens per slot
    server = StubLlamaServer('{"hemisphere": "estate", "persona": "arthur"}')
    client = production_llama_client(arbiter, engines["router-qwen3.5-4b"], transport=server.transport())
    clf = LlamaClassifier(client)
    assert clf.classify("bloods are back").hemisphere == "estate"
    (gen,) = server.generations
    assert gen["max_tokens"] == 200 and stub_prompt_tokens(gen) + 200 + CONTEXT_MARGIN_TOKENS <= 2048
    assert client.last_fit is not None and client.last_fit.window.tokens == 2048
    with pytest.raises(ClassifierError, match="unreachable or failed") as info:
        clf.classify(" ".join(["word"] * 3000))  # ~2400 words after the head/tail clip: over 2048 with the prompt
    assert isinstance(info.value.__cause__, ContextWindowExceeded) and "2048 tokens" in str(info.value)
    assert len(server.generations) == 1 and server.unguarded == []  # the over-long verdict call was never sent


# --- the API ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(config_dir: Path, tmp_path: Path) -> dict[str, Any]:
    """The orchestrator with the PRODUCTION streamer factory over a StubLlamaServer and the unit env files in tmp."""
    config = load_config()
    config.router_rules = config.router_rules.model_copy(
        update={"overrides": {**config.router_rules.overrides, "[DEEP THINK:QUICK]": "deep-think:quick"}}
    )
    ledger = Ledger(":memory:")
    ledger.init_db()
    arbiter, _controller, _probe = make_arbiter(config.engines, ledger=ledger)
    arbiter.engines_env_dir = tmp_path / "engines"
    server = StubLlamaServer()
    personas = PersonaRegistry(config.personas, config.engines)
    router = Router(config, StubClassifier(lambda text: ClassifierVerdict(hemisphere="corporate", persona="ren")),
                    ledger, personas=personas)
    deps = AppDeps(
        config=config,
        ledger=ledger,
        arbiter=arbiter,
        router=router,
        build_system_prompt=lambda d, cards=(), memory=None: build_system_prompt(d, cards, memory, personas=personas),
        streamer_for=production_streamer_for(arbiter, transport=server.transport()),
        vault=VaultController(helper="/usr/local/bin/atlas-vault", mount_dir=str(tmp_path / "open"),
                              runner=FakeVaultRunner()),
        sessions=SessionTags(tmp_path / "sessions.json"),
        approval=ApprovalQueue(ledger, StubSender(), StubNotifier(), personas=personas),
        load_wait_s=5.0,
        generation_wait_s=5.0,
        trusted_hosts=frozenset({"127.0.0.1", "::1", "testclient"}),
    )
    return {"client": TestClient(build_app(deps)), "server": server, "ledger": ledger, "arbiter": arbiter,
            "config": config, "personas": personas, "env": tmp_path / "engines"}


def test_the_api_answers_413_for_a_request_that_cannot_fit(api: dict[str, Any]) -> None:
    _env(api["env"], "glm-5.3-flash", 2048, 1, kv="f16")  # a 2048-token slot for Ren's engine
    client: TestClient = api["client"]
    huge = [{"role": "user", "content": " ".join(["clause"] * 3000)}]
    r = client.post("/v1/chat/completions", json={"model": "ren", "messages": huge})
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["status"] == 413 and err["type"] == "context_window_exceeded"
    assert "glm-5.3-flash" in err["message"] and "window of 2048 tokens" in err["message"]
    assert "glm-5.3-flash.env" in err["message"] and "shorten it or use document retrieval" in err["message"]
    assert api["server"].generations == []  # nothing was sent
    row = api["ledger"].get_task(err["task_id"])
    assert row["status"] == "failed" and "ContextWindowExceeded" in row["error"]
    assert api["ledger"].list_strikes(task_id=err["task_id"]) == []  # no 9.4 strike: nothing was generated
    with client.stream("POST", "/v1/chat/completions", json={"model": "ren", "messages": huge, "stream": True}) as s:
        assert s.status_code == 200  # a stream is answered before the prompt is counted: the 413 is the chunk's
        body = "".join(s.iter_text())
    chunk = next(c for c in _sse(body) if "error" in c)
    assert chunk["error"]["status"] == 413 and chunk["error"]["type"] == "context_window_exceeded"
    assert chunk["choices"][0]["delta"]["content"].startswith("\n[ATLAS] glm-5.3-flash: ")
    r = client.post("/internal/v1/chat/completions", json={"model": "glm-5.3-flash", "messages": huge})
    assert r.status_code == 413 and r.json()["error"]["type"] == "context_window_exceeded"
    assert api["server"].generations == [] and api["arbiter"].generating is None


def test_a_long_chat_is_trimmed_at_exchange_boundaries_and_still_answers(
    api: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    _env(api["env"], "glm-5.3-flash", 8192, 1, kv="f16")
    history = [*_exchanges(30, 300), {"role": "user", "content": "and finally, the summary please"}]
    with caplog.at_level(logging.INFO, logger="atlas.engines"):
        r = api["client"].post("/v1/chat/completions", json={"model": "ren", "messages": history, "max_tokens": 500})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Stub answer from the engine."
    (gen,) = api["server"].generations
    assert gen["messages"][0]["role"] == "system" and "Ren" in gen["messages"][0]["content"]  # the 4.4 prefix stays
    assert gen["messages"][-1]["content"] == "and finally, the summary please" and gen["max_tokens"] == 500
    kept = [m["content"].split()[1] for m in gen["messages"][1:-1:2]]
    assert all(m["content"].startswith("question ") for m in gen["messages"][1:-1:2])  # whole exchanges, in order
    assert 0 < len(kept) < 30 and kept == [str(i) for i in range(30 - len(kept), 30)]  # the newest ones
    assert stub_prompt_tokens(gen) + 500 + CONTEXT_MARGIN_TOKENS <= 8192
    (rec,) = [r for r in caplog.records if r.name == "atlas.engines"]
    assert "engine=glm-5.3-flash window=8192 (glm-5.3-flash.env" in rec.getMessage()


def test_a_fitting_chat_keeps_its_history_whatever_max_tokens_it_asks_for(api: dict[str, Any]) -> None:
    """Open WebUI with a per-model "Max Tokens" at or above the slot: the reply is clamped, the history stays."""
    _env(api["env"], "gpt-oss-120b", 262144, 8, kv="q4_0")  # Ren's engine: 32k x 8 slots
    chat = [{"role": "user", "content": "My name is Ada."}, {"role": "assistant", "content": "Noted, Ada."},
            {"role": "user", "content": "What is my name?"}]
    for asked in (32768, 65536):
        r = api["client"].post("/v1/chat/completions", json={"model": "ren", "messages": chat, "max_tokens": asked})
        assert r.status_code == 200, r.text
    assert len(api["server"].generations) == 2
    for gen in api["server"].generations:
        assert [m["content"] for m in gen["messages"][1:]] == [m["content"] for m in chat]  # the exchange stayed
        assert gen["max_tokens"] == 32768 - CONTEXT_MARGIN_TOKENS - stub_prompt_tokens(gen)


def test_lightrags_gleaning_call_reaches_the_router_whole(api: dict[str, Any]) -> None:
    """LightRAG 1.5.7's entity-extraction gleaning on the router (an 8192-token slot; no max_tokens, so the pipeline's
    4096): the extraction prompt carrying the chunk and the first extraction ride as history before the "continue"
    prompt. They leave the router's 2048-token reply reserve, so they are sent whole and only the reply is clamped;
    reserving the whole 4096 would drop the input text the router is asked to glean from."""
    _env(api["env"], "router-qwen3.5-4b", 32768, 4)  # 8192 tokens per slot
    gleaning = [_m("system", 1700, "EXTRACT"), _m("user", 1500, "CHUNK"), _m("assistant", 1500, "ENTITIES"),
                _m("user", 500, "CONTINUE")]
    r = api["client"].post("/internal/v1/chat/completions", json={"model": "router-qwen3.5-4b", "messages": gleaning})
    assert r.status_code == 200, r.text
    (gen,) = api["server"].generations
    assert [(m["role"], m["content"]) for m in gen["messages"]] == [(m["role"], m["content"]) for m in gleaning]
    prompt = stub_prompt_tokens(gen)
    assert prompt == 3 + 4 * 4 + 5200 and prompt + 4096 + CONTEXT_MARGIN_TOKENS > 8192  # 4096 held back would drop
    assert gen["max_tokens"] == 8192 - CONTEXT_MARGIN_TOKENS - prompt == 2941


def test_a_long_chat_on_a_32k_slot_still_gets_the_default_reply(api: dict[str, Any]) -> None:
    """No max_tokens on a 32k slot: the pipeline's 4096 is under the reserve's cap (a quarter of the slot, 8192), so it
    is reserved whole: the oldest exchanges go until it fits, and the reply is never squeezed toward the minimum."""
    _env(api["env"], "gpt-oss-120b", 262144, 8, kv="q4_0")
    history = [*_exchanges(40, 400), {"role": "user", "content": "and the summary please"}]
    r = api["client"].post("/v1/chat/completions", json={"model": "ren", "messages": history})
    assert r.status_code == 200, r.text
    (gen,) = api["server"].generations
    assert gen["max_tokens"] == 4096 and gen["messages"][-1]["content"] == "and the summary please"
    assert 0 < (len(gen["messages"]) - 2) // 2 < 40  # some exchanges went, some stayed
    prompt, exchange = stub_prompt_tokens(gen), 2 * (4 + 2 + 400)  # one more _exchanges(.., 400) exchange: 812
    assert prompt + 4096 + CONTEXT_MARGIN_TOKENS <= 32768 < prompt + exchange + 4096 + CONTEXT_MARGIN_TOKENS


# --- the bypass guards ------------------------------------------------------------------------------------------------


def test_every_generation_path_goes_through_the_window_guard(api: dict[str, Any]) -> None:
    """The public route (both shapes), /internal (weight-bearing and resident; the Celery tasks, LightRAG and the Phase
    3 load test use it), inline Deep Think, the router's classifier and the approval cross-checker, each against a stub
    llama-server that refuses a generation whose messages were not counted first or that carries no max_tokens."""
    client: TestClient = api["client"]
    server: StubLlamaServer = api["server"]
    user = [{"role": "user", "content": "Plan the quarter."}]
    r = client.post("/v1/chat/completions", json={"model": "atlas", "messages": user})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Stub answer from the engine."
    with client.stream("POST", "/v1/chat/completions", json={"model": "atlas", "messages": user, "stream": True}) as r:
        assert r.status_code == 200 and "Stub" in "".join(r.iter_text())
    for engine in ("gpt-oss-120b", "router-qwen3.5-4b"):
        r = client.post("/internal/v1/chat/completions", json={"model": engine, "messages": user})
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"].startswith("Stub"), r.text
    deep = [{"role": "user", "content": "[DEEP THINK:QUICK] best exit structure?"}]
    r = client.post("/v1/chat/completions", json={"model": "atlas", "messages": deep})
    assert r.status_code == 200 and r.json()["atlas"]["command"] == "deep-think"
    through_api = len(server.generations)
    assert through_api >= 2 + 2 + 4  # deep think quick: several calls on one engine
    config, arbiter = api["config"], api["arbiter"]
    server.text = '{"hemisphere": "corporate", "persona": "ren"}'
    clf = LlamaClassifier(production_llama_client(arbiter, config.engines["router-qwen3.5-4b"],
                                                  transport=server.transport()))
    assert clf.classify("Plan the quarter.").hemisphere == "corporate"
    server.text = '{"verdict": "agree", "notes": ""}'
    checker = LlamaCrossChecker(
        api["personas"],
        lambda key: production_llama_client(arbiter, config.engines[key], transport=server.transport()),
        ledger=api["ledger"],
    )
    queue = ApprovalQueue(api["ledger"], StubSender(), StubNotifier(), personas=api["personas"], cross_checker=checker)
    item = queue.submit(ApprovalItem(tier="sensitive", persona="gideon", recipient="counsel@example.com",
                                     body="Our position on clause 12.", kind="email", reason="Clause 12 is uncapped."))
    assert item.cross_check is not None and item.cross_check.verdict == "agree"
    assert len(server.generations) == through_api + 2
    assert server.unguarded == []
    assert all(isinstance(g["max_tokens"], int) for g in server.generations)


SRC = Path(atlas.__file__).parent
# A request path that names a generation endpoint: llama-server's (/v1/chat/completions, /completion, /infill, ...)
# or the orchestrator's own /internal route, which leads back into the guarded pipeline.
_GEN_PATH = re.compile(r"^(?:/[^\s/]+)*/(?:chat/completions|completions?|infill|responses|messages)(?:/[^\s]*)?$")
EXPECTED_SITES = {
    ("engines.py", "<module>", "/v1/chat/completions"),  # CHAT_PATH
    ("engines.py", "<module>", "/v1/chat/completions/input_tokens"),  # COUNT_PATH (counting, not generating)
    ("engines.py", "LlamaClient._open_chat", "CHAT_PATH"),  # THE choke point
    ("engines.py", "LlamaClient.count_prompt_tokens", "COUNT_PATH"),
    ("engines.py", "LlamaClient._count_by_template", "COUNT_PATH"),  # its log line
    ("api.py", "build_app", "/v1/chat/completions"),  # the orchestrator's own routes (@app.post)
    ("api.py", "build_app", "/internal/v1/chat/completions"),
    ("tasks/__init__.py", "OrchestratorClient.generate", "/internal/v1/chat/completions"),  # back into the pipeline
}


class _Sites(ast.NodeVisitor):
    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel, self.stack, self.sites, self.open_chat = rel, [], set(), set()
        self.docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                           if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                           and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}

    def _scope(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _def(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for d in node.decorator_list:  # decorators run in the enclosing scope (the @app.post routes)
            self.visit(d)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _def

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docstrings and _GEN_PATH.match(node.value):
            self.sites.add((self.rel, self._scope(), node.value))

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in ("CHAT_PATH", "COUNT_PATH") and isinstance(node.ctx, ast.Load):
            self.sites.add((self.rel, self._scope(), node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ("_open_chat", "CHAT_PATH"):
            self.open_chat.add((self.rel, self._scope(), node.attr))
        self.generic_visit(node)


def _scan() -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]]]:
    sites: set[tuple[str, str, str]] = set()
    calls: set[tuple[str, str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        v = _Sites(path.relative_to(SRC).as_posix(), tree)
        v.visit(tree)
        sites |= v.sites
        calls |= v.open_chat
    return sites, calls


def test_no_module_posts_to_a_generation_endpoint_but_the_choke_point() -> None:
    """A new request to a generation endpoint anywhere in atlas/, or the choke point losing its site, fails here: the
    answer is to send it through engines.LlamaClient (chat / chat_chunks), never to widen EXPECTED_SITES lightly."""
    sites, calls = _scan()
    assert sites == EXPECTED_SITES
    assert calls == {("engines.py", "LlamaClient.chat", "_open_chat"),
                     ("engines.py", "LlamaClient.chat_chunks", "_open_chat")}


def test_production_wiring_hands_every_generation_the_units_window() -> None:
    """build_production_deps cannot run in a test (systemd, sysfs); its wiring is checked in the source instead: the
    streamer factory and the classifier's client are the engine_window ones, and LightRAG's LLM calls go to the
    orchestrator's /internal route (the guarded pipeline), never to a llama-server port."""
    api_tree = ast.parse((SRC / "api.py").read_text(encoding="utf-8"))
    (build,) = [n for n in ast.walk(api_tree) if isinstance(n, ast.FunctionDef) and n.name == "build_production_deps"]
    calls = [n for n in ast.walk(build) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    (deps,) = [c for c in calls if c.func.id == "AppDeps"]
    streamer = next(k.value for k in deps.keywords if k.arg == "streamer_for")
    assert isinstance(streamer, ast.Call) and getattr(streamer.func, "id", None) == "production_streamer_for"
    (clf,) = [c for c in calls if c.func.id == "LlamaClassifier"]
    assert isinstance(clf.args[0], ast.Call) and getattr(clf.args[0].func, "id", None) == "production_llama_client"
    mem_tree = ast.parse((SRC / "memory.py").read_text(encoding="utf-8"))
    bases = [k.value for n in ast.walk(mem_tree) if isinstance(n, ast.Call) for k in n.keywords
             if k.arg == "llm_base_url"]
    assert bases and all(isinstance(b, ast.JoinedStr) and isinstance(b.values[-1], ast.Constant)
                         and b.values[-1].value == "/internal/v1" for b in bases)
