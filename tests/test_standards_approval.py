"""Tests for the standards approval page functionality.

This module tests the pure functions that derive summary counts from rule sets,
build diff models for displaying changes, and render the approval page HTML.
"""

from __future__ import annotations

import html

from engineering_audit.standards import Rule, RuleSet, RuleStatus
from engineering_audit.standards_approval import (
    DiffModel,
    derive_summary_counts,
    build_diff_model,
    highlight_managed_block_markers,
)


class TestDeriveSummaryCounts:
    """Tests for derive_summary_counts function."""

    def test_empty_rule_set_returns_zeros(self) -> None:
        """An empty rule set returns all zero counts."""
        rule_set = RuleSet(version="1.0", project="test", rules=[])
        counts = derive_summary_counts(rule_set)
        assert counts.new_rules == 0
        assert counts.upgraded_to_verified == 0
        assert counts.findings_recorded == 0
        assert counts.not_applicable == 0

    def test_new_rules_are_counted(self) -> None:
        """Provisional rules are counted as 'new'."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="New rule 1",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="D01-R02",
                    domain_id="d01",
                    text_short="New rule 2",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-27",
                ),
            ],
        )
        counts = derive_summary_counts(rule_set)
        assert counts.new_rules == 2
        assert counts.upgraded_to_verified == 0

    def test_upgraded_to_verified_rules_are_counted(self) -> None:
        """Verified-pass rules are counted as upgraded to verified."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Verified rule",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_PASS.value,
                    verified_date="2026-08-27",
                ),
            ],
        )
        counts = derive_summary_counts(rule_set)
        assert counts.upgraded_to_verified == 1

    def test_findings_are_counted(self) -> None:
        """Rules with verified-finding status are counted as findings."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Finding 1",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_FINDING.value,
                    verified_date="2026-08-27",
                    severity="high",
                    finding_details={
                        "path": "src/file.py",
                        "line": 42,
                        "issue_title": "Issue",
                        "issue_body": "Details",
                    },
                ),
            ],
        )
        counts = derive_summary_counts(rule_set)
        assert counts.findings_recorded == 1

    def test_not_applicable_rules_are_counted(self) -> None:
        """Rules with verified-not-applicable status are counted."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Not applicable",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_NOT_APPLICABLE.value,
                    verified_date="2026-08-27",
                ),
            ],
        )
        counts = derive_summary_counts(rule_set)
        assert counts.not_applicable == 1

    def test_mixed_rule_set_counts_correctly(self) -> None:
        """A mixed rule set is counted correctly."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Provisional",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="D01-R02",
                    domain_id="d01",
                    text_short="Pass",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_PASS.value,
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="D01-R03",
                    domain_id="d01",
                    text_short="Finding",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_FINDING.value,
                    verified_date="2026-08-27",
                    severity="medium",
                    finding_details={
                        "path": "src/file.py",
                        "line": 1,
                        "issue_title": "Issue",
                        "issue_body": "Details",
                    },
                ),
                Rule(
                    rule_id="D02-R01",
                    domain_id="d02",
                    text_short="Not applicable",
                    text_body="Body.",
                    source="rules-pack",
                    status=RuleStatus.VERIFIED_NOT_APPLICABLE.value,
                    verified_date="2026-08-27",
                ),
            ],
        )
        counts = derive_summary_counts(rule_set)
        assert counts.new_rules == 1
        assert counts.upgraded_to_verified == 1
        assert counts.findings_recorded == 1
        assert counts.not_applicable == 1


class TestBuildDiffModel:
    """Tests for build_diff_model function."""

    def test_diff_model_with_file_exists(self) -> None:
        """Diff model is built correctly when file exists."""
        current_content = "# Old Title\n\nOld content here."
        proposed_content = "# New Title\n\nNew content here."
        diff = build_diff_model(
            current_content=current_content,
            proposed_content=proposed_content,
            document_id="agent-standard",
        )
        assert isinstance(diff, DiffModel)
        assert diff.document_id == "agent-standard"
        assert diff.current_content == current_content
        assert diff.proposed_content == proposed_content
        assert diff.file_exists is True

    def test_diff_model_with_file_not_exists(self) -> None:
        """Diff model is built correctly when file does not exist."""
        current_content = None
        proposed_content = "# New Title\n\nNew content here."
        diff = build_diff_model(
            current_content=current_content,
            proposed_content=proposed_content,
            document_id="human-standard",
        )
        assert isinstance(diff, DiffModel)
        assert diff.document_id == "human-standard"
        assert diff.current_content is None
        assert diff.proposed_content == proposed_content
        assert diff.file_exists is False

    def test_managed_block_markers_are_present_in_proposed_content(self) -> None:
        """Proposed content includes managed-block markers."""
        proposed = (
            '<!-- audit:start id="agent-standard" -->\nContent\n<!-- audit:end -->'
        )
        diff = build_diff_model(
            current_content=None,
            proposed_content=proposed,
            document_id="agent-standard",
        )
        assert "<!-- audit:start" in diff.proposed_content
        assert "<!-- audit:end -->" in diff.proposed_content


class TestHighlightManagedBlockMarkers:
    """Tests for highlight_managed_block_markers function."""

    def test_markers_are_wrapped_in_span(self) -> None:
        """Opening and closing markers are wrapped in highlight span."""
        # Content passed to the function is already HTML-escaped
        import html

        content = html.escape(
            'Line 1\n<!-- audit:start id="test" -->\nContent\n<!-- audit:end -->\nLine 5'
        )
        result = highlight_managed_block_markers(content)
        assert '<span class="managed-block-marker">' in result
        assert "&lt;!-- audit:start" in result
        assert "&lt;!-- audit:end --&gt;" in result

    def test_content_with_no_markers_unchanged(self) -> None:
        """Content with no markers is returned unchanged."""
        content = "Line 1\nLine 2\nLine 3"
        result = highlight_managed_block_markers(content)
        assert result == content

    def test_html_escaped_content_stays_escaped(self) -> None:
        """HTML-escaped content remains escaped after marker highlighting."""
        import html

        # Original content with script tag and markers
        original = '<script>alert(1)</script>\n<!-- audit:start id="test" -->\nContent\n<!-- audit:end -->'
        # Escape once, as would happen in config_page.py
        content = html.escape(original)
        result = highlight_managed_block_markers(content)
        # The escaped script tag should still be escaped
        assert "&lt;script&gt;" in result
        # The marker should be wrapped in span with escaped marker
        assert '<span class="managed-block-marker">&lt;!-- audit:start' in result

    def test_xss_attack_in_content_is_prevented(self) -> None:
        """Raw HTML/script tags in content are already escaped before highlighting."""
        # Simulating HTML-escaped content (as it would come from html.escape)
        import html

        content = html.escape(
            '<script>alert(1)</script>\n<!-- audit:start id="test" -->\nContent\n<!-- audit:end -->'
        )
        result = highlight_managed_block_markers(content)
        # The raw script tag should never appear unescaped in the result
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_multiple_markers_are_all_highlighted(self) -> None:
        """All marker lines in content are highlighted."""
        import html

        content = html.escape(
            "Before\n"
            '<!-- audit:start id="agent" -->\n'
            "Agent content\n"
            "<!-- audit:end -->\n"
            "Middle\n"
        )
        result = highlight_managed_block_markers(content)
        # Both markers should be wrapped
        assert result.count('<span class="managed-block-marker">') >= 2
        assert "&lt;!-- audit:start" in result
        assert "&lt;!-- audit:end --&gt;" in result

    def test_opening_and_closing_markers_are_distinct_spans(self) -> None:
        """Opening and closing markers are each wrapped in their own span."""
        import html

        content = html.escape(
            '<!-- audit:start id="test" -->\nContent\n<!-- audit:end -->'
        )
        result = highlight_managed_block_markers(content)
        # Count the opening spans
        span_count = result.count('<span class="managed-block-marker">')
        close_span_count = result.count("</span>")
        # Should have at least 2 opening and 2 closing spans (one for each marker)
        assert span_count >= 2
        assert close_span_count >= 2

    def test_marker_with_different_ids_are_highlighted(self) -> None:
        """Markers with different id attributes are all highlighted."""
        import html

        content = html.escape(
            '<!-- audit:start id="human-standard" -->\nContent\n<!-- audit:end -->\n'
        )
        result = highlight_managed_block_markers(content)
        assert (
            '<span class="managed-block-marker">&lt;!-- audit:start id=&quot;human-standard&quot; --&gt;'
            in result
        )
        assert (
            '<span class="managed-block-marker">&lt;!-- audit:end --&gt;</span>'
            in result
        )

    def test_real_managed_block_from_api_is_highlighted(self) -> None:
        """A real managed block generated by managed_blocks.py is highlighted.

        This test ensures that highlight_managed_block_markers uses patterns
        derived from managed_blocks.py, so they cannot drift apart.
        The test generates a real managed block using wrap_managed_block,
        escapes it, and verifies it gets highlighted correctly.
        """
        from engineering_audit.managed_blocks import wrap_managed_block

        # Generate a real managed block using the API
        block_id = "test-standard"
        content = "Line 1\nLine 2\nLine 3"
        real_block = wrap_managed_block(content, block_id)

        # HTML-escape it as it would be in the UI
        escaped_block = html.escape(real_block)

        # Highlight it
        result = highlight_managed_block_markers(escaped_block)

        # Verify opening marker is highlighted
        assert '<span class="managed-block-marker">' in result
        assert f"&lt;!-- audit:start id=&quot;{block_id}&quot; --&gt;" in result

        # Verify closing marker is highlighted
        assert "&lt;!-- audit:end --&gt;" in result

        # Verify the span wraps the markers
        assert (
            f'<span class="managed-block-marker">&lt;!-- audit:start id=&quot;{block_id}&quot; --&gt;</span>'
            in result
        )
        assert (
            '<span class="managed-block-marker">&lt;!-- audit:end --&gt;</span>'
            in result
        )
