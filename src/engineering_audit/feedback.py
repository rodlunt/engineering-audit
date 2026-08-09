"""Shared feedback-body construction.

Both `server.py`'s `submit_feedback` tool and `report.py`'s feedback section
need to build the exact same text from the same inputs (the user's free
text, always-included run metadata, and whichever telemetry sections the
user consented to). They must never be allowed to describe the same consent
choice differently, so the construction lives here, once, and both import
it.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from engineering_audit.schema import DomainResult, RunMeta, TelemetryConsent

__all__ = [
    "FEEDBACK_REPO",
    "FEEDBACK_EMAIL",
    "feedback_subject",
    "build_feedback_body",
    "build_mailto_url",
]

# The tool author's repository, for filing a feedback issue, and a personal
# email as the fallback when gh is unavailable or filing fails.
FEEDBACK_REPO = "rodlunt/engineering-audit"
FEEDBACK_EMAIL = "rodneylunt79@gmail.com"

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def feedback_subject(meta: RunMeta) -> str:
    """"Feedback: audit run <date> (<assistant>)", used both as the filed
    issue's title and the mailto fallback's subject, so a filed issue and
    its unsent-email fallback are recognisably the same piece of feedback."""
    date = meta.started[:10]
    return f"Feedback: audit run {date} ({meta.assistant})"


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
    sections: list[str] = []

    if free_text and free_text.strip():
        sections.append(free_text.strip())

    rules_pack_label = meta.rules_pack_name
    if meta.rules_pack_version:
        rules_pack_label = f"{rules_pack_label} ({meta.rules_pack_version})"
    meta_lines = [
        f"Tool version: {meta.tool_version}",
        f"Rules pack: {rules_pack_label}",
        f"Assistant: {meta.assistant}",
        f"Model: {meta.model}",
        f"Repository: {meta.repo_name} ({meta.repo_commit})",
        f"Started: {meta.started}",
        f"Finished: {meta.finished or 'in progress'}",
    ]
    sections.append("Run metadata\n" + "\n".join(f"- {line}" for line in meta_lines))

    if consent.coverage:
        inspected = sum(
            r.coverage.files_inspected for r in domain_results.values() if r.coverage is not None
        )
        skipped = sum(
            r.coverage.files_skipped for r in domain_results.values() if r.coverage is not None
        )
        sections.append(
            "Coverage\n"
            f"- Files inspected: {inspected}\n"
            f"- Files skipped: {skipped}"
        )

    if consent.rollup:
        all_findings = [f for r in domain_results.values() for f in r.findings]
        severity_counts = Counter(f.severity.value for f in all_findings)
        domain_counts = Counter(
            domain_id for domain_id, r in domain_results.items() for _ in r.findings
        )
        severity_lines = "\n".join(
            f"- {sev}: {severity_counts.get(sev, 0)}" for sev in _SEVERITY_ORDER
        )
        domain_lines = (
            "\n".join(f"- {domain_id}: {count}" for domain_id, count in domain_counts.items())
            or "- No findings."
        )
        sections.append(
            "Findings rollup\n"
            f"Total: {len(all_findings)}\n"
            f"By severity:\n{severity_lines}\n"
            f"By domain:\n{domain_lines}"
        )

    if consent.self_assessment:
        lines = []
        for domain_id, result in domain_results.items():
            if result.status == "could-not-run":
                lines.append(f"- {domain_id}: could not run, {result.reason}")
            elif result.self_assessment is not None:
                sa = result.self_assessment
                limits = f" Limits: {sa.limits}." if sa.limits else ""
                lines.append(f"- {domain_id}: confidence {sa.confidence}.{limits}")
            else:
                lines.append(f"- {domain_id}: no self-assessment reported")
        sections.append("Self-assessment by domain\n" + "\n".join(lines))

    if consent.environment:
        environment = meta.environment or {}
        if environment:
            env_lines = "\n".join(f"- {key}: {value}" for key, value in environment.items())
        else:
            env_lines = "- No environment information reported for this run."
        sections.append(f"Environment\n{env_lines}")

    return "\n\n".join(sections)


def build_mailto_url(email: str, subject: str, body: str) -> str:
    """Build a mailto: URL with a URL-encoded subject and body.

    This is the only place a mailto URL is constructed: it is built entirely
    from our own strings (a fixed email address, a subject we generated, a
    body we generated), never from untrusted data, which is what allows the
    report's href-scheme check to permit mailto here while still rejecting
    it everywhere data-sourced URLs are rendered.
    """
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"
