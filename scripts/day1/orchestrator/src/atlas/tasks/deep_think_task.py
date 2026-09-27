"""Deep Think as a Celery task on the gpu queue (Section 9.1, 9.7): the chat path runs Deep Think inline; a
background job (a task force marked heavy, a Sentinel escalation) runs it here. Every engine call goes through the
orchestrator's /internal/v1/chat/completions, which loads through the Engine Arbiter and takes its generation lock
for a weight-bearing engine (a resident small model only waits for a running generation, atlas.api._generation_lock);
the plan itself is the orchestrator's (/internal/deep-think/plan), so the downgrade of rule 8 is honoured.

Vault rule (10.5, fix round): a `session_id` that atlas.vault.SessionTags marks vault is REFUSED unless
`remember=True` (the Principal's explicit "remember this"), because the answer would otherwise persist outside the
vault. The Celery RESULT (Redis db 1, kept `result_expires` days) carries only a small summary; the answer and the
log go to the ledger row (the `result` column) and nowhere else."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from celery import shared_task

from atlas import deep_think
from atlas.vault import DEFAULT_SESSION_FILE, SessionTags

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
) -> dict[str, Any]:
    from atlas.tasks import OrchestratorClient, TaskRecord

    rec = TaskRecord(
        self.request.id,
        "deep-think",
        queue="gpu",
        parent_task_id=parent_task_id,
        payload={"tier": tier, "chars": len(problem), "session_id": session_id},
    )
    sessions = SessionTags(os.environ.get("VAULT_SESSION_FILE") or DEFAULT_SESSION_FILE)
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
    summary = {
        "tier_requested": tier,
        "tier_granted": granted,
        "chars": len(res.answer),
        "duration_s": res.duration_s,
        "task_id": self.request.id,
    }
    return rec.done({**summary, "answer": res.answer, "log": res.log}, returned=summary)
