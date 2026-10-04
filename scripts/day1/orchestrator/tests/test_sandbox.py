"""Section 16.4 / V17 with a StubDocker: the run line is the Dockerfile's (plus --pull never and the atlas gid), /work
is a bounded tmpfs with the inputs staged read-only and the results streamed out as a tar the launcher unpacks (fix
round 5), exit 137 reads as killed_by_cap, a missing image is refused, every network grant is refused (the bridge is a
DNS tunnel at best), staged paths and job ids stay inside SANDBOX_DIR, the operator's caps come from the environment,
and the launcher keeps a bounded amount of the job's output (fix round 4)."""

from __future__ import annotations

import io
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from atlas.sandbox import (
    JOB_STDOUT,
    RESULTS_TAR,
    RUN_SHELL,
    SandboxConfig,
    SandboxError,
    StubDocker,
    SubprocessDocker,
    build_argv,
    drain_bounded,
    parse_memory_mb,
    run,
    sandbox_config_from_env,
    unpack_results,
)


def cfg(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(
        image="atlas-sandbox:py3.12", base_dir=str(tmp_path / "sandbox"), pids=256, tmpfs_size="512m", gid=1234,
        work_size="2g",
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
        "--mount",
        "type=tmpfs,dst=/work,tmpfs-size=2g",  # Section 16.4: the job's files sit inside its memory cap, not on disk
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
        "/srv/atlas/sandbox/job1:/stage:ro",  # inputs read-only; nothing a job writes reaches the data volume directly
        "-w",
        "/work",
        "atlas-sandbox:py3.12",
        "sh",
        "-c",
        RUN_SHELL,
        "sh",
        "python3",
        "/work/main.py",
    ]
    # The shell prologue/epilogue: inputs in, the job's stdout to /work/.stdout, the results tar on stdout, the job's
    # own exit code out (so 137 still reads as the cap or the deadline). Nothing writable but the tmpfs is mounted.
    # The copy carries no --preserve (fix round 6): uid 65534 does not own the /work mount point, and GNU cp applying
    # the source directory's timestamps to it failed with EPERM before the job ran (module docstring "Work directory").
    assert RUN_SHELL.startswith("cp -R /stage/. /work/ && \"$@\" >/work/.stdout; rc=$?;")
    assert "--preserve" not in RUN_SHELL
    assert RUN_SHELL.endswith("cd /work && tar -cf - .; exit $rc")
    assert not any(a.endswith(":/work:rw") for a in argv) and not any(a.endswith(":rw") for a in argv)
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
    assert f"{tmp_path / 'sandbox' / 'abc'}:/stage:ro" in argv
    assert docker.inspected_images == ["atlas-sandbox:py3.12"]
    # A stub has no stdout sink: its stdout is the job's and no results tar is expected (outputs_copied stays False).
    assert not res.outputs_copied and not hasattr(docker, "stdout_sink")
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


def test_network_grant_is_refused_whatever_the_egress_unit_says(tmp_path: Path) -> None:
    """A bridged container reaches the proxy not at all and DNS at best, and DNS to a recursive resolver is a tunnel
    around the allowlist (12.5): the grant buys nothing but the leak, so run() refuses it (README-contracts 'Sandbox'
    records the atlas-sandbox bridge as the remedy). build_argv keeps the shape for that future."""
    docker = StubDocker()
    for active in (False, True):
        with pytest.raises(SandboxError, match="tunnel around the allowlist"):
            run("print(1)", network=True, runner=docker, config=cfg(tmp_path), egress_active=lambda a=active: a)
    assert docker.calls == [] and docker.inspected_images == []
    assert not (tmp_path / "sandbox").exists()
    with_net = build_argv(
        "j", "/w", ["python3"], memory_mb=512, cpus=1, timeout_s=10, network=True, config=cfg(tmp_path)
    )
    assert "--network" not in with_net


@pytest.mark.parametrize("job_id", ["../x", "/abs", "a b", "x" * 65, "sb;rm", "a/b"])
def test_job_ids_that_could_escape_or_break_the_name_are_refused(tmp_path: Path, job_id: str) -> None:
    docker = StubDocker()
    with pytest.raises(SandboxError, match="job id"):
        run("print(1)", runner=docker, config=cfg(tmp_path), job_id=job_id)
    assert docker.calls == []
    assert not (tmp_path / "x").exists() and not (tmp_path / "sandbox" / "x").exists()
    assert not (tmp_path / "sandbox" / "a").exists()


def test_run_defaults_its_caps_to_the_configured_ones(tmp_path: Path) -> None:
    """SANDBOX_MEMORY / SANDBOX_CPUS / SANDBOX_TIMEOUT_S (README-contracts) are what run() uses when the caller
    passes nothing; the code constants are only the fallback of an unset environment."""
    docker = StubDocker()
    c = SandboxConfig(base_dir=str(tmp_path / "sandbox"), gid=1234, memory_mb=777, cpus=1.5, timeout_s=42)
    res = run("print(1)", runner=docker, config=c, job_id="caps")
    argv = list(res.argv)
    assert argv[argv.index("--memory") + 1] == "777m" and argv[argv.index("--cpus") + 1] == "1.5"
    assert argv[3] == "52" and "SANDBOX_TIMEOUT_S=42" in argv
    res = run("print(1)", memory_mb=512, runner=docker, config=c, job_id="caps2")
    assert list(res.argv)[list(res.argv).index("--memory") + 1] == "512m"


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
    monkeypatch.setenv("SANDBOX_WORK_SIZE", "4g")
    monkeypatch.setenv("SANDBOX_FSIZE", "1073741824")
    monkeypatch.setenv("SANDBOX_MEMORY", "2g")
    monkeypatch.setenv("SANDBOX_CPUS", "2")
    monkeypatch.setenv("SANDBOX_TIMEOUT_S", "300")
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX", "4096")
    c = sandbox_config_from_env()
    assert (c.image, c.base_dir, c.pids, c.tmpfs_size, c.fsize) == (
        "atlas-sandbox:py3.12",
        "/srv/atlas/sandbox",
        256,
        "512m",
        1073741824,
    )
    assert (c.memory_mb, c.cpus, c.timeout_s, c.output_max) == (2048, 2.0, 300, 4096)
    assert c.work_size == "4g"
    monkeypatch.delenv("SANDBOX_OUTPUT_MAX")
    monkeypatch.delenv("SANDBOX_WORK_SIZE")
    assert sandbox_config_from_env().output_max == 1 << 20
    assert sandbox_config_from_env().work_size == "2g"  # the default cap when 06d has not written the key
    assert c.run_gid >= 0
    assert parse_memory_mb("512m") == 512 and parse_memory_mb("2048") == 2048 and parse_memory_mb("1G") == 1024
    for bad in ("2 cows", "5m", ""):
        with pytest.raises(SandboxError):
            parse_memory_mb(bad)
    for key, value in (
        ("SANDBOX_PIDS", "lots"),
        ("SANDBOX_CPUS", "two"),
        ("SANDBOX_TIMEOUT_S", "5m"),
        ("SANDBOX_MEMORY", "x"),
    ):
        monkeypatch.setenv(key, value)
        with pytest.raises(SandboxError, match=key):
            sandbox_config_from_env()
        monkeypatch.delenv(key)


def test_launcher_keeps_a_bounded_amount_of_the_jobs_output(tmp_path: Path) -> None:
    """A job that prints without end must not grow the cpu worker until the OOM killer takes it (module docstring
    "Output bound"): the real runner drains both pipes keeping at most output_max bytes each and flags the cut. Proven
    with a plain python3 child (no docker): the bound is the launcher's, whatever argv it runs."""
    kept, cut = drain_bounded(io.BytesIO(b"x" * 100), 10)
    assert kept == b"x" * 10 and cut
    kept, cut = drain_bounded(io.BytesIO(b"short"), 10)
    assert kept == b"short" and not cut
    runner = SubprocessDocker(output_max=1000)
    code = "import sys; sys.stdout.write('o' * 50000); sys.stderr.write('e' * 10); sys.exit(3)"
    rc, out, err = runner.run([sys.executable, "-c", code], timeout_s=30)
    assert rc == 3 and out == "o" * 1000 and err == "e" * 10 and runner.last_truncated == (True, False)
    # Through run(): the flags land on the result and the stub path (no last_truncated) reads as not truncated.
    res = run("print(1)", runner=StubDocker(stdout="1\n"), config=cfg(tmp_path), job_id="bounded")
    assert not res.stdout_truncated and not res.stderr_truncated

    class Loud(StubDocker):
        last_truncated = (True, True)

    res = run("print(1)", runner=Loud(stdout="1\n"), config=cfg(tmp_path), job_id="bounded2")
    assert res.stdout_truncated and res.stderr_truncated


def _results_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o640
            tf.addfile(info, io.BytesIO(data))


def test_results_tar_streams_to_disk_and_unpacks_into_the_job_directory(tmp_path: Path) -> None:
    """Fix round 5: /work is a tmpfs `docker cp` cannot read, so the run line streams a tar of /work on stdout. The
    real runner writes that stream to the sink file (never into memory, whatever its size), unpack_results extracts
    it with the `data` filter into the job directory and the job's stdout is .stdout from the tar, bounded."""
    sink = tmp_path / RESULTS_TAR
    runner = SubprocessDocker(output_max=16)
    runner.stdout_sink = sink
    payload = b"T" * 200_000
    code = "import sys; sys.stdout.buffer.write(b'T' * 200_000); sys.stderr.write('e' * 40); sys.exit(0)"
    rc, out, err = runner.run([sys.executable, "-c", code], timeout_s=30)
    assert rc == 0 and out == "" and err == "e" * 16 and runner.last_truncated == (False, True)
    assert sink.read_bytes() == payload  # the whole stream, not output_max of it
    # A real tar: .stdout plus an output file land in the work dir; the tar is removed; stdout is bounded by `limit`.
    work = tmp_path / "job"
    work.mkdir()
    _results_tar(sink, {f"./{JOB_STDOUT}": b"hello world\n", "./out/result.txt": b"42", "./main.py": b"print(1)"})
    out, cut, ok = unpack_results(sink, work, 5)
    assert ok and out == "hello" and cut and not sink.exists()
    assert (work / "out" / "result.txt").read_text() == "42" and (work / "main.py").read_text() == "print(1)"
    # Hostile members are refused by the data filter: nothing lands outside, the result says not ok (rule §7.4).
    _results_tar(sink, {"../evil.txt": b"x"})
    assert unpack_results(sink, work, 100) == ("", False, False) and not (tmp_path / "evil.txt").exists()
    # An absolute member is confined, not honoured: the data filter strips the leading slash (tarfile docs), so it
    # lands under the job directory and nowhere else.
    _results_tar(sink, {"/etc/evil.txt": b"x"})
    assert unpack_results(sink, work, 100)[2] is True and (work / "etc" / "evil.txt").read_text() == "x"
    # No tar (killed before the epilogue) or an empty one: not ok, empty stdout, no fragment presented as output.
    assert unpack_results(sink, work, 100) == ("", False, False)
    sink.write_bytes(b"")
    assert unpack_results(sink, work, 100) == ("", False, False) and not sink.exists()
    sink.write_bytes(b"not a tar at all")
    assert unpack_results(sink, work, 100)[2] is False and not sink.exists()


def test_run_uses_the_sink_with_a_sink_capable_runner(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """run() points a sink-capable runner at <job dir>/.results.tar, reads the job's stdout from the unpacked tar and
    says when no tar arrived (the job was killed first)."""

    class SinkStub(StubDocker):
        supports_sink = True

        def __init__(self, members: dict[str, bytes] | None, **kw: object) -> None:
            super().__init__(**kw)  # type: ignore[arg-type]
            self.members = members
            self.stdout_sink: Path | None = None
            self.sinks: list[Path] = []

        def run(self, argv, *, timeout_s):  # type: ignore[no-untyped-def]
            assert self.stdout_sink is not None
            self.sinks.append(self.stdout_sink)
            if self.members is not None:
                _results_tar(self.stdout_sink, self.members)
            return super().run(argv, timeout_s=timeout_s)

    docker = SinkStub({f"./{JOB_STDOUT}": b"result line\n", "./made.txt": b"by the job"}, exit_code=0, stdout="")
    res = run("print(1)", runner=docker, config=cfg(tmp_path), job_id="sink", keep_work_dir=True)
    assert res.outputs_copied and res.stdout == "result line\n" and not res.stdout_truncated
    assert docker.sinks == [Path(res.work_dir) / RESULTS_TAR] and docker.stdout_sink is None  # cleared afterwards
    assert (Path(res.work_dir) / "made.txt").read_text() == "by the job"
    assert not (Path(res.work_dir) / RESULTS_TAR).exists()
    killed = SinkStub(None, exit_code=137, stdout="tar fragment", oom=True)
    with caplog.at_level("WARNING", logger="atlas.sandbox"):
        res = run("print(1)", runner=killed, config=cfg(tmp_path), job_id="killed")
    assert res.killed_by_cap and not res.outputs_copied and res.stdout == "" and "no results tar" in caplog.text
    # The default runner is built with the configured bound.
    assert SubprocessDocker().output_max == 1 << 20 and SandboxConfig(output_max=7).output_max == 7
