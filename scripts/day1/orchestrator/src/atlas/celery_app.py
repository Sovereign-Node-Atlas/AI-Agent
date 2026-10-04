"""The Celery Shadow Broker (Section 9.7; systemd/atlas-celery-*.service; phase2/02-orchestrator.sh contract).

    app                     the Celery app the units run: `celery -A atlas.celery_app worker -Q cpu|gpu`, `... beat`
    broker                  redis://127.0.0.1:6379/0 (CELERY_BROKER_URL), results redis://127.0.0.1:6379/1
    queues                  `cpu` (task_default_queue, many workers) and `gpu` (exactly one worker, unit-enforced)
    task_routes             every task that needs an engine goes to `gpu`; it still asks the Engine Arbiter (through
                            the orchestrator's loopback API) before anything loads (4.2 rule 2, C15)
    beat_schedule           ONLY package-owned schedules: chat-retention nightly (D9). Sentinel (hourly), the 72-hour
                            prune and AEGIS nightly are systemd timers that run `atlas-admin enqueue <task>`
                            (CONVENTIONS.md §8 last paragraph; atlas-celery-beat.service header). Never duplicated here.

Task names are the contract with atlas.admin.ENQUEUE_TASKS (that file states it): atlas.tasks.sentinel_pulse,
atlas.tasks.prune_sweep, atlas.tasks.aegis_freeze, atlas.tasks.aegis_thaw, atlas.tasks.chat_retention.
Facts typed from services-tools.md §3 (VERIFIED celery docs): broker_url, result_backend, broker_transport_options
visibility_timeout ("raise it above the longest task"; set to 24 h here, see the conf comment: a task longer than
that must be split), task_routes, task_default_queue, crontab(), `-s` schedule file.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from celery import Celery
from celery.schedules import crontab

log = logging.getLogger("atlas.celery_app")

BROKER_URL = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or "redis://127.0.0.1:6379/1"
ZONEINFO_DIR = "/usr/share/zoneinfo"


def resolve_timezone(
    env: Mapping[str, str] | None = None,
    *,
    etc_timezone: str | Path = "/etc/timezone",
    localtime: str | Path = "/etc/localtime",
) -> str:
    """The zone the beat crontab runs in (fix round 4). The nightly chat-retention (04:15) is placed after the AEGIS
    timer (02:30), and systemd timers fire in the NODE's local time, so beat must use that same zone: TZ when the units
    carry it (phase2/02-orchestrator.sh mirrors TZ from atlas.env into orchestrator.env, which every Celery unit
    loads), else the system's own setting (/etc/timezone, then the /etc/localtime symlink under /usr/share/zoneinfo),
    else UTC with a warning in the log. Never a hard-coded city: a wrong zone could put the retention run before the
    backup, and a hard-coded one was not "auto-detection" (CONVENTIONS.md §3)."""
    env = dict(os.environ if env is None else env)
    tz = (env.get("TZ") or "").strip()
    if tz and not tz.startswith(":"):
        return tz
    try:
        text = Path(etc_timezone).read_text(encoding="utf-8").strip()
        if (text and "/" in text) or text == "UTC":
            return text
    except OSError:
        pass
    try:
        target = Path(localtime).resolve(strict=True)
        parts = target.parts
        if "zoneinfo" in parts:
            zone = "/".join(parts[parts.index("zoneinfo") + 1 :])
            if zone:
                return zone
    except OSError:
        pass
    log.warning(
        "no time zone found (TZ unset, %s and %s unusable); Celery beat runs in UTC: the 04:15 chat-retention may not "
        "follow the node-local 02:30 AEGIS timer (set TZ in /etc/atlas/atlas.env)",
        etc_timezone,
        localtime,
    )
    return "UTC"


TIMEZONE = resolve_timezone()

QUEUE_CPU = "cpu"
QUEUE_GPU = "gpu"

# Names (contract with atlas.admin; the module that defines each is in `include`).
TASK_SENTINEL_PULSE = "atlas.tasks.sentinel_pulse"
TASK_SENTINEL_BLUF = "atlas.tasks.sentinel_bluf"
TASK_PRUNE_SWEEP = "atlas.tasks.prune_sweep"
TASK_AEGIS_FREEZE = "atlas.tasks.aegis_freeze"
TASK_AEGIS_THAW = "atlas.tasks.aegis_thaw"
TASK_AEGIS_MANUAL = "atlas.tasks.aegis_manual_backup"
TASK_CHAT_RETENTION = "atlas.tasks.chat_retention"
TASK_RECORD_STRIKE = "atlas.tasks.record_strike"
TASK_DEEP_THINK = "atlas.tasks.deep_think"

GPU_TASKS: tuple[str, ...] = (TASK_SENTINEL_BLUF, TASK_CHAT_RETENTION, TASK_DEEP_THINK)

app = Celery(
    "atlas",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[
        "atlas.tasks.sentinel",
        "atlas.tasks.prune",
        "atlas.tasks.aegis",
        "atlas.tasks.ouroboros",
        "atlas.tasks.retention",
        "atlas.tasks.deep_think_task",
    ],
)

app.conf.update(
    task_default_queue=QUEUE_CPU,
    task_routes={name: {"queue": QUEUE_GPU} for name in GPU_TASKS},
    # "raise it above the longest task" (services-tools.md §3): with task_acks_late=True a task that outlives this
    # window is REDELIVERED by Redis and runs a second time, i.e. two engine holders for one job on the gpu queue.
    # 9.7 names a multi-hour backtest and a LightRAG indexing pass; 9.1 says a heavy Deep Think "runs as long as it
    # needs", so 6 h was not above the longest task (fix round 2). 24 h covers every uncapped job this package knows;
    # a task expected to exceed 24 h must be split into resumable parts, never run as one Celery task.
    broker_transport_options={"visibility_timeout": 24 * 3600},
    result_expires=7 * 24 * 3600,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=TIMEZONE,
    enable_utc=True,
    # A worker that lost Redis retries the connection instead of dying (the units also Restart=always).
    broker_connection_retry_on_startup=True,
    beat_schedule={
        # D9 / Section 10.4: chats older than 90 days are summarised into the Vector Cortex and deleted, nightly.
        # 04:15 sits after the AEGIS window (atlas-aegis.timer 02:30) so the summaries land in the next backup.
        "chat-retention-nightly": {
            "task": TASK_CHAT_RETENTION,
            "schedule": crontab(minute=15, hour=4),
            "options": {"queue": QUEUE_GPU},
        },
    },
)

__all__ = [
    "GPU_TASKS",
    "QUEUE_CPU",
    "QUEUE_GPU",
    "TASK_AEGIS_FREEZE",
    "TASK_AEGIS_MANUAL",
    "TASK_AEGIS_THAW",
    "TASK_CHAT_RETENTION",
    "TASK_DEEP_THINK",
    "TASK_PRUNE_SWEEP",
    "TASK_RECORD_STRIKE",
    "TASK_SENTINEL_BLUF",
    "TASK_SENTINEL_PULSE",
    "TIMEZONE",
    "app",
    "resolve_timezone",
]
