"""Tests for the stack mismatch page (src/engineering_audit/config_page.py)."""

from __future__ import annotations

import http.client
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

from engineering_audit.config_page import ConfigServer, ConfigTimeoutError
from engineering_audit.rules import load_pack

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _fetch_csrf_token_from_stack_mismatch(url: str) -> str:
    """GET the stack mismatch page and pull the CSRF token from it."""
    with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
        page = resp.read().decode("utf-8")
    match = _CSRF_RE.search(page)
    assert match is not None, "stack mismatch page did not render a csrf_token field"
    return match.group(1)


def _post_stack_choice(url: str, fields: dict[str, object]) -> tuple[int, str]:
    """POST url-encoded fields to /submit-stack-choice and return (status, body)."""
    payload = urlencode(fields, doseq=True).encode("utf-8")
    host_port = url[len("http://") :].rstrip("/")
    host, port_str = host_port.split(":")
    conn = http.client.HTTPConnection(host, int(port_str), timeout=5)
    try:
        conn.putrequest("POST", "/submit-stack-choice")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders()
        conn.send(payload)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        return resp.status, body
    finally:
        conn.close()


@pytest.fixture
def domains():
    pack = load_pack(FIXTURE_PACK)
    return pack.domains


class MockDetectedStack:
    """Mock DetectedStack for testing."""

    def __init__(self, identifiers=None, evidence=None):
        self.identifiers = identifiers or ("python", "django")
        self.evidence = evidence or {}


class MockStackEvidence:
    """Mock StackEvidence for testing."""

    def __init__(self, file_path="pyproject.toml", dependency_or_line="django==4.2"):
        self.file_path = file_path
        self.dependency_or_line = dependency_or_line


def test_stack_mismatch_page_renders_with_both_stacks(domains) -> None:
    """The page should render both the grill and observed stacks."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        # Set up stack mismatch data
        grill_stack = frozenset(("python", "fastapi"))
        observed_stack = MockDetectedStack(
            identifiers=("python", "django"),
            evidence={
                "python": MockStackEvidence("pyproject.toml", "python>=3.10"),
                "django": MockStackEvidence("requirements.txt", "django==4.2"),
            },
        )
        difference = {
            "grill": ["python", "fastapi"],
            "observed": ["python", "django"],
        }
        srv.set_stack_mismatch_data(grill_stack, observed_stack, difference)

        # Fetch the page
        with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
            page = resp.read().decode("utf-8")

        # Check that both stacks are mentioned
        assert "Grill Stack" in page
        assert "Observed Stack" in page
        assert "fastapi" in page
        assert "django" in page
    finally:
        srv.shutdown()


def test_stack_mismatch_page_shows_evidence(domains) -> None:
    """The page should display evidence for the observed stack."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        grill_stack = frozenset(("python",))
        observed_stack = MockDetectedStack(
            identifiers=("python", "django"),
            evidence={
                "django": MockStackEvidence("requirements.txt", "django==4.2"),
            },
        )
        srv.set_stack_mismatch_data(grill_stack, observed_stack, {"diff": "some diff"})

        with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
            page = resp.read().decode("utf-8")

        # Check that evidence is shown
        assert "requirements.txt" in page
        assert "django==4.2" in page
    finally:
        srv.shutdown()


def test_stack_mismatch_page_escapes_evidence(domains) -> None:
    """Evidence containing HTML/script characters should be escaped."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        grill_stack = frozenset(("python",))
        observed_stack = MockDetectedStack(
            identifiers=("malicious",),
            evidence={
                "malicious": MockStackEvidence(
                    file_path="<script>alert('xss')</script>",
                    dependency_or_line="django<img src=x onerror=alert(1)>",
                ),
            },
        )
        srv.set_stack_mismatch_data(grill_stack, observed_stack, {})

        with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
            page = resp.read().decode("utf-8")

        # Script tags should be escaped
        assert "<script>" not in page
        assert "&lt;script&gt;" in page
        # img tag should be escaped
        assert "<img src" not in page
        assert "&lt;img" in page
    finally:
        srv.shutdown()


def test_stack_mismatch_post_with_grill_choice(domains) -> None:
    """Posting 'grill' should return 'grill' from wait_stack_choice."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        token = _fetch_csrf_token_from_stack_mismatch(url)
        status, body = _post_stack_choice(
            url,
            {
                "action": "grill",
                "csrf_token": token,
            },
        )

        assert status == 200
        choice = srv.wait_stack_choice(timeout_s=1.0)
        assert choice == "grill"
    finally:
        srv.shutdown()


def test_stack_mismatch_post_with_audit_choice(domains) -> None:
    """Posting 'audit' should return 'audit' from wait_stack_choice."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        token = _fetch_csrf_token_from_stack_mismatch(url)
        status, body = _post_stack_choice(
            url,
            {
                "action": "audit",
                "csrf_token": token,
            },
        )

        assert status == 200
        choice = srv.wait_stack_choice(timeout_s=1.0)
        assert choice == "audit"
    finally:
        srv.shutdown()


def test_stack_mismatch_post_without_csrf_token_is_rejected(domains) -> None:
    """Posting without a CSRF token should be rejected."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        status, body = _post_stack_choice(
            url,
            {
                "action": "grill",
            },
        )

        assert status == 403
        assert "csrf" in body.lower() or "CSRF" in body
    finally:
        srv.shutdown()


def test_stack_mismatch_post_with_wrong_csrf_token_is_rejected(domains) -> None:
    """Posting with a wrong CSRF token should be rejected."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        # Get a valid token so the page has been served
        _fetch_csrf_token_from_stack_mismatch(url)

        status, _body = _post_stack_choice(
            url,
            {
                "action": "grill",
                "csrf_token": "not-the-real-token",
            },
        )

        assert status == 403
    finally:
        srv.shutdown()


def test_stack_mismatch_post_with_unknown_action_is_rejected(domains) -> None:
    """Posting with an unknown action should be rejected."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        token = _fetch_csrf_token_from_stack_mismatch(url)
        status, _body = _post_stack_choice(
            url,
            {
                "action": "invalid",
                "csrf_token": token,
            },
        )

        assert status == 400
    finally:
        srv.shutdown()


def test_wait_stack_choice_times_out_on_fresh_server(domains) -> None:
    """wait_stack_choice should raise ConfigTimeoutError on timeout."""
    srv = ConfigServer(domains)
    try:
        srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        with pytest.raises(ConfigTimeoutError):
            srv.wait_stack_choice(timeout_s=0.2)
    finally:
        srv.shutdown()


def test_stack_mismatch_page_has_csrf_token_field(domains) -> None:
    """The page should contain a CSRF token hidden field."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )

        with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
            page = resp.read().decode("utf-8")

        # Should have a csrf_token field
        assert 'name="csrf_token"' in page
        assert 'value="' in page
    finally:
        srv.shutdown()


def test_stack_mismatch_page_explains_consequences(domains) -> None:
    """The page should explain the consequences of each choice."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python", "fastapi")),
            MockDetectedStack(),
            {},
        )

        with urllib.request.urlopen(url + "stack-mismatch", timeout=5) as resp:
            page = resp.read().decode("utf-8")

        # Should mention consequences
        assert "Consequences" in page or "consequences" in page
        assert "Use Grill Stack" in page or "use grill" in page.lower()
        assert "Use Audit Stack" in page or "use audit" in page.lower()
    finally:
        srv.shutdown()
