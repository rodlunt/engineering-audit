#!/usr/bin/env python3
"""Check every version pin in the repository against pyproject.toml.

Issue #101: the Gemini extension manifest's `version` field and its uvx
`args` git ref both stayed at v0.5.1 through two subsequent releases, so a
tester who installed with the release tag current at the time silently got
a two-release-old build running the taster rules pack instead of the full
one. The release checklist already named this file and it was still missed
twice, because a checklist a human has to honour on every release is not a
control. This script is the control: it runs in CI on every push and pull
request, so a forgotten pin fails the build instead of shipping.

The sentence above deliberately names no second version: "the release tag
current at the time" records what happened (a tester's install tag was
newer than the manifest's stuck v0.5.1) without literally typing that
tag. Issue #108's follow-up found that check-version-pins.py used to name
that tag outright, using the same install-flag-plus-tag shape
PROSE_PIN_RE watches for, so bump-version.py dutifully rewrote it on
every release: a factual incident report quietly became a false one,
since no tester ever typed the rewritten tag, and the incident happened
with the one specific tag a later bump has no business changing. See
tests/test_bump_version.py::test_bump_does_not_rewrite_this_historical_line
for the regression test.

Issue #108: detecting drift after the fact still left a human making the
edits by hand. scripts/bump-version.py now makes them instead. Both scripts
import their pin discovery from scripts/version_pins.py rather than each
defining their own notion of "where the pins are": see that module's
docstring for why a second, independently maintained discovery would be a
subtler version of the exact bug issue #101 found.

Three kinds of pin are discovered and checked against `pyproject.toml`'s
`version`; see version_pins.py for the exact patterns:

1. Any `@vX.Y.Z` install reference committed anywhere in the tracked tree.
2. The top-level `"version"` field of any tracked JSON manifest under
   `integrations/`.
3. A fixed list of known prose shapes that name the version without an `@`
   prefix (`--ref vX.Y.Z`, `currently vX.Y.Z`, `placeholder: vX.Y.Z`).

Fails loudly, not just on mismatch: this script exits non-zero if any pin
disagrees with `pyproject.toml`, and it also exits non-zero if any of the
three categories above discovers zero pins. Zero pins does not mean the
repository is clean, it means the patterns in version_pins.py have gone
stale after a rename or restructure, and a check that can silently pass by
finding nothing is worse than no check at all.

Every file and pin examined is printed, so the output shows coverage
(what was checked) rather than only a pass or fail verdict.

Run via:

    uv run python scripts/check-version-pins.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# So `import version_pins` resolves to the sibling module in this same
# directory regardless of how this script is loaded: run directly (`uv run
# python scripts/check-version-pins.py`, where sys.path[0] is already this
# directory) or loaded via importlib.util.spec_from_file_location in the
# test suite (where it is not).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from version_pins import REPO_ROOT, discover, read_pyproject_version  # noqa: E402


def main(repo_root: Path = REPO_ROOT, pyproject: Path | None = None) -> int:
    pyproject = pyproject if pyproject is not None else repo_root / "pyproject.toml"
    expected = read_pyproject_version(pyproject)
    print(f"pyproject.toml version: {expected}")

    found = discover(repo_root)

    mismatches: list[str] = []

    print(f"\n@vX.Y.Z install pins found: {len(found.at_pins)}")
    for pin in found.at_pins:
        rel = pin.path.relative_to(repo_root).as_posix()
        status = "ok" if pin.version == expected else "MISMATCH"
        print(f"  {rel}:{pin.line_no}: @v{pin.version} [{status}]")
        if pin.version != expected:
            mismatches.append(
                f"{rel}:{pin.line_no} pins @v{pin.version}, pyproject.toml says {expected}"
            )

    print(f"\nprose version mentions found: {len(found.prose_pins)}")
    for pin in found.prose_pins:
        rel = pin.path.relative_to(repo_root).as_posix()
        status = "ok" if pin.version == expected else "MISMATCH"
        print(f"  {rel}:{pin.line_no}: v{pin.version} [{status}]")
        if pin.version != expected:
            mismatches.append(
                f"{rel}:{pin.line_no} names v{pin.version}, pyproject.toml says {expected}"
            )

    print(f'\nmanifest "version" fields found: {len(found.manifest_pins)}')
    for manifest_pin in found.manifest_pins:
        rel = manifest_pin.path.relative_to(repo_root).as_posix()
        status = "ok" if manifest_pin.version == expected else "MISMATCH"
        print(f'  {rel}: "version": "{manifest_pin.version}" [{status}]')
        if manifest_pin.version != expected:
            mismatches.append(
                f'{rel} "version" is {manifest_pin.version}, pyproject.toml says {expected}'
            )

    # A skipped check must never be representable as a pass: if any pattern
    # found nothing, the pattern itself is broken (moved, renamed, or the
    # repository restructured), not proof the repository is clean.
    problems: list[str] = []
    if not found.at_pins:
        problems.append(
            "found zero @vX.Y.Z install pins anywhere in the tracked tree; "
            "the AT_PIN_RE pattern in scripts/version_pins.py has gone "
            "stale, or every pin was removed, either way this is broken, "
            "not clean"
        )
    if not found.prose_pins:
        problems.append(
            "found zero known prose version mentions anywhere in the "
            "tracked tree; the PROSE_LEAD_INS patterns in "
            "scripts/version_pins.py have gone stale, or every mention "
            "was removed, either way this is broken, not clean"
        )
    if not found.manifest_pins:
        problems.append(
            'found zero manifest "version" fields in the repository root '
            "or under integrations/; the manifest scan in "
            "scripts/version_pins.py has gone stale, or the Gemini "
            "extension manifest was removed, either way this is broken, "
            "not clean"
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
        f"\nOK: {len(found.at_pins)} install pin(s), {len(found.prose_pins)} "
        f"prose mention(s) and {len(found.manifest_pins)} manifest version "
        f"field(s) all match pyproject.toml version {expected}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
