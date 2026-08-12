"""Wires the report page's JS test suites (tests/js/*.test.js, Node's
built-in test runner, node --test) into the pytest suite, so `uv run pytest`
exercises the report page's client-side JS too, not just the Python
renderer around it.

Node is present on this project's CI runners. On a dev machine without
node installed, the JS suite cannot run at all; per this project's rule
that a skipped check must never be representable as a pass, that case is
reported as an explicit pytest skip with a reason, never silently green.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_audit.feedback import build_feedback_sections
from engineering_audit.schema import RunMeta

_JS_TEST_FILE = Path(__file__).parent / "js" / "report.test.js"
_FEEDBACK_PAYLOAD_TEST_FILE = Path(__file__).parent / "js" / "feedback_payload.test.js"


def _require_node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the report page's JS test "
            "suite did not run. Node is present on CI runners; install node to run "
            "this suite locally."
        )
    return node


def test_report_js_suite_passes_under_node_test_runner() -> None:
    node = _require_node()

    result = subprocess.run(
        [node, "--test", str(_JS_TEST_FILE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --test {_JS_TEST_FILE} failed (exit code {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def _current_feedback_section_keys() -> list[str]:
    """The consent-gated section keys build_feedback_sections currently
    returns (every key except run_metadata, which is always included and
    never a consent choice), computed fresh from the real function rather
    than hard-coded, so the Node test this feeds is checked against
    whatever the key list actually is today, not whatever it was when this
    test was written (issue #120).

    An empty meta and no domain results is enough: build_feedback_sections'
    key *names* do not depend on run content, only their text does, and
    only the names matter here.
    """
    meta = RunMeta(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:10:00+00:00",
    )
    sections = build_feedback_sections(meta, {})
    return [key for key in sections if key != "run_metadata"]


def test_report_js_build_feedback_payload_handles_every_current_section_key() -> None:
    """Issue #120: report.js's buildFeedbackPayload must assemble every
    section key build_feedback_sections (feedback.py) currently returns.

    A section wired into feedback.py, schema.py's TelemetryConsent and
    report.py's embedded JSON block, but missed in report.js, would pass
    every other test in this suite: the Python cross-check in
    test_report.py never looks at report.js, and the browser page would
    still render a tickable checkbox backed by real data. Only this test,
    which actually runs buildFeedbackPayload under Node with the current
    key list, catches a section silently dropped from the assembled
    feedback text.
    """
    node = _require_node()
    keys = _current_feedback_section_keys()
    assert keys, "build_feedback_sections returned no consent-gated keys to check"

    result = subprocess.run(
        [node, "--test", str(_FEEDBACK_PAYLOAD_TEST_FILE)],
        capture_output=True,
        text=True,
        env={**os.environ, "FEEDBACK_SECTION_KEYS": json.dumps(keys)},
    )
    assert result.returncode == 0, (
        f"node --test {_FEEDBACK_PAYLOAD_TEST_FILE} failed (exit code {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
