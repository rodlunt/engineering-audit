"""Tests for linking standards documents in project files.

Tests that the tool can add links to the three standards documents in
project files (CLAUDE.md, AGENTS.md, README.md), preserves hand edits
outside its managed block, is idempotent, uses correct relative paths,
and provides actionable validation error messages.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from engineering_audit.link_standards import link_standards_in_project_documents


class TestLinkStandardsBasic:
    """Tests for basic linking functionality."""

    def test_link_standards_in_claude_md(self, tmp_path: Path) -> None:
        """Linking standards adds links to CLAUDE.md."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a basic CLAUDE.md
        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude Instructions\n\nSome content here.\n")

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        assert result["success"] is True
        assert "CLAUDE.md" in result["updated_files"]

        # Verify the file now contains the links
        content = claude_path.read_text(encoding="utf-8")
        assert "Agent Coding Standard" in content
        assert "Engineering Standard" in content
        assert "Engineering Policy" in content
        assert "docs/coding-standard.agent.md" in content
        assert "docs/engineering-standard.md" in content
        assert "docs/engineering-policy.md" in content

    def test_link_standards_in_agents_md(self, tmp_path: Path) -> None:
        """Linking standards adds links to AGENTS.md."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        agents_path = project_dir / "AGENTS.md"
        agents_path.write_text("# Agents\n\nConfiguration for agents.\n")

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["AGENTS.md"],
        )

        assert result["success"] is True
        assert "AGENTS.md" in result["updated_files"]

        content = agents_path.read_text(encoding="utf-8")
        assert "Agent Coding Standard" in content

    def test_link_standards_in_readme_md(self, tmp_path: Path) -> None:
        """Linking standards adds links to README.md."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        readme_path = project_dir / "README.md"
        readme_path.write_text("# Project\n\nProject description.\n")

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["README.md"],
        )

        assert result["success"] is True
        assert "README.md" in result["updated_files"]

        content = readme_path.read_text(encoding="utf-8")
        assert "Engineering Standard" in content

    def test_link_standards_in_multiple_files(self, tmp_path: Path) -> None:
        """Linking standards updates multiple files when specified."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude\n")

        agents_path = project_dir / "AGENTS.md"
        agents_path.write_text("# Agents\n")

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md", "AGENTS.md"],
        )

        assert result["success"] is True
        assert len(result["updated_files"]) == 2
        assert "CLAUDE.md" in result["updated_files"]
        assert "AGENTS.md" in result["updated_files"]


class TestLinkStandardsIdempotency:
    """Tests that linking is idempotent."""

    def test_linking_twice_is_idempotent(self, tmp_path: Path) -> None:
        """Running the tool twice produces identical content."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude\n\nInitial content.\n")

        # First call
        result1 = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )
        assert result1["success"] is True
        content_after_first = claude_path.read_text(encoding="utf-8")

        # Second call
        result2 = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )
        assert result2["success"] is True
        content_after_second = claude_path.read_text(encoding="utf-8")

        # Content should be identical
        assert content_after_first == content_after_second

    def test_idempotency_with_hand_edits_preserved(self, tmp_path: Path) -> None:
        """Hand edits outside the managed block survive idempotent runs."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        initial_content = "# Claude Instructions\n\nSome custom setup.\n"
        claude_path.write_text(initial_content)

        # First link
        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )
        content_after_first = claude_path.read_text(encoding="utf-8")

        # Add hand edits outside the managed block
        # The managed block should be surrounded by hand-edited content
        lines = content_after_first.split("\n")
        # Insert custom content after the closing marker
        insertion_point = None
        for i, line in enumerate(lines):
            if "<!-- audit:end -->" in line:
                insertion_point = i + 1
                break

        if insertion_point is not None:
            lines.insert(insertion_point, "\n## Custom Section")
            lines.insert(insertion_point + 1, "\nThis is hand-edited content.")
            modified_content = "\n".join(lines)
            claude_path.write_text(modified_content)

        # Second link (should preserve hand edits)
        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )
        content_after_second = claude_path.read_text(encoding="utf-8")

        # Hand edits should still be there
        assert "Custom Section" in content_after_second
        assert "hand-edited content" in content_after_second


class TestRelativePaths:
    """Tests for relative path calculation."""

    def test_relative_paths_from_file_at_root(self, tmp_path: Path) -> None:
        """Links use correct relative paths from file at project root."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude\n")

        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        content = claude_path.read_text(encoding="utf-8")
        # From root, docs/ is a relative path
        assert "docs/coding-standard.agent.md" in content
        assert "docs/engineering-standard.md" in content
        assert "docs/engineering-policy.md" in content

    def test_relative_paths_from_file_in_subdirectory(self, tmp_path: Path) -> None:
        """Links use correct relative paths from file in subdirectory."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a subdirectory and file
        subdir = project_dir / "config"
        subdir.mkdir()
        config_file = subdir / "standards.md"
        config_file.write_text("# Standards Config\n")

        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["config/standards.md"],
        )

        content = config_file.read_text(encoding="utf-8")
        # From config/, need to go up one level
        assert "../docs/coding-standard.agent.md" in content
        assert "../docs/engineering-standard.md" in content
        assert "../docs/engineering-policy.md" in content

    def test_relative_paths_from_file_in_nested_subdirectory(
        self, tmp_path: Path
    ) -> None:
        """Links use correct relative paths from file two levels deep."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a nested subdirectory and file
        nested_dir = project_dir / "docs" / "notes"
        nested_dir.mkdir(parents=True)
        nested_file = nested_dir / "standards.md"
        nested_file.write_text("# Nested Standards\n")

        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["docs/notes/standards.md"],
        )

        content = nested_file.read_text(encoding="utf-8")
        # From docs/notes/, need to go up two levels
        assert "../../docs/coding-standard.agent.md" in content
        assert "../../docs/engineering-standard.md" in content
        assert "../../docs/engineering-policy.md" in content


class TestPreservationOfHandEdits:
    """Tests that hand-written content is preserved."""

    def test_hand_edited_content_before_managed_block(self, tmp_path: Path) -> None:
        """Hand edits before managed block are preserved."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        original_preamble = (
            "# Claude Instructions\n\n## Setup\n\nCustom setup instructions.\n"
        )
        claude_path.write_text(original_preamble)

        # Add standards
        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        content = claude_path.read_text(encoding="utf-8")
        assert "Custom setup instructions" in content
        assert content.startswith("# Claude")

    def test_hand_edited_content_after_managed_block(self, tmp_path: Path) -> None:
        """Hand edits after managed block are preserved."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"

        # First add the standards
        claude_path.write_text("# Claude\n")
        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        # Now add custom content after the managed block
        content = claude_path.read_text(encoding="utf-8")
        custom_footer = "\n\n## Additional Resources\n\nSome custom resources.\n"
        claude_path.write_text(content + custom_footer)

        # Run linking again
        link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        # Custom content should still be there
        content_after = claude_path.read_text(encoding="utf-8")
        assert "Additional Resources" in content_after
        assert "custom resources" in content_after


class TestValidationErrors:
    """Tests for validation error messages."""

    def test_project_dir_missing(self) -> None:
        """Missing project directory returns actionable error."""
        result = link_standards_in_project_documents(
            project_dir="/nonexistent/path",
            target_files=["CLAUDE.md"],
        )

        assert result["success"] is False
        assert len(result["errors"]) > 0
        # Error message should be specific: "project_dir does not exist"
        assert "project_dir does not exist" in result["errors"][0]
        assert "/nonexistent/path" in result["errors"][0]

    def test_project_dir_is_not_directory(self, tmp_path: Path) -> None:
        """Project directory that is actually a file returns actionable error."""
        project_file = tmp_path / "not_a_dir"
        project_file.write_text("I am a file")

        result = link_standards_in_project_documents(
            project_dir=str(project_file),
            target_files=["CLAUDE.md"],
        )

        assert result["success"] is False
        # Error message should be specific: "project_dir is not a directory"
        assert "project_dir is not a directory" in result["errors"][0]

    def test_target_file_does_not_exist_without_creation(self, tmp_path: Path) -> None:
        """Target file that does not exist returns actionable error when creation not requested."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        assert result["success"] is False
        # Error message should be specific: "Target file does not exist"
        assert "Target file does not exist" in result["errors"][0]
        assert "CLAUDE.md" in result["errors"][0]

    def test_target_file_not_writable(self, tmp_path: Path) -> None:
        """Target file that is not writable returns actionable error."""
        # Skip this test if running as root, as chmod 444 does not prevent writes for root
        if os.geteuid() == 0:
            pytest.skip("Cannot test write protection when running as root")

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create a read-only file
        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude\n")
        claude_path.chmod(0o444)

        try:
            result = link_standards_in_project_documents(
                project_dir=str(project_dir),
                target_files=["CLAUDE.md"],
            )

            assert result["success"] is False
            # Error message should mention write failure
            assert (
                "writable" in result["errors"][0].lower()
                or "write" in result["errors"][0].lower()
            )
        finally:
            # Clean up: restore permissions so cleanup works
            claude_path.chmod(0o644)

    def test_target_path_escapes_project_with_parent_dir(self, tmp_path: Path) -> None:
        """Target path containing '..' that escapes project is rejected."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["../../etc/passwd"],
        )

        assert result["success"] is False
        # Error message should clearly state the path escapes the project
        assert "escapes project directory" in result["errors"][0]
        assert "../../etc/passwd" in result["errors"][0]

    def test_empty_target_files_list(self, tmp_path: Path) -> None:
        """Empty target files list returns no changes."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=[],
        )

        # Empty list means nothing to do - should still be success
        assert result["success"] is True
        assert len(result["updated_files"]) == 0


class TestResponseShape:
    """Tests for response shape and content."""

    def test_success_response_shape(self, tmp_path: Path) -> None:
        """Success response has correct shape."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        claude_path = project_dir / "CLAUDE.md"
        claude_path.write_text("# Claude\n")

        result = link_standards_in_project_documents(
            project_dir=str(project_dir),
            target_files=["CLAUDE.md"],
        )

        assert isinstance(result, dict)
        assert "success" in result
        assert "updated_files" in result
        assert result["success"] is True
        assert isinstance(result["updated_files"], list)

    def test_failure_response_shape(self) -> None:
        """Failure response has correct shape with errors list."""
        result = link_standards_in_project_documents(
            project_dir="/nonexistent",
            target_files=["CLAUDE.md"],
        )

        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is False
        assert "errors" in result
        assert isinstance(result["errors"], list)
        assert len(result["errors"]) > 0
