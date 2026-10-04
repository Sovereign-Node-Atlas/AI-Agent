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
  * The manual `[EXECUTE AEGIS BACKUP]` trigger (9.5) is `atlas.tasks.aegis_manual_backup`, which creates the EMPTY
    file /run/atlas/aegis-request (AEGIS_REQUEST_FILE) as atlas and nothing else: systemd/atlas-aegis-trigger.path
    (PathExists=) starts atlas-aegis.service, whose first ExecStartPre removes the request, and the task confirms with
    `systemctl is-active atlas-aegis.service` (activating|active within START_CONFIRM_S). NO sudo (fix round 4): the
    earlier `sudo -n systemctl start --no-block` route and its sudoers fragment are gone; /etc/sudoers.d/atlas-engines
    is the only NOPASSWD grant of the atlas account (CONVENTIONS.md §8), phase2/07-restic.sh removes a stale fragment,
    proves sudo refuses the unit, and asserts statically that this module names the request file and builds no sudo
    command. The same unit, the same freeze/thaw, the same include set; it never prunes (atlas-aegis-forget.service is
    the nightly timer's alone, 16.3 item 5). StartLimitBurst=3 per 6 h on the unit: when it is hit the path unit fails
    with unit-start-limit-hit and the error text names the re-arm command.
  * `restic_backup_command()` types the backup line for a caller that IS root (the include file path is checked);
    `restic_forget_command()` types the D9 retention line EXACTLY as systemd/atlas-aegis-forget.service runs it
    (`forget --keep-within 1d --keep-daily 30 --keep-monthly 12 --prune`; that unit is the authoritative copy and the
    only place a backup is ever deleted, 16.3 item 5). The task text names
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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from celery import shared_task

log = logging.getLogger("atlas.aegis")

DEFAULT_FREEZE_FLAG = "/run/atlas/aegis-freeze"
# The manual trigger (AEGIS_REQUEST_FILE): systemd/atlas-aegis-trigger.path watches exactly this path; /run/atlas is
# atlas-orchestrator.service's RuntimeDirectory (atlas:atlas), so the Celery worker (user atlas) may create the file.
DEFAULT_REQUEST_FILE = "/run/atlas/aegis-request"
INCLUDE_CANDIDATES: tuple[str, ...] = ("/etc/atlas/restic.include", "/etc/atlas/restic-include.txt")
EXCLUDE_FILE = "/etc/atlas/restic-exclude.txt"
DEFAULT_VAULT_MOUNT_DIR = "/srv/atlas/vault/open"  # atlas.vault.DEFAULT_MOUNT_DIR; vault.env VAULT_MOUNT_DIR
KEEP_DAILY, KEEP_MONTHLY = 30, 12  # D9
KEEP_WITHIN = "1d"  # atlas-aegis-forget.service's floor: a second snapshot on one day never deletes the earlier one
# Both queues pause (9.5). The thaw travels on `cpu` (admin.py ENQUEUE_TASKS + celery_app task_default_queue) and is
# deliverable because atlas-aegis.service re-adds the consumers over the broadcast channel before enqueueing it.
FREEZE_QUEUES: tuple[str, ...] = ("cpu", "gpu")
THAW_QUEUES: tuple[str, ...] = ("cpu", "gpu")  # re-adding an already-consumed queue is a no-op reply
THAW_QUEUE = "cpu"  # where atlas.tasks.aegis_thaw is delivered (the unit's broadcast add_consumer precedes it)
QUEUES = FREEZE_QUEUES  # kept name: what freeze() pauses
AEGIS_UNIT = "atlas-aegis.service"
START_CONFIRM_S = 30.0  # how long trigger_unit waits for systemd to report the unit activating/active
REARM_COMMAND = (
    "systemctl reset-failed atlas-aegis.service atlas-aegis-trigger.path && systemctl start atlas-aegis-trigger.path"
)

__all__ = [
    "DEFAULT_REQUEST_FILE",
    "FREEZE_QUEUES",
    "REARM_COMMAND",
    "THAW_QUEUE",
    "THAW_QUEUES",
    "aegis_freeze",
    "aegis_manual_backup",
    "aegis_thaw",
    "freeze",
    "request_file",
    "restic_backup_command",
    "restic_forget_command",
    "thaw",
    "trigger_unit",
]


def _flag_path(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    return Path(env.get("AEGIS_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG)


def request_file(env: Mapping[str, str] | None = None) -> Path:
    """The path atlas-aegis-trigger.path watches (AEGIS_REQUEST_FILE in orchestrator.env, else the unit's literal)."""
    env = dict(os.environ if env is None else env)
    return Path(env.get("AEGIS_REQUEST_FILE") or DEFAULT_REQUEST_FILE)


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
    """The D9 retention line, typed exactly as systemd/atlas-aegis-forget.service's ExecStart runs it (the authoritative
    copy; only the nightly timer runs it, 16.3 item 5). Nothing in this package executes it: it is here so the two typed
    versions of the retention line agree (CONVENTIONS.md §8) and a test can hold them together."""
    return [
        "restic",
        "forget",
        "--keep-within",
        KEEP_WITHIN,
        "--keep-daily",
        str(KEEP_DAILY),
        "--keep-monthly",
        str(KEEP_MONTHLY),
        "--prune",
    ]


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


def _create_request(path: Path) -> None:
    """Create the empty request file as this process's user (atlas). O_NOFOLLOW: /run/atlas is atlas-writable, and a
    planted symlink there must never be followed (the unit's own `rm -f` never follows one either). The file is 0640:
    PID 1 only needs it to exist."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create {path.parent} for the AEGIS request ({exc}); /run/atlas is atlas-orchestrator.service's "
            "RuntimeDirectory (atlas:atlas): is the orchestrator running?"
        ) from exc
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create the AEGIS request file {path} ({exc}); it must be creatable by the atlas account "
            "(phase2/07-restic.sh proves this once on Day 1)"
        ) from exc
    os.close(fd)


def trigger_unit(
    unit: str = AEGIS_UNIT,
    *,
    runner: Any = subprocess.run,
    sleep: Any = time.sleep,
    confirm_s: float = START_CONFIRM_S,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The manual trigger (Section 9.5) WITHOUT sudo (fix round 4; CONVENTIONS.md §8): create the empty request file
    that systemd/atlas-aegis-trigger.path watches (PathExists=/run/atlas/aegis-request), then `systemctl is-active`
    until the unit reports activating/active. PID 1 starts the unit within a second or two and its first ExecStartPre
    removes the request; a very small include set can even finish inside the confirmation window, which shows as the
    request gone and the unit inactive again: that is a completed run, not a failure (rule §7.4: never a false failure,
    never a false success). Failure is reported only when the unit is neither activating nor active while the request
    still sits there (StartLimitBurst=3 per 6 h reached, the path unit not running, or the unit failed at once)."""
    req = request_file(env)
    _create_request(req)
    deadline = time.monotonic() + confirm_s
    state = unit_state(unit, runner=runner)
    while state not in ("active", "activating") and time.monotonic() < deadline:
        if not req.exists() and state == "inactive":
            # The unit ran and finished already (the ExecStartPre removed the request, restic was quick).
            return {"unit": unit, "started": True, "state": "finished", "request_file": str(req), "sudo": False}
        sleep(1.0)
        state = unit_state(unit, runner=runner)
    if state not in ("active", "activating"):
        if not req.exists() and state == "inactive":
            return {"unit": unit, "started": True, "state": "finished", "request_file": str(req), "sudo": False}
        raise RuntimeError(
            f"{unit} is {state} {confirm_s:.0f}s after {req} was created: atlas-aegis-trigger.path did not start it "
            f"(StartLimitBurst=3 per 6 h reached, the path unit inactive, or the unit failed at once: journalctl -u "
            f"atlas-aegis-trigger.path -u {unit}). Re-arm with: {REARM_COMMAND}"
        )
    return {"unit": unit, "started": True, "state": state, "request_file": str(req), "sudo": False}


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


def thaw_memory_store(builder: Any | None = None) -> Any | None:
    """The store the thaw replays the spool into: WITH the graph layer (fix round 4). The spool holds graph-insert /
    graph-delete lines beside the vector ones whenever LightRAGStore spooled during the freeze, and
    MemoryStore._commit raises for a graph line when the store has no graph, so a thaw built `with_graph=False` moved
    every graph write into `<file>.failed.jsonl` for ever. build_memory_store() builds the LightRAGStore lazily (only
    when LIGHTRAG_WORKING_DIR is set; lightrag is imported on the first graph line actually replayed), so this costs
    nothing on a node without the graph. None when no store can be built yet (before Phase 2 step 4): nothing was
    spooled, nothing to replay; said in the log."""
    try:
        if builder is None:
            from atlas.memory import build_memory_store as builder
        return builder()
    except Exception as exc:
        log.warning("aegis thaw: memory store not available (%s); no spool replay", exc)
        return None


@shared_task(name="atlas.tasks.aegis_thaw", bind=True)
def aegis_thaw(self: Any) -> dict[str, Any]:
    from atlas.celery_app import app
    from atlas.tasks import TaskRecord

    rec = TaskRecord(self.request.id, "aegis-thaw")
    try:
        out = thaw(control=app.control, memory=thaw_memory_store())
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
        f"AEGIS backup started (manual trigger through {out['request_file']}; unit {out['state']}); the unit reports "
        "on completion in the journal.",
        title="ATLAS AEGIS",
    )
    return rec.done(out)
