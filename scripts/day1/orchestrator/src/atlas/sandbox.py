"""The AEGIS sandbox (Section 16.4; V17): code runs under an operating-system-level cap, never under a prompt.

The run line is the one in the header of docker/sandbox/Dockerfile (README-contracts.md "Sandbox"), typed literally,
plus two flags this package adds (fix round) and says so here:

    timeout -k 5 $((SANDBOX_TIMEOUT_S + 10)) docker run --rm --init --pull never --name sb-<job> \\
      --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \\
      --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=$SANDBOX_TMPFS_SIZE \\
      --ulimit fsize=$SANDBOX_FSIZE --cap-drop ALL --security-opt no-new-privileges --user 65534:<atlas gid> \\
      -e SANDBOX_TIMEOUT_S=$SANDBOX_TIMEOUT_S \\
      -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py
    docker rm -f sb-<job>      # always afterwards

  * `--pull never`: an unqualified image name that is not present locally would otherwise be resolved to
    docker.io/library/atlas-sandbox:py3.12 and PULLED (registry-1.docker.io is allowlisted for build-time pulls), i.e.
    whatever a third party published under that name would run with the job's files mounted. `run()` also refuses
    up front when `docker image inspect` does not know the image, naming docker/sandbox/Dockerfile as the source.
  * `--user 65534:<atlas gid>` instead of `65534:65534`: the job directory is created 0o2770 (setgid, group atlas)
    and every staged file 0o640, so the nobody-uid process reads and writes /work through the GROUP bit; nothing
    under SANDBOX_DIR is world-accessible (CONVENTIONS.md §2; the earlier 1777 directory let any local account drop
    files into a job between mkdir and docker run, and restic snapshots SANDBOX_DIR nightly), and files the job
    creates inherit the atlas group, so atlas can always remove what it made.

The time bound is enforced INSIDE the container (the image's ENTRYPOINT wraps every command in coreutils
`timeout -s KILL $SANDBOX_TIMEOUT_S`); GNU timeout on the host only signals the docker client and is a backstop
10 s later, and because killing the client does not always kill the container the runner follows up with
`docker rm -f` (research §6, design note). Every cap is the kernel's (services-tools.md §6, VERIFIED flags); the OOM
kill shows as exit 137 (128 + SIGKILL; proven by V17) and `docker inspect .State.OOMKilled` is the authoritative
flag when the container still exists. GNU timeout returns 124 when the wall clock expires.

Network (16.4 "no network unless the task's tier grants it"), honestly: `network=True` drops `--network none` and
the container joins the default bridge, whose egress phase1/docker-egress-rules.sh limits to the pinned resolvers
(everything else from a bridge to the LAN is dropped and logged) while squid listens on loopback only (§8). A job
with network therefore reaches DNS and nothing else today: fail-closed, and less than the words promise. A dedicated
`atlas-sandbox` bridge with squid bound on its gateway is the recorded remedy (README-contracts.md), not made here.
Two rules the code does enforce: a grant is refused unless `atlas-docker-egress.service` is active (README-contracts
"the orchestrator must refuse a network grant when ... is not active"), and `network` is a CALLER argument the caller
must derive from the task's tier, never from model output (nothing in this package maps a tier to it yet).

DOCKER SOCKET == HOST ROOT (recorded, fix round; the same words in docker/sandbox/Dockerfile, phase2/README-contracts
and systemd/atlas-orchestrator.service): the account that runs this line, `atlas`, is in the docker group
(CONVENTIONS.md §2; SupplementaryGroups=docker in atlas-orchestrator and both Celery workers), and a docker-group
member can `docker run --privileged -v /:/host`, i.e. read /etc/atlas/secrets, the restic passphrase and the vault
cipher dir, or rewrite sudoers. The caps on the run line bound the SANDBOXED JOB; they do not bound the process that
launches it, so a code-execution bug or a prompt-injected tool in the cpu worker is host root, not "a sandboxed job".
The remedy (a root-owned `/usr/local/sbin/atlas-sandbox-run <job_id> <mem_mb> <cpus> <timeout_s> <net:0|1>` that
validates every argument and builds the docker line itself, allowed exactly in /etc/sudoers.d/atlas-sandbox, Sentinel
reading container health through it, and `docker` dropped from SupplementaryGroups in all three units) changes the
units, phase1/06-docker.sh and this module's runner, and is the Principal's decision (16.5); it is not made here.

`DockerRunner` is a Protocol so tests inject a StubDocker (CONVENTIONS.md §7.8); `build_argv()` never needs the
daemon, which is what tests/test_sandbox.py asserts.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

log = logging.getLogger("atlas.sandbox")

DEFAULT_IMAGE = "atlas-sandbox:py3.12"
DEFAULT_DIR = "/srv/atlas/sandbox"
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2.0
DEFAULT_PIDS = 256
DEFAULT_TMPFS = "512m"
DEFAULT_TIMEOUT_S = 300
DEFAULT_FSIZE = 1 << 30  # SANDBOX_FSIZE: 1 GiB per file (README-contracts.md "Sandbox")
HOST_TIMEOUT_SLACK_S = 10  # the host-side GNU timeout is a backstop after the in-container one
EGRESS_UNIT = "atlas-docker-egress.service"
EXIT_OOM_KILLED = 137
EXIT_TIMEOUT = 124
EXIT_TIMEOUT_KILLED = 137  # SIGKILL from the in-container timeout (or GNU timeout's -k); disambiguated by elapsed time

__all__ = [
    "DockerRunner",
    "SandboxConfig",
    "SandboxError",
    "SandboxResult",
    "StubDocker",
    "SubprocessDocker",
    "build_argv",
    "egress_unit_active",
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
    fsize: int = DEFAULT_FSIZE
    gid: int = -1  # the group the container runs with; -1 = this process's gid (atlas on the node)

    @property
    def base_path(self) -> Path:
        return Path(self.base_dir)

    @property
    def run_gid(self) -> int:
        return self.gid if self.gid >= 0 else os.getgid()


def sandbox_config_from_env(env: dict[str, str] | None = None) -> SandboxConfig:
    """SANDBOX_* keys of /etc/atlas/orchestrator.env (written by phase2/10-gate.sh)."""
    env = dict(os.environ if env is None else env)
    try:
        pids = int(env.get("SANDBOX_PIDS") or DEFAULT_PIDS)
    except ValueError as exc:
        raise SandboxError(f"SANDBOX_PIDS={env.get('SANDBOX_PIDS')!r} is not an integer") from exc
    try:
        fsize = int(env.get("SANDBOX_FSIZE") or DEFAULT_FSIZE)
    except ValueError as exc:
        raise SandboxError(f"SANDBOX_FSIZE={env.get('SANDBOX_FSIZE')!r} is not an integer") from exc
    return SandboxConfig(
        image=env.get("SANDBOX_IMAGE") or DEFAULT_IMAGE,
        base_dir=env.get("SANDBOX_DIR") or DEFAULT_DIR,
        pids=pids,
        tmpfs_size=env.get("SANDBOX_TMPFS_SIZE") or DEFAULT_TMPFS,
        fsize=fsize,
    )


@dataclass(frozen=True)
class SandboxResult:
    job_id: str
    exit_code: int
    stdout: str
    stderr: str
    killed_by_cap: bool  # exit 137 before the timeout: the memory cgroup's OOM killer (or the pids cap) struck
    timed_out: bool  # the in-container/GNU timeout expired (124, or 137 at the deadline)
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

    def image_exists(self, image: str) -> bool: ...


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

    def image_exists(self, image: str) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", "-f", "{{.Id}}", image],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"docker image inspect {image} could not run: {exc}") from exc
        return proc.returncode == 0


class StubDocker:
    """Test double: scripted (exit, stdout, stderr); records argv; can fake the OOM flag and a missing image."""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        *,
        oom: bool | None = None,
        sleep_s: float = 0.0,
        image_present: bool = True,
    ) -> None:
        self.exit_code, self.stdout, self.stderr, self.oom, self.sleep_s = exit_code, stdout, stderr, oom, sleep_s
        self.image_present = image_present
        self.calls: list[tuple[str, ...]] = []
        self.removed: list[str] = []
        self.inspected_images: list[str] = []

    def run(self, argv: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        self.calls.append(tuple(argv))
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return self.exit_code, self.stdout, self.stderr

    def inspect_oom(self, name: str) -> bool | None:
        return self.oom

    def remove(self, name: str) -> None:
        self.removed.append(name)

    def image_exists(self, image: str) -> bool:
        self.inspected_images.append(image)
        return self.image_present


def egress_unit_active(unit: str = EGRESS_UNIT) -> bool:
    """`systemctl is-active atlas-docker-egress.service` (no sudo needed): the DOCKER-USER rules are in place."""
    try:
        proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.strip() == "active"


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
    argv = [
        "timeout",
        "-k",
        "5",
        str(int(timeout_s) + HOST_TIMEOUT_SLACK_S),
        "docker",
        "run",
        "--rm",
        "--init",
        "--pull",
        "never",
        "--name",
        f"sb-{job_id}",
    ]
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
        f"/tmp:rw,noexec,nosuid,nodev,size={config.tmpfs_size}",
        "--ulimit",
        f"fsize={int(config.fsize)}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"65534:{config.run_gid}",
        "-e",
        f"SANDBOX_TIMEOUT_S={int(timeout_s)}",
        "-v",
        f"{work_dir}:/work:rw",
        "-w",
        "/work",
        config.image,
        *command,
    ]
    return argv


def _staged_path(work_dir: Path, name: str) -> Path:
    """A file name from the caller resolved INSIDE the job directory: relative, no `..`, no absolute path, and
    `is_relative_to` on the resolved target (a string-prefix test let `../<job>x/evil.py` through, fix round)."""
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or name.startswith(("/", "\\")) or ".." in pure.parts or "\\" in name:
        raise SandboxError(f"file name {name!r} is not a plain relative path inside the job directory")
    target = (work_dir / pure).resolve()
    if not target.is_relative_to(work_dir.resolve()) or target == work_dir.resolve():
        raise SandboxError(f"file name {name!r} escapes the job directory")
    return target


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
    egress_active: Callable[[], bool] = egress_unit_active,
) -> SandboxResult:
    """Run Python source (str -> /work/main.py) or a command (sequence -> argv inside the container) under the caps.

    Returns the exit code, stdout, stderr and `killed_by_cap` (exit 137 before the timeout). Nothing here raises for a
    failing program; only a broken sandbox contract (no docker, no image, no timeout binary, unwritable SANDBOX_DIR,
    a network grant without the egress rules) raises.
    """
    runner = runner or SubprocessDocker()
    config = config or sandbox_config_from_env()
    if not runner.image_exists(config.image):
        raise SandboxError(
            f"sandbox image {config.image!r} is not present locally and is never pulled (--pull never); build it "
            "from docker/sandbox/Dockerfile (phase2/10-gate.sh does: docker build -t atlas-sandbox:py3.12)"
        )
    if network and not egress_active():
        raise SandboxError(
            f"network grant refused: {EGRESS_UNIT} is not active, so a bridged container would not be confined to "
            "the allowlist proxy (README-contracts.md 'Sandbox')"
        )
    job_id = job_id or uuid.uuid4().hex[:12]
    work_dir = config.base_path / job_id
    try:
        work_dir.mkdir(parents=True, exist_ok=False)
        # setgid group dir: uid 65534 in the container writes through the group bit; nothing world-accessible.
        os.chmod(work_dir, 0o2770)
    except OSError as exc:
        raise SandboxError(
            f"cannot create the job directory {work_dir}: {exc} (SANDBOX_DIR must be atlas-writable)"
        ) from exc
    try:
        for name, content in (files or {}).items():
            target = _staged_path(work_dir, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in target.relative_to(work_dir).parents:
                if str(parent) != ".":
                    os.chmod(work_dir / parent, 0o2770)
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o640)
        if isinstance(code_or_cmd, str):
            (work_dir / "main.py").write_text(code_or_cmd, encoding="utf-8")
            os.chmod(work_dir / "main.py", 0o640)
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
        rc, out, err = runner.run(argv, timeout_s=timeout_s + HOST_TIMEOUT_SLACK_S)
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
        # Files the job created carry the atlas group (setgid dir), so removal normally succeeds; say so if not.
        log.warning("sandbox: could not remove %s (%s); left for the operator", path, exc)
