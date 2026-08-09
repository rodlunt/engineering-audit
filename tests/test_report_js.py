"""Wires tests/js/report.test.js (Node's built-in test runner, node --test)
into the pytest suite, so `uv run pytest` exercises the report page's
client-side JS too, not just the Python renderer around it.

Node is present on this project's CI runners. On a dev machine without
node installed, the JS suite cannot run at all; per this project's rule
that a skipped check must never be representable as a pass, that case is
reported as an explicit pytest skip with a reason, never silently green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_TEST_FILE = Path(__file__).parent / "js" / "report.test.js"


def test_report_js_suite_passes_under_node_test_runner() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the report page's JS test "
            f"suite ({_JS_TEST_FILE}) did not run. Node is present on CI runners; "
            "install node to run this suite locally."
        )

    result = subprocess.run(
        [node, "--test", str(_JS_TEST_FILE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --test {_JS_TEST_FILE} failed (exit code {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
