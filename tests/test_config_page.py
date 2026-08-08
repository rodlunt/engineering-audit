"""Tests for the localhost config page (src/engineering_audit/config_page.py)."""

from __future__ import annotations

import urllib.request
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest

from engineering_audit.config_page import ConfigServer, ConfigTimeoutError
from engineering_audit.rules import load_pack

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


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
        payload = urlencode(
            {
                "domain": ["d01"],
                "issue_mode": "report",
                "feedback_text": "the gnome roster looks great",
                "consent_coverage": "on",
                "consent_rollup": "on",
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


def test_second_submission_is_rejected(domains) -> None:
    srv = ConfigServer(domains)
    try:
        url = srv.start()
        payload = urlencode({"domain": ["d01"], "issue_mode": "report"}, doseq=True).encode("utf-8")
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass

        with pytest.raises(HTTPError) as excinfo:
            request2 = urllib.request.Request(url + "submit", data=payload, method="POST")
            urllib.request.urlopen(request2, timeout=5)
        assert excinfo.value.code == 409
    finally:
        srv.shutdown()


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
        payload = urlencode({"domain": ["d01", "d02"], "issue_mode": "github"}, doseq=True).encode(
            "utf-8"
        )
        request = urllib.request.Request(url + "submit", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass

        config = srv.wait(timeout_s=1.0)
        assert sorted(config.selected_domain_ids) == ["d01", "d02"]
        assert config.issue_mode == "github"
    finally:
        srv.shutdown()
