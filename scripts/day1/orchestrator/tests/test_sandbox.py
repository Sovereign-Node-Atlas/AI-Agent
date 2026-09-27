"""Section 16.4 / V17 with a StubDocker: the run line is the Dockerfile's (plus --pull never and the atlas gid), exit
137 reads as killed_by_cap, a missing image or an unconfined network grant is refused, staged paths stay inside."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from atlas.sandbox import SandboxConfig, SandboxError, StubDocker, build_argv, run, sandbox_config_from_env


def cfg(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(
        image="atlas-sandbox:py3.12", base_dir=str(tmp_path / "sandbox"), pids=256, tmpfs_size="512m", gid=1234
    )


def test_build_argv_is_the_dockerfile_run_line(tmp_path: Path) -> None:
    argv = build_argv(
        "job1",
        "/srv/atlas/sandbox/job1",
        ["python3", "/work/main.py"],
        memory_mb=2048,
        cpus=2,
        timeout_s=300,
        network=False,
        config=cfg(tmp_path),
    )
    assert argv == [
        "timeout",
        "-k",
        "5",
        "310",  # SANDBOX_TIMEOUT_S + 10: the host timeout is a backstop after the in-container one
        "docker",
        "run",
        "--rm",
        "--init",
        "--pull",
        "never",
        "--name",
        "sb-job1",
        "--network",
        "none",
        "--memory",
        "2048m",
        "--memory-swap",
        "2048m",
        "--cpus",
        "2",
        "--pids-limit",
        "256",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=512m",
        "--ulimit",
        f"fsize={1 << 30}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:1234",
        "-e",
        "SANDBOX_TIMEOUT_S=300",
        "-v",
        "/srv/atlas/sandbox/job1:/work:rw",
        "-w",
        "/work",
        "atlas-sandbox:py3.12",
        "python3",
        "/work/main.py",
    ]
    with_net = build_argv(
        "job1", "/w", ["python3"], memory_mb=512, cpus=1, timeout_s=10, network=True, config=cfg(tmp_path)
    )
    assert "--network" not in with_net and "--read-only" in with_net and "--cap-drop" in with_net
    assert with_net[with_net.index("--pull") + 1] == "never"
    with pytest.raises(SandboxError):
        build_argv("j", "/w", ["python3"], memory_mb=5, cpus=1, timeout_s=10, network=False, config=cfg(tmp_path))


def test_run_writes_main_py_and_returns_output(tmp_path: Path) -> None:
    docker = StubDocker(exit_code=0, stdout="hello\n", stderr="")
    res = run(
        "print('hello')",
        memory_mb=512,
        cpus=1,
        timeout_s=30,
        runner=docker,
        config=cfg(tmp_path),
        job_id="abc",
        files={"data/in.txt": "1,2,3"},
    )
    assert res.ok and res.exit_code == 0 and res.stdout == "hello\n"
    assert not res.killed_by_cap and not res.timed_out
    argv = docker.calls[0]
    assert argv[:4] == ("timeout", "-k", "5", "40") and argv[-2:] == ("python3", "/work/main.py")
    assert f"{tmp_path / 'sandbox' / 'abc'}:/work:rw" in argv
    assert docker.inspected_images == ["atlas-sandbox:py3.12"]
    assert docker.removed == ["sb-abc"]
    assert not (tmp_path / "sandbox" / "abc").exists()  # cleaned up


def test_run_keeps_work_dir_when_asked_with_group_only_modes(tmp_path: Path) -> None:
    docker = StubDocker(exit_code=0)
    res = run(
        "print(1)", runner=docker, config=cfg(tmp_path), job_id="keep", keep_work_dir=True, files={"d/x.txt": "x"}
    )
    work = Path(res.work_dir)
    assert (work / "main.py").read_text() == "print(1)"
    # 0o2770 setgid group dir, 0o640 files: nothing world-accessible (CONVENTIONS.md §2), the container writes
    # through the group bit as 65534:<gid>.
    assert stat.S_IMODE(work.stat().st_mode) == 0o2770
    assert stat.S_IMODE((work / "main.py").stat().st_mode) == 0o640
    assert stat.S_IMODE((work / "d" / "x.txt").stat().st_mode) == 0o640
    assert stat.S_IMODE((work / "d").stat().st_mode) == 0o2770


def test_exit_137_before_the_timeout_is_killed_by_cap(tmp_path: Path) -> None:
    docker = StubDocker(exit_code=137, stderr="Killed", oom=True)
    res = run(
        "a=[]\nwhile True: a.append(b'x'*(64<<20))",
        memory_mb=512,
        cpus=1,
        timeout_s=120,
        runner=docker,
        config=cfg(tmp_path),
    )
    assert res.exit_code == 137 and res.killed_by_cap and res.oom_killed is True and not res.timed_out


def test_exit_124_is_a_timeout_not_a_cap(tmp_path: Path) -> None:
    docker = StubDocker(exit_code=124)
    res = run(["python3", "-c", "import time; time.sleep(999)"], timeout_s=1, runner=docker, config=cfg(tmp_path))
    assert res.timed_out and not res.killed_by_cap
    assert res.argv[-3:] == ("python3", "-c", "import time; time.sleep(999)")


def test_missing_image_is_refused_before_any_run(tmp_path: Path) -> None:
    docker = StubDocker(image_present=False)
    with pytest.raises(SandboxError, match="docker/sandbox/Dockerfile"):
        run("print(1)", runner=docker, config=cfg(tmp_path), job_id="noimg")
    assert docker.calls == [] and not (tmp_path / "sandbox" / "noimg").exists()


def test_network_grant_needs_the_egress_unit(tmp_path: Path) -> None:
    docker = StubDocker()
    with pytest.raises(SandboxError, match="atlas-docker-egress"):
        run("print(1)", network=True, runner=docker, config=cfg(tmp_path), egress_active=lambda: False)
    assert docker.calls == []
    res = run("print(1)", network=True, runner=docker, config=cfg(tmp_path), egress_active=lambda: True)
    assert res.ok and "--network" not in res.argv


@pytest.mark.parametrize(
    "name", ["../x/evil.py", "../sandbox/jobx/evil.py", "/etc/evil.py", "a/../../evil.py", "..", "d\\..\\e.py"]
)
def test_staged_file_names_cannot_escape_the_job_directory(tmp_path: Path, name: str) -> None:
    docker = StubDocker()
    with pytest.raises(SandboxError):
        run("print(1)", runner=docker, config=cfg(tmp_path), job_id="job", files={name: "..."})
    assert docker.calls == []
    assert not (tmp_path / "sandbox" / "jobx").exists() and not (tmp_path / "sandbox" / "x").exists()


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_IMAGE", "atlas-sandbox:py3.12")
    monkeypatch.setenv("SANDBOX_DIR", "/srv/atlas/sandbox")
    monkeypatch.setenv("SANDBOX_PIDS", "256")
    monkeypatch.setenv("SANDBOX_TMPFS_SIZE", "512m")
    monkeypatch.setenv("SANDBOX_FSIZE", "1073741824")
    c = sandbox_config_from_env()
    assert (c.image, c.base_dir, c.pids, c.tmpfs_size, c.fsize) == (
        "atlas-sandbox:py3.12",
        "/srv/atlas/sandbox",
        256,
        "512m",
        1073741824,
    )
    assert c.run_gid >= 0
    monkeypatch.setenv("SANDBOX_PIDS", "lots")
    with pytest.raises(SandboxError):
        sandbox_config_from_env()
