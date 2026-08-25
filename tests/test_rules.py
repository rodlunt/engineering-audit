"""Tests for the rules pack loader (src/engineering_audit/rules.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engineering_audit.rules import (
    _MAX_CITATION_LENGTH,
    PACK_FORMAT_MAX,
    PACK_FORMAT_MIN,
    PackMetadata,
    citation,
    RulesPackDuplicateIdError,
    RulesPackError,
    RulesPackParseError,
    get_domain_text,
    load_pack,
    read_pack_metadata,
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


# ---------------------------------------------------------------------------
# pack.toml metadata (issue #170)
# ---------------------------------------------------------------------------

# A verbatim copy of the real framework pack's domains/pack.toml (its PR
# #205, engineering-framework#204), comments and all: the parser must accept
# exactly this shape, not a simplified stand-in for it.
_REAL_FRAMEWORK_PACK_TOML = """\
# Pack metadata for the engineering-audit tool (engineering-framework#204,
# companion to engineering-audit#170). The tool reads this file if present and
# renders an honest mismatch notice when it is older than requires_tool; absent,
# nothing is claimed. It travels inside domains/ so it survives zips and vendored
# copies where git provenance is blind.
#
# No self-version field here, deliberately. The audit tool derives this pack's
# version from git (see engineering-audit's _git_release_version), which is
# stronger than an asserted number could ever be, and engineering-audit#136
# records why an asserted version was declined before. This file carries only
# what git cannot derive: what the pack requires of the tool, not what the pack
# itself currently is.

# The rule-file format this pack is written in: the Source:/Verification: footer
# split (METHOD.md, "Footer format: the citation and the trail are separate
# fields"), which the tool detects as RulesPack.is_v2 in rules.py. Every domain
# file in this pack carries a Verification: marker, so this pack is fully on
# format 2, not partially migrated.
format = 2

# The oldest engineering-audit release that understands format 2: the
# Source:/Verification: split was introduced in the commit that added is_v2
# detection (4a9e3bf, "fix(report): make findings' references publishable"),
# first released as v0.6.0. Checked against the tool's own history: no rule in
# this pack depends on any engineering-audit behaviour newer than that, so this
# is pinned to the version that introduced the format itself, not to whatever
# happens to be current at release time.
requires_tool = "0.6.0"
"""


def test_read_pack_metadata_parses_the_real_framework_pack_shape(
    tmp_path: Path,
) -> None:
    (tmp_path / "pack.toml").write_text(_REAL_FRAMEWORK_PACK_TOML, encoding="utf-8")
    metadata = read_pack_metadata(tmp_path)
    assert metadata == PackMetadata(format=2, requires_tool="0.6.0")


def test_read_pack_metadata_parses_edition_and_full_pack_url(tmp_path: Path) -> None:
    # Issue #255: a pack that is a deliberate subset declares what it is and
    # where the full pack can be requested. Self-declared only; the parser
    # must not fabricate either from anything else.
    (tmp_path / "pack.toml").write_text(
        "format = 1\n"
        'edition = "taster (3 of 16 domains)"\n'
        'full_pack_url = "https://example.test/request"\n',
        encoding="utf-8",
    )
    metadata = read_pack_metadata(tmp_path)
    assert metadata == PackMetadata(
        format=1,
        requires_tool=None,
        edition="taster (3 of 16 domains)",
        full_pack_url="https://example.test/request",
    )


def test_read_pack_metadata_treats_empty_edition_as_absent(tmp_path: Path) -> None:
    # An empty-string declaration is not a claim: rendering "the '' rules
    # pack" would be a notice about nothing.
    (tmp_path / "pack.toml").write_text(
        'edition = ""\nfull_pack_url = ""\n', encoding="utf-8"
    )
    metadata = read_pack_metadata(tmp_path)
    assert metadata is not None
    assert metadata.edition is None
    assert metadata.full_pack_url is None


def test_the_shipped_taster_pack_toml_declares_its_edition() -> None:
    # The real file this feature ships for, parsed by the real parser: if
    # the taster's pack.toml drifts out of the shape read_pack_metadata
    # reads, the begin_run notice silently stops firing, which is exactly
    # the class of quiet death this repo exists to catch.
    taster = Path(__file__).parent.parent / "examples" / "taster-rules"
    metadata = read_pack_metadata(taster)
    assert metadata is not None
    assert metadata.edition == "taster (3 of 16 domains)"
    assert metadata.full_pack_url is not None
    assert metadata.full_pack_url.startswith("https://github.com/rodlunt/")


def test_read_pack_metadata_ignores_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "pack.toml").write_text(
        "format = 2\n"
        'requires_tool = "0.6.0"\n'
        'maintainer = "someone"\n'
        "future_flag = true\n",
        encoding="utf-8",
    )
    metadata = read_pack_metadata(tmp_path)
    assert metadata == PackMetadata(format=2, requires_tool="0.6.0")


def test_read_pack_metadata_handles_a_malformed_file_without_crashing(
    tmp_path: Path,
) -> None:
    # Neither key is in a shape this parser recognises: an unquoted
    # requires_tool and a non-numeric format. Must not raise, and must not
    # fabricate a value for either field.
    (tmp_path / "pack.toml").write_text(
        "this is not valid TOML at all\nformat = two\nrequires_tool = 0.6.0\n",
        encoding="utf-8",
    )
    metadata = read_pack_metadata(tmp_path)
    assert metadata == PackMetadata(format=None, requires_tool=None)


def test_read_pack_metadata_returns_none_when_pack_toml_is_absent(
    tmp_path: Path,
) -> None:
    # Control: tmp_path exists and is a real directory, so a None result here
    # is genuinely "no pack.toml", not a directory-lookup bug.
    assert (tmp_path / "pack.toml").exists() is False
    assert read_pack_metadata(tmp_path) is None


def test_pack_format_range_covers_the_real_framework_pack() -> None:
    # The real pack currently declares format = 2; this is the control that
    # PACK_FORMAT_MIN/MAX are not accidentally narrower than what ships.
    assert PACK_FORMAT_MIN <= 2 <= PACK_FORMAT_MAX
