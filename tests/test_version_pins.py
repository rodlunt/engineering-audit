"""Tests for scripts/version_pins.py, the shared pin discovery that
check-version-pins.py and bump-version.py both import (issue #108).

test_check_and_bump_import_the_same_discovery_functions is the test that
actually protects the design: issue #108 exists specifically because a
bump script with its own, separately maintained notion of "where the pins
are" could quietly drift from what check-version-pins.py verifies. This
test does not check behaviour, it checks the wiring: that both scripts
resolve `discover` to the exact same function object rather than two
independent copies of similar logic.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
import version_pins  # noqa: E402


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
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
    """A tiny tracked tree exercising all three pin categories at once."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8"
    )
    (repo / "README.md").write_text(
        f"Install `--from git+https://example.invalid/x@v{version}`.\n"
        f"gemini extensions install x --ref v{version}\n"
        f"the support table says (currently v{version})\n",
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


# --- the design-protecting test -------------------------------------------


def test_check_and_bump_import_the_same_discovery_functions() -> None:
    check_module = _load_module("check_version_pins", "check-version-pins.py")
    bump_module = _load_module("bump_version", "bump-version.py")

    # Both scripts do `from version_pins import discover`, and Python
    # caches "version_pins" in sys.modules the first time either of them
    # (or this test) imports it, so both names resolve to the identical
    # function object this test also imported directly above. If either
    # script instead defined its own discover()/find_at_pins()/etc, these
    # identity checks would fail even though the reimplementation might
    # look correct in isolation, which is exactly the drift issue #108 is
    # about.
    assert check_module.discover is version_pins.discover
    assert bump_module.discover is version_pins.discover
    assert bump_module.rewrite_text_pin_file is version_pins.rewrite_text_pin_file
    assert bump_module.rewrite_manifest_version is version_pins.rewrite_manifest_version


def test_discovery_returns_the_same_set_regardless_of_caller(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)

    direct = version_pins.discover(repo)

    check_module = _load_module("check_version_pins", "check-version-pins.py")
    bump_module = _load_module("bump_version", "bump-version.py")

    via_check = check_module.discover(repo)
    via_bump = bump_module.discover(repo)

    assert direct == via_check == via_bump


# --- discovery behaviour ----------------------------------------------------


def test_find_at_pins(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, version="9.9.9")
    tracked = version_pins.list_tracked_files(repo)

    pins = version_pins.find_at_pins(tracked, repo)

    assert len(pins) == 1
    assert pins[0].version == "9.9.9"
    assert pins[0].path.name == "README.md"


def test_find_prose_pins_matches_all_known_lead_ins(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, version="9.9.9")
    tracked = version_pins.list_tracked_files(repo)

    pins = version_pins.find_prose_pins(tracked, repo)

    # README.md carries both the "--ref v" and "currently v" shapes.
    assert len(pins) == 2
    assert {pin.version for pin in pins} == {"9.9.9"}


def test_find_manifest_version_pins(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, version="9.9.9")
    tracked = version_pins.list_tracked_files(repo)

    pins = version_pins.find_manifest_version_pins(tracked, repo)

    assert len(pins) == 1
    assert pins[0].version == "9.9.9"


def test_bare_version_without_a_known_lead_in_is_not_a_prose_pin(tmp_path: Path) -> None:
    # Guards the deliberate choice documented in version_pins.py: a bare
    # vX.Y.Z is not enough, it must follow one of PROSE_LEAD_INS. Otherwise
    # unrelated version-shaped text (a citation, a third-party tool's
    # pinned release) would be discovered, and bump-version.py would
    # rewrite text that has nothing to do with this project's version.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (repo / "NOTES.md").write_text(
        "See the ISTQB syllabus v4.0.1 for background.\n", encoding="utf-8"
    )
    _init_git_repo(repo)
    tracked = version_pins.list_tracked_files(repo)

    assert version_pins.find_prose_pins(tracked, repo) == []
    assert version_pins.find_at_pins(tracked, repo) == []


# --- version validation ------------------------------------------------------


@pytest.mark.parametrize("raw", ["0.8.0", "1.2.3", "10.20.30", "0.0.1"])
def test_is_valid_version_accepts_semver_shape(raw: str) -> None:
    assert version_pins.is_valid_version(raw)


@pytest.mark.parametrize(
    "raw", ["v1.2.3", "1.2", "1.2.3.4", "1.2.3-rc1", "1.2.x", "", "latest"]
)
def test_is_valid_version_rejects_anything_else(raw: str) -> None:
    assert not version_pins.is_valid_version(raw)


# --- rewrite helpers ----------------------------------------------------------


def test_rewrite_text_pin_file_at_pin_preserves_surrounding_text() -> None:
    # Built through interpolation rather than a literal @vX.Y.Z in this
    # file's own source: this file is itself tracked, and a literal pin
    # here would be discovered by check-version-pins.py the same as any
    # other tracked text, then reported as a mismatch against
    # pyproject.toml's real version. See scripts/version_pins.py's
    # docstring for the same reasoning applied to its own comments.
    old_version, new_version = "1.2.3", "1.3.0"
    text = f"Install `--from git+https://example.invalid/x@v{old_version}` please.\n"

    new_text, count = version_pins.rewrite_text_pin_file(
        text, version_pins.AT_PIN_RE, new_version
    )

    assert count == 1
    assert new_text == f"Install `--from git+https://example.invalid/x@v{new_version}` please.\n"


@pytest.mark.parametrize("lead_in", version_pins.PROSE_LEAD_INS)
def test_rewrite_text_pin_file_prose_pin_preserves_lead_in(lead_in: str) -> None:
    text = f"before {lead_in}1.2.3 after\n"

    new_text, count = version_pins.rewrite_text_pin_file(
        text, version_pins.PROSE_PIN_RE, "1.3.0"
    )

    assert count == 1
    assert new_text == f"before {lead_in}1.3.0 after\n"


def test_rewrite_manifest_version_preserves_formatting() -> None:
    text = json.dumps({"name": "example", "version": "1.2.3", "other": 1}, indent=2) + "\n"

    new_text, count = version_pins.rewrite_manifest_version(text, "1.2.3", "1.3.0")

    assert count == 1
    assert json.loads(new_text)["version"] == "1.3.0"
    assert json.loads(new_text)["other"] == 1
    # Same indentation style survives: this is a text substitution, not a
    # json.load/json.dump round trip that would risk reformatting the file.
    assert new_text.count("\n  ") == text.count("\n  ")


def test_rewrite_manifest_version_no_match_returns_zero_count() -> None:
    text = json.dumps({"name": "example", "version": "1.2.3"}, indent=2) + "\n"

    new_text, count = version_pins.rewrite_manifest_version(text, "9.9.9", "1.3.0")

    assert count == 0
    assert new_text == text
