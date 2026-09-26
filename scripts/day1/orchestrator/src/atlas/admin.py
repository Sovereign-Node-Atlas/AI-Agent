"""atlas-admin — the operator CLI (console script `atlas-admin`, pyproject.toml).

Subcommands and who calls them:
  init-db              phase2/02-orchestrator.sh step 7: create ATLAS_DB_PATH (idempotent)
  engines list         the engines.json table with ports and unit state
  arbiter status       live GTT counters, unit states, the last ledger decisions
  vault-session-test   V18 helper; the implementation is atlas.vault (another writer), imported lazily
  enqueue <task>       systemd/atlas-sentinel.service, atlas-prune.service, atlas-aegis.service, phase2/08-sentinel.sh:
                       send a Celery task; --wait SECONDS blocks and prints the ledger record as ONE JSON line
                       (contract in phase2/02-orchestrator.sh; the task module atlas.tasks is another writer's)

Every failure exits non-zero with a one-line reason on stderr (rule §7.4); nothing here prompts.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from collections.abc import Sequence
from typing import Any

from atlas.arbiter import GIB, SysfsMemoryProbe
from atlas.config import ConfigError, EngineSpec, Settings, load_engines
from atlas.engines import EngineControlError, SystemdEngineController
from atlas.ledger import Ledger, new_task_id

log = logging.getLogger("atlas.admin")

# Celery task names (contract with atlas.tasks / atlas.celery_app, NOT stated in CONVENTIONS.md: the task writer
# registers these names; the cpu queue is the default, aegis-* may route to gpu inside the task module's task_routes).
ENQUEUE_TASKS: dict[str, str] = {
    "sentinel": "atlas.tasks.sentinel_pulse",
    "prune": "atlas.tasks.prune_sweep",
    "aegis-freeze": "atlas.tasks.aegis_freeze",
    "aegis-thaw": "atlas.tasks.aegis_thaw",
    "chat-retention": "atlas.tasks.chat_retention",
}


def _fail(msg: str, code: int = 1) -> int:
    print(f"atlas-admin: {msg}", file=sys.stderr)
    return code


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


def _unit_state(controller: SystemdEngineController, key: str) -> str:
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
            "state": "-" if args.no_state else _unit_state(controller, spec.key),
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
    out: dict[str, Any] = {"engines": {}, "gtt": {}, "decisions": []}
    try:
        out["gtt"] = {"total_bytes": probe.gtt_total_bytes(), "used_bytes": probe.gtt_used_bytes()}
    except Exception as exc:
        out["gtt"] = {"error": str(exc)}
    active: list[EngineSpec] = []
    for spec in engines.values():
        state = _unit_state(controller, spec.key)
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
    print("engine units:")
    for key, state in out["engines"].items():
        print(f"  {key:<26} {state}")
    print(f"weight-bearing units active: {', '.join(s.key for s in active) or 'none'} (limit {2})")
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
        return _fail("atlas.vault has no vault_session_test(); contract: vault_session_test(idle_seconds: int) -> int",
                     2)
    return int(fn(args.idle_seconds))


# --- enqueue (atlas.celery_app, another writer) -----------------------------------------------------------------------


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
    ledger = Ledger(settings.db_path)
    ledger.init_db()
    task_id = new_task_id()
    # The Celery id equals the ledger id so the worker (and --wait) find the same row.
    ledger.insert_task(args.task, task_id=task_id, status="queued", queue="cpu", payload={"source": "atlas-admin"})
    try:
        result = app.send_task(name, task_id=task_id, queue=args.queue)
    except Exception as exc:
        ledger.update_task(task_id, status="failed", error=f"enqueue failed: {exc}")
        ledger.close()
        return _fail(f"could not enqueue {name}: {exc}")
    if args.wait is None:
        print(f"enqueued {args.task} as {name} task_id={task_id} queue={args.queue}")
        ledger.close()
        return 0
    try:
        result.get(timeout=args.wait, propagate=False)
    except Exception as exc:
        ledger.update_task(task_id, error=f"wait: {exc}")
        print(ledger.record_json("tasks", task_id))
        ledger.close()
        return _fail(f"{args.task} did not finish within {args.wait}s: {exc}")
    state = str(result.state)
    row = ledger.get_task(task_id)
    if row is not None and row.get("status") in ("queued", "running"):
        # The task writer updates the row; when it did not, record Celery's own outcome so the line is honest.
        ledger.update_task(task_id, status="done" if state == "SUCCESS" else "failed",
                           result=result.result if state == "SUCCESS" else None,
                           error=None if state == "SUCCESS" else str(result.result))
    print(ledger.record_json("tasks", task_id))
    ledger.close()
    return 0 if state == "SUCCESS" else 1


# --- parser -----------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atlas-admin", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-db", help="create the SQLite ledger at ATLAS_DB_PATH (idempotent)")
    s.add_argument("--db", help="override ATLAS_DB_PATH")
    s.set_defaults(fn=cmd_init_db)

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
    st.set_defaults(fn=cmd_arbiter_status)

    v = sub.add_parser("vault-session-test", help="V18: open by button, lock on idle, no vault content in memory")
    v.add_argument("--idle-seconds", type=int, default=5)
    v.set_defaults(fn=cmd_vault_session_test)

    q = sub.add_parser("enqueue", help="send a Celery task (sentinel, prune, aegis-freeze, aegis-thaw, chat-retention)")
    q.add_argument("task", choices=sorted(ENQUEUE_TASKS))
    q.add_argument("--wait", type=float, default=None, metavar="SECONDS")
    q.add_argument("--queue", default="cpu")
    q.set_defaults(fn=cmd_enqueue)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        return int(args.fn(args))
    except ConfigError as exc:
        return _fail(f"config: {exc}")
    except EngineControlError as exc:
        return _fail(f"engine control: {exc}")


if __name__ == "__main__":
    sys.exit(main())
