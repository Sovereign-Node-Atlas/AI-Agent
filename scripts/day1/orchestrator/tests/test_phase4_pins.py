"""The Phase 4 torchaudio pin (S51, 2026-10-11) and the checks that hold it, with NO network, no docker and no GPU
(CONVENTIONS.md §7.8).

AMD's shared wheel index serves ROCm 10.0.0 and 10.1.0 builds side by side. A bare `torchaudio` in the base image's
ROCm install would most likely have taken a +rocm10.1.0 build next to torch 2.13.0+rocm10.0.0 (INFERRED: upstream
torchaudio 2.11 declares no torch requirement), so torchaudio is pinned to AMD's documented gfx1151 pairing,
2.11.0.2+rocm10.0.0, local label included ("==2.11.0.2" alone also matches the 10.1 build). Pinned here:
  * docker/rocm-base/Dockerfile: ARG TORCHAUDIO_VER, the exact install spec and the exact constraints-line assertion;
    config/phase4-engines.json base_image agrees with the ARGs; no file advises dropping the pins any more.
  * phase4-engines.sh _p4_wheel_preflight (extracted, run in bash with stubs): it reads ARG TORCHAUDIO_VER, dies when
    the json's torchaudio disagrees or the ARG is missing, and hands the pre-flight the exact spec, never a bare name.
  * verify/v11-rocm-selftest.sh (a copy, docker stubbed): it passes base_image.torchaudio into the container as
    V11_EXPECT_TORCHAUDIO and fails before any container when the json has no pin.
  * phase4/selftest.py torch_gpu() (fake torch/torchaudio modules): the installed torchaudio must import and equal the
    pin exactly; without the variable (the community-image diagnostic) the version is only reported.
Loaded by path like test_wheel_preflight.py: none of these files is part of the atlas package."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

DAY1 = Path(__file__).resolve().parents[2]
DOCKERFILE = DAY1 / "docker" / "rocm-base" / "Dockerfile"
P4_JSON = DAY1 / "config" / "phase4-engines.json"
DRIVER = DAY1 / "phase4-engines.sh"
V11 = DAY1 / "verify" / "v11-rocm-selftest.sh"
SELFTEST = DAY1 / "phase4" / "selftest.py"
COMMON = DAY1 / "lib" / "common.sh"

TA_PIN = "2.11.0.2+rocm10.0.0"  # AMD's ROCm 10.0.0 gfx1151 pairing for torch 2.13.0 (VERIFIED, AMD install docs)


def _args(text: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG ([A-Z_]+)=(\S+)\s*$", text, flags=re.M))


def _rocm_run(text: str) -> str:
    """The RUN that installs from the AMD index (it also writes constraints-rocm.txt), up to the next blank line."""
    runs = [chunk.split("\n\n", 1)[0] for chunk in re.split(r"\n(?=RUN )", text) if chunk.startswith("RUN ")]
    runs = [r for r in runs if '--index-url "${ROCM_INDEX}"' in r]
    assert len(runs) == 1, "expected exactly one RUN installing from ${ROCM_INDEX}"
    return runs[0]


# --- the Dockerfile and the json -------------------------------------------------------------------------------------


def test_dockerfile_pins_torchaudio_with_its_local_label() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    args = _args(text)
    assert args["TORCHAUDIO_VER"] == "2.11.0.2"
    run = _rocm_run(text)
    assert '"torchaudio==${TORCHAUDIO_VER}+rocm${ROCM_VER}"' in run
    # No bare (or label-less) torchaudio requirement anywhere in that install.
    assert not re.search(r"^\s*\"?torchaudio\"?\s*\\$", run, flags=re.M)
    assert '"torchaudio==${TORCHAUDIO_VER}" ' not in run
    # The build asserts the exact constraints line, as it does for torch.
    assert 'grep -q "^torchaudio==${TORCHAUDIO_VER}+rocm${ROCM_VER}$" /opt/atlas/constraints-rocm.txt' in run
    assert "grep -q '^torchaudio==' " not in run
    assert 'grep -q "^torch==${TORCH_VER}+rocm${ROCM_VER}$" /opt/atlas/constraints-rocm.txt' in run


def test_json_base_image_agrees_with_the_dockerfile_args() -> None:
    args = _args(DOCKERFILE.read_text(encoding="utf-8"))
    base = json.loads(P4_JSON.read_text(encoding="utf-8"))["base_image"]
    rocm = base["rocm_version"]
    assert args["ROCM_VER"] == rocm == "10.0.0"
    assert base["torch"] == f"{args['TORCH_VER']}+rocm{rocm}"
    assert base["torchvision"] == f"{args['TORCHVISION_VER']}+rocm{rocm}"
    assert base["torchaudio"] == f"{args['TORCHAUDIO_VER']}+rocm{rocm}" == TA_PIN


@pytest.mark.parametrize("path", [DOCKERFILE, DRIVER], ids=["Dockerfile", "phase4-engines.sh"])
def test_no_advice_to_drop_the_pins(path: Path) -> None:
    """S51: 'drop the == pins and let the extras resolve' would now silently adopt ROCm 10.1 and torch 2.14."""
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(?<!NEVER )(?<!never )drop the (==|\"==\") pins", text)
    assert "let the [device-gfx1151] extras resolve" not in text and "let the extras resolve" not in text
    assert "NEVER drop the" in text and "SAME" in text and "watch-list" in text


def test_s15_issue_references_are_corrected_in_the_scripts() -> None:
    """S51: the hang reports live in ROCm/legacy-rocm-build (ROCm/pytorch #6530 is a 404); #6182 was closed as a
    container setup problem and is no longer cited as an open kernel report."""
    offenders = []
    for path in DAY1.rglob("*"):
        if path.suffix not in {".sh", ".py", ".json"} or "__pycache__" in path.parts or not path.is_file():
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for bad in ("ROCm/pytorch #6530", "ROCm/pytorch #6182", "ROCm/ROCm #6182", "#6530 #6182", "#6530/#6182"):
            if bad in text:
                offenders.append(f"{path.relative_to(DAY1)}: {bad}")
    assert offenders == []


# --- phase4-engines.sh _p4_wheel_preflight ----------------------------------------------------------------------------


def _extract_function(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, flags=re.S | re.M)
    assert m is not None, f"{name}() not found in {script}"
    return m.group(0)


def _p4_cfg_line() -> str:
    m = re.search(r"^_p4_cfg\(\) \{.*\}$", DRIVER.read_text(encoding="utf-8"), flags=re.M)
    assert m is not None, "_p4_cfg() one-liner not found in phase4-engines.sh"
    return m.group(0)


STUB_PREFLIGHT = """import json, os, sys
with open(os.environ["STUB_ARGV"], "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh)
print("P4-wheels: ok, stub")
"""


def _run_preflight(tmp_path: Path, *, base: dict[str, Any] | None = None, dockerfile: str | None = None
                   ) -> tuple[subprocess.CompletedProcess[str], list[str] | None]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    cfg = json.loads(P4_JSON.read_text(encoding="utf-8"))
    if base is not None:
        cfg["base_image"] = base
    (tmp_path / "p4.json").write_text(json.dumps(cfg), encoding="utf-8")
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker" / "Dockerfile").write_text(
        DOCKERFILE.read_text(encoding="utf-8") if dockerfile is None else dockerfile, encoding="utf-8")
    (tmp_path / "day1" / "phase4").mkdir(parents=True)
    (tmp_path / "day1" / "phase4" / "wheel_preflight.py").write_text(STUB_PREFLIGHT, encoding="utf-8")
    funcs = _p4_cfg_line() + "\n" + _extract_function(DRIVER, "_p4_wheel_preflight")
    (tmp_path / "funcs.sh").write_text(funcs, encoding="utf-8")
    script = (
        'set -Eeuo pipefail; T="$1"; die() { echo "DIE: $*"; exit 3; }; log() { :; }; proxy_env() { :; }; '
        '_p4_record_info() { echo "INFO: $1"; }; '
        'P4_JSON="$T/p4.json"; P4_DOCKERFILE_DIR="$T/docker"; ATLAS_DAY1_DIR="$T/day1"; P4_STATE="$T/state"; '
        'export STUB_ARGV="$T/argv.json"; source "$T/funcs.sh"; _p4_wheel_preflight; echo DONE'
    )
    res = subprocess.run([bash, "-c", script, "_", str(tmp_path)], capture_output=True, text=True, check=False)
    argv_file = tmp_path / "argv.json"
    argv = json.loads(argv_file.read_text(encoding="utf-8")) if argv_file.exists() else None
    return res, argv


def test_preflight_passes_the_exact_torchaudio_spec(tmp_path: Path) -> None:
    res, argv = _run_preflight(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "INFO: P4-wheels: ok, stub" in res.stdout and "DONE" in res.stdout
    assert argv is not None
    specs = argv[argv.index("--json") + 2:]
    assert specs == ["rocm==10.0.0", "torch==2.13.0+rocm10.0.0", "torchvision==0.28.0+rocm10.0.0",
                     f"torchaudio=={TA_PIN}", "amd-torch-device-gfx1151"]
    assert "torchaudio" not in specs  # never the bare name (S51)
    assert argv[argv.index("--prefer-local") + 1] == "rocm10.0.0"


@pytest.mark.parametrize("bad", ["2.11.0.3+rocm10.1.0", "2.11.0.2", None])
def test_preflight_dies_when_the_json_torchaudio_disagrees(tmp_path: Path, bad: str | None) -> None:
    base = dict(json.loads(P4_JSON.read_text(encoding="utf-8"))["base_image"])
    if bad is None:
        base.pop("torchaudio")
    else:
        base["torchaudio"] = bad
    res, argv = _run_preflight(tmp_path, base=base)
    assert res.returncode == 3, res.stdout + res.stderr
    assert f"DIE: config/phase4-engines.json base_image.torchaudio is '{bad or ''}' but the Dockerfile pins {TA_PIN}" \
        in res.stdout
    assert argv is None  # the pre-flight never ran


@pytest.mark.parametrize("bad", ["0.29.0a0+rocm10.1.0", "0.28.0", None])
def test_preflight_dies_when_the_json_torchvision_disagrees(tmp_path: Path, bad: str | None) -> None:
    base = dict(json.loads(P4_JSON.read_text(encoding="utf-8"))["base_image"])
    if bad is None:
        base.pop("torchvision")
    else:
        base["torchvision"] = bad
    res, argv = _run_preflight(tmp_path, base=base)
    assert res.returncode == 3, res.stdout + res.stderr
    assert f"DIE: config/phase4-engines.json base_image.torchvision is '{bad or ''}' but the Dockerfile pins " \
        "0.28.0+rocm10.0.0" in res.stdout
    assert argv is None


def test_missing_wheel_message_keeps_the_rocm_version_and_the_label() -> None:
    """S51: the pre-flight's missing-wheel advice is the main path for an unresolvable pin."""
    body = _extract_function(DRIVER, "_p4_wheel_preflight")
    msg = next(line for line in body.splitlines() if "a wheel the Dockerfile pins is not on the index" in line)
    assert "the SAME version" in msg and "NEVER drop a pin" in msg and "watch-list re-test" in msg
    assert "base_image.version" in msg and "org.atlas.rocm-base.version" in msg


def test_preflight_dies_without_the_torchaudio_arg(tmp_path: Path) -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    without = re.sub(r"^ARG TORCHAUDIO_VER=.*\n", "", text, flags=re.M)
    assert without != text
    res, argv = _run_preflight(tmp_path, dockerfile=without)
    assert res.returncode == 3, res.stdout + res.stderr
    assert "DIE:" in res.stdout and "'ARG TORCHAUDIO_VER='" in res.stdout
    assert argv is None


# --- verify/v11-rocm-selftest.sh --------------------------------------------------------------------------------------

STUB_DOCKER = """#!/bin/bash
case "$1" in
  image) exit 0 ;;
  rm) exit 0 ;;
  run) printf '%s\\n' "$@" >"$V11T/run-args"
       echo 'V11JSON {"ok":true,"steps":{}}'
       echo "V11 ok: stub"
       exit 0 ;;
esac
exit 0
"""


def _run_v11(tmp_path: Path, base: dict[str, Any] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    cfg = json.loads(P4_JSON.read_text(encoding="utf-8"))
    if base is not None:
        cfg["base_image"] = base
    (tmp_path / "p4.json").write_text(json.dumps(cfg), encoding="utf-8")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "docker").write_text(STUB_DOCKER, encoding="utf-8")
    (tmp_path / "bin" / "docker").chmod(0o755)
    (tmp_path / "kfd").touch()
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "docker.env").write_text("ATLAS_UID=999\nATLAS_GID=999\nRENDER_GID=992\nVIDEO_GID=44\n",
                                                encoding="utf-8")
    (tmp_path / "srv" / "engines" / "miopen").mkdir(parents=True)
    text = V11.read_text(encoding="utf-8")
    src = 'source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"'
    assert src in text and 'json="$ATLAS_DAY1_DIR/config/phase4-engines.json"' in text and "[[ -e /dev/kfd ]]" in text
    text = (text.replace(src, f'source "{COMMON}"')
            .replace('json="$ATLAS_DAY1_DIR/config/phase4-engines.json"', f'json="{tmp_path / "p4.json"}"')
            .replace("[[ -e /dev/kfd ]]", f'[[ -e "{tmp_path / "kfd"}" ]]'))
    (tmp_path / "v11.sh").write_text(text, encoding="utf-8")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "V11T": str(tmp_path), "ATLAS_ETC": str(tmp_path / "etc"),
           "ATLAS_STATE": str(tmp_path / "state"), "ATLAS_SRV": str(tmp_path / "srv"),
           "ATLAS_OPT": str(tmp_path / "opt")}
    res = subprocess.run([bash, str(tmp_path / "v11.sh"), "atlas/rocm-base:10.0.0"], capture_output=True, text=True,
                         check=False, env=env)
    args_file = tmp_path / "run-args"
    return res, args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []


def test_v11_passes_the_torchaudio_pin_into_the_container(tmp_path: Path) -> None:
    res, args = _run_v11(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.startswith("V11 ok: stub")
    assert f"V11_EXPECT_TORCHAUDIO={TA_PIN}" in args and "V11_EXPECT_DEVICE_WHEEL=1" in args
    assert args[args.index(f"V11_EXPECT_TORCHAUDIO={TA_PIN}") - 1] == "-e"


def test_v11_fails_before_any_container_without_a_json_pin(tmp_path: Path) -> None:
    base = dict(json.loads(P4_JSON.read_text(encoding="utf-8"))["base_image"])
    base.pop("torchaudio")
    res, args = _run_v11(tmp_path, base)
    assert res.returncode == 1, res.stdout + res.stderr
    assert res.stdout.startswith("V11 fail: no base_image.torchaudio pin in ")
    assert args == []  # docker run never happened


# --- phase4/selftest.py ----------------------------------------------------------------------------------------------


def _load_selftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_v11_selftest", SELFTEST)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


st = _load_selftest()


def _fake_torch() -> ModuleType:
    torch = ModuleType("torch")
    props = SimpleNamespace(gcnArchName="gfx1151", name="AMD Radeon 8065S", total_memory=96 * 2**30)
    torch.cuda = SimpleNamespace(is_available=lambda: True, get_device_properties=lambda _i: props,
                                 get_arch_list=lambda: ["gfx1151"])
    torch.version = SimpleNamespace(hip="7.13")
    torch.__version__ = "2.13.0+rocm10.0.0"
    return torch


@pytest.fixture
def fake_gpu(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """torch/torchaudio stand-ins; set installed["torchaudio"] to the distribution version (None = not installed)."""
    installed: dict[str, str | None] = {"torchaudio": TA_PIN}
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    monkeypatch.setitem(sys.modules, "torchaudio", ModuleType("torchaudio"))
    real_version = importlib.metadata.version

    def version(name: str) -> str:
        if name == "torchaudio":
            v = installed["torchaudio"]
            if v is None:
                raise importlib.metadata.PackageNotFoundError(name)
            return v
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    for var in ("HSA_OVERRIDE_GFX_VERSION", "V11_EXPECT_DEVICE_WHEEL", "V11_EXPECT_TORCHAUDIO"):
        monkeypatch.delenv(var, raising=False)
    return installed


def test_selftest_accepts_the_pinned_torchaudio(fake_gpu: dict[str, str | None],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V11_EXPECT_TORCHAUDIO", TA_PIN)
    out = st.torch_gpu()
    assert out["torchaudio"] == TA_PIN and out["torch"] == "2.13.0+rocm10.0.0"


@pytest.mark.parametrize("installed", ["2.11.0.2+rocm10.1.0", "2.11.0.3+rocm10.1.0", "2.11.0.2"])
def test_selftest_fails_on_any_other_torchaudio_build(fake_gpu: dict[str, str | None],
                                                      monkeypatch: pytest.MonkeyPatch, installed: str) -> None:
    """S51: another ROCm's build, a newer one, or a label-less one all fail V11's torch_device step."""
    monkeypatch.setenv("V11_EXPECT_TORCHAUDIO", TA_PIN)
    fake_gpu["torchaudio"] = installed
    with pytest.raises(AssertionError, match=re.escape(f"torchaudio is {installed}, not the pinned {TA_PIN}")):
        st.torch_gpu()


def test_selftest_records_an_import_failure_of_the_pinned_torchaudio(fake_gpu: dict[str, str | None],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The right build that will not load is recorded, not fatal: it fails CosyVoice 2 and Stable Audio Open alone."""
    monkeypatch.setenv("V11_EXPECT_TORCHAUDIO", TA_PIN)
    monkeypatch.setitem(sys.modules, "torchaudio", None)  # import raises ImportError, like a library that cannot load
    out = st.torch_gpu()
    assert out["torchaudio"] == TA_PIN and out["torchaudio_import"].startswith("failed: ")


def test_selftest_fails_when_torchaudio_is_not_installed(fake_gpu: dict[str, str | None],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V11_EXPECT_TORCHAUDIO", TA_PIN)
    fake_gpu["torchaudio"] = None
    with pytest.raises(AssertionError, match=re.escape(f"torchaudio is not installed, not the pinned {TA_PIN}")):
        st.torch_gpu()


def test_selftest_without_the_variable_only_reports(fake_gpu: dict[str, str | None],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The community-image diagnostic runs without V11_EXPECT_TORCHAUDIO: any torchaudio, or none, passes."""
    fake_gpu["torchaudio"] = "2.11.0.3+rocm10.1.0"
    assert st.torch_gpu()["torchaudio"] == "2.11.0.3+rocm10.1.0"
    monkeypatch.setitem(sys.modules, "torchaudio", None)
    out = st.torch_gpu()
    assert out["torchaudio"] == "2.11.0.3+rocm10.1.0" and out["torchaudio_import"].startswith("failed: ")
    fake_gpu["torchaudio"] = None
    assert st.torch_gpu()["torchaudio"] == "not installed"


def test_selftest_summary_names_torchaudio_and_a_mismatch_fails_v11(fake_gpu: dict[str, str | None],
                                                                    monkeypatch: pytest.MonkeyPatch,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("rocminfo", "matmul", "big_alloc", "diffusion_2_steps"):
        monkeypatch.setattr(st, name, lambda: {})
    monkeypatch.setenv("V11_EXPECT_TORCHAUDIO", TA_PIN)
    monkeypatch.delenv("V11_OUT", raising=False)
    monkeypatch.setattr(st, "results", {})
    assert st.main() == 0
    assert f"torchaudio={TA_PIN}" in capsys.readouterr().out.splitlines()[-1]
    monkeypatch.setattr(st, "results", {})
    fake_gpu["torchaudio"] = "2.11.0.3+rocm10.1.0"
    assert st.main() == 1
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("V11 FAIL: torch_device: AssertionError(") and "not the pinned" in last
