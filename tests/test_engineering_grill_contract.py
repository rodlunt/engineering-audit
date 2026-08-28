"""Regression checks for the Engineering Grill's interview contract.

These checks read the integration documents as structured prose. They use
headings, field names, and short semantic markers instead of asserting whole
paragraphs, so useful wording changes do not make the tests brittle.

Tests guard the current skill layout on fix/grill-review-run-friction and
supersede the stale version on feature/engineering-grill.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
GRILL_ROOT = REPO_ROOT / "integrations" / "engineering-grill"
SKILL_PATH = GRILL_ROOT / "engineering-grill" / "SKILL.md"
FORMATS_PATH = (
    GRILL_ROOT / "engineering-grill" / "references" / "documentation-formats.md"
)
README_PATH = GRILL_ROOT / "README.md"

SKILL = SKILL_PATH.read_text(encoding="utf-8")
FORMATS = FORMATS_PATH.read_text(encoding="utf-8")
README = README_PATH.read_text(encoding="utf-8")
_coverage_template_match = re.search(
    r"##\s+Engineering coverage.*?```markdown\n(?P<template>.*?)\n```",
    FORMATS,
    flags=re.MULTILINE | re.DOTALL,
)
assert _coverage_template_match is not None, "engineering coverage template is missing"
COVERAGE_TEMPLATE = _coverage_template_match.group("template")


def _section(document: str, heading: str) -> str:
    """Return one level-two section, including its content but not the next section."""

    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$((?:(?!^##\s).)*)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"expected a level-two section named {heading!r}"
    return match.group(1)


def _first_match(document: str, patterns: tuple[str, ...], label: str) -> re.Match[str]:
    """Return the earliest semantic marker matching one of ``patterns``."""

    matches = [
        re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
        for pattern in patterns
    ]
    found = [match for match in matches if match is not None]
    assert found, f"{label} marker is missing"
    return min(found, key=lambda match: match.start())


def _markdown_table(section: str) -> tuple[list[str], list[list[str]]]:
    """Read the first Markdown table in a public coverage-record section."""

    lines = [
        line.strip() for line in section.splitlines() if line.strip().startswith("|")
    ]
    assert len(lines) >= 3, (
        "expected a Markdown table with a header and at least one row"
    )

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    headers = cells(lines[0])
    rows = [cells(line) for line in lines[2:]]
    assert all(len(row) == len(headers) for row in rows), (
        "coverage-record table rows must have the same columns as their header"
    )
    return headers, rows


def test_coverage_record_defines_four_outcomes_and_cross_reference_provenance() -> None:
    """Question rows expose stable outcome and provenance fields."""

    outcomes_section = _section(COVERAGE_TEMPLATE, "Question outcomes")
    headers, rows = _markdown_table(outcomes_section)

    assert headers == [
        "Session",
        "Question",
        "Domain",
        "Outcome",
        "Decision or answer",
        "Provenance",
        "Reason",
    ]

    outcomes = {row[3] for row in rows}
    assert outcomes == {
        "answered",
        "resolved-by-cross-reference",
        "deferred",
        "not-asked",
    }

    resolved = next(
        row
        for row in rows
        if row[0] == "fresh" and row[3] == "resolved-by-cross-reference"
    )
    assert re.fullmatch(r"DEC-\d+: .+", resolved[4])
    assert re.search(r"earlier decision DEC-\d+: .+", resolved[5], flags=re.IGNORECASE)
    assert resolved[6] not in {"", "-"}


def test_coverage_record_preserves_cross_reference_on_resume() -> None:
    """Resuming carries provenance forward without changing the outcome."""

    outcomes_section = _section(COVERAGE_TEMPLATE, "Question outcomes")
    _headers, rows = _markdown_table(outcomes_section)

    fresh = next(
        row
        for row in rows
        if row[0] == "fresh" and row[3] == "resolved-by-cross-reference"
    )
    resumed = next(row for row in rows if row[0] == "resumed" and row[1] == fresh[1])

    assert resumed[3] == "resolved-by-cross-reference"
    assert resumed[3] != "answered"
    assert resumed[4:] == fresh[4:]


def test_coverage_record_count_equations_hold_in_fresh_and_resumed_sessions() -> None:
    """Fresh and resumed coverage snapshots use both public count equations."""

    totals_section = _section(COVERAGE_TEMPLATE, "Session totals")
    headers, rows = _markdown_table(totals_section)

    assert headers == [
        "Session",
        "Derived",
        "Asked",
        "Answered",
        "Resolved by cross-reference",
        "Deferred",
        "Not asked",
    ]
    totals = {row[0]: [int(value) for value in row[1:]] for row in rows}
    assert set(totals) == {"fresh", "resumed"}

    for derived, asked, answered, resolved, deferred, not_asked in totals.values():
        assert asked == answered + deferred
        assert derived == asked + resolved + not_asked

    assert all(values[3] > 0 for values in totals.values())


def test_domain_totals_match_resumed_question_outcomes() -> None:
    """Per-domain coverage totals agree with the resumed Markdown ledger."""

    domain_headers, domain_rows = _markdown_table(
        _section(COVERAGE_TEMPLATE, "Domain coverage")
    )
    outcome_headers, outcome_rows = _markdown_table(
        _section(COVERAGE_TEMPLATE, "Question outcomes")
    )
    count_headers = (
        "Derived",
        "Asked",
        "Answered",
        "Resolved by cross-reference",
        "Deferred",
        "Not asked",
    )
    assert domain_headers == [
        "Domain",
        "Status",
        "Basis",
        "Source",
        *count_headers,
        "Revisit trigger",
    ]
    assert "Outcome" in outcome_headers
    domain_columns = {header: index for index, header in enumerate(domain_headers)}
    outcome_columns = {header: index for index, header in enumerate(outcome_headers)}

    expected_by_domain: dict[str, dict[str, int]] = {}
    for row in outcome_rows:
        if row[outcome_columns["Session"]] != "resumed":
            continue
        domain_id = row[outcome_columns["Domain"]]
        outcome = row[outcome_columns["Outcome"]]
        counts = expected_by_domain.setdefault(
            domain_id, {header: 0 for header in count_headers}
        )
        counts["Derived"] += 1
        if outcome in {"answered", "deferred"}:
            counts["Asked"] += 1
        if outcome == "answered":
            counts["Answered"] += 1
        elif outcome == "resolved-by-cross-reference":
            counts["Resolved by cross-reference"] += 1
        elif outcome == "deferred":
            counts["Deferred"] += 1
        elif outcome == "not-asked":
            counts["Not asked"] += 1

    for row in domain_rows:
        domain_id = row[domain_columns["Domain"]].split(maxsplit=1)[0]
        if (
            row[domain_columns["Status"]] != "active-now"
            or row[domain_columns["Derived"]] == "n/a"
        ):
            continue
        assert domain_id in expected_by_domain, (
            f"active domain {domain_id} has no resumed question rows"
        )
        actual = {header: int(row[domain_columns[header]]) for header in count_headers}
        assert actual == expected_by_domain[domain_id], (
            f"domain {domain_id} totals disagree with its resumed question outcomes"
        )

    total = next(
        row for row in domain_rows if row[domain_columns["Domain"]] == "**Total**"
    )
    actual_total = {
        header: int(total[domain_columns[header]].strip("*"))
        for header in count_headers
    }
    expected_total = {
        header: sum(counts[header] for counts in expected_by_domain.values())
        for header in count_headers
    }
    assert actual_total == expected_total


def test_documentation_format_states_outcome_and_resume_rules() -> None:
    """The format guide makes the generated record rules explicit."""

    # The output example contains level-two headings of its own, so inspect the
    # complete format reference rather than stopping at the first nested heading.
    coverage = FORMATS

    assert re.search(
        r"`resolved-by-cross-reference`.*?(?:only|valid only).*?earlier decision"
        r".*?identifier.*?title.*?reason",
        coverage,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(r"asked\s*=\s*answered\s*\+\s*deferred", coverage)
    assert re.search(
        r"derived\s*=\s*asked\s*\+\s*resolved-by-cross-reference\s*\+\s*not[- ]asked",
        coverage,
    )
    assert re.search(
        r"resum.*?preserv.*?resolved-by-cross-reference.*?(?:not|never).*?answered",
        coverage,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_skill_and_guide_define_outcomes_resume_and_count_semantics() -> None:
    """The user-facing guide matches the coverage-record contract."""

    hot_seat = _section(SKILL, "The Hot Seat")
    capture = _section(SKILL, "Capture confirmed decisions")
    progress = _section(README, "Progress and stopping")

    assert re.search(
        r"`answered`.*?direct user answer", hot_seat, flags=re.IGNORECASE | re.DOTALL
    )
    assert re.search(
        r"`resolved-by-cross-reference`.*?not asked directly.*?earlier decision",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"`resolved-by-cross-reference`.*?(?:only|valid only).*?identifier.*?title.*?reason",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"`deferred`.*?asked.*?reason", hot_seat, flags=re.IGNORECASE | re.DOTALL
    )
    assert re.search(
        r"`not-asked`.*?retained.*?not shown.*?resolved",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"resum.*?preserv.*?resolved-by-cross-reference.*?(?:not|never).*?answered",
        hot_seat + capture,
        flags=re.IGNORECASE | re.DOTALL,
    )

    assert "resolved-by-cross-reference" in progress
    assert re.search(r"-\s+\*\*not-asked:\*\*", progress)
    assert re.search(r"asked\s*=\s*answered\s*\+\s*deferred", progress)
    assert re.search(
        r"derived\s*=\s*asked\s*\+\s*resolved-by-cross-reference\s*\+\s*not[- ]asked",
        progress,
    )


def test_skill_has_nine_section_headings_in_order() -> None:
    """Guard against revert to old layout. Current sections are fixed."""
    expected_sections = [
        "Load the live framework",
        "Establish project facts",
        "Triage every returned domain",
        "Derive the questions",
        "The Hot Seat",
        "The deep dive",
        "Capture confirmed decisions",
        "Complete the grill",
        "Host notes",
    ]

    found_sections = re.findall(r"^##\s+([^#\n]+?)\s*$", SKILL, flags=re.MULTILINE)
    assert found_sections == expected_sections, (
        f"SKILL.md must have exactly nine sections in this order: {expected_sections}; "
        f"found {found_sections}"
    )


def test_four_domain_classifications_exist() -> None:
    """Guard that all four triage states are present."""
    triage = _section(SKILL, "Triage every returned domain")

    states = re.findall(r"^[-*]\s+\*\*([^*]+)\*\*:", triage, flags=re.MULTILINE)
    assert states == ["active-now", "required-later", "not-applicable", "unknown"], (
        "Triage section must define exactly four decision states in order: "
        "active-now, required-later, not-applicable, unknown; found " + str(states)
    )


def test_framework_access_is_read_only() -> None:
    """Guard that only list_domains and get_domain are used."""
    load_framework = _section(SKILL, "Load the live framework")

    assert re.search(
        r"use\s+only\s+`list_domains`\s+and\s+`get_domain`",
        load_framework,
        flags=re.IGNORECASE,
    ), (
        "Load the live framework section must state that only list_domains "
        "and get_domain are used"
    )


def test_framework_forbids_lifecycle_tools() -> None:
    """Guard that begin_run, record_domain_result, file_issues, render_report are forbidden."""
    load_framework = _section(SKILL, "Load the live framework")

    forbidden_tools = [
        "begin_run",
        "record_domain_result",
        "file_issues",
        "render_report",
    ]

    for tool in forbidden_tools:
        assert re.search(
            rf"(?:never|must\s+not|do\s+not).*?`{re.escape(tool)}`",
            load_framework,
            flags=re.IGNORECASE | re.DOTALL,
        ), f"Load the live framework section must explicitly forbid {tool}"


def test_first_shipped_version_vs_aspirational_audience() -> None:
    """Guard distinction between near-term and aspirational audience in triage."""
    establish_facts = _section(SKILL, "Establish project facts")

    assert re.search(
        r"(?:first\s+shipped|initial\s+ship|near-term).{0,80}"
        r"(?:audience|shape)",
        establish_facts,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Establish project facts must distinguish the first-shipped-version audience"

    assert re.search(
        r"(?:aspirational|eventual|later|vision).{0,80}(?:audience|scale)",
        establish_facts,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Establish project facts must distinguish the aspirational/eventual audience"

    assert re.search(
        r"(?:active-now|active).{0,40}(?:near-term|now|initial|first)",
        establish_facts,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Establish project facts must state that triage keys off the near-term audience"

    assert re.search(
        r"(?:aspirational|eventual).{0,60}(?:revisit\s+triggers).{0,40}required-later",
        establish_facts,
        flags=re.IGNORECASE | re.DOTALL,
    ), (
        "Establish project facts must state that aspirational answers set revisit triggers for required-later"
    )


def test_merged_questions_counted_per_domain_distinct_in_total() -> None:
    """Guard merged-question counting: counts for each domain, total is distinct."""
    hot_seat = _section(SKILL, "The Hot Seat")

    assert re.search(
        r"(?:merged\s+)?questions?.{0,100}(?:count|count\s+as\s+asked\s+and\s+answered)"
        r".{0,100}(?:every|each)\s+domain",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    ), (
        "The Hot Seat must state that merged questions count for every domain they subsume"
    )

    assert re.search(
        r"(?:distinct\s+)?(?:question\s+)?count.{0,80}"
        r"(?:not|not\s+the)\s+(?:sum|total|add\s+up)",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    ), (
        "The Hot Seat must state that total row reports distinct questions "
        "not sum of columns"
    )

    # Also verify in documentation-formats.md (must check full file since merged
    # explanation comes after code block with embedded ## headings)
    assert re.search(
        r"merged\s+(?:question|questions)",
        FORMATS,
        flags=re.IGNORECASE,
    ), "documentation-formats.md must discuss merged questions"

    assert re.search(
        r"per-domain.{0,80}(?:may\s+)?exceed.{0,80}distinct",
        FORMATS,
        flags=re.IGNORECASE | re.DOTALL,
    ), (
        "documentation-formats.md must note that per-domain totals may exceed distinct count"
    )

    assert re.search(
        r"(?:Total.*?)?row\s+reports\s+distinct",
        FORMATS,
        flags=re.IGNORECASE | re.DOTALL,
    ), "documentation-formats.md must state that Total row reports distinct count"


def test_scope_expanding_events_named_including_prototype_reactivation() -> None:
    """Guard that scope-expanding events are listed, including prototype/build reactivation."""
    triage = _section(SKILL, "Triage every returned domain")

    assert re.search(
        r"(?:scope[- ]expanding|expand\s+(?:the\s+)?scope).{0,20}events",
        triage,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Triage section must name scope-expanding events up front"

    assert re.search(
        r"(?:prototype|build[- ]side|let'?s\s+build).{0,80}(?:reactivate|activate)",
        triage,
        flags=re.IGNORECASE | re.DOTALL,
    ), (
        "Triage section must state that asking for a prototype or 'let's build' "
        "reactivates build-side domains"
    )


def test_framework_unavailable_offers_save_brief() -> None:
    """Guard that framework-unavailable passage offers to save work before stopping."""
    load_framework = _section(SKILL, "Load the live framework")

    assert re.search(
        r"(?:save|preserve).{0,80}(?:brief|work|facts|session)",
        load_framework,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Load the live framework must offer to save the brief/facts before stopping"

    assert re.search(
        r"(?:save|preserve).{0,80}(?:file|path|location)",
        load_framework,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Load the live framework must name where work is saved"


def test_framework_unavailable_diagnoses_scope_user_issue_245() -> None:
    """Guard that framework-unavailable passage carries issue #245 diagnosis."""
    load_framework = _section(SKILL, "Load the live framework")

    assert re.search(
        r"(?:--scope\s+user|scope.{0,40}user)",
        load_framework,
        flags=re.IGNORECASE,
    ), "Load the live framework must mention --scope user registration issue"

    assert re.search(
        r"(?:245|issue\s+#?\s*245)",
        load_framework,
        flags=re.IGNORECASE,
    ), "Load the live framework must reference issue #245"

    assert re.search(
        r"(?:claude\s+mcp\s+(?:list|remove))",
        load_framework,
        flags=re.IGNORECASE,
    ), "Load the live framework must give concrete diagnosis steps: claude mcp commands"


def test_bail_out_is_unconditional() -> None:
    """Guard that bail-out is unconditional."""
    hot_seat = _section(SKILL, "The Hot Seat")

    assert re.search(
        r"(?:bail[- ]out|stop|enough|that\s+will\s+do).*?(?:is\s+)?unconditional",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    ), "The Hot Seat must state that bail-out is unconditional"


def test_bail_out_offers_handoff_or_mvp_split() -> None:
    """Guard that after bail-out record, two continuations are offered: handoff or MVP."""
    hot_seat = _section(SKILL, "The Hot Seat")

    assert re.search(
        r"(?:handoff|hand[- ]off)",
        hot_seat,
        flags=re.IGNORECASE,
    ), "The Hot Seat must offer handoff as a continuation after bail-out"

    assert re.search(
        r"(?:(?:v1|version\s+1|MVP|minimum\s+viable).{0,80}(?:split|assess))",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    ), "The Hot Seat must offer MVP/v1 assessment as a continuation after bail-out"

    assert re.search(
        r"(?:after\s+the\s+record\s+is\s+written).{0,200}"
        r"(?:handoff|offering.*?(?:continue|way\s+forward))",
        hot_seat,
        flags=re.IGNORECASE | re.DOTALL,
    ), "The Hot Seat must offer these after the record is written"
