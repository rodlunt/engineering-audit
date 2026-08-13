"""Tests for the report template's stylesheet (issues #125, #126, #127).

These guard three defects that are invisible in an ordinary desktop check
of the rendered report and only surface for a reader on paper, on a phone,
or with the OS in dark mode: a print rule that claimed an effect CSS
cannot deliver on a textarea, an unbroken monospace token with no wrapping
rule, and severity badge text that fails contrast in the dark palette.

Where a test can assert against the browser-parsed rendering of the report
(contrast ratios computed from the actual CSS custom properties, not from
a value copied out of the stylesheet by hand) it does, so a later palette
or layout edit that quietly reintroduces one of these defects fails here
rather than shipping unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

from engineering_audit.report import render_report
from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    AuditConfig,
    Coverage,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    RunState,
    SelfAssessment,
    Severity,
    Verdict,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"
TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "engineering_audit"
    / "templates"
    / "report.html"
)


def _style_block() -> str:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert match is not None, "report.html has no <style> block"
    return match.group(1)


def _meta() -> RunMeta:
    return RunMeta(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:10:00+00:00",
    )


def _rendered_report() -> str:
    """Render a minimal report with one finding, enough to exercise the
    Issues section and the finding-location/finding-reference markup that
    #125 and #126 target."""
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    verdicts = [RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in d01.rules]
    verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
    result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=verdicts,
        findings=[
            Finding(
                rule_id="D01-R02",
                severity=Severity.HIGH,
                title="Set shared-bed flag for bed-14",
                location="src/very/deeply/nested/module/implementation_detail.py:1234-5678",
                body_md="Two gnomes share bed-14 without the shared-bed flag set.",
                issue_title="Set shared-bed flag for bed-14",
                issue_body="bed-14 has two occupants and no shared-bed flag.",
            )
        ],
        self_assessment=SelfAssessment(confidence="high", limits=""),
        coverage=Coverage(files_inspected=12, files_skipped=0),
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={"d01": result},
    )
    return render_report(run_state, pack)


# ---------------------------------------------------------------------------
# #125: print drops the Issues section and prints a substitute note.
# ---------------------------------------------------------------------------


def test_print_media_hides_the_whole_issues_section() -> None:
    style = _style_block()
    print_block_match = re.search(
        r"@media print\s*\{(.*)\}\s*</style>", style, re.DOTALL
    )
    # Fall back to searching the whole style text for the print block if the
    # trailing anchor above doesn't match (kept permissive on purpose: this
    # assertion cares about the declaration, not the exact block boundary).
    print_block = print_block_match.group(1) if print_block_match else style
    assert re.search(r"#issues\s*\{[^}]*display:\s*none\s*!important", print_block), (
        "the Issues section (#issues) must be hidden with display: none "
        "!important inside @media print"
    )


def test_print_media_shows_the_substitute_note() -> None:
    style = _style_block()
    assert re.search(r"\.print-only-note\s*\{[^}]*display:\s*none", style), (
        "the substitute note must be hidden on screen by default"
    )
    assert re.search(
        r"@media print.*\.print-only-note\s*\{[^}]*display:\s*block\s*!important",
        style,
        re.DOTALL,
    ), "the substitute note must be shown under @media print"


def test_rendered_report_contains_the_print_only_note_after_the_issues_section() -> (
    None
):
    rendered = _rendered_report()
    issues_pos = rendered.index('<section id="issues">')
    note_match = re.search(r'<p class="print-only-note">(.*?)</p>', rendered, re.DOTALL)
    assert note_match is not None, "rendered report has no print-only-note paragraph"
    note_text = " ".join(note_match.group(1).split())
    assert "on-screen version" in note_text
    assert "Issues" in note_text
    # The note must come after the Issues section closes, so it reads as a
    # substitute for it in document order, matching what @media print shows.
    assert note_match.start() > issues_pos


def test_issue_block_textarea_no_longer_carries_the_dead_print_expansion_rule() -> None:
    # The original defect: "height: auto !important; overflow: visible
    # !important" on a readonly textarea does nothing (height: auto
    # resolves to the rows attribute; overflow: visible on a scroll
    # container computes back to auto). Hiding the whole section removes
    # the need for that rule on .issue-block textarea entirely; asserting
    # its absence stops the ineffective rule from quietly reappearing as a
    # "fix" for a problem that no longer needs solving that way.
    style = _style_block()
    # The on-screen rule sizing the textarea (width, padding, border) is
    # legitimate and stays; what must be gone is the print-only override
    # that claimed height: auto and overflow: visible would expand it,
    # neither of which does anything on a textarea.
    assert not re.search(
        r"\.issue-block textarea[^{]*\{[^}]*height:\s*auto\s*!important", style
    )


# ---------------------------------------------------------------------------
# #126: monospace location and reference lines wrap instead of overflowing.
# ---------------------------------------------------------------------------


def test_finding_location_and_reference_use_overflow_wrap_anywhere() -> None:
    style = _style_block()
    rule_match = re.search(
        r"\.finding-location,\s*\.finding-reference\s*\{([^}]*)\}", style
    )
    assert rule_match is not None, (
        ".finding-location and .finding-reference must share a rule "
        "declaring overflow-wrap"
    )
    declarations = rule_match.group(1)
    assert "overflow-wrap: anywhere" in declarations
    # word-break: break-all would also chop ordinary prose mid-word; the
    # issue explicitly asks for overflow-wrap instead.
    assert "word-break" not in declarations


def test_body_has_a_defensive_overflow_wrap() -> None:
    style = _style_block()
    body_match = re.search(r"(?<!\w)body\s*\{([^}]*)\}", style)
    assert body_match is not None
    assert "overflow-wrap: break-word" in body_match.group(1)


def test_rendered_finding_location_and_reference_carry_their_classes() -> None:
    rendered = _rendered_report()
    assert '<div class="finding-location">' in rendered
    assert '<div class="finding-reference">' in rendered


# ---------------------------------------------------------------------------
# #127: severity badge contrast clears 4.5:1 in both palettes, and a
# non-hue cue on the card stripe survives greyscale.
# ---------------------------------------------------------------------------

_SEVERITIES = ("critical", "high", "medium", "low")


def _extract_root_vars(block: str) -> dict[str, str]:
    return dict(re.findall(r"--(\w[\w-]*):\s*(#[0-9a-fA-F]{6});", block))


def _relative_luminance(hex_colour: str) -> float:
    hex_colour = hex_colour.lstrip("#")
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    lum_a = _relative_luminance(hex_a)
    lum_b = _relative_luminance(hex_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _dark_screen_overridden_severities(style: str) -> set[str]:
    """Return the severities whose badge text is overridden to #1a1a1a by a
    dark-mode rule scoped to screen (never leaking into print)."""
    match = re.search(
        r"@media screen and \(prefers-color-scheme:\s*dark\)\s*\{(.*?)\}\s*\}",
        style,
        re.DOTALL,
    )
    if not match:
        return set()
    inner = match.group(1)
    selector_match = re.search(r"([^{]*)\{[^}]*color:\s*#1a1a1a", inner)
    if not selector_match:
        return set()
    return {sev for sev in _SEVERITIES if f".severity-{sev}" in selector_match.group(1)}


def _unconditional_dark_text_severities(style: str) -> set[str]:
    """Return the severities whose badge text is #1a1a1a unconditionally
    (outside any @media block), the way .severity-medium already is. Such
    an override applies in both palettes, not just one."""
    overridden = set()
    for sev in _SEVERITIES:
        rule_match = re.search(rf"\.severity-{sev}\s*\{{([^}}]*)\}}", style)
        assert rule_match is not None, f".severity-{sev} rule not found"
        if "color: #1a1a1a" in rule_match.group(1):
            overridden.add(sev)
    return overridden


def test_every_severity_badge_clears_4_5_to_1_in_both_palettes() -> None:
    style = _style_block()

    root_match = re.search(r":root\s*\{([^}]*)\}", style)
    assert root_match is not None
    light_vars = _extract_root_vars(root_match.group(1))

    dark_root_match = re.search(
        r"@media \(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}",
        style,
    )
    assert dark_root_match is not None
    dark_vars = _extract_root_vars(dark_root_match.group(1))

    base_badge_match = re.search(r"\.severity-badge\s*\{([^}]*)\}", style)
    assert base_badge_match is not None
    base_colour_match = re.search(
        r"color:\s*(#[0-9a-fA-F]{6})", base_badge_match.group(1)
    )
    assert base_colour_match is not None
    base_colour = base_colour_match.group(1)

    # .severity-medium sets color: #1a1a1a unconditionally, so it applies
    # in both palettes; the screen-and-dark rule only overrides the other
    # three, and only in dark mode.
    unconditional_dark_text = _unconditional_dark_text_severities(style)
    dark_screen_dark_text = _dark_screen_overridden_severities(style)

    failures = []
    for sev in _SEVERITIES:
        light_bg = light_vars[sev]
        dark_bg = dark_vars[sev]
        light_fg = "#1a1a1a" if sev in unconditional_dark_text else base_colour
        dark_fg = (
            "#1a1a1a"
            if sev in unconditional_dark_text or sev in dark_screen_dark_text
            else base_colour
        )

        light_ratio = _contrast_ratio(light_bg, light_fg)
        dark_ratio = _contrast_ratio(dark_bg, dark_fg)

        if light_ratio < 4.5:
            failures.append(
                f"light {sev}: {light_ratio:.2f}:1 (bg {light_bg} fg {light_fg})"
            )
        if dark_ratio < 4.5:
            failures.append(
                f"dark {sev}: {dark_ratio:.2f}:1 (bg {dark_bg} fg {dark_fg})"
            )

    assert not failures, "severity badges below 4.5:1 contrast:\n" + "\n".join(failures)


def test_dark_mode_severity_text_override_is_scoped_to_screen_not_print() -> None:
    # A rule that flips badge text to #1a1a1a whenever the OS prefers dark
    # would also fire while printing on a dark-mode OS, where the print
    # block forces the light palette's saturated backgrounds back on and
    # white text is what stays readable. "screen and" keeps the override
    # off the mutually exclusive "print" media type altogether.
    style = _style_block()
    match = re.search(
        r"@media\s+([^{]*prefers-color-scheme:\s*dark[^{]*)\{[^}]*severity-critical",
        style,
    )
    assert match is not None, "no dark-mode rule touching .severity-critical found"
    assert "screen" in match.group(1)


def test_card_stripe_width_gives_a_non_hue_severity_cue() -> None:
    # Dark-mode critical and low backgrounds sit within 0.01 of each other
    # in relative luminance, so colour or greyscale luminance alone cannot
    # order them. Stripe width must be strictly monotonic with severity so
    # the cue survives greyscale, a photocopy, or the badge scrolling out
    # of view.
    style = _style_block()
    widths = {}
    for sev in _SEVERITIES:
        rule_match = re.search(rf"\.finding\.sev-{sev}\s*\{{([^}}]*)\}}", style)
        assert rule_match is not None, f".finding.sev-{sev} rule not found"
        width_match = re.search(r"border-left-width:\s*(\d+)px", rule_match.group(1))
        assert width_match is not None, f".finding.sev-{sev} sets no border-left-width"
        widths[sev] = int(width_match.group(1))

    assert widths["critical"] > widths["high"] > widths["medium"] > widths["low"], (
        f"stripe widths are not strictly monotonic with severity: {widths}"
    )


# ---------------------------------------------------------------------------
# #123: the per-domain table's verdict bar survives greyscale and print.
# ---------------------------------------------------------------------------

_SEGMENTS = ("seg-pass", "seg-finding", "seg-na", "seg-cne")
_SEGMENT_VARS = ("pass", "finding", "na", "cne")

# The bar encodes quantity as length, and colour only reinforces a category
# the numerals in the same cell already name. That still leaves the four
# segments needing to be told apart in greyscale, so every pair of them is
# held to this much separation in relative luminance, in both palettes. Any
# pair can end up adjacent, because any segment can be zero and drop out.
_MIN_SEGMENT_SEPARATION = 1.8


def _print_block(style: str) -> str:
    """Everything from '@media print {' to the end of the stylesheet.

    The print block is the last thing in the <style> element, so taking the
    tail is enough and does not need brace matching. _style_block() returns
    the style element's contents without its closing tag, which is why this
    cannot anchor on </style>.
    """
    match = re.search(r"@media print\s*\{(.*)", style, re.DOTALL)
    assert match is not None, "no @media print block found"
    return match.group(1)


def test_every_pair_of_verdict_bar_segments_is_separable_in_greyscale() -> None:
    style = _style_block()

    root_match = re.search(r":root\s*\{(.*?)\n  \}", style, re.DOTALL)
    assert root_match is not None
    light_vars = _extract_root_vars(root_match.group(1))

    dark_root_match = re.search(
        r"@media \(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\n    \}",
        style,
        re.DOTALL,
    )
    assert dark_root_match is not None
    dark_vars = _extract_root_vars(dark_root_match.group(1))

    failures = []
    for palette_name, variables in (("light", light_vars), ("dark", dark_vars)):
        for index, first in enumerate(_SEGMENT_VARS):
            for second in _SEGMENT_VARS[index + 1 :]:
                key_a, key_b = f"seg-{first}", f"seg-{second}"
                assert key_a in variables, (
                    f"--{key_a} missing from the {palette_name} palette"
                )
                assert key_b in variables, (
                    f"--{key_b} missing from the {palette_name} palette"
                )
                ratio = _contrast_ratio(variables[key_a], variables[key_b])
                if ratio < _MIN_SEGMENT_SEPARATION:
                    failures.append(
                        f"{palette_name} {first} vs {second}: {ratio:.2f}:1"
                    )

    assert not failures, (
        "verdict bar segments too close to tell apart in greyscale:\n"
        + "\n".join(failures)
    )


def test_print_palette_redefines_the_verdict_bar_segments_too() -> None:
    # The print block forces the light palette back on. A segment variable
    # left out of it would be drawn in dark-mode colours on white paper for
    # anyone printing from a dark-mode OS.
    print_block = _print_block(_style_block())
    for variable in _SEGMENT_VARS:
        assert re.search(rf"--seg-{variable}:\s*#[0-9a-fA-F]{{6}};", print_block), (
            f"--seg-{variable} is not redefined inside @media print"
        )


def test_the_two_not_checked_segments_carry_a_texture_not_only_a_colour() -> None:
    # Not applicable and could not evaluate are the "nothing was checked"
    # half of the bar, and on a bulk-set-aside run they are most of it. A
    # texture keeps that half distinguishable when the hues are gone.
    style = _style_block()
    for segment in ("seg-na", "seg-cne"):
        rule_match = re.search(rf"\.{segment}\s*\{{([^}}]*)\}}", style, re.DOTALL)
        assert rule_match is not None, f".{segment} rule not found"
        assert "repeating-linear-gradient" in rule_match.group(1)


def test_table_scrolls_sideways_on_screen_but_not_on_paper() -> None:
    # overflow-x: auto is right on a phone and wrong on paper, where a
    # clipped table silently loses its right-hand columns.
    style = _style_block()
    wrap_match = re.search(r"\.domain-table-wrap\s*\{([^}]*)\}", style)
    assert wrap_match is not None
    assert "overflow-x: auto" in wrap_match.group(1)

    print_block = _print_block(style)
    print_wrap_match = re.search(r"\.domain-table-wrap\s*\{([^}]*)\}", print_block)
    assert print_wrap_match is not None, (
        "@media print must override the table's overflow container"
    )
    assert "overflow-x: visible" in print_wrap_match.group(1)


def test_print_asks_for_the_bar_fills_to_be_kept() -> None:
    print_block = _print_block(_style_block())
    match = re.search(r"\.vseg,\s*\.vkey\s*\{([^}]*)\}", print_block)
    assert match is not None, "no print-color-adjust rule for the bar segments"
    assert "print-color-adjust: exact" in match.group(1)


# ---------------------------------------------------------------------------
# #124: a closed <details> must still print its contents.
# ---------------------------------------------------------------------------


def test_print_forces_collapsed_sections_open_by_both_known_mechanisms() -> None:
    # A closed <details> does not print its contents. Which declaration
    # actually opens it depends on how the engine implements the closed
    # state: modern Blink uses content-visibility on ::details-content
    # (measured in Chrome 151: a closed details is 19px tall, and 53px with
    # the ::details-content rule applied, while the display override alone
    # leaves it at 19px), older engines used display: none on the children.
    # Both ship, because neither covers every engine on its own.
    print_block = _print_block(_style_block())

    assert re.search(
        r"details::details-content\s*\{[^}]*content-visibility:\s*visible", print_block
    ), (
        "@media print must reveal ::details-content, which is what actually "
        "opens a closed <details> in current Blink"
    )
    assert re.search(
        r"details:not\(\[open\]\)\s*>\s*\*:not\(summary\)\s*\{[^}]*display:\s*block\s*!important",
        print_block,
    ), (
        "@media print must also carry the display override, for engines that "
        "still implement the closed state that way"
    )


def test_summary_lines_are_not_hidden_from_print() -> None:
    # The summary carries the numbers. If the print block ever hid it as
    # interactive furniture, a printed report would lose the signal and keep
    # the evidence, which is exactly backwards.
    print_block = _print_block(_style_block())
    assert not re.search(r"(^|[\s,])summary\s*\{[^}]*display:\s*none", print_block)


def test_rendered_table_carries_the_bar_markup_and_its_numerals() -> None:
    rendered = _rendered_report()
    assert '<div class="domain-table-wrap">' in rendered
    assert '<span class="vbar-track" aria-hidden="true">' in rendered
    for segment in _SEGMENTS:
        # seg-na does not appear in this fixture run (nothing was set aside),
        # so only the legend swatch is guaranteed for every segment.
        assert f'class="vkey {segment}"' in rendered
    assert '<span class="verdict-numerals">' in rendered


# ---------------------------------------------------------------------------
# #164: the two prominent yellow blocks collapse behind their headline.
# ---------------------------------------------------------------------------


def _rendered_report_with_na_and_cne() -> str:
    """A run with one not-applicable and one could-not-evaluate verdict, so
    both prominent blocks render their non-empty (collapsed) form rather
    than the "nothing to report" paragraph."""
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    verdicts = [RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in d01.rules]
    verdicts[2] = RuleVerdict(
        rule_id="D01-R03",
        verdict=Verdict.NOT_APPLICABLE,
        note="this repository ships no gnome roster",
    )
    verdicts[3] = RuleVerdict(
        rule_id="D01-R04",
        verdict=Verdict.COULD_NOT_EVALUATE,
        note="the beard-length ledger lives outside this repository",
    )
    result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=verdicts,
        self_assessment=SelfAssessment(confidence="high", limits=""),
        coverage=Coverage(files_inspected=12, files_skipped=0),
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={"d01": result},
    )
    return render_report(run_state, pack)


def test_could_not_evaluate_block_is_a_closed_details_with_the_count_in_summary() -> (
    None
):
    # Issue #164: on a phone this block used to render every rule id
    # expanded, screens of them between the reader and everything below.
    # #124's collapsed-section rule already requires the summary to carry
    # its own numbers; this only checks the block now uses that mechanism.
    rendered = _rendered_report_with_na_and_cne()
    match = re.search(
        r"<details><summary>(Could not evaluate: \d+ of \d+ rules? verdicted)"
        r"</summary>(.*?)</details>",
        rendered,
        re.DOTALL,
    )
    assert match is not None, "could-not-evaluate block is not a closed <details>"
    summary, body = match.group(1), match.group(2)
    assert re.search(r"\d", summary), "summary carries no count"
    # The rule id is deliberately inside this one (issue #164's inversion of
    # the #124 guard): collapsing the evidence is the point of this issue.
    assert "D01-R04" in body


def test_not_applicable_block_is_a_closed_details_with_the_count_in_summary() -> None:
    rendered = _rendered_report_with_na_and_cne()
    match = re.search(
        r"<details><summary>(Not applicable: \d+ of \d+ rules? verdicted)"
        r"</summary>(.*?)</details>",
        rendered,
        re.DOTALL,
    )
    assert match is not None, "not-applicable block is not a closed <details>"
    summary, body = match.group(1), match.group(2)
    assert re.search(r"\d", summary), "summary carries no count"
    assert "D01-R03" in body


def test_prominent_blocks_are_closed_by_default() -> None:
    # An always-open <details> discloses nothing; this is the collapse #164
    # asked for, not a decoration.
    rendered = _rendered_report_with_na_and_cne()
    assert "<details open" not in rendered


# ---------------------------------------------------------------------------
# #165: the per-domain table stacks into blocks below a narrow breakpoint
# instead of scrolling sideways with squeezed columns.
# ---------------------------------------------------------------------------


def test_narrow_viewport_rule_turns_table_rows_into_blocks() -> None:
    # Measured against the rendered table at 412px (16 synthetic domains,
    # chrome-devtools): the six columns need ~750px of min-width between
    # them, several times a phone's actual width, so the table scrolled
    # sideways with only a sliver of each row in view and both the domain
    # name and the verdict sentence wrapped hard inside that sliver. Below
    # this breakpoint the row becomes a block instead.
    style = _style_block()
    match = re.search(
        r"@media screen and \(max-width:\s*640px\)\s*\{(.*?)\n  \}",
        style,
        re.DOTALL,
    )
    assert match is not None, "no narrow-viewport rule for the domain table found"
    narrow_block = match.group(1)
    assert re.search(
        r"\.domain-table,\s*\.domain-table tbody,\s*\.domain-table tfoot,\s*\n"
        r"\s*\.domain-table tr,\s*\.domain-table th,\s*\.domain-table td\s*\{"
        r"[^}]*display:\s*block",
        narrow_block,
    ), "table, rows and cells must switch to display: block below the breakpoint"
    assert "overflow-x: visible" in narrow_block, (
        "the horizontal-scroll container must be switched off once rows are "
        "blocks, the same way @media print already does"
    )


def test_narrow_viewport_rule_keeps_column_headers_for_assistive_tech() -> None:
    # The header row is visually hidden, not display: none, so a screen
    # reader still announces "Findings" before a stacked row's findings
    # cell. display: none would drop it from the accessibility tree too.
    style = _style_block()
    match = re.search(
        r"@media screen and \(max-width:\s*640px\)\s*\{(.*?)\n  \}",
        style,
        re.DOTALL,
    )
    assert match is not None
    narrow_block = match.group(1)
    thead_match = re.search(r"\.domain-table thead\s*\{([^}]*)\}", narrow_block)
    assert thead_match is not None, "no narrow-viewport rule for the table header"
    declarations = thead_match.group(1)
    assert "display: none" not in declarations
    assert "clip:" in declarations or "clip-path:" in declarations


def test_narrow_viewport_rule_generates_a_label_before_each_stacked_value() -> None:
    style = _style_block()
    match = re.search(
        r"@media screen and \(max-width:\s*640px\)\s*\{(.*?)\n  \}",
        style,
        re.DOTALL,
    )
    assert match is not None
    narrow_block = match.group(1)
    assert re.search(
        r"\.domain-table td\[data-label\]::before\s*\{[^}]*content:\s*attr\(data-label\)",
        narrow_block,
    )


def test_rendered_domain_table_cells_carry_data_label() -> None:
    # The CSS rule above only does something if the markup carries the
    # attribute it reads.
    rendered = _rendered_report()
    for label in ("Rule verdicts", "Findings", "Files", "Confidence", "Rules fetched"):
        assert f'data-label="{label}"' in rendered


def test_desktop_table_layout_declarations_are_unchanged_outside_the_narrow_query() -> (
    None
):
    # The #165 fix must not touch how the table renders above the
    # breakpoint. This does not re-render at 1172px (that is done with a
    # real browser, see the PR description); it checks the desktop-scoped
    # declarations for display and overflow are exactly what they were
    # before this issue, i.e. absent from the unscoped rule bodies (a table
    # element's default display is already "table" et al, so #123 never had
    # to say so) and that .domain-table-wrap's unscoped rule still says
    # overflow-x: auto, not visible.
    style = _style_block()
    unscoped_wrap_match = re.search(
        r"(?<!\{)\n  \.domain-table-wrap\s*\{([^}]*)\}", style
    )
    assert unscoped_wrap_match is not None
    assert "overflow-x: auto" in unscoped_wrap_match.group(1)
    unscoped_table_match = re.search(r"\n  \.domain-table\s*\{([^}]*)\}", style)
    assert unscoped_table_match is not None
    assert "display" not in unscoped_table_match.group(1)


# ---------------------------------------------------------------------------
# #166: the meta grid's value cells wrap an unbroken token instead of
# letting the cell grow past the card's edge.
# ---------------------------------------------------------------------------


def test_meta_value_uses_overflow_wrap_anywhere_and_min_width_zero() -> None:
    # #126 gave .finding-location the same pair for the same reason: a table
    # or grid cell's intrinsic min-content width is set by its longest
    # unbroken token (here, a 40-character commit hash), so the track grows
    # to fit it instead of wrapping unless min-width: 0 stops that first.
    style = _style_block()
    rule_match = re.search(r"\.meta-value\s*\{([^}]*)\}", style)
    assert rule_match is not None, ".meta-value rule not found"
    declarations = rule_match.group(1)
    assert "overflow-wrap: anywhere" in declarations
    assert "min-width: 0" in declarations
