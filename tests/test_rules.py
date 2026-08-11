"""Tests for the rules pack loader (src/engineering_audit/rules.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engineering_audit.rules import (
    _MAX_CITATION_LENGTH,
    citation,
    RulesPackDuplicateIdError,
    RulesPackError,
    RulesPackParseError,
    get_domain_text,
    load_pack,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"
MALFORMED_DIR = Path(__file__).parent / "fixtures_malformed"


def test_loads_both_triggered_domains() -> None:
    pack = load_pack(FIXTURE_PACK)
    ids = [d.id for d in pack.domains]
    assert ids == ["d01", "d02"]


def test_domain_fields_parsed_correctly() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert d01.number == 1
    assert d01.slug == "gnome-husbandry"
    assert d01.title == "Gnome Husbandry Record Keeping"
    assert d01.trigger == (
        "you are about to register, relocate or retire a garden gnome in the husbandry ledger."
    )
    assert d01.load_when.startswith("adding a new gnome to the roster")
    assert "\n" not in d01.load_when


def test_rule_ids_and_titles_parsed_correctly() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    rule_ids = [r.id for r in d01.rules]
    assert rule_ids == ["D01-R01", "D01-R02", "D01-R03", "D01-R04"]
    first = d01.rules[0]
    assert (
        first.title == "Record every gnome's hat colour before assigning a garden bed."
    )
    assert first.volatility == "durable"
    assert d01.rules[1].volatility == "volatile"


def test_sourced_rule_parses_the_source_fragment() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert d01.rules[0].source == "invented for test fixtures only, no external source"


def test_sourceless_rule_gives_none_not_an_error() -> None:
    # D01-R04's footer deliberately carries no 'Source:' fragment. Absent
    # source is a legitimate, expected result, not a parse failure.
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    rule = next(r for r in d01.rules if r.id == "D01-R04")
    assert rule.source is None


def test_multiline_source_fragment_collapses_to_one_line(tmp_path: Path) -> None:
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Wrapped Source Domain\n\n"
        "**Trigger:** you are about to exercise a wrapped source citation.\n\n"
        "### 1. A rule whose source citation is hand-wrapped across lines.\n\n"
        "Body.\n\n"
        "*Source: a citation that was\n"
        "hand-wrapped across several\n"
        "lines in the source file. Rule id: D01-R01. Volatility: durable.*\n",
    )
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert d01.rules[0].source == (
        "a citation that was hand-wrapped across several lines in the source file"
    )


def test_source_footer_wins_over_earlier_source_mention_in_body(tmp_path: Path) -> None:
    # A rule's own body prose could mention the word 'Source:' before its
    # real footer (e.g. quoting another document). The last 'Source:'
    # occurrence within the footer's own paragraph must win, not the first
    # one found anywhere in the whole block.
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Source Mention Domain\n\n"
        "**Trigger:** you are about to exercise a body-level Source mention.\n\n"
        "### 1. A rule whose body quotes a document that itself says Source: nothing.\n\n"
        "The quoted document says Source: nothing useful here, in prose only.\n\n"
        "*Source: the real citation for this rule. Rule id: D01-R01. Volatility: durable.*\n",
    )
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert d01.rules[0].source == "the real citation for this rule"


def test_second_domain_parsed_correctly() -> None:
    pack = load_pack(FIXTURE_PACK)
    d02 = pack.get_domain("d02")
    assert d02 is not None
    assert d02.number == 2
    assert d02.slug == "teacup-logistics"
    assert [r.id for r in d02.rules] == ["D02-R01", "D02-R02", "D02-R03"]


def test_skip_report_contains_the_no_trigger_file() -> None:
    pack = load_pack(FIXTURE_PACK)
    skipped_names = [s.path.name for s in pack.skipped]
    assert "03-no-trigger-draft.md" in skipped_names
    reason = next(
        s.reason for s in pack.skipped if s.path.name == "03-no-trigger-draft.md"
    )
    assert "Trigger" in reason
    # The no-trigger file's rule must never sneak into a loaded domain.
    for domain in pack.domains:
        assert "D03-R01" not in [r.id for r in domain.rules]


def test_malformed_triggered_file_raises_loudly(tmp_path: Path) -> None:
    # Copy the good fixture pack plus one triggered-but-malformed file into a
    # scratch directory: a triggered file that cannot be parsed must raise,
    # never silently drop out of the pack the way a no-trigger file does.
    scratch = tmp_path / "pack"
    shutil.copytree(FIXTURE_PACK, scratch)
    shutil.copy(MALFORMED_DIR / "bad-header.md", scratch / "09-bad-header.md")

    with pytest.raises(RulesPackParseError):
        load_pack(scratch)


def test_load_pack_raises_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(RulesPackError):
        load_pack(tmp_path / "does-not-exist")


def test_load_pack_raises_when_zero_domains_loaded(tmp_path: Path) -> None:
    only_no_trigger = tmp_path / "pack"
    only_no_trigger.mkdir()
    shutil.copy(
        FIXTURE_PACK / "03-no-trigger-draft.md",
        only_no_trigger / "03-no-trigger-draft.md",
    )
    with pytest.raises(RulesPackError):
        load_pack(only_no_trigger)


def test_rule_id_footer_wins_over_earlier_prose_cross_reference(tmp_path: Path) -> None:
    # A rule's body can mention another rule's id in prose before its own
    # metadata footer. The footer line is always the last occurrence in the
    # block, so parsing must take the last match, not the first.
    scratch = tmp_path / "pack"
    scratch.mkdir()
    (scratch / "01-cross-ref.md").write_text(
        "# Domain 01: Cross Reference Domain\n\n"
        "**Trigger:** you are about to touch a cross-referencing rule.\n\n"
        "### 1. A rule whose body cites another rule before its own footer.\n\n"
        "This is as covered by Rule id: D01-R01. in a different rule's footer, "
        "mentioned here only in prose.\n\n"
        "*Source: fixture only. Rule id: D01-R05. Volatility: durable.*\n",
        encoding="utf-8",
    )
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert [r.id for r in d01.rules] == ["D01-R05"]


def test_get_domain_text_returns_full_document() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    text = get_domain_text(d01)
    assert text.startswith("# Domain 01: Gnome Husbandry Record Keeping")
    assert "D01-R04" in text


def _write_pack(tmp_path: Path, body: str) -> Path:
    scratch = tmp_path / "pack"
    scratch.mkdir()
    (scratch / "01-letter-series.md").write_text(body, encoding="utf-8")
    return scratch


def test_letter_prefixed_rule_headings_parse_as_their_own_rules(tmp_path: Path) -> None:
    # The real pack mixes numbered headings ('### 8.') with letter-series
    # headings ('### T1.'). Both must parse as distinct rules; the T-series
    # being absorbed into the previous rule is the bug that hid nine rules of
    # a sixteen-rule domain during the first proving run.
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Letter Series Domain\n\n"
        "**Trigger:** you are about to exercise letter-series rule headings.\n\n"
        "### 1. A plain numbered rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n\n"
        "### T1. A letter-series rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-T01. Volatility: durable.*\n\n"
        "### T2. Another letter-series rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-T02. Volatility: fast.*\n",
    )
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert [r.id for r in d01.rules] == ["D01-R01", "D01-T01", "D01-T02"]
    assert [r.number for r in d01.rules] == [1, 1, 2]


def test_unrecognised_h3_heading_raises_rather_than_absorbing(tmp_path: Path) -> None:
    # A '###' heading the rule pattern does not match must be a loud parse
    # error, never silently folded into the previous rule's block.
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Absorbing Domain\n\n"
        "**Trigger:** you are about to exercise an unrecognised heading.\n\n"
        "### 1. A plain numbered rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n\n"
        "### Appendix of miscellany\n\n"
        "Not a rule heading; must not be absorbed silently.\n",
    )
    with pytest.raises(RulesPackParseError) as excinfo:
        load_pack(scratch)
    assert "Appendix of miscellany" in str(excinfo.value)


def test_duplicate_rule_ids_raise(tmp_path: Path) -> None:
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Duplicate Id Domain\n\n"
        "**Trigger:** you are about to exercise duplicate rule ids.\n\n"
        "### 1. First rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n\n"
        "### 2. Second rule reusing the same id.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n",
    )
    with pytest.raises(RulesPackParseError) as excinfo:
        load_pack(scratch)
    assert "D01-R01" in str(excinfo.value)


def test_cross_file_unique_rule_ids_load_successfully() -> None:
    # The fixture pack's two domains define disjoint rule id ranges
    # (D01-R0x, D02-R0x); loading it must not trip the new cross-file check.
    pack = load_pack(FIXTURE_PACK)
    assert len(pack.rule_index) == 7


def test_cross_file_duplicate_rule_id_raises_naming_both_files(tmp_path: Path) -> None:
    scratch = tmp_path / "pack"
    scratch.mkdir()
    (scratch / "01-first.md").write_text(
        "# Domain 01: First Domain\n\n"
        "**Trigger:** you are about to exercise domain one.\n\n"
        "### 1. First domain's only rule.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n",
        encoding="utf-8",
    )
    (scratch / "02-second.md").write_text(
        "# Domain 02: Second Domain\n\n"
        "**Trigger:** you are about to exercise domain two.\n\n"
        "### 1. Second domain's rule, reusing the first domain's id by mistake.\n\n"
        "Body.\n\n"
        "*Source: fixture only. Rule id: D01-R01. Volatility: durable.*\n",
        encoding="utf-8",
    )
    with pytest.raises(RulesPackDuplicateIdError) as excinfo:
        load_pack(scratch)
    message = str(excinfo.value)
    assert "D01-R01" in message
    assert "01-first.md" in message
    assert "02-second.md" in message


def test_citation_returns_a_plain_source_unchanged() -> None:
    source = (
        "Object-Role Modeling and its Conceptual Schema Design Procedure "
        "(Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 1"
    )
    assert citation(source) == source


def test_citation_caps_at_the_sentence_boundary_before_the_excerpt() -> None:
    # A complete sentence precedes the excerpt marker here ("v4.0.1." is a
    # safe place to cut, since the heuristic only needs a period followed
    # by whitespace, not a linguistically perfect sentence split), so the
    # cap lands cleanly at the end of that sentence, dropping the sentence
    # that introduces the quote entirely rather than cutting it mid-clause.
    source = (
        "The ISTQB Foundation Level syllabus, v4.0.1. Sections 3.1.2 and 3.1.3 "
        'state: "Static testing can detect defects early."'
    )
    assert citation(source) == "The ISTQB Foundation Level syllabus, v4.0.1."


def test_citation_with_no_sentence_boundary_before_the_excerpt_is_not_cut() -> None:
    # Issue #86: the old heuristic cut at the colon itself regardless of
    # whether that produced a complete clause, which is how a real audit
    # run shipped fragments like "...rules 4 and 6. Rule 4" and "...Table 1
    # and surrounding text. Beneficence". When no sentence boundary exists
    # before the excerpt marker, a cut there would repeat that bug, so the
    # source is published whole (subject only to the hard character
    # ceiling) rather than fragmented.
    source = (
        "ISTQB Certified Tester Foundation Level syllabus v4.0.1, sections 3.1.2 "
        'and 3.1.3: "Static testing can detect defects early"'
    )
    assert citation(source) == source


def test_citation_keeps_a_quoted_work_title_when_no_sentence_boundary_precedes_it() -> (
    None
):
    # A title quoted after a comma has no sentence-ending punctuation before
    # the excerpt colon either, so (per the rule above) nothing is cut.
    source = (
        'Mike Cohn, "The Forgotten Layer of the Test Automation Pyramid," '
        'Mountain Goat Software (2009): "At the base of the pyramid is unit testing"'
    )
    assert citation(source) == source


def test_citation_hard_truncates_an_unbounded_source_with_a_visible_marker() -> None:
    # Issue #86's other failure mode: a source with no excerpt marker at
    # all was previously published whole and unbounded, which is how a
    # 2,051-character verification narrative reached a client-facing
    # report. The fix is a hard ceiling with a visible marker, never a
    # silent cut.
    source = "A very long verification narrative. " * 30
    assert len(source) > 400
    result = citation(source)
    assert len(result) < len(source)
    assert result.endswith("[reference truncated]")
    # The cut must not land mid-word: the character immediately before the
    # marker's leading space is not a letter fragment.
    assert " [reference truncated]" in result


def test_citation_skips_excerpt_capping_for_a_v2_pack() -> None:
    # A v2 pack's Source: is self-contained and publishable verbatim by its
    # own authoring contract (the rule-footer-format-v2 contract), so the
    # sentence-boundary excerpt cap must not fire: the colon-then-quote here
    # is part of the citation, not the start of a supporting excerpt.
    source = (
        "ISTQB Certified Tester Foundation Level syllabus v4.0.1, sections 3.1.2 "
        'and 3.1.3: "Static testing can detect defects early"'
    )
    assert citation(source, pack_is_v2=True) == source


def test_citation_still_applies_the_hard_ceiling_to_a_v2_pack() -> None:
    # The ceiling is NOT part of the v1-only path. Pack-wide v2 detection
    # flips on a single Verification: marker found anywhere, so a partly
    # migrated pack reports v2 while still holding unmigrated footers with
    # narrative in Source:. Those must still be truncated visibly rather
    # than published whole, and rather than tripping report.py's own
    # ReportError backstop and failing the entire render.
    long_source = "A very long but, per the v2 contract, self-contained citation. " * 20
    result = citation(long_source, pack_is_v2=True)

    assert result != long_source
    assert len(result) <= _MAX_CITATION_LENGTH + len(" [reference truncated]")
    assert result.endswith("[reference truncated]")


def test_extract_source_stops_at_a_verification_marker(tmp_path: Path) -> None:
    # Under the rule-footer-format-v2 contract a footer may carry
    # Source: <citation> Verification: <trail> ... in that order; the
    # verification trail must never leak into the parsed source, since it
    # is the pack maintainer's own bookkeeping, not a citation.
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Verification Marker Domain\n\n"
        "**Trigger:** you are about to exercise a Verification: marker.\n\n"
        "### 1. A rule whose footer carries a Verification: trail.\n\n"
        "Body.\n\n"
        "*Source: a self-contained citation. Verification: checked on 2026-08-05 and "
        "ruled out three other candidates. Rule id: D01-R01. Volatility: durable.*\n",
    )
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    assert d01.rules[0].source == "a self-contained citation"


def test_rules_pack_is_v2_false_when_no_verification_marker_anywhere() -> None:
    # The fixture pack's footers use "Verified:" (past tense, a bare date
    # stamp), never the "Verification:" field name the v2 contract defines;
    # it must not be mistaken for a migrated pack.
    pack = load_pack(FIXTURE_PACK)
    assert pack.is_v2 is False


def test_rules_pack_is_v2_true_when_any_domain_carries_the_marker(
    tmp_path: Path,
) -> None:
    scratch = _write_pack(
        tmp_path,
        "# Domain 01: Verification Marker Domain\n\n"
        "**Trigger:** you are about to exercise a Verification: marker.\n\n"
        "### 1. A rule whose footer carries a Verification: trail.\n\n"
        "Body.\n\n"
        "*Source: a self-contained citation. Verification: checked on 2026-08-05. "
        "Rule id: D01-R01. Volatility: durable.*\n",
    )
    pack = load_pack(scratch)
    assert pack.is_v2 is True
