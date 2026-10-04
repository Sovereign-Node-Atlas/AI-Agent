"""Deep Think as a Celery task on the gpu queue (Section 9.1, 9.7): the chat path runs the QUICK tier inline and
enqueues standard/deep here (atlas.api._enqueue_deep_think, fix round 2: 9.7 "the chat interface returns to the
Principal immediately"); a background job (a task force marked heavy, a Sentinel escalation) runs here too. Every
engine call goes through the orchestrator's /internal/v1/chat/completions, which loads through the Engine Arbiter and
holds the orchestrator's one generation slot (atlas.api.GenerationSlot: every generation, resident small models
included); the plan itself is the orchestrator's (/internal/deep-think/plan), so the downgrade of rule 8 is honoured.

The final answer goes through the never-delegate rewrite (16.1 rule 5; governance.never_delegate_rewrite) with the
PRINCIPAL register before it is stored when `audience` is "principal" (the chat path's default): the Principal reads
the stored answer, so nothing task-shaped may be in it. Intermediate calls (generator, adversary, refine) are not
rewritten: they are the engines talking to each other. When done, ntfy tells the Principal where the answer is.

`hemisphere` (fix round 4) is the chat's hemisphere, filled by atlas.api._enqueue_deep_think from the router's decision
and REQUIRED (one of HEMISPHERES, else the task fails before any generation): every /internal generation this task
makes declares it, so a failed call's scar is bound to that hemisphere and never carries the Principal's problem into
the other hemisphere's prompts (7.3, 10.1).

Vault rule (10.5, fix round): a `session_id` that atlas.vault.SessionTags marks vault is REFUSED unless
`remember=True` (the Principal's explicit "remember this"), because the answer would otherwise persist outside the
vault (the chat path runs such a session inline instead). The Celery RESULT (Redis db 1, kept `result_expires` days)
carries only a small summary; the answer and the log go to the ledger row (the `result` column) and nowhere else."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from celery import shared_task

from atlas import deep_think
from atlas.config import HEMISPHERES
from atlas.governance import never_delegate_rewrite
from atlas.vault import SessionTags

log = logging.getLogger("atlas.tasks.deep_think")


@shared_task(name="atlas.tasks.deep_think", bind=True)
def deep_think_task(
    self: Any,
    problem: str,
    tier: str = "standard",
    context: str = "",
    documents: str = "",
    parent_task_id: str | None = None,
    session_id: str | None = None,
    remember: bool = False,
    audience: str = "principal",
    hemisphere: str | None = None,
) -> dict[str, Any]:
    from atlas.tasks import OrchestratorClient, TaskRecord, notify

    rec = TaskRecord(
        self.request.id,
        "deep-think",
        queue="gpu",
        parent_task_id=parent_task_id,
        payload={
            "tier": tier,
            "chars": len(problem),
            "session_id": session_id,
            "audience": audience,
            "hemisphere": hemisphere,
        },
    )
    if hemisphere not in HEMISPHERES:
        msg = (
            f"hemisphere={hemisphere!r} is not one of {sorted(HEMISPHERES)}: a Deep Think must declare the chat's "
            "hemisphere for every generation it makes (module docstring; atlas.api._enqueue_deep_think fills it)"
        )
        rec.failed(msg)
        raise RuntimeError(msg)
    sessions = SessionTags.from_env()
    if sessions.is_vault(session_id) and not remember:
        msg = (
            f"session {session_id} is vault-tagged: a Deep Think answer would persist in the ledger and the result "
            "backend outside the vault (Section 10.5); refused unless remember=True"
        )
        rec.failed(msg)
        raise RuntimeError(msg)
    client = OrchestratorClient()
    try:
        r = client._http.post("/internal/deep-think/plan", json={"tier": tier, "task_id": self.request.id})
        if r.status_code >= 400:
            raise RuntimeError(f"deep-think plan -> HTTP {r.status_code}: {r.text[:200]}")
        plan = r.json()
        granted = str(plan.get("granted") or "quick")

        def call(
            engine: str, persona: str, messages: Sequence[Mapping[str, str]], *, temperature: float, max_tokens: int
        ) -> str:
            return client.generate(
                engine,
                list(messages),
                hemisphere=hemisphere,
                max_tokens=max_tokens,
                temperature=temperature,
                task_id=self.request.id,
            )

        res = deep_think.run(
            granted, problem, call, resident_engine=plan.get("resident_engine"), context=context, documents=documents
        )
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        client.close()
    answer = res.answer
    governance: dict[str, Any] | None = None
    if audience.strip().lower() == "principal":
        rw = never_delegate_rewrite(answer, "principal")  # 16.1 rule 5 before anything the Principal reads is stored
        answer = rw.text
        governance = {"register": "principal", "rewrites": len(rw.rewrites), "flagged": list(rw.flagged)[:20]}
        if rw.flagged:
            log.warning("deep think %s: never-delegate pass flagged %d sentence(s)", self.request.id, len(rw.flagged))
    summary = {
        "tier_requested": tier,
        "tier_granted": granted,
        "chars": len(answer),
        "duration_s": res.duration_s,
        "task_id": self.request.id,
        "governance": governance,
    }
    out = rec.done({**summary, "answer": answer, "log": res.log}, returned=summary)
    notify(
        f"Deep Think ({granted}) finished in {res.duration_s:.0f}s: {len(answer)} chars. The answer is on ledger "
        f"task {self.request.id} (tasks.result_json).",
        title="ATLAS Deep Think",
    )
    return out
