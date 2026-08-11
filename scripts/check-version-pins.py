#!/usr/bin/env python3
"""Check every version pin in the repository against pyproject.toml.

Issue #101: the Gemini extension manifest's `version` field and its uvx
`args` git ref both stayed at v0.5.1 through two subsequent releases, so a
tester who installed with `--ref v0.7.0` silently got a two-release-old
build running the taster rules pack instead of the full one. The release
checklist already named this file and it was still missed twice, because a
checklist a human has to honour on every release is not a control. This
script is the control: it runs in CI on every push and pull request, so a
forgotten pin fails the build instead of shipping.

Two kinds of pin are discovered and checked against `pyproject.toml`'s
`version`:

1. Any `@vX.Y.Z` install reference committed anywhere in the tracked tree
   (README install commands, integration docs, the Gemini extension
   manifest's uvx `args`, and anywhere else the same pattern shows up).
2. The top-level `"version"` field of any tracked JSON manifest under
   `integrations/` (currently just the Gemini extension manifest, but a
   future extension's manifest is covered for free rather than needing its
   own hardcoded path here).

Fails loudly, not just on mismatch: this script exits non-zero if any pin
disagrees with `pyproject.toml`, and it also exits non-zero if either
category above discovers zero pins. Zero pins does not mean the repository
is clean, it means the patterns above have gone stale after a rename or
restructure, and a check that can silently pass by finding nothing is worse
than no check at all.

Every file and pin examined is printed, so the output shows coverage
(what was checked) rather than only a pass or fail verdict.

Run via:

    uv run python scripts/check-version-pins.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Matches an `@v0.7.0`-style git ref pin, e.g. the uvx --from argument
# `git+https://github.com/rodlunt/engineering-audit@v0.7.0`. Deliberately
# not matching GitHub Actions pins like `@c771a70e... # v9.0.0`: those pin a
# third-party action's commit SHA, with the human-readable version only in a
# trailing comment, so the `@v` characters never end up adjacent there.
AT_PIN_RE = re.compile(r"@v(\d+\.\d+\.\d+)")

# Machine-generated, not an install pin a human maintains by hand: uv
# regenerates this from pyproject.toml itself, so it is not the kind of
# forgotten-pin bug this script exists to catch.
EXCLUDED_FILES = {"uv.lock"}


def read_pyproject_version() -> str:
    """Read the `[project] version` field from pyproject.toml.

    Uses a regex rather than a TOML parser: the project's floor is Python
    3.10 (see requires-python in pyproject.toml) and tomllib is 3.11+, so a
    full parse would need a dependency this one-line extraction does not.
    Mirrors the same extraction tag-version-guard.yml already does in
    shell with sed, for the same reason.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SystemExit(
            f"could not find a `version = \"...\"` line in {PYPROJECT}; "
            "check extraction is broken, not that the project has no version"
        )
    return match.group(1)


def list_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def find_at_pins(tracked_files: list[Path]) -> list[tuple[Path, int, str]]:
    """Return (file, line number, pinned version) for every `@vX.Y.Z` found
    in a tracked text file."""
    found: list[tuple[Path, int, str]] = []
    for path in tracked_files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable file (images under docs/images, etc.):
            # not a place an install pin could live.
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in AT_PIN_RE.finditer(line):
                found.append((path, line_no, m.group(1)))
    return found


def find_manifest_version_pins(
    tracked_files: list[Path],
) -> list[tuple[Path, str]]:
    """Return (file, version) for every tracked JSON manifest under
    integrations/ that declares a top-level "version" field."""
    found: list[tuple[Path, str]] = []
    for path in tracked_files:
        rel = path.relative_to(REPO_ROOT)
        if rel.suffix != ".json":
            continue
        if "integrations" not in rel.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        version = data.get("version")
        if isinstance(version, str):
            found.append((path, version))
    return found


def main() -> int:
    expected = read_pyproject_version()
    print(f"pyproject.toml version: {expected}")

    tracked_files = list_tracked_files()
    at_pins = find_at_pins(tracked_files)
    manifest_pins = find_manifest_version_pins(tracked_files)

    mismatches: list[str] = []

    print(f"\n@vX.Y.Z install pins found: {len(at_pins)}")
    for path, line_no, version in at_pins:
        rel = path.relative_to(REPO_ROOT).as_posix()
        status = "ok" if version == expected else "MISMATCH"
        print(f"  {rel}:{line_no}: @v{version} [{status}]")
        if version != expected:
            mismatches.append(
                f"{rel}:{line_no} pins @v{version}, pyproject.toml says {expected}"
            )

    print(f"\nmanifest \"version\" fields found: {len(manifest_pins)}")
    for path, version in manifest_pins:
        rel = path.relative_to(REPO_ROOT).as_posix()
        status = "ok" if version == expected else "MISMATCH"
        print(f'  {rel}: "version": "{version}" [{status}]')
        if version != expected:
            mismatches.append(
                f'{rel} "version" is {version}, pyproject.toml says {expected}'
            )

    # A skipped check must never be representable as a pass: if either
    # pattern found nothing, the pattern itself is broken (moved, renamed,
    # or the repository restructured), not proof the repository is clean.
    problems: list[str] = []
    if not at_pins:
        problems.append(
            "found zero @vX.Y.Z install pins anywhere in the tracked tree; "
            "the AT_PIN_RE pattern in this script has gone stale, or every "
            "pin was removed, either way this is broken, not clean"
        )
    if not manifest_pins:
        problems.append(
            "found zero manifest \"version\" fields under integrations/; "
            "the manifest scan in this script has gone stale, or the "
            "Gemini extension manifest was removed, either way this is "
            "broken, not clean"
        )

    if problems:
        print("\nFAILED (check is broken, not the repository is clean):")
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    if mismatches:
        print("\nFAILED (version pin mismatches):")
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return 1

    print(
        f"\nOK: {len(at_pins)} install pin(s) and {len(manifest_pins)} "
        f"manifest version field(s) all match pyproject.toml version {expected}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
