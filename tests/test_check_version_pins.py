"""Tests for scripts/check-version-pins.py (issue #101).

Builds a small throwaway git repository per test with its own
pyproject.toml, an @vX.Y.Z install pin and an integrations/*.json manifest
version field, then points the loaded script module at it. This exercises
the same code paths the real CI step runs, without touching the real
repository's own pins.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-version-pins.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_version_pins", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_git_repo(path: Path) -> None:
    """Initialise path as a git repo with one commit covering whatever is
    already on disk, using -c flags for user.email/user.name so this works
    on a bare CI runner with no global git identity configured. Mirrors the
    helper of the same name in tests/test_server.py."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _build_repo(
    tmp_path: Path,
    *,
    pyproject_version: str = "1.2.3",
    at_pin_version: str | None = "1.2.3",
    manifest_version: str | None = "1.2.3",
) -> Path:
    """Build a minimal tracked tree: pyproject.toml, a README with an
    @vX.Y.Z pin (omitted if at_pin_version is None), and an
    integrations/example/manifest.json with a "version" field (omitted if
    manifest_version is None)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{pyproject_version}"\n',
        encoding="utf-8",
    )
    if at_pin_version is not None:
        (repo / "README.md").write_text(
            f"Install with `--from git+https://example.invalid/x@v{at_pin_version}`.\n",
            encoding="utf-8",
        )
    else:
        (repo / "README.md").write_text("No pins here.\n", encoding="utf-8")
    if manifest_version is not None:
        manifest_dir = repo / "integrations" / "example"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"name": "example", "version": manifest_version}),
            encoding="utf-8",
        )
    _init_git_repo(repo)
    return repo


def _run_main(module: ModuleType, repo: Path) -> int:
    module.REPO_ROOT = repo
    module.PYPROJECT = repo / "pyproject.toml"
    return module.main()


def test_matching_pins_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()
    repo = _build_repo(tmp_path)

    exit_code = _run_main(module, repo)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "OK:" in out


def test_mismatched_at_pin_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    repo = _build_repo(tmp_path, pyproject_version="1.2.3", at_pin_version="1.0.0")

    exit_code = _run_main(module, repo)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "README.md" in captured.out
    assert "MISMATCH" in captured.out
    assert "README.md" in captured.err
    assert "1.0.0" in captured.err and "1.2.3" in captured.err


def test_mismatched_manifest_version_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    repo = _build_repo(tmp_path, pyproject_version="1.2.3", manifest_version="0.9.0")

    exit_code = _run_main(module, repo)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "integrations/example/manifest.json" in captured.out
    assert "MISMATCH" in captured.out
    assert "0.9.0" in captured.err and "1.2.3" in captured.err


def test_zero_at_pins_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    repo = _build_repo(tmp_path, at_pin_version=None)

    exit_code = _run_main(module, repo)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "zero @vX.Y.Z install pins" in captured.err


def test_zero_manifest_pins_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    repo = _build_repo(tmp_path, manifest_version=None)

    exit_code = _run_main(module, repo)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert 'zero manifest "version" fields' in captured.err


def test_real_repository_passes_its_own_check() -> None:
    # Not a fabricated tmp repo: runs the check against this repository as
    # it actually stands, so a real regression (a pin that drifts again)
    # fails the suite directly rather than only the CI step.
    module = _load_module()
    exit_code = module.main()
    assert exit_code == 0
