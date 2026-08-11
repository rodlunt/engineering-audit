"""Shared version-pin discovery for check-version-pins.py and bump-version.py.

Issue #108: PR #103 (issue #101) added check-version-pins.py to detect drift
between pyproject.toml's version and every place that version gets copied.
That closes the "ship it wrong" hole but not the "type it wrong" one: a
human still made about ten hand edits per release, and the Gemini extension
manifest was missed at two consecutive releases despite being named in the
release checklist every time. bump-version.py exists to make those edits
instead of a human, and this module is the one definition of "where the
pins are" that both scripts import. A second, slightly different notion of
pin discovery living inside bump-version.py would let the writer and the
checker quietly disagree about coverage, which is a subtler version of the
exact bug this whole effort exists to prevent. One discovery, two callers:
check-version-pins.py compares every discovered pin against pyproject.toml
and reports mismatches; bump-version.py overwrites pyproject.toml's version
and then rewrites every discovered pin to match, in place.

Three kinds of pin are discovered:

1. `@vX.Y.Z` install references: README install commands, integration
   docs, and the Gemini extension manifest's uvx `args`.
2. The top-level `"version"` field of any tracked JSON manifest under
   `integrations/`.
3. Prose that names the version without an `@` prefix, matched by a fixed
   list of known lead-in phrases (PROSE_LEAD_INS below), not by a bare
   `vX.Y.Z` pattern. A context-free scan for `vX.Y.Z` anywhere in the tree
   also matches things that are not this project's version at all: the
   ISTQB syllabus citation `v4.0.1` repeated through
   examples/taster-rules, this project's own release-tag test fixtures in
   tests/test_update_check.py, and GitHub Actions pin comments like
   `# v9.0.0` in the workflow files. Rewriting any of those on a bump
   would corrupt them. When a release adds a new prose shape, extend
   PROSE_LEAD_INS rather than broadening the pattern to "any vX.Y.Z",
   which would reopen that hole.

   This category also catches check-version-pins.py's own module
   docstring, which narrates the issue #101 incident using the tag that
   was current when it was written (`--ref vX.Y.Z`, currently matching
   this repository's real version by coincidence of when it was last
   edited). Deliberately not excluded: an illustrative example that
   silently goes stale is exactly the failure this module exists to
   catch, so a bump rewrites that sentence's tag too.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Matches an `@vX.Y.Z`-style git ref pin, e.g. the uvx --from argument
# `git+https://github.com/rodlunt/engineering-audit@vX.Y.Z`. Deliberately
# not matching GitHub Actions pins like `@c771a70e... # vN.N.N`: those pin a
# third-party action's commit SHA, with the human-readable version only in a
# trailing comment, so the `@v` characters never end up adjacent there.
#
# Written with the placeholder `X.Y.Z` rather than this project's own
# current version on purpose: a concrete example version in this comment
# would itself be a pin AT_PIN_RE discovers (it already happened once, in
# the version of this file that lived at scripts/check-version-pins.py
# before issue #108), forcing every release to edit a comment for no
# functional reason.
AT_PIN_RE = re.compile(r"@v(\d+\.\d+\.\d+)")

# Fixed, known prose shapes that name this project's own version without an
# `@` prefix. Each is a literal lead-in immediately followed by the version
# digits, chosen because it is what this repository's own release history
# (see the diff that bumped from the release before this one) actually
# rewrites by hand today:
#
#   --ref vX.Y.Z          gemini extensions install ... --ref vX.Y.Z
#   currently vX.Y.Z      SECURITY.md's support table
#   placeholder: vX.Y.Z   the bug report issue template's example value
#
# The version is always the last captured group and sits at the very end of
# the full match, which rewrite_text_pin_file() below relies on to
# reconstruct the lead-in unchanged.
PROSE_LEAD_INS = ("--ref v", "currently v", "placeholder: v")
PROSE_PIN_RE = re.compile(
    "(" + "|".join(re.escape(lead_in) for lead_in in PROSE_LEAD_INS) + r")(\d+\.\d+\.\d+)"
)

# A valid `[project] version` value: three dot-separated non-negative
# integers. Deliberately stricter than PEP 440 (no pre-release or build
# metadata suffixes): every existing pin format in this repository
# (@vX.Y.Z refs, the manifest's "version" field, the prose shapes above)
# assumes exactly this shape, so a version this project would actually tag
# never needs more, and accepting more would let bump-version.py write a
# value some pin's own pattern then cannot recognise on the next run.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Machine-generated, not an install pin a human maintains by hand: uv
# regenerates this from pyproject.toml itself, so it is not the kind of
# forgotten-pin bug this module exists to catch.
EXCLUDED_FILES = {"uv.lock"}


@dataclass(frozen=True)
class TextPin:
    """A version mention found on one line of a tracked text file."""

    path: Path
    line_no: int
    version: str


@dataclass(frozen=True)
class ManifestPin:
    """A top-level "version" field found in a tracked JSON manifest."""

    path: Path
    version: str


def is_valid_version(raw: str) -> bool:
    """True if raw looks like a version this project's pins can represent:
    three dot-separated non-negative integers, no leading "v", no
    pre-release or build suffix. See VERSION_RE for why the shape is this
    strict."""
    return bool(VERSION_RE.match(raw))


def read_pyproject_version(pyproject: Path = PYPROJECT) -> str:
    """Read the `[project] version` field from pyproject.toml.

    Uses a regex rather than a TOML parser: the project's floor is Python
    3.10 (see requires-python in pyproject.toml) and tomllib is 3.11+, so a
    full parse would need a dependency this one-line extraction does not.
    Mirrors the same extraction tag-version-guard.yml already does in
    shell with sed, for the same reason.
    """
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SystemExit(
            f"could not find a `version = \"...\"` line in {pyproject}; "
            "check extraction is broken, not that the project has no version"
        )
    return match.group(1)


def write_pyproject_version(target: str, pyproject: Path = PYPROJECT) -> None:
    """Overwrite the `[project] version` field in pyproject.toml with
    target, in place. Raises if the line this project's own
    read_pyproject_version() relies on is not found, for the same reason
    that function raises rather than returning something misleading."""
    text = pyproject.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^version = "[^"]+"$',
        f'version = "{target}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise SystemExit(
            f"could not find a `version = \"...\"` line in {pyproject}; "
            "refusing to write a version pyproject.toml apparently has no "
            "place to hold"
        )
    pyproject.write_text(new_text, encoding="utf-8")


def list_tracked_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [repo_root / line for line in result.stdout.splitlines() if line]


def _find_text_pins(
    tracked_files: list[Path], repo_root: Path, pattern: re.Pattern[str]
) -> list[TextPin]:
    found: list[TextPin] = []
    for path in tracked_files:
        rel = path.relative_to(repo_root).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable file (images under docs/images, etc.):
            # not a place an install pin could live.
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in pattern.finditer(line):
                found.append(TextPin(path=path, line_no=line_no, version=m.group(m.lastindex or 1)))
    return found


def find_at_pins(tracked_files: list[Path], repo_root: Path = REPO_ROOT) -> list[TextPin]:
    """Return every `@vX.Y.Z` found in a tracked text file."""
    return _find_text_pins(tracked_files, repo_root, AT_PIN_RE)


def find_prose_pins(tracked_files: list[Path], repo_root: Path = REPO_ROOT) -> list[TextPin]:
    """Return every known prose version mention (PROSE_LEAD_INS) found in a
    tracked text file."""
    return _find_text_pins(tracked_files, repo_root, PROSE_PIN_RE)


def find_manifest_version_pins(
    tracked_files: list[Path], repo_root: Path = REPO_ROOT
) -> list[ManifestPin]:
    """Return every tracked JSON manifest under integrations/ that declares
    a top-level "version" field."""
    found: list[ManifestPin] = []
    for path in tracked_files:
        rel = path.relative_to(repo_root)
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
            found.append(ManifestPin(path=path, version=version))
    return found


@dataclass(frozen=True)
class Discovery:
    """Every pin this module found, gathered in one pass over the tracked
    tree. Both callers build one of these and work from it, so there is
    only ever one traversal of `git ls-files` per run."""

    at_pins: list[TextPin]
    prose_pins: list[TextPin]
    manifest_pins: list[ManifestPin]


def discover(repo_root: Path = REPO_ROOT) -> Discovery:
    tracked_files = list_tracked_files(repo_root)
    return Discovery(
        at_pins=find_at_pins(tracked_files, repo_root),
        prose_pins=find_prose_pins(tracked_files, repo_root),
        manifest_pins=find_manifest_version_pins(tracked_files, repo_root),
    )


def rewrite_text_pin_file(text: str, pattern: re.Pattern[str], target: str) -> tuple[str, int]:
    """Rewrite every match of pattern in text so its version group becomes
    target, preserving everything else in the match (the `@v` or the prose
    lead-in) unchanged. Works for both AT_PIN_RE and PROSE_PIN_RE because in
    both the version is the last captured group and sits at the tail end of
    the full match, so slicing that many characters off the end of the
    match leaves exactly the lead-in to keep."""

    def repl(m: re.Match[str]) -> str:
        version = m.group(m.lastindex or 1)
        lead_in = m.group(0)[: -len(version)]
        return f"{lead_in}{target}"

    return pattern.subn(repl, text)


def rewrite_manifest_version(text: str, old_version: str, target: str) -> tuple[str, int]:
    """Rewrite a JSON manifest's top-level "version": "OLD" field to target,
    as a targeted line-anchored text substitution rather than a
    json.load/json.dump round trip, so the file's existing formatting
    (indentation, key order, trailing newline) survives untouched. Anchored
    to the whole line and capped at one replacement: the field this
    function is called for was already confirmed, by
    find_manifest_version_pins()'s json.loads parse, to be the document's
    top-level "version" key, and count=1 makes sure only that first
    matching line moves even if some other JSON string elsewhere in the
    file happens to equal old_version."""
    pattern = re.compile(
        r'^(\s*"version"\s*:\s*")' + re.escape(old_version) + r'(",?\s*)$',
        re.MULTILINE,
    )
    return pattern.subn(rf"\g<1>{target}\g<2>", text, count=1)
