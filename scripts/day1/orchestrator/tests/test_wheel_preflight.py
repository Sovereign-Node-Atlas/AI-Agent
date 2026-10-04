"""scripts/day1/phase4/wheel_preflight.py (the Phase 4 step 1 wheel pre-flight) and the driver's `P4-wheels` recorder,
with NO network (CONVENTIONS.md §7.8): fetch() and head() are replaced by a fake PEP 503 index held in a dict, so the
parser, the platform/py-tag filter (fix round 4 caught a macosx_*_x86_64 wheel being admitted by a suffix test), the
exact-version vs bare-name selection with --prefer-local, and every exit code are pinned here. Loaded by path:
wheel_preflight.py is a script, not part of the atlas package (like test_phase3_loadtest.py does for loadtest.py).

The last test runs phase4-engines.sh's `_p4_record_info` (the local twin of record_v for the non-V id P4-wheels,
Section 21 scope note v0.3.2) against the real lib/common.sh in a temp state dir and reads the row back through
verify_table: fix round 5's blocker was a driver that called record_v with that id and died before `docker build` on
every fresh run."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

DAY1 = Path(__file__).resolve().parents[2]
PREFLIGHT = DAY1 / "phase4" / "wheel_preflight.py"
DRIVER = DAY1 / "phase4-engines.sh"
COMMON = DAY1 / "lib" / "common.sh"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_wheel_preflight", PREFLIGHT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p4_wheel_preflight"] = mod
    spec.loader.exec_module(mod)
    return mod


wp = _load()

INDEX = "https://idx.example/rocm/whl-next/"
FILES = "https://files.example/pkg/"

TORCH_OK = "torch-2.13.0+rocm10.0.0-cp312-cp312-manylinux_2_28_x86_64.whl"
TORCH_MAC = "torch-2.13.0+rocm10.0.0-cp312-cp312-macosx_11_0_x86_64.whl"
TORCH_311 = "torch-2.13.0+rocm10.0.0-cp311-cp311-manylinux_2_28_x86_64.whl"
TORCH_OLD = "torch-2.12.0+rocm10.0.0-cp312-cp312-linux_x86_64.whl"
TA_PLAIN = "torchaudio-2.13.0-cp312-cp312-manylinux_2_28_x86_64.whl"
TA_LOCAL = "torchaudio-2.13.0+rocm10.0.0-cp312-cp312-linux_x86_64.whl"
TA_NEWER = "torchaudio-2.14.0-cp312-cp312-linux_x86_64.whl"
DEVICE = "amd_torch_device_gfx1151-2.13.0+rocm10.0.0-py3-none-any.whl"
ROCM = "rocm-10.0.0-py3-none-any.whl"


def _page(*names: str) -> str:
    """A PEP 503 project page: relative hrefs on another host, a sha256 fragment, the file name as the link text."""
    links = "".join(
        f'<a href="{FILES}{name.replace("+", "%2B")}#sha256=00">{name}</a><br/>\n' for name in names
    )
    return f"<!DOCTYPE html><html><body>{links}</body></html>"


DEFAULT_PAGES: dict[str, tuple[int, str]] = {
    INDEX: (200, "<html><body><a href='torch/'>torch</a></body></html>"),
    INDEX + "torch/": (200, _page(TORCH_OK, TORCH_MAC, TORCH_311, TORCH_OLD)),
    INDEX + "torchaudio/": (200, _page(TA_PLAIN, TA_LOCAL, TA_NEWER)),
    INDEX + "amd-torch-device-gfx1151/": (200, _page(DEVICE)),
    INDEX + "rocm/": (200, _page(ROCM)),
}


class FakeIndex:
    """fetch()/head() stand-ins. pages: url -> (status, html); a url absent from pages is a 404. unreachable: urls
    whose fetch raises IndexUnreachable. head_codes: wheel url -> http code (0 = transport failure), default 200."""

    def __init__(self, pages: dict[str, tuple[int, str]] | None = None) -> None:
        self.pages = dict(DEFAULT_PAGES if pages is None else pages)
        self.unreachable: set[str] = set()
        self.head_codes: dict[str, int] = {}
        self.fetched: list[str] = []
        self.headed: list[str] = []

    def fetch(self, url: str, *, any_http_ok: bool = False) -> tuple[int, str]:
        self.fetched.append(url)
        if url in self.unreachable:
            raise wp.IndexUnreachable(f"{url}: URLError: Tunnel connection failed: 403 Forbidden")
        status, body = self.pages.get(url, (404, ""))
        if status >= 400 and not (status == 404 or any_http_ok):
            raise wp.IndexUnreachable(f"{url}: HTTP {status}")
        return status, body

    def head(self, url: str) -> tuple[int, int | None, str]:
        self.headed.append(url)
        code = self.head_codes.get(url, 200)
        if code == 0:
            return 0, None, "URLError: Tunnel connection failed: 403 Forbidden"
        if code != 200:
            return code, None, f"HTTP {code} Not Found"
        return 200, 123 * 1048576, ""


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> FakeIndex:
    fake = FakeIndex()
    monkeypatch.setattr(wp, "fetch", fake.fetch)
    monkeypatch.setattr(wp, "head", fake.head)
    return fake


# --- PEP 427 names and the compatibility filter -----------------------------------------------------------------------


def test_parse_wheel_five_and_six_parts() -> None:
    w = wp.parse_wheel(TORCH_OK)
    assert w is not None
    assert (w.name, w.version, w.build, w.py, w.abi, w.plat) == (
        "torch", "2.13.0+rocm10.0.0", "", "cp312", "cp312", "manylinux_2_28_x86_64")
    w6 = wp.parse_wheel("pkg-1.0-1build-py3-none-any.whl")
    assert w6 is not None and w6.build == "1build" and w6.py == "py3" and w6.plat == "any"
    assert wp.parse_wheel("pkg-1.0-py3-none.whl") is None  # four parts
    assert wp.parse_wheel("pkg-1.0.tar.gz") is None


@pytest.mark.parametrize(
    ("filename", "ok"),
    [
        (TORCH_OK, True),
        ("x-1.0-cp312-abi3-linux_x86_64.whl", True),
        ("x-1.0-py3-none-any.whl", True),
        ("x-1.0-py312-none-any.whl", True),
        ("x-1.0-py2.py3-none-any.whl", True),
        ("x-1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", True),
        (TORCH_311, False),
        (TORCH_MAC, False),  # macosx_11_0_x86_64 ends with _x86_64: a suffix test admitted it (fix round 4)
        ("x-1.0-cp312-cp312-win_amd64.whl", False),
        ("x-1.0-cp312-cp312-manylinux_2_28_aarch64.whl", False),
        ("x-1.0-py2-none-any.whl", False),
    ],
)
def test_compatible_filters_py_tag_and_platform(filename: str, ok: bool) -> None:
    w = wp.parse_wheel(filename)
    assert w is not None
    assert wp.compatible(w, "cp312") is ok


def test_version_key_orders_numerically() -> None:
    assert wp.version_key("2.14.0") > wp.version_key("2.13.0") > wp.version_key("2.9.0")
    assert wp.version_key("2.13.0+rocm10.0.0") > wp.version_key("2.13.0")


def test_norm_name_pep503() -> None:
    assert wp.norm_name("amd_torch_device.gfx1151") == "amd-torch-device-gfx1151"
    assert wp.norm_name("Torch") == "torch"


# --- resolve(): selection, misses and the transport case ------------------------------------------------------------


def test_resolve_exact_version_picks_the_linux_cp312_wheel(index: FakeIndex) -> None:
    rec, miss = wp.resolve(INDEX, "torch==2.13.0+rocm10.0.0", "cp312", "rocm10.0.0")
    assert miss is None
    assert rec["filename"] == TORCH_OK
    assert rec["url"] == FILES + TORCH_OK.replace("+", "%2B")  # the fragment is dropped, the href kept as served
    assert rec["http"] == 200 and rec["bytes"] == 123 * 1048576 and rec["page_http"] == 200
    assert index.headed == [rec["url"]]


def test_resolve_version_miss_names_expected_file_and_what_was_there(index: FakeIndex) -> None:
    rec, miss = wp.resolve(INDEX, "torch==2.99.0+rocm10.0.0", "cp312", "rocm10.0.0")
    assert rec["filename"] is None and index.headed == []
    assert miss is not None
    assert miss.startswith("torch-2.99.0+rocm10.0.0-cp312-*.whl not on " + INDEX + "torch/")
    assert TORCH_OK in miss and TORCH_MAC in miss  # every file of the project the page listed


def test_resolve_missing_project_page_is_a_404_miss(index: FakeIndex) -> None:
    rec, miss = wp.resolve(INDEX, "torchvision==0.28.0+rocm10.0.0", "cp312", "rocm10.0.0")
    assert rec["page_http"] == 404
    assert miss is not None and "no project page" in miss and "(HTTP 404)" in miss


def test_resolve_bare_name_prefers_local_tag_then_newest(index: FakeIndex) -> None:
    rec, miss = wp.resolve(INDEX, "torchaudio", "cp312", "rocm10.0.0")
    assert miss is None and rec["filename"] == TA_LOCAL
    rec, miss = wp.resolve(INDEX, "torchaudio", "cp312", "")
    assert miss is None and rec["filename"] == TA_NEWER


def test_resolve_underscored_project_matches_dashed_spec(index: FakeIndex) -> None:
    rec, miss = wp.resolve(INDEX, "amd-torch-device-gfx1151", "cp312", "rocm10.0.0")
    assert miss is None and rec["filename"] == DEVICE and rec["project"] == "amd-torch-device-gfx1151"


def test_resolve_head_http_error_is_a_miss_naming_the_url(index: FakeIndex) -> None:
    index.head_codes[FILES + TORCH_OK.replace("+", "%2B")] = 404
    rec, miss = wp.resolve(INDEX, "torch==2.13.0+rocm10.0.0", "cp312", "rocm10.0.0")
    assert rec["http"] == 404
    assert miss is not None and "listed on" in miss and "HEAD" in miss and "HTTP 404" in miss


def test_resolve_head_transport_failure_raises_naming_the_host(index: FakeIndex) -> None:
    """A denied CONNECT to the wheel host is not a missing wheel (fix round 5): the allowlist is the cause."""
    index.head_codes[FILES + TORCH_OK.replace("+", "%2B")] = 0
    with pytest.raises(wp.IndexUnreachable) as ei:
        wp.resolve(INDEX, "torch==2.13.0+rocm10.0.0", "cp312", "rocm10.0.0")
    text = str(ei.value)
    assert "files.example" in text and "config/allowlist.txt" in text and "Tunnel connection failed" in text
    assert TORCH_OK in text


# --- main(): exit codes and the json ---------------------------------------------------------------------------------

SPECS = ["rocm==10.0.0", "torch==2.13.0+rocm10.0.0", "torchaudio", "amd-torch-device-gfx1151"]


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str], *specs: str) -> tuple[int, str, dict[str, Any]]:
    out = tmp_path / "wheels-preflight.json"
    rc = wp.main(["--index", INDEX.rstrip("/"), "--python-tag", "cp312", "--prefer-local", "rocm10.0.0",
                  "--json", str(out), *(specs or SPECS)])
    summary = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    return rc, summary, json.loads(out.read_text(encoding="utf-8"))


def test_main_ok_exit_0_summary_and_json(index: FakeIndex, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 0
    assert summary.startswith("P4-wheels: ok, 4 wheels on " + INDEX)
    assert TORCH_OK in summary and TA_LOCAL in summary and "(123 MiB)" in summary
    assert report["status"] == "ok" and report["missing"] == [] and report["root_http"] == 200
    assert report["index"] == INDEX  # the trailing slash is restored
    assert [r["filename"] for r in report["wheels"]] == [ROCM, TORCH_OK, TA_LOCAL, DEVICE]
    assert not (tmp_path / "wheels-preflight.json.tmp").exists()


def test_main_missing_wheel_exit_1(index: FakeIndex, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, summary, report = _run(tmp_path, capsys, "torch==2.13.0+rocm10.0.0", "torchvision==0.28.0+rocm10.0.0")
    assert rc == 1
    assert summary.startswith("P4-wheels: MISSING on " + INDEX)
    assert "torchvision-0.28.0+rocm10.0.0-cp312-*.whl" in summary
    assert report["status"] == "missing" and len(report["missing"]) == 1
    assert report["wheels"][0]["filename"] == TORCH_OK  # the hit is still recorded beside the miss


def test_main_root_unreachable_exit_2(index: FakeIndex, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    index.unreachable.add(INDEX)
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 2
    assert "cannot be fetched through proxy" in summary and "Tunnel connection failed" in summary
    assert report["status"] == "unreachable" and report["wheels"] == []
    assert index.fetched == [INDEX]  # nothing else was tried


def test_main_root_404_is_only_a_note_when_project_pages_exist(index: FakeIndex, tmp_path: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    """pip never fetches the channel root; a 404 there must not block a build pip would complete (fix round 5)."""
    index.pages[INDEX] = (404, "")
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 0 and summary.startswith("P4-wheels: ok")
    assert report["root_http"] == 404 and report["status"] == "ok"


def test_main_root_403_is_only_a_note_too(index: FakeIndex, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    index.pages[INDEX] = (403, "")
    rc, _summary, report = _run(tmp_path, capsys)
    assert rc == 0 and report["root_http"] == 403


def test_main_every_project_404_means_channel_gone_exit_2(index: FakeIndex, tmp_path: Path,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    index.pages = {INDEX: (200, "<html></html>")}
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 2
    assert "every project page" in summary and "channel URL is wrong or gone" in summary and "rocm_index" in summary
    assert report["status"] == "unreachable" and all(r["page_http"] == 404 for r in report["wheels"])


def test_main_project_page_unreachable_exit_2(index: FakeIndex, tmp_path: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    index.unreachable.add(INDEX + "torch/")
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 2 and "a project page or a wheel host was not" in summary and INDEX + "torch/" in summary
    assert report["status"] == "unreachable"


def test_main_wheel_host_denied_exit_2_names_host(index: FakeIndex, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    index.head_codes[FILES + TORCH_OK.replace("+", "%2B")] = 0
    rc, summary, report = _run(tmp_path, capsys)
    assert rc == 2
    assert "files.example" in summary and "config/allowlist.txt" in summary and TORCH_OK in summary
    assert report["status"] == "unreachable"


# --- the driver's P4-wheels recorder against the real lib/common.sh ---------------------------------------------------


def _extract_function(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, flags=re.S | re.M)
    assert m is not None, f"{name}() not found in {script}"
    return m.group(0)


def test_p4_record_info_writes_a_row_common_sh_reads_back(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    func = _extract_function(DRIVER, "_p4_record_info")
    assert "record_v" not in func.splitlines()[0]  # it is the twin, not a wrapper around record_v
    (tmp_path / "twin.sh").write_text(func, encoding="utf-8")
    msg = 'P4-wheels: ok, 1 wheels "quoted" \\ backslash'
    script = (
        'set -Eeuo pipefail; export ATLAS_ETC="$1/etc" ATLAS_STATE="$1/state" ATLAS_OPT="$1/opt" ATLAS_SRV="$1/srv" '
        'ATLAS_PHASE=phase4 ATLAS_ENTRY=./atlas-day1.sh; source "$2"; source "$1/twin.sh"; _p4_record_info "$3"; '
        'echo ---; verify_table V11 P4-wheels; echo ---; cat "$ATLAS_VERIFY_FILE"'
    )
    res = subprocess.run([bash, "-c", script, "_", str(tmp_path), str(COMMON), msg],
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    head, table, raw = res.stdout.split("---\n")
    assert "verify P4-wheels=info:" in head
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["id"] == "P4-wheels" and rows[0]["result"] == "info" and rows[0]["phase"] == "phase4"
    assert rows[0]["msg"] == msg and set(rows[0]) == {"ts", "phase", "id", "result", "msg"}
    lines = table.splitlines()
    assert lines[0].startswith("ID")
    assert any(line.startswith("V11") and "missing" in line for line in lines)
    assert any(line.startswith("P4-wheels") and " info " in line and "quoted" in line for line in lines)
