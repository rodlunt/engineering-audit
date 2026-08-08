"""A localhost-only configuration page for one audit run.

Binds 127.0.0.1 on an ephemeral port using the standard library's
ThreadingHTTPServer, running in a daemon thread, so the calling process (the
MCP server) can keep doing other work while a human fills in the form. This
page never binds any other interface: it exists for the person sitting at
this keyboard, not as a network service.
"""

from __future__ import annotations

import html
import string
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from pydantic import ValidationError

from engineering_audit.rules import Domain
from engineering_audit.schema import AuditConfig, TelemetryConsent

__all__ = ["ConfigServer", "ConfigTimeoutError"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "config-page.html"

_SUBMITTED_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>engineering-audit: configuration received</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 0 1rem; }
</style>
</head>
<body>
<h1>Configuration received</h1>
<p>You can close this tab. The audit will continue in your terminal.</p>
</body>
</html>
"""


class ConfigTimeoutError(Exception):
    """Raised by ConfigServer.wait() when no submission arrives before the
    timeout elapses. A caller must never treat a timeout as an implicit
    default configuration: that would run an audit the user never actually
    chose, which is the same shape of bug as a check that fails open."""


class ConfigServer:
    """Serves the audit configuration form and captures exactly one submission."""

    def __init__(self, domains: list[Domain], defaults: AuditConfig | None = None) -> None:
        if not domains:
            raise ValueError("ConfigServer needs at least one domain to offer")
        self._domains = list(domains)
        self._defaults = defaults
        self._template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._config: AuditConfig | None = None
        self._submitted = threading.Event()

    def start(self) -> str:
        if self._httpd is not None:
            raise RuntimeError("ConfigServer.start() called twice on the same instance")

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format_str: str, *args: object) -> None:  # noqa: A002
                # Best effort only: this is a localhost dev page, and request
                # logging to stderr adds noise, not safety. Swallowing it here
                # never hides an audit result, only an access log line.
                pass

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
                if self.path not in ("/", ""):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = server._render_form().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
                if self.path != "/submit":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if server._submitted.is_set():
                    self.send_error(HTTPStatus.CONFLICT, "Configuration already submitted")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8")
                try:
                    config = server._parse_submission(raw_body)
                except (ValidationError, ValueError) as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                with server._lock:
                    server._config = config
                server._submitted.set()
                body = _SUBMITTED_PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def _render_form(self) -> str:
        defaults = self._defaults
        selected_ids = (
            set(defaults.selected_domain_ids) if defaults else {d.id for d in self._domains}
        )
        issue_mode = defaults.issue_mode if defaults else "report"
        consent = defaults.telemetry_consent if defaults else TelemetryConsent()
        feedback_text = defaults.feedback_text if defaults else ""

        domain_rows = []
        for domain in self._domains:
            checked = " checked" if domain.id in selected_ids else ""
            domain_rows.append(
                '<label class="domain-row">'
                f'<input type="checkbox" name="domain" value="{html.escape(domain.id)}"{checked}>'
                f' <span class="domain-title">{html.escape(domain.title)}</span>'
                f'<span class="domain-trigger">{html.escape(domain.trigger)}</span>'
                "</label>"
            )

        template = string.Template(self._template_text)
        return template.substitute(
            domain_checkboxes="\n".join(domain_rows),
            issue_mode_github_checked="checked" if issue_mode == "github" else "",
            issue_mode_report_checked="checked" if issue_mode == "report" else "",
            feedback_text=html.escape(feedback_text or ""),
            consent_coverage_checked="checked" if consent.coverage else "",
            consent_rollup_checked="checked" if consent.rollup else "",
            consent_self_assessment_checked="checked" if consent.self_assessment else "",
            consent_environment_checked="checked" if consent.environment else "",
        )

    def _parse_submission(self, raw_body: str) -> AuditConfig:
        fields = parse_qs(raw_body, keep_blank_values=True)
        selected_domain_ids = fields.get("domain", [])
        issue_mode = fields.get("issue_mode", ["report"])[0]
        feedback_text = fields.get("feedback_text", [""])[0].strip() or None
        consent = TelemetryConsent(
            coverage="consent_coverage" in fields,
            rollup="consent_rollup" in fields,
            self_assessment="consent_self_assessment" in fields,
            environment="consent_environment" in fields,
        )
        # No fallback to "all domains" here: an empty selection is a real user
        # choice (or a broken form) and AuditConfig rejects it below, loudly,
        # rather than this function quietly inventing a selection nobody made.
        return AuditConfig(
            selected_domain_ids=selected_domain_ids,
            issue_mode=issue_mode,
            feedback_text=feedback_text,
            telemetry_consent=consent,
        )

    def poll(self) -> "str | AuditConfig":
        """Return the submitted AuditConfig, or the literal string 'pending'."""
        if self._submitted.is_set():
            with self._lock:
                if self._config is None:
                    raise RuntimeError("submitted event set but no config stored: internal bug")
                return self._config
        return "pending"

    def wait(self, timeout_s: float) -> AuditConfig:
        """Block for up to timeout_s seconds for a submission.

        Raises ConfigTimeoutError on expiry rather than returning a default:
        there is no safe default audit configuration to fall back to.
        """
        if not self._submitted.wait(timeout=timeout_s):
            raise ConfigTimeoutError(
                f"No configuration submitted within {timeout_s} seconds."
            )
        with self._lock:
            if self._config is None:
                raise RuntimeError("submitted event set but no config stored: internal bug")
            return self._config

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
