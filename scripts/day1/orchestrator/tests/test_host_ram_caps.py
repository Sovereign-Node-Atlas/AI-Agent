"""Measures 6 to 9 of research_notes/ATLAS v0.4.0 build record/answers.md section 1.5, approved by the Principal on
2026-10-17: host-RAM bounds outside the Arbiter's budget, pinned in the files that set them (CONVENTIONS.md §7.8: no
live services). The /tmp tmpfs cap (measure 7), the Celery cpu concurrency rule run in bash with nproc stubbed
(measure 6), the memory caps of the voice and document containers (measure 8) and the OOM victim order (measure 9).
Measure 5 (--cache-ram) is in test_engine_env.py and measure 10 (stop a failed start) in test_arbiter.py."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DAY1 = Path(__file__).resolve().parents[2]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_tmp_tmpfs_is_capped_at_4g() -> None:
    """Measure 7: 16G was the whole host reserve, and a tmpfs page cannot be reclaimed on a node with no swap."""
    text = (DAY1 / "phase1" / "03-mounts.sh").read_text(encoding="utf-8")
    assert re.findall(r"^Options=(.*)$", text, re.M) == ["mode=1777,strictatime,nosuid,nodev,size=4G,nr_inodes=1m"]
    assert "the 4G cap applies from the next boot" in text and "16G cap" not in text


def test_celery_cpu_concurrency_is_min_8_and_nproc_minus_2() -> None:
    """Measure 6: min(8, nproc - 2), at least 1, computed by phase2/02-orchestrator.sh into CELERY_CPU_CONCURRENCY."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    src = (DAY1 / "phase2" / "02-orchestrator.sh").read_text(encoding="utf-8")
    m = re.search(r"^  cpu_conc=\$\(\( \$\(nproc\) - 2 \)\)\n(?:  .*\n)*?  \(\( cpu_conc >= 1 \)\) \|\| cpu_conc=1\n",
                  src, re.M)
    assert m is not None, "the cpu_conc lines of _orch_env_write moved"
    for n, want in ((1, 1), (2, 1), (3, 1), (4, 2), (9, 7), (10, 8), (32, 8), (64, 8)):
        res = subprocess.run([bash, "-c", f"nproc() {{ echo {n}; }}\n{m.group(0)}echo \"$cpu_conc\""],
                             capture_output=True, text=True, check=False)
        assert (res.returncode, res.stdout.strip()) == (0, str(want)), (n, res.stderr)
    assert 'ensure_kv "$ORCH_ENV" CELERY_CPU_CONCURRENCY "$cpu_conc"' in src
    unit = (DAY1 / "systemd" / "atlas-celery-cpu.service").read_text(encoding="utf-8")
    assert "--concurrency=${CELERY_CPU_CONCURRENCY}" in unit and "min(8, nproc - 2)" in unit


def test_voice_and_document_containers_are_capped_without_swap() -> None:
    """Measure 8: generous caps, at least the suggested 2g / 3g / 6g, with memswap_limit = mem_limit (no swap)."""
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load((DAY1 / "docker" / "core" / "compose.voice.yml").read_text(encoding="utf-8"))["services"]
    caps = {k: (v.get("mem_limit"), v.get("memswap_limit")) for k, v in services.items()}
    assert caps == {"kokoro": ("3g", "3g"), "speaches": ("4g", "4g"), "docling": ("8g", "8g")}
    suggested = {"kokoro": 2, "speaches": 3, "docling": 6}
    assert all(int(caps[k][0].rstrip("g")) >= g for k, g in suggested.items())


def test_oom_victim_order() -> None:
    """Measure 9: background work (atlas-celery-cpu, docling) dies before the orchestrator and the engines, whose loss
    stops the seat and the measured footprints; one comment line above each setting says why."""
    want = {"atlas-celery-cpu.service": "500", "atlas-orchestrator.service": "-500", "llama-server@.service": "-500"}
    for unit, adj in want.items():
        lines = _lines(DAY1 / "systemd" / unit)
        idx = [i for i, ln in enumerate(lines) if ln.startswith("OOMScoreAdjust=")]
        assert [lines[i] for i in idx] == [f"OOMScoreAdjust={adj}"], unit
        assert lines[idx[0] - 1].startswith("# OOM victim order (answers.md 1.5 measure 9): "), unit
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load((DAY1 / "docker" / "core" / "compose.voice.yml").read_text(encoding="utf-8"))["services"]
    assert {k: v.get("oom_score_adj") for k, v in services.items()} == {"kokoro": None, "speaches": None,
                                                                         "docling": 500}
    # Measure 10 keeps the restart policy: a resident companion that crashes after loading is restarted; the Arbiter
    # stops a unit whose start failed (atlas/arbiter.py _execute_load), which cancels the pending restart.
    llama = _lines(DAY1 / "systemd" / "llama-server@.service")
    assert "Restart=on-failure" in llama
    assert any("is stopped by the Arbiter at once (atlas/arbiter.py _execute_load)" in ln for ln in llama)
