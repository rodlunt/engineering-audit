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
from pathlib import Path
from urllib.parse import urlparse

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    build_feedback_sections,
    build_issue_trailing_line,
    duration_text,
    feedback_subject,
    rules_pack_label,
)
from engineering_audit.rules import citation, Rule, RulesPack
from engineering_audit.schema import (
    ConsultedSource,
    DomainResult,
    Finding,
    IncompleteResultError,
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

# The severities whose issue checkbox is ticked when the report loads. See
# _issues_section for why this is not all four.
_PRETICKED_SEVERITIES = ("critical", "high")


class ReportError(Exception):
    """Raised when a RunState cannot be rendered into a trustworthy report:
    a selected domain has no result, a completed result fails the
    every-rule-has-a-verdict check, or a finding references a rule id that
    is not in the rules pack. A report is what a human trusts as the record
    of the run; it must never render a plausible-looking page over broken or
    incomplete data."""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """ "1 finding" / "2 findings", for the few places whose whole job is to
    read like written English.

    The rest of this file uses the "rule(s)" form, which is fine inside a
    dense list. The headline block is the one paragraph a reader is most
    likely to quote, so it gets real grammar.
    """
    return singular if count == 1 else (plural or f"{singular}s")


def _join_clauses(clauses: list[str]) -> str:
    """Join clauses as English: "a", "a and b", "a, b and c"."""
    if len(clauses) <= 1:
        return "".join(clauses)
    return f"{', '.join(clauses[:-1])} and {clauses[-1]}"


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
        ("Duration", duration_text(meta)),
    ]
    rows_html = "".join(
        f'<div class="meta-label">{_esc(label)}</div><div class="meta-value">{_esc(value)}</div>'
        for label, value in rows
    )
    return f'<div class="meta-grid">{rows_html}</div>'


def _verdict_counts(result: DomainResult) -> Counter[str]:
    """One domain's rule verdicts, counted by verdict value.

    Every one of the four verdicts is present, at zero where nothing
    carried it, so a caller can index the result without deciding for
    itself what a missing key means. A could-not-run domain has no
    verdicts at all (DomainResult enforces that), so it comes back as four
    zeros, which is exactly the shape it should have: nothing was checked.
    """
    counts: Counter[str] = Counter(rv.verdict.value for rv in result.rule_verdicts)
    for verdict in Verdict:
        counts.setdefault(verdict.value, 0)
    return counts


def _run_totals(selected: dict[str, DomainResult]) -> Counter[str]:
    """The whole run's rule verdicts, counted by verdict value."""
    totals: Counter[str] = Counter()
    for result in selected.values():
        totals.update(_verdict_counts(result))
    return totals


def _headline_block(
    all_findings: list[tuple[str, Finding]],
    selected: dict[str, DomainResult],
) -> str:
    """The computed sentence that opens the report (issue #122).

    Everything here is summed from the run state, the same as every other
    number on the page. Two sentences: what needs attention first, then
    what this run did not check. The second one exists because the first
    one, read alone, invites the reader to treat the finding count as the
    whole story, and on a run that set most of its rules aside it is not.

    Every figure ships with its base (D16-R03): "30 findings" is
    meaningless without "across 244 rules verdicted in 15 of 16 domains".
    No figure is expressed as a percentage, so no share ever appears
    without the count it was taken from.

    The lead sentence is conditional, and where no severity threshold is
    met it falls back to a descriptive line rather than manufacturing
    urgency the counts do not support (D16-R10).
    """
    severity_counts: Counter[str] = Counter(f.severity.value for _, f in all_findings)
    critical = severity_counts.get("critical", 0)
    high = severity_counts.get("high", 0)
    total_findings = len(all_findings)

    totals = _run_totals(selected)
    verdicted = sum(totals.values())
    domains_with_verdicts = len(_domain_ids_with_verdicts(selected))
    domain_count = len(selected)

    base = (
        f"{verdicted} {_plural(verdicted, 'rule')} verdicted in "
        f"{domains_with_verdicts} of {domain_count} "
        f"{_plural(domain_count, 'domain')}"
    )

    if critical or high:
        urgent = _join_clauses(
            [
                f"{count} {label}"
                for count, label in ((critical, "critical"), (high, "high"))
                if count
            ]
        )
        urgent_total = critical + high
        lead = (
            f"{urgent} {_plural(urgent_total, 'finding')} "
            f"{_plural(urgent_total, 'needs', 'need')} attention first, out of "
            f"{total_findings} {_plural(total_findings, 'finding')} across {base}."
        )
    elif total_findings:
        lead = (
            f"No critical or high findings. {total_findings} "
            f"{_plural(total_findings, 'finding')} of medium or low severity "
            f"{_plural(total_findings, 'was', 'were')} recorded, across {base}."
        )
    else:
        lead = f"No findings were recorded, across {base}."

    not_applicable = totals["not-applicable"]
    could_not_evaluate = totals["could-not-evaluate"]
    not_run = [
        domain_id
        for domain_id, result in selected.items()
        if result.status == "could-not-run"
    ]

    # Each clause carries the base its count came out of, which is the whole
    # point of the sentence: "155 rules were set aside" invites the reader to
    # supply their own denominator, and "155 of 244" does not.
    caveats = []
    if not_applicable:
        caveats.append(
            f"{not_applicable} of {verdicted} rules "
            f"{_plural(not_applicable, 'was', 'were')} set aside as not applicable"
        )
    if could_not_evaluate:
        caveats.append(
            f"{could_not_evaluate} of {verdicted} rules could not be evaluated"
        )
    if not_run:
        caveats.append(
            f"{len(not_run)} of {domain_count} "
            f"{_plural(domain_count, 'domain')} did not run at all"
        )

    if caveats:
        caveat = (
            f"{_join_clauses(caveats)}, so this is not a clean bill of health: "
            "read the Tool performance summary before treating the findings above "
            "as the whole picture."
        )
    else:
        caveat = (
            "No rule was set aside as not applicable, none was left could not "
            f"evaluate, and all {domain_count} selected "
            f"{_plural(domain_count, 'domain')} ran."
        )

    return (
        '<div class="headline">'
        f'<p class="headline-lead">{_esc(lead)}</p>'
        f'<p class="headline-caveat">{_esc(caveat)}</p>'
        "</div>"
    )


def _coverage_summary(
    selected: dict[str, DomainResult], domain_titles: dict[str, str]
) -> str:
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
        f"<li>{_esc(sev)}: {severity_counts.get(sev, 0)}</li>"
        for sev in _SEVERITY_ORDER
    )
    # Built from every selected domain, defaulting to zero, the same way the
    # by-severity breakdown above always shows all four severities including
    # zero counts: a domain that was audited and came back clean must still
    # appear here, not be indistinguishable from one that was never run.
    #
    # A domain whose every rule was set aside as not applicable is marked as
    # such on its own row, because "0" is exactly what a domain that was
    # swept clean also shows and the two are not the same result (issue
    # #100). The full counts and reasons are in the Not applicable block.
    #
    # A could-not-run domain gets the same treatment for the same reason,
    # and needs it more (issue #122, point 5): it has no verdicts at all, so
    # _fully_not_applicable_domain_ids deliberately excludes it, and it used
    # to fall through to the plain branch and render as a bare "0". That is
    # the exact string a domain swept clean also renders, on a domain where
    # not one rule was ever looked at. The numeral is suppressed rather than
    # printed as zero: there is no denominator it could be a count out of.
    fully_not_applicable = set(
        _fully_not_applicable_domain_ids(_not_applicable_counts(selected))
    )
    set_aside_note = " (every rule not applicable, nothing checked)"

    def _domain_item(domain_id: str) -> str:
        label = f"{_esc(domain_id)}: {_esc(domain_titles[domain_id])}"
        if selected[domain_id].status == "could-not-run":
            return f"<li>{label}: did not run, nothing checked</li>"
        note = set_aside_note if domain_id in fully_not_applicable else ""
        return f"<li>{label}: {domain_counts.get(domain_id, 0)}{note}</li>"

    domain_items = (
        "".join(_domain_item(domain_id) for domain_id in selected)
        or "<li>No domains selected.</li>"
    )
    return (
        f"<p>Total findings: <strong>{total}</strong></p>"
        f"<h3>By severity</h3><ul>{sev_items}</ul>"
        f"<h3>By domain</h3><ul>{domain_items}</ul>"
    )


def _group_rule_ids_by_reason(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    verdict: Verdict,
    unrecorded_reason_label: str,
) -> dict[str, list[str]]:
    """Group every rule id carrying ``verdict`` by the reason recorded with it.

    Grouped by the verdict's own reason text, not one row per rule: a real
    16-domain run produced 122 could-not-evaluate rows carrying only 18
    distinct reasons, eight of which accounted for 105 rows of near-identical
    boilerplate ("no X in this repository"). That buried the handful of
    genuinely rule-specific reasons and made a tool-performance section read
    like 122 more defects (see issue #88). not-applicable is grouped the same
    way for the same reason, and shares this function rather than copying it,
    so the two sections cannot drift apart.

    ``unrecorded_reason_label`` is the group a verdict with no note falls
    into. Both verdicts require one now, so this only catches a run-state
    written before that was true of not-applicable (see issue #100 and the
    schema_version 4 gate): such a verdict is shown under a label saying no
    reason was recorded, never quietly folded in with the ones that carry a
    real reason.
    """
    reason_to_rule_ids: dict[str, list[str]] = {}
    for domain_id, result in selected.items():
        for rv in result.rule_verdicts:
            if rv.verdict != verdict:
                continue
            if rv.rule_id not in rule_index:
                # Consistent with the findings check in render_report: a
                # verdict for a rule id absent from the pack is a broken run,
                # not a cosmetic gap, and must raise rather than render a
                # placeholder label.
                raise ReportError(
                    f"domain '{domain_id}' has a rule_verdict for rule id "
                    f"'{rv.rule_id}', which is not in the rules pack"
                )
            note = rv.note.strip() if rv.note else ""
            reason_to_rule_ids.setdefault(note or unrecorded_reason_label, []).append(
                rv.rule_id
            )
    return reason_to_rule_ids


def _reason_groups_html(reason_to_rule_ids: dict[str, list[str]]) -> str:
    """Render grouped reasons as a list, largest group first.

    Sorted by descending rule count, reason text as the tie-breaker for a
    stable order: the reasons that account for the most rows (almost always
    boilerplate) collapse to the top, leaving the rare, genuinely
    rule-specific reasons visible near the bottom rather than buried inside a
    hundred near-identical rows.
    """
    ordered_reasons = sorted(
        reason_to_rule_ids.items(), key=lambda item: (-len(item[1]), item[0])
    )
    items = "".join(
        f"<li><strong>{_esc(reason)}</strong><br>"
        f"{_esc(', '.join(sorted(rule_ids)))} "
        f"({len(rule_ids)} rule{'' if len(rule_ids) == 1 else 's'})</li>"
        for reason, rule_ids in ordered_reasons
    )
    return f"<ul>{items}</ul>"


# The group label for a verdict that records no reason at all. Only a
# not-applicable verdict from a run-state written before schema_version 4 can
# reach it (see _group_rule_ids_by_reason), and it is deliberately worded as
# an absence rather than as a reason.
_NO_REASON_RECORDED = "No reason recorded for this verdict"


def _could_not_evaluate_list(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    domain_titles: dict[str, str],
) -> str:
    reason_to_rule_ids = _group_rule_ids_by_reason(
        selected, rule_index, Verdict.COULD_NOT_EVALUATE, _NO_REASON_RECORDED
    )

    total = sum(len(rule_ids) for rule_ids in reason_to_rule_ids.values())

    # A could-not-run domain has no rule_verdicts by design (DomainResult's
    # own consistency check enforces this), so it satisfies "no rule left
    # could-not-evaluate" above by construction, even though not a single
    # rule in it was ever evaluated. That must never be reported as full
    # coverage: this box's whole purpose is to flag evaluation gaps, and a
    # domain that never ran at all is the largest possible gap.
    not_run_domain_ids = [
        domain_id
        for domain_id, result in selected.items()
        if result.status == "could-not-run"
    ]

    if not reason_to_rule_ids and not not_run_domain_ids:
        return (
            "<h3>Could not evaluate</h3>"
            '<p class="ok">Every selected rule reached a verdict of pass, finding or '
            "not applicable. Nothing was left could-not-evaluate.</p>"
        )

    parts = [f"<h3>Could not evaluate ({total})</h3>"]
    if reason_to_rule_ids:
        parts.append(
            "<p>These are rules the audit could not reach a verdict on, usually because "
            "the evidence lives outside the repository. They are not findings.</p>"
        )
        parts.append(_reason_groups_html(reason_to_rule_ids))
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


def _not_applicable_counts(
    selected: dict[str, DomainResult],
) -> dict[str, tuple[int, int]]:
    """Domain id -> (rules verdicted not-applicable, rules verdicted at all).

    Both halves of the pair matter: "21 not applicable" says nothing on its
    own, while "21 of 21" says the domain was set aside in full. Computed
    once and shared by every place that needs it (the rollup, the findings
    section and the not-applicable block), so the three cannot disagree with
    each other about the same domain.
    """
    counts: dict[str, tuple[int, int]] = {}
    for domain_id, result in selected.items():
        not_applicable = sum(
            1 for rv in result.rule_verdicts if rv.verdict == Verdict.NOT_APPLICABLE
        )
        counts[domain_id] = (not_applicable, len(result.rule_verdicts))
    return counts


def _fully_not_applicable_domain_ids(counts: dict[str, tuple[int, int]]) -> list[str]:
    """The domains where every rule that got a verdict got not-applicable.

    A domain with no verdicts at all is not one of these: it did not run,
    which the could-not-evaluate block already reports, and folding the two
    together would blur two different failures into one.
    """
    return [
        domain_id
        for domain_id, (not_applicable, verdicted) in counts.items()
        if verdicted > 0 and not_applicable == verdicted
    ]


def _not_applicable_list(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    domain_titles: dict[str, str],
) -> str:
    """The 'Not applicable' block: how many rules each domain set aside, and
    the reasons given for setting them aside.

    Rendered at all because it was not, and that was the bug (issue #100): a
    16-domain run waved away 172 of 260 rules as not-applicable, nine whole
    domains of them, and the report did not contain the phrase anywhere. A
    domain nobody checked and a domain checked clean both rendered as "0
    findings", and no reader could tell them apart.
    """
    counts = _not_applicable_counts(selected)
    total = sum(not_applicable for not_applicable, _ in counts.values())

    if total == 0:
        return (
            "<h3>Not applicable</h3>"
            '<p class="ok">No rule was set aside as not applicable. Every rule that was '
            "verdicted was verdicted against this repository.</p>"
        )

    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            rows.append(
                f"<li>{_esc(domain_id)}: {_esc(title)}: did not run at all</li>"
            )
            continue
        not_applicable, verdicted = counts[domain_id]
        rows.append(
            f"<li>{_esc(domain_id)}: {_esc(title)}: {not_applicable} of {verdicted} "
            "rule(s) not applicable</li>"
        )

    parts = [
        f"<h3>Not applicable ({total})</h3>",
        "<p>These are rules the audit set aside because the thing they are about is not "
        "present in this repository. They were not checked against it, and they are not "
        "findings: a rule set aside is a claim about the repository, so each one carries "
        "the reason it was set aside.</p>",
        f"<ul>{''.join(rows)}</ul>",
    ]

    fully_not_applicable = _fully_not_applicable_domain_ids(counts)
    if fully_not_applicable:
        names = ", ".join(
            f"{_esc(domain_titles[domain_id])} ({_esc(domain_id)})"
            for domain_id in fully_not_applicable
        )
        parts.append(
            f"<p><strong>{len(fully_not_applicable)} selected domain(s) had every rule set "
            "aside as not applicable</strong>, which is not the same as a clean result: "
            f"{names}. Read the reasons below and judge whether they hold.</p>"
        )

    parts.append(
        _reason_groups_html(
            _group_rule_ids_by_reason(
                selected, rule_index, Verdict.NOT_APPLICABLE, _NO_REASON_RECORDED
            )
        )
    )
    return "".join(parts)


# The one sentence this block is allowed to claim, and the limit that comes
# with it. Fetching rule text is not reading it: an agent can call get_domain
# for every domain, discard every byte and bulk-mark anyway, and this block
# shows a clean bill of health. Said on every rendering, including the clean
# one, because a clean result is exactly where a reader is most likely to
# upgrade "fetched" into "audited" on the tool's behalf (issue #110).
_RULES_FETCHED_LIMIT = (
    "<p>This says the rule text was fetched from the server, and nothing more. It is not "
    "evidence that the rules were read, or applied, or that the verdicts below follow from "
    "them: a run that fetched every domain and then guessed would look the same here. What "
    "it can show is the opposite case, where the rules were never even asked for.</p>"
)


def _domain_ids_with_verdicts(selected: dict[str, DomainResult]) -> list[str]:
    """The selected domains that recorded at least one rule verdict.

    The fetch check is about verdicts reached without the rules, so a domain
    that reached none is outside it. A could-not-run domain carries no verdicts
    by construction (see DomainResult) and is already reported by the could not
    evaluate block; naming it here as well would read as a second, separate
    fault.
    """
    return [domain_id for domain_id, result in selected.items() if result.rule_verdicts]


def _rules_fetched_list(
    run_state: RunState,
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
) -> str:
    """The 'Rules fetched' block: which domains recorded verdicts without their
    rule text ever being requested from the server.

    ``get_domain`` is the only thing in this package that returns rule body
    text, so the set of domains it served is the one observable event that
    could have supplied the rules a verdict rests on. Rendered next to the not
    applicable block because it is the same class of problem: another way a
    domain can look audited without being audited (issues #100 and #110).

    Three outcomes per domain, never two. ``rules_fetched_domain_ids`` is None
    for a run whose state predates the record, and every domain in it is
    unknown; a domain in ``rules_fetch_unknown_domain_ids`` was carried into a
    resumed run from such a record. Unknown is rendered as unknown, never
    folded into either answer.
    """
    considered = _domain_ids_with_verdicts(selected)
    if not considered:
        return (
            "<h3>Rules fetched</h3>"
            "<p>No selected domain recorded a rule verdict, so there is nothing here to "
            "check the fetched rule text against. See the could not evaluate block above "
            "for what did happen.</p>"
        )

    recorded = run_state.rules_fetched_domain_ids
    if recorded is None:
        # Nothing in this run-state distinguishes a domain that was fetched
        # from one that was not, because the build that wrote it recorded
        # neither. Saying so is the whole answer.
        return (
            "<h3>Rules fetched: not recorded</h3>"
            "<p>This run's saved state was written before the tool recorded which domains "
            "had their rule text fetched, so for every domain here the answer is unknown. "
            "Unknown is not a pass and not a failure: it means this report cannot tell you "
            "whether the rules behind these verdicts were ever requested.</p>"
        )

    fetched_ids = set(recorded)
    unknown_ids = set(run_state.rules_fetch_unknown_domain_ids) - fetched_ids
    unknown = [d for d in considered if d in unknown_ids]
    missing = [d for d in considered if d not in fetched_ids and d not in unknown_ids]

    def _names(domain_ids: list[str]) -> str:
        return ", ".join(
            f"{_esc(domain_titles[domain_id])} ({_esc(domain_id)})"
            for domain_id in domain_ids
        )

    if not missing and not unknown:
        return (
            "<h3>Rules fetched</h3>"
            f'<p class="ok">All {len(considered)} domain(s) that recorded verdicts had their '
            "rule text fetched from this server first.</p>" + _RULES_FETCHED_LIMIT
        )

    heading = (
        f"Rules fetched ({len(missing)} of {len(considered)} domain(s) never fetched)"
        if missing
        else f"Rules fetched (not recorded for {len(unknown)} domain(s))"
    )
    parts = [f"<h3>{heading}</h3>"]
    if missing:
        parts.append(
            f"<p><strong>{len(missing)} domain(s) recorded rule verdicts without their rule "
            f"text ever being fetched this run</strong>: {_names(missing)}.</p>"
        )
        parts.append(
            "<p>The rules for a domain are served by one call, and no such call was recorded "
            "for these. Unless their rules were read some other way, the verdicts in them "
            "were reached without the rules they are supposed to be verdicts on. Treat them "
            "as unsupported until they are redone.</p>"
        )
    if unknown:
        parts.append(
            f"<p>{len(unknown)} domain(s) were carried into this run from a saved record "
            "written before the tool recorded any of this, so whether their rules were "
            f"fetched is not recorded, which is neither answer: {_names(unknown)}.</p>"
        )
    parts.append(_RULES_FETCHED_LIMIT)
    return "".join(parts)


def _self_assessment_list(
    selected: dict[str, DomainResult], domain_titles: dict[str, str]
) -> str:
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            rows.append(f"<li>{_esc(title)}: could not run, {_esc(result.reason)}</li>")
        elif result.self_assessment is not None:
            sa = result.self_assessment
            limits = f" Limits: {_esc(sa.limits)}." if sa.limits else ""
            rows.append(
                f"<li>{_esc(title)}: confidence {_esc(sa.confidence)}.{limits}</li>"
            )
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
        f"<li><strong>{_esc(key)}:</strong> {_esc(value)}</li>"
        for key, value in environment.items()
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


def _finding_card(
    domain_title: str,
    finding: Finding,
    rule_index: dict[str, Rule],
    pack_is_v2: bool,
) -> str:
    severity = finding.severity.value
    badge = f'<span class="severity-badge severity-{_esc(severity)}">{_esc(severity)}</span>'
    # render_report has already confirmed every finding's rule_id is in the
    # pack, so this lookup cannot miss.
    rule = rule_index[finding.rule_id]
    return (
        f'<div class="finding sev-{_esc(severity)}">'
        f'<div class="finding-head">{badge} <strong>{_esc(finding.title)}</strong> '
        f'<span class="finding-rule">({_esc(finding.rule_id)})</span> '
        f'<span class="finding-domain">{_esc(domain_title)}</span></div>'
        f'<div class="finding-location">{_esc(finding.location)}</div>'
        f'<div class="finding-body">{_markdownish(finding.body_md)}</div>'
        f'<div class="finding-reference">{_esc(_reference_line(rule, pack_is_v2))}</div>'
        "</div>"
    )


def _domains_without_findings(
    selected: dict[str, DomainResult], domain_titles: dict[str, str]
) -> str:
    """The closing list of every selected domain that produced no findings,
    saying for each one which kind of "none" it was.

    Three outcomes, never one. A domain swept clean, a domain whose every
    rule was set aside as not applicable, and a domain that never ran all
    produce zero findings, and reporting them with the same sentence is the
    defect issues #100 and #122 both exist to close. The zeros are printed
    rather than dropped: a domain missing from this list is a domain that
    found something, not a domain nobody looked at.
    """
    counts = _not_applicable_counts(selected)
    fully_not_applicable = set(_fully_not_applicable_domain_ids(counts))
    quiet = [
        (domain_id, result)
        for domain_id, result in selected.items()
        if not result.findings
    ]
    if not quiet:
        return ""

    rows = []
    for domain_id, result in quiet:
        label = f"{_esc(domain_id)}: {_esc(domain_titles[domain_id])}"
        if result.status == "could-not-run":
            rows.append(
                f"<li>{label}: <strong>did not run, nothing checked</strong>: "
                f"{_esc(result.reason)}</li>"
            )
            continue
        not_applicable, verdicted = counts[domain_id]
        if domain_id in fully_not_applicable:
            rows.append(
                f"<li>{label}: <strong>no findings, and nothing checked</strong>: "
                f"all {not_applicable} of {verdicted} rule(s) in this domain were set "
                "aside as not applicable. The reasons are in the Not applicable block "
                "of the Tool performance summary below.</li>"
            )
            continue
        rows.append(
            f"<li>{label}: no findings, from {verdicted} rule(s) verdicted "
            f"({not_applicable} of them set aside as not applicable)</li>"
        )

    return (
        f"<h3>Domains with no findings: {len(quiet)} of {len(selected)}</h3>"
        f'<ul class="quiet-domains">{"".join(rows)}</ul>'
    )


def _findings_section(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    rule_index: dict[str, Rule],
    pack_is_v2: bool,
) -> str:
    """Every finding in the run, ordered by severity rather than by the order
    the rules pack happens to list domains in (issue #122, point 3).

    The old order was rules-pack id order, then recorded order inside each
    domain, which put nothing in particular first: on a real 16-domain run
    the two critical findings sat about 4,000 pixels apart with eight
    lower-severity findings between them. Sorting is stable, so within one
    severity the old domain-then-recorded order is exactly preserved.

    The domain moves onto the card rather than becoming a subheading inside
    each severity group. It is one short string per finding either way, and
    a subheading level that often holds a single card is furniture
    (D16-R06).
    """
    if not selected:
        return "<p>No domains selected.</p>"

    all_findings = [
        (domain_id, finding)
        for domain_id, result in selected.items()
        for finding in result.findings
    ]
    total = len(all_findings)

    blocks = []
    for severity in _SEVERITY_ORDER:
        group = [pair for pair in all_findings if pair[1].severity.value == severity]
        if not group:
            continue
        cards = "".join(
            _finding_card(domain_titles[domain_id], finding, rule_index, pack_is_v2)
            for domain_id, finding in group
        )
        blocks.append(
            f"<h3>{_esc(severity.capitalize())}: {len(group)} of {total} "
            f"{_plural(total, 'finding')}</h3>{cards}"
        )

    if not blocks:
        blocks.append("<p>No findings were recorded in any selected domain.</p>")
    blocks.append(_domains_without_findings(selected, domain_titles))
    return "".join(blocks)


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
        "<label>Repository (owner/name)<br>"
        f'<input type="text" id="gh-repo" value="{_esc(repo_prefill)}" placeholder="owner/name">'
        "</label><br>"
        "<label>Personal access token<br>"
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
            {
                "rule_id": finding.rule_id,
                "title": finding.issue_title,
                "body": body_with_trailing,
            }
        )

        seen[finding.rule_id] += 1
        finding_key = f"{finding.rule_id}#{seen[finding.rule_id]}"
        filed_url = issue_urls.get(finding_key)
        textarea_id = f"issue-text-{index}"
        status_id = f"issue-status-{index}"

        if filed_url:
            _require_href_scheme(
                filed_url,
                ("http", "https"),
                f"issue url for rule id '{finding.rule_id}'",
            )
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}" disabled> '
                f'<a href="{_esc(filed_url)}">already filed</a>'
            )
        else:
            # Only critical and high are ticked on load (issue #122, point
            # 4). Ticking all of them made the default action treat a leaked
            # credential and a missing docstring as the same work, which is
            # the opposite of the ordering the report now leads with. The
            # rest are one click away, and the note above the list says so:
            # nothing is hidden, only unticked.
            checked = (
                " checked" if finding.severity.value in _PRETICKED_SEVERITIES else ""
            )
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}"{checked} '
                'onchange="updateGithubFileButtonLabel()">'
            )

        blocks.append(
            '<div class="issue-block">'
            f'<label class="issue-select">{checkbox_html}</label>'
            f"<p><strong>{_esc(finding.issue_title)}</strong></p>"
            f'<textarea id="{textarea_id}" readonly rows="6">{_esc(full_text)}</textarea>'
            f'<button type="button" onclick="copyIssueText(\'{textarea_id}\', this)">'
            "Copy issue text</button> "
            f'<span class="issue-status" id="{status_id}"></span>'
            "</div>"
        )

    preticked = sum(
        1 for f in all_findings if f.severity.value in _PRETICKED_SEVERITIES
    )
    selection_note = (
        f'<p class="muted">{preticked} of {len(all_findings)} '
        f"{_plural(len(all_findings), 'issue')} "
        f"{_plural(preticked, 'is', 'are')} ticked: the critical and high findings. "
        "The medium and low ones are listed unticked, not hidden. Tick any you also "
        "want to file, or untick one you do not. An issue already filed this run is "
        "shown unticked with a link to it.</p>"
    )

    button_row = _issue_button_row()
    data_script = (
        '<script type="application/json" id="issues-data">'
        f"{_json_script({'issues': issues_data})}"
        "</script>"
    )

    return (
        f"{selection_note}"
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
        _require_href_scheme(
            feedback_issue_url, ("http", "https"), "feedback issue url"
        )
        filed_html = (
            f'<p>Feedback for this run was already filed as <a href="{_esc(feedback_issue_url)}">'
            "an issue</a> on the tool author's repository. Further feedback can still be sent "
            "below.</p>"
        )

    sections = build_feedback_sections(
        run_state.meta,
        run_state.domain_results,
        rules_fetched_domain_ids=run_state.rules_fetched_domain_ids,
        rules_fetch_unknown_domain_ids=run_state.rules_fetch_unknown_domain_ids,
    )
    feedback_data = {
        "email": FEEDBACK_EMAIL,
        "subject": feedback_subject(run_state.meta),
        "run_metadata": sections["run_metadata"],
        "coverage": sections["coverage"],
        "rollup": sections["rollup"],
        "self_assessment": sections["self_assessment"],
        "environment": sections["environment"],
        "consulted_sources": sections["consulted_sources"],
        "verdict_distribution": sections["verdict_distribution"],
        "duration": sections["duration"],
        "rules_fetched": sections["rules_fetched"],
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
        + _consent_row(
            "consent-verdict-distribution",
            "Rule verdict distribution (counts of pass, finding, not-applicable and "
            "could-not-evaluate, per domain and in total; not the finding text)",
            consent.verdict_distribution,
        )
        + _consent_row(
            "consent-duration",
            "Run duration (the assistant-reported span, the server-measured span, and "
            "whether they agree). Token counts are not included, since the server never "
            "sees them: paste them into the feedback text above if you want to share them.",
            consent.duration,
        )
        + _consent_row(
            "consent-rules-fetched",
            "Rules fetched (per domain, whether this run's rule text was fetched via "
            "get_domain. Shows only that it was fetched, never that it was read or applied.)",
            consent.rules_fetched,
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
            raise ReportError(
                f"selected domain '{domain_id}' has no DomainResult for this run"
            )
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
        f'<div class="perf-block prominent">'
        f"{_not_applicable_list(selected, rule_index, domain_titles)}</div>"
        f'<div class="perf-block prominent">'
        f"{_rules_fetched_list(run_state, selected, domain_titles)}</div>"
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
        repo_heading=_esc(run_state.meta.repo_name),
        headline_block=_headline_block(all_findings, selected),
        meta_block=_render_meta_block(run_state),
        performance_summary=performance_summary,
        findings_section=_findings_section(
            selected, domain_titles, rule_index, pack.is_v2
        ),
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
