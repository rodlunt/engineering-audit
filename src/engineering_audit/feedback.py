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

import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote

from engineering_audit.rules import Rule, citation
from engineering_audit.schema import (
    DomainResult,
    Finding,
    RunMeta,
    TelemetryConsent,
    Verdict,
)

__all__ = [
    "FEEDBACK_REPO",
    "FEEDBACK_EMAIL",
    "feedback_subject",
    "rules_pack_label",
    "duration_text",
    "strip_markdown_emphasis",
    "domain_confidence_note",
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
_VERDICT_ORDER = (
    Verdict.pass_,
    Verdict.FINDING,
    Verdict.NOT_APPLICABLE,
    Verdict.COULD_NOT_EVALUATE,
)

# Issue #128's decision: strip markdown emphasis markers rather than render
# them. Rendering untrusted assistant-authored finding text into HTML would
# need a mature CommonMark renderer plus a mature HTML sanitiser, two
# dependencies this project does not carry, for a gain that is purely
# cosmetic (bold text). Stripping is the safe, boring choice, but the first
# cut of it (blanket-removing every literal asterisk) was too blunt: this
# tool's whole output is claims about code, and code is full of legitimate,
# unpaired asterisks. A blanket strip turned "def handler(*args, **kwargs):"
# into "def handler(args, kwargs):", "SELECT * FROM users" into
# "SELECT  FROM users", and "rm -rf build/*" into "rm -rf build/", the last
# of which silently rewrites a shell command into a different shell command
# on its way into a filed GitHub issue. This is the boundary-guard strip
# hardened against that: it still adds no markdown library and still never
# turns anything into markup, but it now only removes an asterisk that is
# genuinely part of a matched emphasis pair, and never touches a code span.
_CODE_SPAN_RE = re.compile(r"(`+)(.*?)\1", re.DOTALL)
_ASTERISK_RUN_RE = re.compile(r"\*+")


def strip_markdown_emphasis(text: str) -> str:
    """Strip markdown emphasis, leaving code and unpaired asterisks alone
    (issue #128).

    Two safeguards, both required, neither sufficient alone:

    1. **Code spans are protected outright.** Anything between two matching
       runs of backticks (an inline `` `code span` `` or a fenced code
       block, which is the same shape with a longer backtick run) is never
       inspected by step 2 below. Without this, two unrelated code spans
       that each hold one multiplication asterisk, e.g.
       "`a * b` and `c * d`", would still get incorrectly paired with each
       other (both are length-1 runs), stripping an asterisk out of code
       that was never markdown.
    2. **Only a matched pair of same-length delimiter runs is stripped,
       never a lone run.** A naive per-character regex (``\\*(.+?)\\*``)
       matches the first '*' of a '**' run as the closing half of an
       earlier, unrelated single '*', so
       "def handler(*args, **kwargs):" corrupts to
       "def handler(args, *kwargs):": a run of length 1 (before "args")
       gets paired against the first character of the run of length 2
       (before "kwargs"), rather than the two runs being recognised as
       different delimiters. Pairing by matching run *length* instead (a
       single '*' can only close another single '*'; a '**' can only close
       another '**') leaves that line untouched entirely, along with a
       bare "SELECT * FROM users", "glob pattern **/*.py" and
       "rm -rf build/*": none of those contain two runs of the same
       length, so nothing in them is a genuine pair.

    This is still deliberately not a markdown parser: it does not resolve
    CommonMark's left/right-flanking rules, and a genuinely intended,
    unpaired double asterisk in prose is still lost, same as before. The
    safest parser is the one that is never run; this makes the boundary
    strip more conservative, not a step towards becoming a renderer.
    """
    protected_spans = [match.span() for match in _CODE_SPAN_RE.finditer(text)]

    def _is_protected(position: int) -> bool:
        return any(start <= position < end for start, end in protected_spans)

    runs = [
        match
        for match in _ASTERISK_RUN_RE.finditer(text)
        if not _is_protected(match.start())
    ]
    if not runs:
        return text

    # Nearest-neighbour pairing per run length, left to right: the first
    # unmatched run of a given length is a candidate opener; the next run
    # of that same length closes it. Two runs of different lengths never
    # pair with each other, which is the property the docstring above
    # depends on.
    pending: dict[
        int, int
    ] = {}  # run length -> index into `runs` of its open candidate
    drop: set[int] = set()  # indices into `runs` whose asterisks are removed
    for index, run in enumerate(runs):
        length = len(run.group())
        opener_index = pending.pop(length, None)
        if opener_index is None:
            pending[length] = index
        else:
            drop.add(opener_index)
            drop.add(index)
    if not drop:
        return text

    pieces = []
    cursor = 0
    for index in sorted(drop):
        run = runs[index]
        pieces.append(text[cursor : run.start()])
        cursor = run.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def feedback_subject(meta: RunMeta) -> str:
    """ "Feedback: audit run <date> (<assistant>)", used both as the filed
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


# A duration disagreement is flagged, never resolved: see duration_text's
# docstring for why neither figure is corrected in place.
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


def duration_text(meta: RunMeta) -> str:
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

    Used both by report.py's meta block, which shows this for every run
    regardless of consent, and by build_feedback_sections' duration section
    below, which is consent-gated: the same wording either way, so the two
    can never describe the same run's duration differently.
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


def _rules_fetched_state(
    domain_id: str,
    rules_fetched_domain_ids: list[str] | None,
    rules_fetch_unknown_domain_ids: list[str],
) -> bool | None:
    """Whether domain_id's rule text was fetched this run: True, False, or
    None when that cannot be known.

    Mirrors server.py's own _rules_fetched_state, which reads the same three
    states off a live RunTracker; this one reads them off the plain lists
    RunState and RunProgress carry (see RULES_FETCHED_FIELD_DESCRIPTION and
    RULES_FETCH_UNKNOWN_FIELD_DESCRIPTION on schema.py), since this module
    never sees a RunTracker.

    rules_fetched_domain_ids is None for a whole run saved before fetches
    were tracked at all: every domain's status is unknown then, regardless
    of rules_fetch_unknown_domain_ids, because there was nothing yet able to
    record it either way. Once fetches are tracked, a domain named in
    rules_fetch_unknown_domain_ids is one carried in from an earlier,
    untracked save across a resume; everything else is a plain fetched/not
    fetched answer.
    """
    if rules_fetched_domain_ids is None:
        return None
    if domain_id in rules_fetched_domain_ids:
        return True
    if domain_id in rules_fetch_unknown_domain_ids:
        return None
    return False


def build_feedback_sections(
    meta: RunMeta,
    domain_results: dict[str, DomainResult],
    *,
    rules_fetched_domain_ids: list[str] | None = None,
    rules_fetch_unknown_domain_ids: list[str] | None = None,
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

    rules_fetched_domain_ids and rules_fetch_unknown_domain_ids carry the
    same None-vs-empty-vs-populated contract RunState and RunProgress give
    them (see RULES_FETCHED_FIELD_DESCRIPTION on schema.py): omitting them
    (the default, None) is the honest choice for a caller with nothing to
    pass, since it renders exactly like a run that predates fetch tracking,
    which is the truth for such a caller.
    """
    rules_fetch_unknown_domain_ids = rules_fetch_unknown_domain_ids or []
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

    # Per-domain counts only, never summed across domains: each domain audits
    # the same repository from its own angle, so a file that sixteen domains
    # each independently declined to open would be counted as sixteen
    # separate skips, and the sum would inflate by roughly the domain count
    # (issue #87, carried over from the report to this feedback path by
    # issue #134). report.py's own _coverage_summary dropped the summed
    # "Total files..." figures for the same reason; this mirrors that
    # per-domain wording so the report a maintainer opens and the feedback
    # they receive describe coverage the same way.
    coverage_lines = []
    for domain_id, result in domain_results.items():
        if result.status == "could-not-run":
            coverage_lines.append(f"- {domain_id}: did not run")
        elif result.coverage is not None:
            note = f" ({result.coverage.note})" if result.coverage.note else ""
            coverage_lines.append(
                f"- {domain_id}: {result.coverage.files_inspected} file(s) inspected, "
                f"{result.coverage.files_skipped} skipped{note}"
            )
        else:
            coverage_lines.append(f"- {domain_id}: no coverage reported")
    coverage = "Coverage\n" + ("\n".join(coverage_lines) or "- No domains audited.")

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
            f"- {domain_id}: {domain_counts.get(domain_id, 0)}"
            for domain_id in domain_results
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
            self_assessment_lines.append(
                f"- {domain_id}: could not run, {result.reason}"
            )
        elif result.self_assessment is not None:
            sa = result.self_assessment
            limits = f" Limits: {sa.limits}." if sa.limits else ""
            self_assessment_lines.append(
                f"- {domain_id}: confidence {sa.confidence}.{limits}"
            )
        else:
            self_assessment_lines.append(f"- {domain_id}: no self-assessment reported")
    self_assessment = "Self-assessment by domain\n" + "\n".join(self_assessment_lines)

    environment = meta.environment or {}
    if environment:
        env_lines = "\n".join(f"- {key}: {value}" for key, value in environment.items())
    else:
        env_lines = "- No environment information reported for this run."
    environment_section = f"Environment\n{env_lines}"

    # Rule id, URL and the agent's one-line why only: consulted_sources also
    # carries a title and an accessed timestamp, but those are for the local
    # report to display, not for what leaves the machine here. Grouped by
    # rule id, the same way self-assessment and rollup are grouped by domain,
    # so a consented send is still readable finding-by-finding rather than
    # one flat list.
    consulted_source_lines: list[str] = []
    for result in domain_results.values():
        for source in result.consulted_sources:
            consulted_source_lines.append(
                f"- {source.rule_id}: {source.url} (why: {source.why})"
            )
    consulted_sources = (
        "Sources consulted\n" + "\n".join(consulted_source_lines)
        if consulted_source_lines
        else "Sources consulted\n- No sources were consulted outside the rules pack this run."
    )

    # Per-domain pass/finding/not-applicable/could-not-evaluate counts, plus
    # the run total: the single table that makes a thin run (many
    # not-applicable, few findings) look different from a thorough one,
    # rather than indistinguishable from it the way a findings-only rollup
    # is (issue #111). Contains counts of the tool's own vocabulary only, no
    # repository content, paths, URLs or finding text.
    verdict_totals: Counter[str] = Counter()
    verdict_domain_lines = []
    for domain_id, result in domain_results.items():
        if result.status == "could-not-run":
            verdict_domain_lines.append(f"- {domain_id}: could not run")
            continue
        domain_verdict_counts = Counter(rv.verdict.value for rv in result.rule_verdicts)
        verdict_totals.update(domain_verdict_counts)
        counts_text = ", ".join(
            f"{verdict.value} {domain_verdict_counts.get(verdict.value, 0)}"
            for verdict in _VERDICT_ORDER
        )
        verdict_domain_lines.append(f"- {domain_id}: {counts_text}")
    verdict_totals_lines = "\n".join(
        f"- {verdict.value}: {verdict_totals.get(verdict.value, 0)}"
        for verdict in _VERDICT_ORDER
    )
    verdict_distribution = (
        "Rule verdict distribution\n"
        f"Total verdicts: {sum(verdict_totals.values())}\n"
        f"By verdict:\n{verdict_totals_lines}\n"
        "By domain:\n" + ("\n".join(verdict_domain_lines) or "- No domains audited.")
    )

    # Both spans and the divergence verdict between them, in the exact
    # wording report.py's always-shown "Duration" meta row uses: the two
    # must never describe the same run's duration differently just because
    # one is consent-gated and the other is not.
    duration = f"Duration\n{duration_text(meta)}"

    # Whether this run's rule text was FETCHED via get_domain for each
    # domain, never whether it was read or applied (issue #110's wording
    # discipline, carried over rather than loosened here): an agent can
    # fetch every domain, discard the text and bulk-mark anyway, and this
    # section will still look clean. "unrecorded" is its own state, never
    # collapsed into "not fetched": see _rules_fetched_state above.
    rules_fetched_lines = []
    for domain_id, result in domain_results.items():
        if result.status == "could-not-run":
            rules_fetched_lines.append(f"- {domain_id}: did not run")
            continue
        state = _rules_fetched_state(
            domain_id, rules_fetched_domain_ids, rules_fetch_unknown_domain_ids
        )
        if state is True:
            rules_fetched_lines.append(f"- {domain_id}: fetched")
        elif state is False:
            rules_fetched_lines.append(f"- {domain_id}: not fetched")
        else:
            rules_fetched_lines.append(f"- {domain_id}: unrecorded")
    rules_fetched = (
        "Rules fetched\n"
        "Shows only that rule text was served by get_domain for each domain this run, "
        "never that it was read or applied.\n"
        + ("\n".join(rules_fetched_lines) or "- No domains audited.")
    )

    return {
        "run_metadata": run_metadata,
        "coverage": coverage,
        "rollup": rollup,
        "self_assessment": self_assessment,
        "environment": environment_section,
        "consulted_sources": consulted_sources,
        "verdict_distribution": verdict_distribution,
        "duration": duration,
        "rules_fetched": rules_fetched,
    }


def build_feedback_body(
    free_text: str | None,
    meta: RunMeta,
    consent: TelemetryConsent,
    domain_results: dict[str, DomainResult],
    *,
    rules_fetched_domain_ids: list[str] | None = None,
    rules_fetch_unknown_domain_ids: list[str] | None = None,
) -> str:
    """Build the plain-text feedback body: the user's free text (if any),
    then the always-included run-metadata section, then each consented
    telemetry section in turn. A section the user did not consent to is
    omitted entirely, not included empty: an omission is the only way an
    unconsented section can be told apart from a consented one that simply
    had nothing to report.
    """
    sections_by_name = build_feedback_sections(
        meta,
        domain_results,
        rules_fetched_domain_ids=rules_fetched_domain_ids,
        rules_fetch_unknown_domain_ids=rules_fetch_unknown_domain_ids,
    )

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
    if consent.consulted_sources:
        sections.append(sections_by_name["consulted_sources"])
    if consent.verdict_distribution:
        sections.append(sections_by_name["verdict_distribution"])
    if consent.duration:
        sections.append(sections_by_name["duration"])
    if consent.rules_fetched:
        sections.append(sections_by_name["rules_fetched"])

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


def _confidence_clause(confidence: str | None) -> str:
    return (
        f"self-assessed confidence {confidence}"
        if confidence
        else "no self-assessed confidence reported"
    )


def _rules_fetched_clause(rules_fetched: bool | None) -> str:
    # Wording discipline carried over from issue #110, unchanged here: this
    # says only that the rule text was served by get_domain, never that it
    # was read or applied, in either direction. A domain that fetched its
    # rules and guessed anyway would read the same as one that read them
    # properly; this clause cannot and does not tell the two apart.
    if rules_fetched is True:
        return "its rule text was fetched from the server this run"
    if rules_fetched is False:
        return (
            "its rule text was never fetched from the server this run: treat "
            "this finding as unsupported until the domain is redone"
        )
    return "whether its rule text was fetched this run is not recorded"


def domain_confidence_note(confidence: str | None, rules_fetched: bool | None) -> str:
    """One line carrying a finding's domain confidence and rules-fetched
    status onto the finding itself (issue #130).

    The report's Tool performance summary states a domain's confidence and
    fetch status once, and until now a finding card or filed issue never
    repeated either: a low-confidence finding from a domain whose rules
    were never fetched rendered identically to a high-confidence one whose
    rules were. Shared by report.py's per-finding card and
    build_issue_trailing_line below, so a finding's report card and its
    filed-issue text can never describe the same domain differently.
    """
    return f"This finding's domain: {_confidence_clause(confidence)}; {_rules_fetched_clause(rules_fetched)}."


def build_issue_trailing_line(
    finding: Finding,
    rule: Rule,
    *,
    confidence: str | None = None,
    rules_fetched: bool | None = None,
) -> str:
    """Build the trailing attribution line appended after every filed or
    copyable issue body: "Found by an engineering-practice audit (rule
    <id>, severity <sev>, at <loc>). [<domain confidence note>.] Reference:
    <capped citation>".

    This is the single place that wording is built. `server.py`'s
    `file_issues` (issues filed via the user's own `gh` CLI) and
    `report.py`'s issues section (issues copied from, or filed via a PAT
    from, the rendered report) both call this, so a filed issue and its
    in-report copy text can never describe the same finding differently.

    ``confidence`` and ``rules_fetched`` are the finding's own domain's
    :attr:`SelfAssessment.confidence` and fetch status (issue #130): pass
    both to carry a domain-confidence note into the built line, or leave
    both at their default ``None`` to omit it entirely, which reproduces
    this function's pre-#130 output byte for byte. Passing only one is
    still honoured (the note names the other as not reported/not recorded)
    since a caller that has one may not have the other.
    """
    if not rule.source:
        raise ValueError(
            f"finding references rule {rule.id}, which has no cited source in the "
            "rules pack. A finding is a published claim; this tool does not publish "
            "claims without evidence."
        )
    parts = [
        f"Found by an engineering-practice audit (rule {finding.rule_id}, severity "
        f"{finding.severity.value}, at {finding.location})."
    ]
    if confidence is not None or rules_fetched is not None:
        parts.append(domain_confidence_note(confidence, rules_fetched))
    # Issue #128: a citation copied from the rules pack can carry markdown
    # emphasis (nested *italic* titles, or a whole footer wrapped in
    # asterisks); this is the boundary where it is stripped before
    # publication, same as report.py's own _reference_line.
    parts.append(f"Reference: {strip_markdown_emphasis(citation(rule.source))}")
    return " ".join(parts)
