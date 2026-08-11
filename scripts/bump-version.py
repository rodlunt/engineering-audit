#!/usr/bin/env python3
"""Bump every version pin in the repository to match a target version.

Issue #108: PR #103 (issue #101) added check-version-pins.py, which detects
drift between pyproject.toml's version and roughly ten places that copy it,
but only after the fact. A human still made every edit by hand on each
release and the Gemini extension manifest was missed at two consecutive
releases despite being named in the release checklist both times. This
script is the release checklist entry it replaces: it writes
pyproject.toml's version, then rewrites every other discovered pin to
match, so a release becomes one command instead of a list a human has to
honour.

The central constraint this script exists to satisfy: it shares its pin
discovery with check-version-pins.py by importing scripts/version_pins.py
rather than reimplementing the patterns. A second, independently maintained
notion of "where the pins are" would let the writer and the checker
disagree about coverage, which is a subtler version of the exact bug issue
#101 found. Whatever version_pins.py's discovery functions find is what
gets rewritten here and what check-version-pins.py verifies afterwards; see
that module's docstring for the three pin categories and why prose
detection is a fixed list of known shapes rather than a bare `vX.Y.Z` scan.

After rewriting, this script loads and runs check-version-pins.py's own
main() against the now-bumped tree as a self-check, and exits non-zero if
that check does not pass. A bump that leaves the check failing is not a
successful bump: better to fail loudly here than to let CI catch it on the
next push, and cheap to prove given the check is one import away.

Run via:

    uv run python scripts/bump-version.py X.Y.Z
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

# So `import version_pins` resolves to the sibling module in this same
# directory regardless of how this script is invoked. Matches
# check-version-pins.py's own sys.path handling for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from version_pins import (  # noqa: E402
    AT_PIN_RE,
    PROSE_PIN_RE,
    REPO_ROOT,
    ManifestPin,
    TextPin,
    discover,
    is_valid_version,
    read_pyproject_version,
    rewrite_manifest_version,
    rewrite_text_pin_file,
    write_pyproject_version,
)

CHECK_SCRIPT = Path(__file__).resolve().parent / "check-version-pins.py"


def _load_check_module() -> ModuleType:
    """Load check-version-pins.py as a module so this script can call its
    main() as a self-check after bumping, without reimplementing the
    comparison logic that already lives there. A plain `import` cannot
    reach it: the filename has a hyphen, so this uses the same
    spec_from_file_location technique tests/test_check_version_pins.py
    already uses to load it."""
    spec = importlib.util.spec_from_file_location("check_version_pins", CHECK_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rewrite_text_pins(
    pins: list[TextPin], target: str, pattern: re.Pattern[str]
) -> list[tuple[Path, int, str, str]]:
    """Rewrite every file that appears in pins so pattern's matches become
    target, and return (path, line_no, old_version, target) for each pin
    actually rewritten. Groups pins by file first so a file with more than
    one match on the same pattern is read and written exactly once."""
    changes: list[tuple[Path, int, str, str]] = []
    by_path: dict[Path, list[TextPin]] = {}
    for pin in pins:
        by_path.setdefault(pin.path, []).append(pin)

    for path, path_pins in by_path.items():
        text = path.read_text(encoding="utf-8")
        new_text, count = rewrite_text_pin_file(text, pattern, target)
        if count == 0:
            continue
        path.write_text(new_text, encoding="utf-8")
        for pin in path_pins:
            changes.append((path, pin.line_no, pin.version, target))
    return changes


def rewrite_manifest_pins(
    pins: list[ManifestPin], target: str, repo_root: Path
) -> list[tuple[Path, str, str]]:
    """Rewrite every manifest's top-level "version" field to target, and
    return (path, old_version, target) for each one actually rewritten."""
    changes: list[tuple[Path, str, str]] = []
    for pin in pins:
        text = pin.path.read_text(encoding="utf-8")
        new_text, count = rewrite_manifest_version(text, pin.version, target)
        if count == 0:
            # find_manifest_version_pins() parsed this version out of the
            # file a moment ago, so zero here means the targeted rewrite
            # pattern in version_pins.py does not recognise this file's
            # formatting, not that there is nothing to do. Surface it
            # rather than silently leaving the manifest unbumped.
            raise SystemExit(
                f"could not rewrite the \"version\" field in "
                f"{pin.path.relative_to(repo_root).as_posix()}: "
                f"rewrite_manifest_version() found the field via json.loads "
                f"but its line-anchored rewrite pattern did not match; the "
                f"file's formatting has likely changed"
            )
        pin.path.write_text(new_text, encoding="utf-8")
        changes.append((pin.path, pin.version, target))
    return changes


def main(argv: list[str] | None = None, repo_root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(
        description="Bump every version pin in the repository from "
        "pyproject.toml's current version to a target version."
    )
    parser.add_argument(
        "version",
        help="target version, three dot-separated non-negative integers, "
        "e.g. 0.8.0 (no leading v)",
    )
    args = parser.parse_args(argv)

    target = args.version
    if not is_valid_version(target):
        print(
            f"refusing to bump to {target!r}: not a valid version. "
            "Expected three dot-separated non-negative integers with no "
            "leading v and no pre-release or build suffix, e.g. 0.8.0.",
            file=sys.stderr,
        )
        return 2

    pyproject = repo_root / "pyproject.toml"
    current = read_pyproject_version(pyproject)
    print(f"current pyproject.toml version: {current}")
    print(f"target version: {target}")

    write_pyproject_version(target, pyproject)
    print(f"\npyproject.toml: version = \"{current}\" -> \"{target}\"")

    found = discover(repo_root)

    at_changes = rewrite_text_pins(found.at_pins, target, AT_PIN_RE)
    print(f"\n@vX.Y.Z install pins rewritten: {len(at_changes)}")
    for path, line_no, old, new in at_changes:
        rel = path.relative_to(repo_root).as_posix()
        print(f"  {rel}:{line_no}: @v{old} -> @v{new}")

    prose_changes = rewrite_text_pins(found.prose_pins, target, PROSE_PIN_RE)
    print(f"\nprose version mentions rewritten: {len(prose_changes)}")
    for path, line_no, old, new in prose_changes:
        rel = path.relative_to(repo_root).as_posix()
        print(f"  {rel}:{line_no}: v{old} -> v{new}")

    manifest_changes = rewrite_manifest_pins(found.manifest_pins, target, repo_root)
    print(f'\nmanifest "version" fields rewritten: {len(manifest_changes)}')
    for path, old, new in manifest_changes:
        rel = path.relative_to(repo_root).as_posix()
        print(f'  {rel}: "version": "{old}" -> "{new}"')

    print("\nself-check: running check-version-pins.py against the bumped tree")
    check_module = _load_check_module()
    check_exit = check_module.main(repo_root=repo_root, pyproject=pyproject)
    if check_exit != 0:
        print(
            "\nFAILED: check-version-pins.py did not pass after the bump; "
            "the bump is incomplete or a pin's format is not one "
            "version_pins.py's discovery or rewrite logic recognises. "
            "See the check output above for which pin.",
            file=sys.stderr,
        )
        return check_exit

    print(f"\nOK: bumped {current} -> {target} and check-version-pins.py passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
