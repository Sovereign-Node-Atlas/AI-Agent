"""Unit tests for phase4/engines/p4common.py that need no GPU, no network and no container (CONVENTIONS §7.8: tests
run with pytest and no live services). They prove the offline-cache contract the fix round introduced:
huggingface_hub (2.0.0 VERIFIED) writes no refs/main for a commit-hash download, every GPU test resolves "main" with
HF_HUB_OFFLINE=1, so the pull must write refs/main itself and prove_offline must catch a cache that does not resolve."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ENGINES = HERE.parent / "engines"


def _load_p4common() -> object:
    spec = importlib.util.spec_from_file_location("p4common", ENGINES / "p4common.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p4common"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    """A fake hub cache holding one snapshot pinned to a sha and NO refs/ directory (what snapshot_download leaves
    behind for revision=<sha>). Returns (HF_HOME, snapshot dir, sha)."""
    pytest.importorskip("huggingface_hub")
    home = tmp_path / "hf"
    sha = "a" * 40
    snap = home / "hub" / "models--org--repo" / "snapshots" / sha
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (snap / "model.safetensors").write_bytes(b"\0" * 16)
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    # huggingface_hub reads its constants at import: make sure this process imports them with the env above.
    for name in [m for m in sys.modules if m == "huggingface_hub" or m.startswith("huggingface_hub.")]:
        del sys.modules[name]
    return home, snap, sha


def test_offline_default_revision_needs_refs_main(hf_cache: tuple[Path, Path, str]) -> None:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    _home, snap, sha = hf_cache
    # Without refs/main the offline lookup of the default revision fails: the blocker the pull must prevent.
    with pytest.raises(LocalEntryNotFoundError):
        snapshot_download("org/repo")
    p4 = _load_p4common()
    ref = p4.write_main_ref(snap, sha)
    assert ref == snap.parents[1] / "refs" / "main"
    assert ref.read_text(encoding="utf-8") == sha
    assert Path(snapshot_download("org/repo")).resolve() == snap.resolve()


def test_prove_offline_reports_unresolvable_cache(hf_cache: tuple[Path, Path, str]) -> None:
    p4 = _load_p4common()
    _home, snap, sha = hf_cache
    files = [{"name": "config.json", "bytes": 2, "sha256": None}]
    why = p4.prove_offline("org/repo", None, files, snap)
    assert why is not None and "snapshot_download" in why
    p4.write_main_ref(snap, sha)
    assert p4.prove_offline("org/repo", None, files, snap) is None
    assert p4.prove_offline("org/repo", ["*.json"], files, snap) is None


def test_write_main_ref_is_atomic_and_idempotent(hf_cache: tuple[Path, Path, str]) -> None:
    p4 = _load_p4common()
    _home, snap, sha = hf_cache
    p4.write_main_ref(snap, sha)
    p4.write_main_ref(snap, sha)
    refs = snap.parents[1] / "refs"
    assert sorted(p.name for p in refs.iterdir()) == ["main"]      # no leftover .tmp


def test_hf_token_distinguishes_absent_from_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p4 = _load_p4common()
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN_FILE", str(tmp_path / "absent.env"))
    assert p4.hf_token() is None
    tok = tmp_path / "hf-token.env"
    tok.write_text("# comment\nHF_TOKEN='hf_abc'\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN_FILE", str(tok))
    assert p4.hf_token() == "hf_abc"
    if os.geteuid() == 0:
        pytest.skip("root reads a 000 file; the PermissionError branch needs an unprivileged runner")
    tok.chmod(0)
    with pytest.raises(p4.TokenUnreadable):
        p4.hf_token()


def test_hf_base_binds_the_token_to_hugging_face_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    p4 = _load_p4common()
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    assert p4.hf_base() == ("https://huggingface.co", True)
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.hf.co/")
    assert p4.hf_base() == ("https://mirror.hf.co", True)
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example.net")
    base, ok = p4.hf_base()
    assert base == "https://mirror.example.net" and ok is False      # pull honoured, token withheld


def test_load_kwargs_by_library_version(monkeypatch: pytest.MonkeyPatch) -> None:
    p4 = _load_p4common()

    class Fake:
        pass

    Fake.__module__ = "diffusers.pipelines.x"
    monkeypatch.setattr(p4, "_lib_version", lambda name: (0, 36, 0))
    assert p4.load_kwargs(Fake, "bf16") == {"torch_dtype": "bf16"}
    monkeypatch.setattr(p4, "_lib_version", lambda name: (0, 37, 1))
    assert p4.load_kwargs(Fake, "bf16") == {"torch_dtype": "bf16", "disable_mmap": True}
    monkeypatch.setattr(p4, "_lib_version", lambda name: (0, 40, 0))
    assert p4.load_kwargs(Fake, "bf16") == {"dtype": "bf16", "disable_mmap": True}
    assert p4.load_kwargs(Fake, "bf16", disable_mmap=False) == {"dtype": "bf16"}
    Fake.__module__ = "transformers.models.y"
    monkeypatch.setattr(p4, "_lib_version", lambda name: (4, 55, 0))
    assert p4.load_kwargs(Fake, "bf16") == {"torch_dtype": "bf16"}
    monkeypatch.setattr(p4, "_lib_version", lambda name: (5, 18, 0))
    assert p4.load_kwargs(Fake, "bf16") == {"dtype": "bf16"}
    Fake.__module__ = "chronos.z"
    assert p4.load_kwargs(Fake, "bf16") == {"torch_dtype": "bf16"}
