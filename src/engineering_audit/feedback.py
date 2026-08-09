"""Shared feedback-body and issue-attribution construction.

Both `server.py`'s `submit_feedback` tool and `report.py`'s feedback section
need to build the exact same text from the same inputs (the user's free
text, always-included run metadata, and whichever telemetry sections the
user consented to). They must never be allowed to describe the same consent
choice differently, so the construction lives here, once, and both import
it.

The same reasoning applies to `server.py`'s `file_issues` tool and
`report.py`'s issues section, for the attribution line appended to every
finding filed or copied as an issue: :func:`build_issue_trailing_line` is
the one place that wording is built.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from engineering_audit.rules import Rule, citation
from engineering_audit.schema import DomainResult, Finding, RunMeta, TelemetryConsent

__all__ = [
    "FEEDBACK_REPO",
    "FEEDBACK_EMAIL",
    "feedback_subject",
    "rules_pack_label",
    "build_feedback_sections",
    "build_feedback_body",
    "build_mailto_url",
    "build_issue_trailing_line",
]

# The tool author's repository, for filing a feedback issue, and a personal
# email as the fallback when gh is unavailable or filing fails.
FEEDBACK_REPO = "rodlunt/engineering-audit"
FEEDBACK_EMAIL = "rodneylunt79+audit-feedback@gmail.com"

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def feedback_subject(meta: RunMeta) -> str:
    """"Feedback: audit run <date> (<assistant>)", used both as the filed
    issue's title and the mailto fallback's subject, so a filed issue and
    its unsent-email fallback are recognisably the same piece of feedback."""
    date = meta.started[:10]
    return f"Feedback: audit run {date} ({meta.assistant})"


def rules_pack_label(meta: RunMeta) -> str:
    """The human-readable rules pack label: the pack name, plus its version
    in parentheses when one is recorded.

    Built here once so build_feedback_sections (this module), report.py's
    _render_meta_block and report.py's _render_footer cannot drift apart on
    how the same run's rules pack is described.
    """
    if meta.rules_pack_version:
        return f"{meta.rules_pack_name} ({meta.rules_pack_version})"
    return meta.rules_pack_name


def build_feedback_sections(
    meta: RunMeta,
    domain_results: dict[str, DomainResult],
) -> dict[str, str]:
    """Build every fixed-text feedback section, keyed by name, regardless of
    consent.

    This is the single place the wording of each telemetry section is
    built. :func:`build_feedback_body` (the MCP path) and the report's
    feedback UI (the report-rendering path, via the embedded JSON block
    read by its inline JS) both draw from this same dict, so the two can
    never describe the same consent choice differently. Computing every
    section unconditionally and letting the caller decide which to use
    keeps consent purely a selection concern, never a wording concern.
    """
    meta_lines = [
        f"Tool version: {meta.tool_version}",
        f"Tool commit: {meta.tool_commit or 'unknown'}",
        f"Rules pack: {rules_pack_label(meta)}",
        f"Rules commit: {meta.rules_pack_commit or 'unknown'}",
        f"Assistant: {meta.assistant}",
        f"Model: {meta.model}",
        f"Repository: {meta.repo_name} ({meta.repo_commit})",
        f"Started: {meta.started}",
        f"Finished: {meta.finished or 'in progress'}",
    ]
    run_metadata = "Run metadata\n" + "\n".join(f"- {line}" for line in meta_lines)

    inspected = sum(
        r.coverage.files_inspected for r in domain_results.values() if r.coverage is not None
    )
    skipped = sum(
        r.coverage.files_skipped for r in domain_results.values() if r.coverage is not None
    )
    coverage = (
        "Coverage\n"
        f"- Files inspected: {inspected}\n"
        f"- Files skipped: {skipped}"
    )

    all_findings = [f for r in domain_results.values() for f in r.findings]
    severity_counts = Counter(f.severity.value for f in all_findings)
    domain_counts = Counter(
        domain_id for domain_id, r in domain_results.items() for _ in r.findings
    )
    severity_lines = "\n".join(
        f"- {sev}: {severity_counts.get(sev, 0)}" for sev in _SEVERITY_ORDER
    )
    # Built from every domain in domain_results, defaulting to zero, the same
    # way severity_lines above always lists all four severities: a domain
    # audited and found clean must still appear here, not be indistinguishable
    # from a domain that never ran at all.
    domain_lines = (
        "\n".join(
            f"- {domain_id}: {domain_counts.get(domain_id, 0)}" for domain_id in domain_results
        )
        or "- No domains audited."
    )
    rollup = (
        "Findings rollup\n"
        f"Total: {len(all_findings)}\n"
        f"By severity:\n{severity_lines}\n"
        f"By domain:\n{domain_lines}"
    )

    self_assessment_lines = []
    for domain_id, result in domain_results.items():
        if result.status == "could-not-run":
            self_assessment_lines.append(f"- {domain_id}: could not run, {result.reason}")
        elif result.self_assessment is not None:
            sa = result.self_assessment
            limits = f" Limits: {sa.limits}." if sa.limits else ""
            self_assessment_lines.append(f"- {domain_id}: confidence {sa.confidence}.{limits}")
        else:
            self_assessment_lines.append(f"- {domain_id}: no self-assessment reported")
    self_assessment = "Self-assessment by domain\n" + "\n".join(self_assessment_lines)

    environment = meta.environment or {}
    if environment:
        env_lines = "\n".join(f"- {key}: {value}" for key, value in environment.items())
    else:
        env_lines = "- No environment information reported for this run."
    environment_section = f"Environment\n{env_lines}"

    return {
        "run_metadata": run_metadata,
        "coverage": coverage,
        "rollup": rollup,
        "self_assessment": self_assessment,
        "environment": environment_section,
    }


def build_feedback_body(
    free_text: str | None,
    meta: RunMeta,
    consent: TelemetryConsent,
    domain_results: dict[str, DomainResult],
) -> str:
    """Build the plain-text feedback body: the user's free text (if any),
    then the always-included run-metadata section, then each consented
    telemetry section in turn. A section the user did not consent to is
    omitted entirely, not included empty: an omission is the only way an
    unconsented section can be told apart from a consented one that simply
    had nothing to report.
    """
    sections_by_name = build_feedback_sections(meta, domain_results)

    sections: list[str] = []
    if free_text and free_text.strip():
        sections.append(free_text.strip())

    sections.append(sections_by_name["run_metadata"])
    if consent.coverage:
        sections.append(sections_by_name["coverage"])
    if consent.rollup:
        sections.append(sections_by_name["rollup"])
    if consent.self_assessment:
        sections.append(sections_by_name["self_assessment"])
    if consent.environment:
        sections.append(sections_by_name["environment"])

    return "\n\n".join(sections)


def build_mailto_url(email: str, subject: str, body: str) -> str:
    """Build a mailto: URL with a URL-encoded subject and body.

    Used by `server.py`'s `submit_feedback` tool for its mailto fallback
    when `gh` is unavailable or filing fails. The report's own "Email
    feedback" button builds its mailto URL client-side in JS instead (the
    body is assembled at click time from the current textarea text and
    ticked sections, which this module cannot see), so this function is not
    called from `report.py`.
    """
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


def build_issue_trailing_line(finding: Finding, rule: Rule) -> str:
    """Build the trailing attribution line appended after every filed or
    copyable issue body: "Found by an engineering-practice audit (rule
    <id>, severity <sev>, at <loc>). Reference: <capped citation>".

    This is the single place that wording is built. `server.py`'s
    `file_issues` (issues filed via the user's own `gh` CLI) and
    `report.py`'s issues section (issues copied from, or filed via a PAT
    from, the rendered report) both call this, so a filed issue and its
    in-report copy text can never describe the same finding differently.
    """
    if not rule.source:
        raise ValueError(
            f"finding references rule {rule.id}, which has no cited source in the "
            "rules pack. A finding is a published claim; this tool does not publish "
            "claims without evidence."
        )
    return (
        f"Found by an engineering-practice audit (rule {finding.rule_id}, severity "
        f"{finding.severity.value}, at {finding.location}). Reference: {citation(rule.source)}"
    )
