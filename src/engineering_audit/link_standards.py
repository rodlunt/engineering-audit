"""Add links to standards documents in project files.

Adds managed-block links to the three standards documents in specified
project files (CLAUDE.md, AGENTS.md, README.md, or custom files), preserving
hand-edited content outside the managed block. The tool is idempotent: running
it twice produces identical results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engineering_audit.managed_blocks import (
    wrap_managed_block,
    write_managed_block,
)

__all__ = ["link_standards_in_project_documents"]

logger = logging.getLogger(__name__)


def link_standards_in_project_documents(
    project_dir: str,
    target_files: list[str],
) -> dict[str, Any]:
    """Add links to standards documents in specified project files.

    Adds managed-block links to the three standards documents (Agent Coding
    Standard, Engineering Standard, Engineering Policy) in the specified
    target files. The links use relative paths from each target file to
    docs/. The tool is idempotent: running it twice produces identical results.

    Args:
        project_dir: Path to the project root directory. Must exist and be
            a directory. The three standards documents are expected to be in
            project_dir/docs/ relative to this directory.
        target_files: List of file names or relative paths to update. Files
            must exist (unless creation is explicitly requested). Only these
            files will be touched. Examples: ["CLAUDE.md"], ["AGENTS.md", "README.md"],
            ["config/standards.md"]. Must not be empty.

    Returns:
        Dictionary with keys:
        - success: Boolean indicating success.
        - updated_files: List of file paths that were successfully updated (if success=True).
        - errors: List of error message strings (if success=False).

    Notes:
        The tool uses a managed block with id='standards-links' to mark the
        section where it adds links, enabling idempotency. Hand-edited content
        outside the managed block is preserved.
    """
    project_path = Path(project_dir)

    # Validate project directory
    if not project_dir:
        return {
            "success": False,
            "errors": [
                "project_dir is required. Provide the path to the project root directory."
            ],
        }

    if not project_path.exists():
        return {
            "success": False,
            "errors": [
                f"project_dir does not exist: {project_dir}. "
                "Please provide a valid path to an existing project directory."
            ],
        }

    if not project_path.is_dir():
        return {
            "success": False,
            "errors": [
                f"project_dir is not a directory: {project_dir}. "
                "Please provide a path to a valid project directory, not a file."
            ],
        }

    # Validate target files
    if not target_files:
        return {
            "success": True,
            "updated_files": [],
        }

    updated_files: list[str] = []
    errors: list[str] = []

    for target_file in target_files:
        file_path = project_path / target_file

        # Validate that the resolved path is within the project directory
        try:
            file_path.resolve().relative_to(project_path.resolve())
        except ValueError:
            errors.append(
                f"Target file escapes project directory: {target_file}. "
                f"Path must be within the project root. "
                f"Remove '..' or provide a path that stays within the project."
            )
            continue

        # Check if file exists
        if not file_path.exists():
            errors.append(
                f"Target file does not exist: {target_file}. "
                f"Please create the file or provide a different path."
            )
            continue

        # Check if it's actually a file, not a directory
        if not file_path.is_file():
            errors.append(
                f"Target path is not a file: {target_file}. "
                f"Please provide a path to a file, not a directory."
            )
            continue

        # Generate relative path from this file to docs/
        relative_docs_path = _compute_relative_path_to_docs(file_path, project_path)

        # Generate the standards links content
        standards_content = _generate_standards_links_content(relative_docs_path)

        # Update the file with managed block
        try:
            success = _update_file_with_standards_block(file_path, standards_content)
            if success:
                updated_files.append(target_file)
            else:
                # Error logged; capture for response
                errors.append(
                    f"Failed to write standards links to {target_file}. "
                    "Ensure the file is writable."
                )
        except OSError as exc:
            errors.append(
                f"Could not write to {target_file}: {exc}. "
                "Please ensure the file is writable."
            )

    if errors:
        return {
            "success": False,
            "errors": errors,
        }

    return {
        "success": True,
        "updated_files": updated_files,
    }


def _compute_relative_path_to_docs(file_path: Path, project_path: Path) -> str:
    """Compute the relative path from a file to the docs/ directory.

    Args:
        file_path: Absolute path to the target file.
        project_path: Absolute path to the project root.

    Returns:
        Relative path from file to docs/ (e.g., "docs/" or "../docs/").
    """
    # Get the directory containing the file
    file_dir = file_path.parent

    # Calculate relative path from file's directory to project root
    try:
        rel_to_root = file_dir.relative_to(project_path)
    except ValueError:
        # file_path is not relative to project_path; shouldn't happen
        # but fall back to using ".." for each level up
        file_parts = file_path.parts
        project_parts = project_path.parts

        # Find common ancestor
        common_depth = 0
        for fp, pp in zip(file_parts, project_parts):
            if fp == pp:
                common_depth += 1
            else:
                break

        levels_up = len(project_parts) - common_depth
        rel_to_root = Path("/".join([".."] * levels_up)) if levels_up > 0 else Path(".")

    # Compute how many levels up we need to go to reach project root
    levels_up = len(rel_to_root.parts)

    # Build path to docs/
    if levels_up == 0:
        # File is in project root
        return "docs/"
    else:
        # Need to go up N levels, then into docs/
        return "/".join([".."] * levels_up) + "/docs/"


def _update_file_with_standards_block(file_path: Path, standards_content: str) -> bool:
    """Update a file with standards links in a managed block.

    If the file already has managed-block markers for standards-links,
    updates the content between them. If it has no markers, appends the
    managed block to the end. This ensures idempotency and preserves
    hand-written content.

    Args:
        file_path: Path to the file to update.
        standards_content: Content to place inside the managed block.

    Returns:
        True if successful, False otherwise. Errors are logged.
    """
    try:
        file_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(f"Could not read file {file_path}: {exc}")
        return False

    # Check if file already has the standards-links markers
    if '<!-- audit:start id="standards-links" -->' in file_text:
        # Use write_managed_block to update existing markers
        return write_managed_block(file_path, standards_content, "standards-links")
    else:
        # File exists but has no markers; append them at the end
        wrapped_content = wrap_managed_block(standards_content, "standards-links")
        new_content = file_text.rstrip() + "\n\n" + wrapped_content + "\n"

        try:
            file_path.write_text(new_content, encoding="utf-8")
            return True
        except OSError as exc:
            logger.error(f"Could not write file {file_path}: {exc}")
            return False


def _generate_standards_links_content(relative_docs_path: str) -> str:
    """Generate the standards links markdown content.

    Args:
        relative_docs_path: Relative path from target file to docs/ (e.g., "docs/" or "../docs/").

    Returns:
        Markdown content with links to the three standards documents.
    """
    agent_link = f"{relative_docs_path}coding-standard.agent.md"
    human_link = f"{relative_docs_path}engineering-standard.md"
    policy_link = f"{relative_docs_path}engineering-policy.md"

    return (
        "## Engineering Standards and Policies\n\n"
        "This project is committed to the following engineering standards:\n\n"
        f"- [Agent Coding Standard]({agent_link}) for the rules Claude Code follows when writing code.\n"
        f"- [Engineering Standard]({human_link}) for the team's engineering practices and requirements.\n"
        f"- [Engineering Policy]({policy_link}) for project policies and decision records.\n"
    )
