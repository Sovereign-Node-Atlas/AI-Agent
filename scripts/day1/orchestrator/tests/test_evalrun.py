"""atlas.evalrun: the phased evaluation runner for D-49 (seat test sets, T-27) and D-46 (the V4.1 trial, T-30).

TEST FIXTURES ONLY. Every item below is synthetic: the prompts, references and canary say so, and none is a real task,
a real matter or a real source. The engines are stub llama-servers behind httpx.MockTransport (CONVENTIONS.md §7.8):
no network, no real model. The workbook tests need openpyxl (the package's [eval] extra) and are skipped without it.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
import random
import re
import shutil
import stat
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

import atlas
from atlas import admin
from atlas import evalrun as ev
from atlas.config import EngineSpec, load_domain_cards, load_personas
from atlas.engines import CHAT_PATH
from atlas.ledger import Ledger
from stubs import counting, make_arbiter

UID = os.geteuid()
GLM, DEEPSEEK, GPT_OSS, TRIAL = ev.GLM, ev.DEEPSEEK, ev.GPT_OSS, ev.TRIAL_KEY
CANARY = "ATLAS-EVAL-TEST-FIXTURE-CANARY-synthetic-not-a-real-task-0001"
DISPLAY = {GLM: "GLM-5.3-Flash", DEEPSEEK: "DeepSeek-V4-Flash", GPT_OSS: "gpt-oss-120b", TRIAL: "DeepSeek-V4.1-Flash"}


# --- synthetic fixture items ------------------------------------------------------------------------------------------


def seat_item(n: int, **over: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": f"fx-law-{n:02d}", "set": ev.SEAT_SET, "version": "fixture-1", "canary": CANARY,
        "domain": "au-law", "seat": "gideon", "task_force": None, "cards": [6], "category": "APPLY",
        "source_kind": "public", "drafted_by": "principal", "drafted_where": "node",
        "checked_by": "fixture-checker", "as_at": "2026-10-17",
        "prompt": f"TEST FIXTURE item fx-law-{n:02d}: a synthetic question with no real content.",
        "context": [{"title": "Synthetic extract", "text": "TEST FIXTURE context, invented.",
                     "source_url": "https://example.invalid/fixture", "compilation": "fixture-c1"}],
        "reference": {"answer": "TEST FIXTURE reference answer.",
                      "must_include": [{"point": "fixture point", "cite": "fixture s 1 (compilation fixture-c1)"}],
                      "fatal": ["fixture fatal error"]},
        "objective": {"type": "none", "answer": None, "tolerance": None},
        "scoring": "5-3-2+fatal", "holdout": False,
    }
    item.update(over)
    return item


TRIAL_OBJECTIVES: dict[str, dict[str, Any]] = {
    "A": {"type": "none", "answer": None, "tolerance": None},
    "B": {"type": "flaws", "answer": [{"id": "f1", "keywords": ["fixture-flaw-one"]}], "tolerance": None},
    "C": {"type": "none", "answer": None, "tolerance": None},
    "D": {"type": "exact", "answer": ["fixture-fact-alpha"], "tolerance": None},
    "E": {"type": "numeric", "answer": 42, "tolerance": 0.5},
}


def trial_item(cat: str, n: int = 1, **over: Any) -> dict[str, Any]:
    item = seat_item(n, id=f"fx-trial-{cat}{n}", set=ev.TRIAL_SET, domain="fixture-trial", seat="arthur",
                     task_force="TF_OMEGA", cards=[14], category=cat,
                     source_kind="private" if cat in ("A", "C") else "public",
                     prompt=f"TEST FIXTURE item fx-trial-{cat}{n}: synthetic, category {cat}.",
                     objective=copy.deepcopy(TRIAL_OBJECTIVES[cat]), scoring="pairwise+deep-think-rubric")
    item.update(over)
    return item


def private_dir(base: Path, name: str = "sets") -> Path:
    d = base / name
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def write_set(directory: Path, items: list[dict[str, Any]], name: str = "set.jsonl", mode: int = 0o600) -> Path:
    path = directory / name
    path.write_text("".join(json.dumps(it) + "\n" for it in items), encoding="utf-8")
    os.chmod(path, mode)
    return path


# --- stub engines -----------------------------------------------------------------------------------------------------


def sse(text: str, finish: str) -> httpx.Response:
    events = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "stub reasoning, never kept"},
                      "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
         "timings": {"prompt_n": 10, "predicted_n": 20, "predicted_per_second": 12.5}},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class FakeEngines:
    """One stub llama-server per engine key. An answer carries QUALITY/FATAL/SKEW/GARBLE/BIAS markers that the stub
    judges read back, so a test sets each judge's verdict through the answer it writes."""

    def __init__(self, marks: dict[tuple[str, str], dict[str, Any]] | None = None,
                 finish: dict[str, Callable[[str], str]] | None = None) -> None:
        self.marks = marks or {}
        self.finish = finish or {}
        self.bodies: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def transport_for(self, key: str) -> httpx.MockTransport:
        return httpx.MockTransport(counting(lambda request: self._handle(key, request)))

    def _handle(self, key: str, request: httpx.Request) -> httpx.Response:
        assert request.url.path == CHAT_PATH
        body = json.loads(request.content)
        self.bodies[key].append(body)
        system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        if "neutral grader" in system:
            return sse(self._grade(key, user), "stop")
        if "neutral judge" in system:
            return sse(self._verdict(key, user), "stop")
        task = re.search(r"item (fx-[\w-]+):", user).group(1)  # type: ignore[union-attr]
        m = {"quality": 8, "fatal": 0, "skew": 0, **self.marks.get((key, task), {})}
        text = (f"<think>hidden chain of {key}</think>As {DISPLAY[key]}, here is a TEST FIXTURE answer. "
                f"QUALITY={m['quality']} FATAL={m['fatal']} SKEW={m['skew']}"
                + (" GARBLE" if m.get("garble") else "") + (" BIAS" if m.get("bias") else "")
                + "\n* first point\n+ second point\n1) numbered\n" + m.get("extra", ""))
        fin = self.finish.get(key)
        return sse(text, fin(task) if fin else "stop")

    @staticmethod
    def _marks(section: str) -> tuple[int, int, int]:
        q = re.search(r"QUALITY=(\d+)", section)
        f = re.search(r"FATAL=(\d)", section)
        s = re.search(r"SKEW=(\d+)", section)
        return int(q.group(1)) if q else 0, int(f.group(1)) if f else 0, int(s.group(1)) if s else 0

    def _grade(self, judge: str, user: str) -> str:
        answer = user.split("=== Answer to grade ===", 1)[1]
        if "GARBLE" in answer:
            return "I would rather not give a score."
        q, f, skew = self._marks(answer)
        total = max(0, min(10, q - (skew if judge == GPT_OSS else 0)))
        s = min(5, total)
        so = min(3, total - s)
        return json.dumps({"substance": s, "sources": so, "clarity": total - s - so, "fatal": bool(f),
                           "fatal_reason": "fixture"})

    def _verdict(self, judge: str, user: str) -> str:
        a1 = user.split("=== Answer 1 ===", 1)[1].split("=== Answer 2 ===", 1)[0]
        a2 = user.split("=== Answer 2 ===", 1)[1]
        (q1, f1, _), (q2, f2, _) = self._marks(a1), self._marks(a2)
        if judge == GLM and ("BIAS" in a1 or "BIAS" in a2):
            better = "1"  # position bias: the two orders disagree, a split
        else:
            better = "1" if q1 > q2 else "2" if q2 > q1 else "tie"
        return json.dumps({"better": better, "fatal_1": bool(f1), "fatal_2": bool(f2), "reason": "fixture"})


# --- helpers ----------------------------------------------------------------------------------------------------------


def seat_prompts(config_dir: Path, engines: dict[str, EngineSpec]) -> ev.SeatPrompts:
    return ev.SeatPrompts(load_personas(config_dir, engines), load_domain_cards(config_dir))


def freeze_items(tmp_path: Path, items: list[dict[str, Any]], run_id: str = "fx-run") -> tuple[ev.Run, Path]:
    path = write_set(private_dir(tmp_path), items)
    db = tmp_path / "ledger" / "atlas.sqlite3"
    run = ev.freeze(path, eval_root=tmp_path / "evalroot", db=db, owner_uid=UID, run_id=run_id,
                    rng=random.Random(7))
    return run, db


def no_say(_: str) -> None:
    return None


SEAT_MARKS = {
    (GLM, "fx-law-02"): {"skew": 2},  # judges 2 apart: not escalated
    (DEEPSEEK, "fx-law-03"): {"skew": 3},  # judges 3 apart: escalated
    (GPT_OSS, "fx-law-04"): {"fatal": 1},  # FATAL from both judges: escalated
    (GLM, "fx-law-05"): {"garble": True},  # the judges' replies do not parse: escalated after one retry
}


def seat_run(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec], *, ledger: Ledger | None = None,
             marks: dict[tuple[str, str], dict[str, Any]] | None = None,
             items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = items or [seat_item(n) for n in range(1, 6)]
    run, db = freeze_items(tmp_path, items)
    fake = FakeEngines(SEAT_MARKS if marks is None else marks, finish={DEEPSEEK: lambda task: "length"})
    arb, controller, _ = make_arbiter(engines, ledger=ledger)
    gate = ev.ArbiterGate(arb, load_wait_s=0.0)
    client_for = ev.client_factory(engines, env_dir=None, transport_for=fake.transport_for)
    summary = ev.run_answers(run, gate, client_for, seat_prompts(config_dir, engines), say=no_say)
    ev.run_judges(run, gate, client_for, say=no_say)
    return {"run": run, "db": db, "fake": fake, "controller": controller, "summary": summary, "arbiter": arb}


# --- 1. schema --------------------------------------------------------------------------------------------------------


def test_a_valid_seat_set_loads_and_the_plan_report_is_advisory(tmp_path: Path) -> None:
    items = [seat_item(1), seat_item(2, category="TRAP", holdout=True, source_kind="private")]
    evset = ev.load_set(write_set(private_dir(tmp_path), items))
    assert (evset.kind, evset.version, len(evset.items)) == (ev.SEAT_SET, "fixture-1", 2)
    report = ev.plan_report(evset)
    assert any("au-law (wave 1)" in line for line in report)  # 2 items against the approved 16: said, not refused
    assert sum(ev.D49_PLAN[d][1][i] for d in ev.D49_PLAN for i in range(5)) == 100
    assert [sum(ev.D49_PLAN[d][1][i] for d in ev.D49_PLAN) for i in range(5)] == [35, 15, 23, 16, 11]
    assert sum(ev.D46_PLAN.values()) == 40


BAD_ITEMS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("unknown field", lambda it: it.update(extra=1), "unknown field"),
    ("missing field", lambda it: it.pop("holdout"), "missing field"),
    ("category", lambda it: it.update(category="GUESS"), "category"),
    ("four cards", lambda it: it.update(cards=[1, 2, 3, 4]), "cards"),
    ("card 40", lambda it: it.update(cards=[40]), "cards"),
    ("date", lambda it: it.update(as_at="17/10/2026"), "as_at"),
    ("seat", lambda it: it.update(seat="nobody"), "seat"),
    ("domain", lambda it: it.update(domain="astrology"), "D-49 domain"),
    ("canary", lambda it: it.update(canary="short"), "canary"),
    ("no points", lambda it: it["reference"].update(must_include=[]), "must_include is empty"),
    ("no cite", lambda it: it["reference"].update(must_include=[{"point": "p", "cite": ""}]), "pinpoint"),
    ("numeric key", lambda it: it.update(objective={"type": "numeric", "answer": "x", "tolerance": 1}), "numeric"),
    ("scoring", lambda it: it.update(scoring="pairwise+deep-think-rubric"), "scoring must be"),
    ("holdout", lambda it: it.update(holdout="no"), "holdout"),
    ("private off-node", lambda it: it.update(source_kind="private", drafted_where="offnode"),
     "private item drafted off the node is refused"),
    ("drafter by name", lambda it: it.update(drafted_by="GLM-5.3-Flash"), "drafted_by 'GLM-5.3-Flash' must be one of"),
    ("drafter short name", lambda it: it.update(drafted_by="glm"), "the own-draft rule compares it with the engine"),
    ("engine drafting off the node", lambda it: it.update(drafted_by=GLM, drafted_where="offnode"),
     "must be one of ['offnode-session'] for drafted_where 'offnode'"),
    ("form feed in the context", lambda it: it["context"][0].update(text="page one\x0cpage two"),
     "context[0].text holds a control character (U+000C)"),
    ("escape in the reference", lambda it: it["reference"].update(answer="\x1b[0m"),
     "reference.answer holds a control character (U+001B)"),
]


@pytest.mark.parametrize(("name", "mutate", "message"), BAD_ITEMS, ids=[b[0] for b in BAD_ITEMS])
def test_a_bad_item_is_refused_with_its_reason(tmp_path: Path, name: str, mutate: Callable[[dict[str, Any]], None],
                                               message: str) -> None:
    item = seat_item(1)
    mutate(item)
    with pytest.raises(ev.EvalError, match=re.escape(message)):
        ev.load_set(write_set(private_dir(tmp_path), [item]))


def test_a_private_item_drafted_off_the_node_is_refused_but_a_public_one_is_not() -> None:
    off = {"drafted_where": "offnode", "drafted_by": "offnode-session"}
    assert ev.validate_item(seat_item(1, **off), "line 1") == []  # public, off-node: allowed
    errs = ev.validate_item(seat_item(1, source_kind="private", **off), "line 1")
    assert len(errs) == 1 and "drafted on the node only" in errs[0] and "L55" in errs[0]


def test_drafted_by_is_an_engine_key_or_a_named_drafter() -> None:
    """The own-draft rule compares drafted_by with the engine keys exactly, so only exact keys pass (D-49)."""
    for by in (GLM, DEEPSEEK, GPT_OSS, "principal"):
        assert ev.validate_item(seat_item(1, drafted_by=by), "line 1") == []
    assert ev.validate_item(seat_item(1, drafted_by="offnode-session"), "line 1")  # the session drafts off the node
    assert ev.validate_item(seat_item(1, drafted_by=["glm-5.3-flash"]), "line 1")  # not a string: refused, no crash
    assert ev.validate_item(seat_item(1, drafted_where=["node"]), "line 1")
    assert ev.validate_item(seat_item(1, prompt="tab\tand newline\nand CR\r stay"), "line 1") == []


def test_trial_items_keep_their_own_rules() -> None:
    assert all(ev.validate_item(trial_item(c), "t") == [] for c in "ABCDE")
    errs = ev.validate_item(trial_item("A", source_kind="public", drafted_where="offnode",
                                       drafted_by="offnode-session"), "t")
    assert any("drafted only on the node" in e for e in errs)
    errs = ev.validate_item(trial_item("B", objective={"type": "none", "answer": None, "tolerance": None}), "t")
    assert any("needs an objective key" in e for e in errs)
    errs = ev.validate_item(trial_item("A", category="APPLY"), "t")
    assert any("category 'APPLY'" in e for e in errs)


def test_one_set_per_file_unique_ids_and_parseable_lines(tmp_path: Path) -> None:
    d = private_dir(tmp_path)
    with pytest.raises(ev.EvalError, match="one canary per file"):
        ev.load_set(write_set(d, [seat_item(1), seat_item(2, canary=CANARY + "-other")]))
    with pytest.raises(ev.EvalError, match="used twice"):
        ev.load_set(write_set(d, [seat_item(1), seat_item(1)]))
    (d / "broken.jsonl").write_text('{"id": \n', encoding="utf-8")
    with pytest.raises(ev.EvalError, match="not JSON"):
        ev.load_set(d / "broken.jsonl")


def test_freeze_refuses_an_item_nobody_has_checked(tmp_path: Path) -> None:
    path = write_set(private_dir(tmp_path), [seat_item(1, checked_by=None)])
    assert ev.load_set(path).items  # a draft validates ...
    with pytest.raises(ev.EvalError, match="checked_by is null"):  # ... but is never frozen
        ev.freeze(path, eval_root=tmp_path / "root", db=tmp_path / "db.sqlite3", owner_uid=UID)


# --- 2. private paths -------------------------------------------------------------------------------------------------


def test_a_set_inside_a_git_tree_or_the_repository_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    path = write_set(private_dir(repo), [seat_item(1)])
    with pytest.raises(ev.EvalError, match="git working tree"):
        ev.freeze(path, eval_root=tmp_path / "root", db=tmp_path / "db", owner_uid=UID)
    assert not (tmp_path / "root").exists()  # refused before anything was written
    here = Path(__file__).resolve().parent / "fixtures" / "set.jsonl"  # this repository's own tree
    with pytest.raises(ev.EvalError, match=r"git working tree|copy of the repository"):
        ev.refuse_repository_path(here, "set")
    with pytest.raises(ev.EvalError, match="copy of the repository"):
        ev.refuse_repository_path(Path("/opt/atlas/day1/sets/set.jsonl"), "set")
    ok = write_set(private_dir(tmp_path), [seat_item(1)])
    with pytest.raises(ev.EvalError, match="git working tree"):
        ev.freeze(ok, eval_root=repo / "evalroot", db=tmp_path / "db", owner_uid=UID)


def test_validate_refuses_a_repository_path_from_the_command_line(tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    path = write_set(private_dir(repo), [seat_item(1)])
    assert ev.main(["validate", str(path)]) == 1
    assert "git working tree" in capsys.readouterr().err


def test_freeze_needs_root_only_files_and_directories(tmp_path: Path) -> None:
    d = private_dir(tmp_path)
    loose = write_set(d, [seat_item(1)], mode=0o644)
    with pytest.raises(ev.EvalError, match="must be root-only"):
        ev.freeze(loose, eval_root=tmp_path / "root", db=tmp_path / "db", owner_uid=UID)
    os.chmod(loose, 0o600)
    os.chmod(d, 0o755)
    with pytest.raises(ev.EvalError, match="must be root-only"):
        ev.freeze(loose, eval_root=tmp_path / "root", db=tmp_path / "db", owner_uid=UID)
    os.chmod(d, 0o700)
    with pytest.raises(ev.EvalError, match="must be root-only"):  # owned by someone else
        ev.freeze(loose, eval_root=tmp_path / "root", db=tmp_path / "db", owner_uid=UID + 1)


# --- 4. freeze: blinding key, hashes and the rule in the ledger -------------------------------------------------------


def test_freeze_writes_the_key_0600_and_records_hashes_and_rule_before_any_run(tmp_path: Path) -> None:
    items = [trial_item(c) for c in "ABCDE"]
    run, db = freeze_items(tmp_path, items, run_id="fx-trial")
    key_path = Path(run.manifest["key_path"])
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)  # noqa: E731
    assert mode(key_path) == 0o600 and mode(key_path.parent) == 0o700
    assert mode(run.dir) == 0o700 and mode(run.dir / "set.jsonl") == 0o400 and mode(run.dir / "manifest.json") == 0o600
    assert ev.git_tree_of(key_path) is None
    led = Ledger(db)
    rows = led.query("SELECT * FROM tasks")
    led.close()
    assert [r["kind"] for r in rows] == ["eval-freeze"] and rows[0]["id"] == run.manifest["ledger_task_id"]
    payload = json.loads(rows[0]["payload_json"])
    set_bytes = (private_dir(tmp_path) / "set.jsonl").read_bytes()
    assert payload["set_sha256"] == hashlib.sha256(set_bytes).hexdigest()
    assert payload["key_sha256"] == hashlib.sha256(key_path.read_bytes()).hexdigest()
    assert payload["rule"]["min_wins"] == 26 and payload["rule"]["of_decided"] == 40
    assert payload["rule"]["max_tie_fraction"] == 0.25 and payload["rule"]["promise_preference"] == 0.45
    assert payload["rule"]["no_loss_categories"] == ["B", "D", "E"] and payload["rule"]["d47_floor_required"]
    assert payload["rule_sha256"] == hashlib.sha256(ev.canonical(payload["rule"])).hexdigest()
    assert not (run.dir / "answers.jsonl").exists()  # nothing has run
    blob = json.dumps(rows, default=str)
    assert "TEST FIXTURE" not in blob and CANARY not in blob  # hashes and the rule only, never an item
    assert ev.verify_preregistration(run, db, required=True) == "ledger"
    other = tmp_path / "other.sqlite3"
    with pytest.raises(ev.EvalError, match="no eval-freeze row"):
        ev.verify_preregistration(run, other, required=True)
    assert ev.verify_preregistration(run, other, required=False) == "archived"


def test_a_rule_changed_after_the_freeze_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule applied is the rule pre-registered: a constant edited after the freeze refuses the frozen run, for the
    runner and for results, rather than decide it under another rule."""
    run, db = freeze_items(tmp_path, [trial_item(c) for c in "ABCDE"], run_id="fx-trial")
    assert ev.verify_preregistration(run, db, required=True) == "ledger"
    monkeypatch.setattr(ev, "TRIAL_PROMISE", 0.55)
    for required in (True, False):
        with pytest.raises(ev.EvalError, match=r"the rule in this code differs from the rule pre-registered for it at "
                                               r"freeze \(promise_preference\); decide it with the code that froze it"):
            ev.verify_preregistration(run, db, required=required)
    monkeypatch.setattr(ev, "TRIAL_PROMISE", 0.45)
    assert ev.verify_preregistration(run, db, required=True) == "ledger"
    seat, seat_db = freeze_items(tmp_path / "seat", [seat_item(1)])
    monkeypatch.setattr(ev, "USABLE_MIN", 6)
    with pytest.raises(ev.EvalError, match=r"\(usable_min\)"):
        ev.verify_preregistration(seat, seat_db, required=False)


def test_a_run_resumes_after_its_freeze_row_is_archived(tmp_path: Path) -> None:
    """D9 archives tasks rows after 30 days. Before the first answer the eval-freeze row is required; once answers
    exist, the run resumes on the manifest's hashes (as results does) instead of being re-frozen."""
    run, db = freeze_items(tmp_path, [seat_item(1)])
    gone = tmp_path / "archived.sqlite3"  # a ledger without the row, as after the archive
    with pytest.raises(ev.EvalError, match="no eval-freeze row"):
        ev.verify_for_run(run, gone)
    assert ev.verify_for_run(run, db) == "ledger"
    ev.append_jsonl(run.answers_path, {"label": next(iter(run.key["answers"])), "text": "x"})
    assert ev.verify_for_run(run, gone) == "archived"


def test_the_key_labels_every_answer_and_hides_the_engine(tmp_path: Path) -> None:
    run, _ = freeze_items(tmp_path, [seat_item(n) for n in range(1, 4)])
    answers = run.key["answers"]
    assert len(answers) == 9 and len(set(answers)) == 9
    assert {(v["task"], v["engine"]) for v in answers.values()} == {
        (f"fx-law-{n:02d}", e) for n in range(1, 4) for e in ev.DAY1_ENGINES}
    assert all(re.fullmatch(r"A-[A-Z2-9]{6}", lb) for lb in answers)  # opaque: no engine, no order
    assert len(run.key["seed"]) == 64


def test_a_frozen_run_is_never_refrozen_and_a_changed_key_voids_it(tmp_path: Path) -> None:
    run, db = freeze_items(tmp_path, [seat_item(1)])
    path = private_dir(tmp_path) / "set.jsonl"
    with pytest.raises(ev.EvalError, match="already exists"):
        ev.freeze(path, eval_root=run.root, db=db, owner_uid=UID, run_id=run.run_id)
    key_path = Path(run.manifest["key_path"])
    key = json.loads(key_path.read_text())
    key["seed"] = "0" * 64
    key_path.write_text(json.dumps(key))
    with pytest.raises(ev.EvalError, match="does not match the key recorded at freeze"):
        ev.Run.open(run.root, run.run_id, owner_uid=UID)


# --- blinding ---------------------------------------------------------------------------------------------------------


def test_blinding_strips_reasoning_self_identification_and_normalises_markdown() -> None:
    raw = ("<think>I am GLM and I think...</think>\n<|channel|>analysis<|message|>private<|end|>"
           "As DeepSeek-V4-Flash (from DeepSeek), built like GPT-4 by OpenAI or gpt-oss-120b, I answer.\n\n\n\n"
           "* one   \n+ two\n1) three\n**bold** stays")
    out = ev.blind(raw)
    assert "think" not in out.lower() and "private" not in out and "<|" not in out
    for name in ("GLM", "DeepSeek", "GPT-4", "OpenAI", "gpt-oss"):
        assert name.lower() not in out.lower()
    assert "[model]" in out and "- one\n- two\n1. three\n**bold** stays" in out and "\n\n\n" not in out


# --- 3 and 5. the run: the Arbiter, grouped engines, caps, truncations ------------------------------------------------


def test_a_seat_run_goes_through_the_arbiter_one_engine_at_a_time_with_few_swaps(
        tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    out = seat_run(tmp_path, config_dir, engines)
    run, fake, controller = out["run"], out["fake"], out["controller"]
    starts = [k for verb, k in controller.calls if verb == "start"]
    # answers: GLM, DeepSeek, gpt-oss; judges start with gpt-oss, already resident: five loads, not fifteen
    assert starts == [GLM, DEEPSEEK, GPT_OSS, GLM, DEEPSEEK]
    answers = ev.read_jsonl(run.answers_path)
    assert len(answers) == 15 and {ev.Run.writer(run, a["label"]) for a in answers} == set(ev.DAY1_ENGINES)
    gens = [b for k in ev.DAY1_ENGINES for b in fake.bodies[k] if "neutral" not in b["messages"][0]["content"]]
    assert len(gens) == 15 and all(b["max_tokens"] == ev.D49_MAX_TOKENS for b in gens)
    assert all("temperature" not in b for b in gens)  # gideon has no sampling_temperature: the server's default
    system = gens[0]["messages"][0]["content"]
    assert "Gideon" in system and "Governance" in system and "06. General Counsel" in system
    assert "Retrieved memory" not in system and "Scars" not in system  # layers 1-3 only, no memory read
    assert "TEST FIXTURE context" in gens[0]["messages"][1]["content"]
    for j in ev.read_jsonl(run.judgements_path):  # no engine ever grades its own answer
        assert run.writer(j["label"]) != j["judge"]
    assert len(ev.read_jsonl(run.judgements_path)) == 30


def test_every_truncation_is_recorded(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    out = seat_run(tmp_path, config_dir, engines)
    run, summary = out["run"], out["summary"]
    assert summary[DEEPSEEK]["truncated"] == 5 and summary[DEEPSEEK]["rate"] == 1.0
    assert summary[GLM]["truncated"] == 0 and summary[DEEPSEEK]["flagged"] == []  # D-49 records, D-46 flags
    recs = [a for a in ev.read_jsonl(run.answers_path) if a["truncated"]]
    assert len(recs) == 5 and all(a["finish_reason"] == "length" and a["max_tokens"] == 16384 for a in recs)
    events = ev.read_jsonl(run.runlog_path)
    assert any(e["event"] == "answers-done" and e["engine"] == DEEPSEEK and e["summary"]["truncated"] == 5
               for e in events)


def test_a_resumed_run_keeps_what_it_has(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    out = seat_run(tmp_path, config_dir, engines)
    run, fake, arb = out["run"], out["fake"], out["arbiter"]
    before = sum(len(v) for v in fake.bodies.values())
    gate = ev.ArbiterGate(arb, load_wait_s=0.0)
    client_for = ev.client_factory(engines, env_dir=None, transport_for=fake.transport_for)
    ev.run_answers(run, gate, client_for, seat_prompts(config_dir, engines), say=no_say)
    ev.run_judges(run, gate, client_for, say=no_say)
    assert sum(len(v) for v in fake.bodies.values()) == before


# --- the no-memory guarantee ------------------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {"atlas.memory", "atlas.router", "atlas.api", "atlas.tasks", "atlas.approval",
                     "atlas.celery_app", "atlas.vault", "atlas.governance", "atlas.openwebui_filter", "chromadb",
                     "lightrag", "celery"}


def test_the_runner_imports_no_memory_router_pipeline_or_task_module() -> None:
    tree = ast.parse(Path(ev.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")}


def test_a_whole_run_writes_no_memory_no_conversation_and_no_routing(
        tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec], monkeypatch: pytest.MonkeyPatch) -> None:
    import atlas.memory as memory
    import atlas.router as router

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("the evaluation runner reached a memory, routing, approval or strike writer")

    monkeypatch.setattr(memory.MemoryStore, "write", forbidden)
    monkeypatch.setattr(memory.LightRAGStore, "insert", forbidden)
    monkeypatch.setattr(router.Router, "route", forbidden)
    for name in ("insert_routing_decision", "insert_approval", "insert_strike", "insert_sentinel_pulse"):
        monkeypatch.setattr(Ledger, name, forbidden)
    db = tmp_path / "ledger" / "atlas.sqlite3"
    db.parent.mkdir()
    ledger = Ledger(db)
    ledger.init_db()
    out = seat_run(tmp_path, config_dir, engines, ledger=ledger, marks={})
    ledger.close()
    run = out["run"]
    led = Ledger(out["db"])
    try:
        for table in ("routing_decisions", "approvals", "strikes", "sentinel_pulses"):
            assert led.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0
        assert {r["kind"] for r in led.query("SELECT kind FROM tasks")} == {"eval-freeze"}
        decisions = led.query("SELECT * FROM arbiter_decisions")
        dump = json.dumps({t: led.query(f"SELECT * FROM {t}") for t in led.tables()}, default=str)
    finally:
        led.close()
    assert decisions and all(str(d["task_id"]).startswith(f"eval-{run.run_id}") for d in decisions)
    for text in ("TEST FIXTURE", "QUALITY=", CANARY, "hidden chain"):
        assert text not in dump  # no prompt, context, reference or answer reached the ledger


# --- 6. judges: aggregation, escalation, calibration ------------------------------------------------------------------


def test_judge_aggregation_and_escalation(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    run = seat_run(tmp_path, config_dir, engines)["run"]
    seat = ev.aggregate_seat(run)

    def at(engine: str, task: str) -> ev.SeatAnswer:
        return seat[run.label_of(task, engine)]

    assert not at(GLM, "fx-law-01").escalate and at(GLM, "fx-law-01").judge_mean == 8
    assert at(GLM, "fx-law-01").usable
    two = at(GLM, "fx-law-02")
    assert not two.escalate and two.judge_mean == 7 and two.usable  # 8 and 6: 2 apart, mean 7, usable
    three = at(DEEPSEEK, "fx-law-03")
    assert three.escalate and "3 or more points apart" in three.reasons[0]
    fatal = at(GPT_OSS, "fx-law-04")
    assert fatal.escalate and len([r for r in fatal.reasons if "FATAL" in r]) == 2 and not fatal.usable
    garbled = at(GLM, "fx-law-05")
    assert garbled.escalate and garbled.judge_mean is None and "did not parse" in garbled.reasons[0]
    judged = [j for j in ev.read_jsonl(run.judgements_path) if j["label"] == garbled.label]
    assert all(j["attempts"] == 2 and j["grade"] is None for j in judged)  # one literal retry each
    rows = ev.principal_rows(run)
    assert sorted(k for k, v in rows.items() if v == "escalation") == sorted(
        [three.label, fatal.label, garbled.label])
    cal = [k for k, v in rows.items() if v == "calibration"]
    assert len(cal) == math.ceil(0.10 * 15) and rows == ev.principal_rows(run)  # 10%, fixed by the key's seed
    assert not any(seat[k].escalate for k in cal)


def test_the_calibration_sample_is_drawn_per_domain(tmp_path: Path, config_dir: Path,
                                                     engines: dict[str, EngineSpec]) -> None:
    """Agreement is judged per domain, so each domain gets its own sample: 10% of its answers, at least 2, from its
    non-escalated answers. Drawn from the whole pool, the 6 answers of a small domain could get none."""
    items = [*(seat_item(n) for n in range(1, 6)), *(seat_item(n, domain="hr") for n in (6, 7))]
    run = seat_run(tmp_path, config_dir, engines, items=items)["run"]
    seat = ev.aggregate_seat(run)
    rows = ev.principal_rows(run)
    cal = [seat[k] for k, v in rows.items() if v == "calibration"]
    assert sorted(a.domain for a in cal) == ["au-law", "au-law", "hr", "hr"]
    assert not any(a.escalate for a in cal) and rows == ev.principal_rows(run)  # fixed by the key's seed
    rule = ev.preregistered_rule(ev.SEAT_SET)
    assert rule["calibration_min_per_domain"] == 2 and "per domain" in rule["calibration"]


# --- the seat rule ----------------------------------------------------------------------------------------------------


def test_the_seat_rule() -> None:
    t = ev.Tally
    keeps = ev.seat_rule({GLM: t(16, 13, 1), DEEPSEEK: t(16, 12, 0), GPT_OSS: t(16, 10, 0)},
                         {DEEPSEEK: (-1, 0), GPT_OSS: (-3, -2)})
    assert (keeps["seat"], keeps["decision"], keeps["verifier_pass_required"]) == (GLM, "keeps", False)
    two_fatal = ev.seat_rule({GLM: t(16, 14, 2), DEEPSEEK: t(16, 12, 0), GPT_OSS: t(16, 9, 0)},
                             {DEEPSEEK: (-2, -2), GPT_OSS: (-5, -4)})
    assert two_fatal["decision"] == "keeps, unproven" and two_fatal["verifier_pass_required"]
    assert two_fatal["raised_approval_tier"]
    moves = ev.seat_rule({GLM: t(16, 10, 0), DEEPSEEK: t(16, 13, 0), GPT_OSS: t(16, 9, 0)},
                         {DEEPSEEK: (3, 3), GPT_OSS: (-1, 0)})
    assert (moves["seat"], moves["decision"], moves["verifier_pass_required"]) == (DEEPSEEK, "moves", False)
    # The approved order: GLM at the bar keeps the seat, whatever another engine's lead.
    at_bar = ev.seat_rule({GLM: t(16, 13, 0), DEEPSEEK: t(16, 16, 0), GPT_OSS: t(16, 9, 0)},
                          {DEEPSEEK: (3, 3), GPT_OSS: (-4, -4)})
    assert (at_bar["seat"], at_bar["decision"], at_bar["verifier_pass_required"]) == (GLM, "keeps", False)
    neutral_disagrees = ev.seat_rule({GLM: t(16, 10, 0), DEEPSEEK: t(16, 13, 0), GPT_OSS: t(16, 9, 0)},
                                     {DEEPSEEK: (3, 2), GPT_OSS: (-1, 0)})
    assert neutral_disagrees["seat"] == GLM
    lead_two = ev.seat_rule({GLM: t(16, 13, 0), DEEPSEEK: t(16, 15, 0), GPT_OSS: t(16, 9, 0)},
                            {DEEPSEEK: (2, 4), GPT_OSS: (-4, -4)})
    assert (lead_two["seat"], lead_two["decision"]) == (GLM, "keeps")
    tie = ev.seat_rule({GLM: t(16, 8, 0), DEEPSEEK: t(16, 12, 0), GPT_OSS: t(16, 12, 0)},
                       {DEEPSEEK: (4, 4), GPT_OSS: (4, 3)})
    assert tie["seat"] == GLM and tie["decision"].startswith("contested")
    assert ev.meets_bar(t(10, 8, 1)) and not ev.meets_bar(t(10, 7, 0)) and not ev.meets_bar(t(0, 0, 0))


# --- the D-46 promotion rule at its edges -----------------------------------------------------------------------------


def trial(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"wins": 26, "losses": 14, "ties": 0, "fatal_trial": 1, "fatal_incumbent": 1,
                            "objective": {"B": (5.0, 5.0), "D": (4.0, 4.0), "E": (3.0, 2.0)}, "floor_met": True}
    return ev.trial_rule(**{**base, **kw})


def test_the_promotion_rule_at_25_26_and_27_wins() -> None:
    # The approved table (notes_SETS.md section 6): one-sided sign test at alpha 0.05; 30 -> 20, 40 -> 26, 50 -> 32.
    assert ev.wins_needed(40) == 26 and ev.TRIAL_ALPHA == 0.05
    assert ev.wins_needed(50) == 32 and ev.wins_needed(30) == 20 and ev.wins_needed(4) is None
    assert [ev.wins_needed(n) for n in (28, 35, 37, 42, 44, 49)] == [19, 23, 24, 27, 28, 31]
    assert trial(wins=25, losses=15)["decision"] == "keep-0731"
    assert trial(wins=26, losses=14)["decision"] == "promote"
    assert trial(wins=27, losses=13)["decision"] == "promote"


def test_ties_above_a_quarter_ask_for_ten_more_tasks() -> None:
    at_quarter = trial(wins=20, losses=10, ties=10)  # 25%: decided on the 30 pairs, which need 20 (the table)
    assert at_quarter["decision"] == "promote" and at_quarter["wins_needed"] == 20
    over = trial(wins=20, losses=9, ties=11)  # 27.5%
    assert over["decision"] == "extend" and "add 10 tasks" in over["reason"] and not over["promote"]
    scaled = trial(wins=26, losses=14, ties=10)  # 20% ties, 40 decided
    assert scaled["decision"] == "promote"


def test_fatal_excess_an_objective_loss_or_the_floor_blocks_promotion() -> None:
    assert trial(fatal_trial=2)["decision"] == "keep-0731"
    assert "objective" in trial(objective={"B": (4.0, 5.0), "D": (4.0, 4.0), "E": (3.0, 3.0)})["reason"]
    assert trial(floor_met=False)["decision"] == "keep-0731"
    assert trial(floor_met=None)["decision"] == "held"
    assert trial(wins=18, losses=22)["promise"] and not trial(wins=17, losses=23)["promise"]
    assert not trial(wins=18, losses=22, fatal_trial=2)["promise"]  # T-32 promise: 45% and no fatal excess


# --- the D-46 run: production caps per role, four verdicts, the trial arm via the Arbiter -----------------------------


def trial_setup(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> dict[str, Any]:
    items = [trial_item(c) for c in "ABCDE"]
    run, db = freeze_items(tmp_path, items, run_id="fx-trial")
    marks = {
        (TRIAL, "fx-trial-A1"): {"quality": 9},
        (TRIAL, "fx-trial-B1"): {"extra": "flaw: fixture-flaw-one"},
        (DEEPSEEK, "fx-trial-C1"): {"quality": 9, "bias": True},  # GLM's position bias: a split
        (TRIAL, "fx-trial-D1"): {"extra": "fixture-fact-alpha"},
        (DEEPSEEK, "fx-trial-D1"): {"extra": "fixture-fact-alpha"},
        (TRIAL, "fx-trial-E1"): {"extra": "ANSWER: 42.2", "quality": 9},
        (DEEPSEEK, "fx-trial-E1"): {"extra": "ANSWER: 40"},
    }
    fake = FakeEngines(marks, finish={TRIAL: lambda task: "length" if task == "fx-trial-B1" else "stop"})
    trial_spec = EngineSpec(key=TRIAL, arbiter_class="external", mode="external", footprint_gb=1.0, ctx_size=131072)
    arb, controller, _ = make_arbiter({**engines, TRIAL: trial_spec})
    gate = ev.ArbiterGate(arb, load_wait_s=0.0)
    client_for = ev.client_factory(engines, env_dir=None, external={TRIAL: ("http://127.0.0.1:8199", 131072)},
                                   transport_for=fake.transport_for)
    return {"run": run, "db": db, "fake": fake, "arb": arb, "gate": gate, "client_for": client_for,
            "prompts": seat_prompts(config_dir, engines), "controller": controller}


def run_trial(s: dict[str, Any]) -> None:
    run, gate, client_for, prompts, arb = s["run"], s["gate"], s["client_for"], s["prompts"], s["arb"]
    ev.run_answers(run, gate, client_for, prompts, engines=[DEEPSEEK], say=no_say)  # the 0731 arm, after Day 1
    with pytest.raises(ev.EvalError, match="trial build"):  # V4.1 before the trial build registers it
        ev.run_answers(run, gate, client_for, prompts, engines=[TRIAL], external={TRIAL}, say=no_say)
    assert arb.request_load(TRIAL, task_id="trial-build").granted  # the S-51 build registers it (external)
    s["summary"] = ev.run_answers(run, gate, client_for, prompts, engines=[TRIAL], external={TRIAL}, say=no_say)
    assert arb.request_unload(TRIAL, task_id="trial-build").granted
    ev.run_judges(run, gate, client_for, say=no_say)


def test_the_trial_runs_each_role_at_its_production_cap_and_sampling(
        tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    s = trial_setup(tmp_path, config_dir, engines)
    run_trial(s)
    fake, controller = s["fake"], s["controller"]
    assert TRIAL not in [k for verb, k in controller.calls]  # the runner never starts or stops the trial engine
    for arm in (DEEPSEEK, TRIAL):
        bodies = {re.search(r"item (fx-trial-\w+):", b["messages"][1]["content"]).group(1): b  # type: ignore
                  for b in fake.bodies[arm]}
        a, b, c, d, e = (bodies[f"fx-trial-{x}1"] for x in "ABCDE")
        assert (a["temperature"], a["top_p"], a["max_tokens"]) == (1.0, 1.0, 4096)
        assert (b["temperature"], b["max_tokens"]) == (0.15, 1024) and "top_p" not in b
        assert (c["temperature"], c["max_tokens"]) == (0.6, 3072) and "top_p" not in c
        assert (d["temperature"], d["top_p"], d["max_tokens"], e["max_tokens"]) == (1.0, 1.0, 4096, 4096)
        assert "Arthur" in a["messages"][0]["content"]
    trunc = s["summary"][TRIAL]
    assert trunc["truncated"] == 1 and trunc["flagged"] == ["B"]  # 1 of 1 in B: above 10%, flagged
    assert s["summary"][DEEPSEEK]["flagged"] == []


def test_the_apex_roles_take_the_chat_pipelines_cap() -> None:
    """TF_OMEGA runs through the chat pipeline, whose cap is api.DEFAULT_MAX_TOKENS: A, D and E take it (evalrun copies
    it, since it must not import atlas.api); B and C keep deep_think.py's 1,024 and 3,072."""
    from atlas import api

    assert {c: r.max_tokens for c, r in ev.D46_ROLES.items()} == {
        "A": api.DEFAULT_MAX_TOKENS, "B": 1024, "C": 3072, "D": api.DEFAULT_MAX_TOKENS, "E": api.DEFAULT_MAX_TOKENS}


def test_four_verdicts_per_task_and_a_split_is_escalated(tmp_path: Path, config_dir: Path,
                                                         engines: dict[str, EngineSpec]) -> None:
    s = trial_setup(tmp_path, config_dir, engines)
    run_trial(s)
    run = s["run"]
    verdicts = ev.read_jsonl(run.judgements_path)
    assert len(verdicts) == 5 * 4
    assert {(v["judge"], v["order"]) for v in verdicts if v["task"] == "fx-trial-A1"} == {
        (j, o) for j in ev.TRIAL_JUDGES for o in ("xy", "yx")}
    pairs = ev.aggregate_pairs(run)
    assert pairs["fx-trial-A1"].winner_arm() == TRIAL and not pairs["fx-trial-A1"].escalate
    split = pairs["fx-trial-C1"]
    assert split.judge_winner is None and split.escalate and any("orders disagree" in r for r in split.reasons)
    assert pairs["fx-trial-B1"].winner_arm() == "tie"
    judge_bodies = [b for j in ev.TRIAL_JUDGES for b in s["fake"].bodies[j]]
    assert all(DISPLAY[DEEPSEEK] not in b["messages"][1]["content"] and "hidden chain" not in b["messages"][1][
        "content"] for b in judge_bodies)  # judges see blinded answers only


# --- 7. the workbook round trip and unblinding ------------------------------------------------------------------------


def fill(path: Path, out: Path, values: dict[str, Any]) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(path)
    ws = wb[ev.SEAT_SHEET] if ev.SEAT_SHEET in wb.sheetnames else wb[ev.PAIR_SHEET]
    header = [c.value for c in ws[ev.HEADER_ROW]]
    for row in ws.iter_rows(min_row=ev.HEADER_ROW + 1):
        if row[0].value is None:
            continue
        for name, v in (values(row[0].value) if callable(values) else values).items():
            row[header.index(name)].value = v
    wb.save(out)


def test_the_seat_workbook_round_trip_unblinds_only_when_every_grade_is_in(
        tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    out = seat_run(tmp_path, config_dir, engines)
    run, db = out["run"], out["db"]
    path, n = ev.write_workbook(run, run.dir / "workbook.xlsx", owner_uid=UID)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and n == 3 + 2  # three escalations, two calibration rows
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == [ev.README_SHEET, ev.SEAT_SHEET, ev.META_SHEET]
    assert wb[ev.META_SHEET].sheet_state == "hidden"
    ws = wb[ev.SEAT_SHEET]
    validations = {str(dv.sqref).split(":")[0][0]: dv.formula1 for dv in ws.data_validations.dataValidation}
    assert validations == {"J": '"0,1,2,3,4,5"', "K": '"0,1,2,3"', "L": '"0,1,2"', "M": '"No,Yes"'}
    assert ws.cell(row=ev.HEADER_ROW + 1, column=10).fill.fgColor.rgb.endswith("FFFF00")
    cells = " ".join(str(c.value) for sheet in wb for row in sheet.iter_rows() for c in row if c.value is not None)
    for leak in (*ev.DAY1_ENGINES, *DISPLAY.values(), "DeepSeek", "GLM", "hidden chain", "<think>"):
        assert leak not in cells  # blind: no engine named, no reasoning
    with pytest.raises(ev.EvalError, match="still need the Principal's grade; nothing is unblinded"):
        ev.results(run, [path], db)
    assert not (run.dir / "results.json").exists()
    half = tmp_path / "half.xlsx"
    fill(path, half, {"Substance 0-5": 5})
    with pytest.raises(ev.EvalError, match="must all be chosen"):
        ev.results(run, [half], db)
    graded = tmp_path / "graded.xlsx"
    fill(path, graded, {"Substance 0-5": "5", "Sources 0-3": 3, "Clarity 0-2": "2", "Fatal": "No"})
    res = ev.results(run, [graded], db)
    assert res["preregistration"] == "ledger" and res["principal_rows"] == 5
    law = res["domains"]["au-law"]
    assert law["status"] == "decided" and law["calibration_agreement"] == 1.0
    assert set(law["tallies"]) == set(ev.DAY1_ENGINES) and law["seat"] in ev.DAY1_ENGINES
    assert sorted(res["truncated_answers"]) == [f"fx-law-0{n}:{DEEPSEEK}" for n in range(1, 6)]
    assert stat.S_IMODE((run.dir / "results.json").stat().st_mode) == 0o600
    led = Ledger(db)
    kinds = [r["kind"] for r in led.query("SELECT kind FROM tasks ORDER BY created_at")]
    led.close()
    assert kinds == ["eval-freeze", "eval-unblind"]


def test_a_control_character_never_stops_the_workbook(tmp_path: Path, config_dir: Path,
                                                      engines: dict[str, EngineSpec]) -> None:
    """openpyxl refuses C0 control characters (IllegalCharacterError, not a ValueError). An answer that holds one is
    written without it; validate refuses one in an item's own text (the schema tests)."""
    openpyxl = pytest.importorskip("openpyxl")
    marks = {(GLM, "fx-law-01"): {"extra": "page one\x0cpage two \x1b[0m end"}}
    run = seat_run(tmp_path, config_dir, engines, marks=marks)["run"]
    path, n = ev.write_workbook(run, run.dir / "all.xlsx", owner_uid=UID, all_rows=True)
    assert n == 15
    cells = [str(c.value) for row in openpyxl.load_workbook(path)[ev.SEAT_SHEET].iter_rows() for c in row
             if c.value is not None]
    assert any("page onepage two [0m end" in c for c in cells)
    assert not any(ev.CONTROL_CHARS_RE.search(c) for c in cells)


def test_a_domain_whose_calibration_disagrees_is_graded_in_full(
        tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    pytest.importorskip("openpyxl")
    items = [seat_item(1, drafted_by=GLM), *(seat_item(n) for n in range(2, 6))]  # GLM drafted item 1 on the node
    out = seat_run(tmp_path, config_dir, engines, items=items)
    run, db = out["run"], out["db"]
    rows = ev.principal_rows(run)
    first, _ = ev.write_workbook(run, run.dir / "workbook.xlsx", owner_uid=UID)
    graded = tmp_path / "graded.xlsx"
    fill(first, graded, lambda lb: (
        {"Substance 0-5": 1, "Sources 0-3": 0, "Clarity 0-2": 0, "Fatal": "No"} if rows.get(lb) == "calibration"
        else {"Substance 0-5": 5, "Sources 0-3": 3, "Clarity 0-2": 2, "Fatal": "No"}))
    # Below 80%: the domain is graded in full by hand, still blind. Nothing is attributed to an engine before that:
    # no results.json, no truncation by engine, no eval-unblind row.
    with pytest.raises(ev.EvalError, match=r"domain au-law: calibration agreement 0% is below 80%: grade the domain "
                                           r"in full \(atlas-admin eval workbook fx-run --domain au-law\)"):
        ev.results(run, [graded], db)
    assert not (run.dir / "results.json").exists()
    led = Ledger(db)
    assert [r["kind"] for r in led.query("SELECT kind FROM tasks")] == ["eval-freeze"]
    led.close()
    full, n = ev.write_workbook(run, run.dir / "full.xlsx", owner_uid=UID, domains=["au-law"])
    assert n == 15
    full_graded = tmp_path / "full-graded.xlsx"
    fill(full, full_graded, {"Substance 0-5": 5, "Sources 0-3": 3, "Clarity 0-2": 2, "Fatal": "No"})
    res = ev.results(run, [graded, full_graded], db)
    law = res["domains"]["au-law"]
    assert law["status"] == "decided" and law["seat"] == GLM and law["decision"] == "keeps"
    assert law["tallies"][GLM]["n"] == 4  # an engine is never scored on an item it drafted
    assert all(t["usable"] == t["n"] == 5 for e, t in law["tallies"].items() if e != GLM)
    # On the four items neither drafted: level by the final grades; by gpt-oss's own grades GLM's items 2 (6/10) and
    # 5 (no parse) are not usable against DeepSeek's item 3 (5/10), so the neutral view is DeepSeek +1, not +3.
    assert law["leads"][DEEPSEEK] == {"lead": 0, "neutral_lead": 1} and law["decision"] == "keeps"


def test_the_trial_workbook_round_trip(tmp_path: Path, config_dir: Path, engines: dict[str, EngineSpec]) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    s = trial_setup(tmp_path, config_dir, engines)
    run_trial(s)
    run, db = s["run"], s["db"]
    path, n = ev.write_workbook(run, run.dir / "workbook.xlsx", owner_uid=UID)
    assert n == 5  # one split escalated, the other four as the calibration sample (10, capped by the pool)
    ws = openpyxl.load_workbook(path)[ev.PAIR_SHEET]
    validations = {str(dv.sqref)[0]: dv.formula1 for dv in ws.data_validations.dataValidation}
    assert validations == {"H": '"X better,Y better,Tie"', "I": '"No,Yes"', "J": '"No,Yes"'}
    with pytest.raises(ev.EvalError, match="nothing is unblinded"):
        ev.results(run, [path], db)
    pairs = {p.label: p for p in ev.aggregate_pairs(run).values()}

    def agree(label: str) -> dict[str, Any]:  # the Principal agrees with the judges; the split goes to the trial
        p = pairs[label]
        slot = p.judge_winner or ("x" if p.x == TRIAL else "y")
        return {"Verdict": {"x": "X better", "y": "Y better", "tie": "Tie"}[slot], "Fatal in X": "No",
                "Fatal in Y": "No"}

    graded = tmp_path / "graded.xlsx"
    fill(path, graded, agree)
    res = ev.results(run, [graded], db, floor_met=True)
    assert res["status"] == "decided" and res["calibration_agreement"] == 1.0
    assert (res["wins"], res["losses"], res["ties"]) == (3, 0, 2)  # A, C (the Principal), E; B and D tie
    assert res["objective"] == {"B": [1.0, 0.0], "D": [1.0, 1.0], "E": [1.0, 0.0]}
    assert res["decision"] == "extend"  # 2 ties of 5 is above 25%: add 10 tasks
    assert res["truncations"][TRIAL]["flagged"] == ["B"]


# --- the production gate: the orchestrator's Arbiter over loopback ----------------------------------------------------


def test_the_orchestrator_gate_loads_through_the_arbiter_and_waits_for_the_slot() -> None:
    seen: list[tuple[str, str, dict[str, Any] | None]] = []
    answers = {"load": [{"decision": "queued", "granted": False, "reason": "generation in progress"},
                        {"decision": "granted", "granted": True, "reason": "fits"}],
               "health": [{"generating": {"engine": GLM, "task_id": "chat"}, "generation_queue": []},
                          {"generating": None, "generation_queue": []}]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        assert request.headers.get(ev.ADMIN_TOKEN_HEADER) == "fixture-token"
        if request.url.path == "/arbiter/load":
            return httpx.Response(200, json=answers["load"].pop(0))
        if request.url.path == "/health":
            return httpx.Response(200, json=answers["health"].pop(0))
        if request.url.path == "/arbiter/status":
            return httpx.Response(200, json={"resident": [{"engine": GLM, "loading": False, "unloading": False}]})
        return httpx.Response(404)

    sleeps: list[float] = []
    gate = ev.OrchestratorGate("http://127.0.0.1:8800", admin_token="fixture-token",
                               transport=httpx.MockTransport(handler), sleep=sleeps.append)
    gate.load(GLM, task_id="eval-x")
    with gate.generation(GLM, task_id="eval-x-1"):
        pass
    assert sleeps == [ev.QUEUED_RETRY_S, 5.0]
    assert [(m, p) for m, p, _ in seen] == [("POST", "/arbiter/load"), ("POST", "/arbiter/load"), ("GET", "/health"),
                                            ("GET", "/health"), ("GET", "/arbiter/status")]
    assert seen[0][2] == {"engine": GLM, "task_id": "eval-x"}
    assert not gate.is_resident(DEEPSEEK)
    with pytest.raises(ev.EvalError, match="loopback only"):
        ev.OrchestratorGate("http://192.0.2.1:8800")
    refused = ev.OrchestratorGate("http://127.0.0.1:8800", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"decision": "refused", "granted": False, "reason": "budget"})))
    with pytest.raises(ev.EvalError, match="refused for"):
        refused.load(DEEPSEEK, task_id="eval-x")


# --- the ledger written as its owner (root only) ----------------------------------------------------------------------


@pytest.mark.skipif(UID != 0, reason="needs root to write as another uid")
def test_root_writes_the_ledger_as_its_owner_so_the_service_keeps_its_files() -> None:
    base = Path(tempfile.mkdtemp(prefix="atlas-eval-owner-", dir="/tmp"))
    try:
        os.chmod(base, 0o755)
        d = base / "orchestrator"
        d.mkdir(mode=0o750)
        db = d / "atlas.sqlite3"
        led = Ledger(db)
        led.init_db()
        led.close()
        nobody = 65534
        for p in [d, *d.iterdir()]:
            os.chown(p, nobody, nobody)
        task_id = ev.ledger_record(db, "eval-freeze", {"run_id": "fx"})
        row = ev.ledger_row(db, task_id)
        assert row is not None and row["kind"] == "eval-freeze"
        assert {p.stat().st_uid for p in d.iterdir()} == {nobody}  # no root-owned -wal/-shm left behind
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- objective checks and the CLI -------------------------------------------------------------------------------------


def test_objective_checks() -> None:
    numeric = {"type": "numeric", "answer": 42, "tolerance": 0.5}
    assert ev.check_objective(numeric, "working 41\nANSWER: 42.4") == (1.0, 1.0)
    assert ev.check_objective(numeric, "ANSWER: 43\nthen 42") == (0.0, 1.0)  # the ANSWER line wins
    assert ev.check_objective({**numeric, "tolerance": "1%"}, "about 42.3") == (1.0, 1.0)
    assert ev.check_objective({"type": "exact", "answer": ["Alpha  Beta", "gamma"], "tolerance": None},
                              "alpha beta, no third") == (1.0, 2.0)
    assert ev.check_objective(TRIAL_OBJECTIVES["B"], "Flaw: FIXTURE-FLAW-ONE") == (1.0, 1.0)
    assert ev.check_objective(TRIAL_OBJECTIVES["A"], "anything") == (0.0, 0.0)


def test_the_cli_refuses_a_missing_ledger_freezes_and_asks_for_the_trial_endpoint(
        tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ev, "OWNER_UID", UID)  # the node's is root; the suite runs as whoever runs it
    path = write_set(private_dir(tmp_path), [trial_item(c) for c in "ABCDE"])
    root, db = tmp_path / "evalroot", tmp_path / "led" / "atlas.sqlite3"
    common = ["--db", str(db), "--eval-root", str(root)]
    assert admin.main(["eval", "freeze", str(path), "--run-id", "cli-trial", *common]) == 1
    assert "no ledger at" in capsys.readouterr().err and not root.exists()  # root never creates the ledger
    db.parent.mkdir()
    led = Ledger(db)
    led.init_db()
    led.close()
    assert admin.main(["eval", "freeze", str(path), "--run-id", "cli-trial", *common]) == 0
    assert "frozen: run cli-trial" in capsys.readouterr().out
    assert admin.main(["eval", "run", "cli-trial", *common]) == 1  # before any engine is touched
    assert "needs --trial-url and --trial-window" in capsys.readouterr().err
    assert admin.main(["eval", "workbook", "cli-trial", "--eval-root", str(root)]) == 1
    assert "finish --phase judges" in capsys.readouterr().err
    monkeypatch.setattr(ev, "OWNER_UID", UID + 1)
    assert admin.main(["eval", "workbook", "cli-trial", "--eval-root", str(root)]) == 1
    assert "run as root" in capsys.readouterr().err


def test_atlas_admin_eval_hands_the_line_to_the_runner(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_set(private_dir(tmp_path), [seat_item(1)])
    assert admin.main(["eval", "validate", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"valid: {path} {ev.SEAT_SET} fixture-1, 1 item(s)") and "plan:" in out
    with pytest.raises(SystemExit) as exc:
        admin.main(["eval", "--help"])
    assert exc.value.code == 0 and "validate" in capsys.readouterr().out
    assert Path(atlas.__file__).parent == Path(ev.__file__).parent
