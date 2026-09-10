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


# ---------------------------------------------------------------------------
# Reachability: /stack-mismatch-ready plus the poller on config-submitted.html
# (issue 07's trailing note; mirrors issue 05's /approval-ready fix)
# ---------------------------------------------------------------------------


def test_stack_mismatch_ready_endpoint_answers_404_before_data_is_set(
    domains,
) -> None:
    """Same shape as /approval-ready: a poller needs a cheap way to ask
    "is there a mismatch waiting for me" without rendering the (larger)
    stack-mismatch page itself."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        try:
            urllib.request.urlopen(url + "stack-mismatch-ready", timeout=5)
            pytest.fail("Expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_stack_mismatch_ready_endpoint_answers_204_once_data_is_set(
    domains,
) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python", "fastapi")),
            MockDetectedStack(),
            {},
        )
        with urllib.request.urlopen(url + "stack-mismatch-ready", timeout=5) as resp:
            assert resp.status == 204
            assert resp.read() == b""
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# The stack-choice window state guard (mirrors 51f7cbb's approval-side fix)
# and the handoff into a standards-approval step that always follows a
# resolved stack mismatch in the same run.
# ---------------------------------------------------------------------------


def _give_approval_data(srv: ConfigServer) -> None:
    """Mirrors test_config_page.py's _set_approval_data: gives a
    ConfigServer some approval data the same way server.py does before
    calling wait_approval, so a test can exercise the stack-choice ->
    approval handoff inside a single run."""
    from engineering_audit.standards import RuleSet
    from engineering_audit.standards_approval import (
        build_diff_model,
        derive_rule_set_summary_counts,
    )

    rule_set = RuleSet(version="1.0", project="test", rules=[])
    proposed_content = (
        '<!-- audit:start id="agent-standard" -->\nContent\n<!-- audit:end -->'
    )
    diffs = [
        build_diff_model(None, proposed_content, "agent-standard"),
        build_diff_model(None, proposed_content, "human-standard"),
        build_diff_model(None, proposed_content, "engineering-policy"),
    ]
    counts = derive_rule_set_summary_counts(rule_set)
    srv.set_approval_data(diffs, counts)


def test_stack_choice_then_approval_handoff_page_has_poller_not_close_invitation(
    domains,
) -> None:
    """The important regression test: a resolved (non-timeout) stack
    mismatch is always followed by a standards-approval step in the same
    run (see server.py's ordering around wait_stack_choice /
    set_approval_data / wait_approval). The mismatch page navigation that
    got the user here unloads config-submitted.html, killing both of its
    pollers, so the page served after a valid stack-choice POST must carry
    its own approval-readiness poller and navigation target, and must never
    invite the user to close the tab while a review may still be coming.
    """
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )
        token = _fetch_csrf_token_from_stack_mismatch(url)
        status, body = _post_stack_choice(url, {"action": "grill", "csrf_token": token})

        assert status == 200
        assert "close this window" not in body.lower()
        assert "approval-ready" in body
        assert "approve-standards" in body

        choice = srv.wait_stack_choice(timeout_s=1.0)
        assert choice == "grill"

        # Same run: a standards approval step follows immediately, exactly
        # as server.py does it.
        _give_approval_data(srv)
        with urllib.request.urlopen(url + "approval-ready", timeout=5) as resp:
            assert resp.status == 204
        with urllib.request.urlopen(url + "approve-standards", timeout=5) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_stack_mismatch_ready_endpoint_not_ready_once_choice_consumed(
    domains,
) -> None:
    """Same shape as the approval-ready equivalent: once wait_stack_choice
    has consumed the one decision it was waiting for, the readiness poll
    must stop telling a browser there is still something to navigate to."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )
        with urllib.request.urlopen(url + "stack-mismatch-ready", timeout=5) as resp:
            assert resp.status == 204

        token = _fetch_csrf_token_from_stack_mismatch(url)
        status, _body = _post_stack_choice(
            url, {"action": "grill", "csrf_token": token}
        )
        assert status == 200
        assert srv.wait_stack_choice(timeout_s=1.0) == "grill"

        try:
            urllib.request.urlopen(url + "stack-mismatch-ready", timeout=5)
            pytest.fail("Expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_stack_mismatch_ready_endpoint_not_ready_after_timeout(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )
        with pytest.raises(ConfigTimeoutError):
            srv.wait_stack_choice(timeout_s=0.2)
        try:
            urllib.request.urlopen(url + "stack-mismatch-ready", timeout=5)
            pytest.fail("Expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_stack_choice_post_before_window_open_is_rejected(domains) -> None:
    """A choice POSTed with the page's long-lived CSRF token, before
    set_stack_mismatch_data has ever been called, cannot have come from
    someone who actually saw the stack-mismatch page. It must be rejected
    with a non-2xx status and an honest body, and must not record a
    choice."""
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        # No call to set_stack_mismatch_data: the window has never opened.
        status, body = _post_stack_choice(
            url, {"action": "grill", "csrf_token": srv._csrf_token}
        )

        assert status not in (200, 201, 202, 203, 204)
        # The success page's own heading, not merely the word "recorded"
        # (the honest rejection body legitimately uses that word too, as in
        # "no choice can be recorded until...").
        assert "stack choice recorded</h1>" not in body.lower()
        assert "not open" in body.lower()
        assert srv._stack_choice is None
    finally:
        srv.shutdown()


def test_stack_choice_post_after_first_consumed_is_rejected(domains) -> None:
    """A second stack-choice POST arriving after wait_stack_choice has
    already consumed the decision (a reloaded tab, or a second click) must
    not be told it succeeded: nothing was applied and nothing ever will be
    for this submission."""
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
            url, {"action": "grill", "csrf_token": token}
        )
        assert status == 200
        assert srv.wait_stack_choice(timeout_s=1.0) == "grill"

        status2, body2 = _post_stack_choice(
            url, {"action": "audit", "csrf_token": token}
        )
        assert status2 == 410
        assert "was not recorded" in body2.lower()
        assert "closed" in body2.lower()
    finally:
        srv.shutdown()


def test_stack_choice_post_after_timeout_is_rejected(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        srv.set_stack_mismatch_data(
            frozenset(("python",)),
            MockDetectedStack(),
            {},
        )
        with pytest.raises(ConfigTimeoutError):
            srv.wait_stack_choice(timeout_s=0.2)

        status, body = _post_stack_choice(
            url, {"action": "grill", "csrf_token": srv._csrf_token}
        )
        assert status == 410
        assert "was not recorded" in body.lower()
        assert "closed" in body.lower()
    finally:
        srv.shutdown()
