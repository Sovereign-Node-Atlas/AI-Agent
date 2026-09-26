"""AEGIS Backup Protocol (Section 9.5; systemd/atlas-aegis.service; phase2/07-restic.sh; V13; D9 retention).

Division of labour (atlas-aegis.service header, CONVENTIONS.md §8 "AEGIS nightly is the same pattern"):
  * atlas-aegis.timer starts atlas-aegis.service as ROOT (the restic passphrase and /srv/backups are root-only).
  * ExecStartPre runs `atlas-admin enqueue aegis-freeze --wait 900` -> `atlas.tasks.aegis_freeze` here: pause the
    Celery queues (cancel_consumer on cpu and gpu: running tasks finish, nothing new starts), raise the freeze flag
    that atlas.memory honours (every writer in this package waits), and give ChromaDB and the LightRAG store a
    consistent moment (below). Returns a dict; the unit tolerates a failure so a backup still happens.
  * ExecStart runs restic itself (as root); ExecStopPost enqueues `aegis-thaw` -> `atlas.tasks.aegis_thaw`: flag
    down, consumers back.
  * The manual `[EXECUTE AEGIS BACKUP]` trigger (9.5) is `atlas.tasks.aegis_manual_backup`, which runs
    `sudo -n systemctl start atlas-aegis.service` under /etc/sudoers.d/atlas-aegis (plain sudo, conflict 7): the same
    unit, the same freeze/thaw, the same include set.
  * `restic_backup_command()` types the backup line for a caller that IS root (the include file path is checked, the
    D9 retention is `forget --keep-daily 30 --keep-monthly 12 --prune`). The task text names
    /etc/atlas/restic.include; phase2/07-restic.sh writes /etc/atlas/restic-include.txt: both are looked for, in that
    order, and a missing include file stops the run (never an empty backup).

"Consistent snapshot" (9.5 Freeze row), honestly: ChromaDB 1.x's Rust server has no documented snapshot/checkpoint
API (services-tools.md §2.1 lists the config; UNVERIFIED that any exists), and its persist dir is root-owned. What
this task can do and does: stop every writer of this package (queues paused, freeze flag), let in-flight requests
drain for AEGIS_SETTLE_S seconds, and record in the ledger that the store was quiescent from this side. LightRAG's
default storages persist to files under WORKING_DIR on each insert, so a paused writer set is a consistent set.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from celery import shared_task

log = logging.getLogger("atlas.aegis")

DEFAULT_FREEZE_FLAG = "/run/atlas/aegis-freeze"
INCLUDE_CANDIDATES: tuple[str, ...] = ("/etc/atlas/restic.include", "/etc/atlas/restic-include.txt")
EXCLUDE_FILE = "/etc/atlas/restic-exclude.txt"
KEEP_DAILY, KEEP_MONTHLY = 30, 12  # D9
QUEUES: tuple[str, ...] = ("cpu", "gpu")
AEGIS_UNIT = "atlas-aegis.service"

__all__ = [
    "aegis_freeze",
    "aegis_manual_backup",
    "aegis_thaw",
    "freeze",
    "restic_backup_command",
    "restic_forget_command",
    "thaw",
    "trigger_unit",
]


def _flag_path(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    return Path(env.get("AEGIS_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG)


def include_file(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    explicit = env.get("RESTIC_INCLUDE_FILE")
    candidates = [explicit] if explicit else list(INCLUDE_CANDIDATES)
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise FileNotFoundError(
        f"no restic include file at {candidates} (phase2/07-restic.sh writes it); refusing to back up an empty set"
    )


def restic_backup_command(env: dict[str, str] | None = None) -> list[str]:
    inc = include_file(env)
    cmd = ["restic", "backup", "--files-from", str(inc), "--exclude-caches", "--one-file-system", "--tag", "aegis"]
    if Path(EXCLUDE_FILE).is_file():
        cmd += ["--exclude-file", EXCLUDE_FILE]
    return cmd


def restic_forget_command() -> list[str]:
    return ["restic", "forget", "--keep-daily", str(KEEP_DAILY), "--keep-monthly", str(KEEP_MONTHLY), "--prune"]


def freeze(
    *,
    control: Any | None = None,
    queues: Sequence[str] = QUEUES,
    settle_s: float | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Pause consumers, raise the flag, settle. `control` is celery's app.control (injected for tests)."""
    env = dict(os.environ if env is None else env)
    flag = _flag_path(env)
    paused: dict[str, Any] = {}
    if control is not None:
        for q in queues:
            try:
                paused[q] = control.cancel_consumer(q, reply=True, timeout=10)  # VERIFIED celery control command
            except Exception as exc:
                paused[q] = f"error: {exc}"
                log.error("aegis freeze: cancel_consumer(%s) failed: %s", q, exc)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(f"{time.time()}\n", encoding="utf-8")
    settle = float(env.get("AEGIS_SETTLE_S") or 10) if settle_s is None else settle_s
    if settle > 0:
        time.sleep(settle)
    log.info("aegis freeze: flag %s raised, consumers paused on %s, settled %.0fs", flag, ",".join(queues), settle)
    return {
        "flag": str(flag),
        "paused": paused,
        "settle_s": settle,
        "frozen_at": time.time(),
        "note": "chromadb has no snapshot API (UNVERIFIED); writers of this package are quiescent",
    }


def thaw(
    *, control: Any | None = None, queues: Sequence[str] = QUEUES, env: dict[str, str] | None = None
) -> dict[str, Any]:
    flag = _flag_path(env)
    existed = flag.exists()
    try:
        flag.unlink()
    except FileNotFoundError:
        pass
    resumed: dict[str, Any] = {}
    if control is not None:
        for q in queues:
            try:
                resumed[q] = control.add_consumer(q, reply=True, timeout=10)  # VERIFIED celery control command
            except Exception as exc:
                resumed[q] = f"error: {exc}"
                log.error("aegis thaw: add_consumer(%s) failed: %s", q, exc)
    log.info(
        "aegis thaw: flag %s removed (was %s), consumers resumed on %s",
        flag,
        "present" if existed else "absent",
        ",".join(queues),
    )
    return {"flag": str(flag), "was_frozen": existed, "resumed": resumed, "thawed_at": time.time()}


def trigger_unit(unit: str = AEGIS_UNIT, *, runner: Any = subprocess.run) -> dict[str, Any]:
    """The manual trigger: `sudo -n systemctl start atlas-aegis.service` (/etc/sudoers.d/atlas-aegis)."""
    cmd = ["sudo", "-n", "systemctl", "start", unit]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{' '.join(cmd)} could not run: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]} "
            "(is /etc/sudoers.d/atlas-aegis installed by phase2/07-restic.sh?)"
        )
    return {"unit": unit, "started": True, "command": " ".join(cmd)}


# --- Celery tasks -----------------------------------------------------------------------------------------------------


@shared_task(name="atlas.tasks.aegis_freeze", bind=True)
def aegis_freeze(self: Any) -> dict[str, Any]:
    from atlas.celery_app import app
    from atlas.tasks import TaskRecord

    rec = TaskRecord(self.request.id, "aegis-freeze")
    try:
        out = freeze(control=app.control)
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    return rec.done(out)


@shared_task(name="atlas.tasks.aegis_thaw", bind=True)
def aegis_thaw(self: Any) -> dict[str, Any]:
    from atlas.celery_app import app
    from atlas.tasks import TaskRecord

    rec = TaskRecord(self.request.id, "aegis-thaw")
    try:
        out = thaw(control=app.control)
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    return rec.done(out)


@shared_task(name="atlas.tasks.aegis_manual_backup", bind=True)
def aegis_manual_backup(self: Any, requested_by: str = "principal") -> dict[str, Any]:
    from atlas.tasks import TaskRecord, notify

    rec = TaskRecord(self.request.id, "aegis-manual", payload={"requested_by": requested_by})
    try:
        out = trigger_unit()
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        notify(f"AEGIS manual backup could not start: {exc}", title="ATLAS AEGIS", priority="high", tags=["warning"])
        raise
    notify("AEGIS backup started (manual trigger); the unit reports on completion in the journal.", title="ATLAS AEGIS")
    return rec.done(out)
