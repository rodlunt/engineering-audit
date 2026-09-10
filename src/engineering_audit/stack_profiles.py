"""Loader for stack profile rules from a rules pack.

Stack profiles are technology-specific rule sets (e.g., Python+FastAPI, Python+Django)
that augment the main rules pack with stack-specific engineering practices.

**On-disk layout:**

Stack profile definitions are stored as markdown files in a `stack-profiles/`
subdirectory of the rules pack root. Each profile file:

- Is named using the pattern `sp-<identifier>.md` (e.g., `sp-python-fastapi.md`)
- Defines one or more stack identifiers (e.g., "python", "fastapi")
- Follows the same markdown structure as domain files in the pack:
  - A title header: `# Stack Profile: <name>`
  - Stack identifiers declaration (e.g., `**Stack identifiers:** python, fastapi`)
  - A `**Trigger:**` line
  - A `**Load this when:**` section
  - Rule headings (### N. Rule title) with Rule id and Volatility footers
  - Source citations where applicable

The loader parses these files into Rule objects with:
- `source="stack-profile"`
- `stack_profile` set to the matched identifier
- Standard Rule fields (id, title, number, volatility, source)

See ADR 0002 (baked-stack-profiles-not-live-lookup.md) for the design decision.
No network access is used. Malformed profile files raise StackProfileParseError.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from engineering_audit.standards import Rule

__all__ = [
    "load_stack_profile_rules",
    "StackProfileParseError",
]


class StackProfileParseError(Exception):
    """Raised when a stack profile file cannot be parsed."""

    pass


def load_stack_profile_rules(
    pack_root: Path, stack_identifiers: Iterable[str]
) -> list[Rule]:
    """Load stack profile rules for given stack identifiers from a rules pack.

    Searches the rules pack for stack profile definitions matching the given
    stack identifiers. A profile matches if ALL of its declared stack identifiers
    are in the requested set. For example, a profile declaring 'python, fastapi'
    will only load when both python and fastapi are detected; it will not load
    for a python+django project. If the pack contains no profiles directory or the
    directory has no matching profile files, returns an empty list.

    Args:
        pack_root: Path to the rules pack root directory.
        stack_identifiers: Iterable of individual stack identifiers to match
            (e.g., ['python', 'fastapi']). A profile file that declares multiple
            identifiers will match only if ALL of them are in this set. Each Rule's
            stack_profile field is set to the first identifier declared in the
            profile.

    Returns:
        List of Rule objects with source="stack-profile" and stack_profile
        set to the first declared identifier in the matching profile file,
        sorted by rule ID. Returns empty list if no matching profiles are found.

    Raises:
        StackProfileParseError: If a profile file declares stack identifiers
            but cannot be parsed as valid rules (malformed markdown, missing
            required fields, etc.).
    """
    pack_root = Path(pack_root)
    profiles_dir = pack_root / "stack-profiles"

    # If no stack-profiles directory exists, return empty list
    if not profiles_dir.is_dir():
        return []

    # Normalise requested identifiers to a set for matching
    requested_ids = set(s.lower().strip() for s in stack_identifiers)

    # Find all profile files and parse matching ones
    rules: list[Rule] = []
    profile_files = sorted(profiles_dir.glob("sp-*.md"))

    for profile_file in profile_files:
        try:
            text = profile_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StackProfileParseError(
                f"Could not read stack profile {profile_file}: {exc}"
            ) from exc

        # Extract stack identifiers from the file
        # Look for **Stack identifiers:** line
        identifiers_match = re.search(
            r"\*\*Stack identifiers:\*\*\s*(.+?)(?:\n|$)", text, re.IGNORECASE
        )
        if identifiers_match is None:
            # No identifiers declared; skip this file silently
            # (it may be a template or draft)
            continue

        identifiers_str = identifiers_match.group(1)
        # Keep order: split and normalise, but preserve first declared identifier
        file_identifiers_list = [i.lower().strip() for i in identifiers_str.split(",")]
        file_identifiers_set = set(file_identifiers_list)

        # Check if ALL of the file's identifiers are present in requested ones
        # This ensures a profile for "python + fastapi" only loads when both are detected
        if not file_identifiers_set.issubset(requested_ids):
            # No match; skip this file
            continue

        # This file matches; parse it
        # Use first declared identifier for the Rule's stack_profile field
        primary_identifier = file_identifiers_list[0]

        try:
            file_rules = _parse_stack_profile(profile_file, text, primary_identifier)
            rules.extend(file_rules)
        except StackProfileParseError:
            raise
        except Exception as exc:
            raise StackProfileParseError(
                f"Failed to parse stack profile {profile_file}: {exc}"
            ) from exc

    # Sort by rule ID for determinism
    rules.sort(key=lambda r: r.rule_id)
    return rules


# Regex patterns borrowed from rules.py, adapted for stack profiles
_TRIGGER_RE = re.compile(r"^\*\*Trigger:\*\*\s*(?P<trigger>.+)$", re.MULTILINE)
_RULE_HEADING_RE = re.compile(
    r"^###\s*(?P<label>[A-Za-z]*\d+)\.\s*(?P<title>.+?)\s*$", re.MULTILINE
)
_ANY_H3_RE = re.compile(r"^###\s.*$", re.MULTILINE)
_RULE_ID_RE = re.compile(r"Rule\s+id:\s*(?P<rule_id>[A-Za-z0-9]+-[A-Za-z0-9]+)\s*\.")


def _parse_stack_profile(path: Path, text: str, stack_identifier: str) -> list[Rule]:
    """Parse a stack profile markdown file into Rule objects.

    Args:
        path: Path to the profile file (for error reporting).
        text: Full markdown text of the profile.
        stack_identifier: The stack identifier this profile matched (to set
            in Rule.stack_profile).

    Returns:
        List of Rule objects with source="stack-profile" and stack_profile
        set to the given identifier.

    Raises:
        StackProfileParseError: If the file cannot be parsed.
    """
    # Check for Trigger line (required)
    trigger_match = _TRIGGER_RE.search(text)
    if trigger_match is None:
        raise StackProfileParseError(
            f"{path}: declares stack identifiers but has no '**Trigger:**' line"
        )

    # Find all rule headings
    headings = list(_RULE_HEADING_RE.finditer(text))
    if not headings:
        raise StackProfileParseError(
            f"{path}: declares a Trigger but has no '### N. Rule title' headings"
        )

    # Validate that all H3 headings match the rule pattern
    all_h3_lines = [line.rstrip() for line in _ANY_H3_RE.findall(text)]
    matched_lines = {m.group(0).rstrip() for m in headings}
    unmatched = [line for line in all_h3_lines if line not in matched_lines]
    if len(all_h3_lines) != len(headings):
        detail = f": {unmatched[:5]}" if unmatched else ""
        raise StackProfileParseError(
            f"{path}: {len(all_h3_lines) - len(headings)} '###' heading(s) do not match "
            f"the '### <label>. <title>' rule-heading shape and would be silently "
            f"absorbed into the previous rule{detail}"
        )

    # Parse rules from headings
    rules: list[Rule] = []
    seen_ids: dict[str, str] = {}

    for index, heading_match in enumerate(headings):
        start = heading_match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end]
        heading_label = heading_match.group("label")
        heading_number = int(re.sub(r"\D", "", heading_label))
        heading_title = heading_match.group("title").strip()

        # Extract rule id (last occurrence in block)
        id_matches = list(_RULE_ID_RE.finditer(block))
        if not id_matches:
            raise StackProfileParseError(
                f"{path}: rule {heading_number} ('{heading_title}') has no parseable "
                "'Rule id: ...' metadata line"
            )

        winning_id_match = id_matches[-1]
        rule_id = winning_id_match.group("rule_id").upper()

        if rule_id in seen_ids:
            raise StackProfileParseError(
                f"{path}: rule id {rule_id} appears on both '{seen_ids[rule_id]}' and "
                f"'{heading_title}'. Duplicate ids make verdicts unattributable; each "
                "rule needs its own id."
            )
        seen_ids[rule_id] = heading_title

        # Create Rule with stack-profile source
        # Stack profile rules are added as provisional rules; the choice resolution
        # logic will upgrade them to verified-pass when the user chooses audit stack
        today = datetime.now().date().isoformat()
        rule = Rule(
            rule_id=rule_id,
            domain_id=None,  # Stack profile rules don't belong to a domain
            text_short=heading_title,
            text_body="",  # No body text extracted from profile markdown
            source="stack-profile",
            stack_profile=stack_identifier,
            status="provisional",
            verified_date=today,
            severity=None,
            finding_details=None,
            conflict_with_stack_profile=None,
            conflict_resolution=None,
            source_url=None,
            grill_intent_note=None,
            revisit_trigger=None,
            fix_due=None,
            ownership=None,
        )
        rules.append(rule)

    return rules
