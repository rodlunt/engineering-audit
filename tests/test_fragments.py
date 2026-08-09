"""Tests for the inline-fragment generator (src/engineering_audit/fragments.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engineering_audit.fragments import OutDirIsFileError, generate_fragments, main
from engineering_audit.rules import RulesPackParseError

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"
MALFORMED_DIR = Path(__file__).parent / "fixtures_malformed"

_FRAGMENT_NAMES = ("AGENTS-fragment.md", "GEMINI-fragment.md")


def test_fragments_contain_every_domain_trigger_and_get_domain_id(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    pack = generate_fragments(FIXTURE_PACK, out_dir)

    for filename in _FRAGMENT_NAMES:
        text = (out_dir / filename).read_text(encoding="utf-8")
        for domain in pack.domains:
            assert domain.trigger in text
            assert f'get_domain("{domain.id}")' in text


def test_fragments_end_with_a_newline(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    generate_fragments(FIXTURE_PACK, out_dir)
    for filename in _FRAGMENT_NAMES:
        assert (out_dir / filename).read_text(encoding="utf-8").endswith("\n")


def test_agents_and_gemini_fragments_differ_only_in_header_line(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    generate_fragments(FIXTURE_PACK, out_dir)
    agents_lines = (out_dir / "AGENTS-fragment.md").read_text(encoding="utf-8").splitlines()
    gemini_lines = (out_dir / "GEMINI-fragment.md").read_text(encoding="utf-8").splitlines()

    assert agents_lines[0] != gemini_lines[0]
    assert "AGENTS.md" in agents_lines[0]
    assert "GEMINI.md" in gemini_lines[0]
    assert agents_lines[1:] == gemini_lines[1:]


def test_generation_is_deterministic_across_runs(tmp_path: Path) -> None:
    out_dir_a = tmp_path / "a"
    out_dir_b = tmp_path / "b"
    generate_fragments(FIXTURE_PACK, out_dir_a)
    generate_fragments(FIXTURE_PACK, out_dir_b)

    for filename in _FRAGMENT_NAMES:
        assert (out_dir_a / filename).read_bytes() == (out_dir_b / filename).read_bytes()


def test_no_trigger_fixture_file_is_excluded_from_the_fragments(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    generate_fragments(FIXTURE_PACK, out_dir)
    text = (out_dir / "AGENTS-fragment.md").read_text(encoding="utf-8")
    assert "D03-R01" not in text
    assert 'get_domain("d03")' not in text


def test_skipped_files_are_reported_to_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "out"
    main(["--rules-dir", str(FIXTURE_PACK), "--out-dir", str(out_dir)])
    err = capsys.readouterr().err
    assert "03-no-trigger-draft.md" in err
    assert "Trigger" in err


def test_out_dir_that_is_a_file_is_refused(tmp_path: Path) -> None:
    out_dir_as_file = tmp_path / "out"
    out_dir_as_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OutDirIsFileError):
        generate_fragments(FIXTURE_PACK, out_dir_as_file)


def test_main_exits_loudly_when_out_dir_is_a_file(tmp_path: Path) -> None:
    out_dir_as_file = tmp_path / "out"
    out_dir_as_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--rules-dir", str(FIXTURE_PACK), "--out-dir", str(out_dir_as_file)])


def test_malformed_triggered_file_raises_rather_than_producing_a_short_fragment(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "pack"
    shutil.copytree(FIXTURE_PACK, scratch)
    shutil.copy(MALFORMED_DIR / "bad-header.md", scratch / "09-bad-header.md")

    with pytest.raises(RulesPackParseError):
        generate_fragments(scratch, tmp_path / "out")


def test_main_exits_loudly_on_a_broken_pack(tmp_path: Path) -> None:
    scratch = tmp_path / "pack"
    shutil.copytree(FIXTURE_PACK, scratch)
    shutil.copy(MALFORMED_DIR / "bad-header.md", scratch / "09-bad-header.md")

    with pytest.raises(SystemExit):
        main(["--rules-dir", str(scratch), "--out-dir", str(tmp_path / "out")])
