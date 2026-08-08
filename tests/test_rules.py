"""Tests for the rules pack loader (src/engineering_audit/rules.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engineering_audit.rules import (
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
    assert first.title == "Record every gnome's hat colour before assigning a garden bed."
    assert first.volatility == "durable"
    assert d01.rules[1].volatility == "volatile"


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
    reason = next(s.reason for s in pack.skipped if s.path.name == "03-no-trigger-draft.md")
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


def test_get_domain_text_returns_full_document() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    text = get_domain_text(d01)
    assert text.startswith("# Domain 01: Gnome Husbandry Record Keeping")
    assert "D01-R04" in text
