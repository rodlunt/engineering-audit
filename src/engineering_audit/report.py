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
import json
import re
import string
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    build_feedback_sections,
    build_issue_trailing_line,
    feedback_subject,
    rules_pack_label,
)
from engineering_audit.rules import citation, Rule, RulesPack
from engineering_audit.schema import (
    ConsultedSource,
    DomainResult,
    Finding,
    IncompleteResultError,
    RunMeta,
    RunState,
    UnknownRuleIdError,
    Verdict,
    validate_completeness,
    validate_consulted_sources,
)

__all__ = ["ReportError", "render_report", "write_report"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

# The report page's client-side JS lives in its own file, not a Python
# string constant, so it gets syntax highlighting and JS tooling like every
# other .js file. It is read once at import time and substituted verbatim
# into the rendered page's <script> block (see render_report), the same way
# _TEMPLATE_PATH's HTML is read and substituted into.
_SCRIPT_PATH = Path(__file__).parent / "static" / "report.js"
_INLINE_SCRIPT = _SCRIPT_PATH.read_text(encoding="utf-8").strip()

# A repo field is only ever prefilled from run metadata when it already looks
# like a plausible 'owner/name' GitHub slug; anything else is left blank
# rather than risk pre-populating the GitHub-filing form with a string that
# was never meant to be a repository identifier.
_REPO_SLUG_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

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


def _short_commit(value: str | None) -> str:
    """Shorten a full git SHA for the footer's one-line summary; the meta
    block still shows the stored value in full.

    None (could-not-determine) renders as "unknown", never a fabricated
    value. A '-dirty' suffix, if present, is preserved on the shortened
    form so a dirty build is still visibly dirty in the short display.
    Anything that is not a bare 40-hex-character SHA (with or without that
    suffix) is left unchanged rather than guessed at.
    """
    if value is None:
        return "unknown"
    sha, suffix = value, ""
    if value.endswith("-dirty"):
        sha, suffix = value[: -len("-dirty")], "-dirty"
    if len(sha) == 40 and all(c in string.hexdigits for c in sha):
        return f"{sha[:12]}{suffix}"
    return value


def _json_script(data: object) -> str:
    """Serialise data for embedding inside an inline
    ``<script type="application/json">`` block.

    A JSON string value can legitimately contain the literal text
    "</script>" (an issue body built from agent-authored text, a
    self-assessment's limits note, an environment value, and so on). The
    HTML parser does not know or care that it is inside a JSON string when
    scanning a <script> element's raw text for that closing tag, so any
    unescaped occurrence would terminate the block early and dump the rest
    of the payload as literal HTML. "/" is a legal JSON string escape, so
    replacing every "</" with the equivalent "<\\/" after serialising is a
    safe, blanket fix: those two characters can only appear together inside
    a quoted string in ``json.dumps`` output, never as JSON structural
    syntax.
    """
    return json.dumps(data).replace("</", "<\\/")


def _require_href_scheme(url: str, allowed: tuple[str, ...], context: str) -> None:
    """Raise ReportError unless url's scheme is one of allowed.

    Used for issue links and the filed-feedback-issue link: both carry a
    URL a filing integration produced from real ``gh`` output, and a
    non-http(s) scheme there is a bug upstream, not a cosmetic issue.
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


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp as recorded on RunMeta, or None if value
    is None or is not a timestamp this tool would have written itself.

    Every string reaching here has already passed RunMeta's own
    _valid_iso_timestamp validator at parse time, so a second failure here
    would mean a bug in that validator, not bad input; returning None rather
    than raising keeps this a display-time concern; a report must still
    render over a corrupt or foreign-written run-state file.
    """
    if value is None:
        return None
    normalised = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalised)
    except ValueError:
        return None


def _format_duration(total_seconds: float) -> str:
    total_seconds = int(round(abs(total_seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return "".join(parts)


# A duration disagreement is flagged, never resolved: see the module
# docstring on the pair below for why neither figure is corrected in place.
#
# The threshold is the larger of a fixed floor and a share of the longer
# duration, so both ends of a run get an honest tolerance:
#   - On a short run, the proportional share alone would flag any pair of
#     clocks that do not agree to the second, which is tighter than two
#     independently-read clocks (the assistant's notion of "now" and the
#     server's) should ever be expected to agree even when both are honest.
#     The floor absorbs that.
#   - On a long run, a fixed floor alone would flag routine minute-scale
#     variance (tool round trips, a slow domain sweep) on every run long
#     enough to accumulate it. The proportional share scales with the run
#     instead.
# Chosen so the bug that motivated this fix, started == finished on a run
# that took minutes, is always caught: the gap between a zero-second
# assistant-reported duration and any real server-measured duration IS the
# server duration, which clears the 60-second floor on any audit worth
# auditing.
_DURATION_DIVERGENCE_FLOOR_SECONDS = 60.0
_DURATION_DIVERGENCE_PROPORTION = 0.15


def _duration_text(meta: RunMeta) -> str:
    """The "Duration" meta row's value: the assistant-reported span, the
    server-measured span, or both with a warning when they disagree by more
    than expected.

    Never overwrites the assistant's figure with the server's, or vice
    versa (see issue #102): a resumed run legitimately spans a wall-clock
    gap that is not audit work, so the server's elapsed time is not
    automatically the truer number either. The only failure mode this
    guards against is a duration presented as fact when it was never
    checked against anything; once both are shown, or the disagreement is
    named, the reader can judge which to trust.
    """
    assistant_start = _parse_iso(meta.started)
    assistant_end = _parse_iso(meta.finished)
    server_start = _parse_iso(meta.server_started)
    server_end = _parse_iso(meta.server_finished)

    assistant_duration = (
        (assistant_end - assistant_start).total_seconds()
        if assistant_start is not None and assistant_end is not None
        else None
    )
    server_duration = (
        (server_end - server_start).total_seconds()
        if server_start is not None and server_end is not None
        else None
    )

    if assistant_duration is None and server_duration is None:
        return "not available"
    if server_duration is None:
        # This run (or the resume that continued it) predates server-side
        # duration measurement, so there is nothing to check the
        # assistant's figure against. Unmeasured, not confirmed, and the
        # row has to say so rather than rendering the lone figure as if it
        # had been.
        assert assistant_duration is not None  # the both-None case returned above
        return (
            f"{_format_duration(assistant_duration)} as reported by the assistant; not "
            "measured by the server, so this could not be checked"
        )
    if assistant_duration is None:
        return f"{_format_duration(server_duration)} (server-measured; run still in progress or unreported)"

    threshold = max(
        _DURATION_DIVERGENCE_FLOOR_SECONDS,
        _DURATION_DIVERGENCE_PROPORTION * max(assistant_duration, server_duration),
    )
    if abs(assistant_duration - server_duration) > threshold:
        return (
            f"{_format_duration(assistant_duration)} as reported by the assistant, but the "
            f"server measured {_format_duration(server_duration)}. These disagree by more "
            "than expected: treat the reported duration with caution."
        )
    return f"{_format_duration(assistant_duration)} (server-measured: {_format_duration(server_duration)})"


def _render_meta_block(run_state: RunState) -> str:
    meta = run_state.meta
    rows = [
        ("Repository", meta.repo_name),
        ("Commit", meta.repo_commit),
        ("Rules pack", rules_pack_label(meta)),
        ("Rules commit", meta.rules_pack_commit or "unknown"),
        ("Assistant", meta.assistant),
        ("Model", meta.model),
        # Only rendered for a resumed run picked up by a different assistant or
        # model. Naming just the current pair would credit it with findings an
        # earlier one recorded, which is the defect this row exists to close
        # (#93): a provenance header that is confidently wrong is worse than one
        # that is absent, because nothing prompts the reader to doubt it.
        *(
            [("Earlier contributors", ", ".join(meta.earlier_contributors))]
            if meta.earlier_contributors
            else []
        ),
        ("Tool version", meta.tool_version),
        ("Tool commit", meta.tool_commit or "unknown"),
        ("Tool update", meta.update_check or "not checked"),
        ("Rules pack update", meta.pack_update_check or "not checked"),
        ("Started", meta.started),
        ("Finished", meta.finished or "in progress"),
        # The assistant-supplied Started/Finished rows above are asserted,
        # never measured (issue #102): the server has no clock of its own
        # until this row, which checks them against server_started/
        # server_finished rather than silently trusting either.
        ("Duration", _duration_text(meta)),
    ]
    rows_html = "".join(
        f'<div class="meta-label">{_esc(label)}</div><div class="meta-value">{_esc(value)}</div>'
        for label, value in rows
    )
    return f'<div class="meta-grid">{rows_html}</div>'


def _coverage_summary(selected: dict[str, DomainResult], domain_titles: dict[str, str]) -> str:
    """Render the per-domain coverage list.

    This used to also sum inspected/skipped file counts across every
    selected domain and publish two "Total files..." figures. Each domain
    audits the same repository from its own angle, so a file that sixteen
    domains each independently declined to open was counted as sixteen
    separate skips: the totals inflated by roughly the domain count and had
    no honest reading (a 344-file repository rendered "5320 skipped", see
    issue #87). The per-domain list below is already correct and
    unambiguous on its own, so the totals are dropped rather than fixed:
    nothing a reader needs is lost.
    """
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.coverage is not None:
            note = f" ({_esc(result.coverage.note)})" if result.coverage.note else ""
            rows.append(
                f"<li>{_esc(title)}: {result.coverage.files_inspected} file(s) inspected, "
                f"{result.coverage.files_skipped} skipped{note}</li>"
            )
        else:
            rows.append(f"<li>{_esc(title)}: no coverage reported</li>")
    return f"<ul>{''.join(rows)}</ul>"


def _findings_rollup(
    all_findings: list[tuple[str, Finding]],
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
) -> str:
    severity_counts: Counter[str] = Counter(f.severity.value for _, f in all_findings)
    # Keyed by domain id, not title: two domains with identical titles (from
    # different rules-pack files) must not merge into one rollup row.
    domain_counts: Counter[str] = Counter(domain_id for domain_id, _ in all_findings)
    total = len(all_findings)

    sev_items = "".join(
        f"<li>{_esc(sev)}: {severity_counts.get(sev, 0)}</li>" for sev in _SEVERITY_ORDER
    )
    # Built from every selected domain, defaulting to zero, the same way the
    # by-severity breakdown above always shows all four severities including
    # zero counts: a domain that was audited and came back clean must still
    # appear here, not be indistinguishable from one that was never run.
    domain_items = (
        "".join(
            f"<li>{_esc(domain_id)}: {_esc(domain_titles[domain_id])}: "
            f"{domain_counts.get(domain_id, 0)}</li>"
            for domain_id in selected
        )
        or "<li>No domains selected.</li>"
    )
    return (
        f"<p>Total findings: <strong>{total}</strong></p>"
        f"<h3>By severity</h3><ul>{sev_items}</ul>"
        f"<h3>By domain</h3><ul>{domain_items}</ul>"
    )


def _could_not_evaluate_list(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    domain_titles: dict[str, str],
) -> str:
    # Grouped by the verdict's own reason text, not rendered one row per
    # rule: a real 16-domain run produced 122 could-not-evaluate rows
    # carrying only 18 distinct reasons, eight of which accounted for 105
    # rows of near-identical boilerplate ("no X in this repository"). That
    # buried the handful of genuinely rule-specific reasons and made a
    # tool-performance section read like 122 more defects (see issue #88).
    reason_to_rule_ids: dict[str, list[str]] = {}
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
            reason_to_rule_ids.setdefault(rv.note or "", []).append(rv.rule_id)

    total = sum(len(rule_ids) for rule_ids in reason_to_rule_ids.values())

    # A could-not-run domain has no rule_verdicts by design (DomainResult's
    # own consistency check enforces this), so it satisfies "no rule left
    # could-not-evaluate" above by construction, even though not a single
    # rule in it was ever evaluated. That must never be reported as full
    # coverage: this box's whole purpose is to flag evaluation gaps, and a
    # domain that never ran at all is the largest possible gap.
    not_run_domain_ids = [
        domain_id for domain_id, result in selected.items() if result.status == "could-not-run"
    ]

    if not reason_to_rule_ids and not not_run_domain_ids:
        return (
            '<h3>Could not evaluate</h3>'
            '<p class="ok">Every selected rule reached a verdict of pass, finding or '
            "not applicable. Nothing was left could-not-evaluate.</p>"
        )

    parts = [f"<h3>Could not evaluate ({total})</h3>"]
    if reason_to_rule_ids:
        parts.append(
            "<p>These are rules the audit could not reach a verdict on, usually because "
            "the evidence lives outside the repository. They are not findings.</p>"
        )
        # Sorted by descending rule count, reason text as the tie-breaker for
        # a stable order: the reasons that account for the most rows (almost
        # always boilerplate) collapse to the top, leaving the rare,
        # genuinely rule-specific reasons visible near the bottom rather than
        # buried inside a hundred near-identical rows.
        ordered_reasons = sorted(
            reason_to_rule_ids.items(), key=lambda item: (-len(item[1]), item[0])
        )
        items = "".join(
            f"<li><strong>{_esc(reason)}</strong><br>"
            f"{_esc(', '.join(sorted(rule_ids)))} "
            f"({len(rule_ids)} rule{'' if len(rule_ids) == 1 else 's'})</li>"
            for reason, rule_ids in ordered_reasons
        )
        parts.append(f"<ul>{items}</ul>")
    else:
        parts.append(
            "<p>No individual rule was left could-not-evaluate, but see below: "
            "an entire selected domain did not run at all.</p>"
        )
    if not_run_domain_ids:
        names = ", ".join(
            f"{_esc(domain_titles[domain_id])} ({_esc(domain_id)})"
            for domain_id in not_run_domain_ids
        )
        parts.append(
            f"<p><strong>{len(not_run_domain_ids)} selected domain(s) did not run at all</strong> "
            f"and had no rules evaluated, which is not the same as a clean result: {names}.</p>"
        )
    return "".join(parts)


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


def _consulted_source_link(source: ConsultedSource) -> str:
    """A consulted source's title as a link, when its url is genuinely
    http(s), or the title and raw url as plain escaped text otherwise.

    Unlike the filed-issue and feedback-issue links elsewhere in this file,
    a consulted source's url is self-reported by the driving agent, not
    produced by this tool's own gh integration: it is display only, and a
    scheme this page will not turn into a clickable link must degrade to
    text rather than raise ReportError and take down the whole report over
    one bad citation.
    """
    scheme = urlparse(source.url).scheme.lower()
    if scheme in ("http", "https"):
        return f'<a href="{_esc(source.url)}">{_esc(source.title)}</a>'
    return _esc(f"{source.title} ({source.url})")


def _consulted_sources_section(
    selected: dict[str, DomainResult], rule_index: dict[str, Rule]
) -> str:
    """The 'Sources consulted this run' block: every ConsultedSource across
    the selected domains, grouped by rule id.

    Always rendered, even when nothing was recorded: an empty run must say
    "none recorded" rather than the section vanishing, since a vanished
    section and a genuinely clean run are otherwise indistinguishable to
    whoever is reading the report (see issue #54 for the same reasoning
    applied to could-not-evaluate).
    """
    by_rule: dict[str, list[ConsultedSource]] = {}
    for result in selected.values():
        for source in result.consulted_sources:
            by_rule.setdefault(source.rule_id, []).append(source)

    if not by_rule:
        return '<p class="muted">none recorded</p>'

    blocks = []
    for rule_id in sorted(by_rule):
        # render_report has already confirmed every consulted source's
        # rule_id is one of its own domain's rules, so this lookup cannot
        # miss.
        rule = rule_index[rule_id]
        items = "".join(
            f"<li>{_consulted_source_link(source)}: {_esc(source.why)} "
            f'<span class="muted">(accessed {_esc(source.accessed)})</span></li>'
            for source in by_rule[rule_id]
        )
        blocks.append(f"<h4>{_esc(rule_id)} ({_esc(rule.title)})</h4><ul>{items}</ul>")
    return "".join(blocks)


def _environment_info(run_state: RunState) -> str:
    environment = run_state.meta.environment
    if not environment:
        return "<p>No environment information reported for this run.</p>"
    rows = "".join(
        f"<li><strong>{_esc(key)}:</strong> {_esc(value)}</li>" for key, value in environment.items()
    )
    return f"<ul>{rows}</ul>"


# Independent of citation()'s own capping, and deliberately larger than
# rules._MAX_CITATION_LENGTH: any citation, from a v1 or a v2 pack, can run
# up to that ceiling plus the visible truncation marker's own length.
#
# This is a backstop for a programming error in citation() itself, not a
# second policy. citation() now applies its ceiling unconditionally, on both
# the v1 and v2 branches, so a citation arriving here oversized means the
# capping did not run at all rather than that a pack broke its authoring
# contract. Turning that into a loud render failure is preferable to
# shipping an oversized reference (see the rule-footer-format-v2 contract).
_MAX_REFERENCE_LENGTH = 900


def _reference_line(rule: Rule, pack_is_v2: bool) -> str:
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
    cited = citation(rule.source, pack_is_v2=pack_is_v2)
    if len(cited) > _MAX_REFERENCE_LENGTH:
        raise ReportError(
            f"finding references rule {rule.id}, whose citation is {len(cited)} "
            f"characters long, over the {_MAX_REFERENCE_LENGTH}-character reference "
            "ceiling. A finding's reference line must stay publishable; fix the rule's "
            "Source: (or Verification:) footer rather than shipping an oversized reference."
        )
    return f"Reference: {rule.id}: {cited}"


def _findings_section(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    rule_index: dict[str, Rule],
    pack_is_v2: bool,
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
                f'<div class="finding-reference">{_esc(_reference_line(rule, pack_is_v2))}</div>'
                "</div>"
            )
        blocks.append(f"<h3>{_esc(title)}</h3>{''.join(items)}")
    return "".join(blocks) or "<p>No domains selected.</p>"


def _issue_button_row() -> str:
    return (
        '<div class="issue-actions">'
        '<button type="button" onclick="revealGithubFileForm()">'
        "Add selected issues to GitHub (requires GitHub PAT)</button> "
        '<button type="button" onclick="copySelectedIssues(this)">'
        "Copy selected issues (for pasting into an LLM or editor)</button>"
        "</div>"
    )


def _github_file_form(repo_prefill: str) -> str:
    return (
        '<div id="github-file-form" class="github-file-form" style="display:none">'
        '<p class="muted">Files each selected issue directly from your browser to '
        "api.github.com over HTTPS, using the REST API. The token is used only in memory on "
        "this page: it is never stored (no localStorage, sessionStorage or cookies). A "
        "fine-grained personal access token with Issues read and write access on the one "
        "target repository is enough.</p>"
        '<label>Repository (owner/name)<br>'
        f'<input type="text" id="gh-repo" value="{_esc(repo_prefill)}" placeholder="owner/name">'
        "</label><br>"
        '<label>Personal access token<br>'
        '<input type="password" id="gh-pat" autocomplete="off">'
        "</label><br>"
        '<button type="button" id="gh-file-button" onclick="fileSelectedIssues()">'
        "File 0 selected issues</button> "
        '<button type="button" id="gh-stop-button" onclick="stopFilingIssues()" '
        'style="display:none">Stop</button>'
        '<p id="github-file-summary" class="muted"></p>'
        "</div>"
    )


def _issues_section(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    issue_urls: dict[str, str] | None,
    repo_prefill: str,
) -> str:
    all_findings = [f for result in selected.values() for f in result.findings]
    if not all_findings:
        return "<p>No findings, so nothing to file as an issue.</p>"

    issue_urls = issue_urls or {}
    issues_data: list[dict[str, str]] = []
    blocks = []
    # Keyed per finding ("<rule id>#<n>"), matching server.py's _run_issues:
    # the same per-rule counter, incremented before use, over each domain's
    # findings in list order. Iterating findings across domains here (rather
    # than within one domain, as _run_issues effectively does) cannot skew
    # this counter out of step with server.py's: a rule id belongs to exactly
    # one domain (the #25 validator rejects a duplicate rule id across rule
    # files), so every increment of a given rule's count happens while
    # walking that one domain's own finding list, in the same order either
    # way. That coupling to server.py's enumeration is real and cannot be
    # expressed in the type system, only guarded by this comment and the
    # tests that pin it.
    seen: Counter[str] = Counter()
    for index, finding in enumerate(all_findings):
        # render_report has already confirmed every finding's rule_id is in
        # the pack and carries a cited source, so this lookup and the
        # trailing-line build below cannot fail.
        rule = rule_index[finding.rule_id]
        trailing_line = build_issue_trailing_line(finding, rule)
        body_with_trailing = f"{finding.issue_body}\n\n{trailing_line}"
        full_text = f"{finding.issue_title}\n\n{body_with_trailing}"

        issues_data.append(
            {"rule_id": finding.rule_id, "title": finding.issue_title, "body": body_with_trailing}
        )

        seen[finding.rule_id] += 1
        finding_key = f"{finding.rule_id}#{seen[finding.rule_id]}"
        filed_url = issue_urls.get(finding_key)
        textarea_id = f"issue-text-{index}"
        status_id = f"issue-status-{index}"

        if filed_url:
            _require_href_scheme(
                filed_url, ("http", "https"), f"issue url for rule id '{finding.rule_id}'"
            )
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}" disabled> '
                f'<a href="{_esc(filed_url)}">already filed</a>'
            )
        else:
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}" checked '
                'onchange="updateGithubFileButtonLabel()">'
            )

        blocks.append(
            '<div class="issue-block">'
            f'<label class="issue-select">{checkbox_html}</label>'
            f'<p><strong>{_esc(finding.issue_title)}</strong></p>'
            f'<textarea id="{textarea_id}" readonly rows="6">{_esc(full_text)}</textarea>'
            f"<button type=\"button\" onclick=\"copyIssueText('{textarea_id}', this)\">"
            "Copy issue text</button> "
            f'<span class="issue-status" id="{status_id}"></span>'
            "</div>"
        )

    button_row = _issue_button_row()
    data_script = (
        '<script type="application/json" id="issues-data">'
        f"{_json_script({'issues': issues_data})}"
        "</script>"
    )

    return (
        f"{button_row}"
        f"{_github_file_form(repo_prefill)}"
        f"{''.join(blocks)}"
        f"{button_row}"
        f"{data_script}"
    )


def _consent_row(input_id: str, label: str, checked: bool) -> str:
    checked_attr = " checked" if checked else ""
    return (
        f'<label class="consent-row"><input type="checkbox" id="{input_id}"{checked_attr}> '
        f"{_esc(label)}</label>"
    )


def _feedback_section(run_state: RunState, feedback_issue_url: str | None) -> str:
    config = run_state.config
    consent = config.telemetry_consent
    text = config.feedback_text or ""

    filed_html = ""
    if feedback_issue_url:
        _require_href_scheme(feedback_issue_url, ("http", "https"), "feedback issue url")
        filed_html = (
            f'<p>Feedback for this run was already filed as <a href="{_esc(feedback_issue_url)}">'
            "an issue</a> on the tool author's repository. Further feedback can still be sent "
            "below.</p>"
        )

    sections = build_feedback_sections(run_state.meta, run_state.domain_results)
    feedback_data = {
        "email": FEEDBACK_EMAIL,
        "subject": feedback_subject(run_state.meta),
        "run_metadata": sections["run_metadata"],
        "coverage": sections["coverage"],
        "rollup": sections["rollup"],
        "self_assessment": sections["self_assessment"],
        "environment": sections["environment"],
        "consulted_sources": sections["consulted_sources"],
    }

    # Same wording as the configuration page's consent section, so a user
    # who saw one recognises the other.
    consent_rows = (
        _consent_row(
            "consent-coverage",
            "Coverage statistics (files inspected, files skipped)",
            consent.coverage,
        )
        + _consent_row(
            "consent-rollup",
            "Findings rollup (counts by severity and domain, not the finding text)",
            consent.rollup,
        )
        + _consent_row(
            "consent-self-assessment",
            "Self assessment (confidence and limits per domain)",
            consent.self_assessment,
        )
        + _consent_row(
            "consent-environment",
            "Environment information (assistant, model, tool version)",
            consent.environment,
        )
        + _consent_row(
            "consent-consulted-sources",
            "Send fetched references to the maintainer (rule id, URL and why, for each "
            "source consulted outside the rules pack). Off by default: URLs fetched while "
            "auditing a private repository can hint at what that repository is about.",
            consent.consulted_sources,
        )
        + '<label class="consent-row locked"><input type="checkbox" checked disabled> '
        "Run metadata (always included when sending feedback)</label>"
    )

    return (
        f"{filed_html}"
        '<div class="feedback-form">'
        '<label for="feedback-textarea">Freeform feedback</label>'
        f'<textarea id="feedback-textarea" rows="6">{_esc(text)}</textarea>'
        '<div class="consent-rows">'
        f"{consent_rows}"
        "</div>"
        '<div class="feedback-actions">'
        '<button type="button" onclick="emailFeedback()">Email feedback</button> '
        '<button type="button" onclick="copyFeedback(this)">Copy feedback</button>'
        "</div>"
        "</div>"
        '<script type="application/json" id="feedback-sections-data">'
        f"{_json_script(feedback_data)}"
        "</script>"
    )


def _render_footer(run_state: RunState) -> str:
    meta = run_state.meta
    finished = meta.finished or "in progress"
    tool_commit = _short_commit(meta.tool_commit)
    rules_pack_commit = _short_commit(meta.rules_pack_commit)
    return (
        "<p>"
        f"Generated by engineering-audit v{_esc(meta.tool_version)} (commit {_esc(tool_commit)}) "
        f"against rules pack {_esc(rules_pack_label(meta))} (commit {_esc(rules_pack_commit)}), "
        f"auditing {_esc(meta.repo_name)} at commit {_esc(meta.repo_commit)}, finished {_esc(finished)}."
        "</p>"
        '<p><a href="https://github.com/rodlunt">rodlunt on GitHub</a> | '
        '<a href="https://github.com/rodlunt/engineering-audit">engineering-audit on GitHub</a>'
        "</p>"
        "<p>This report was generated locally. Nothing in it leaves your machine unless you "
        "choose to send or file it.</p>"
    )


def render_report(run_state: RunState, pack: RulesPack) -> str:
    """Render a complete, self-contained HTML report.

    Filed issue urls and any feedback issue link are read from
    ``run_state.filed_issue_urls`` and ``run_state.feedback_issue_url``: the
    RunState is the single source for both, so a report rendered straight
    from a saved run-state.json (see render_cli.py) always matches one
    rendered live from the same run's in-progress tracker.

    Raises ReportError if a selected domain has no matching DomainResult, if
    a completed DomainResult fails :func:`validate_completeness`, or if a
    finding references a rule id that is not in the pack.
    """
    domain_titles: dict[str, str] = {d.id: d.title for d in pack.domains}
    rule_index: dict[str, Rule] = pack.rule_index

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
        domain = pack.get_domain(domain_id)
        assert domain is not None  # already checked above
        if result.status == "completed":
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
        if result.consulted_sources:
            # Re-checked here, not trusted from record_domain_result: a
            # report can be rendered straight from a saved run-state.json
            # (see render_cli.py) that never passed through the live server
            # at all.
            try:
                validate_consulted_sources(domain, result)
            except UnknownRuleIdError as exc:
                raise ReportError(str(exc)) from exc

    all_findings = [
        (domain_id, finding)
        for domain_id, result in selected.items()
        for finding in result.findings
    ]

    performance_summary = (
        f'<div class="perf-block"><h3>Coverage</h3>{_coverage_summary(selected, domain_titles)}</div>'
        f'<div class="perf-block"><h3>Findings rollup</h3>'
        f"{_findings_rollup(all_findings, selected, domain_titles)}</div>"
        f'<div class="perf-block prominent">'
        f"{_could_not_evaluate_list(selected, rule_index, domain_titles)}</div>"
        f'<div class="perf-block"><h3>Self-assessment by domain</h3>'
        f"{_self_assessment_list(selected, domain_titles)}</div>"
        f'<div class="perf-block"><h3>Environment</h3>{_environment_info(run_state)}</div>'
        f'<div class="perf-block"><h3>Sources consulted this run</h3>'
        f"{_consulted_sources_section(selected, rule_index)}</div>"
    )

    repo_name = run_state.meta.repo_name
    repo_prefill = repo_name if _REPO_SLUG_RE.match(repo_name) else ""

    template = string.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        page_title=f"Engineering practice audit report: {_esc(run_state.meta.repo_name)}",
        meta_block=_render_meta_block(run_state),
        performance_summary=performance_summary,
        findings_section=_findings_section(selected, domain_titles, rule_index, pack.is_v2),
        issues_section=_issues_section(
            selected, rule_index, run_state.filed_issue_urls or None, repo_prefill
        ),
        feedback_section=_feedback_section(run_state, run_state.feedback_issue_url),
        footer_block=_render_footer(run_state),
        inline_script=_INLINE_SCRIPT,
    )


def write_report(run_state: RunState, pack: RulesPack, out_path: str | Path) -> Path:
    """Render the report and write it to out_path, returning the Path written."""
    rendered = render_report(run_state, pack)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path
