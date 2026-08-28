"""Managed-block write protocol for standards documents.

A managed block is a section of a document marked by opening and closing HTML
comments (e.g. <!-- audit:start id="agent-standard" --> and <!-- audit:end -->)
that the tool rewrites on each run. Hand edits outside the block are preserved
exactly. Missing, malformed, duplicated, or mismatched markers are reported with
specific, actionable error messages.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

__all__ = [
    "write_managed_block",
    "wrap_managed_block",
    "get_managed_block_opening_marker",
    "get_managed_block_closing_marker",
    "get_escaped_opening_marker_pattern",
    "get_escaped_opening_marker_pattern_any_id",
    "get_escaped_closing_marker_pattern",
    "get_document_title",
]

logger = logging.getLogger(__name__)


def get_managed_block_opening_marker(block_id: str) -> str:
    """Get the opening marker string for a managed block.

    Args:
        block_id: The block identifier (e.g. 'agent-standard').

    Returns:
        The opening marker string, e.g. '<!-- audit:start id="agent-standard" -->'
    """
    return f'<!-- audit:start id="{block_id}" -->'


def get_managed_block_closing_marker() -> str:
    """Get the closing marker string for a managed block.

    Returns:
        The closing marker string: '<!-- audit:end -->'
    """
    return "<!-- audit:end -->"


def get_escaped_opening_marker_pattern(block_id: str) -> str:
    """Get the regex pattern for an escaped opening marker with a specific block_id.

    This pattern matches the opening marker after it has been HTML-escaped
    (i.e. when & becomes &amp;, < becomes &lt;, > becomes &gt;, " becomes &quot;).

    Args:
        block_id: The block identifier to match in the pattern.

    Returns:
        A regex pattern string that matches the escaped opening marker.
    """
    escaped_id = re.escape(block_id)
    return rf"&lt;!-- audit:start id=&quot;{escaped_id}&quot; --&gt;"


def get_escaped_opening_marker_pattern_any_id() -> str:
    """Get the regex pattern for an escaped opening marker with any block_id.

    This pattern matches the opening marker after it has been HTML-escaped,
    accepting any block identifier.

    Returns:
        A regex pattern string that matches any escaped opening marker.
    """
    return r"&lt;!-- audit:start id=&quot;[^&]*&quot; --&gt;"


def get_escaped_closing_marker_pattern() -> str:
    """Get the regex pattern for an escaped closing marker.

    This pattern matches the closing marker after it has been HTML-escaped
    (i.e. when & becomes &amp;, < becomes &lt;, > becomes &gt;).

    Returns:
        A regex pattern string that matches the escaped closing marker.
    """
    return r"&lt;!-- audit:end --&gt;"


def wrap_managed_block(content: str, block_id: str) -> str:
    """Wrap content in managed-block markers.

    This is the single source of truth for managed-block marker format.
    All code that needs to wrap content with audit markers must use this function.

    Args:
        content: The content to wrap.
        block_id: The block identifier (e.g. 'agent-standard').

    Returns:
        Content wrapped with opening and closing markers.
    """
    opening = get_managed_block_opening_marker(block_id)
    closing = get_managed_block_closing_marker()
    return f"{opening}\n{content}\n{closing}"


def write_managed_block(file_path: Path, content: str, block_id: str) -> bool:
    """Write content to a managed block in a file, preserving hand edits outside.

    This function updates a standards document by replacing the content inside
    managed-block markers whilst preserving everything outside them. If the file
    does not exist, it is created with a minimal header and the managed block.

    Args:
        file_path: Path to the document file to write. If it does not exist, it
            is created with a minimal header.
        content: The new content to place inside the managed block.
        block_id: The identifier for this managed block (e.g. 'agent-standard',
            'human-standard', 'engineering-policy'). Used to match the opening
            marker's id attribute and to validate that the correct block is
            being updated.

    Returns:
        True if the write succeeded. False if the file exists but markers are
        missing, malformed, duplicated, or mismatched; a specific error message
        is logged in this case, and the file is not modified.

    Notes:
        - Opening marker format: <!-- audit:start id="<block_id>" -->
        - Closing marker format: <!-- audit:end -->
        - If the file does not exist, it is created with a title, date, and
          the managed block inside. The user may add content outside the block
          later, and the tool will preserve it on future runs.
    """
    file_path = Path(file_path)

    # If the file does not exist, create it with a minimal header
    if not file_path.exists():
        return _create_file_with_managed_block(file_path, content, block_id)

    # File exists; validate and update the managed block
    return _update_managed_block_in_existing_file(file_path, content, block_id)


def _create_file_with_managed_block(
    file_path: Path, content: str, block_id: str
) -> bool:
    """Create a new file with a minimal header and a managed block.

    The file is created with:
    - A title heading derived from the block_id
    - A date line with today's date
    - The managed block with the provided content inside
    """
    title = get_document_title(block_id)
    today = date.today().isoformat()
    wrapped_content = wrap_managed_block(content, block_id)

    file_content = f"# {title}\n\nDate: {today}\n\n{wrapped_content}\n"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(file_content, encoding="utf-8")
        return True
    except OSError as exc:
        logger.error(f"Could not create file {file_path}: {exc}")
        return False


def get_document_title(document_id: str) -> str:
    """Get the human-readable title for a standards document ID.

    Maps the three known standards document IDs to their human-readable titles.
    For unknown IDs, returns the raw ID unchanged.

    Args:
        document_id: The document identifier (e.g., 'agent-standard',
            'human-standard', 'engineering-policy').

    Returns:
        The human-readable title for the document.

    Examples:
        'agent-standard' -> 'Agent Coding Standard'
        'human-standard' -> 'Engineering Standard'
        'engineering-policy' -> 'Engineering Policy'
        'unknown-id' -> 'unknown-id'
    """
    titles = {
        "agent-standard": "Agent Coding Standard",
        "human-standard": "Engineering Standard",
        "engineering-policy": "Engineering Policy",
    }
    return titles.get(document_id, document_id)


def _update_managed_block_in_existing_file(
    file_path: Path, content: str, block_id: str
) -> bool:
    """Update a managed block in an existing file.

    Validates that the file contains exactly one managed block with the
    correct id, then replaces the content inside whilst preserving everything
    outside the markers.
    """
    try:
        file_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(f"Could not read file {file_path}: {exc}")
        return False

    # Validate the markers
    validation_result = _validate_markers(file_text, block_id)
    if validation_result is not True:
        logger.error(validation_result)  # validation_result is the error message
        return False

    # Extract content before and after the managed block
    before, after = _extract_surrounding_content(file_text, block_id)

    # Construct the new file content using the single source of truth for markers
    wrapped_content = wrap_managed_block(content, block_id)
    new_file_content = f"{before}{wrapped_content}{after}"

    try:
        file_path.write_text(new_file_content, encoding="utf-8")
        return True
    except OSError as exc:
        logger.error(f"Could not write file {file_path}: {exc}")
        return False


def _validate_markers(file_text: str, block_id: str) -> bool | str:
    """Validate that the file contains exactly one well-formed managed block.

    Returns:
        True if markers are valid.
        A specific error message string if there is an issue.
    """
    opening_pattern = rf'<!-- audit:start id="{re.escape(block_id)}" -->'
    closing_pattern = re.escape(get_managed_block_closing_marker())

    opening_matches = list(re.finditer(opening_pattern, file_text))
    closing_matches = list(re.finditer(closing_pattern, file_text))

    # Check for opening marker
    if len(opening_matches) == 0:
        # Check if there's an opening marker with a different id
        wrong_id_match = re.search(r'<!-- audit:start id="([^"]*)" -->', file_text)
        if wrong_id_match is not None:
            found_id = wrong_id_match.group(1)
            return (
                f"Opening marker has id '{found_id}' but expected '{block_id}'; "
                f"update the id attribute to '{block_id}' and try again"
            )
        # Check if there's a malformed opening marker
        if re.search(r"<!-- audit:start", file_text):
            return (
                f"Opening marker is malformed (missing or misquoted id attribute); "
                f'use this format: <!-- audit:start id="{block_id}" -->'
            )
        return (
            f'Opening marker missing; add <!-- audit:start id="{block_id}" --> '
            f"before the content to be generated"
        )

    # Check for multiple opening markers
    if len(opening_matches) > 1:
        return (
            f"Multiple opening markers found (expected exactly one); "
            f'remove the duplicate <!-- audit:start id="{block_id}" --> '
            f"and try again"
        )

    # Check for closing marker
    if len(closing_matches) == 0:
        return (
            "Closing marker missing; add <!-- audit:end --> "
            "after the generated content and try again"
        )

    # Check for multiple closing markers
    if len(closing_matches) > 1:
        return (
            "Multiple closing markers found (expected exactly one); "
            "remove the duplicate <!-- audit:end --> and try again"
        )

    # Check that opening comes before closing
    opening_pos = opening_matches[0].start()
    closing_pos = closing_matches[0].start()
    if opening_pos >= closing_pos:
        return (
            "Closing marker <!-- audit:end --> appears before opening marker "
            f'<!-- audit:start id="{block_id}" -->; reorder them and try again'
        )

    return True


def _extract_surrounding_content(file_text: str, block_id: str) -> tuple[str, str]:
    """Extract content before opening marker and after closing marker.

    Returns:
        A tuple of (before, after) where before is content before the opening
        marker and after is content after the closing marker (excluding the
        closing marker itself).

    This function assumes validation has already passed, so markers are
    guaranteed to exist and be well-formed.
    """
    opening_pattern = rf'<!-- audit:start id="{re.escape(block_id)}" -->'
    closing_pattern = re.escape(get_managed_block_closing_marker())

    opening_match = re.search(opening_pattern, file_text)
    closing_match = re.search(closing_pattern, file_text)

    assert opening_match is not None
    assert closing_match is not None

    before = file_text[: opening_match.start()]
    after = file_text[closing_match.end() :]

    return before, after
