"""Tests for the localhost config page (src/engineering_audit/config_page.py)."""

from __future__ import annotations

import http.client
import re
import urllib.request
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest

from engineering_audit.config_page import ConfigServer, ConfigTimeoutError
from engineering_audit.rules import load_pack

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _fetch_csrf_token(url: str) -> str:
    """GET the config page and pull the per-run CSRF token out of its
    hidden form field, the same way a real browser submission would carry
    it forward: never hardcoded, always read back from the page actually
    served."""
    with urllib.request.urlopen(url, timeout=5) as resp:
        page = resp.read().decode("utf-8")
    match = _CSRF_RE.search(page)
    assert match is not None, "config page did not render a csrf_token hidden field"
    return match.group(1)


def _post(url: str, fields: dict[str, object]) -> tuple[int, str]:
    """POST url-encoded fields to /submit and return (status, body text),
    without raising on a non-2xx status: several tests here want to inspect
    an error response's body, which urllib.request.urlopen only exposes via
    an exception."""
    payload = urlencode(fields, doseq=True).encode("utf-8")
    host_port = url[len("http://") :].rstrip("/")
    host, port_str = host_port.split(":")
    conn = http.client.HTTPConnection(host, int(port_str), timeout=5)
    try:
        conn.putrequest("POST", "/submit")
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


def test_start_returns_a_localhost_url(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        assert url.startswith("http://127.0.0.1:")
    finally:
        srv.shutdown()


def test_get_page_contains_domains_and_locked_metadata_row(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        for domain in domains:
            assert domain.title in page
            assert domain.trigger in page
        # The run-metadata row must be visibly locked: checked and disabled,
        # and not just checked (which a user could untick).
        assert "Run metadata" in page
        metadata_idx = page.index("Run metadata")
        preceding = page[max(0, metadata_idx - 200) : metadata_idx]
        assert "disabled" in preceding
        assert "checked" in preceding
    finally:
        srv.shutdown()


def test_get_page_has_a_label_for_the_feedback_textarea(domains) -> None:
    # Issue #50: the feedback textarea had no id, wrapping label or
    # aria-label, so its only visible name came from a heading and a
    # placeholder, neither of which is programmatically associated with the
    # field. Every other control on the form (domain checkboxes, issue-mode
    # radios, consent checkboxes) is already wrapped in its own <label>, so
    # this only needs to check the one field that was not.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert 'label for="feedback-textarea"' in page
        assert '<textarea id="feedback-textarea"' in page
    finally:
        srv.shutdown()


def test_get_page_telemetry_consent_defaults_are_all_unticked(domains) -> None:
    # Issue #47: coverage, rollup and self_assessment used to default to
    # True, so a fresh page arrived with three of four consent boxes
    # pre-ticked. Opt-in consent means starting from nothing ticked, the
    # same way the fourth box (environment) already behaved.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        for name in (
            "consent_coverage",
            "consent_rollup",
            "consent_self_assessment",
            "consent_environment",
        ):
            tag_match = re.search(rf'<input type="checkbox" name="{name}"[^>]*>', page)
            assert tag_match is not None, f"{name} checkbox not found on the page"
            assert "checked" not in tag_match.group(0), f"{name} was pre-ticked"
    finally:
        srv.shutdown()


def test_get_page_environment_consent_label_matches_what_the_code_sends(domains) -> None:
    # Issue #48: the label used to say "(assistant, model, tool version)",
    # a fixed three-field description. The field it controls actually
    # gates an open dict[str, str] the calling agent supplies (RunMeta.environment,
    # see server.py's begin_run) and feedback.py serialises every key/value
    # pair present, not a fixed subset. The label must say the contents are
    # decided by the driving assistant, not imply a closed set of fields.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "(assistant, model, tool version)" not in page
        assert "recorded by the AI assistant driving this" in page
    finally:
        srv.shutdown()


def test_poll_is_pending_before_submission(domains) -> None:
    srv = ConfigServer(domains)
    try:
        srv.start()
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_post_submission_then_poll_returns_config(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {
                "domain": ["d01"],
                "issue_mode": "report",
                "feedback_text": "the gnome roster looks great",
                "consent_coverage": "on",
                "consent_rollup": "on",
                "csrf_token": token,
            },
            doseq=True,
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 200
            assert "close this tab" in resp.read().decode("utf-8")

        config = srv.poll()
        assert config != "pending"
        assert config.selected_domain_ids == ["d01"]
        assert config.issue_mode == "report"
        assert config.feedback_text == "the gnome roster looks great"
        assert config.telemetry_consent.coverage is True
        assert config.telemetry_consent.rollup is True
        assert config.telemetry_consent.self_assessment is False
        assert config.telemetry_consent.environment is False
    finally:
        srv.shutdown()


def test_post_without_csrf_token_is_rejected(domains) -> None:
    # Issue #39: the POST endpoint had no CSRF protection at all. A request
    # that never even carries the field (as a forged cross-site form post
    # would not, since it cannot read this page's own response) must be
    # rejected outright, and the run must remain unconfigured.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        status, body = _post(url, {"domain": ["d01"], "issue_mode": "report"})
        assert status == 403
        assert "csrf" in body.lower() or "CSRF" in body
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_post_with_wrong_csrf_token_is_rejected(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        # Fetch a real token so the page has genuinely been served once,
        # then submit a different value: this proves the check actually
        # compares the token, not just checks presence of the field.
        _fetch_csrf_token(url)
        status, _body = _post(
            url,
            {"domain": ["d01"], "issue_mode": "report", "csrf_token": "not-the-real-token"},
        )
        assert status == 403
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_post_with_correct_csrf_token_is_accepted(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, _body = _post(
            url, {"domain": ["d01"], "issue_mode": "report", "csrf_token": token}
        )
        assert status == 200
        config = srv.poll()
        assert config != "pending"
        assert config.selected_domain_ids == ["d01"]
    finally:
        srv.shutdown()


def test_post_with_non_numeric_content_length_returns_400_and_server_keeps_serving(domains) -> None:
    # int(Content-Length) on a malformed header used to raise ValueError
    # uncaught, crashing the handler thread. It must return a clean 400 and
    # leave the server able to serve the next request.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        host_port = url[len("http://") :].rstrip("/")
        host, port_str = host_port.split(":")
        conn = http.client.HTTPConnection(host, int(port_str), timeout=5)
        try:
            conn.putrequest("POST", "/submit")
            conn.putheader("Content-Length", "abc")
            conn.endheaders()
            resp = conn.getresponse()
            assert resp.status == 400
            resp.read()
        finally:
            conn.close()

        # The server must still be alive and serving after the bad request.
        with urllib.request.urlopen(url, timeout=5) as resp2:
            assert resp2.status == 200
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_post_with_oversized_content_length_is_rejected(domains) -> None:
    # Issue #39 also asked for a cap on the body size read in do_POST, so a
    # forged or careless request cannot make the handler read an unbounded
    # amount of attacker-controlled data. The check must fire from the
    # header alone, before rfile.read() is ever called.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        host_port = url[len("http://") :].rstrip("/")
        host, port_str = host_port.split(":")
        conn = http.client.HTTPConnection(host, int(port_str), timeout=5)
        try:
            conn.putrequest("POST", "/submit")
            conn.putheader("Content-Length", str(1 << 30))  # 1 GiB claimed, never sent
            conn.endheaders()
            resp = conn.getresponse()
            assert resp.status == 413
            resp.read()
        finally:
            conn.close()
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_post_with_no_domain_selected_shows_friendly_error_and_preserves_form(domains) -> None:
    # Issue #49: submitting with no domain ticked used to raise a raw
    # Pydantic ValidationError straight into send_error, showing the user
    # an exception dump and losing everything else they had typed. It must
    # instead re-render the form with a plain-language message and keep
    # whatever else was filled in.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _post(
            url,
            {
                "issue_mode": "github",
                "feedback_text": "please keep my note",
                "consent_coverage": "on",
                "csrf_token": token,
            },
        )
        assert status == 400
        assert "validation error for AuditConfig" not in body
        assert "Select at least one domain to audit" in body
        assert "please keep my note" in body
        # The github radio and the coverage checkbox must still be checked
        # in the re-rendered form.
        github_match = re.search(r'<input type="radio" name="issue_mode" value="github"[^>]*>', body)
        assert github_match is not None
        assert "checked" in github_match.group(0)
        coverage_match = re.search(r'<input type="checkbox" name="consent_coverage"[^>]*>', body)
        assert coverage_match is not None
        assert "checked" in coverage_match.group(0)
        # Nothing was actually accepted: the run is still unconfigured and
        # a corrected submission must still be possible.
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_second_submission_is_rejected(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {"domain": ["d01"], "issue_mode": "report", "csrf_token": token}, doseq=True
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass

        with pytest.raises(HTTPError) as excinfo:
            request2 = urllib.request.Request(url + "submit", data=payload, method="POST")
            urllib.request.urlopen(request2, timeout=5)
        assert excinfo.value.code == 409
    finally:
        srv.shutdown()


def test_unsupported_method_returns_405_with_allow_header(domains) -> None:
    # Issue #42: PUT/DELETE/PATCH used to fall through to the standard
    # library's default 501 Not Implemented, which claims the server is
    # broken rather than saying this endpoint just does not accept the
    # method. A 405 must carry an Allow header naming what is accepted.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        host_port = url[len("http://") :].rstrip("/")
        host, port_str = host_port.split(":")
        for method in ("PUT", "DELETE", "PATCH"):
            conn = http.client.HTTPConnection(host, int(port_str), timeout=5)
            try:
                conn.putrequest(method, "/")
                conn.putheader("Content-Length", "0")
                conn.endheaders()
                resp = conn.getresponse()
                assert resp.status == 405, f"{method} did not return 405"
                allow = resp.getheader("Allow")
                assert allow is not None, f"{method} response had no Allow header"
                assert "GET" in allow and "POST" in allow
                resp.read()
            finally:
                conn.close()
        # The server must still be alive and serving after all of the above.
        with urllib.request.urlopen(url, timeout=5) as resp2:
            assert resp2.status == 200
    finally:
        srv.shutdown()


def test_responses_carry_a_content_security_policy_header(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            csp = resp.getheader("Content-Security-Policy")
        assert csp is not None
        assert "default-src 'none'" in csp
    finally:
        srv.shutdown()


def test_fresh_config_server_never_carries_a_previous_runs_feedback_text(domains) -> None:
    # Issue #62: the feedback textarea was observed pre-filled with text
    # from an earlier run against a different repository. Investigation
    # (see config_page.py and the only caller, server.py's start_config)
    # ruled out server-side carry-over: the production call site always
    # constructs ConfigServer(state.pack.domains) with no defaults
    # argument, so self._defaults is always None and _render_form always
    # renders an empty feedback field for a fresh instance. This test
    # reproduces the two-run shape the issue describes and pins down the
    # actual fix: autocomplete="off" on the field, since the only remaining
    # candidate was the browser restoring a same-named field's previous
    # value across the two (differently-ported) origins.
    first = ConfigServer(domains)
    try:
        url = first.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {
                "domain": ["d01"],
                "issue_mode": "report",
                "feedback_text": "feedback for the OTHER repository",
                "csrf_token": token,
            },
            doseq=True,
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass
    finally:
        first.shutdown()

    # A second run, constructed exactly as server.py's start_config() always
    # constructs it: no defaults passed.
    second = ConfigServer(domains)
    try:
        url2 = second.start()
        with urllib.request.urlopen(url2, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "feedback for the OTHER repository" not in page
        textarea_start = page.index("<textarea")
        tag_end = page.index(">", textarea_start) + 1
        content_end = page.index("</textarea>")
        assert page[tag_end:content_end] == ""
        assert 'autocomplete="off"' in page[textarea_start:tag_end]
    finally:
        second.shutdown()


def test_wait_times_out_loudly_on_a_fresh_server(domains) -> None:
    srv = ConfigServer(domains)
    try:
        srv.start()
        with pytest.raises(ConfigTimeoutError):
            srv.wait(timeout_s=0.2)
    finally:
        srv.shutdown()


def test_wait_returns_immediately_once_submitted(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {"domain": ["d01", "d02"], "issue_mode": "github", "csrf_token": token}, doseq=True
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass

        config = srv.wait(timeout_s=1.0)
        assert sorted(config.selected_domain_ids) == ["d01", "d02"]
        assert config.issue_mode == "github"
    finally:
        srv.shutdown()
