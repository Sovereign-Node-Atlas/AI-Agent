"""Deep Think, adversarial optimisation (Section 9.1; 4.2 rule 8): a working skeleton, honest about what is tuned.

Depths (9.1 table):
    quick     generator and adversary on the SAME loaded engine with different role prompts; 0 swaps; 1-2 min
    standard  Ren generates three trajectories, Arthur scores on his own engine, Ren refines the winner; 2 swaps
    deep      standard plus a second expansion and scoring round, the Apex engine (DeepSeek V4 Flash) delivering the
              final synthesis, Qwen3.5 as third opinion on documents; 3-4 swaps

Corrections adopted (9.1): Nemotron (Arthur) runs at LOW temperature, not zero (reasoning models loop at exactly
zero); scores out of 100 are rubric judgements with NAMED criteria, not mathematics; where a real figure exists Silas
computes it in the sandbox and Arthur scores against the number; phases are BATCHED (all generations, then all
scores), never round-by-round ping-pong between engines.

`plan(tier, arbiter)` pre-requests the full footprint and accepts the Arbiter's downgrade (rule 8). The shapes are
plain functions over an `EngineCall` the orchestrator supplies (atlas.api wires one that loads through the Arbiter and
holds the generation lock), so this module never touches an engine itself and the tests can run it with a stub.

What is a skeleton here, marked TODO: the prompt wording (untested against the real engines), the rubric weights
(equal), the JSON-score repair (one retry), the Silas figure hook (a callback slot, no automatic detection of
"a real figure exists"), Qwen3.5's third opinion (called only when `documents` are passed). None of it lies: every
step runs, returns real text, and records what it did in `DeepThinkResult.log`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.arbiter import APEX_KEY, DEEP_THINK_TIERS, Arbiter, DeepThinkPlan

log = logging.getLogger("atlas.deep_think")

__all__ = [
    "RUBRIC",
    "DeepThinkResult",
    "EngineCall",
    "Trajectory",
    "plan",
    "run",
    "run_deep",
    "run_quick",
    "run_standard",
    "score_trajectories",
]

# Engines per role (Section 6.2 final engine map; CONVENTIONS.md §8 keys). Persona keys follow the same map.
GENERATOR_ENGINE = "gpt-oss-120b"  # Ren
ADVERSARY_ENGINE = "nemotron-3-super"  # Arthur
THIRD_OPINION_ENGINE = "qwen3.5-122b"  # Arthur, override: long documents
SYNTHESIS_ENGINE = APEX_KEY  # deepseek-v4-flash, deep only
GENERATOR_PERSONA = "ren"
ADVERSARY_PERSONA = "arthur"

GENERATOR_TEMPERATURE = 0.8  # ren.md sampling_temperature
ADVERSARY_TEMPERATURE = 0.15  # arthur.md "low-not-zero" (9.1 correction)
N_TRAJECTORIES = 3

# Rubric: named criteria, equal weights. TODO tune weights per task force once real Deep Think runs exist.
RUBRIC: tuple[tuple[str, str], ...] = (
    ("correctness", "Are the facts, figures and legal or technical claims right, or at least not wrong?"),
    ("completeness", "Does it cover what the problem actually asks, including the constraints stated?"),
    ("risk", "Are the downside cases named and handled, with the worst case stated plainly?"),
    ("actionability", "Can the Principal act on it tomorrow morning without asking a follow-up question?"),
    ("clarity", "Is it compressed, direct and free of padding, in the register the Principal expects?"),
)


class EngineCall(Protocol):
    """One generation on a named engine as a named persona. The orchestrator's implementation loads through the
    Engine Arbiter and holds the generation lock for the call (4.2 rules 2-4)."""

    def __call__(
        self, engine: str, persona: str, messages: Sequence[Mapping[str, str]], *, temperature: float, max_tokens: int
    ) -> str: ...


@dataclass
class Trajectory:
    index: int
    text: str
    engine: str
    scores: dict[str, int] = field(default_factory=dict)
    critique: str = ""

    @property
    def total(self) -> int:
        return sum(self.scores.values())


@dataclass
class DeepThinkResult:
    tier: str
    plan: DeepThinkPlan | None
    answer: str
    trajectories: list[Trajectory] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def duration_s(self) -> float:
        return (self.finished or time.time()) - self.started


# --- planning (rule 8) ------------------------------------------------------------------------------------------------


def plan(tier: str, arbiter: Arbiter, *, task_id: str | None = None) -> DeepThinkPlan:
    """Pre-request the full footprint; the Arbiter downgrades deep -> standard -> quick when it does not fit."""
    if tier not in DEEP_THINK_TIERS:
        raise ValueError(f"unknown Deep Think tier {tier!r}; Section 9.1: {DEEP_THINK_TIERS}")
    p = arbiter.plan_deep_think(tier, task_id=task_id)
    if p.downgraded:
        log.warning("deep think %s downgraded to %s: %s (Section 4.2 rule 8)", tier, p.granted, p.reason)
    return p


# --- prompts (TODO tune wording on the node) --------------------------------------------------------------------------


def generator_prompt(problem: str, n: int, context: str = "") -> list[dict[str, str]]:
    ctx = f"\n\nContext the Principal supplied:\n{context}" if context else ""
    return [
        {
            "role": "system",
            "content": (
                "You are the Generator in an adversarial optimisation. Produce ONE complete, self-contained answer "
                f"to the problem. This is trajectory {n}: it must differ materially in approach from the other "
                "trajectories, not "
                "in wording. Lead with the recommendation, then the reasoning, then the risks. No preamble."
            ),
        },
        {"role": "user", "content": f"Problem:\n{problem}{ctx}"},
    ]


def adversary_prompt(problem: str, trajectory: str, computed: str = "") -> list[dict[str, str]]:
    rubric = "\n".join(f"- {name}: {question}" for name, question in RUBRIC)
    comp = (
        (
            f"\n\nFigures computed independently (score arithmetic against these, never against the answer's own "
            f"numbers):\n{computed}"
        )
        if computed
        else ""
    )
    return [
        {
            "role": "system",
            "content": (
                "You are the Adversary. Attack the answer below against the problem. Score each criterion 0-100 as a "
                "judgement with a one-line justification; the score is a rubric judgement, not arithmetic. Then "
                "write the "
                "single most damaging critique. Reply with JSON only: "
                '{"scores": {"<criterion>": <0-100>, ...}, "justification": {"<criterion>": "<one line>"}, '
                '"critique": "<text>"}.\n\nCriteria:\n' + rubric
            ),
        },
        {"role": "user", "content": f"Problem:\n{problem}\n\nAnswer under review:\n{trajectory}{comp}"},
    ]


def refine_prompt(problem: str, winner: str, critique: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Generator, refining the winning answer. Repair every point of the critique that is right, "
                "reject in one line each point that is wrong, keep the structure: recommendation, reasoning, risks. "
                "Return the refined answer only."
            ),
        },
        {
            "role": "user",
            "content": f"Problem:\n{problem}\n\nWinning answer:\n{winner}\n\nAdversary critique:\n{critique}",
        },
    ]


def synthesis_prompt(problem: str, refined: str, third_opinion: str) -> list[dict[str, str]]:
    extra = f"\n\nThird opinion on the documents:\n{third_opinion}" if third_opinion else ""
    return [
        {
            "role": "system",
            "content": (
                "You deliver the final synthesis of a Deep Think. Reconcile the refined answer with any third opinion, "
                "state the decision the Principal must make in one line at the top, then the answer. Do not ask the "
                "Principal to do work; ask only for decisions or approvals."
            ),
        },
        {"role": "user", "content": f"Problem:\n{problem}\n\nRefined answer:\n{refined}{extra}"},
    ]


def third_opinion_prompt(problem: str, documents: str, refined: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You give a third opinion grounded ONLY in the documents supplied. Point at the passages that "
                "support or "
                "contradict the refined answer. Where the documents are silent, say so."
            ),
        },
        {"role": "user", "content": f"Problem:\n{problem}\n\nDocuments:\n{documents}\n\nRefined answer:\n{refined}"},
    ]


# --- scoring ---------------------------------------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_scores(text: str) -> tuple[dict[str, int], str]:
    """The adversary's JSON -> (scores by criterion, critique). Missing criteria score 0 and are logged."""
    m = _JSON_RE.search(text)
    data: dict[str, Any] = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = {}
    raw = data.get("scores") if isinstance(data, dict) else None
    scores: dict[str, int] = {}
    for name, _q in RUBRIC:
        v = raw.get(name) if isinstance(raw, Mapping) else None
        try:
            scores[name] = max(0, min(100, round(float(v))))
        except (TypeError, ValueError):
            scores[name] = 0
            log.warning("adversary gave no usable score for %r (0 assumed)", name)
    critique = str(data.get("critique") or "") if isinstance(data, dict) else ""
    if not critique:
        critique = text.strip()[:2000]
    return scores, critique


def score_trajectories(
    problem: str,
    trajectories: list[Trajectory],
    call: EngineCall,
    *,
    engine: str,
    persona: str,
    computed: str = "",
    result: DeepThinkResult | None = None,
    max_tokens: int = 1024,
) -> None:
    """One batched scoring phase on ONE engine (9.1: batched, never ping-pong). One JSON repair retry per item."""
    for t in trajectories:
        text = call(
            engine,
            persona,
            adversary_prompt(problem, t.text, computed),
            temperature=ADVERSARY_TEMPERATURE,
            max_tokens=max_tokens,
        )
        scores, critique = parse_scores(text)
        if sum(scores.values()) == 0 and not _JSON_RE.search(text):
            # TODO: a second-chance repair prompt; for now one literal retry with the same prompt.
            text = call(
                engine,
                persona,
                adversary_prompt(problem, t.text, computed),
                temperature=ADVERSARY_TEMPERATURE,
                max_tokens=max_tokens,
            )
            scores, critique = parse_scores(text)
        t.scores, t.critique = scores, critique
        if result is not None:
            result.log.append(f"scored trajectory {t.index} on {engine}: {t.total}/{100 * len(RUBRIC)}")


def _generate(
    problem: str,
    call: EngineCall,
    *,
    engine: str,
    persona: str,
    context: str,
    n: int,
    max_tokens: int,
    result: DeepThinkResult,
) -> list[Trajectory]:
    out: list[Trajectory] = []
    for i in range(1, n + 1):
        text = call(
            engine,
            persona,
            generator_prompt(problem, i, context),
            temperature=GENERATOR_TEMPERATURE,
            max_tokens=max_tokens,
        )
        out.append(Trajectory(index=i, text=text, engine=engine))
        result.log.append(f"generated trajectory {i} on {engine} ({len(text)} chars)")
    return out


def _winner(trajectories: list[Trajectory]) -> Trajectory:
    return max(trajectories, key=lambda t: (t.total, -t.index))


# --- the three shapes -------------------------------------------------------------------------------------------------


def run_quick(
    problem: str,
    call: EngineCall,
    *,
    engine: str,
    persona: str,
    context: str = "",
    computed: str = "",
    max_tokens: int = 2048,
) -> DeepThinkResult:
    """Generator and adversary on the same loaded engine with different role prompts (0 swaps)."""
    result = DeepThinkResult(tier="quick", plan=None, answer="")
    trajectories = _generate(
        problem, call, engine=engine, persona=persona, context=context, n=2, max_tokens=max_tokens, result=result
    )
    score_trajectories(problem, trajectories, call, engine=engine, persona=persona, computed=computed, result=result)
    best = _winner(trajectories)
    result.answer = call(
        engine,
        persona,
        refine_prompt(problem, best.text, best.critique),
        temperature=GENERATOR_TEMPERATURE,
        max_tokens=max_tokens,
    )
    result.trajectories = trajectories
    result.log.append(f"refined trajectory {best.index} on {engine}")
    result.finished = time.time()
    return result


def run_standard(
    problem: str,
    call: EngineCall,
    *,
    context: str = "",
    computed: str = "",
    max_tokens: int = 2048,
    generator_engine: str = GENERATOR_ENGINE,
    adversary_engine: str = ADVERSARY_ENGINE,
) -> DeepThinkResult:
    """Ren generates three trajectories; Arthur scores on his engine; Ren refines the winner (2 swaps, batched)."""
    result = DeepThinkResult(tier="standard", plan=None, answer="")
    trajectories = _generate(
        problem,
        call,
        engine=generator_engine,
        persona=GENERATOR_PERSONA,
        context=context,
        n=N_TRAJECTORIES,
        max_tokens=max_tokens,
        result=result,
    )
    score_trajectories(
        problem,
        trajectories,
        call,
        engine=adversary_engine,
        persona=ADVERSARY_PERSONA,
        computed=computed,
        result=result,
    )
    best = _winner(trajectories)
    result.answer = call(
        generator_engine,
        GENERATOR_PERSONA,
        refine_prompt(problem, best.text, best.critique),
        temperature=GENERATOR_TEMPERATURE,
        max_tokens=max_tokens,
    )
    result.trajectories = trajectories
    result.log.append(f"refined trajectory {best.index} on {generator_engine}")
    result.finished = time.time()
    return result


def run_deep(
    problem: str,
    call: EngineCall,
    *,
    context: str = "",
    computed: str = "",
    documents: str = "",
    max_tokens: int = 3072,
) -> DeepThinkResult:
    """Standard, then a second expansion and scoring round, Qwen3.5 as third opinion on documents, the Apex engine
    delivering the final synthesis (3-4 swaps)."""
    first = run_standard(problem, call, context=context, computed=computed, max_tokens=max_tokens)
    result = DeepThinkResult(
        tier="deep",
        plan=None,
        answer="",
        trajectories=list(first.trajectories),
        log=list(first.log),
        started=first.started,
    )
    # Second expansion: three new trajectories seeded with the refined answer as context, scored again.
    seeded = f"{context}\n\nBest answer so far (improve on it or beat it):\n{first.answer}".strip()
    second = _generate(
        problem,
        call,
        engine=GENERATOR_ENGINE,
        persona=GENERATOR_PERSONA,
        context=seeded,
        n=N_TRAJECTORIES,
        max_tokens=max_tokens,
        result=result,
    )
    for t in second:
        t.index += N_TRAJECTORIES
    score_trajectories(
        problem, second, call, engine=ADVERSARY_ENGINE, persona=ADVERSARY_PERSONA, computed=computed, result=result
    )
    baseline = Trajectory(
        index=0, text=first.answer, engine=GENERATOR_ENGINE, scores=dict.fromkeys((n for n, _ in RUBRIC), 0)
    )
    best = _winner([*second, baseline])
    refined = (
        best.text
        if best.index == 0
        else call(
            GENERATOR_ENGINE,
            GENERATOR_PERSONA,
            refine_prompt(problem, best.text, best.critique),
            temperature=GENERATOR_TEMPERATURE,
            max_tokens=max_tokens,
        )
    )
    result.trajectories += second
    third = ""
    if documents.strip():
        third = call(
            THIRD_OPINION_ENGINE,
            ADVERSARY_PERSONA,
            third_opinion_prompt(problem, documents, refined),
            temperature=ADVERSARY_TEMPERATURE,
            max_tokens=max_tokens,
        )
        result.log.append(f"third opinion on {THIRD_OPINION_ENGINE}")
    result.answer = call(
        SYNTHESIS_ENGINE,
        GENERATOR_PERSONA,
        synthesis_prompt(problem, refined, third),
        temperature=0.6,
        max_tokens=max_tokens,
    )
    result.log.append(f"final synthesis on {SYNTHESIS_ENGINE}")
    result.finished = time.time()
    return result


def run(
    granted_tier: str,
    problem: str,
    call: EngineCall,
    *,
    resident_engine: str | None = None,
    resident_persona: str = GENERATOR_PERSONA,
    context: str = "",
    computed: str = "",
    documents: str = "",
    compute_hook: Callable[[str], str] | None = None,
    plan_used: DeepThinkPlan | None = None,
) -> DeepThinkResult:
    """Dispatch on the tier the Arbiter granted. `compute_hook(problem) -> figures` is the Silas slot (9.1: where a
    real figure exists Silas computes it in the sandbox; the caller decides when to pass one)."""
    if compute_hook is not None and not computed:
        computed = compute_hook(problem)
    if granted_tier == "deep":
        res = run_deep(problem, call, context=context, computed=computed, documents=documents)
    elif granted_tier == "standard":
        res = run_standard(problem, call, context=context, computed=computed)
    elif granted_tier == "quick":
        engine = resident_engine or GENERATOR_ENGINE
        res = run_quick(problem, call, engine=engine, persona=resident_persona, context=context, computed=computed)
    else:
        raise ValueError(f"unknown granted tier {granted_tier!r}")
    res.plan = plan_used
    return res
