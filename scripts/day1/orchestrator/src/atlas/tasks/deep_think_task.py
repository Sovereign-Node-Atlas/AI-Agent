"""Deep Think as a Celery task on the gpu queue (Section 9.1, 9.7): the chat path runs Deep Think inline; a
background job (a task force marked heavy, a Sentinel escalation) runs it here. Every engine call goes through the
orchestrator's /internal/v1/chat/completions, which loads through the Engine Arbiter and holds the generation lock;
the plan itself is the orchestrator's (/internal/deep-think/plan), so the downgrade of rule 8 is honoured."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from celery import shared_task

from atlas import deep_think

log = logging.getLogger("atlas.tasks.deep_think")


@shared_task(name="atlas.tasks.deep_think", bind=True)
def deep_think_task(
    self: Any,
    problem: str,
    tier: str = "standard",
    context: str = "",
    documents: str = "",
    parent_task_id: str | None = None,
) -> dict[str, Any]:
    from atlas.tasks import OrchestratorClient, TaskRecord

    rec = TaskRecord(
        self.request.id,
        "deep-think",
        queue="gpu",
        parent_task_id=parent_task_id,
        payload={"tier": tier, "chars": len(problem)},
    )
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
                engine, list(messages), max_tokens=max_tokens, temperature=temperature, task_id=self.request.id
            )

        res = deep_think.run(
            granted, problem, call, resident_engine=plan.get("resident_engine"), context=context, documents=documents
        )
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        client.close()
    return rec.done(
        {
            "tier_requested": tier,
            "tier_granted": granted,
            "answer": res.answer,
            "log": res.log,
            "duration_s": res.duration_s,
        }
    )
