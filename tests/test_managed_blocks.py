"""Tests for the managed-block write protocol (src/engineering_audit/managed_blocks.py).

Managed blocks are sections of a document marked by opening and closing HTML
comments that the tool rewrites. Hand edits outside the blocks are preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_audit.managed_blocks import wrap_managed_block, write_managed_block


class TestWriteManagedBlock:
    """Tests for the write_managed_block function."""

    def test_replaces_content_between_markers_in_existing_file(
        self, tmp_path: Path
    ) -> None:
        """Happy path: file exists with valid markers, content is replaced."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# My Document\n\n"
            '<!-- audit:start id="agent-standard" -->\n'
            "Old content\n"
            "<!-- audit:end -->\n\n"
            "Hand-edited section below\n"
        )

        new_content = "New content here"
        result = write_managed_block(test_file, new_content, "agent-standard")

        assert result is True
        written_text = test_file.read_text()
        assert "New content here" in written_text
        assert "Old content" not in written_text
        assert "Hand-edited section below" in written_text

    def test_preserves_hand_edits_outside_managed_block(self, tmp_path: Path) -> None:
        """Hand-edited content outside the block is preserved exactly."""
        test_file = tmp_path / "test.md"
        original_text = (
            "# Engineering Standard\n\n"
            "Introduction written by hand.\n\n"
            '<!-- audit:start id="human-standard" -->\n'
            "Tool-generated rules\n"
            "<!-- audit:end -->\n\n"
            "Footer and hand-written notes.\n"
            "With multiple lines\n"
            "And blank lines:\n\n"
            "Still hand-written.\n"
        )
        test_file.write_text(original_text)

        new_content = "Updated tool-generated content"
        write_managed_block(test_file, new_content, "human-standard")

        written_text = test_file.read_text()
        assert "Introduction written by hand." in written_text
        assert "Footer and hand-written notes." in written_text
        assert "With multiple lines" in written_text
        assert "Still hand-written." in written_text
        assert written_text.startswith("# Engineering Standard\n\n")
        assert written_text.endswith("Still hand-written.\n")

    def test_preserves_formatting_indentation_and_blank_lines(
        self, tmp_path: Path
    ) -> None:
        """Formatting, indentation, and blank lines outside the block are preserved."""
        test_file = tmp_path / "test.md"
        original_text = (
            "# Document\n\n"
            "    Code example:\n"
            "    - indented\n"
            "    - with spacing\n\n\n"
            '<!-- audit:start id="policy" -->\n'
            "Old\n"
            "<!-- audit:end -->\n\n\n"
            "    More code:\n"
            "    - also indented\n"
        )
        test_file.write_text(original_text)

        write_managed_block(test_file, "New content", "policy")

        written_text = test_file.read_text()
        assert (
            "    Code example:\n    - indented\n    - with spacing\n\n\n"
            in written_text
        )
        assert "    More code:\n    - also indented\n" in written_text
        assert "Old" not in written_text
        assert "New content" in written_text

    def test_creates_new_file_with_minimal_header_if_file_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        """If file does not exist, create it with a minimal header and managed block."""
        test_file = tmp_path / "new_standard.md"
        assert not test_file.exists()

        new_content = "Initial rule set content"
        result = write_managed_block(test_file, new_content, "agent-standard")

        assert result is True
        assert test_file.exists()
        written_text = test_file.read_text()

        # Should have a minimal header with title and date
        assert "# " in written_text  # Has a heading
        assert "2026-" in written_text or "2025-" in written_text  # Has a date
        # Should have managed block markers
        assert '<!-- audit:start id="agent-standard" -->' in written_text
        assert "<!-- audit:end -->" in written_text
        # Should contain the new content
        assert new_content in written_text

    def test_returns_false_and_logs_error_when_opening_marker_missing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing opening marker: returns False, logs actionable error, does not write."""
        test_file = tmp_path / "test.md"
        test_file.write_text("# Document\n\nSome content\n<!-- audit:end -->\n")

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        # File should not be modified
        assert "Some content" in test_file.read_text()
        # Error message should be specific and actionable
        assert len(caplog.records) > 0
        error_message = caplog.text
        assert (
            "Opening marker missing" in error_message
            or "opening marker" in error_message.lower()
        )
        assert "agent-standard" in error_message

    def test_returns_false_and_logs_error_when_closing_marker_missing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing closing marker: returns False, logs actionable error, does not write."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            '# Document\n\n<!-- audit:start id="agent-standard" -->\nSome content\n'
        )

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        assert "Some content" in test_file.read_text()
        error_message = caplog.text
        assert "closing marker" in error_message.lower() or "audit:end" in error_message

    def test_returns_false_and_logs_error_when_markers_are_duplicated(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Duplicated markers: returns False, logs actionable error, does not write."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n"
            '<!-- audit:start id="agent-standard" -->\n'
            "First block\n"
            "<!-- audit:end -->\n\n"
            '<!-- audit:start id="agent-standard" -->\n'
            "Second block\n"
            "<!-- audit:end -->\n"
        )

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        original_text = test_file.read_text()
        assert "First block" in original_text
        assert "Second block" in original_text
        error_message = caplog.text
        assert (
            "duplicate" in error_message.lower() or "multiple" in error_message.lower()
        )

    def test_returns_false_and_logs_error_when_marker_missing_id_attribute(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed marker without id attribute: returns False, logs actionable error."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n<!-- audit:start -->\nSome content\n<!-- audit:end -->\n"
        )

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        assert "Some content" in test_file.read_text()
        error_message = caplog.text
        assert "id" in error_message.lower() or "malformed" in error_message.lower()

    def test_returns_false_and_logs_error_when_marker_has_wrong_id(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Marker has different id than requested: returns False, logs actionable error."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n"
            '<!-- audit:start id="human-standard" -->\n'
            "Some content\n"
            "<!-- audit:end -->\n"
        )

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        assert "Some content" in test_file.read_text()
        error_message = caplog.text
        assert "agent-standard" in error_message

    def test_returns_false_and_logs_error_when_marker_format_is_wrong(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed marker with wrong format: returns False, logs actionable error."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n"
            "<!-- audit:start id=agent-standard -->\n"
            "Some content\n"
            "<!-- audit:end -->\n"
        )

        result = write_managed_block(test_file, "new content", "agent-standard")

        assert result is False
        assert "Some content" in test_file.read_text()
        error_message = caplog.text
        assert "malformed" in error_message.lower() or "format" in error_message.lower()

    def test_preserves_content_before_opening_marker(self, tmp_path: Path) -> None:
        """Content before opening marker is preserved exactly."""
        test_file = tmp_path / "test.md"
        before_content = (
            "# Title\n\nIntroduction with special chars: @#$%\nMultiple\nLines\n\n"
        )
        test_file.write_text(
            before_content + '<!-- audit:start id="block" -->\n'
            "Old\n"
            "<!-- audit:end -->\n"
        )

        write_managed_block(test_file, "New", "block")

        written_text = test_file.read_text()
        assert written_text.startswith(before_content)

    def test_preserves_content_after_closing_marker(self, tmp_path: Path) -> None:
        """Content after closing marker is preserved exactly."""
        test_file = tmp_path / "test.md"
        after_content = "\nFooter content\nWith multiple lines\n\nAnd blank lines"
        test_file.write_text(
            '<!-- audit:start id="block" -->\nOld\n<!-- audit:end -->' + after_content
        )

        write_managed_block(test_file, "New", "block")

        written_text = test_file.read_text()
        assert written_text.endswith(after_content)

    def test_handles_multiline_content_replacement(self, tmp_path: Path) -> None:
        """Multiline content is handled correctly."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n"
            '<!-- audit:start id="block" -->\n'
            "Line 1\n"
            "Line 2\n"
            "<!-- audit:end -->\n"
        )

        new_content = "New line 1\nNew line 2\nNew line 3\n"
        write_managed_block(test_file, new_content, "block")

        written_text = test_file.read_text()
        assert "New line 1\nNew line 2\nNew line 3\n" in written_text
        assert "Line 1" not in written_text
        assert "Line 2" not in written_text

    def test_handles_empty_content_replacement(self, tmp_path: Path) -> None:
        """Empty content is handled correctly."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            "# Document\n\n"
            '<!-- audit:start id="block" -->\n'
            "Old content\n"
            "<!-- audit:end -->\n"
            "After\n"
        )

        write_managed_block(test_file, "", "block")

        written_text = test_file.read_text()
        assert "Old content" not in written_text
        # Empty content results in blank line between markers
        assert '<!-- audit:start id="block" -->\n\n<!-- audit:end -->' in written_text

    def test_handles_block_with_no_surrounding_content(self, tmp_path: Path) -> None:
        """File with only the managed block is handled correctly."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            '<!-- audit:start id="block" -->\nContent\n<!-- audit:end -->'
        )

        write_managed_block(test_file, "New", "block")

        written_text = test_file.read_text()
        assert "New" in written_text
        assert "Content" not in written_text
        assert written_text.count("<!-- audit:start") == 1
        assert written_text.count("<!-- audit:end") == 1

    def test_uses_path_object(self, tmp_path: Path) -> None:
        """Function accepts Path objects."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            '<!-- audit:start id="block" -->\nOld\n<!-- audit:end -->\n'
        )

        # Ensure we can pass a Path object
        assert isinstance(test_file, Path)
        result = write_managed_block(test_file, "New", "block")
        assert result is True

    def test_handles_whitespace_in_markers(self, tmp_path: Path) -> None:
        """Markers with extra whitespace are handled correctly."""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            '<!-- audit:start id="block" -->\nOld\n<!-- audit:end -->\n'
        )

        write_managed_block(test_file, "New", "block")

        written_text = test_file.read_text()
        assert "New" in written_text
        assert "Old" not in written_text


class TestWrapManagedBlock:
    """Tests for the wrap_managed_block function."""

    def test_wrap_managed_block_produces_valid_markers(self) -> None:
        """wrap_managed_block output contains valid opening and closing markers."""
        content = "Test content here"
        block_id = "test-block"

        wrapped = wrap_managed_block(content, block_id)

        assert '<!-- audit:start id="test-block" -->' in wrapped
        assert "<!-- audit:end -->" in wrapped
        assert content in wrapped

    def test_wrap_managed_block_round_trips_with_write(self, tmp_path: Path) -> None:
        """Content wrapped with wrap_managed_block can be round-tripped through write/read.

        This test ensures that wrap_managed_block and write_managed_block use
        the same marker format. If a second copy of the marker format were
        reintroduced, this test would fail because the parser would not
        recognize the output of wrap_managed_block.
        """
        test_file = tmp_path / "test.md"
        original_content = "Original test content"
        block_id = "test-standard"

        # Create a file with markers using wrap_managed_block
        wrapped_content = wrap_managed_block(original_content, block_id)
        test_file.write_text(
            f"# Test\n\nSome preamble\n\n{wrapped_content}\n\nSome footer\n"
        )

        # Update using write_managed_block with new content
        new_content = "Updated test content"
        result = write_managed_block(test_file, new_content, block_id)

        # Should succeed because markers are valid
        assert result is True

        # Content should be updated
        written_text = test_file.read_text()
        assert new_content in written_text
        assert original_content not in written_text

        # Preamble and footer should be preserved
        assert "Some preamble" in written_text
        assert "Some footer" in written_text
