"""The vault control path and the vault session tag (Sections 10.5, 11; D13; V18; phase2/README-contracts.md "Vault").

Two things live here:

  * `VaultController`: open / lock / status through the helper `/usr/local/bin/atlas-vault` under
    `/etc/sudoers.d/atlas-vault` (plain `sudo -n`, never `sudo -E`: adjudicated conflict 7). The passphrase arrives in
    the JSON body of POST /vault/open, is piped to the helper on STDIN, and is never logged, stored, or shown to a model
    (Section 11: "the passphrase never passes through a model or a chat message"). `status()` is computed from this
    process's OWN view of /proc/self/mountinfo (field 5 == VAULT_MOUNT_DIR), which is what V18 checks: a mount that is
    invisible inside the orchestrator's mount namespace must read as locked here, not be papered over.
  * `SessionTags`: the vault session tag of Section 10.5. Anything read from under VAULT_MOUNT_DIR tags the session
    `vault` for the life of that session; atlas.memory honours the tag by DROPPING every write from a vault-tagged
    session (logged, never silently) unless the Principal explicitly says "remember this" (`remember=True`).

`vault_session_test()` is the V18 helper `atlas-admin vault-session-test` calls (admin.py imports it lazily): read a
file inside a vault-tagged session, attempt a memory write through the normal path SYNCHRONOUSLY, close the file,
print one JSON line, exit 0 only when the write was suppressed.

Helper exit codes (phase2/09b-vault.sh, VERIFIED in that file): open -> 0 mounted, 1 refused (gocryptfs exit 12 =
wrong passphrase), 2 contract error; lock -> 0; status prints "open" or "locked".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("atlas.vault")

DEFAULT_HELPER = "/usr/local/bin/atlas-vault"
DEFAULT_MOUNT_DIR = "/srv/atlas/vault/open"
DEFAULT_SESSION_FILE = "/run/atlas/vault-sessions.json"
SUDO = "sudo"

__all__ = [
    "SessionTags",
    "VaultController",
    "VaultError",
    "VaultResult",
    "build_vault_controller",
    "is_mounted_here",
    "vault_session_test",
]


class VaultError(RuntimeError):
    """A contract error on the vault path (helper missing, sudoers wrong); never a wrong passphrase."""


@dataclass(frozen=True)
class VaultResult:
    ok: bool
    state: str  # "open" | "locked"
    message: str = ""
    exit_code: int = 0

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "state": self.state, "message": self.message}


def is_mounted_here(mount_dir: str | Path, mountinfo: str | Path = "/proc/self/mountinfo") -> bool:
    """True when `mount_dir` is a mount point in THIS process's mount namespace (README-contracts.md "Vault")."""
    target = str(mount_dir).rstrip("/") or "/"
    try:
        text = Path(mountinfo).read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        fields = line.split()
        # /proc/self/mountinfo: field 5 (1-based) is the mount point, octal-escaped (\040 for a space).
        if len(fields) >= 5 and _unescape(fields[4]) == target:
            return True
    return False


def _unescape(field: str) -> str:
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


# --- session tags (Section 10.5) --------------------------------------------------------------------------------------


class SessionTags:
    """Which sessions are vault-tagged. In-process, with an optional file mirror so atlas-admin (another process)
    and the Celery workers see the same set; the file lives on the orchestrator's tmpfs RuntimeDirectory."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._vault: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._vault = {str(k): float(v) for k, v in dict(data.get("vault", {})).items()}
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("vault sessions file %s unreadable (%s); starting empty", self.path, exc)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"vault": self._vault}), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("vault sessions file %s not written (%s); the in-process tag still holds", self.path, exc)

    def tag_vault(self, session_id: str) -> None:
        with self._lock:
            if session_id not in self._vault:
                log.info("session %s is now vault-tagged: memory writes from it are dropped (Section 10.5)", session_id)
            self._vault[session_id] = time.time()
            self._save()

    def is_vault(self, session_id: str | None) -> bool:
        if session_id is None:
            return False
        with self._lock:
            self._load()
            return session_id in self._vault

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._vault.pop(session_id, None)
            self._save()

    def vault_sessions(self) -> list[str]:
        with self._lock:
            return sorted(self._vault)


# --- the controller ---------------------------------------------------------------------------------------------------

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class VaultController:
    def __init__(
        self,
        *,
        helper: str = DEFAULT_HELPER,
        mount_dir: str = DEFAULT_MOUNT_DIR,
        sudo: bool = True,
        runner: Runner = subprocess.run,
        sessions: SessionTags | None = None,
        timeout_s: float = 180.0,
    ) -> None:
        self.helper = helper
        self.mount_dir = mount_dir
        self.sudo = sudo
        self._run = runner
        self.sessions = sessions or SessionTags()
        self.timeout_s = timeout_s

    def _argv(self, verb: str) -> list[str]:
        # `sudo -n`: never prompt (sudo-rs); the fragment allows exactly open|lock|status.
        return ([SUDO, "-n"] if self.sudo else []) + [self.helper, verb]

    def _call(self, verb: str, *, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
        argv = self._argv(verb)
        try:
            return self._run(
                argv, input=stdin_text, capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except FileNotFoundError as exc:
            raise VaultError(
                f"{argv[0]} not found; phase2/09b-vault.sh installs {self.helper} and the sudoers fragment"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise VaultError(f"atlas-vault {verb} did not return within {self.timeout_s:.0f}s") from exc

    def open(self, passphrase: str) -> VaultResult:
        """Pipe the passphrase to `atlas-vault open`. The passphrase is in this call's memory only."""
        if not passphrase:
            return VaultResult(False, self.status().state, "empty passphrase", 1)
        proc = self._call("open", stdin_text=passphrase + "\n")
        if proc.returncode == 0:
            state = "open" if self.status().state == "open" else "locked"
            if state != "open":
                # The helper says mounted but this process cannot see it: say so, never pretend (V18 tests this).
                msg = (
                    f"helper reported open but {self.mount_dir} is not mounted in the orchestrator's mount "
                    "namespace (README-contracts.md 'Vault': mount propagation)"
                )
                log.error("vault open: %s", msg)
                return VaultResult(False, state, msg, 0)
            log.info("vault opened at %s", self.mount_dir)
            return VaultResult(True, "open", "open", 0)
        if proc.returncode == 1:
            log.warning("vault open refused by the helper (wrong passphrase or mount failure)")
            return VaultResult(False, "locked", "refused: passphrase incorrect or mount failed", 1)
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        raise VaultError(f"atlas-vault open exited {proc.returncode}: {err}")

    def lock(self) -> VaultResult:
        proc = self._call("lock")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            raise VaultError(f"atlas-vault lock exited {proc.returncode}: {err}")
        log.info("vault locked")
        return VaultResult(True, "locked", "locked", 0)

    def status(self) -> VaultResult:
        """This process's own view; the helper is consulted only when the mount is not visible, to tell the
        difference between 'locked' and 'open but invisible here' in the message."""
        if is_mounted_here(self.mount_dir):
            return VaultResult(True, "open", "open", 0)
        msg = "locked"
        try:
            proc = self._call("status")
            helper_view = (proc.stdout or "").strip()
            if proc.returncode == 0 and helper_view == "open":
                msg = (
                    f"locked in the orchestrator's view although the helper reports open: {self.mount_dir} did "
                    "not propagate into this mount namespace"
                )
                log.error("vault status: %s", msg)
        except VaultError as exc:
            msg = f"locked (helper unavailable: {exc})"
        return VaultResult(True, "locked", msg, 0)


def build_vault_controller(env: dict[str, str] | None = None, sessions: SessionTags | None = None) -> VaultController:
    """Production wiring from /etc/atlas/orchestrator.env (VAULT_* keys mirrored by phase2/09b-vault.sh)."""
    env = dict(os.environ if env is None else env)
    session_file = env.get("VAULT_SESSION_FILE") or DEFAULT_SESSION_FILE
    return VaultController(
        helper=env.get("VAULT_HELPER") or DEFAULT_HELPER,
        mount_dir=env.get("VAULT_MOUNT_DIR") or DEFAULT_MOUNT_DIR,
        sessions=sessions or SessionTags(session_file),
    )


# --- V18 helper: atlas-admin vault-session-test ---------------------------------------------------------------------


def vault_session_test(idle_seconds: int = 5, file: str | None = None) -> int:
    """Read `file` inside a vault-tagged session, try to remember it through the normal memory path, report.

    Contract (README-contracts.md): synchronous, closes the file, prints ONE JSON line, exit 0. A write that reached a
    collection is a FAIL (exit 1) even though the JSON is printed: V18 also checks ChromaDB itself. `idle_seconds` is
    accepted for admin.py's `--idle-seconds` argument (this function does not sleep: the idle lock is gocryptfs's).
    NOTE for the admin writer: verify/v18-vault.sh calls `atlas-admin vault-session-test --file PATH`; admin.py's
    parser must accept --file and pass it here (contract stated in README-contracts.md, not CONVENTIONS.md).
    """
    if file is None:
        file = os.environ.get("ATLAS_VAULT_TEST_FILE") or _file_from_argv()
    if not file:
        print(
            json.dumps(
                {"read": False, "error": "no --file given (README-contracts.md: vault-session-test --file PATH)"}
            )
        )
        return 2
    controller = build_vault_controller()
    mount_dir = controller.mount_dir.rstrip("/")
    path = Path(file)
    inside = str(path).startswith(mount_dir + "/")
    session_id = f"vault-session-test-{int(time.time())}"
    result: dict[str, object] = {
        "read": False,
        "chars": 0,
        "vault_tagged": False,
        "memory_write_attempted": False,
        "memory_write_suppressed": False,
        "file": str(path),
        "inside_vault": inside,
        "idle_seconds": idle_seconds,
    }
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        # The file is closed here: an open file keeps gocryptfs "not idle" (man page, -idle).
    except OSError as exc:
        result["error"] = f"cannot read {path}: {exc}"
        print(json.dumps(result))
        return 1
    result["read"] = True
    result["chars"] = len(text)
    # Section 10.5: anything read from the vault tags the session for its whole life.
    controller.sessions.tag_vault(session_id)
    result["vault_tagged"] = controller.sessions.is_vault(session_id)

    from atlas.memory import HemisphereViolation, MemoryStoreError, build_memory_store

    try:
        store = build_memory_store(sessions=controller.sessions)
    except MemoryStoreError as exc:
        result["error"] = f"memory store not configured: {exc} (memory.env from phase2/04-memory.sh)"
        print(json.dumps(result))
        return 1
    result["memory_write_attempted"] = True
    suppressed = True
    try:
        # The normal write path, synchronously: a vault-tagged session's write is dropped inside MemoryStore.write.
        for collection, hemisphere in (("estate", "estate"), ("corporate", "corporate")):
            wr = store.write(
                collection,
                [text],
                [{"kind": "vault-session-test", "source": str(path)}],
                ids=[f"{session_id}-{collection}"],
                hemisphere=hemisphere,
                session_id=session_id,
            )
            suppressed = suppressed and wr.dropped and not wr.written
        graph = store.graph
        if graph is not None:
            gr = graph.insert(
                text,
                doc_id=f"{session_id}-graph",
                metadata={"kind": "vault-session-test"},
                hemisphere="estate",
                session_id=session_id,
            )
            suppressed = suppressed and gr.dropped and not gr.written
    except HemisphereViolation as exc:
        result["error"] = f"hemisphere check raised instead of the vault rule: {exc}"
        suppressed = False
    result["memory_write_suppressed"] = suppressed
    controller.sessions.clear(session_id)
    print(json.dumps(result))
    return 0 if suppressed else 1


def _file_from_argv() -> str | None:
    """`--file PATH` from the process argv when the CLI parser did not pass it (admin.py contract note above)."""
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--file" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--file="):
            return a.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> int:
    """`python -m atlas.vault session-test --file PATH` (fallback entry for V18) and status/lock from the shell."""
    p = argparse.ArgumentParser(prog="atlas.vault")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("session-test")
    s.add_argument("--file", required=True)
    s.add_argument("--idle-seconds", type=int, default=5)
    sub.add_parser("status")
    sub.add_parser("lock")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.cmd == "session-test":
        return vault_session_test(args.idle_seconds, args.file)
    controller = build_vault_controller()
    res = controller.lock() if args.cmd == "lock" else controller.status()
    print(json.dumps(res.as_dict()))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
