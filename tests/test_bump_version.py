"""Tests for scripts/bump-version.py (issue #108).

Exercises the same throwaway-git-repo pattern as
tests/test_check_version_pins.py, plus one test that runs the real bump
against a throwaway copy of this actual repository's tracked files: proof
that running the bump for real, then running the real check for real,
leaves the check passing on the repository's actual pin set, not just on a
minimal synthetic fixture. The real repository itself is never modified;
everything happens inside tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check-version-pins.py"
BUMP_SCRIPT = REPO_ROOT / "scripts" / "bump-version.py"

_module_counter = 0


def _load_module(base_name: str, path: Path) -> ModuleType:
    # Each call gets a unique module name: bump-version.py's own main()
    # also loads check-version-pins.py via importlib, and re-using the same
    # module name across a test run risks pytest or importlib reusing a
    # stale cached module instead of a fresh exec.
    global _module_counter
    _module_counter += 1
    spec = importlib.util.spec_from_file_location(f"{base_name}_{_module_counter}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_git_repo(path: Path) -> None:
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


def _build_repo(tmp_path: Path, version: str = "1.2.3") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8"
    )
    (repo / "README.md").write_text(
        f"Install `--from git+https://example.invalid/x@v{version}`.\n"
        f"gemini extensions install x --ref v{version}\n",
        encoding="utf-8",
    )
    manifest_dir = repo / "integrations" / "example"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"name": "example", "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    _init_git_repo(repo)
    return repo


def test_bump_rewrites_every_pin_and_passes_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # old_version/target are interpolated into the assertions below rather
    # than written as literal @vX.Y.Z / --ref vX.Y.Z strings in this file's
    # own source: this test file is itself tracked, and a literal pin here
    # would be discovered by check-version-pins.py and reported as a
    # mismatch against pyproject.toml's real version.
    old_version, target = "1.2.3", "1.3.0"
    repo = _build_repo(tmp_path, version=old_version)
    bump_module = _load_module("bump_version", BUMP_SCRIPT)

    exit_code = bump_module.main([target], repo_root=repo)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"OK: bumped {old_version} -> {target}" in out
    assert "check-version-pins.py passes" in out

    assert f'version = "{target}"' in (repo / "pyproject.toml").read_text()
    readme = (repo / "README.md").read_text()
    assert f"@v{target}" in readme
    assert f"--ref v{target}" in readme
    assert old_version not in readme
    manifest = json.loads(
        (repo / "integrations" / "example" / "manifest.json").read_text()
    )
    assert manifest["version"] == target

    # Independently reload and run the check, rather than trusting bump's
    # own self-check report: proves the bumped tree passes the check as a
    # fresh, separate invocation would see it, e.g. from a later CI step.
    check_module = _load_module("check_version_pins", CHECK_SCRIPT)
    check_exit = check_module.main(repo_root=repo, pyproject=repo / "pyproject.toml")
    assert check_exit == 0


def test_bump_refuses_invalid_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="1.2.3")
    bump_module = _load_module("bump_version", BUMP_SCRIPT)

    exit_code = bump_module.main(["not-a-version"], repo_root=repo)

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "not a valid version" in captured.err
    # Refused before writing anything: pyproject.toml is untouched.
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text()


@pytest.mark.parametrize(
    "bad",
    ["1.2", "1.2.3.4", "v1.2.3", "1.2.3-rc1", "latest", ""],
)
def test_bump_refuses_various_invalid_shapes(tmp_path: Path, bad: str) -> None:
    repo = _build_repo(tmp_path, version="1.2.3")
    bump_module = _load_module("bump_version", BUMP_SCRIPT)

    exit_code = bump_module.main([bad], repo_root=repo)

    assert exit_code != 0
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text()


def test_bump_against_a_real_repository_copy_then_check_passes(tmp_path: Path) -> None:
    """Copies this actual repository's tracked files into a scratch git
    repo, bumps it for real to the next minor version, and runs the real
    check against the result. Proves the bump covers everything the check
    covers on the repository's actual, full-sized pin set, not just a
    minimal synthetic fixture. The real repository is only ever read from;
    nothing under REPO_ROOT is written."""
    copy = tmp_path / "repo-copy"
    copy.mkdir()

    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in tracked:
        src = REPO_ROOT / rel
        if not src.is_file():
            continue
        dst = copy / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    _init_git_repo(copy)

    current_text = (copy / "pyproject.toml").read_text()
    match = re.search(r'version = "(\d+)\.(\d+)\.(\d+)"', current_text)
    assert match is not None
    major, minor, _patch = match.groups()
    old_version = f"{major}.{minor}.{_patch}"
    target = f"{major}.{int(minor) + 1}.0"

    bump_module = _load_module("bump_version", BUMP_SCRIPT)
    exit_code = bump_module.main([target], repo_root=copy)
    assert exit_code == 0

    check_module = _load_module("check_version_pins", CHECK_SCRIPT)
    check_exit = check_module.main(repo_root=copy, pyproject=copy / "pyproject.toml")
    assert check_exit == 0

    # The real repository's own pyproject.toml is untouched by any of this.
    assert f'version = "{old_version}"' in (REPO_ROOT / "pyproject.toml").read_text()
