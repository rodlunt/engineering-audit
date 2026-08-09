"""Deterministic HTML report renderer.

Builds a single self-contained HTML document from a :class:`RunState` and the
:class:`RulesPack` it was audited against. Every number in the report is
computed here from the run state itself (sums over findings and coverage
records); nothing is accepted as a pre-computed, decorative count. All
interpolated content is passed through ``html.escape`` before it reaches the
page, and the only rules-pack content that ever appears is a rule id or a
rule's short heading title, never a rule's full body text: the rules pack is
private and a shared report must not leak it.
"""

from __future__ import annotations

import html
import string
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    build_feedback_body,
    build_mailto_url,
    feedback_subject,
)
from engineering_audit.rules import Rule, RulesPack
from engineering_audit.schema import (
    DomainResult,
    Finding,
    IncompleteResultError,
    RunState,
    Verdict,
    validate_completeness,
)

__all__ = ["ReportError", "render_report", "write_report"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

_INLINE_SCRIPT = """
function copyIssueText(textareaId, button) {
  var el = document.getElementById(textareaId);
  if (!el) { return; }
  el.select();
  var text = el.value;
  var done = function () {
    var original = button.textContent;
    button.textContent = "Copied";
    setTimeout(function () { button.textContent = original; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done);
  } else {
    document.execCommand("copy");
    done();
  }
}
""".strip()

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


class ReportError(Exception):
    """Raised when a RunState cannot be rendered into a trustworthy report:
    a selected domain has no result, a completed result fails the
    every-rule-has-a-verdict check, or a finding references a rule id that
    is not in the rules pack. A report is what a human trusts as the record
    of the run; it must never render a plausible-looking page over broken or
    incomplete data."""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _require_href_scheme(url: str, allowed: tuple[str, ...], context: str) -> None:
    """Raise ReportError unless url's scheme is one of allowed.

    Used both for issue links (http/https only: these carry a URL a filing
    integration produced from real gh output, and a non-http(s) scheme there
    is a bug upstream, not a cosmetic issue) and for the feedback mailto
    button (mailto only, and only ever called on a URL this module built
    itself from known-safe strings, never on data).
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in allowed:
        raise ReportError(
            f"{context} has scheme '{scheme or '(none)'}', only "
            f"{'/'.join(allowed)} {'is' if len(allowed) == 1 else 'are'} allowed: {url!r}"
        )


def _markdownish(text: str) -> str:
    """Escape then apply the barest paragraph/line-break formatting.

    No markdown library is a dependency here (mcp + pydantic only), so this
    is deliberately not a markdown renderer: it escapes first, then turns
    blank-line-separated chunks into paragraphs and single newlines into
    line breaks.
    """
    # Normalise CRLF (and lone CR) to LF first: a paragraph split on a literal
    # '\n\n' would otherwise miss every blank line in CRLF-sourced text.
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = html.escape(normalised)
    paragraphs = [p for p in escaped.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _render_meta_block(run_state: RunState) -> str:
    meta = run_state.meta
    rules_pack_label = meta.rules_pack_name
    if meta.rules_pack_version:
        rules_pack_label = f"{rules_pack_label} ({meta.rules_pack_version})"
    rows = [
        ("Repository", meta.repo_name),
        ("Commit", meta.repo_commit),
        ("Rules pack", rules_pack_label),
        ("Assistant", meta.assistant),
        ("Model", meta.model),
        ("Tool version", meta.tool_version),
        ("Started", meta.started),
        ("Finished", meta.finished or "in progress"),
    ]
    rows_html = "".join(
        f'<div class="meta-label">{_esc(label)}</div><div class="meta-value">{_esc(value)}</div>'
        for label, value in rows
    )
    return f'<div class="meta-grid">{rows_html}</div>'


def _coverage_summary(selected: dict[str, DomainResult], domain_titles: dict[str, str]) -> str:
    total_inspected = 0
    total_skipped = 0
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.coverage is not None:
            total_inspected += result.coverage.files_inspected
            total_skipped += result.coverage.files_skipped
            note = f" ({_esc(result.coverage.note)})" if result.coverage.note else ""
            rows.append(
                f"<li>{_esc(title)}: {result.coverage.files_inspected} file(s) inspected, "
                f"{result.coverage.files_skipped} skipped{note}</li>"
            )
        else:
            rows.append(f"<li>{_esc(title)}: no coverage reported</li>")
    summary = (
        f"<p>Total files inspected across selected domains: <strong>{total_inspected}</strong>. "
        f"Total files skipped: <strong>{total_skipped}</strong>.</p>"
        f"<ul>{''.join(rows)}</ul>"
    )
    return summary


def _findings_rollup(
    all_findings: list[tuple[str, Finding]], domain_titles: dict[str, str]
) -> str:
    severity_counts: Counter[str] = Counter(f.severity.value for _, f in all_findings)
    # Keyed by domain id, not title: two domains with identical titles (from
    # different rules-pack files) must not merge into one rollup row.
    domain_counts: Counter[str] = Counter(domain_id for domain_id, _ in all_findings)
    total = len(all_findings)

    sev_items = "".join(
        f"<li>{_esc(sev)}: {severity_counts.get(sev, 0)}</li>" for sev in _SEVERITY_ORDER
    )
    domain_items = (
        "".join(
            f"<li>{_esc(domain_id)}: {_esc(domain_titles[domain_id])}: {count}</li>"
            for domain_id, count in domain_counts.items()
        )
        or "<li>No findings.</li>"
    )
    return (
        f"<p>Total findings: <strong>{total}</strong></p>"
        f"<h3>By severity</h3><ul>{sev_items}</ul>"
        f"<h3>By domain</h3><ul>{domain_items}</ul>"
    )


def _could_not_evaluate_list(
    selected: dict[str, DomainResult], rule_index: dict[str, Rule]
) -> str:
    rows = []
    for domain_id, result in selected.items():
        for rv in result.rule_verdicts:
            if rv.verdict != Verdict.COULD_NOT_EVALUATE:
                continue
            rule = rule_index.get(rv.rule_id)
            if rule is None:
                # Consistent with the findings check below: a verdict for a
                # rule id absent from the pack is a broken run, not a cosmetic
                # gap, and must raise rather than render a placeholder label.
                raise ReportError(
                    f"domain '{domain_id}' has a rule_verdict for rule id "
                    f"'{rv.rule_id}', which is not in the rules pack"
                )
            rows.append(
                f"<li><strong>{_esc(rv.rule_id)}</strong> ({_esc(rule.title)}): {_esc(rv.note)}</li>"
            )
    if not rows:
        return (
            '<h3>Could not evaluate</h3>'
            '<p class="ok">Every selected rule reached a verdict of pass, finding or '
            "not applicable. Nothing was left could-not-evaluate.</p>"
        )
    return (
        f"<h3>Could not evaluate ({len(rows)})</h3>"
        f"<ul>{''.join(rows)}</ul>"
    )


def _self_assessment_list(selected: dict[str, DomainResult], domain_titles: dict[str, str]) -> str:
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            rows.append(f"<li>{_esc(title)}: could not run, {_esc(result.reason)}</li>")
        elif result.self_assessment is not None:
            sa = result.self_assessment
            limits = f" Limits: {_esc(sa.limits)}." if sa.limits else ""
            rows.append(f"<li>{_esc(title)}: confidence {_esc(sa.confidence)}.{limits}</li>")
        else:
            rows.append(f"<li>{_esc(title)}: no self-assessment reported</li>")
    return f"<ul>{''.join(rows)}</ul>"


def _environment_info(run_state: RunState) -> str:
    environment = run_state.meta.environment
    if not environment:
        return "<p>No environment information reported for this run.</p>"
    rows = "".join(
        f"<li><strong>{_esc(key)}:</strong> {_esc(value)}</li>" for key, value in environment.items()
    )
    return f"<ul>{rows}</ul>"


def _reference_line(rule: Rule) -> str:
    """Build the deterministic reference line appended after every rendered
    finding's body.

    This is added by the report renderer itself, never by the auditing
    agent: the citation grounding a finding comes from the rules pack's own
    parsed ``Source:`` fragment (see rules.py), not from whatever the agent
    recalls about the rule. A finding is a published claim, and this tool
    does not publish claims without evidence: a rule with no parsed source
    is refused upstream (see the render_report gate), so by the time this
    runs the source is always present.
    """
    if not rule.source:
        raise ReportError(
            f"finding references rule {rule.id}, which has no cited source in the "
            "rules pack. A finding is a published claim; this tool does not publish "
            "claims without evidence. Fix the rule's Source: footer or drop the finding."
        )
    return f"Reference: {rule.id}: {rule.source}"


def _findings_section(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    rule_index: dict[str, Rule],
) -> str:
    blocks = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            blocks.append(
                f"<h3>{_esc(title)}</h3><p class='muted'>This domain could not be run: "
                f"{_esc(result.reason)}</p>"
            )
            continue
        if not result.findings:
            blocks.append(f"<h3>{_esc(title)}</h3><p>No findings.</p>")
            continue
        items = []
        for finding in result.findings:
            severity = finding.severity.value
            badge = f'<span class="severity-badge severity-{_esc(severity)}">{_esc(severity)}</span>'
            # render_report has already confirmed every finding's rule_id is
            # in the pack, so this lookup cannot miss.
            rule = rule_index[finding.rule_id]
            items.append(
                f'<div class="finding sev-{_esc(severity)}">'
                f'<div class="finding-head">{badge} <strong>{_esc(finding.title)}</strong> '
                f'<span class="finding-rule">({_esc(finding.rule_id)})</span></div>'
                f'<div class="finding-location">{_esc(finding.location)}</div>'
                f'<div class="finding-body">{_markdownish(finding.body_md)}</div>'
                f'<div class="finding-reference">{_esc(_reference_line(rule))}</div>'
                "</div>"
            )
        blocks.append(f"<h3>{_esc(title)}</h3>{''.join(items)}")
    return "".join(blocks) or "<p>No domains selected.</p>"


def _issues_section(
    selected: dict[str, DomainResult], issue_urls: dict[str, str] | None
) -> str:
    all_findings = [f for result in selected.values() for f in result.findings]
    if not all_findings:
        return "<p>No findings, so nothing to file as an issue.</p>"

    if issue_urls is not None:
        rows = []
        for finding in all_findings:
            url = issue_urls.get(finding.rule_id)
            if url:
                _require_href_scheme(
                    url, ("http", "https"), f"issue url for rule id '{finding.rule_id}'"
                )
                rows.append(f'<li><a href="{_esc(url)}">{_esc(finding.issue_title)}</a></li>')
            else:
                rows.append(f"<li>{_esc(finding.issue_title)} (no issue filed for this finding)</li>")
        return f"<ul>{''.join(rows)}</ul>"

    blocks = []
    for index, finding in enumerate(all_findings):
        combined_text = f"{finding.issue_title}\n\n{finding.issue_body}"
        textarea_id = f"issue-text-{index}"
        blocks.append(
            f'<div class="issue-block"><p><strong>{_esc(finding.issue_title)}</strong></p>'
            f'<textarea id="{textarea_id}" readonly rows="6">{_esc(combined_text)}</textarea>'
            f"<button type=\"button\" onclick=\"copyIssueText('{textarea_id}', this)\">"
            "Copy issue text</button></div>"
        )
    return "".join(blocks)


def _feedback_section(run_state: RunState, feedback_issue_url: str | None) -> str:
    text = run_state.config.feedback_text
    if text:
        text_html = f'<div class="feedback-text">{_markdownish(text)}</div>'
    else:
        text_html = "<p>No feedback text supplied for this run.</p>"

    if feedback_issue_url:
        _require_href_scheme(feedback_issue_url, ("http", "https"), "feedback issue url")
        return (
            f"{text_html}"
            f'<p>This feedback was filed as <a href="{_esc(feedback_issue_url)}">an issue</a> '
            "on the tool author's repository.</p>"
        )

    body = build_feedback_body(
        run_state.config.feedback_text,
        run_state.meta,
        run_state.config.telemetry_consent,
        run_state.domain_results,
    )
    subject = feedback_subject(run_state.meta)
    mailto_url = build_mailto_url(FEEDBACK_EMAIL, subject, body)
    _require_href_scheme(mailto_url, ("mailto",), "feedback mailto url")

    return (
        f"{text_html}"
        f'<p><a class="feedback-mailto" href="{_esc(mailto_url)}">Send feedback to the '
        "developer</a></p>"
        '<p class="muted">If that does not open a mail client, copy the text below into an '
        "email instead:</p>"
        '<textarea id="feedback-body-text" readonly rows="10">'
        f"{_esc(body)}</textarea>"
        "<button type=\"button\" onclick=\"copyIssueText('feedback-body-text', this)\">"
        "Copy feedback text</button>"
    )


def render_report(
    run_state: RunState,
    pack: RulesPack,
    issue_urls: dict[str, str] | None = None,
    feedback_issue_url: str | None = None,
) -> str:
    """Render a complete, self-contained HTML report.

    Raises ReportError if a selected domain has no matching DomainResult, if
    a completed DomainResult fails :func:`validate_completeness`, or if a
    finding references a rule id that is not in the pack.
    """
    domain_titles: dict[str, str] = {d.id: d.title for d in pack.domains}
    rule_index: dict[str, Rule] = {rule.id: rule for domain in pack.domains for rule in domain.rules}

    selected: dict[str, DomainResult] = {}
    for domain_id in run_state.config.selected_domain_ids:
        if domain_id not in domain_titles:
            raise ReportError(
                f"selected domain '{domain_id}' is not present in the rules pack at {pack.root}"
            )
        result = run_state.domain_results.get(domain_id)
        if result is None:
            raise ReportError(f"selected domain '{domain_id}' has no DomainResult for this run")
        selected[domain_id] = result

    for domain_id, result in selected.items():
        if result.status == "completed":
            domain = pack.get_domain(domain_id)
            assert domain is not None  # already checked above
            try:
                validate_completeness(domain, result)
            except IncompleteResultError as exc:
                raise ReportError(str(exc)) from exc
        for finding in result.findings:
            if finding.rule_id not in rule_index:
                raise ReportError(
                    f"finding in domain '{domain_id}' references rule id "
                    f"'{finding.rule_id}', which is not in the rules pack"
                )
            if not rule_index[finding.rule_id].source:
                raise ReportError(
                    f"finding in domain '{domain_id}' references rule "
                    f"{finding.rule_id}, which has no cited source in the rules pack. "
                    "A finding is a published claim; this tool does not publish claims "
                    "without evidence. Fix the rule's Source: footer or drop the finding."
                )

    all_findings = [
        (domain_id, finding)
        for domain_id, result in selected.items()
        for finding in result.findings
    ]

    performance_summary = (
        f'<div class="perf-block"><h3>Coverage</h3>{_coverage_summary(selected, domain_titles)}</div>'
        f'<div class="perf-block"><h3>Findings rollup</h3>'
        f"{_findings_rollup(all_findings, domain_titles)}</div>"
        f'<div class="perf-block prominent">{_could_not_evaluate_list(selected, rule_index)}</div>'
        f'<div class="perf-block"><h3>Self-assessment by domain</h3>'
        f"{_self_assessment_list(selected, domain_titles)}</div>"
        f'<div class="perf-block"><h3>Environment</h3>{_environment_info(run_state)}</div>'
    )

    template = string.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        page_title=f"Engineering practice audit report: {_esc(run_state.meta.repo_name)}",
        meta_block=_render_meta_block(run_state),
        performance_summary=performance_summary,
        findings_section=_findings_section(selected, domain_titles, rule_index),
        issues_section=_issues_section(selected, issue_urls),
        feedback_section=_feedback_section(run_state, feedback_issue_url),
        tool_version=_esc(run_state.meta.tool_version),
        inline_script=_INLINE_SCRIPT,
    )


def write_report(
    run_state: RunState,
    pack: RulesPack,
    out_path: str | Path,
    issue_urls: dict[str, str] | None = None,
    feedback_issue_url: str | None = None,
) -> Path:
    """Render the report and write it to out_path, returning the Path written."""
    rendered = render_report(run_state, pack, issue_urls, feedback_issue_url)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path
