"""A localhost-only configuration page for one audit run.

Binds 127.0.0.1 on an ephemeral port using the standard library's
ThreadingHTTPServer, running in a daemon thread, so the calling process (the
MCP server) can keep doing other work while a human fills in the form. This
page never binds any other interface: it exists for the person sitting at
this keyboard, not as a network service.
"""

from __future__ import annotations

import html
import json
import secrets
import string
import threading
from dataclasses import dataclass
from enum import Enum, auto
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

if TYPE_CHECKING:
    from engineering_audit.stack_detection import DetectedStack

from engineering_audit.managed_blocks import get_document_title
from engineering_audit.output_location import (
    UnresolvableOutputLocation,
    existing_deliverables_warning,
    resolve_deliverables_dir,
    validate_deliverables_dir,
)
from engineering_audit.rules import Domain
from engineering_audit.schema import AuditConfig, TelemetryConsent
from engineering_audit.standards_approval import (
    DiffModel,
    SummaryCount,
    highlight_managed_block_markers,
)

__all__ = ["ConfigServer", "ConfigTimeoutError"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "config-page.html"
_SUBMITTED_TEMPLATE_PATH = Path(__file__).parent / "templates" / "config-submitted.html"
_APPROVAL_TEMPLATE_PATH = Path(__file__).parent / "templates" / "approval-page.html"
_STACK_MISMATCH_TEMPLATE_PATH = (
    Path(__file__).parent / "templates" / "stack-mismatch-page.html"
)
# Served in place of a static "you can close this window" byte string once a
# stack choice has been recorded: a standards-approval step always follows a
# resolved (non-timeout) stack mismatch in the same run (see server.py), and
# the mismatch page navigation that got the user here already unloaded
# config-submitted.html, killing both of its pollers. This page picks the
# approval poll back up so the one tab the user has open keeps working
# without ever inviting them to close it while a review may still be coming.
_STACK_CHOICE_RECORDED_TEMPLATE_PATH = (
    Path(__file__).parent / "templates" / "stack-choice-recorded.html"
)

# The path the page's heartbeat polls. Deliberately its own route rather than
# a HEAD of "/": it answers with no body and no work, so a poll every few
# seconds costs nothing, and it cannot be confused with a real page fetch in
# anything that later reads this handler.
_ALIVE_PATH = "/alive"

# The path the "configuration received" page polls, from the one tab a human
# still has open once the audit starts running, to find out whether there is
# now a standards approval waiting for them. Its own cheap route, mirroring
# _ALIVE_PATH exactly, so a poll every few seconds never has to render the
# (much larger) approval page itself just to ask whether one exists yet.
_APPROVAL_READY_PATH = "/approval-ready"

# The path the "configuration received" page polls to find out whether a
# stack mismatch has been detected and is waiting for the user's choice.
# Mirrors _APPROVAL_READY_PATH exactly, and for the same reason: the poll
# runs every few seconds for as long as the audit is running, so it must
# never have to render the stack-mismatch page itself just to answer
# "is there one yet".
_STACK_MISMATCH_READY_PATH = "/stack-mismatch-ready"

# The path the page's custom-output-location field checks against as the user
# types, so the resolved absolute path (and any problem with it) is shown
# before the user ever submits the form, not after. Read-only and
# side-effect-free (it only stats the filesystem), so unlike /submit it needs
# no CSRF token: nothing it does can be forged into changing this run's
# configuration.
_CHECK_OUTPUT_LOCATION_PATH = "/check-output-location"

# A hard cap on the path text this endpoint will even attempt to resolve.
# Nothing a person types into this field legitimately approaches this length;
# anything past it is refused without touching the filesystem at all.
_MAX_OUTPUT_LOCATION_PATH_CHARS = 4096

# How often the page polls _ALIVE_PATH, and how many consecutive failures it
# takes before it declares the audit process gone. Two failures rather than
# one so a single dropped request does not tell the user their run has died
# when it has not; four seconds so the truth arrives within about ten, which
# is well before anyone finishes ticking sixteen domains.
_HEARTBEAT_INTERVAL_MS = 4000
_HEARTBEAT_FAILURES_BEFORE_DEAD = 2

# The one-shot cookie the page writes when its heartbeat fails, so the
# replacement page served by a resumed run can restore the domain selection
# the user had already made.
#
# A cookie rather than localStorage or sessionStorage because both of those
# are scoped per origin, and an origin includes the port: the replacement page
# is served on a fresh ephemeral port, so neither would ever be readable from
# it. Cookies are scoped to the host, which is exactly the scope needed here.
#
# It is host-scoped, which means any other service on 127.0.0.1 can read it,
# so it carries only what it has to: which domains were ticked and which
# delivery mode was chosen. Not the feedback text (free prose the user wrote
# for the tool author), and NOT the telemetry consent boxes. Consent that
# arrives pre-ticked from a value another local process could have written is
# not consent, and re-ticking five boxes is not the burden this is here to
# remove.
_DRAFT_COOKIE_NAME = "engineering_audit_config_draft"
_DRAFT_COOKIE_MAX_AGE_S = 3600

# A hard cap on the draft cookie before it is parsed. Sixteen domain ids and a
# delivery mode is a few hundred bytes; anything past this did not come from
# this page, and is dropped rather than parsed.
_MAX_DRAFT_COOKIE_CHARS = 4096


def _csp(script_nonce: str | None) -> str:
    """The page's Content-Security-Policy, granting the one inline script this
    page serves a per-response nonce and nothing else.

    A nonce rather than 'unsafe-inline' because the two cannot be told apart
    by a reader six months from now, and only one of them still says no to a
    script this file did not write. Responses with no script of their own
    (every error page, the submitted page, the heartbeat) get 'none', so the
    grant exists only on the exact response that needs it.
    """
    script_src = f"'nonce-{script_nonce}'" if script_nonce else "'none'"
    return (
        "default-src 'none'; "
        f"script-src {script_src}; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'"
    )


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
    output_location_mode: str
    output_location_path: str


@dataclass(frozen=True)
class _ConfigDraft:
    """The choices a previous configuration page saved when it noticed its own
    server had gone: which domains were ticked, and which delivery mode.

    Deliberately narrower than :class:`_FormState`. This one is rebuilt from a
    cookie, which is to say from data this process did not write and cannot
    vouch for, so it only carries the two fields whose worst case is a
    pre-ticked box the user can see and change. Consent flags and feedback
    text are not in it; see _DRAFT_COOKIE_NAME for why.
    """

    selected_domain_ids: set[str]
    issue_mode: str


@dataclass
class _ApprovalData:
    """Data for the standards approval page.

    Attributes:
        diffs: List of DiffModel objects for the three documents (agent, human, policy).
        summary_counts: Summary counts derived from the rule set.
    """

    diffs: list[DiffModel]
    summary_counts: SummaryCount


class _WindowState(Enum):
    """The lifecycle of a one-shot decision window (standards approval, or
    a stack-mismatch choice), as a genuinely three-way state rather than a
    single nullable "closed reason" field.

    A nullable reason alone cannot tell "the window has never opened" apart
    from "the window is open right now": both read as None. That collapse
    is what let a decision POSTed with the page's long-lived CSRF token,
    sent any time after the config server starts and before the window's
    data-setter (``set_approval_data`` / ``set_stack_mismatch_data``) is
    ever called, be accepted as if it were a real decision from a page the
    user had actually seen. Making "not opened yet" its own state, distinct
    from "open", closes that gap. Shared between the two flows rather than
    duplicated: both need exactly the same three states, transitioned by
    exactly the same two events (a setter opens it, a wait_* call closes
    it, whether by timeout or by consuming the one decision it was waiting
    for).
    """

    NOT_YET_OPEN = auto()
    OPEN = auto()
    CLOSED = auto()


def _parse_draft_cookie(
    header_value: str | None, known_domain_ids: set[str]
) -> _ConfigDraft | None:
    """Rebuild a :class:`_ConfigDraft` from a Cookie header, or None if there
    is nothing usable in it.

    Returning None for anything malformed is safe here, and is not the
    "swallow the error" pattern this codebase refuses elsewhere, for one
    specific reason: this value decides nothing. It pre-ticks boxes on a form
    the user then reads and submits themselves, and the submission is
    validated from scratch on the way back in. A draft that cannot be read
    costs the user the re-ticking this feature was trying to save them, which
    is exactly where they would have been without it, and it is never the
    difference between an audit that ran and one that did not.
    """
    if not header_value or len(header_value) > _MAX_DRAFT_COOKIE_CHARS:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(header_value)
    except CookieError:
        return None
    morsel = jar.get(_DRAFT_COOKIE_NAME)
    if morsel is None:
        return None
    try:
        payload = json.loads(unquote(morsel.value))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    raw_domains = payload.get("d")
    if not isinstance(raw_domains, list):
        return None
    # Intersected with the pack actually loaded now, never trusted as given: a
    # draft written against a different rules pack must not put an id on this
    # page that this page cannot offer.
    selected = {
        value for value in raw_domains if isinstance(value, str)
    } & known_domain_ids
    if not selected:
        return None

    raw_mode = payload.get("m")
    issue_mode = raw_mode if raw_mode in ("github", "report") else "report"
    return _ConfigDraft(selected_domain_ids=selected, issue_mode=issue_mode)


def _is_empty_domain_selection_error(exc: ValidationError) -> bool:
    """True only for AuditConfig's 'select at least one domain' failure,
    never for any other validation problem.

    Every other field but two is constrained by the HTML itself (a radio
    button, a fixed set of checkboxes), so an empty domain selection used to
    be the only shape of bad request a normal, unmodified submission could
    reach. The custom output location's free-text path field is the second:
    see _InvalidOutputLocation, raised from _parse_submission and given the
    same form-preserving re-render as this one, rather than routed through
    pydantic at all, since validating a filesystem path is not a data-shape
    question. Anything else here still falls through to the generic 400
    response, on the assumption that it came from a hand-crafted request,
    not a person using the page as built.
    """
    return any(
        error["loc"] == ("selected_domain_ids",) and error["type"] == "value_error"
        for error in exc.errors()
    )


class _InvalidOutputLocation(ValueError):
    """Raised by _parse_submission when the submitted custom deliverables
    path fails validation, so do_POST can give it the same friendly,
    form-preserving re-render the empty-domain-selection error gets, rather
    than a bare 400 that loses everything else the user had already filled
    in."""


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
            consulted_sources="consent_consulted_sources" in fields,
            verdict_distribution="consent_verdict_distribution" in fields,
            duration="consent_duration" in fields,
            rules_fetched="consent_rules_fetched" in fields,
            reader_conclusions="consent_reader_conclusions" in fields,
        ),
        output_location_mode=fields.get("output_location", ["default"])[0],
        output_location_path=fields.get("output_location_path", [""])[0].strip(),
    )


class ConfigServer:
    """Serves the audit configuration form and captures exactly one submission."""

    def __init__(
        self,
        domains: list[Domain],
        defaults: AuditConfig | None = None,
        *,
        output_dir: Path | None = None,
        gitignore_warning: str | None = None,
    ) -> None:
        if not domains:
            raise ValueError("ConfigServer needs at least one domain to offer")
        self._domains = list(domains)
        self._defaults = defaults
        # The run's own output_dir, shown next to the default choice so the
        # user sees exactly where the report lands rather than trusting a
        # word like "default" to mean something. None only in tests that
        # construct a ConfigServer directly without a real run behind it;
        # every production call site (server.py's start_config) always has
        # one.
        self._output_dir = output_dir
        # Precomputed by the caller (server.py, which already owns every
        # other git subprocess call) rather than shelled out to from here:
        # see _run_git and _output_dir_ignore_warning in server.py. None
        # means either the check found nothing to warn about, or it could
        # not be made at all (no repo_dir, git unavailable); either way this
        # page says nothing rather than guessing.
        self._gitignore_warning = gitignore_warning
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
        self._approval_data: _ApprovalData | None = None
        self._approval_template_text = (
            _APPROVAL_TEMPLATE_PATH.read_text(encoding="utf-8")
            if _APPROVAL_TEMPLATE_PATH.exists()
            else None
        )
        # Approval flow for standards documents
        self._approval_submitted = threading.Event()
        self._approval_action: str | None = None  # "approve" or "cancel"
        # Three distinct states, not a nullable "closed reason" alone: the
        # window has never opened yet (this state), is open (set by
        # set_approval_data), or is closed with a reason (set by
        # wait_approval). Everything that could make a decision look like
        # it succeeded when it should not (/approval-ready,
        # /approve-standards, /submit-standards) reads this before doing
        # anything else. See set_approval_data and wait_approval.
        self._approval_window_state = _WindowState.NOT_YET_OPEN
        # Only meaningful once _approval_window_state is CLOSED; None in
        # the other two states. Set by wait_approval, under _lock, the
        # moment the window closes, either because it timed out or because
        # the one decision it was waiting for has already been consumed.
        self._approval_closed_reason: str | None = None
        # Stack mismatch flow
        self._stack_mismatch_data: dict | None = None
        self._stack_mismatch_template_text = (
            _STACK_MISMATCH_TEMPLATE_PATH.read_text(encoding="utf-8")
            if _STACK_MISMATCH_TEMPLATE_PATH.exists()
            else None
        )
        self._stack_choice_submitted = threading.Event()
        self._stack_choice: str | None = None
        # Same three-state guard as _approval_window_state, and for the same
        # reason: a nullable "closed reason" alone cannot tell "never
        # opened" apart from "open", and the CSRF token this page's POST
        # handler checks is handed out long before set_stack_mismatch_data
        # is ever called. Opened by set_stack_mismatch_data, closed by
        # wait_stack_choice in both its terminal cases. See
        # _handle_stack_choice_submission.
        self._stack_choice_window_state = _WindowState.NOT_YET_OPEN
        # Only meaningful once _stack_choice_window_state is CLOSED; None in
        # the other two states. Mirrors _approval_closed_reason exactly.
        self._stack_choice_closed_reason: str | None = None
        self._stack_choice_recorded_template_text = (
            _STACK_CHOICE_RECORDED_TEMPLATE_PATH.read_text(encoding="utf-8")
            if _STACK_CHOICE_RECORDED_TEMPLATE_PATH.exists()
            else None
        )

    def start(self) -> str:
        if self._httpd is not None:
            raise RuntimeError("ConfigServer.start() called twice on the same instance")

        server = self

        class Handler(BaseHTTPRequestHandler):
            # Set for the one response that carries the page's inline script,
            # and left None for every other response, so end_headers can put a
            # matching nonce in the CSP without any response ever granting a
            # script it did not serve.
            _script_nonce: str | None = None

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
                self.send_header("Content-Security-Policy", _csp(self._script_nonce))
                super().end_headers()

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
                split = urlsplit(self.path)
                if split.path == _ALIVE_PATH:
                    self._serve_heartbeat()
                    return
                if split.path == _CHECK_OUTPUT_LOCATION_PATH:
                    self._serve_output_location_check(split.query)
                    return
                if split.path == "/stack-mismatch":
                    self._serve_stack_mismatch_page()
                    return
                if split.path == "/approve-standards":
                    # The only way a real browser ever reaches this page: an
                    # address bar, a link, or (see config-submitted.html) a
                    # script navigating window.location.href all issue GET,
                    # never POST. The POST route below exists only because
                    # do_POST used to be this endpoint's sole route; it is
                    # left in place, unchanged, since existing callers rely
                    # on it, but it is not how a person ever gets here.
                    self._serve_approval_page()
                    return
                if split.path == _APPROVAL_READY_PATH:
                    self._serve_approval_ready()
                    return
                if split.path == _STACK_MISMATCH_READY_PATH:
                    self._serve_stack_mismatch_ready()
                    return
                if split.path not in ("/", ""):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                draft = _parse_draft_cookie(
                    self.headers.get("Cookie"), {d.id for d in server._domains}
                )
                self._script_nonce = secrets.token_urlsafe(16)
                body = server._render_form(
                    draft=draft, script_nonce=self._script_nonce
                ).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if draft is not None:
                    # Restored once, then gone. A draft that outlived the page
                    # it was restored into would quietly pre-tick a later,
                    # unrelated audit with choices made for a different one,
                    # and the user would have no way to tell that the ticks in
                    # front of them were a leftover rather than the default.
                    self.send_header(
                        "Set-Cookie",
                        f"{_DRAFT_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Strict",
                    )
                self.end_headers()
                self.wfile.write(body)

            def _serve_heartbeat(self) -> None:
                """Answer the page's liveness poll: no body, no work, no cache.

                It exists so the page can tell that this process is still here.
                Once it is gone nothing answers on this port at all, and the
                page's failed fetch is the signal, which is why this endpoint
                has nothing to say beyond 204.
                """
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _serve_approval_ready(self) -> None:
                """Answer the submitted page's approval-readiness poll: 204
                once approval data has been set, 404 until then.

                No body either way, matching _serve_heartbeat: this exists
                only so the page can decide whether to navigate itself to
                /approve-standards, never to carry any of the approval data
                itself.
                """
                with server._lock:
                    ready = server._approval_data is not None
                if ready:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "No approval pending")

            def _serve_stack_mismatch_ready(self) -> None:
                """Answer the submitted page's stack-mismatch-readiness poll:
                204 once mismatch data has been set, 404 until then.

                Mirrors _serve_approval_ready exactly, and for the same
                reason: no body either way, so this exists only so the page
                can decide whether to navigate itself to /stack-mismatch,
                never to carry any of the mismatch data itself. A 204 here
                must be a genuine "the page is ready", never a default or a
                fallback response that happens to share its status code.
                """
                with server._lock:
                    ready = server._stack_mismatch_data is not None
                if ready:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "No stack mismatch pending")

            def _serve_output_location_check(self, query: str) -> None:
                """Answer the custom-output-location field's live preview:
                the resolved absolute path for whatever it currently holds,
                and a plain-language error if that path could not hold this
                run's deliverables.

                Read-only and best-effort by design: this is the convenience
                that lets the user see where files will land before they
                submit, not the validation itself. The real check runs
                again, from scratch, in _parse_submission when the form is
                actually submitted, because a page is not the only possible
                caller (see _InvalidOutputLocation) and because a path can
                stop existing, or start existing, in the gap between a
                preview and a submission.
                """
                raw_path = parse_qs(query).get("path", [""])[0]
                if not raw_path.strip():
                    payload: dict[str, str | None] = {"resolved": "", "error": None}
                elif len(raw_path) > _MAX_OUTPUT_LOCATION_PATH_CHARS:
                    payload = {"resolved": "", "error": "That path is too long."}
                else:
                    try:
                        resolved = resolve_deliverables_dir(raw_path)
                    except UnresolvableOutputLocation as exc:
                        # Same treatment as any other unusable path: this is
                        # a best-effort preview (see the docstring above),
                        # so an unresolvable ~user is shown as the error
                        # text next to the field, not a crashed request.
                        payload = {"resolved": "", "error": str(exc)}
                    else:
                        payload = {
                            "resolved": str(resolved),
                            "error": validate_deliverables_dir(resolved),
                        }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve_approval_page(self) -> None:
                """Serve the standards approval page.

                This is a read-only preview page showing diffs of the three
                standards documents before they are written to disk. It must
                not write anything to disk.
                """
                with server._lock:
                    approval_data = server._approval_data
                    approval_template_text = server._approval_template_text
                    closed_reason = server._approval_closed_reason
                if approval_data is None or approval_template_text is None:
                    self.send_error(
                        HTTPStatus.NOT_FOUND,
                        closed_reason or "Approval data not available",
                    )
                    return

                self._script_nonce = secrets.token_urlsafe(16)
                body = server._render_approval_page(self._script_nonce).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle_standards_submission(self) -> None:
                """Handle Approve/Cancel from the standards approval page."""
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(
                        HTTPStatus.BAD_REQUEST, "Invalid Content-Length header"
                    )
                    return
                if length > _MAX_BODY_BYTES:
                    self.send_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"
                    )
                    return
                raw_body = self.rfile.read(length).decode("utf-8")
                fields = parse_qs(raw_body, keep_blank_values=True)

                # Check CSRF token
                token = fields.get("csrf_token", [None])[0]
                if token is None or not secrets.compare_digest(
                    token, server._csrf_token
                ):
                    self.send_error(
                        HTTPStatus.FORBIDDEN, "Missing or invalid CSRF token"
                    )
                    return

                # Get the action (approve or cancel)
                action = fields.get("action", [None])[0]
                if action not in ("approve", "cancel"):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid action")
                    return

                # The check (is the window open, and has it ever been?) and
                # the act (record the decision) must happen inside the same
                # critical section. Reading state and writing
                # _approval_action under two separate lock acquisitions left
                # an unlocked gap between them: wait_approval could close
                # the window in that gap, and this handler, having already
                # seen the window as open, would sail past the guard below
                # and record a decision for a window that was no longer
                # open, then answer with the same "processed" message a
                # real decision gets. Collapsing both into one
                # `with server._lock:` block makes that interleaving
                # impossible: whichever of this handler or wait_approval
                # gets the lock first decides the outcome, and the other
                # sees it.
                with server._lock:
                    window_state = server._approval_window_state
                    closed_reason = server._approval_closed_reason
                    # Checked independently of window_state, even though
                    # OPEN should always imply this is set: the two are
                    # logically separate guards, and a decision must never
                    # be recorded against approval data that is not there.
                    accepted = (
                        window_state is _WindowState.OPEN
                        and server._approval_data is not None
                    )
                    if accepted:
                        server._approval_action = action

                if window_state is _WindowState.NOT_YET_OPEN or (
                    window_state is _WindowState.OPEN and not accepted
                ):
                    # The CSRF token is handed out on the very first page
                    # load, long before any audit run has produced anything
                    # to review. A decision arriving before set_approval_data
                    # has ever been called cannot have been made by someone
                    # who actually saw the approval page and its diffs, so
                    # this must be a clear rejection, never anything that
                    # could be read as "processed": that is exactly the
                    # false success this endpoint used to hand out.
                    body = (
                        b"<html><body><p>The standards review window is "
                        b"not open yet: no decision can be recorded until "
                        b"the review page is ready. Please wait and try "
                        b"again.</p></body></html>"
                    )
                    self.send_response(HTTPStatus.CONFLICT)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if window_state is _WindowState.CLOSED:
                    # wait_approval already closed this window, either
                    # because it timed out or because the one decision it
                    # was waiting for has already been recorded. Answering
                    # this with the same "processed" message given to a real
                    # decision would be a false success: nothing was written
                    # and nothing ever will be for this decision, so the
                    # response has to say that plainly rather than imply the
                    # click did something.
                    body = (
                        f"<html><body><p>{html.escape(closed_reason or '')}</p></body></html>"
                    ).encode("utf-8")
                    self.send_response(HTTPStatus.GONE)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # _approval_action was already written above, inside the
                # same critical section as the window_state check. The
                # event and the response are deliberately left outside the
                # lock: never hold a lock while writing to a socket.
                server._approval_submitted.set()

                # Return a confirmation page
                body = b"<html><body><p>Standards update processed. You can close this window.</p></body></html>"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve_stack_mismatch_page(self) -> None:
                """Serve the stack mismatch choice page.

                This page shows the grill stack and the observed stack with
                evidence, and asks the user which one is correct.
                """
                if (
                    server._stack_mismatch_data is None
                    or server._stack_mismatch_template_text is None
                ):
                    self.send_error(
                        HTTPStatus.NOT_FOUND, "Stack mismatch data not available"
                    )
                    return

                self._script_nonce = secrets.token_urlsafe(16)
                body = server._render_stack_mismatch_page(self._script_nonce).encode(
                    "utf-8"
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle_stack_choice_submission(self) -> None:
                """Handle stack choice submission (grill or audit)."""
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(
                        HTTPStatus.BAD_REQUEST, "Invalid Content-Length header"
                    )
                    return
                if length > _MAX_BODY_BYTES:
                    self.send_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"
                    )
                    return
                raw_body = self.rfile.read(length).decode("utf-8")
                fields = parse_qs(raw_body, keep_blank_values=True)

                # Check CSRF token
                token = fields.get("csrf_token", [None])[0]
                if token is None or not secrets.compare_digest(
                    token, server._csrf_token
                ):
                    self.send_error(
                        HTTPStatus.FORBIDDEN, "Missing or invalid CSRF token"
                    )
                    return

                # Get the action (grill or audit)
                action = fields.get("action", [None])[0]
                if action not in ("grill", "audit"):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid action")
                    return

                # Check-then-act inside the same critical section, mirroring
                # _handle_standards_submission exactly and for the same
                # reason: reading window_state and writing _stack_choice
                # under two separate lock acquisitions would leave an
                # unlocked gap in which wait_stack_choice could close the
                # window between the check and the write, letting this
                # handler record a choice for a window it had already seen
                # as open, then answer with the same "recorded" message a
                # real choice gets.
                with server._lock:
                    window_state = server._stack_choice_window_state
                    closed_reason = server._stack_choice_closed_reason
                    accepted = (
                        window_state is _WindowState.OPEN
                        and server._stack_mismatch_data is not None
                    )
                    if accepted:
                        server._stack_choice = action

                if window_state is _WindowState.NOT_YET_OPEN or (
                    window_state is _WindowState.OPEN and not accepted
                ):
                    # The CSRF token is handed out on the very first page
                    # load, long before any audit run has detected a stack
                    # mismatch. A choice arriving before
                    # set_stack_mismatch_data has ever been called cannot
                    # have come from someone who actually saw the
                    # stack-mismatch page, so this must be a clear
                    # rejection, never anything that could be read as
                    # "recorded".
                    body = (
                        b"<html><body><p>The stack-choice window is not "
                        b"open yet: no choice can be recorded until a "
                        b"stack mismatch has actually been detected. "
                        b"Please wait and try again.</p></body></html>"
                    )
                    self.send_response(HTTPStatus.CONFLICT)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if window_state is _WindowState.CLOSED:
                    # wait_stack_choice already closed this window, either
                    # because it timed out or because the one choice it was
                    # waiting for has already been recorded. Answering this
                    # with the same "recorded" message given to a real
                    # choice would be a false success: nothing was applied
                    # and nothing ever will be for this submission.
                    body = (
                        f"<html><body><p>{html.escape(closed_reason or '')}</p></body></html>"
                    ).encode("utf-8")
                    self.send_response(HTTPStatus.GONE)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # _stack_choice was already written above, inside the same
                # critical section as the window_state check. The event and
                # the response are deliberately left outside the lock:
                # never hold a lock while writing to a socket.
                server._stack_choice_submitted.set()

                # Return a confirmation page that keeps the user connected
                # to the flow, rather than a static "you can close this
                # window" byte string: a resolved (non-timeout) stack
                # mismatch is always followed by a standards-approval step
                # in the same run (see server.py), and the navigation to
                # /stack-mismatch that got the user here already unloaded
                # config-submitted.html, killing both of its pollers. This
                # page picks the approval poll back up in the same tab.
                self._script_nonce = secrets.token_urlsafe(16)
                body = server._render_stack_choice_recorded_page(
                    self._script_nonce
                ).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
                if self.path == "/approve-standards":
                    self._serve_approval_page()
                    return
                if self.path == "/submit-standards":
                    self._handle_standards_submission()
                    return
                if self.path == "/submit-stack-choice":
                    self._handle_stack_choice_submission()
                    return
                if self.path != "/submit":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if server._submitted.is_set():
                    self.send_error(
                        HTTPStatus.CONFLICT, "Configuration already submitted"
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    # A non-numeric Content-Length is a malformed request, not
                    # a reason to crash the handler thread: reply 400 and keep
                    # serving.
                    self.send_error(
                        HTTPStatus.BAD_REQUEST, "Invalid Content-Length header"
                    )
                    return
                if length > _MAX_BODY_BYTES:
                    # An attacker-controlled Content-Length is not proof the
                    # body is actually that large, but reading up to it is:
                    # reject before ever calling rfile.read().
                    self.send_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"
                    )
                    return
                raw_body = self.rfile.read(length).decode("utf-8")
                fields = parse_qs(raw_body, keep_blank_values=True)

                token = fields.get("csrf_token", [None])[0]
                if token is None or not secrets.compare_digest(
                    token, server._csrf_token
                ):
                    # Missing, wrong, or forged: this submission did not
                    # come from a page this server itself rendered, so it
                    # does not get to change the run's configuration.
                    self.send_error(
                        HTTPStatus.FORBIDDEN, "Missing or invalid CSRF token"
                    )
                    return

                try:
                    config = server._parse_submission(fields)
                except ValidationError as exc:
                    if _is_empty_domain_selection_error(exc):
                        # This re-render gets its own nonce as well: it is the
                        # same form, and a page that quietly lost its heartbeat
                        # because the user forgot to tick a domain would be the
                        # exact misleading state the heartbeat exists to end.
                        self._script_nonce = secrets.token_urlsafe(16)
                        body = server._render_form(
                            error="Select at least one domain to audit.",
                            state=_form_state_from_fields(fields),
                            script_nonce=self._script_nonce,
                        ).encode("utf-8")
                        self.send_response(HTTPStatus.BAD_REQUEST)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                except _InvalidOutputLocation as exc:
                    # Same treatment as the empty-domain-selection error
                    # above: this is the second shape of bad request a normal
                    # person can reach honestly (a mistyped path), so it gets
                    # the form back with the problem named, not a bare 400.
                    self._script_nonce = secrets.token_urlsafe(16)
                    body = server._render_form(
                        output_location_error=str(exc),
                        state=_form_state_from_fields(fields),
                        script_nonce=self._script_nonce,
                    ).encode("utf-8")
                    self.send_response(HTTPStatus.BAD_REQUEST)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except ValueError as exc:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                with server._lock:
                    server._config = config
                server._submitted.set()
                self._script_nonce = secrets.token_urlsafe(16)
                body = server._render_submitted_page(self._script_nonce).encode("utf-8")
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

    def _render_form(
        self,
        *,
        error: str | None = None,
        output_location_error: str | None = None,
        state: _FormState | None = None,
        draft: _ConfigDraft | None = None,
        script_nonce: str = "",
    ) -> str:
        if state is not None:
            selected_ids = state.selected_domain_ids
            issue_mode = state.issue_mode
            consent = state.consent
            feedback_text = state.feedback_text
            output_location_mode = state.output_location_mode
            output_location_path = state.output_location_path
        else:
            defaults = self._defaults
            selected_ids = (
                set(defaults.selected_domain_ids)
                if defaults
                else {d.id for d in self._domains}
            )
            issue_mode = defaults.issue_mode if defaults else "report"
            consent = defaults.telemetry_consent if defaults else TelemetryConsent()
            feedback_text = (defaults.feedback_text or "") if defaults else ""
            # A fresh form always opens on the default location: a saved
            # draft never carries a custom path (see _ConfigDraft's
            # docstring for why the cookie stays narrow), and there is no
            # other source of a prior choice for a form that has not been
            # submitted yet.
            output_location_mode = (
                "custom" if defaults and defaults.deliverables_dir else "default"
            )
            output_location_path = (defaults.deliverables_dir or "") if defaults else ""
            if draft is not None:
                # A recovered draft beats the all-domains default, because it
                # is a choice the user actually made, and it beats the run's
                # own defaults for the same reason. It never touches consent:
                # see _DRAFT_COOKIE_NAME.
                selected_ids = draft.selected_domain_ids
                issue_mode = draft.issue_mode

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
        domain_error = (
            f'<p class="form-error">{html.escape(error)}</p>' if error else ""
        )
        output_location_error_html = (
            f'<p class="form-error">{html.escape(output_location_error)}</p>'
            if output_location_error
            else ""
        )
        draft_notice = (
            '<p class="draft-notice">Your previous domain selection has been restored. '
            "The consent boxes below start unticked: those are yours to choose again.</p>"
            if draft is not None
            else ""
        )
        output_dir_display = (
            html.escape(str(self._output_dir))
            if self._output_dir is not None
            else "this run's output directory"
        )
        gitignore_warning_html = (
            f'<p class="gitignore-warning">{html.escape(self._gitignore_warning)}</p>'
            if self._gitignore_warning
            else ""
        )
        # Computed here, live, rather than precomputed by the caller like
        # gitignore_warning: unlike the gitignore check this is a plain
        # filesystem stat with no git subprocess involved, so there is no
        # benefit to doing it anywhere but where the other output-location
        # filesystem checks in this class already run (see
        # _serve_output_location_check and _parse_submission below). None
        # when there is no run behind this page at all (see self._output_dir).
        default_location_warning = (
            existing_deliverables_warning(self._output_dir)
            if self._output_dir is not None
            else None
        )
        existing_deliverables_warning_html = (
            f'<p class="output-location-warning">{html.escape(default_location_warning)}</p>'
            if default_location_warning
            else ""
        )

        template = string.Template(self._template_text)
        return template.substitute(
            domain_checkboxes="\n".join(domain_rows),
            domain_error=domain_error,
            draft_notice=draft_notice,
            csp_nonce=html.escape(script_nonce),
            alive_path=_ALIVE_PATH,
            check_output_location_path=_CHECK_OUTPUT_LOCATION_PATH,
            draft_cookie_name=_DRAFT_COOKIE_NAME,
            draft_cookie_max_age=str(_DRAFT_COOKIE_MAX_AGE_S),
            heartbeat_interval_ms=str(_HEARTBEAT_INTERVAL_MS),
            heartbeat_failures_before_dead=str(_HEARTBEAT_FAILURES_BEFORE_DEAD),
            issue_mode_github_checked="checked" if issue_mode == "github" else "",
            issue_mode_report_checked="checked" if issue_mode == "report" else "",
            feedback_text=html.escape(feedback_text or ""),
            consent_coverage_checked="checked" if consent.coverage else "",
            consent_rollup_checked="checked" if consent.rollup else "",
            consent_self_assessment_checked="checked"
            if consent.self_assessment
            else "",
            consent_environment_checked="checked" if consent.environment else "",
            consent_consulted_sources_checked="checked"
            if consent.consulted_sources
            else "",
            consent_verdict_distribution_checked="checked"
            if consent.verdict_distribution
            else "",
            consent_duration_checked="checked" if consent.duration else "",
            consent_rules_fetched_checked="checked" if consent.rules_fetched else "",
            consent_reader_conclusions_checked="checked"
            if consent.reader_conclusions
            else "",
            csrf_token=html.escape(self._csrf_token),
            output_location_error=output_location_error_html,
            output_dir_display=output_dir_display,
            gitignore_warning=gitignore_warning_html,
            existing_deliverables_warning=existing_deliverables_warning_html,
            output_location_default_checked="checked"
            if output_location_mode == "default"
            else "",
            output_location_custom_checked="checked"
            if output_location_mode == "custom"
            else "",
            output_location_path=html.escape(output_location_path or ""),
        )

    def _render_submitted_page(self, script_nonce: str = "") -> str:
        """Render the "configuration received" page shown after /submit.

        This is the one tab a human still has open while the audit runs, so
        it carries two independent pollers: one for _APPROVAL_READY_PATH and
        one for _STACK_MISMATCH_READY_PATH (see config-submitted.html), each
        navigating itself to its own destination page the moment there is
        something to review. Without those scripts this page is a dead end:
        nothing else ever tells a real browser that either page exists.
        Either poller can fire first, since either a standards approval or a
        stack mismatch may come up first depending on the run.
        """
        template = string.Template(self._submitted_text)
        return template.substitute(
            csp_nonce=html.escape(script_nonce),
            approval_ready_path=_APPROVAL_READY_PATH,
            approval_page_path="/approve-standards",
            approval_poll_interval_ms=str(_HEARTBEAT_INTERVAL_MS),
            stack_mismatch_ready_path=_STACK_MISMATCH_READY_PATH,
            stack_mismatch_page_path="/stack-mismatch",
            stack_mismatch_poll_interval_ms=str(_HEARTBEAT_INTERVAL_MS),
        )

    def _render_stack_choice_recorded_page(self, script_nonce: str = "") -> str:
        """Render the page shown after a valid POST to /submit-stack-choice.

        The mismatch page that got the user here already navigated their
        one open tab away from config-submitted.html, unloading both of
        its pollers. A resolved (non-timeout) stack mismatch is always
        followed by a standards-approval step in the same run (see
        server.py's ordering), so this page carries the same
        approval-readiness poller config-submitted.html does -- just the
        one, since the stack-mismatch choice this page is confirming has
        already been made -- rather than a static "you can close this
        window" message that would strand the user exactly when a review
        may still be coming.
        """
        template = string.Template(
            self._stack_choice_recorded_template_text
            or "<html><body><p>Stack choice recorded.</p></body></html>"
        )
        return template.substitute(
            csp_nonce=html.escape(script_nonce),
            approval_ready_path=_APPROVAL_READY_PATH,
            approval_page_path="/approve-standards",
            approval_poll_interval_ms=str(_HEARTBEAT_INTERVAL_MS),
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
            consulted_sources="consent_consulted_sources" in fields,
            verdict_distribution="consent_verdict_distribution" in fields,
            duration="consent_duration" in fields,
            rules_fetched="consent_rules_fetched" in fields,
            reader_conclusions="consent_reader_conclusions" in fields,
        )

        output_location_mode = fields.get("output_location", ["default"])[0]
        deliverables_dir: str | None = None
        if output_location_mode == "custom":
            raw_path = fields.get("output_location_path", [""])[0].strip()
            if not raw_path:
                raise _InvalidOutputLocation(
                    "Enter a custom path, or choose the default location inside the "
                    "repository."
                )
            try:
                resolved = resolve_deliverables_dir(raw_path)
            except UnresolvableOutputLocation as exc:
                # Same friendly, form-preserving re-render as any other bad
                # custom path (see _InvalidOutputLocation's own docstring):
                # an unknown ~user is an ordinary typo, not a reason to
                # crash the handler thread and lose every other field the
                # user had already filled in.
                raise _InvalidOutputLocation(str(exc)) from exc
            path_error = validate_deliverables_dir(resolved)
            if path_error:
                raise _InvalidOutputLocation(path_error)
            deliverables_dir = str(resolved)

        # No fallback to "all domains" here: an empty selection is a real user
        # choice (or a broken form) and AuditConfig rejects it below, loudly,
        # rather than this function quietly inventing a selection nobody made.
        return AuditConfig(
            selected_domain_ids=selected_domain_ids,
            issue_mode=issue_mode,
            feedback_text=feedback_text,
            telemetry_consent=consent,
            deliverables_dir=deliverables_dir,
        )

    def poll(self) -> "str | AuditConfig":
        """Return the submitted AuditConfig, or the literal string 'pending'."""
        if self._submitted.is_set():
            with self._lock:
                if self._config is None:
                    raise RuntimeError(
                        "submitted event set but no config stored: internal bug"
                    )
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
                raise RuntimeError(
                    "submitted event set but no config stored: internal bug"
                )
            return self._config

    def wait_approval(self, timeout_s: float) -> str:
        """Block for up to timeout_s seconds for approval or cancellation.

        Returns:
            "approve" if the user clicked Approve, "cancel" if they clicked Cancel.

        Raises ConfigTimeoutError on expiry.

        Either way, this call is the one place that knows the approval
        window is now over, so it is the one place that closes it: on
        expiry, and again the moment a real decision is read out of
        _approval_action, _approval_data is cleared and
        _approval_closed_reason is set to an honest explanation. That is
        what makes /approval-ready stop pointing a poller here, makes
        /approve-standards stop serving the page, and makes a late POST to
        /submit-standards get told the window is closed instead of the
        false "processed" success it would otherwise still receive. Closing
        it anywhere else (the server.py call site, say) would miss the
        second case: nothing there ever runs again once wait_approval has
        already returned a decision.
        """
        if not self._approval_submitted.wait(timeout=timeout_s):
            with self._lock:
                self._approval_data = None
                self._approval_window_state = _WindowState.CLOSED
                self._approval_closed_reason = (
                    "The standards review window has closed: no decision "
                    "arrived within the time allowed, so the audit run "
                    "moved on without writing the standards documents. "
                    "This decision was not recorded and cannot be accepted "
                    "now."
                )
            raise ConfigTimeoutError(
                f"No approval decision submitted within {timeout_s} seconds."
            )
        with self._lock:
            if self._approval_action is None:
                raise RuntimeError(
                    "approval_submitted event set but no action stored: internal bug"
                )
            action = self._approval_action
            self._approval_data = None
            self._approval_window_state = _WindowState.CLOSED
            self._approval_closed_reason = (
                "The standards review window has closed: a decision for "
                "this run has already been recorded, and the audit run has "
                "moved on. This decision was not recorded."
            )
            return action

    def set_stack_mismatch_data(
        self,
        grill_stack: frozenset[str] | tuple[str, ...],
        observed_stack: DetectedStack,
        difference: dict[str, Any],
    ) -> None:
        """Set the stack mismatch data for the /stack-mismatch endpoint.

        Args:
            grill_stack: The stack recorded at grill time (frozenset or tuple of identifiers).
            observed_stack: The stack detected during audit (DetectedStack object).
            difference: Dict describing the stack difference.
        """
        with self._lock:
            self._stack_mismatch_data = {
                "grill_stack": grill_stack,
                "observed_stack": observed_stack,
                "difference": difference,
            }
            self._stack_choice_window_state = _WindowState.OPEN

    def wait_stack_choice(self, timeout_s: float) -> str:
        """Block for up to timeout_s seconds for a stack choice.

        Returns:
            "grill" if the user chose to use the grill stack,
            "audit" if the user chose to use the audit stack.

        Raises ConfigTimeoutError on expiry.

        Either way, this call is the one place that knows the stack-choice
        window is now over, so it is the one place that closes it: on
        expiry, and again the moment a real choice is read out of
        _stack_choice, _stack_mismatch_data is cleared and
        _stack_choice_closed_reason is set to an honest explanation. That
        mirrors wait_approval exactly (see its own docstring for why this
        cannot be done anywhere else): it is what makes
        /stack-mismatch-ready stop pointing a poller at /stack-mismatch,
        makes /stack-mismatch stop serving the page, and makes a late POST
        to /submit-stack-choice get told the window is closed instead of a
        false "recorded" success.
        """
        if not self._stack_choice_submitted.wait(timeout=timeout_s):
            with self._lock:
                self._stack_mismatch_data = None
                self._stack_choice_window_state = _WindowState.CLOSED
                self._stack_choice_closed_reason = (
                    "The stack-choice window has closed: no decision "
                    "arrived within the time allowed, so the audit run "
                    "moved on without applying a stack choice. This "
                    "decision was not recorded and cannot be accepted now."
                )
            raise ConfigTimeoutError(
                f"No stack choice submitted within {timeout_s} seconds."
            )
        with self._lock:
            if self._stack_choice is None:
                raise RuntimeError(
                    "stack_choice_submitted event set but no choice stored: internal bug"
                )
            choice = self._stack_choice
            self._stack_mismatch_data = None
            self._stack_choice_window_state = _WindowState.CLOSED
            self._stack_choice_closed_reason = (
                "The stack-choice window has closed: a decision for this "
                "run has already been recorded, and the audit run has "
                "moved on. This decision was not recorded."
            )
            return choice

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def set_approval_data(
        self, diffs: list[DiffModel], summary_counts: SummaryCount
    ) -> None:
        """Set the approval data for the /approve-standards endpoint.

        Args:
            diffs: List of DiffModel objects (one per document).
            summary_counts: SummaryCount object with rule counts.
        """
        with self._lock:
            self._approval_data = _ApprovalData(
                diffs=diffs, summary_counts=summary_counts
            )
            self._approval_window_state = _WindowState.OPEN

    def _render_approval_page(self, script_nonce: str = "") -> str:
        """Render the approval page showing diffs of the three documents.

        Args:
            script_nonce: CSP nonce for inline scripts.

        Returns:
            HTML string ready to send to the client.
        """
        # Read both under the lock and hold onto the result, rather than
        # re-reading self._approval_data through the rest of this method:
        # wait_approval can clear it, concurrently, the instant a decision
        # arrives or the window times out (see wait_approval), and this
        # method's own caller (_serve_approval_page) checks for None before
        # ever calling it. A second, unlocked read here could see None where
        # the caller saw data, which is exactly the kind of gap that used to
        # be harmless only because nothing ever cleared this field.
        with self._lock:
            approval_data = self._approval_data
            approval_template_text = self._approval_template_text
        if approval_data is None or approval_template_text is None:
            return "<html><body>Approval data not available</body></html>"

        # Build HTML for each diff
        diffs_html = []
        for diff in approval_data.diffs:
            current = diff.current_content or "(File does not exist yet)"
            # Escape content first, then apply marker highlighting
            escaped_current = html.escape(current)
            highlighted_current = highlight_managed_block_markers(escaped_current)
            escaped_proposed = html.escape(diff.proposed_content)
            highlighted_proposed = highlight_managed_block_markers(escaped_proposed)
            diffs_html.append(
                f"""
    <div class="document-diff" data-document-id="{html.escape(diff.document_id)}">
        <h3>{html.escape(get_document_title(diff.document_id))}</h3>
        <div class="diff-container">
            <div class="diff-side current-side">
                <h4>Current</h4>
                <pre>{highlighted_current}</pre>
            </div>
            <div class="diff-side proposed-side">
                <h4>Proposed</h4>
                <pre>{highlighted_proposed}</pre>
            </div>
        </div>
    </div>
                """
            )

        summary = approval_data.summary_counts
        summary_html = f"""
    <div class="summary">
        <h2>Summary of Changes</h2>
        <ul>
            <li><strong>New rules:</strong> {summary.new_rules}</li>
            <li><strong>Verified (passed):</strong> {summary.upgraded_to_verified}</li>
            <li><strong>Findings recorded:</strong> {summary.findings_recorded}</li>
            <li><strong>Not applicable:</strong> {summary.not_applicable}</li>
        </ul>
    </div>
        """

        template = string.Template(approval_template_text)
        return template.substitute(
            csp_nonce=html.escape(script_nonce),
            summary=summary_html,
            diffs="\n".join(diffs_html),
            csrf_token=html.escape(self._csrf_token),
        )

    def _render_stack_mismatch_page(self, script_nonce: str = "") -> str:
        """Render the stack mismatch choice page.

        Args:
            script_nonce: CSP nonce for inline scripts.

        Returns:
            HTML string ready to send to the client.
        """
        if (
            self._stack_mismatch_data is None
            or self._stack_mismatch_template_text is None
        ):
            return "<html><body>Stack mismatch data not available</body></html>"

        data = self._stack_mismatch_data
        grill_stack = data["grill_stack"]
        observed_stack = data["observed_stack"]

        # Build HTML for grill stack evidence
        grill_html = self._format_stack_evidence(grill_stack, observed_stack, "grill")

        # Build HTML for observed stack evidence
        observed_html = self._format_stack_evidence(
            grill_stack, observed_stack, "observed"
        )

        template = string.Template(self._stack_mismatch_template_text)
        return template.substitute(
            csp_nonce=html.escape(script_nonce),
            grill_stack_html=grill_html,
            observed_stack_html=observed_html,
            csrf_token=html.escape(self._csrf_token),
        )

    def _format_stack_evidence(
        self, grill_stack, observed_stack, stack_type: str
    ) -> str:
        """Format stack evidence as HTML, escaping all values.

        Args:
            grill_stack: Frozenset or tuple of grill stack identifiers.
            observed_stack: DetectedStack object from stack_detection.
            stack_type: "grill" or "observed".

        Returns:
            HTML string with the stack and its evidence.
        """
        if stack_type == "grill":
            identifiers = grill_stack
            evidence_dict: dict[
                str, Any
            ] = {}  # Grill stack doesn't have evidence, just identifiers
        else:
            # Observed stack has the evidence
            identifiers = observed_stack.identifiers if observed_stack else ()
            evidence_dict = observed_stack.evidence if observed_stack else {}

        # Build list of identifiers
        identifier_html = []
        for identifier in sorted(identifiers):
            if stack_type == "grill":
                # Grill stack: just list identifiers
                identifier_html.append(f"<li>{html.escape(identifier)}</li>")
            else:
                # Observed stack: list identifier with evidence
                if identifier in evidence_dict:
                    evidence = evidence_dict[identifier]
                    file_path = html.escape(evidence.file_path)
                    dependency_or_line = html.escape(evidence.dependency_or_line)
                    identifier_html.append(
                        f"<li>{html.escape(identifier)}<br>"
                        f'<span class="evidence">Found in: {file_path}</span><br>'
                        f'<span class="evidence">Evidence: {dependency_or_line}</span></li>'
                    )
                else:
                    identifier_html.append(f"<li>{html.escape(identifier)}</li>")

        if not identifier_html:
            identifier_html.append("<li>(no stack identifiers detected)</li>")

        return "<ul>" + "\n".join(identifier_html) + "</ul>"
