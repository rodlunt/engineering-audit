"""Tests for the localhost config page (src/engineering_audit/config_page.py)."""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

import pytest

from engineering_audit.config_page import (
    _DRAFT_COOKIE_NAME,
    ConfigServer,
    ConfigTimeoutError,
    _parse_draft_cookie,
)
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
            "consent_consulted_sources",
            "consent_verdict_distribution",
            "consent_duration",
            "consent_rules_fetched",
        ):
            tag_match = re.search(rf'<input type="checkbox" name="{name}"[^>]*>', page)
            assert tag_match is not None, f"{name} checkbox not found on the page"
            assert "checked" not in tag_match.group(0), f"{name} was pre-ticked"
    finally:
        srv.shutdown()


def test_get_page_verdict_distribution_consent_label_names_its_contents(domains) -> None:
    # Issue #111: the label must say plainly what the section contains
    # (four verdict kinds, per domain and in total), not just gesture at
    # "verdicts", and must not claim to include finding text.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "Rule verdict distribution" in page
        assert "pass, finding, not-applicable and could-not-evaluate" in page
        assert "not the finding text" in page
    finally:
        srv.shutdown()


def test_get_page_duration_consent_label_names_its_contents_and_excludes_token_counts(domains) -> None:
    # Issue #111: token counts cannot be part of this section, since the
    # server never sees them; the label must say so and point at the
    # free-text field rather than silently omitting them with no
    # explanation.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "Run duration" in page
        assert "assistant-reported span" in page
        assert "server-measured span" in page
        assert "Token counts are not included" in page
        assert "the server never sees them" in page
    finally:
        srv.shutdown()


def test_get_page_rules_fetched_consent_label_carries_the_fetched_not_applied_wording(domains) -> None:
    # Issue #111 / #110: this section must never claim the rule text was
    # read or applied, only that it was fetched. That wording discipline
    # was established for the report and MCP tool by #117 and must not be
    # loosened here.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "Rules fetched" in page
        assert "Shows only that it was fetched, never that it was read or applied" in page
    finally:
        srv.shutdown()


def test_get_page_environment_consent_label_matches_what_the_code_sends(domains) -> None:
    # Issue #48 made this label stop claiming a fixed three-field set, because
    # the field it gated was an open dict the assistant filled however it
    # liked. Issue #89 closed the schema instead (ENVIRONMENT_KEYS), so the
    # label goes back to naming the contents exactly, and must name the three
    # keys the server now accepts and nothing else. It must not restate
    # assistant, model or tool version: those are fixed report-header rows,
    # and a label that duplicated them is why nobody ever populated the field.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "(assistant, model, tool version)" not in page
        assert "recorded by the AI assistant driving this" not in page
        assert "exactly three values" in page
        assert "your operating system" in page
        assert "name of the CLI application driving this audit" in page
        assert "that CLI's version" in page
    finally:
        srv.shutdown()


def test_get_page_consulted_sources_consent_label_states_the_privacy_note(domains) -> None:
    # Issue #57: URLs fetched while auditing a private repository can hint
    # at what that repository is about; the label controlling whether they
    # are sent to the maintainer must say this plainly, not just default the
    # box off silently.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "can hint at what that repository is about" in page
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
        assert config.telemetry_consent.consulted_sources is False
    finally:
        srv.shutdown()


def test_post_submission_with_consulted_sources_consent_ticked(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {
                "domain": ["d01"],
                "issue_mode": "report",
                "consent_consulted_sources": "on",
                "csrf_token": token,
            },
            doseq=True,
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 200

        config = srv.poll()
        assert config != "pending"
        assert config.telemetry_consent.consulted_sources is True
    finally:
        srv.shutdown()


def test_post_submission_with_verdict_distribution_duration_and_rules_fetched_consent_ticked(
    domains,
) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        payload = urlencode(
            {
                "domain": ["d01"],
                "issue_mode": "report",
                "consent_verdict_distribution": "on",
                "consent_duration": "on",
                "consent_rules_fetched": "on",
                "csrf_token": token,
            },
            doseq=True,
        ).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 200

        config = srv.poll()
        assert config != "pending"
        assert config.telemetry_consent.verdict_distribution is True
        assert config.telemetry_consent.duration is True
        assert config.telemetry_consent.rules_fetched is True
        # Untouched flags stay unticked, the same opt-in-only contract as
        # every other consent box.
        assert config.telemetry_consent.coverage is False
        assert config.telemetry_consent.consulted_sources is False
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


# ---------------------------------------------------------------------------
# Heartbeat and the dead-server banner (issue #91)
# ---------------------------------------------------------------------------


def test_alive_endpoint_answers_204_with_no_body_and_no_cache(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url + "alive", timeout=5) as resp:
            assert resp.status == 204
            assert resp.read() == b""
            assert resp.getheader("Cache-Control") == "no-store"
    finally:
        srv.shutdown()


def test_alive_endpoint_stops_answering_once_the_server_is_shut_down(domains) -> None:
    # The whole heartbeat rests on this: a dead process does not answer, and
    # a page whose fetch fails is looking at exactly the situation issue #91
    # describes. A control first (the endpoint answering while the server is
    # up), so the failure afterwards means something.
    srv = ConfigServer(domains)
    url = srv.start()
    with urllib.request.urlopen(url + "alive", timeout=5) as resp:
        assert resp.status == 204
    srv.shutdown()
    with pytest.raises(URLError):
        urllib.request.urlopen(url + "alive", timeout=5)


def test_page_carries_the_heartbeat_script_and_the_dead_banner(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert 'fetch("/alive"' in page
        assert 'id="dead-banner"' in page
        assert "submit.disabled = true" in page
        # The banner has to say all three things a user in this state needs:
        # the process is gone, this URL is not coming back, and the way out is
        # to resume the run.
        assert "The audit process is no longer running" in page
        assert "This address will not come back" in page
        assert "resume the audit" in page
    finally:
        srv.shutdown()


def test_the_page_script_runs_under_a_nonce_that_matches_its_csp(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            csp = resp.getheader("Content-Security-Policy")
            page = resp.read().decode("utf-8")
        assert csp is not None
        # connect-src is what lets the heartbeat fetch its own origin at all;
        # without it the CSP would silently defeat the feature.
        assert "connect-src 'self'" in csp
        nonce_match = re.search(r'<script nonce="([^"]+)">', page)
        assert nonce_match is not None, "page served no nonced script tag"
        assert f"script-src 'nonce-{nonce_match.group(1)}'" in csp
    finally:
        srv.shutdown()


def test_each_page_load_gets_its_own_script_nonce(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        nonces = []
        for _ in range(2):
            with urllib.request.urlopen(url, timeout=5) as resp:
                match = re.search(r'<script nonce="([^"]+)">', resp.read().decode("utf-8"))
            assert match is not None
            nonces.append(match.group(1))
        assert nonces[0] != nonces[1]
    finally:
        srv.shutdown()


def test_a_response_with_no_script_grants_no_script_source(domains) -> None:
    # The nonce is a grant, and a grant that lands on responses which carry no
    # script of their own is a grant nobody is watching.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with pytest.raises(HTTPError) as excinfo:
            urllib.request.urlopen(url + "nope", timeout=5)
        assert "script-src 'none'" in excinfo.value.headers["Content-Security-Policy"]
    finally:
        srv.shutdown()


def test_the_validation_error_re_render_keeps_its_heartbeat(domains) -> None:
    # Forgetting to tick a domain must not be the thing that leaves the user
    # on a page which can no longer tell them their run has died.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _post(url, {"issue_mode": "report", "csrf_token": token})
        assert status == 400
        assert re.search(r'<script nonce="([^"]+)">', body) is not None
        assert 'fetch("/alive"' in body
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# The one-shot draft cookie (issue #91)
# ---------------------------------------------------------------------------


def _get_page_with_cookie(url: str, cookie: str) -> tuple[str, str | None]:
    """GET the config page with a Cookie header, returning (body, Set-Cookie)."""
    request = urllib.request.Request(url, headers={"Cookie": cookie})
    with urllib.request.urlopen(request, timeout=5) as resp:
        return resp.read().decode("utf-8"), resp.getheader("Set-Cookie")


def _draft_cookie(payload: object) -> str:
    return f"{_DRAFT_COOKIE_NAME}={quote(json.dumps(payload))}"


def _ticked_domain_ids(page: str) -> set[str]:
    return set(re.findall(r'name="domain" value="([^"]+)" checked', page))


def test_a_saved_draft_restores_the_domain_selection_on_a_fresh_page(domains) -> None:
    # The point of issue #91's second half: the replacement page is served by
    # a different process on a different port, and the user must not have to
    # tick sixteen boxes again to get back to where they were.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        page, _ = _get_page_with_cookie(url, _draft_cookie({"d": ["d02"], "m": "github"}))
        assert _ticked_domain_ids(page) == {"d02"}
        assert re.search(r'value="github" checked', page) is not None
    finally:
        srv.shutdown()


def test_a_restored_draft_is_cleared_so_it_cannot_pre_tick_a_later_run(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        _, set_cookie = _get_page_with_cookie(url, _draft_cookie({"d": ["d02"], "m": "report"}))
        assert set_cookie is not None
        assert set_cookie.startswith(f"{_DRAFT_COOKIE_NAME}=;")
        assert "Max-Age=0" in set_cookie
    finally:
        srv.shutdown()


def test_a_page_served_without_a_draft_sets_no_cookie_at_all(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.getheader("Set-Cookie") is None
    finally:
        srv.shutdown()


def test_a_draft_never_pre_ticks_a_consent_box(domains) -> None:
    # Consent that arrives pre-ticked from a host-scoped cookie any other
    # local process could have written is not consent. The draft restores the
    # tedious part (which domains) and nothing that is a decision about what
    # leaves the machine.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        page, _ = _get_page_with_cookie(
            url,
            _draft_cookie(
                {
                    "d": ["d01"],
                    "m": "report",
                    "c": ["consent_environment", "consent_consulted_sources"],
                }
            ),
        )
        for name in (
            "consent_coverage",
            "consent_rollup",
            "consent_self_assessment",
            "consent_environment",
            "consent_consulted_sources",
            "consent_verdict_distribution",
            "consent_duration",
            "consent_rules_fetched",
        ):
            tag_match = re.search(rf'<input type="checkbox" name="{name}"[^>]*>', page)
            assert tag_match is not None
            assert "checked" not in tag_match.group(0), f"{name} was restored from a draft"
    finally:
        srv.shutdown()


def test_a_draft_naming_a_domain_this_pack_does_not_have_is_intersected_away(
    domains,
) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        page, _ = _get_page_with_cookie(url, _draft_cookie({"d": ["d01", "d99"], "m": "report"}))
        assert _ticked_domain_ids(page) == {"d01"}
    finally:
        srv.shutdown()


@pytest.mark.parametrize(
    "cookie",
    [
        "",
        "some_other_cookie=1",
        f"{_DRAFT_COOKIE_NAME}=not-json",
        f"{_DRAFT_COOKIE_NAME}={quote(json.dumps(['d01']))}",
        f"{_DRAFT_COOKIE_NAME}={quote(json.dumps({'d': 'd01'}))}",
        f"{_DRAFT_COOKIE_NAME}={quote(json.dumps({'d': []}))}",
        f"{_DRAFT_COOKIE_NAME}={quote(json.dumps({'d': ['d99']}))}",
        f"{_DRAFT_COOKIE_NAME}={'x' * 5000}",
    ],
)
def test_an_unusable_draft_cookie_falls_back_to_the_normal_default(cookie, domains) -> None:
    # Falling back means every domain ticked, which is the page's own default
    # and a state the user can see; it is never a silently narrowed selection.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        page, set_cookie = _get_page_with_cookie(url, cookie)
        assert _ticked_domain_ids(page) == {d.id for d in domains}
        assert set_cookie is None
    finally:
        srv.shutdown()


def test_the_pages_inline_script_parses(domains) -> None:
    # A syntax error in this script would not look like a failure: the browser
    # refuses to run the whole block, the page renders exactly as before, and
    # the heartbeat that was supposed to catch a dead server is itself dead
    # with nothing to say so. Parsing it under node is the cheapest control
    # available against that.
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the configuration page's inline "
            "script was not parse-checked. Node is present on CI runners; install node "
            "to run this check locally."
        )
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
    finally:
        srv.shutdown()
    script = page[page.index(">", page.index("<script")) + 1 : page.index("</script>")]
    result = subprocess.run(
        [node, "--check", "-"], input=script, capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"the configuration page's inline script does not parse:\n{result.stderr}"
    )


# A minimal stand-in for the bits of the DOM the page's script touches, so the
# script can be run for real rather than only parsed. Everything the script is
# supposed to do on a dead server (disable the submit control, show the banner,
# save the draft) is observable in the JSON this prints.
_DOM_STUB_JS = """
var pings = 0;
var domainBoxes = [
  { value: "d01", checked: true },
  { value: "d02", checked: false }
];
var banner = { hidden: true, scrollIntoView: function () {} };
var submit = { disabled: false };
var form = {
  querySelectorAll: function () { return domainBoxes; },
  querySelector: function () { return { value: "github" }; }
};
var cookieJar = "";
globalThis.document = {
  getElementById: function (id) {
    if (id === "audit-config-form") { return form; }
    if (id === "dead-banner") { return banner; }
    if (id === "submit-button") { return submit; }
    throw new Error("unexpected getElementById: " + id);
  },
  set cookie(value) { cookieJar = value; },
  get cookie() { return cookieJar; }
};
globalThis.fetch = function () {
  pings += 1;
  return Promise.reject(new Error("connection refused"));
};
globalThis.setInterval = function (fn) { fn(); fn(); };
"""

_DOM_REPORT_JS = """
setTimeout(function () {
  console.log(JSON.stringify({
    pings: pings,
    cookie: cookieJar,
    submitDisabled: submit.disabled,
    bannerHidden: banner.hidden
  }));
}, 0);
"""


def test_the_page_script_disables_submit_and_saves_a_draft_when_the_heartbeat_fails(
    domains,
) -> None:
    # The end of issue #91's loop, checked end to end: the script reacts to a
    # dead server, and the cookie it writes is one the Python side can actually
    # read back. Testing the two halves separately would let them drift into
    # agreeing about nothing.
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the configuration page's "
            "heartbeat behaviour was not exercised. Node is present on CI runners; "
            "install node to run this check locally."
        )
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
    finally:
        srv.shutdown()
    script = page[page.index(">", page.index("<script")) + 1 : page.index("</script>")]

    result = subprocess.run(
        [node, "--input-type=commonjs", "-"],
        input=_DOM_STUB_JS + script + _DOM_REPORT_JS,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"the page script threw:\n{result.stderr}"
    observed = json.loads(result.stdout)

    assert observed["pings"] == 2
    assert observed["submitDisabled"] is True
    assert observed["bannerHidden"] is False

    # And the draft it wrote is one this module's own parser accepts: only the
    # ticked domain, and the delivery mode that was selected.
    draft = _parse_draft_cookie(observed["cookie"], {d.id for d in domains})
    assert draft is not None
    assert draft.selected_domain_ids == {"d01"}
    assert draft.issue_mode == "github"


def test_the_page_script_leaves_the_form_alone_while_the_heartbeat_answers(domains) -> None:
    # The control for the test above: an answering server must not produce a
    # banner, a disabled button or a saved draft. Without this, a script that
    # declared the server dead unconditionally would pass that test.
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the configuration page's "
            "heartbeat behaviour was not exercised. Node is present on CI runners; "
            "install node to run this check locally."
        )
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
    finally:
        srv.shutdown()
    script = page[page.index(">", page.index("<script")) + 1 : page.index("</script>")]
    healthy_fetch = (
        'globalThis.fetch = function () { pings += 1; return Promise.resolve({ ok: true }); };'
    )

    result = subprocess.run(
        [node, "--input-type=commonjs", "-"],
        input=_DOM_STUB_JS + healthy_fetch + script + _DOM_REPORT_JS,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"the page script threw:\n{result.stderr}"
    observed = json.loads(result.stdout)

    assert observed["pings"] == 2
    assert observed["submitDisabled"] is False
    assert observed["bannerHidden"] is True
    assert observed["cookie"] == ""


# ---------------------------------------------------------------------------
# Output location choice (issue #109)
# ---------------------------------------------------------------------------


def _submit_output_location(
    url: str, token: str, output_location: str, output_location_path: str = ""
) -> tuple[int, str]:
    return _post(
        url,
        {
            "domain": ["d01"],
            "issue_mode": "report",
            "output_location": output_location,
            "output_location_path": output_location_path,
            "csrf_token": token,
        },
    )


def test_default_output_location_leaves_deliverables_dir_unset(domains) -> None:
    # The conservative reading of issue #109: the default stays exactly what
    # it was, and the user gains a choice rather than a changed behaviour.
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, _body = _submit_output_location(url, token, "default")
        assert status == 200
        config = srv.poll()
        assert config != "pending"
        assert config.deliverables_dir is None
    finally:
        srv.shutdown()


def test_custom_output_location_is_resolved_and_honoured(tmp_path, domains) -> None:
    target = tmp_path / "reports" / "this-run"
    target.mkdir(parents=True)
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, _body = _submit_output_location(url, token, "custom", str(target))
        assert status == 200
        config = srv.poll()
        assert config != "pending"
        assert config.deliverables_dir == str(target.resolve())
    finally:
        srv.shutdown()


def test_custom_output_location_path_is_expanded_and_resolved(
    tmp_path, domains, monkeypatch
) -> None:
    # "~" expansion happens against HOME at submission time; a fabricated
    # HOME here makes the resolution observable rather than incidental.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "reports").mkdir()
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, _body = _submit_output_location(url, token, "custom", "~/reports")
        assert status == 200
        config = srv.poll()
        assert config != "pending"
        assert config.deliverables_dir == str((tmp_path / "reports").resolve())
    finally:
        srv.shutdown()


def test_check_output_location_endpoint_echoes_the_resolved_path(
    tmp_path, domains, monkeypatch
) -> None:
    # This is the "before submission" echo: a live, read-only preview the
    # page's script polls as the user types, not only shown after a
    # rejected submission.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "reports").mkdir()
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url + "check-output-location?path=~%2Freports", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["resolved"] == str((tmp_path / "reports").resolve())
        assert payload["error"] is None
    finally:
        srv.shutdown()


def test_check_output_location_endpoint_reports_a_missing_parent(tmp_path, domains) -> None:
    missing = tmp_path / "does-not-exist" / "reports"
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(
            url + "check-output-location?path=" + quote(str(missing)), timeout=5
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["resolved"] == str(missing)
        assert payload["error"] is not None
        assert "does not exist" in payload["error"]
    finally:
        srv.shutdown()


def test_custom_output_location_with_missing_parent_is_rejected_at_config_time(
    tmp_path, domains
) -> None:
    # Requirement: fail clearly at configuration time, not at render_report
    # after the whole audit has been paid for.
    missing = tmp_path / "does-not-exist" / "reports"
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _submit_output_location(url, token, "custom", str(missing))
        assert status == 400
        assert "does not exist" in body
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_custom_output_location_with_unwritable_parent_is_rejected_at_config_time(
    tmp_path, domains
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip(
            "running as root: permission bits never block root, so an unwritable "
            "parent cannot be exercised this way"
        )
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)  # read + execute, no write
    target = parent / "reports"
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        try:
            status, body = _submit_output_location(url, token, "custom", str(target))
        finally:
            parent.chmod(0o700)  # restore so tmp_path cleanup can remove it
        assert status == 400
        assert "not writable" in body
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_custom_output_location_never_silently_overwrites_an_existing_report(
    tmp_path, domains
) -> None:
    target = tmp_path / "reports"
    target.mkdir()
    (target / "report.html").write_text("an earlier run's report", encoding="utf-8")
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _submit_output_location(url, token, "custom", str(target))
        assert status == 400
        assert "already contains" in body
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_custom_output_location_with_blank_path_is_rejected_with_a_friendly_message(
    domains,
) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _submit_output_location(url, token, "custom", "")
        assert status == 400
        assert "Enter a custom path" in body
        assert srv.poll() == "pending"
    finally:
        srv.shutdown()


def test_rejected_custom_output_location_re_renders_the_form_and_keeps_other_fields(
    tmp_path, domains
) -> None:
    missing = tmp_path / "does-not-exist" / "reports"
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        token = _fetch_csrf_token(url)
        status, body = _post(
            url,
            {
                "domain": ["d01"],
                "issue_mode": "github",
                "feedback_text": "please keep my note",
                "output_location": "custom",
                "output_location_path": str(missing),
                "csrf_token": token,
            },
        )
        assert status == 400
        assert "please keep my note" in body
        github_match = re.search(
            r'<input type="radio" name="issue_mode" value="github"[^>]*>', body
        )
        assert github_match is not None
        assert "checked" in github_match.group(0)
        # The mistyped path itself is preserved so the user is not left
        # retyping it.
        assert str(missing) in body
    finally:
        srv.shutdown()


def test_default_output_location_shows_the_runs_output_dir(tmp_path, domains) -> None:
    out_dir = tmp_path / "audit-output"
    srv = ConfigServer(domains, output_dir=out_dir)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert str(out_dir) in page
    finally:
        srv.shutdown()


def test_gitignore_warning_is_shown_when_given_to_the_server(domains) -> None:
    srv = ConfigServer(
        domains, gitignore_warning="audit-output is not covered by a .gitignore entry."
    )
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "not covered by a .gitignore entry" in page
    finally:
        srv.shutdown()


def test_no_gitignore_warning_when_none_is_given(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert 'class="gitignore-warning"' not in page
    finally:
        srv.shutdown()


def test_the_second_inline_script_parses(domains) -> None:
    # The output-location preview lives in its own <script> tag (see
    # config-page.html) precisely so it cannot take the heartbeat down with
    # it on a syntax error; this is that second tag's own parse check, the
    # sibling of test_the_pages_inline_script_parses.
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "node is not installed on this machine, so the configuration page's second "
            "inline script was not parse-checked. Node is present on CI runners; install "
            "node to run this check locally."
        )
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        with urllib.request.urlopen(url, timeout=5) as resp:
            page = resp.read().decode("utf-8")
    finally:
        srv.shutdown()
    starts = [m.start() for m in re.finditer(r'<script nonce="', page)]
    assert len(starts) == 2, "expected exactly two <script> tags on the configuration page"
    second_open = page.index(">", starts[1]) + 1
    second_close = page.index("</script>", second_open)
    script = page[second_open:second_close]
    result = subprocess.run([node, "--check", "-"], input=script, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"the configuration page's second inline script does not parse:\n{result.stderr}"
    )


def test_parse_draft_cookie_rejects_a_bad_issue_mode_without_dropping_the_domains() -> None:
    draft = _parse_draft_cookie(
        f"{_DRAFT_COOKIE_NAME}={quote(json.dumps({'d': ['d01'], 'm': 'rm -rf'}))}",
        {"d01", "d02"},
    )
    assert draft is not None
    assert draft.selected_domain_ids == {"d01"}
    # An unrecognised mode falls back to the safe one (findings stay local)
    # rather than being taken at face value from a cookie.
    assert draft.issue_mode == "report"
