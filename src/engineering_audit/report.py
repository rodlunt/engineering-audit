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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    unevaluated_base,
    build_feedback_sections,
    build_issue_trailing_line,
    domain_confidence_note,
    duration_text,
    feedback_subject,
    rules_pack_label,
    strip_markdown_emphasis,
)
from engineering_audit.rules import (
    PACK_FORMAT_MAX,
    PACK_FORMAT_MIN,
    citation,
    Rule,
    RulesPack,
)
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

# The severities whose issue checkbox is ticked when the report loads. See
# _issues_section for why this is not all four.
_PRETICKED_SEVERITIES = ("critical", "high")

# Word for word from AUDIT.md's own guidance for the agent choosing a
# finding's severity ("Have a `severity` chosen with this guidance", step 4).
# Copied here rather than read from AUDIT.md at render time, since AUDIT.md
# is a document for the driving assistant to read, not a file this package
# ships or opens at run time; test_severity_definitions_match_audit_md
# parses AUDIT.md's own bullets and asserts they still equal this dict, so
# the two cannot drift apart silently (issue #129). The report never showed
# these definitions at all until now: a reader had no way to know what
# "critical" meant on this page, or that it was an assistant's judgement
# call rather than a measurement (see _findings_rollup).
_SEVERITY_DEFINITIONS: dict[str, str] = {
    "critical": (
        "exploitable now, or causes data loss (a secret committed to history, an "
        "auth bypass, an unguarded destructive migration)"
    ),
    "high": (
        "not on fire today, but will bite soon under normal operation (a race "
        "condition in a hot path, a silently-swallowed error in a payment flow)"
    ),
    "medium": (
        "should be fixed, but is not urgent (a missing test for a common path, an "
        "inconsistent naming convention that will cause a mistake eventually)"
    ),
    "low": "hygiene (a stale comment, a formatting inconsistency, a missing docstring)",
}


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


@dataclass(frozen=True)
class _CountedPopulation:
    """The wording for one summary line that states a count over a population
    some of whose members may never have been asked the question.

    Wording only. The states, the sentence shapes and the rule that every
    figure ships with the base it came out of all live in
    :func:`_count_over_population`, so a new summary line supplies words and
    inherits the behaviour rather than reimplementing it.

    ``found_predicate`` and the two others are past tense on purpose: past
    tense is number-invariant in English, so the same phrase reads correctly
    after "0 of 16 domains" and after "1 of 16 domains" without a second
    variant to keep in sync.

    ``never_recorded_unit`` exists because the never-asked count is not always
    in the same units as the found count. A rule that could not be evaluated is
    counted in rules; a domain that never ran, and so had no rule evaluated at
    all, is counted in domains. Both belong in one sentence, each with its own
    base.
    """

    label: str
    unit: str
    unit_plural: str | None = None
    found_predicate: str = ""
    all_recorded_predicate: str = ""
    never_recorded_predicate: str = ""
    never_recorded_unit: str | None = None


# Every summary line in this report that states a count over a population is
# registered here and rendered by _count_over_population below.
#
# One defect has now been found four times in this file: a summary that reads
# as a clean result when the underlying question was never asked. #100 (172
# not-applicable verdicts rendering as "0 findings"), #122 item 5 (a
# could-not-run domain rendering as a bare zero), #184 (an evidence boundary
# reading "0 of 16" on a run where no domain recorded one) and #195 ("None of
# the N domains reported a limit" on a run where no domain was asked for a
# self-assessment). Each was fixed where it was found, which is why the fourth
# shipped inside the block written to prevent the third.
#
# The point of the registry is that the fix is now one state machine with a
# test that walks it (test_report.py), instead of four sentences that each
# happened to get it right. Adding a summary line means adding an entry here.
_COUNT_SUMMARIES: dict[str, _CountedPopulation] = {
    "evidence-boundary": _CountedPopulation(
        label="Evidence boundary",
        unit="completed domain",
        found_predicate=(
            "reached verdicts without reading something the repository points at"
        ),
        all_recorded_predicate="recorded what they did not read",
        never_recorded_predicate=(
            "never recorded what they did not read, which is not the same as "
            "having read everything"
        ),
    ),
    "self-assessment-limits": _CountedPopulation(
        label="Self-assessment limits",
        unit="selected domain",
        found_predicate="reported a limit on their own assessment",
        all_recorded_predicate="recorded a self-assessment",
        never_recorded_predicate=(
            "never recorded a self-assessment at all, which is not the same as "
            "reporting no limits"
        ),
    ),
    "could-not-evaluate": _CountedPopulation(
        label="Could not evaluate",
        unit="rule verdicted",
        unit_plural="rules verdicted",
        never_recorded_unit="selected domain",
        never_recorded_predicate=(
            "did not run at all, so no rule in them was evaluated"
        ),
    ),
}


def _count_over_population(
    key: str,
    *,
    found: int,
    never_recorded: int,
    population: int,
    never_recorded_population: int | None = None,
) -> str:
    """One summary line stating a count over a population, in whichever of the
    four states that population is in.

    The three states this exists to keep apart:

    * **none recorded.** Nobody answered, so the count is not a result. The
      line says how many never answered and never shows the zero, because the
      zero is the reassuring reading of a question that was never put.
    * **none found.** Everybody answered and none of them had the thing. The
      line says so, and says that everybody answered.
    * **N found.** The count, with the base it came out of.

    The fourth is the mix, where some answered and some did not: both counts
    go in the sentence, because dropping either one recreates one of the first
    two states in a run that is not in it.

    No figure appears without its base (D16-R03), which is what stops
    "155 rules were set aside" inviting a reader to supply a denominator.
    """
    wording = _COUNT_SUMMARIES[key]
    if never_recorded_population is None:
        never_recorded_population = population

    units = _plural(population, wording.unit, wording.unit_plural)
    found_clause = f"{found} of {population} {units}"
    if wording.found_predicate:
        found_clause = f"{found_clause} {wording.found_predicate}"

    # The never-asked count keeps its own unit and its own base, so the two
    # halves of a mixed sentence cannot be read as one fraction.
    never_units = (
        _plural(never_recorded_population, wording.never_recorded_unit)
        if wording.never_recorded_unit is not None
        else _plural(never_recorded_population, wording.unit, wording.unit_plural)
    )
    never_clause = f"{never_recorded} of {never_recorded_population} {never_units}"
    if wording.never_recorded_predicate:
        never_clause = f"{never_clause} {wording.never_recorded_predicate}"

    if found == 0 and never_recorded and never_recorded == never_recorded_population:
        return f"{wording.label}: {never_clause}"
    if never_recorded:
        return f"{wording.label}: {found_clause}, and {never_clause}"
    if population == 0:
        return f"{wording.label}: no {units} to report on"
    if found == 0 and wording.all_recorded_predicate:
        return (
            f"{wording.label}: {found_clause}, and all {population} {units} "
            f"{wording.all_recorded_predicate}"
        )
    return f"{wording.label}: {found_clause}"


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
    """Strip markdown emphasis, escape, then apply the barest
    paragraph/line-break formatting.

    No markdown library is a dependency here (mcp + pydantic only), so this
    is deliberately not a markdown renderer: it escapes first, then turns
    blank-line-separated chunks into paragraphs and single newlines into
    line breaks. finding.body_md is assistant-authored and untrusted, and
    assistants write markdown by default (issue #128); the recorded
    decision is to strip it rather than render it, so this is the boundary
    that turns "**The issue**: ..." into plain "The issue: ...".
    """
    # Normalise CRLF (and lone CR) to LF first: a paragraph split on a literal
    # '\n\n' would otherwise miss every blank line in CRLF-sourced text.
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised = strip_markdown_emphasis(normalised)
    escaped = html.escape(normalised)
    paragraphs = [p for p in escaped.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


# The marker put on a header row whose value the calling assistant supplied
# and this server cannot check (issue #176). Short by design: the header is
# already dense, and the sentence explaining it lives once under the grid
# rather than on every row it applies to.
SELF_REPORTED_MARKER = "self-reported"

SELF_REPORTED_FOOTNOTE = (
    "Rows marked self-reported carry what the calling assistant said it was. "
    "Nothing on this page can verify them, and the same qualifier applies to "
    "every severity in this report, which that assistant assigned rather than "
    "measured. The unmarked rows above were measured or derived here."
)


def _render_meta_block(run_state: RunState) -> str:
    meta = run_state.meta
    # (label, value, asserted). asserted=True means the calling assistant
    # supplied it and the server has no way to check it (issue #176). Two real
    # runs by the same tester both ran gpt-5.6-sol while their headers read
    # "gpt-5.6-luna" and "GPT-5", and the maintainer initially read the two
    # headers as evidence of a model change. The values are kept, because
    # unknown-but-stated is an honest state and refusing them would lose the
    # information; what changes is that they no longer sit at the same visual
    # authority as the rows beside them that were actually measured.
    #
    # Started and Finished are deliberately NOT marked, though they are also
    # assistant-supplied. The Duration row directly below reconciles them
    # against the server's own clock (issue #102) and says so, which is a
    # stronger statement than this qualifier, and marking them would imply the
    # qualifier is all they get.
    rows: list[tuple[str, str, bool]] = [
        ("Repository", meta.repo_name, False),
        ("Commit", meta.repo_commit, False),
        ("Rules pack", rules_pack_label(meta), False),
        # Only when the pack's own pack.toml declares it a subset of a larger
        # pack (issue #255): self-declared, never inferred from domain count,
        # so an ordinary custom pack renders no row here at all.
        *(
            [
                (
                    "Rules pack edition",
                    meta.rules_pack_edition
                    + (
                        f"; full pack available on request: {meta.rules_pack_full_pack_url}"
                        " (already have it? re-register with --rules-dir aimed at its"
                        " domains/ directory; this run used the subset)"
                        if meta.rules_pack_full_pack_url
                        else ""
                    ),
                    False,
                )
            ]
            if meta.rules_pack_edition
            else []
        ),
        ("Rules commit", meta.rules_pack_commit or "unknown", False),
        ("Assistant", meta.assistant, True),
        ("Model", meta.model, True),
        # Only rendered for a resumed run picked up by a different assistant or
        # model. Naming just the current pair would credit it with findings an
        # earlier one recorded, which is the defect this row exists to close
        # (#93): a provenance header that is confidently wrong is worse than one
        # that is absent, because nothing prompts the reader to doubt it. It is
        # built from the same asserted assistant/model pair, so it inherits the
        # same qualifier.
        *(
            [("Earlier contributors", ", ".join(meta.earlier_contributors), True)]
            if meta.earlier_contributors
            else []
        ),
        ("Tool version", meta.tool_version, False),
        ("Tool commit", meta.tool_commit or "unknown", False),
        ("Tool update", meta.update_check or "not checked", False),
        ("Rules pack update", meta.pack_update_check or "not checked", False),
        ("Started", meta.started, False),
        ("Finished", meta.finished or "in progress", False),
        # The assistant-supplied Started/Finished rows above are asserted,
        # never measured (issue #102): the server has no clock of its own
        # until this row, which checks them against server_started/
        # server_finished rather than silently trusting either.
        ("Duration", duration_text(meta), False),
    ]
    rows_html = "".join(
        f'<div class="meta-label">{_esc(label)}'
        + (
            f' <span class="asserted">{_esc(SELF_REPORTED_MARKER)}</span>'
            if asserted
            else ""
        )
        + f'</div><div class="meta-value">{_esc(value)}</div>'
        for label, value, asserted in rows
    )
    # Collapsed behind a summary that is sufficient on its own (issue #124):
    # which repository, at which commit, audited by what against which rules
    # pack, and when it finished. Provenance a reader needs to trust the page
    # is on the visible line; the remaining rows are the detail behind it.
    # Every identifier in this summary is also in the footer, which never
    # collapses, so nothing here depends on the <details> opening.
    summary = (
        f"Run details: {_esc(meta.repo_name)} at commit {_esc(meta.repo_commit)}, "
        f"audited by {_esc(meta.assistant)} / {_esc(meta.model)} against rules pack "
        f"{_esc(rules_pack_label(meta))}, finished "
        f"{_esc(meta.finished or 'in progress')}. "
        f"{len(rows)} recorded {_plural(len(rows), 'field')}."
    )
    return (
        f'<details class="meta-details"><summary>{summary}</summary>'
        f'<div class="meta-grid">{rows_html}</div>'
        f'<p class="meta-footnote">{_esc(SELF_REPORTED_FOOTNOTE)}</p>'
        "</details>"
    )


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


def _unevaluated_map(
    selected: dict[str, DomainResult],
) -> dict[str, tuple[int, int] | None]:
    """Domain id -> ``(could_not_evaluate_count, rules_verdicted)``, the base
    every self-reported confidence claim is rendered against (issue #211).

    Computed once and shared by the per-domain table, the finding cards and
    the Issues section, for the same reason ``confidence_map`` and
    ``fetch_status`` already are: three places that describe the same
    domain must not be able to disagree about it.

    A could-not-run domain maps to ``None`` rather than ``(0, 0)``. It has
    no verdicts by construction, so there is no denominator, and "all 0
    rules evaluated" would read as a clean sweep of a domain that never
    ran, which is the exact defect #55 and #100 were about.
    """
    return {
        domain_id: (
            None
            if result.status == "could-not-run"
            else (
                _verdict_counts(result)[Verdict.COULD_NOT_EVALUATE.value],
                len(result.rule_verdicts),
            )
        )
        for domain_id, result in selected.items()
    }


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


# The bar's segment order, left to right, and the words that name each
# segment in the numerals beside it. Checked first (pass, finding), then not
# checked (not applicable, could not evaluate), so the split a reader most
# wants (how much of this domain was actually looked at) falls where the
# colours change rather than being scattered across the bar.
_VERDICT_BAR_ORDER: tuple[tuple[str, str, str], ...] = (
    (Verdict.pass_.value, "pass", "seg-pass"),
    (Verdict.FINDING.value, "finding", "seg-finding"),
    (Verdict.NOT_APPLICABLE.value, "not applicable", "seg-na"),
    (Verdict.COULD_NOT_EVALUATE.value, "could not evaluate", "seg-cne"),
)


def _verdict_bar(counts: Counter[str], verdicted: int, scale_max: int) -> str:
    """One domain's verdict mix as an inline stacked bar.

    Length, not colour intensity, carries the quantity (D16-R05): a
    heat-shaded cell would encode a number in the one visual channel people
    read least reliably. The bar is scaled against the largest domain in the
    run rather than being stretched to full width in every row, so a
    twelve-rule domain draws a visibly shorter bar than a twenty-rule one.
    Stretching each bar to full width would have made every row the same
    size and turned the only length cue in the table into a proportion.

    The bar is aria-hidden and carries no data of its own: every number it
    draws is written out in words in the same cell, immediately after it.
    That is what makes it decoration in the good sense (redundant
    reinforcement, D16-R16) rather than the only place a value lives, and it
    satisfies D16-R17's text-alternative requirement without a second
    element to keep in sync.
    """
    if verdicted <= 0 or scale_max <= 0:
        return ""
    segments = "".join(
        f'<span class="vseg {css_class}" style="width:{counts[key] / verdicted * 100:.4f}%"></span>'
        for key, _label, css_class in _VERDICT_BAR_ORDER
        if counts[key]
    )
    return (
        '<span class="vbar-track" aria-hidden="true">'
        f'<span class="vbar" style="width:{verdicted / scale_max * 100:.4f}%">'
        f"{segments}</span></span>"
    )


def _verdict_numerals(counts: Counter[str], verdicted: int) -> str:
    """The bar's values in words, in the bar's own segment order.

    Every count ships with the base it came out of, which is the whole
    reason this reads "6 not applicable" followed by "of 15 rules verdicted"
    rather than a bare 6, and the reason none of it is a percentage: a share
    with no base is exactly the figure a reader fills in wrongly.
    """
    # Label first, count second. "2 finding" and "2 pass" both read like
    # typos, and pluralising a verdict's own name ("2 findings") would make
    # the finding verdict look like the findings count, which it is not: two
    # findings can be recorded against one rule.
    parts = ", ".join(
        f"{label}: {counts[key]}" for key, label, _css in _VERDICT_BAR_ORDER
    )
    return (
        f'<span class="verdict-numerals">{_esc(parts)}, of {verdicted} '
        f"{_plural(verdicted, 'rule')} verdicted</span>"
    )


def _rules_fetched_status(
    run_state: RunState, selected: dict[str, DomainResult]
) -> dict[str, str]:
    """Domain id -> the fetch answer for that domain, in three states plus one.

    Shared by the per-domain table and the Rules fetched block below it, so
    the two cannot disagree about the same domain. "not recorded" is a third
    answer, never folded into either of the other two, and a domain that
    reached no verdict at all is outside the question rather than a failure
    of it.
    """
    considered = set(_domain_ids_with_verdicts(selected))
    recorded = run_state.rules_fetched_domain_ids
    fetched_ids = set(recorded or ())
    unknown_ids = set(run_state.rules_fetch_unknown_domain_ids) - fetched_ids

    status: dict[str, str] = {}
    for domain_id in selected:
        if domain_id not in considered:
            status[domain_id] = "no verdicts to check"
        elif recorded is None or domain_id in unknown_ids:
            status[domain_id] = "not recorded"
        elif domain_id in fetched_ids:
            status[domain_id] = "yes"
        else:
            status[domain_id] = "no"
    return status


def _fetch_status_to_bool(status: str) -> bool | None:
    """Map _rules_fetched_status's four-way per-domain answer to the
    True/False/None a single finding's own domain-confidence note needs
    (issue #130).

    "no verdicts to check" folds into None here rather than getting its own
    branch: it is unreachable for a domain that produced a finding (a
    finding requires a finding-verdict, so such a domain is always in
    ``considered``), and where it is reachable (a domain with no findings
    at all) nothing calls this for it, since there is no finding card or
    issue to attach the note to.
    """
    if status == "yes":
        return True
    if status == "no":
        return False
    return None


def _files_cell(result: DomainResult) -> str:
    if result.coverage is None:
        return "no coverage reported"
    note = (
        f' <span class="muted">({_esc(result.coverage.note)})</span>'
        if result.coverage.note
        else ""
    )
    return (
        f"{result.coverage.files_inspected} inspected, "
        f"{result.coverage.files_skipped} skipped{note}"
    )


def _severity_cell(domain_findings: Counter[str], total_findings: int) -> str:
    """A domain's findings, as a count out of the run's total plus the four
    severities including the zeros.

    Nonzero critical and high counts are bolded rather than coloured. Weight
    is a channel that survives greyscale, a photocopy and the print
    palette; a colour that carries meaning nothing else carries would fail
    D16-R16, and a colour that carries meaning the word beside it already
    carries would be furniture (D16-R06).
    """
    domain_total = sum(domain_findings.values())
    parts = []
    for severity in _SEVERITY_ORDER:
        count = domain_findings.get(severity, 0)
        text = f"{count} {_esc(severity)}"
        if count and severity in _PRETICKED_SEVERITIES:
            text = f"<strong>{text}</strong>"
        parts.append(text)
    return (
        f"{domain_total} of {total_findings}<br>"
        f'<span class="muted">{", ".join(parts)}</span>'
    )


def _domain_table(
    run_state: RunState,
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    all_findings: list[tuple[str, Finding]],
) -> str:
    """One row per domain, replacing five separate per-domain lists.

    The report used to hold five facts about each domain and render each as
    its own list, in five blocks spread over roughly 4,000 vertical pixels:
    coverage, findings rollup, not applicable, self-assessment and rules
    fetched. That is a 16-row, 5-column table shredded into five
    one-column lists, and answering "which domain should I worry about"
    meant joining them by domain title by eye (issue #123).

    Every cell carries its own base. A domain that produced nothing is a row
    of zeros, not a missing row, and the three ways of producing nothing
    (swept clean, set aside in full, never ran) each read differently.
    """
    if not selected:
        return "<p>No domains selected.</p>"

    findings_by_domain: dict[str, Counter[str]] = {
        domain_id: Counter() for domain_id in selected
    }
    for domain_id, finding in all_findings:
        findings_by_domain[domain_id][finding.severity.value] += 1
    total_findings = len(all_findings)

    verdict_counts = {
        domain_id: _verdict_counts(result) for domain_id, result in selected.items()
    }
    verdicted_totals = {
        domain_id: sum(counts.values()) for domain_id, counts in verdict_counts.items()
    }
    scale_max = max(verdicted_totals.values(), default=0)
    fetch_status = _rules_fetched_status(run_state, selected)
    unevaluated = _unevaluated_map(selected)

    rows = []
    for domain_id, result in selected.items():
        label = (
            f'<th scope="row"><span class="domain-id">{_esc(domain_id)}</span> '
            f"{_esc(domain_titles[domain_id])}</th>"
        )
        # Issue #211: the self-report never renders on its own. A domain that
        # could not evaluate 10 of its 18 rules and claimed "high" used to be
        # indistinguishable here from one that could not evaluate 2 of 15,
        # which is the reverse of what README.md promises a reader. The claim
        # stays the auditor's; the base beside it is the tool's own count.
        confidence = (
            _esc(result.self_assessment.confidence)
            if result.self_assessment is not None
            else "not reported"
        ) + _esc(unevaluated_base(unevaluated[domain_id]))
        if result.status == "could-not-run":
            # No verdicts by construction, so there is no mix to draw and no
            # denominator any numeral here could be a count out of. Saying
            # "did not run, nothing checked" is the whole content of the row.
            verdict_cell = "<strong>did not run, nothing checked</strong>"
            severity_cell = "did not run"
        else:
            counts = verdict_counts[domain_id]
            verdicted = verdicted_totals[domain_id]
            verdict_cell = _verdict_bar(
                counts, verdicted, scale_max
            ) + _verdict_numerals(counts, verdicted)
            severity_cell = _severity_cell(
                findings_by_domain[domain_id], total_findings
            )
        # data-label carries each column's header text onto its own cell
        # (issue #165). Unused by any browser at desktop width; the narrow
        # breakpoint below 640px turns each row into a block and reveals it
        # via a ::before rule, so a stacked cell still says what it is
        # without the table's header row, which stacking makes impractical
        # to keep visible.
        rows.append(
            "<tr>"
            f"{label}"
            f'<td class="verdict-cell" data-label="Rule verdicts">{verdict_cell}</td>'
            f'<td class="findings-cell" data-label="Findings">{severity_cell}</td>'
            f'<td class="files-cell" data-label="Files">{_files_cell(result)}</td>'
            f'<td data-label="Confidence">{confidence}</td>'
            f'<td data-label="Rules fetched">{_esc(fetch_status[domain_id])}</td>'
            "</tr>"
        )

    totals = _run_totals(selected)
    verdicted_total = sum(totals.values())
    totals_cells = (
        '<th scope="row">All '
        f"{len(selected)} selected {_plural(len(selected), 'domain')}</th>"
        f'<td class="verdict-cell" data-label="Rule verdicts">'
        f"{_verdict_numerals(totals, verdicted_total)}</td>"
        f'<td data-label="Findings">{total_findings} of {total_findings}</td>'
        # Deliberately not summed. Each domain audits the same repository
        # from its own angle, so a file that sixteen domains each declined to
        # open is sixteen skips in a naive total: a 344-file repository
        # rendered "5320 skipped" before issue #87 removed the figure. The
        # cell says why rather than going blank, which would read as missing
        # data.
        '<td class="muted" data-label="Files">not summed: a file two domains both '
        "opened would count twice</td>"
        '<td class="muted" data-label="Confidence">per domain</td>'
        '<td class="muted" data-label="Rules fetched">per domain</td>'
    )

    legend = "".join(
        f'<li><span class="vkey {css_class}" aria-hidden="true"></span>{_esc(label)}</li>'
        for _key, label, css_class in _VERDICT_BAR_ORDER
    )

    return (
        "<p>One row per selected domain. The bar in the verdicts column is drawn to the "
        "same scale in every row, so a domain with fewer rules draws a shorter bar, and "
        "every value it draws is written out beside it.</p>"
        f'<ul class="vbar-legend">{legend}</ul>'
        '<div class="domain-table-wrap">'
        '<table class="domain-table">'
        "<thead><tr>"
        '<th scope="col">Domain</th>'
        '<th scope="col">Rule verdicts</th>'
        '<th scope="col">Findings</th>'
        '<th scope="col">Files '
        f'<span class="asserted">{_esc(SELF_REPORTED_MARKER)}</span></th>'
        '<th scope="col">Confidence</th>'
        '<th scope="col">Rules fetched</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"<tfoot><tr>{totals_cells}</tr></tfoot>"
        "</table>"
        "</div>"
    )


def _self_assessment_limits(
    selected: dict[str, DomainResult], domain_titles: dict[str, str]
) -> str:
    """The limits each domain reported on its own assessment.

    The confidence word itself is a table column now. The limits are free
    text of arbitrary length and would wreck the table, so they stay here,
    and they stay at all because a self-reported limit is the one part of a
    self-assessment that can contradict the confidence beside it.

    Rendered even when nothing was reported: a vanished block and a run
    where every domain claimed no limits look identical otherwise.

    Which was only half the question (issue #195). A run where every domain
    recorded a self-assessment and stated no limits, and a run where no domain
    recorded one at all, produced the same reassuring sentence, and the second
    is the common case rather than the edge: AUDIT.md never asks the auditor
    for a self-assessment, so an auditor that does not improvise leaves it
    None everywhere. The summary goes through _count_over_population, which
    keeps the two apart the way the confidence column above already does by
    rendering "not reported".
    """
    rows = [
        f"<li>{_esc(domain_id)}: {_esc(domain_titles[domain_id])}: "
        f"{_esc(result.self_assessment.limits)}</li>"
        for domain_id, result in selected.items()
        if result.self_assessment is not None and result.self_assessment.limits
    ]
    never_recorded = sum(
        1 for result in selected.values() if result.self_assessment is None
    )
    # Collapsed behind its own count (issue #124). The limits are free text
    # of arbitrary length and are the longest thing in this block; the count
    # is the signal, and each domain named inside is also a row in the table
    # above, which never collapses.
    summary = _count_over_population(
        "self-assessment-limits",
        found=len(rows),
        never_recorded=never_recorded,
        population=len(selected),
    )
    if not rows:
        # Nothing to collapse, so the summary is the whole block. It is the
        # same sentence either way: the reader must not have to open a
        # <details> to learn that nobody answered.
        return (
            f"<p>{_esc(summary)}. Each domain's confidence is in the table above.</p>"
        )
    return (
        "<p>Each domain's confidence is in the table above.</p>"
        f"<details><summary>{_esc(summary)}</summary>"
        f"<ul>{''.join(rows)}</ul>"
        "</details>"
    )


def _severity_definitions_block(assistant: str, model: str) -> str:
    """The four severity definitions from AUDIT.md, plus a sentence saying
    they were assigned by this run's assistant while writing each finding,
    not measured against a fixed instrument (issue #129).

    d16 review evidence: an external tester's write-up opened by supplying
    its own definitions of "high", "medium" and "low", because the artefact
    never carried them; the definitions themselves were close to
    AUDIT.md's, showing the protocol was sound and simply never reached the
    page. Matches the voice of _RULES_FETCHED_LIMIT: what the label is,
    what it is not, stated the same way on every rendering.
    """
    definitions = "".join(
        f"<dt>{_esc(severity)}</dt><dd>{_esc(definition)}.</dd>"
        for severity, definition in _SEVERITY_DEFINITIONS.items()
    )
    return (
        f'<dl class="severity-definitions">{definitions}</dl>'
        f"<p>{_esc(assistant)} ({_esc(model)}) judged each finding's severity against "
        "these four definitions while writing it. It is not a measurement: the same "
        "evidence graded by a different assistant or model could come back a different "
        "severity, and nothing on this page recalibrates the two to agree.</p>"
    )


def _findings_rollup(
    all_findings: list[tuple[str, Finding]],
    selected: dict[str, DomainResult],
    assistant: str,
    model: str,
) -> str:
    """The run's totals: findings by severity, and every rule verdict by
    verdict.

    The by-domain list that used to live here is the table above, keyed by
    domain id so two domains sharing a title cannot merge into one row.

    Two things changed with issue #123. Every count now carries the base it
    came out of, because "Total findings: 30" and "critical: 2" both shipped
    without one, and thirty findings against 37 passes is a very different
    report from thirty against 244 rules mostly set aside. And the pass
    count appears, which it never did anywhere in the visible report despite
    being computed for the feedback payload all along.

    ``assistant`` and ``model`` (this run's own, from the header) are for
    the severity-definitions block issue #129 adds after the by-severity
    list: naming who assigned these labels, not just what they mean.
    """
    severity_counts: Counter[str] = Counter(f.severity.value for _, f in all_findings)
    total = len(all_findings)
    totals = _run_totals(selected)
    verdicted = sum(totals.values())
    domains_with_verdicts = len(_domain_ids_with_verdicts(selected))

    sev_items = "".join(
        f"<li>{_esc(sev)}: {severity_counts.get(sev, 0)} of {total}</li>"
        for sev in _SEVERITY_ORDER
    )
    verdict_items = "".join(
        f"<li>{_esc(label)}: {totals[key]} of {verdicted}</li>"
        for key, label, _css in _VERDICT_BAR_ORDER
    )
    return (
        f"<p><strong>{total}</strong> {_plural(total, 'finding')} across "
        f"{verdicted} {_plural(verdicted, 'rule')} verdicted in "
        f"{domains_with_verdicts} of {len(selected)} "
        f"{_plural(len(selected), 'domain')}.</p>"
        f"<h3>Findings by severity</h3><ul>{sev_items}</ul>"
        f"{_severity_definitions_block(assistant, model)}"
        f"<h3>Rule verdicts</h3><ul>{verdict_items}</ul>"
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


def _evidence_boundary_list(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
) -> str:
    """What the run did not read, per domain, and which domains never said
    (issue #179).

    Three outcomes, reported as three, for the same reason
    :func:`_domains_without_findings` splits its zeros three ways. A domain
    that read everything the repository points at, a domain that read less and
    said so, and a domain that never answered the question all used to be
    indistinguishable here, and the third is the one that produced #179: a run
    whose d02 knew the requirements were in an issue tracker it had not opened,
    recorded that against one rule's free-text note, and filed eleven findings
    in the same domain asserting those requirements did not exist.

    A domain that recorded nothing is not reported as a domain that read
    everything. It is named, and counted, and its findings are not annotated
    on their cards, because there is nothing recorded to annotate them with.
    """
    completed = {
        domain_id: result
        for domain_id, result in selected.items()
        if result.status == "completed"
    }
    if not completed:
        return (
            "<h3>Evidence boundary</h3>"
            "<p>No domain completed, so no domain recorded what it did not read.</p>"
        )

    never_recorded = [
        domain_id
        for domain_id, result in completed.items()
        if result.uninspected_evidence is None
    ]
    with_gaps = {
        domain_id: result.uninspected_evidence
        for domain_id, result in completed.items()
        if result.uninspected_evidence
    }
    read_everything = len(completed) - len(never_recorded) - len(with_gaps)

    if not never_recorded and not with_gaps:
        return (
            "<h3>Evidence boundary</h3>"
            f'<p class="ok">All {len(completed)} completed '
            f"{_plural(len(completed), 'domain')} recorded that the repository points "
            "at nothing they did not read.</p>"
        )

    # The body below has said "N completed domains never recorded an evidence
    # boundary at all" since #179, and the summary above it still read
    # "0 of 16", which is the sentence a reader skims and quotes (issue #184).
    # Both counts are in the summary now, from the same helper every other
    # count-over-a-population line in this report uses.
    summary = _count_over_population(
        "evidence-boundary",
        found=len(with_gaps),
        never_recorded=len(never_recorded),
        population=len(completed),
    )
    parts = [
        "<p>A finding says this repository does not do something. That claim is only as "
        "good as the places the audit looked. This is what each domain recorded that it "
        "did not open.</p>"
    ]
    if with_gaps:
        rows = []
        for domain_id, entries in with_gaps.items():
            items = "".join(f"<li>{_esc(entry.strip())}</li>" for entry in entries)
            rows.append(
                f"<li><strong>{_esc(domain_id)}: {_esc(domain_titles[domain_id])}</strong>"
                f"<ul>{items}</ul></li>"
            )
        parts.append(f'<ul class="boundary-domains">{"".join(rows)}</ul>')
    if never_recorded:
        names = ", ".join(
            f"{_esc(domain_titles[domain_id])} ({_esc(domain_id)})"
            for domain_id in never_recorded
        )
        parts.append(
            f"<p><strong>{len(never_recorded)} completed "
            f"{_plural(len(never_recorded), 'domain')} never recorded an evidence "
            f"boundary at all</strong>, which is not the same as having none: "
            f"{names}. Their findings carry no scope caveat because none was "
            "recorded to carry.</p>"
        )
    if read_everything:
        parts.append(
            f"<p>The remaining {read_everything} completed "
            f"{_plural(read_everything, 'domain')} recorded that the repository "
            "points at nothing they did not read.</p>"
        )
    return (
        "<h3>Evidence boundary</h3>"
        f"<details open><summary>{_esc(summary)}</summary>{''.join(parts)}</details>"
    )


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

    verdicted = sum(_run_totals(selected).values())
    # The not-run domains were named in the body and missing from the summary,
    # so a run with a whole domain that never started could still be headlined
    # "Could not evaluate: 0 of 244 rules verdicted" behind a closed <details>.
    # That is #184's defect at a third site, in rules rather than domains,
    # which is why the helper carries a separate base for the never-asked
    # count instead of forcing both counts into one denominator.
    summary = _count_over_population(
        "could-not-evaluate",
        found=total,
        never_recorded=len(not_run_domain_ids),
        population=verdicted,
        never_recorded_population=len(selected),
    )
    parts = []
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
    # Collapsed behind its headline (issue #164), which is why the rule-id
    # lists inside it are now allowed inside a closed <details>: #124 left
    # them expanded because find-in-page inside a closed <details> was
    # judged unreliable across engines, but a real reader test of a 181-rule
    # report showed screen after screen of rule ids between the reader and
    # everything below, and the maintainer's call on seeing that was to
    # collapse. The headline carries the count on its own, same as every
    # other collapsed summary in this report.
    return f"<details><summary>{_esc(summary)}</summary>{''.join(parts)}</details>"


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

    # The per-domain "N of M rule(s) not applicable" list that used to sit
    # here is the table at the top of this section now (issue #123): it was
    # one of five one-column lists a reader had to join by domain title by
    # eye. Its denominators went with it into the table's verdicts column,
    # rather than being dropped on the way.
    verdicted_total = sum(_run_totals(selected).values())
    summary = (
        f"Not applicable: {total} of {verdicted_total} "
        f"{_plural(verdicted_total, 'rule')} verdicted"
    )
    parts = [
        "<p>These are rules the audit set aside because the thing they are about is not "
        "present in this repository. They were not checked against it, and they are not "
        "findings: a rule set aside is a claim about the repository, so each one carries "
        "the reason it was set aside. The per-domain counts are in the table at the top "
        "of this section.</p>",
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
    # Collapsed behind its headline (issue #164): same trade-off as the
    # could-not-evaluate block above, and for the same reason. See that
    # block's comment for the history.
    return f"<details><summary>{_esc(summary)}</summary>{''.join(parts)}</details>"


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


def _provenance_blind_notice(meta: RunMeta) -> str:
    """A loud line for the one state neither staleness check's own header
    row makes obvious: both the tool update check and the rules pack update
    check came back could-not-check on the same run (issue #136).

    Each check is honest on its own: 'could-not-check' already refuses to
    be mistaken for 'current' (see update_check.py). What the two header
    rows do not do is say what it costs the reader when *both* land there
    at once, which is the one combination where a stale build running
    stale rules is completely undetectable, because the two mechanisms
    that exist to catch exactly that could not run at all. One check
    working is a materially different, much less serious situation (this
    build's age, or this pack's age, is still known) and must never trip
    this notice; only the combination does.

    Fires on the 'could-not-check' prefix only, never on 'not-checked'
    (the check was turned off deliberately, a choice, not a mystery) and
    never on None (an older run-state file that predates the field
    entirely, which is a different, and separately honest, kind of
    unknown). Never fatal and never guesses a version, matching the
    constraint the tri-state detection itself already follows: an unknown
    provenance is a fact to report, not a run to refuse.

    Matches _RULES_FETCHED_LIMIT's register: says exactly what is and is
    not known, and no more. It does not say the findings are wrong, only
    that nothing here can rule out stale rules or a stale build. Wording
    discipline carried over from issue #110 too: 'served' is the rules
    pack's own fetch status, not this notice's business; what this notice
    reports is only that the *version comparison itself* could not run,
    never that the rules or the build are in fact stale.
    """
    tool_check = meta.update_check or ""
    pack_check = meta.pack_update_check or ""
    if not (
        tool_check.startswith("could-not-check")
        and pack_check.startswith("could-not-check")
    ):
        return ""
    return (
        '<div class="perf-block prominent">'
        "<h3>Both provenance checks are blind</h3>"
        "<p>The Tool update and Rules pack update rows above both read "
        "<code>could-not-check</code>: neither this build's age nor this rules pack's age "
        "could be compared against what has since been released. That is not evidence "
        "either is stale, only that nothing here can tell you either way. This run is a "
        "build of unknown age judging your repository against rules of unknown age, and "
        'no mechanism in this report can detect if either has drifted. See "What keeps '
        'the staleness checks working" in the project README for the install shapes '
        "that keep both checks attached.</p>"
        "</div>"
    )


def _stale_build_notice(meta: RunMeta) -> str:
    """A loud line when either staleness check positively confirmed a newer
    release than the one this run used (issue #254).

    The check's result already sat in the Tool update / Rules pack update
    meta rows, but a meta row inside a collapsed details block is where a
    caveat goes to be technically-disclosed: a report produced by an
    outdated build is itself a caveat on the findings, and gets the same
    prominent treatment as a modified build (_modified_tool_notice) for the
    same reason. Fires on a "stale" prefix only: "could-not-check" has its
    own notice above when both checks are blind, and neither it nor
    "not-checked" is evidence of staleness (nothing was established), so
    neither may borrow this one. Matches the register of its siblings: a
    stale build is not evidence the findings are wrong, only that they
    cannot claim to have come from the current release.
    """
    lines = []
    if (meta.update_check or "").startswith("stale"):
        lines.append(f"<p>Tool: <code>{_esc(meta.update_check)}</code></p>")
    if (meta.pack_update_check or "").startswith("stale"):
        lines.append(f"<p>Rules pack: <code>{_esc(meta.pack_update_check)}</code></p>")
    if not lines:
        return ""
    return (
        '<div class="perf-block prominent">'
        "<h3>A newer release existed when this run was made</h3>"
        + "".join(lines)
        + "<p>That is not evidence the findings below are wrong, only that this "
        "run cannot claim they came from the current release. The status above "
        "carries the update command for the host that ran this audit.</p>"
        "</div>"
    )


def _modified_tool_notice(meta: RunMeta) -> str:
    """A loud line when the tool build that produced this run was itself
    modified (issue #169): ``tool_commit`` carries a ``-dirty`` suffix,
    which server.py's ``_git_commit`` only appends once the git fallback for
    an editable or checkout install finds uncommitted changes in the tool's
    own source tree.

    Matches _RULES_FETCHED_LIMIT's and _provenance_blind_notice's register:
    says what is known and claims nothing more. A run made by a modified
    tool build is exactly as caveat-worthy as one made against a modified
    rules pack (see check_pack_for_update's dirty branch): the code that
    produced the verdicts below does not match any released commit, so it
    cannot be reproduced or compared against one. That says nothing about
    whether the verdicts themselves are right or wrong.

    Fires on the ``-dirty`` suffix only, never on None (unknown provenance,
    a materially different and separately honest state) and never on a
    clean SHA.
    """
    tool_commit = meta.tool_commit or ""
    if not tool_commit.endswith("-dirty"):
        return ""
    return (
        '<div class="perf-block prominent">'
        "<h3>This run used a modified tool build</h3>"
        f"<p>The tool commit above (<code>{_esc(_short_commit(tool_commit))}</code>) has "
        "uncommitted changes: this run was made by a development checkout, not a build "
        "that matches any released commit. That is not evidence the findings below are "
        "wrong, only that the code which produced them cannot be reproduced from a "
        "release tag or compared against one.</p>"
        "</div>"
    )


_VERSION_TUPLE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version_tuple(value: str) -> tuple[int, int, int] | None:
    """Parse a strict X.Y.Z version string into a comparable tuple, or None
    if it is not in that exact shape.

    A tuple compares numerically ('0.9.0' > '0.10.0' as strings, the wrong
    answer; as tuples, (0, 9, 0) < (0, 10, 0), the right one), the same
    choice update_check.py's _resolve_update_status already makes for the
    same reason. None on a shape this does not recognise (a placeholder
    like '0.0.0-dev', or a hand-edited pack.toml) is not an error here: the
    caller treats it the same as absent metadata, no notice, never a
    guessed comparison.
    """
    match = _VERSION_TUPLE_RE.match(value.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _pack_requires_tool_notice(meta: RunMeta) -> str:
    """A loud line when the loaded pack's optional pack.toml (issue #170)
    declares a requires_tool newer than the tool version that produced this
    run.

    Absent metadata (``rules_pack_requires_tool`` is None: no pack.toml, an
    unreadable one, one silent on this key, or a run-state file that
    predates this field) renders nothing and claims nothing, matching
    _provenance_blind_notice's and _modified_tool_notice's None handling: an
    unasserted requirement is not evidence of anything. An unparseable
    requires_tool or tool_version (see :func:`_parse_version_tuple`) is
    treated the same way: silently no notice, never a crash and never a
    guessed comparison.

    Matches the other two notices' register: names both versions, says only
    what was checked, and never claims the findings below are wrong.
    """
    requires_tool = meta.rules_pack_requires_tool
    if requires_tool is None:
        return ""
    required = _parse_version_tuple(requires_tool)
    running = _parse_version_tuple(meta.tool_version)
    if required is None or running is None or required <= running:
        return ""
    return (
        '<div class="perf-block prominent">'
        "<h3>This pack asks for a newer tool</h3>"
        "<p>The rules pack's pack.toml declares "
        f'<code>requires_tool = "{_esc(requires_tool)}"</code>, and this run used '
        f"engineering-audit {_esc(meta.tool_version)}. That is not evidence the findings "
        "below are wrong, only that this pack's rules were written assuming tool "
        "behaviour this build may predate.</p>"
        "</div>"
    )


def _pack_format_notice(meta: RunMeta) -> str:
    """A loud line when the loaded pack's optional pack.toml (issue #170)
    declares a rule-file format outside PACK_FORMAT_MIN..PACK_FORMAT_MAX,
    the range this build's parser actually reads (rules.py, next to
    is_v2).

    Same None handling and register as _pack_requires_tool_notice: absent
    metadata (``rules_pack_format`` is None) renders nothing, and the
    notice never claims the findings below are wrong, only that this build
    may not understand the pack's rule-file shape as its authors intended.
    """
    fmt = meta.rules_pack_format
    if fmt is None or PACK_FORMAT_MIN <= fmt <= PACK_FORMAT_MAX:
        return ""
    return (
        '<div class="perf-block prominent">'
        "<h3>This pack declares an unreadable rule-file format</h3>"
        f"<p>The rules pack's pack.toml declares <code>format = {fmt}</code>, and this "
        f"tool reads format {PACK_FORMAT_MIN} to {PACK_FORMAT_MAX}. That is not evidence "
        "the findings below are wrong, only that this build may not understand this "
        "pack's rule-file shape as its authors intended.</p>"
        "</div>"
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

    # Derived from the same map the table's Rules fetched column is built
    # from, so a domain cannot read "no" in the table and be absent from the
    # callout below, or the reverse (issue #123).
    status = _rules_fetched_status(run_state, selected)
    unknown = [d for d in considered if status[d] == "not recorded"]
    missing = [d for d in considered if status[d] == "no"]

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
    """The host CLI and version the calling assistant said it was running in.

    Marked self-reported for the same reason the assistant and model rows are
    (issue #176): every value here is supplied by the caller and none of it is
    checkable from this server. It is the same class of claim, so it carries
    the same qualifier rather than a different one that would imply a
    different standard of evidence.
    """
    environment = run_state.meta.environment
    if not environment:
        return "<p>No environment information reported for this run.</p>"
    rows = "".join(
        f"<li><strong>{_esc(key)}:</strong> {_esc(value)}</li>"
        for key, value in environment.items()
    )
    return (
        f'<p class="muted">Every value below is {_esc(SELF_REPORTED_MARKER)}: '
        "the calling assistant supplied it and nothing here can verify it.</p>"
        f"<ul>{rows}</ul>"
    )


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

    The citation is our own text from the rules pack, not assistant text,
    but the pack's own authoring convention wraps a footer (and any nested
    title inside it) in markdown emphasis (issue #128): stripped here,
    immediately after citation() returns, so the length ceiling below is
    measured against what a reader actually sees, never inflated by
    markup characters that carry no information.
    """
    if not rule.source:
        raise ReportError(
            f"finding references rule {rule.id}, which has no cited source in the "
            "rules pack. A finding is a published claim; this tool does not publish "
            "claims without evidence. Fix the rule's Source: footer or drop the finding."
        )
    cited = strip_markdown_emphasis(citation(rule.source, pack_is_v2=pack_is_v2))
    if len(cited) > _MAX_REFERENCE_LENGTH:
        raise ReportError(
            f"finding references rule {rule.id}, whose citation is {len(cited)} "
            f"characters long, over the {_MAX_REFERENCE_LENGTH}-character reference "
            "ceiling. A finding's reference line must stay publishable; fix the rule's "
            "Source: (or Verification:) footer rather than shipping an oversized reference."
        )
    return f"Reference: {rule.id}: {cited}"


def _finding_domain_note_html(
    confidence: str | None,
    rules_fetched: bool | None,
    unevaluated: tuple[int, int] | None = None,
) -> str:
    """The muted line under a finding card's location: this finding's
    domain confidence and rules-fetched status (issue #130).

    Rendered in full contrast and bold, not muted, when rules_fetched is
    False: that is the one state the Tool performance summary already
    calls out as "unsupported" (the Rules fetched block), and a finding
    from such a domain must not read as visually identical to one whose
    rules were fetched. Weight carries the emphasis rather than colour, the
    same convention _severity_cell uses for a nonzero critical/high count,
    so it survives greyscale and a photocopy (D16-R16).
    """
    note = _esc(domain_confidence_note(confidence, rules_fetched, unevaluated))
    if rules_fetched is False:
        return f'<div class="finding-domain-note"><strong>{note}</strong></div>'
    return f'<div class="finding-domain-note muted">{note}</div>'


def _finding_precondition_html(precondition: str | None) -> str:
    """The line stating the precondition that makes this rule apply here
    (issue #178).

    A finding is two claims welded together: the rule says do X, which the
    reference line below vouches for, and this repository does not do X, which
    nothing vouches for. Neither of those is the claim that failed in the run
    that produced #178. What failed was a third claim, left silent: that the
    rule applies here at all. Eleven findings cited real standards, quoted them
    correctly, and applied them to a one-person pre-release tool with no
    release pipeline and no external users.

    So the precondition is printed on the card, above the body, in the same
    reading position as the location: both answer "why should I believe this
    finding is about my repository". When it was never recorded, that is
    printed too, in the same words the domain note uses for a missing
    confidence, rather than the line simply being absent: a card with nothing
    where the precondition goes reads as a card that had nothing to say.
    """
    if precondition and precondition.strip():
        return (
            '<div class="finding-precondition">Applies here because: '
            f"{_esc(precondition.strip())}</div>"
        )
    return (
        '<div class="finding-precondition muted">'
        "No precondition recorded: this finding does not say what makes its rule "
        "apply to this repository.</div>"
    )


def _finding_evidence_boundary_html(uninspected_evidence: list[str] | None) -> str:
    """The scope caveat carried onto every finding from a domain that read
    less than the repository points at (issue #179).

    Deliberately rendered on all of the domain's findings rather than on the
    ones this code guesses are absence claims. Whether "no acceptance criteria
    exist" is refuted by an issue tracker nobody opened is a judgement about
    the finding's content, and a classifier here would be the tool pre-writing
    that verdict on evidence it does not have (hardening rule 12). The reader
    has the finding and the boundary side by side and can make the call; the
    tool's job is to stop the boundary being invisible, which is what it was.

    Silent when the domain recorded an empty list, which is the common case and
    means the repository points at nothing the audit did not read. Silent too
    when the field is None: that is a pre-0.9.0 run, and the Tool performance
    summary already reports the field as never recorded for the run as a whole,
    so repeating it on every card would be noise.
    """
    if not uninspected_evidence:
        return ""
    items = "".join(f"<li>{_esc(entry.strip())}</li>" for entry in uninspected_evidence)
    return (
        '<div class="finding-boundary"><strong>Reached without reading:</strong> '
        "this domain recorded evidence the repository points at that the audit did "
        f"not open.<ul>{items}</ul></div>"
    )


def _finding_card(
    domain_title: str,
    finding: Finding,
    rule_index: dict[str, Rule],
    pack_is_v2: bool,
    confidence: str | None,
    rules_fetched: bool | None,
    uninspected_evidence: list[str] | None = None,
    unevaluated: tuple[int, int] | None = None,
) -> str:
    severity = finding.severity.value
    badge = f'<span class="severity-badge severity-{_esc(severity)}">{_esc(severity)}</span>'
    # render_report has already confirmed every finding's rule_id is in the
    # pack, so this lookup cannot miss.
    rule = rule_index[finding.rule_id]
    # finding.title is assistant-authored, same as body_md; stripped for the
    # same reason (issue #128), just without _markdownish's paragraph
    # wrapping, since a card heading is one line, not prose.
    title = strip_markdown_emphasis(finding.title)
    return (
        f'<div class="finding sev-{_esc(severity)}">'
        f'<div class="finding-head">{badge} <strong>{_esc(title)}</strong> '
        f'<span class="finding-rule">({_esc(finding.rule_id)})</span> '
        f'<span class="finding-domain">{_esc(domain_title)}</span></div>'
        f'<div class="finding-location">{_esc(finding.location)}</div>'
        f"{_finding_precondition_html(finding.precondition)}"
        f"{_finding_evidence_boundary_html(uninspected_evidence)}"
        f"{_finding_domain_note_html(confidence, rules_fetched, unevaluated)}"
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

    Collapsed behind a summary that carries the split three ways (issue
    #124), because "Domains with no findings: 5 of 16" on its own is exactly
    the sentence that hid the difference in the first place. The summary is
    the signal; the domain names and reasons behind it are the evidence, and
    every domain id in there also appears in the per-domain table, which
    never collapses.
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

    not_run = sum(1 for _, result in quiet if result.status == "could-not-run")
    set_aside = sum(1 for domain_id, _ in quiet if domain_id in fully_not_applicable)
    clean = len(quiet) - not_run - set_aside

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

    summary = (
        f"Domains with no findings: {len(quiet)} of {len(selected)}. "
        f"{clean} audited and clean, {set_aside} with every rule set aside as not "
        f"applicable, {not_run} that did not run at all."
    )
    return (
        f"<h3>Domains with no findings: {len(quiet)} of {len(selected)}</h3>"
        f"<details><summary>{_esc(summary)}</summary>"
        f'<ul class="quiet-domains">{"".join(rows)}</ul>'
        "</details>"
    )


def _findings_section(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    rule_index: dict[str, Rule],
    pack_is_v2: bool,
    fetch_status: dict[str, str],
    confidence_map: dict[str, str | None],
    unevaluated_map: dict[str, tuple[int, int] | None],
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

    ``fetch_status`` (from :func:`_rules_fetched_status`) and
    ``confidence_map`` (domain id -> ``self_assessment.confidence`` or
    ``None``) carry each finding's own domain confidence and fetch status
    onto its card (issue #130), computed once by the caller and shared with
    the per-domain table and the Issues section so none of the three can
    disagree about the same domain.
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
            _finding_card(
                domain_titles[domain_id],
                finding,
                rule_index,
                pack_is_v2,
                confidence_map[domain_id],
                _fetch_status_to_bool(fetch_status[domain_id]),
                selected[domain_id].uninspected_evidence,
                unevaluated_map[domain_id],
            )
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
    fetch_status: dict[str, str],
    confidence_map: dict[str, str | None],
    unevaluated_map: dict[str, tuple[int, int] | None],
) -> str:
    """``fetch_status``, ``confidence_map`` and ``unevaluated_map`` are the
    same per-domain maps _findings_section takes (issues #130 and #211):
    they carry each finding's domain confidence, fetch status and the
    could-not-evaluate base that confidence rests on into its
    filed-or-copied issue text via build_issue_trailing_line, and the first
    two decide which critical/high findings are excluded from the default
    pre-tick below.
    """
    all_findings = [
        (domain_id, finding)
        for domain_id, result in selected.items()
        for finding in result.findings
    ]
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
    filed_urls: list[str | None] = []
    for index, (domain_id, finding) in enumerate(all_findings):
        # render_report has already confirmed every finding's rule_id is in
        # the pack and carries a cited source, so this lookup and the
        # trailing-line build below cannot fail.
        rule = rule_index[finding.rule_id]
        rules_fetched = _fetch_status_to_bool(fetch_status[domain_id])
        trailing_line = build_issue_trailing_line(
            finding,
            rule,
            confidence=confidence_map[domain_id],
            rules_fetched=rules_fetched,
            unevaluated=unevaluated_map[domain_id],
        )
        # issue_title and issue_body are assistant-authored and untrusted,
        # same as body_md; stripped here for the same reason (issue #128) so
        # a filed or copied issue reads as plain prose, never leaking the
        # "**Suggested fix**"-style markdown an assistant defaults to.
        issue_title = strip_markdown_emphasis(finding.issue_title)
        issue_body = strip_markdown_emphasis(finding.issue_body)
        body_with_trailing = f"{issue_body}\n\n{trailing_line}"
        full_text = f"{issue_title}\n\n{body_with_trailing}"

        issues_data.append(
            {
                "rule_id": finding.rule_id,
                "title": issue_title,
                "body": body_with_trailing,
            }
        )

        seen[finding.rule_id] += 1
        finding_key = f"{finding.rule_id}#{seen[finding.rule_id]}"
        filed_url = issue_urls.get(finding_key)
        filed_urls.append(filed_url)
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
            #
            # A critical or high finding from a domain whose rules were
            # never fetched this run is excluded from that pre-tick too
            # (issue #130): rules_fetched is False composes with the
            # severity check rather than replacing it, so an unfetched
            # critical finding ends up unticked even though its severity
            # alone would otherwise tick it. See
            # test_unfetched_critical_finding_is_unticked_despite_severity.
            checked = (
                " checked"
                if finding.severity.value in _PRETICKED_SEVERITIES
                and rules_fetched is not False
                else ""
            )
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}"{checked} '
                'onchange="updateGithubFileButtonLabel()">'
            )

        blocks.append(
            '<div class="issue-block">'
            f'<label class="issue-select">{checkbox_html}</label>'
            f"<p><strong>{_esc(issue_title)}</strong></p>"
            f'<textarea id="{textarea_id}" readonly rows="6">{_esc(full_text)}</textarea>'
            f'<button type="button" onclick="copyIssueText(\'{textarea_id}\', this)">'
            "Copy issue text</button> "
            f'<span class="issue-status" id="{status_id}"></span>'
            "</div>"
        )

    # A finding that was already filed this run renders as a disabled,
    # unticked "already filed" link above (see the `if filed_url:` branch),
    # regardless of severity or fetch status. Both counts below must exclude
    # it too, or the note claims a box is ticked (or unticked-for-being-
    # unfetched) that is actually unticked-for-being-filed (issue #154).
    preticked = sum(
        1
        for (domain_id, f), filed_url in zip(all_findings, filed_urls)
        if f.severity.value in _PRETICKED_SEVERITIES
        and fetch_status[domain_id] != "no"
        and not filed_url
    )
    unfetched_high_priority = sum(
        1
        for (domain_id, f), filed_url in zip(all_findings, filed_urls)
        if f.severity.value in _PRETICKED_SEVERITIES
        and fetch_status[domain_id] == "no"
        and not filed_url
    )
    # The verb agrees with the raw ticked count ("3 of 7 issues are", "1 of 7
    # issues is"), except when there is only one issue in total: "1 issue"
    # is then the whole subject, a single thing that either is or is not
    # ticked, so the verb stays singular even when preticked is 0 ("0 of 1
    # issue is ticked", never "...issue are ticked"). Excluding filed
    # findings from preticked (issue #154) made that zero case reachable
    # for the first time, which is what surfaced this.
    ticked_verb_count = 1 if len(all_findings) == 1 else preticked
    note_text = (
        f"{preticked} of {len(all_findings)} {_plural(len(all_findings), 'issue')} "
        f"{_plural(ticked_verb_count, 'is', 'are')} ticked: the critical and high "
        "findings. The medium and low ones are listed unticked, not hidden. Tick any "
        "you also want to file, or untick one you do not. An issue already filed "
        "this run is shown unticked with a link to it."
    )
    if unfetched_high_priority:
        note_text += (
            f" {unfetched_high_priority} critical or high "
            f"{_plural(unfetched_high_priority, 'finding')} from a domain whose rules "
            f"were never fetched this run {_plural(unfetched_high_priority, 'is', 'are')} "
            "listed unticked too: treat them as unsupported until the domain is redone."
        )
    selection_note = f'<p class="muted">{note_text}</p>'

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


# Wording for each consent-gated section's checkbox, keyed by the same name
# build_feedback_sections uses. This is the one place left where a new
# section's label text is hand-written, and it has to be: the copy carries
# nuance (consulted_sources' privacy caveat, duration's note about token
# counts) that cannot be derived from the key name. Everything else about a
# section, whether it exists at all, its checkbox id, and whether it is
# ticked, is derived from build_feedback_sections' own keys and
# TelemetryConsent below (issue #120), so a key present in one and missing
# from this dict fails loudly with a KeyError at render time rather than
# silently rendering without a label.
#
# Same wording as the configuration page's consent section, so a user who
# saw one recognises the other.
_CONSENT_LABELS: dict[str, str] = {
    "coverage": "Coverage statistics (files inspected, files skipped)",
    "rollup": "Findings rollup (counts by severity and domain, not the finding text)",
    "self_assessment": "Self assessment (confidence and limits per domain)",
    "environment": "Environment information (assistant, model, tool version)",
    "consulted_sources": (
        "Send fetched references to the maintainer (rule id, URL and why, for each "
        "source consulted outside the rules pack). Off by default: URLs fetched while "
        "auditing a private repository can hint at what that repository is about."
    ),
    "verdict_distribution": (
        "Rule verdict distribution (counts of pass, finding, not-applicable and "
        "could-not-evaluate, per domain and in total; not the finding text)"
    ),
    "duration": (
        "Run duration (the assistant-reported span, the server-measured span, and "
        "whether they agree). Token counts are not included, since the server never "
        "sees them: paste them into the feedback text above if you want to share them."
    ),
    "rules_fetched": (
        "Rules fetched (per domain, whether this run's rule text was fetched via "
        "get_domain. Shows only that it was fetched, never that it was read or applied.)"
    ),
    "reader_conclusions": (
        "Your own conclusions after reading this report (what it told you, in one "
        "sentence, and what you would fix first; answered below, in your own words, "
        "not derived from anything the tool recorded)"
    ),
}


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
    # Every section build_feedback_sections returns except run_metadata
    # (always included, never a consent choice) is consent-gated. This list
    # drives both the consent checkboxes below and the JS payload loop in
    # static/report.js (issue #120): a section added to build_feedback_sections
    # without a matching TelemetryConsent flag surfaces here as an
    # AttributeError from getattr(consent, key) below, rather than silently
    # rendering with no way to consent to it, or being read by the client-side
    # payload builder without a checkbox to gate it.
    consent_keys = [key for key in sections if key != "run_metadata"]
    feedback_data = {
        "email": FEEDBACK_EMAIL,
        "subject": feedback_subject(run_state.meta),
        "consent_keys": consent_keys,
        **sections,
    }

    consent_rows = (
        "".join(
            _consent_row(
                f"consent-{key.replace('_', '-')}",
                _CONSENT_LABELS[key],
                getattr(consent, key),
            )
            for key in consent_keys
        )
        + '<label class="consent-row locked"><input type="checkbox" checked disabled> '
        "Run metadata (always included when sending feedback)</label>"
    )

    return (
        f"{filed_html}"
        '<div class="feedback-form">'
        '<div class="reader-conclusions">'
        "<p>Answering these two is optional, and tells the tool author whether the report "
        'itself worked, not just how the run went. Tick "Your own conclusions" below to '
        "include them if you answer.</p>"
        '<label for="reader-conclusion-headline">In one sentence, what did this report '
        "tell you about your repository?</label>"
        '<textarea id="reader-conclusion-headline" class="reader-conclusion-textarea" '
        'rows="2"></textarea>'
        '<label for="reader-conclusion-fix-first">What would you fix first?</label>'
        '<textarea id="reader-conclusion-fix-first" class="reader-conclusion-textarea" '
        'rows="2"></textarea>'
        "</div>"
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

    # Computed once and threaded through the findings and issues sections
    # (issue #130), the same way the per-domain table already computes and
    # shows both: a finding card, its filed-issue text and the Issues
    # section's pre-tick state must all agree with the table about the same
    # domain's confidence and fetch status.
    fetch_status = _rules_fetched_status(run_state, selected)
    confidence_map: dict[str, str | None] = {
        domain_id: (
            result.self_assessment.confidence if result.self_assessment else None
        )
        for domain_id, result in selected.items()
    }
    # Issue #211: the base each confidence claim is rendered against, from
    # the one helper the per-domain table also reads, so the table, the
    # finding cards and the filed-issue text cannot disagree about how much
    # of a domain went unevaluated.
    unevaluated_map = _unevaluated_map(selected)

    performance_summary = (
        f"{_provenance_blind_notice(run_state.meta)}"
        f"{_stale_build_notice(run_state.meta)}"
        f"{_modified_tool_notice(run_state.meta)}"
        f"{_pack_requires_tool_notice(run_state.meta)}"
        f"{_pack_format_notice(run_state.meta)}"
        f'<div class="perf-block"><h3>Every domain, side by side</h3>'
        f"{_domain_table(run_state, selected, domain_titles, all_findings)}</div>"
        f'<div class="perf-block"><h3>Run totals</h3>'
        f"{_findings_rollup(all_findings, selected, run_state.meta.assistant, run_state.meta.model)}</div>"
        f'<div class="perf-block prominent">'
        f"{_evidence_boundary_list(selected, domain_titles)}</div>"
        f'<div class="perf-block prominent">'
        f"{_could_not_evaluate_list(selected, rule_index, domain_titles)}</div>"
        f'<div class="perf-block prominent">'
        f"{_not_applicable_list(selected, rule_index, domain_titles)}</div>"
        f'<div class="perf-block prominent">'
        f"{_rules_fetched_list(run_state, selected, domain_titles)}</div>"
        f'<div class="perf-block"><h3>Self-assessment limits</h3>'
        f"{_self_assessment_limits(selected, domain_titles)}</div>"
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
            selected,
            domain_titles,
            rule_index,
            pack.is_v2,
            fetch_status,
            confidence_map,
            unevaluated_map,
        ),
        issues_section=_issues_section(
            selected,
            rule_index,
            run_state.filed_issue_urls or None,
            repo_prefill,
            fetch_status,
            confidence_map,
            unevaluated_map,
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
