"""AEGIS Backup Protocol (Section 9.5; systemd/atlas-aegis.service; phase2/07-restic.sh; V13; D9 retention).

Division of labour (atlas-aegis.service header, CONVENTIONS.md §8 "AEGIS nightly is the same pattern"):
  * atlas-aegis.timer starts atlas-aegis.service as ROOT (the restic passphrase and /srv/backups are root-only).
  * ExecStartPre runs `atlas-admin enqueue aegis-freeze --wait 900` -> `atlas.tasks.aegis_freeze` here: pause BOTH
    consumers, `cpu` and `gpu` (9.5 Freeze row: "the orchestrator pauses the Celery queues"; cancel_consumer: the
    running task finishes, nothing new starts, so no Sentinel pulse file, Docling/graph work or sandbox run lands in
    the restic include set mid-snapshot), raise the freeze flag that atlas.memory honours (every writer in this
    package spools or waits), and give ChromaDB and the LightRAG store a consistent moment (below). Returns a dict;
    the unit (its helper) fails the backup when the freeze fails while the orchestrator is up.
    HOW THE THAW STAYS DELIVERABLE with `cpu` cancelled (fix round 2; fix round 1 had left cpu running for this):
    systemd/atlas-aegis.service's `finish` step re-adds both consumers over celery's BROADCAST channel (`celery control
    add_consumer cpu|gpu`), which a worker still reads while its task queues are cancelled, and only then enqueues
    `aegis-thaw`; a worker that does not answer is restarted and the unit is reported FAILED. That is the unit's stated
    contract ("Contract with the `atlas` package"), and this task's thaw() re-adds both consumers again, harmlessly.
  * ExecStart runs restic itself (as root); ExecStopPost enqueues `aegis-thaw` -> `atlas.tasks.aegis_thaw`: flag
    down, consumers back, and the memory spool (writes deferred during the freeze) replayed.
  * The manual `[EXECUTE AEGIS BACKUP]` trigger (9.5) is `atlas.tasks.aegis_manual_backup`, which runs
    `sudo -n systemctl start --no-block atlas-aegis.service` under /etc/sudoers.d/atlas-aegis (plain sudo, conflict
    7; the fragment permits `start` and `start --no-block`): the same unit, the same freeze/thaw, the same include
    set. `--no-block` because `systemctl start` of a Type=oneshot blocks until ExecStart finishes (restic runs minutes
    to hours; TimeoutStartSec=6h): the task returns once `systemctl is-active` reports activating/active, and reports
    failure only when the unit is neither (fix round: a blocking start that timed out was a false failure).
  * `restic_backup_command()` types the backup line for a caller that IS root (the include file path is checked, the
    D9 retention is `forget --keep-daily 30 --keep-monthly 12 --prune`). The task text names
    /etc/atlas/restic.include; phase2/07-restic.sh writes /etc/atlas/restic-include.txt: both are looked for, in that
    order, and a missing include file stops the run (never an empty backup). The exclude file is required too, and
    the vault's plaintext mount (VAULT_MOUNT_DIR, vault.env) is excluded explicitly on top of it: an open vault's
    plaintext must never enter a snapshot (Section 11), whatever the exclude file says.

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
DEFAULT_VAULT_MOUNT_DIR = "/srv/atlas/vault/open"  # atlas.vault.DEFAULT_MOUNT_DIR; vault.env VAULT_MOUNT_DIR
KEEP_DAILY, KEEP_MONTHLY = 30, 12  # D9
# Both queues pause (9.5). The thaw travels on `cpu` (admin.py ENQUEUE_TASKS + celery_app task_default_queue) and is
# deliverable because atlas-aegis.service re-adds the consumers over the broadcast channel before enqueueing it.
FREEZE_QUEUES: tuple[str, ...] = ("cpu", "gpu")
THAW_QUEUES: tuple[str, ...] = ("cpu", "gpu")  # re-adding an already-consumed queue is a no-op reply
THAW_QUEUE = "cpu"  # where atlas.tasks.aegis_thaw is delivered (the unit's broadcast add_consumer precedes it)
QUEUES = FREEZE_QUEUES  # kept name: what freeze() pauses
AEGIS_UNIT = "atlas-aegis.service"
START_CONFIRM_S = 30.0  # how long trigger_unit waits for systemd to report the unit activating/active

__all__ = [
    "FREEZE_QUEUES",
    "THAW_QUEUE",
    "THAW_QUEUES",
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


def exclude_file(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    path = Path(env.get("RESTIC_EXCLUDE_FILE") or EXCLUDE_FILE)
    if not path.is_file():
        raise FileNotFoundError(
            f"no restic exclude file at {path} (phase2/07-restic.sh writes it: secrets, weights, the vault mount); "
            "refusing to back up an unfiltered include set"
        )
    return path


def restic_backup_command(env: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ if env is None else env)
    inc = include_file(env)
    exc = exclude_file(env)
    # The plaintext view of the vault is excluded here as well as in the exclude file (Section 11: ciphertext only).
    mount = (env.get("VAULT_MOUNT_DIR") or DEFAULT_VAULT_MOUNT_DIR).rstrip("/") or DEFAULT_VAULT_MOUNT_DIR
    return [
        "restic",
        "backup",
        "--files-from",
        str(inc),
        "--exclude-file",
        str(exc),
        "--exclude",
        mount,
        "--exclude-caches",
        "--one-file-system",
        "--tag",
        "aegis",
    ]


def restic_forget_command() -> list[str]:
    return ["restic", "forget", "--keep-daily", str(KEEP_DAILY), "--keep-monthly", str(KEEP_MONTHLY), "--prune"]


def freeze(
    *,
    control: Any | None = None,
    queues: Sequence[str] = FREEZE_QUEUES,
    settle_s: float | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Pause consumers (both queues, 9.5), raise the flag, settle. `control` is celery's app.control (injected for
    tests). The thaw's deliverability is the unit's broadcast add_consumer (module docstring), not an exemption here.
    """
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
    *,
    control: Any | None = None,
    queues: Sequence[str] = THAW_QUEUES,
    env: dict[str, str] | None = None,
    memory: Any | None = None,
) -> dict[str, Any]:
    """Flag down, consumers back, then replay the writes atlas.memory spooled while the flag was up (9.5 "then
    writes resume"). `memory` is an atlas.memory.MemoryStore (built by the task; injected in tests)."""
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
    replay: dict[str, Any] = {"replayed": 0, "failed": 0, "skipped": "no memory store"}
    if memory is not None:
        try:
            replay = memory.replay_spool()
        except Exception as exc:  # the thaw itself succeeded; a replay failure is reported, not hidden
            replay = {"replayed": 0, "failed": -1, "error": f"{type(exc).__name__}: {exc}"}
            log.error("aegis thaw: spool replay failed: %s", exc)
    log.info(
        "aegis thaw: flag %s removed (was %s), consumers resumed on %s, spool %s",
        flag,
        "present" if existed else "absent",
        ",".join(queues),
        replay,
    )
    return {"flag": str(flag), "was_frozen": existed, "resumed": resumed, "thawed_at": time.time(), "spool": replay}


def unit_state(unit: str, *, runner: Any = subprocess.run) -> str:
    """`systemctl is-active <unit>` (no sudo needed): active | activating | inactive | failed | deactivating | ..."""
    try:
        proc = runner(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unknown ({exc})"
    return (proc.stdout or "").strip() or "unknown"


def trigger_unit(
    unit: str = AEGIS_UNIT,
    *,
    runner: Any = subprocess.run,
    sleep: Any = time.sleep,
    confirm_s: float = START_CONFIRM_S,
) -> dict[str, Any]:
    """The manual trigger: `sudo -n systemctl start --no-block atlas-aegis.service` (/etc/sudoers.d/atlas-aegis),
    then `systemctl is-active` until the unit reports activating/active. A blocking start that times out is treated
    as "started, still running" and confirmed the same way; failure is reported only when the unit is not
    active/activating afterwards (rule §7.4: never a false failure)."""
    cmd = ["sudo", "-n", "systemctl", "start", "--no-block", unit]
    timed_out = False
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True  # a Type=oneshot start that blocked: the unit is running; confirm below
        proc = None
    except OSError as exc:
        raise RuntimeError(f"{' '.join(cmd)} could not run: {exc}") from exc
    if proc is not None and proc.returncode != 0:
        state = unit_state(unit, runner=runner)
        if state not in ("active", "activating"):
            raise RuntimeError(
                f"{' '.join(cmd)} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]} "
                f"(unit is {state}; is /etc/sudoers.d/atlas-aegis installed by phase2/07-restic.sh, with the "
                "`start --no-block` line?)"
            )
    deadline = time.monotonic() + confirm_s
    state = unit_state(unit, runner=runner)
    while state not in ("active", "activating") and time.monotonic() < deadline:
        sleep(1.0)
        state = unit_state(unit, runner=runner)
    if state not in ("active", "activating"):
        raise RuntimeError(
            f"{unit} is {state} after `{' '.join(cmd)}` (StartLimitBurst=2 per 6 h reached, or the unit failed at "
            "once: journalctl -u atlas-aegis.service)"
        )
    return {"unit": unit, "started": True, "state": state, "command": " ".join(cmd), "start_timed_out": timed_out}


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
        memory = None
        try:
            from atlas.memory import build_memory_store

            memory = build_memory_store(with_graph=False)
        except Exception as exc:  # no memory store yet (before Phase 2 step 4): nothing spooled, nothing to replay
            log.warning("aegis thaw: memory store not available (%s); no spool replay", exc)
        out = thaw(control=app.control, memory=memory)
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
    notify(
        f"AEGIS backup started (manual trigger; unit {out['state']}); the unit reports on completion in the journal.",
        title="ATLAS AEGIS",
    )
    return rec.done(out)
