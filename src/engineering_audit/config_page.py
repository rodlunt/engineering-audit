"""A localhost-only configuration page for one audit run.

Binds 127.0.0.1 on an ephemeral port using the standard library's
ThreadingHTTPServer, running in a daemon thread, so the calling process (the
MCP server) can keep doing other work while a human fills in the form. This
page never binds any other interface: it exists for the person sitting at
this keyboard, not as a network service.
"""

from __future__ import annotations

import html
import secrets
import string
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from pydantic import ValidationError

from engineering_audit.rules import Domain
from engineering_audit.schema import AuditConfig, TelemetryConsent

__all__ = ["ConfigServer", "ConfigTimeoutError"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "config-page.html"
_SUBMITTED_TEMPLATE_PATH = Path(__file__).parent / "templates" / "config-submitted.html"

# A localhost-only page with no scripts and one relative form post: this is
# about as strict as a Content-Security-Policy gets. It buys nothing today
# (there is no inline script here to restrict) but keeps the page safe by
# construction if a future change ever adds one without also updating this.
_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"

# A generous but bounded cap on the submitted form body. The form is a
# handful of checkboxes, a couple of radio buttons and one feedback
# textarea: nothing this page legitimately sends should ever approach this
# limit, so it exists only to stop a forged or careless request from making
# the handler read an unbounded amount of attacker-controlled data into
# memory.
_MAX_BODY_BYTES = 1 << 20  # 1 MiB


class ConfigTimeoutError(Exception):
    """Raised by ConfigServer.wait() when no submission arrives before the
    timeout elapses. A caller must never treat a timeout as an implicit
    default configuration: that would run an audit the user never actually
    chose, which is the same shape of bug as a check that fails open."""


@dataclass
class _FormState:
    """The values needed to re-render the configuration form: either the
    defaults an in-progress run was constructed with, or the raw values a
    rejected submission arrived with, so a validation failure never throws
    away what the user had already filled in."""

    selected_domain_ids: set[str]
    issue_mode: str
    feedback_text: str
    consent: TelemetryConsent


def _is_empty_domain_selection_error(exc: ValidationError) -> bool:
    """True only for AuditConfig's 'select at least one domain' failure,
    never for any other validation problem.

    This is the one validation failure a normal, unmodified submission of
    the form can actually reach: every other field is constrained by the
    HTML itself (a radio button, a fixed set of checkboxes), so an empty
    domain selection is the only shape of bad request that deserves the
    friendly, form-preserving re-render. Anything else still falls through
    to the generic 400 response, on the assumption that it came from a
    hand-crafted request, not a person using the page as built.
    """
    return any(
        error["loc"] == ("selected_domain_ids",) and error["type"] == "value_error"
        for error in exc.errors()
    )


def _form_state_from_fields(fields: dict[str, list[str]]) -> _FormState:
    """Rebuild the form's visible state from a rejected submission's raw
    fields, so re-rendering after a validation failure shows the user
    exactly what they had already filled in rather than a blank form."""
    return _FormState(
        selected_domain_ids=set(fields.get("domain", [])),
        issue_mode=fields.get("issue_mode", ["report"])[0],
        feedback_text=fields.get("feedback_text", [""])[0].strip(),
        consent=TelemetryConsent(
            coverage="consent_coverage" in fields,
            rollup="consent_rollup" in fields,
            self_assessment="consent_self_assessment" in fields,
            environment="consent_environment" in fields,
        ),
    )


class ConfigServer:
    """Serves the audit configuration form and captures exactly one submission."""

    def __init__(self, domains: list[Domain], defaults: AuditConfig | None = None) -> None:
        if not domains:
            raise ValueError("ConfigServer needs at least one domain to offer")
        self._domains = list(domains)
        self._defaults = defaults
        self._template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
        self._submitted_text = _SUBMITTED_TEMPLATE_PATH.read_text(encoding="utf-8")
        # One token per run, generated fresh for every ConfigServer instance
        # and never rotated: since exactly one submission is ever accepted
        # (see _submitted below), there is nothing to rotate it against.
        # It exists so a forged cross-site POST, which cannot read this
        # page's own response, has no way to guess the value it must send.
        self._csrf_token = secrets.token_urlsafe(32)
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

            def end_headers(self) -> None:
                # Overriding this one spot, rather than adding the header to
                # every send_header call, means it lands on every response
                # this handler ever sends, including send_error's own error
                # pages: a CSP only guards anything if it is genuinely on
                # every response, not just the happy path.
                self.send_header("Content-Security-Policy", _CSP)
                super().end_headers()

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
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    # A non-numeric Content-Length is a malformed request, not
                    # a reason to crash the handler thread: reply 400 and keep
                    # serving.
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
                    return
                if length > _MAX_BODY_BYTES:
                    # An attacker-controlled Content-Length is not proof the
                    # body is actually that large, but reading up to it is:
                    # reject before ever calling rfile.read().
                    self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
                    return
                raw_body = self.rfile.read(length).decode("utf-8")
                fields = parse_qs(raw_body, keep_blank_values=True)

                token = fields.get("csrf_token", [None])[0]
                if token is None or not secrets.compare_digest(token, server._csrf_token):
                    # Missing, wrong, or forged: this submission did not
                    # come from a page this server itself rendered, so it
                    # does not get to change the run's configuration.
                    self.send_error(HTTPStatus.FORBIDDEN, "Missing or invalid CSRF token")
                    return

                try:
                    config = server._parse_submission(fields)
                except ValidationError as exc:
                    if _is_empty_domain_selection_error(exc):
                        body = server._render_form(
                            error="Select at least one domain to audit.",
                            state=_form_state_from_fields(fields),
                        ).encode("utf-8")
                        self.send_response(HTTPStatus.BAD_REQUEST)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except ValueError as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                with server._lock:
                    server._config = config
                server._submitted.set()
                body = server._submitted_text.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _reject_method(self) -> None:
                # RFC 9110: a method this endpoint simply never accepts is
                # 405, not 501. 501 means the server itself is missing a
                # feature or broken; this server is neither, it just does
                # not support PUT/DELETE/PATCH/etc on this resource.
                self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
                self.send_header("Allow", "GET, POST")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler method name
                self._reject_method()

            def do_PUT(self) -> None:  # noqa: N802 - stdlib handler method name
                self._reject_method()

            def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler method name
                self._reject_method()

            def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler method name
                self._reject_method()

            def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler method name
                self._reject_method()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        if isinstance(host, (bytes, bytearray)):
            # server_address is typed to allow bytes (it also covers
            # AF_UNIX sockets); this server only ever binds AF_INET to the
            # literal string "127.0.0.1", so this branch never runs in
            # practice. Decoding here, rather than an f-string, is the
            # correct fallback if it ever did: an f-string would print the
            # bytes' repr ("b'127.0.0.1'"), not the text.
            host = host.decode("ascii")
        return f"http://{host}:{port}/"

    def _render_form(self, *, error: str | None = None, state: _FormState | None = None) -> str:
        if state is not None:
            selected_ids = state.selected_domain_ids
            issue_mode = state.issue_mode
            consent = state.consent
            feedback_text = state.feedback_text
        else:
            defaults = self._defaults
            selected_ids = (
                set(defaults.selected_domain_ids) if defaults else {d.id for d in self._domains}
            )
            issue_mode = defaults.issue_mode if defaults else "report"
            consent = defaults.telemetry_consent if defaults else TelemetryConsent()
            feedback_text = (defaults.feedback_text or "") if defaults else ""

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
        domain_error = f'<p class="form-error">{html.escape(error)}</p>' if error else ""

        template = string.Template(self._template_text)
        return template.substitute(
            domain_checkboxes="\n".join(domain_rows),
            domain_error=domain_error,
            issue_mode_github_checked="checked" if issue_mode == "github" else "",
            issue_mode_report_checked="checked" if issue_mode == "report" else "",
            feedback_text=html.escape(feedback_text or ""),
            consent_coverage_checked="checked" if consent.coverage else "",
            consent_rollup_checked="checked" if consent.rollup else "",
            consent_self_assessment_checked="checked" if consent.self_assessment else "",
            consent_environment_checked="checked" if consent.environment else "",
            csrf_token=html.escape(self._csrf_token),
        )

    def _parse_submission(self, fields: dict[str, list[str]]) -> AuditConfig:
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
