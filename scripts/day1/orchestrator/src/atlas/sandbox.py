"""The AEGIS sandbox (Section 16.4; V17): code runs under an operating-system-level cap, never under a prompt.

The run line is the one in the header of docker/sandbox/Dockerfile (README-contracts.md "Sandbox"), typed literally:

    timeout -k 5 $SANDBOX_TIMEOUT_S docker run --rm --name sb-<job> \
      --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
      --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,size=$SANDBOX_TMPFS_SIZE \
      --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
      -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py

A tier that grants network replaces `--network none` (the container then joins the default bridge, whose egress is
the DOCKER-USER allowlist chain of Phase 1 step 6; nothing else changes). Every cap is the kernel's (services-tools.md
§6, VERIFIED flags); the OOM kill shows as exit 137 (128 + SIGKILL, convention UNVERIFIED by the Docker docs, proven
by V17) and `docker inspect .State.OOMKilled` is the authoritative flag when the container still exists. GNU timeout
returns 124 when the wall clock expires; because killing the client does not always kill the container, the runner
follows up with `docker rm -f` (research §6, design note).

`DockerRunner` is a Protocol so tests inject a StubDocker (CONVENTIONS.md §7.8); `run()` never needs the daemon to
build its argv, which is what tests/test_sandbox.py asserts.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger("atlas.sandbox")

DEFAULT_IMAGE = "atlas-sandbox:py3.12"
DEFAULT_DIR = "/srv/atlas/sandbox"
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2.0
DEFAULT_PIDS = 256
DEFAULT_TMPFS = "512m"
DEFAULT_TIMEOUT_S = 300
EXIT_OOM_KILLED = 137
EXIT_TIMEOUT = 124
EXIT_TIMEOUT_KILLED = 137  # GNU timeout after its own -k SIGKILL; disambiguated by the elapsed time

__all__ = [
    "DockerRunner",
    "SandboxConfig",
    "SandboxError",
    "SandboxResult",
    "StubDocker",
    "SubprocessDocker",
    "build_argv",
    "run",
    "sandbox_config_from_env",
]


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxConfig:
    image: str = DEFAULT_IMAGE
    base_dir: str = DEFAULT_DIR
    pids: int = DEFAULT_PIDS
    tmpfs_size: str = DEFAULT_TMPFS

    @property
    def base_path(self) -> Path:
        return Path(self.base_dir)


def sandbox_config_from_env(env: dict[str, str] | None = None) -> SandboxConfig:
    """SANDBOX_* keys of /etc/atlas/orchestrator.env (written by phase2/10-gate.sh)."""
    env = dict(os.environ if env is None else env)
    try:
        pids = int(env.get("SANDBOX_PIDS") or DEFAULT_PIDS)
    except ValueError as exc:
        raise SandboxError(f"SANDBOX_PIDS={env.get('SANDBOX_PIDS')!r} is not an integer") from exc
    return SandboxConfig(
        image=env.get("SANDBOX_IMAGE") or DEFAULT_IMAGE,
        base_dir=env.get("SANDBOX_DIR") or DEFAULT_DIR,
        pids=pids,
        tmpfs_size=env.get("SANDBOX_TMPFS_SIZE") or DEFAULT_TMPFS,
    )


@dataclass(frozen=True)
class SandboxResult:
    job_id: str
    exit_code: int
    stdout: str
    stderr: str
    killed_by_cap: bool  # exit 137 before the timeout: the memory cgroup's OOM killer (or the pids cap) struck
    timed_out: bool  # GNU timeout expired (124) or had to SIGKILL after expiry
    duration_s: float
    argv: tuple[str, ...] = ()
    oom_killed: bool | None = None  # docker inspect .State.OOMKilled when it could be read
    work_dir: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.killed_by_cap


class DockerRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]: ...

    def inspect_oom(self, name: str) -> bool | None: ...

    def remove(self, name: str) -> None: ...


class SubprocessDocker:
    """The real runner: argv already starts with `timeout -k 5 N docker run ...`."""

    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout_s + 30,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise SandboxError(f"{argv[0]} not found (coreutils timeout / docker CLI missing): {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"sandbox run did not return {timeout_s + 30:.0f}s after start; docker hung?") from exc
        return proc.returncode, proc.stdout, proc.stderr

    def inspect_oom(self, name: str) -> bool | None:
        try:
            proc = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.OOMKilled}}", name],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None  # --rm already removed it; the exit code carries the verdict
        return proc.stdout.strip() == "true"

    def remove(self, name: str) -> None:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=60, check=False)


class StubDocker:
    """Test double: scripted (exit, stdout, stderr); records argv; can fake the OOM flag."""

    def __init__(
        self, exit_code: int = 0, stdout: str = "", stderr: str = "", *, oom: bool | None = None, sleep_s: float = 0.0
    ) -> None:
        self.exit_code, self.stdout, self.stderr, self.oom, self.sleep_s = exit_code, stdout, stderr, oom, sleep_s
        self.calls: list[tuple[str, ...]] = []
        self.removed: list[str] = []

    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        self.calls.append(tuple(argv))
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return self.exit_code, self.stdout, self.stderr

    def inspect_oom(self, name: str) -> bool | None:
        return self.oom

    def remove(self, name: str) -> None:
        self.removed.append(name)


def build_argv(
    job_id: str,
    work_dir: str | Path,
    command: Sequence[str],
    *,
    memory_mb: int,
    cpus: float,
    timeout_s: int,
    network: bool,
    config: SandboxConfig,
) -> list[str]:
    """The run line of docker/sandbox/Dockerfile, with the caps filled in. Pure: no docker needed."""
    if memory_mb < 6:
        raise SandboxError("docker's minimum --memory is 6m (services-tools.md §6)")
    if cpus <= 0 or timeout_s <= 0:
        raise SandboxError("cpus and timeout_s must be positive")
    mem = f"{int(memory_mb)}m"
    argv = ["timeout", "-k", "5", str(int(timeout_s)), "docker", "run", "--rm", "--name", f"sb-{job_id}"]
    if not network:
        argv += ["--network", "none"]
    argv += [
        "--memory",
        mem,
        "--memory-swap",
        mem,
        "--cpus",
        str(cpus),
        "--pids-limit",
        str(config.pids),
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,size={config.tmpfs_size}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
        "-v",
        f"{work_dir}:/work:rw",
        "-w",
        "/work",
        config.image,
        *command,
    ]
    return argv


def run(
    code_or_cmd: str | Sequence[str],
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    network: bool = False,
    *,
    runner: DockerRunner | None = None,
    config: SandboxConfig | None = None,
    job_id: str | None = None,
    files: dict[str, str] | None = None,
    keep_work_dir: bool = False,
) -> SandboxResult:
    """Run Python source (str -> /work/main.py) or a command (sequence -> argv inside the container) under the caps.

    Returns the exit code, stdout, stderr and `killed_by_cap` (exit 137 before the timeout). Nothing here raises for a
    failing program; only a broken sandbox contract (no docker, no timeout binary, unwritable SANDBOX_DIR) raises.
    """
    runner = runner or SubprocessDocker()
    config = config or sandbox_config_from_env()
    job_id = job_id or uuid.uuid4().hex[:12]
    work_dir = config.base_path / job_id
    try:
        work_dir.mkdir(parents=True, exist_ok=False)
        # The container runs as 65534 (nobody) and must write its results here; atlas cannot chown to nobody.
        os.chmod(work_dir, 0o1777)
    except OSError as exc:
        raise SandboxError(
            f"cannot create the job directory {work_dir}: {exc} (SANDBOX_DIR must be atlas-writable)"
        ) from exc
    try:
        for name, content in (files or {}).items():
            target = work_dir / name
            if not str(target.resolve()).startswith(str(work_dir.resolve())):
                raise SandboxError(f"file name {name!r} escapes the job directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o644)
        if isinstance(code_or_cmd, str):
            (work_dir / "main.py").write_text(code_or_cmd, encoding="utf-8")
            os.chmod(work_dir / "main.py", 0o644)
            command: list[str] = ["python3", "/work/main.py"]
        else:
            command = [str(c) for c in code_or_cmd]
            if not command:
                raise SandboxError("empty command")
        argv = build_argv(
            job_id,
            work_dir,
            command,
            memory_mb=memory_mb,
            cpus=cpus,
            timeout_s=timeout_s,
            network=network,
            config=config,
        )
        log.info(
            "sandbox job=%s memory=%dm cpus=%s timeout=%ds network=%s: %s",
            job_id,
            memory_mb,
            cpus,
            timeout_s,
            network,
            shlex.join(command),
        )
        t0 = time.monotonic()
        rc, out, err = runner.run(argv, timeout_s=timeout_s)
        elapsed = time.monotonic() - t0
        name = f"sb-{job_id}"
        oom = runner.inspect_oom(name)
        runner.remove(name)  # the client may have been killed by timeout while the container lived on
        timed_out = rc == EXIT_TIMEOUT or (rc == EXIT_TIMEOUT_KILLED and elapsed >= timeout_s)
        killed_by_cap = (rc == EXIT_OOM_KILLED and not timed_out) or oom is True
        if killed_by_cap:
            log.warning(
                "sandbox job=%s killed by the cap after %.1fs (exit %d, oom_killed=%s)", job_id, elapsed, rc, oom
            )
        elif timed_out:
            log.warning("sandbox job=%s hit the %ds timeout (exit %d)", job_id, timeout_s, rc)
        return SandboxResult(
            job_id=job_id,
            exit_code=rc,
            stdout=out,
            stderr=err,
            killed_by_cap=killed_by_cap,
            timed_out=timed_out,
            duration_s=elapsed,
            argv=tuple(argv),
            oom_killed=oom,
            work_dir=str(work_dir),
        )
    finally:
        if not keep_work_dir:
            _rmtree_quiet(work_dir)


def _rmtree_quiet(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path)
    except OSError as exc:
        # Files created by uid 65534 inside a 1777 directory cannot always be removed by atlas; say so, keep going.
        log.warning("sandbox: could not remove %s (%s); left for the operator", path, exc)
