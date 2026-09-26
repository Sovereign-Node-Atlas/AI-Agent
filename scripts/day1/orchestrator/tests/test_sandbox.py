"""Section 16.4 / V17 with a StubDocker: the run line is the Dockerfile's, exit 137 reads as killed_by_cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.sandbox import SandboxConfig, SandboxError, StubDocker, build_argv, run, sandbox_config_from_env


def cfg(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(image="atlas-sandbox:py3.12", base_dir=str(tmp_path / "sandbox"), pids=256, tmpfs_size="512m")


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
        "300",
        "docker",
        "run",
        "--rm",
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
        "/tmp:rw,size=512m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
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
    assert argv[:4] == ("timeout", "-k", "5", "30") and argv[-2:] == ("python3", "/work/main.py")
    assert f"{tmp_path / 'sandbox' / 'abc'}:/work:rw" in argv
    assert docker.removed == ["sb-abc"]
    assert not (tmp_path / "sandbox" / "abc").exists()  # cleaned up


def test_run_keeps_work_dir_when_asked(tmp_path: Path) -> None:
    docker = StubDocker(exit_code=0)
    res = run("print(1)", runner=docker, config=cfg(tmp_path), job_id="keep", keep_work_dir=True)
    assert (Path(res.work_dir) / "main.py").read_text() == "print(1)"


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


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_IMAGE", "atlas-sandbox:py3.12")
    monkeypatch.setenv("SANDBOX_DIR", "/srv/atlas/sandbox")
    monkeypatch.setenv("SANDBOX_PIDS", "256")
    monkeypatch.setenv("SANDBOX_TMPFS_SIZE", "512m")
    c = sandbox_config_from_env()
    assert (c.image, c.base_dir, c.pids, c.tmpfs_size) == ("atlas-sandbox:py3.12", "/srv/atlas/sandbox", 256, "512m")
    monkeypatch.setenv("SANDBOX_PIDS", "lots")
    with pytest.raises(SandboxError):
        sandbox_config_from_env()
