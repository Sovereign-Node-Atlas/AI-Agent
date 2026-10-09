"""atlas-admin — the operator CLI (console script `atlas-admin`, pyproject.toml).

Subcommands and who calls them:
  init-db              phase2/02-orchestrator.sh step 7: create ATLAS_DB_PATH (idempotent)
  engines list         the engines.json table with ports and unit state
  arbiter status       live GTT counters, the host reserve beside MemAvailable (S44), unit states, last decisions
  vault-session-test   V18 helper: `atlas-admin vault-session-test --file PATH [--idle-seconds N]` (contract in
                       phase2/README-contracts.md, called by verify/v18-vault.sh); the implementation is
                       atlas.vault.vault_session_test(idle_seconds, file=...) (another writer), imported lazily
  enqueue <task>       systemd/atlas-sentinel.service, atlas-prune.service, atlas-aegis.service, phase2/08-sentinel.sh:
                       send a Celery task on the queue atlas.celery_app routes it to (--queue overrides); --wait
                       SECONDS blocks and prints the ledger record as ONE JSON line (contract in
                       phase2/02-orchestrator.sh; the task module atlas.tasks is another writer's)
  config check         load the whole config tree the way the service does, then the strict CONVENTIONS.md §8 pass over
                       the domain cards (load_domain_cards(strict=True)); exit 0 and one line, or exit 1 with every
                       card that drifts from §8 and the H1 it should carry. For the Phase 2 gate / a verify script
                       (NOT a CONVENTIONS.md contract; offered to the gate writer, fix round 3).

Every failure exits non-zero with a one-line reason on stderr (rule §7.4); nothing here prompts. Root is refused for
the subcommands that open the ledger (init-db, arbiter status, enqueue) when the database belongs to another user:
Ledger() sets WAL mode and would leave root-owned -wal/-shm files that atlas-orchestrator.service cannot write; run
them as that user (`runuser -u atlas -- atlas-admin ...`, what phase2/02-orchestrator.sh's orch_admin does).
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from atlas.arbiter import GIB, MAX_RESIDENT, ArbiterError, SysfsMemoryProbe
from atlas.config import (
    ConfigError,
    EngineSpec,
    Settings,
    load_config,
    load_domain_cards,
    load_engines,
    parse_env_file,
)
from atlas.engines import EngineControlError, SystemdEngineController
from atlas.ledger import Ledger, new_task_id

log = logging.getLogger("atlas.admin")

# Celery task names (contract with atlas.tasks / atlas.celery_app, NOT stated in CONVENTIONS.md: the task writer
# registers these names; the queue comes from the app's task_routes (chat_retention -> gpu, the rest -> cpu default).
#
# CONTRACT for the task writer (atlas/tasks/__init__.py should state the same): a task's return value and the message
# of any exception it raises are printed by `enqueue --wait` to stdout — the systemd journal for atlas-sentinel,
# atlas-prune and atlas-aegis.service — and stored in the ledger's tasks row (result_json / error). They MUST NOT carry
# tokens, URLs with embedded credentials, or the contents of any file under /etc/atlas/secrets (rule §7.2). This side
# masks `scheme://user:pass@host` credentials in every Celery/kombu error it records (_redact), nothing more: a secret
# a task returns on purpose cannot be recognised here.
ENQUEUE_TASKS: dict[str, str] = {
    "sentinel": "atlas.tasks.sentinel_pulse",
    "prune": "atlas.tasks.prune_sweep",
    "aegis-freeze": "atlas.tasks.aegis_freeze",
    "aegis-thaw": "atlas.tasks.aegis_thaw",
    "chat-retention": "atlas.tasks.chat_retention",
}
# `redis://:hunter2@127.0.0.1:6379/0` -> `redis://:***@127.0.0.1:6379/0` (the user part, if any, is kept). The password
# runs to the LAST '@' before whitespace, so an unencoded '/' or '+' inside it (a base64 secret) is masked too; the
# `?password=` / `&token=` query forms are masked by the second pattern.
_URL_CREDENTIALS = re.compile(r"://([^/@:\s]*):([^@\s]*)@")
_QUERY_CREDENTIALS = re.compile(r"(?i)([?&](?:password|passwd|pass|pwd|token|auth|secret|api_key|apikey)=)[^&\s]+")


def _redact(text: str) -> str:
    """Mask the password of any `scheme://user:pass@host` (and `?password=`/`&token=` query values) in a message
    before it reaches a log or the ledger."""
    return _QUERY_CREDENTIALS.sub(r"\1***", _URL_CREDENTIALS.sub(r"://\1:***@", text))


def _fail(msg: str, code: int = 1) -> int:
    print(f"atlas-admin: {msg}", file=sys.stderr)
    return code


def _file_uid(path: Path) -> int:
    return path.stat().st_uid


def _root_owner_conflict(db: Path, *, euid: int | None = None) -> str | None:
    """Why a root run must not open `db`, or None.

    Every ledger-opening subcommand run as root while atlas-orchestrator is stopped would create root-owned
    atlas.sqlite3-wal/-shm (Ledger() sets journal_mode=WAL) or, on a fresh --db path, a root-owned database; the atlas
    service then fails with 'attempt to write a readonly database' on its next write. The Ledger only warns when it
    cannot chmod a file it does not own; the owner check belongs here, before anything is opened (rule §7.4).
    """
    euid = os.geteuid() if euid is None else euid
    if euid != 0 or not db.exists():
        return None
    owner = _file_uid(db)
    if owner == 0:
        return None
    return (f"{db} is owned by uid {owner}; run as that user (runuser -u atlas -- atlas-admin ...) so the -wal/-shm "
            "files stay writable by the service (rule §7.4)")


# --- init-db ----------------------------------------------------------------------------------------------------------


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    path = args.db or settings.db_path
    ledger = Ledger(path)
    ledger.init_db()
    tables = ledger.tables()
    ledger.close()
    print(f"ledger ready: {path} tables={','.join(tables)}")
    return 0


# --- engines list -----------------------------------------------------------------------------------------------------


def _unit_state(controller: SystemdEngineController, key: str, spec: Any = None) -> str:
    if spec is not None and getattr(spec, "is_external", False):
        return "external (in-process, no unit)"  # class external: run by its caller, budgeted by the Arbiter
    try:
        return "active" if controller.is_active(key) else "inactive"
    except EngineControlError:
        return "?"


def cmd_engines_list(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    engines = load_engines(settings.config_dir, settings.llama_port_base)
    controller = SystemdEngineController(engines=engines)
    rows: list[dict[str, Any]] = []
    for spec in engines.values():
        rows.append({
            "key": spec.key, "class": spec.arbiter_class, "mode": spec.mode, "kv": spec.kv_class,
            "footprint_gb": spec.footprint_gb, "ctx_size": spec.ctx_size, "parallel": spec.parallel,
            "port": spec.port, "unit": spec.systemd_unit,
            "state": "-" if args.no_state else _unit_state(controller, spec.key, spec),
        })
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    fmt = "{key:<26} {class:<10} {mode:<10} {kv:<5} {footprint_gb:>8} {ctx_size:>8} {parallel:>3} {port:>5} {state}"
    print(fmt.format(key="engine", **{"class": "class"}, mode="mode", kv="kv", footprint_gb="GB", ctx_size="ctx",
                     parallel="np", port="port", state="unit"))
    for r in rows:
        print(fmt.format(**r))
    return 0


# --- arbiter status ---------------------------------------------------------------------------------------------------


def cmd_arbiter_status(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    engines = load_engines(settings.config_dir, settings.llama_port_base)
    controller = SystemdEngineController(engines=engines)
    probe = SysfsMemoryProbe()
    out: dict[str, Any] = {"engines": {}, "gtt": {}, "host": {}, "decisions": []}
    try:
        out["gtt"] = {"total_bytes": probe.gtt_total_bytes(), "used_bytes": probe.gtt_used_bytes()}
    except Exception as exc:
        out["gtt"] = {"error": str(exc)}
    # Section 4.1 (S44): the reserve the Arbiter keeps for CPU-side memory, beside what the host uses right now. The
    # service reads ATLAS_ARBITER_HEADROOM_GIB from orchestrator.env (its EnvironmentFile), which a shell does not
    # carry, so that file is read here too (unreadable: the process environment and atlas.env decide, as before).
    unit = Settings.from_env({**parse_env_file(settings.etc_dir / "orchestrator.env"), **os.environ})
    out["host"] = {"headroom_bytes": unit.arbiter_headroom_bytes}
    if unit.arbiter_headroom_error:  # the orchestrator refuses to start with it (build_arbiter)
        out["host"]["error"] = unit.arbiter_headroom_error
    meminfo = probe.host_meminfo()
    if meminfo is not None:
        out["host"].update(mem_total_bytes=meminfo[0], mem_available_bytes=meminfo[1])
        if meminfo[2] is not None:
            out["host"]["gpu_pages_bytes"] = meminfo[2]
    active: list[EngineSpec] = []
    for spec in engines.values():
        state = _unit_state(controller, spec.key, spec)
        out["engines"][spec.key] = state
        if state == "active" and not spec.is_resident:
            active.append(spec)
    # The in-process ledger lives in the running orchestrator; the CLI shows the durable trail instead.
    if settings.db_path.is_file():
        ledger = Ledger(settings.db_path)
        out["decisions"] = ledger.list_arbiter_decisions(limit=args.limit)
        ledger.close()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    gtt = out["gtt"]
    if "error" in gtt:
        print(f"gtt: {gtt['error']}")
    else:
        print(f"gtt: used {gtt['used_bytes'] / GIB:.2f} GiB of {gtt['total_bytes'] / GIB:.2f} GiB")
    host = out["host"]
    line = f"host: Arbiter headroom {host['headroom_bytes'] / GIB:.0f} GiB (ATLAS_ARBITER_HEADROOM_GIB)"
    if "error" in host:
        line += f" [orchestrator.env: {host['error']}]"
    if "mem_total_bytes" in host:
        line += (f"; MemAvailable {host['mem_available_bytes'] / GIB:.2f} GiB of MemTotal "
                 f"{host['mem_total_bytes'] / GIB:.2f} GiB")
    print(line)
    print("engine units:")
    for key, state in out["engines"].items():
        print(f"  {key:<26} {state}")
    print(f"weight-bearing units active: {', '.join(s.key for s in active) or 'none'} (limit {MAX_RESIDENT})")
    print(f"last {len(out['decisions'])} ledger decisions:")
    for d in out["decisions"]:
        print(f"  #{d['id']} {d['action']:<10} {d['decision']:<8} {d['engine'] or '-':<26} task={d['task_id'] or '-'} "
              f"{d['reason']}")
    return 0


# --- vault-session-test (atlas.vault, another writer) -----------------------------------------------------------------


def cmd_vault_session_test(args: argparse.Namespace) -> int:
    try:
        vault = importlib.import_module("atlas.vault")
    except ImportError as exc:
        return _fail(f"atlas.vault is not installed in this package ({exc}); V18 needs the vault writer's module", 2)
    fn = getattr(vault, "vault_session_test", None)
    if fn is None:
        return _fail("atlas.vault has no vault_session_test(); contract: "
                     "vault_session_test(idle_seconds: int, file: str | None) -> int", 2)
    return int(fn(args.idle_seconds, file=args.file))


# --- enqueue (atlas.celery_app, another writer) -----------------------------------------------------------------------


def _routed_queue(app: Any, name: str) -> str:
    """The queue Celery's router would deliver `name` to (task_routes, else task_default_queue)."""
    try:
        route = app.amqp.router.route({}, name)
        queue = route.get("queue")
        return str(getattr(queue, "name", queue) or app.conf.task_default_queue or "cpu")
    except Exception as exc:  # a router error must not hide the task; say which queue we assume
        log.warning("could not resolve the route of %s (%s); assuming the default queue", name, exc)
        return str(app.conf.task_default_queue or "cpu")


def _redacted_result(value: Any) -> Any:
    """A task's return value with every embedded credential masked; the masked JSON text when it no longer parses."""
    masked = _redact(json.dumps(value, ensure_ascii=False, default=str))
    try:
        return json.loads(masked)
    except ValueError:
        return masked


def cmd_enqueue(args: argparse.Namespace) -> int:
    name = ENQUEUE_TASKS.get(args.task)
    if name is None:
        return _fail(f"unknown task {args.task!r}; one of {sorted(ENQUEUE_TASKS)}")
    try:
        celery_app = importlib.import_module("atlas.celery_app")
    except ImportError as exc:
        return _fail(f"atlas.celery_app cannot be imported ({exc}); the Celery module is the task writer's file", 2)
    app = getattr(celery_app, "app", None)
    if app is None:
        return _fail("atlas.celery_app exposes no `app` (contract in phase2/02-orchestrator.sh)", 2)
    settings = Settings.from_env()
    # The queue the app's own router picks (task_routes: chat_retention -> gpu, the single worker of C15), unless
    # --queue overrides it; the ledger row names the queue the task was really sent to (Section 9.7 honesty).
    effective = args.queue or _routed_queue(app, name)
    opts: dict[str, Any] = {"queue": args.queue} if args.queue else {}
    ledger = Ledger(settings.db_path)
    ledger.init_db()
    task_id = new_task_id()
    # The Celery id equals the ledger id so the worker (and --wait) find the same row.
    ledger.insert_task(args.task, task_id=task_id, status="queued", queue=effective, payload={"source": "atlas-admin"})
    try:
        result = app.send_task(name, task_id=task_id, **opts)
    except Exception as exc:
        reason = _redact(str(exc))  # kombu names the broker URL, credentials included, in its errors
        ledger.update_task(task_id, status="failed", error=f"enqueue failed: {reason}")
        ledger.close()
        return _fail(f"could not enqueue {name}: {reason}")
    if args.wait is None:
        print(f"enqueued {args.task} as {name} task_id={task_id} queue={effective}")
        ledger.close()
        return 0
    try:
        result.get(timeout=args.wait, propagate=False)
    except Exception as exc:
        reason = _redact(str(exc))
        ledger.update_task(task_id, error=f"wait: {reason}")
        print(ledger.record_json("tasks", task_id))
        ledger.close()
        return _fail(f"{args.task} did not finish within {args.wait}s: {reason}")
    state = str(result.state)
    row = ledger.get_task(task_id)
    if row is not None and row.get("status") in ("queued", "running"):
        # The task writer updates the row; when it did not, record Celery's own outcome so the line is honest. The
        # SUCCESS result passes through the same mask as the failures: a task that returns a dict naming the broker
        # URL would otherwise reach the journal and result_json verbatim (the contract above asks the task writer not
        # to; this side does not rely on it).
        res = _redacted_result(result.result) if state == "SUCCESS" else None
        ledger.update_task(task_id, status="done" if state == "SUCCESS" else "failed", result=res,
                           error=None if state == "SUCCESS" else _redact(str(result.result)))
    # Every byte this command writes to stdout has passed the mask, whoever wrote the row.
    print(_redact(ledger.record_json("tasks", task_id)))
    ledger.close()
    return 0 if state == "SUCCESS" else 1


# --- config check (CONVENTIONS.md §8 agreement; for the Phase 2 gate) -------------------------------------------------


def cmd_config_check(args: argparse.Namespace) -> int:
    """Exit 0 when the tree loads as the service loads it AND every domain card carries the §8 H1 triple; else 1."""
    settings = Settings.from_env()
    cfg = load_config(settings)  # lenient on the cards, like atlas-orchestrator; ConfigError -> main() -> exit 1
    try:
        load_domain_cards(settings.config_dir, strict=True)
    except ConfigError as exc:
        return _fail(f"config check: {exc}")
    print(f"config ok: {settings.config_dir} engines={len(cfg.engines)} phase4={len(cfg.phase4_engines)} "
          f"personas={len(cfg.personas)} task_forces={len(cfg.task_forces)} cards={len(cfg.domain_cards)} "
          "(every card in the CONVENTIONS.md §8 H1 form)")
    return 0


# --- parser -----------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atlas-admin", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-db", help="create the SQLite ledger at ATLAS_DB_PATH (idempotent)")
    s.add_argument("--db", help="override ATLAS_DB_PATH")
    s.set_defaults(fn=cmd_init_db, opens_ledger=True)

    e = sub.add_parser("engines", help="engine table")
    esub = e.add_subparsers(dest="engines_cmd", required=True)
    el = esub.add_parser("list", help="list engines.json with ports and unit state")
    el.add_argument("--json", action="store_true")
    el.add_argument("--no-state", action="store_true", help="do not query systemctl")
    el.set_defaults(fn=cmd_engines_list)

    a = sub.add_parser("arbiter", help="Engine Arbiter")
    asub = a.add_subparsers(dest="arbiter_cmd", required=True)
    st = asub.add_parser("status", help="GTT counters, unit states, last ledger decisions")
    st.add_argument("--json", action="store_true")
    st.add_argument("--limit", type=int, default=20)
    st.set_defaults(fn=cmd_arbiter_status, opens_ledger=True)

    c = sub.add_parser("config", help="the config tree")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    cc = csub.add_parser("check", help="load the tree as the service does, then the strict §8 pass over the domain "
                                       "cards; exit 1 naming every card that drifts (for the Phase 2 gate)")
    cc.set_defaults(fn=cmd_config_check)

    v = sub.add_parser("vault-session-test", help="V18: open by button, lock on idle, no vault content in memory")
    v.add_argument("--idle-seconds", type=int, default=5)
    v.add_argument("--file", required=True, metavar="PATH",
                   help="file to read inside a vault-tagged session (V18; phase2/README-contracts.md)")
    v.set_defaults(fn=cmd_vault_session_test)

    q = sub.add_parser("enqueue", help="send a Celery task (sentinel, prune, aegis-freeze, aegis-thaw, chat-retention)")
    q.add_argument("task", choices=sorted(ENQUEUE_TASKS))
    q.add_argument("--wait", type=float, default=None, metavar="SECONDS")
    q.add_argument("--queue", default=None, help="override the task's configured route (default: what "
                                                  "atlas.celery_app.task_routes says, else the cpu queue)")
    q.set_defaults(fn=cmd_enqueue, opens_ledger=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        if getattr(args, "opens_ledger", False):
            db = Path(args.db) if getattr(args, "db", None) else Settings.from_env().db_path
            conflict = _root_owner_conflict(db)
            if conflict:
                return _fail(conflict, 2)
        return int(args.fn(args))
    except ConfigError as exc:
        return _fail(f"config: {exc}")
    except EngineControlError as exc:
        return _fail(f"engine control: {exc}")
    except ArbiterError as exc:
        return _fail(f"arbiter: {exc}")
    except (sqlite3.Error, OSError) as exc:
        # sqlite ("unable to open database file", a locked WAL) or the directory it lives in (mkdir/chmod refused).
        return _fail(f"ledger: {exc} (ATLAS_DB_PATH={Settings.from_env().db_path}; is its directory writable by "
                     "this user?)")


if __name__ == "__main__":
    sys.exit(main())
