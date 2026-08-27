"""MCP stdio server exposing the rules pack to a local coding agent.

Milestone 1 shipped the deterministic core: the rules pack loader and two
read-only, pack-inspection tools (list_domains, get_domain). Milestone 2
wired the full audit flow on top of it: begin_run, start_config, get_config,
record_domain_result, run_status and render_report. Milestone 3 adds
file_issues (GitHub issue filing via the user's own gh CLI, gated behind an
explicit confirmation step) and submit_feedback (an optional feedback
channel to the tool author). The driving agent is expected to call these
roughly in that order, once per audited domain in between
record_domain_result calls; see AUDIT.md for the full procedure.

The tools are registered per concern by the _register_*_tools functions,
which build_server calls in sequence; adding or changing one tool means
reading one of those, not the whole module.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution as _pkg_distribution
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.server import MCPServer
from pydantic import ValidationError

# Private import: the SDK enables OpenTelemetry span middleware on every
# server unconditionally (mcp/server/lowlevel/server.py), and this tool's
# consent model forbids ambient telemetry, so it is stripped out in
# build_server() below. If this import breaks on an SDK upgrade, that is the
# loud ImportError we want rather than a silent no-op strip.
#
# An import that keeps working is not proof the strip still works: a future
# mcp 2.x could rename or relocate this class while the module stays put, in
# which case the isinstance filter below would silently match nothing. See
# issue #107. _strip_ambient_otel_middleware asserts the postcondition
# instead of assuming it, with a name-based backstop that does not depend on
# this same symbol.
from mcp.server._otel import OpenTelemetryMiddleware

from engineering_audit.config_page import ConfigServer, ConfigTimeoutError
from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    FEEDBACK_REPO,
    build_feedback_body,
    build_issue_trailing_line,
    build_mailto_url,
    feedback_subject,
    strip_markdown_emphasis,
)
from engineering_audit.issues import (
    IssueFilingError,
    create_issue,
    detect_repo,
    ensure_label,
    gh_available,
)
from engineering_audit.output_location import (
    REPORT_FILENAME,
    RUN_STATE_FILENAME,
    UnresolvableOutputLocation,
    deliverables_dir_for,
    resolve_deliverables_dir,
    validate_deliverables_dir,
)
from engineering_audit.report import ReportError, write_report
from engineering_audit.rules import (
    Rule,
    RulesPack,
    RulesPackError,
    get_domain_text,
    load_pack,
    read_pack_metadata,
)
from engineering_audit.run_state_io import (
    PROGRESS_FILENAME,
    RunStateLoadError,
    atomic_write_text,
    load_run_progress_file,
    save_run_progress,
)
from engineering_audit.stack_detection import (
    describe_stack_difference,
    detect_stack,
    grill_stack_from_rule_set,
    stacks_differ,
)
from engineering_audit.standards import Rule as StandardsRule, RuleSet
from engineering_audit.standards_integration import (
    audit_rules_from_domain_results,
    build_diffs,
    build_stack_choice_decision,
    derive_summary_counts,
    load_prior_rule_set,
    render_all,
    resolve_stack_choice,
    verdicts_from_domain_results,
    write_standards,
)
from engineering_audit.standards_merge import merge_rule_set
from engineering_audit.schema import (
    RUN_STATE_SCHEMA_VERSION,
    AuditConfig,
    DomainResult,
    Finding,
    RunMeta,
    RunProgress,
    RunState,
    Verdict,
    validate_completeness,
    validate_consulted_sources,
    validate_finding_locations,
    validate_environment,
)
from engineering_audit.update_check import check_for_update, check_pack_for_update

__all__ = ["AppState", "FinishedRun", "PriorRun", "RunTracker", "build_server", "main"]

_SEVERITY_ORDER = ("critical", "high", "medium", "low")

# The longest get_config will block inside a single MCP call before returning
# status="waiting" and asking to be called again.
#
# Chosen to sit well under the shortest per-tool timeout a host is known to
# impose: Codex has mcp_servers.<name>.tool_timeout_sec, and the run reported
# in issue #85 was cancelled by it after 300 seconds. A call cancelled by a
# host timeout does not merely fail, it can take the whole stdio MCP process
# down with it and the run's configuration page along with it. The margin is
# deliberately large, because
# the cost of being wrong in one direction (a dead process and a stale form)
# is nothing like the cost in the other (one more cheap tool call).
_CONFIG_POLL_INTERVAL_S = 25.0


def _default_tool_version() -> str:
    """Read the installed package version, or a clear placeholder if the
    package metadata is not available (e.g. running from a source checkout
    that was never installed)."""
    try:
        return _pkg_version("engineering-audit")
    except PackageNotFoundError:
        return "0.0.0-dev"


def _now_utc_iso() -> str:
    """The server's own UTC wall-clock stamp, in the same 'Z'-suffixed ISO
    8601 form the assistant-supplied started/finished timestamps are
    documented to use.

    Exists as one function, rather than inlining datetime.now(timezone.utc)
    at each call site, so begin_run and render_report always stamp in
    exactly the same format and so a test can freeze "now" with a single
    monkeypatch. See RunMeta.server_started/server_finished (schema.py) and
    issue #102: this is the reading a report's duration is actually checked
    against, independent of whatever the assistant claims for started/finished.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_direct_url_commit(direct_url_json: str) -> str | None:
    """Pull ``vcs_info.commit_id`` out of a PEP 610 ``direct_url.json``
    document's text, or None if it is not a git install record.

    Factored out of :func:`_default_tool_commit` as a pure function so the
    parsing logic is testable with plain string fixtures, without needing a
    real installed distribution or importlib.metadata in the test.
    """
    try:
        data = json.loads(direct_url_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    vcs_info = data.get("vcs_info")
    if not isinstance(vcs_info, dict):
        return None
    commit_id = vcs_info.get("commit_id")
    return commit_id if isinstance(commit_id, str) and commit_id else None


def _parse_direct_url_source_dir(direct_url_json: str) -> Path | None:
    """Pull the local source directory out of a PEP 610 ``direct_url.json``
    document's text, when it records a directory install (``dir_info``
    present: an editable install, or a plain ``pip install /path/to/checkout``),
    or None otherwise (a git-URL install, which carries ``vcs_info`` instead,
    or a PyPI/wheel install, which carries neither).

    Factored out of :func:`_default_tool_commit` as a pure function for the
    same reason :func:`_parse_direct_url_commit` is: testable with plain
    string fixtures, no real installed distribution needed.
    """
    try:
        data = json.loads(direct_url_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    dir_info = data.get("dir_info")
    if not isinstance(dir_info, dict):
        return None
    url = data.get("url")
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    path_str = url2pathname(urlparse(url).path)
    return Path(path_str) if path_str else None


def _default_tool_commit() -> str | None:
    """Best-effort: the git commit the installed tool build was made from.

    Two sources, tried in order:

    1. ``vcs_info.commit_id`` from the installed distribution's PEP 610
       install record (direct_url.json), present when installed via
       ``pip``/``uv`` from a git URL. A git-URL install is an immutable
       build, so this never carries a ``-dirty`` suffix.
    2. Failing that, ``dir_info`` from the same record (issue #169): an
       editable install, or a plain ``pip install /path/to/checkout``, has
       no ``vcs_info`` at all but does record the local source directory it
       was installed from. Falling back to :func:`_git_commit` over that
       directory turns what used to be a flat "unknown" for every dev
       checkout into a real SHA (or ``SHA-dirty`` when the tree has
       uncommitted changes), which is the exact blind spot issue #136
       documented: a developer running against a clone got no tool
       provenance at all. This fallback is scoped repo-wide
       (``subtree_only=False``), deliberately unlike the rules pack's
       subtree-only scoping from #168: the tool's whole source tree *is*
       the running code, so dirt anywhere in it is genuinely worth
       recording. Do not "harmonise" the two call sites; the difference is
       intentional. See :func:`_git_commit`'s docstring for the same point
       made from the other side.

    Neither source is load-bearing for the tool to run, so any failure here
    (package not installed, no direct_url.json, unreadable, malformed, no
    source tree, git missing) is swallowed and reported as None: the caller
    renders that as "unknown" in the report rather than fabricating a
    commit the tool cannot actually vouch for. A wheel/PyPI install, which
    has neither vcs_info nor dir_info, still reports unknown: there is no
    source tree to fall back to.
    """
    try:
        direct_url_json = _pkg_distribution("engineering-audit").read_text(
            "direct_url.json"
        )
    except Exception:
        return None
    if direct_url_json is None:
        return None
    commit = _parse_direct_url_commit(direct_url_json)
    if commit is not None:
        return commit
    source_dir = _parse_direct_url_source_dir(direct_url_json)
    if source_dir is None:
        return None
    return _git_commit(source_dir, subtree_only=False)


def _run_git(args: list[str], path: Path) -> subprocess.CompletedProcess[str] | None:
    """Run a git subcommand rooted at ``path``, or None if it could not be
    run at all (git missing, or it timed out)."""
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_commit(path: Path, *, subtree_only: bool) -> str | None:
    """Best-effort: the full HEAD SHA of the git repository containing
    ``path``, with a ``-dirty`` suffix appended when the working tree has
    uncommitted changes.

    ``subtree_only`` decides what "dirty" is scoped to, and every call site
    must say which it means: there is no default, so a new call site cannot
    silently pick the wrong one by omission.

    - ``True``: only changes within the ``path`` subtree count (``git
      status --porcelain -- .``, run with cwd at ``path``). Used for the
      rules pack (issue #168): ``load_pack`` reads only that directory, so
      an untracked file elsewhere in a containing repository (a stray
      .DS_Store at the clone root, an audit-output/ directory, editor
      droppings) cannot affect what gets audited, and must not cost the
      reader their pack staleness comparison by tripping a dirty flag it
      has nothing to do with.
    - ``False``: any change anywhere in the containing repository counts
      (plain ``git status --porcelain``, no pathspec). Used for the tool's
      own source tree (issue #169): the whole tree *is* the running code,
      so dirt anywhere in it is genuinely worth recording, unlike the pack
      case above. Do not "harmonise" these two call sites into one scope;
      the difference is deliberate, not an oversight left over from before
      #168.

    None means could-not-determine (git not installed, ``path`` is not
    inside a repository, or either git call timed out or exited non-zero),
    and is rendered as "unknown" downstream, never as a fabricated value.
    """
    rev_parse = _run_git(["rev-parse", "HEAD"], path)
    if rev_parse is None or rev_parse.returncode != 0:
        return None
    sha = rev_parse.stdout.strip()
    if not sha:
        return None
    status_args = ["status", "--porcelain"]
    if subtree_only:
        status_args += ["--", "."]
    status = _run_git(status_args, path)
    if status is None or status.returncode != 0:
        return None
    return f"{sha}-dirty" if status.stdout.strip() else sha


def _git_release_version(path: Path) -> str | None:
    """Best-effort: the release version of the rules pack at ``path``, as the
    most recent version tag reachable from HEAD, with a ``+<count>`` suffix
    naming how many commits HEAD has moved past that tag.

    A report identifies its rules by commit SHA, which is precise and tells a
    reader nothing without a checkout of the pack to resolve it against. The
    standard pack publishes semantic-version releases, so "audited against
    rules pack v0.6.0" is the statement worth making, and a finding is a claim
    measured against a specific version of a rule.

    None means could-not-determine (git missing, not a repository, no version
    tags, or the call failed), and renders as "unknown" downstream. It is never
    inferred or fabricated: a third-party pack with no tags legitimately has no
    version, and saying so is the honest answer rather than guessing one from
    a commit date or a directory name.

    The ``+<count>`` suffix matters more than it looks. A pack sitting several
    commits past v0.6.0 is not v0.6.0, and reporting it as such would attribute
    findings to rule text that release does not contain. Same failure as
    reporting a resumed run's original model: a precise-looking provenance
    value that is quietly wrong. A bare ``+`` (the previous shape) said "past
    the tag" but not how far, which left two runs' packs only comparable by
    diffing their SHAs; ``git describe --long`` already counts the commits,
    so this reports the count it gives instead of discarding it, e.g.
    ``v0.6.1+14`` rather than ``v0.6.1+``.
    """
    describe = _run_git(["describe", "--tags", "--long", "--match", "v*"], path)
    if describe is None or describe.returncode != 0:
        return None
    output = describe.stdout.strip()
    if not output:
        return None
    # --long always emits '<tag>-<count>-g<sha>', including at the tag itself
    # ('<tag>-0-g<sha>'). count and the 'g<sha>' suffix are the last two
    # hyphen-separated fields; the tag is everything before them, split from
    # the right rather than the left because a tag name may itself contain
    # hyphens (e.g. a pre-release suffix).
    parts = output.rsplit("-", 2)
    if len(parts) != 3:
        return None
    tag, count_str, _ghash = parts
    if not tag or not count_str.isdigit():
        return None
    count = int(count_str)
    return tag if count == 0 else f"{tag}+{count}"


def _output_dir_ignore_warning(repo_dir: Path | None, output_dir: Path) -> str | None:
    """A plain-language warning if output_dir is not covered by a gitignore
    entry inside repo_dir, or None if it is, or the check does not apply, or
    it could not be made at all.

    Issue #109: a tester noticed by eye that the default output_dir sits
    untracked inside the repository being audited, with nothing telling
    them so. This is the tool making the same observation itself, from
    repo_dir, before the run gets far enough to matter: shown on the
    configuration page next to the default choice, not after the fact.

    Best effort, the same convention every other git fact in this module
    follows (see _git_commit, _git_release_version): no repo_dir, git not
    installed, the call timing out, or git reporting a fatal error (most
    commonly output_dir sitting outside repo_dir entirely, or repo_dir not
    being a git repository at all) all fall through to None rather than a
    guess dressed up as a fact.
    """
    if repo_dir is None:
        return None
    result = _run_git(["check-ignore", "-q", str(output_dir)], repo_dir)
    if result is None or result.returncode not in (0, 1):
        return None
    if result.returncode == 0:
        return None  # already ignored: nothing to warn about
    return (
        f"{output_dir} is not covered by a .gitignore entry in this repository. Add one, "
        "or choose a custom location above, or the report and run state risk being "
        "committed by accident."
    )


@dataclass
class RunTracker:
    """Mutable, in-progress state for one audit run.

    This is deliberately not the pydantic :class:`~engineering_audit.schema.RunState`:
    that model requires ``config`` up front, but a run exists (post begin_run)
    before configuration is chosen. This tracker holds whatever has been
    gathered so far and is assembled into a real RunState only once render_report
    runs.
    """

    meta: RunMeta
    output_dir: Path
    # The schema_version this run's already-recorded domain_results were
    # loaded at, carried forward rather than left to default to the current
    # RUN_STATE_SCHEMA_VERSION. A fresh run (no prior file) has nothing
    # loaded, so the current version is exactly right here; a resumed run
    # sets this from the file it resumed (see _resume_run), because that
    # file's domain_results may still hold verdicts recorded under an older,
    # more lenient rule (issue #100's not-applicable note requirement,
    # relaxed for anything below NOT_APPLICABLE_NOTE_SCHEMA_VERSION in
    # schema.py). _run_progress and render_report both build a fresh
    # RunProgress/RunState from this tracker rather than re-serialising
    # whatever was loaded, and Pydantic does not revalidate the
    # already-validated DomainResult/RuleVerdict instances that construction
    # carries over, so that write always succeeds regardless of this value.
    # What this value protects is the file's own honesty: declaring the
    # current version while still holding a verdict that predates its rule
    # would tell the next load "hold this to the full requirement" about
    # data that cannot honestly meet it, and the next load would refuse it
    # (issue #155). Carrying the true version forward is safe to keep doing
    # indefinitely, including once every domain in the file is fully
    # compliant: relaxing an already-satisfied requirement changes nothing,
    # since record_domain_result enforces the requirement in full for every
    # verdict recorded from here on, independent of this field.
    schema_version: int = RUN_STATE_SCHEMA_VERSION
    repo_dir: Path | None = None
    config: AuditConfig | None = None
    config_mode: str | None = None
    config_url: str | None = None
    config_server: ConfigServer | None = None
    # A monotonic stamp taken when the interactive configuration page starts,
    # so get_config can hold the run's overall waiting deadline across many
    # short polls instead of inside one long blocking call. Monotonic, not
    # wall clock: a machine that sleeps or has its clock stepped mid-wait must
    # not be able to turn "still waiting" into "timed out" or the reverse.
    config_wait_started_at: float | None = None
    domain_results: dict[str, DomainResult] = field(default_factory=dict)
    # Keyed by finding key (see _pending_issues), not by rule id: a domain
    # result may legitimately carry two findings for the same rule, and a map
    # keyed by rule id drops one of the two issue urls without saying so, and
    # makes the second finding look already-filed on a retry.
    filed_issues: dict[str, str] = field(default_factory=dict)
    # Domain ids get_domain has served rule text for during this run, and the
    # domain ids whose fetch status can never be known for it. See
    # RULES_FETCHED_FIELD_DESCRIPTION and RULES_FETCH_UNKNOWN_FIELD_DESCRIPTION
    # in schema.py: these two are that pair, held as sets while the run is live.
    #
    # A live tracker always records, so the first set is never None here; the
    # None the schema allows belongs to a run-state written before any of this
    # existed, and only reaches the models by being loaded from disk.
    rules_fetched: set[str] = field(default_factory=set)
    rules_fetch_unknown: set[str] = field(default_factory=set)
    # Per-domain stamps from the server's own clock (issue #205). Held as
    # plain dicts while the run is live, for the same reason rules_fetched is
    # a live set: a running tracker always records, so the None the schema
    # allows belongs to a saved file written before these existed and only
    # reaches the models by being loaded from disk. See
    # DOMAIN_RULES_FETCHED_AT_DESCRIPTION and DOMAIN_RECORDED_AT_DESCRIPTION
    # in schema.py, in particular that these are arrival stamps and not
    # per-domain durations.
    domain_rules_fetched_at: dict[str, str] = field(default_factory=dict)
    domain_recorded_at: dict[str, str] = field(default_factory=dict)
    feedback_issue_url: str | None = None
    resumed: bool = False
    # Persistence failures waiting to be reported, and the set of facts
    # already reported. A failed write must be visible in a tool response,
    # but repeating the same line on every later call trains the agent
    # reading them to skip the whole field, so each distinct fact is said
    # once and then remembered here as said.
    persist_warnings: list[str] = field(default_factory=list)
    persist_warnings_seen: set[str] = field(default_factory=set)


@dataclass
class FinishedRun:
    """A run that render_report has already written out.

    Kept so a late submit_feedback (the order AUDIT.md documents: feedback is
    offered after the report is handed over) still has a run to send feedback
    for, and so the written report and run-state can be rewritten to carry the
    feedback issue's URL. Without the rewrite the two files would claim no
    feedback was ever sent while an issue existed for it.
    """

    tracker: RunTracker
    run_state: RunState
    report_path: Path
    run_state_path: Path


@dataclass(frozen=True)
class PriorRun:
    """A crash-recovery file found in the output directory begin_run was
    pointed at.

    ``progress`` is None when the file is present but could not be loaded,
    with ``error`` carrying why. The two are kept distinct on purpose: a
    recovery file that cannot be read is a prior run whose contents are
    unknown, which is never the same thing as no prior run at all. Collapsing
    them would turn an unreadable file into a silent fresh start over the top
    of it.
    """

    path: Path
    progress: RunProgress | None
    error: str | None


@dataclass
class AppState:
    """Process-wide state for one server run."""

    pack: RulesPack
    run: RunTracker | None = None
    finished: FinishedRun | None = None
    # Whether begin_run should run its update checks at all: the flag and the
    # environment variable, already reconciled together by build_server (see
    # its docstring), carried here as a plain value rather than left for
    # begin_run to re-derive from os.environ at call time. See
    # _update_check_enabled_from_env below for the environment-variable half
    # of that reconciliation.
    update_check_enabled: bool = True


@dataclass(frozen=True)
class PendingIssue:
    """One recorded finding, with the key its filing bookkeeping is under.

    ``key`` is ``<rule id>#<n>``, where n counts occurrences of that rule id
    across the run in recording order, so two findings on the same rule stay
    distinguishable. The key is positional by construction: re-recording a
    domain (record_domain_result with replace=True) after some of its issues
    were filed can shift which finding a key refers to.
    """

    key: str
    domain_id: str
    finding: Finding


def _run_issues(run: RunTracker) -> list[PendingIssue]:
    """Every recorded finding in this run, in recording order, keyed by
    finding identity rather than by rule id."""
    seen: Counter[str] = Counter()
    issues: list[PendingIssue] = []
    for domain_id, result in run.domain_results.items():
        for finding in result.findings:
            seen[finding.rule_id] += 1
            issues.append(
                PendingIssue(
                    key=f"{finding.rule_id}#{seen[finding.rule_id]}",
                    domain_id=domain_id,
                    finding=finding,
                )
            )
    return issues


def _pending_issues(run: RunTracker) -> list[PendingIssue]:
    """The findings in this run that have not been filed as issues yet."""
    return [issue for issue in _run_issues(run) if issue.key not in run.filed_issues]


def _rules_fetched_state(run: RunTracker, domain_id: str) -> bool | None:
    """Whether this run fetched ``domain_id``'s rule text: True, False, or None
    for a domain whose fetch status was never recorded.

    None is not a soft False. It is reachable only by resuming a run saved
    before fetches were recorded at all, and it means the question cannot be
    answered for that domain, in either direction.
    """
    if domain_id in run.rules_fetched:
        return True
    if domain_id in run.rules_fetch_unknown:
        return None
    return False


def _rules_fetch_warning(run: RunTracker, result: DomainResult) -> str | None:
    """The warning to hand back with a recorded result whose rules were never
    fetched, or whose fetch status is unrecorded. None when there is nothing
    to say.

    Only for a result that carries verdicts. A could-not-run result carries
    none by construction (see DomainResult), and telling an agent it reached
    verdicts without the rules when it reached no verdicts at all is noise that
    teaches it to ignore the field.
    """
    if not result.rule_verdicts:
        return None
    state = _rules_fetched_state(run, result.domain_id)
    if state is True:
        return None
    verdict_count = len(result.rule_verdicts)
    if state is None:
        return (
            f"Recorded {verdict_count} verdict(s) for domain '{result.domain_id}', which was "
            "carried in from a saved run that predates the record of which domains had their "
            "rule text fetched. Whether the rules were fetched for it is therefore not "
            "recorded, and the report will say exactly that rather than treating it as either "
            "answer. Tell the user."
        )
    return (
        f"Recorded {verdict_count} verdict(s) for domain '{result.domain_id}', but "
        f"get_domain('{result.domain_id}') was never called during this run, and it is the "
        "only thing that serves this pack's rule text. The result is recorded as given, and "
        "the report will name this domain as one that recorded verdicts without its rules "
        "being fetched. If you reached those verdicts without reading the rules, fetch them, "
        "redo the domain and re-record it with replace=True. Tell the user either way."
    )


def _rules_fetch_summary(run: RunTracker) -> dict[str, list[str]]:
    """The run's fetch record, split the three ways the report splits it.

    Handed back by render_report as well as written into the report, because
    the report is a file the agent hands over and the response is what the
    agent reads: a signal that lives only in the HTML is one the person driving
    the audit hears about only if they open it.
    """
    with_verdicts = sorted(
        domain_id
        for domain_id, result in run.domain_results.items()
        if result.rule_verdicts
    )
    return {
        "fetched_domain_ids": sorted(run.rules_fetched),
        "verdicts_without_rules_fetched_domain_ids": [
            domain_id
            for domain_id in with_verdicts
            if _rules_fetched_state(run, domain_id) is False
        ],
        "fetch_not_recorded_domain_ids": [
            domain_id
            for domain_id in with_verdicts
            if _rules_fetched_state(run, domain_id) is None
        ],
    }


def _progress_path(output_dir: Path) -> Path:
    """The crash-recovery file for a run whose output directory is this.

    It lives in output_dir and nowhere else, even when the configuration
    page (or a preset AuditConfig) sends the finished report.html and
    run-state.json to a different, user-chosen deliverables directory (issue
    #109): output_dir is the one path the agent already has to name and
    remember, so a resumed session finds the progress file by pointing
    begin_run at the same place it pointed the interrupted one, regardless of
    where that run's deliverables end up landing. A central per-user recovery
    directory would need its own naming, its own cleanup, and its own answer
    to "which of these is my run", none of which this needs.
    """
    return output_dir / PROGRESS_FILENAME


def _run_progress(run: RunTracker, *, completed: bool = False) -> RunProgress:
    """Snapshot the tracker as the record written to disk.

    schema_version is carried from the tracker rather than left to its field
    default: see RunTracker.schema_version for why a resumed run must keep
    declaring whatever version it was loaded at (issue #155).
    """
    return RunProgress(
        schema_version=run.schema_version,
        meta=run.meta,
        config=run.config,
        config_mode=run.config_mode,
        repo_dir=str(run.repo_dir) if run.repo_dir is not None else None,
        domain_results=run.domain_results,
        filed_issues=run.filed_issues,
        # Sorted, not in fetch order: this record is compared between runs and
        # diffed by hand, and set iteration order is not stable across
        # processes.
        rules_fetched_domain_ids=sorted(run.rules_fetched),
        rules_fetch_unknown_domain_ids=sorted(run.rules_fetch_unknown),
        # Copied, not handed over: the tracker keeps mutating after this
        # snapshot is written, and a shared dict would let a later domain
        # appear in a record that was saved before it was recorded.
        domain_rules_fetched_at=dict(run.domain_rules_fetched_at),
        domain_recorded_at=dict(run.domain_recorded_at),
        feedback_issue_url=run.feedback_issue_url,
        completed=completed,
    )


def _queue_persist_warning(run: RunTracker, warning: str) -> None:
    """Queue a warning for the next tool response, once per distinct fact."""
    if warning in run.persist_warnings_seen:
        return
    run.persist_warnings_seen.add(warning)
    run.persist_warnings.append(warning)


def _with_warnings(run: RunTracker, response: dict[str, Any]) -> dict[str, Any]:
    """Attach any queued warnings to a tool response and mark them as said.

    Draining here is what makes "once per fact" true rather than "once per
    call": the fact travels in exactly one response, the one that comes back
    first after it happened. The key is left off entirely when there is
    nothing to say, so a healthy run's response shape is unchanged.
    """
    if not run.persist_warnings:
        return response
    existing = response.get("warnings")
    response["warnings"] = [
        *(existing if isinstance(existing, list) else []),
        *run.persist_warnings,
    ]
    run.persist_warnings.clear()
    return response


def _persist_run(run: RunTracker, *, completed: bool = False) -> None:
    """Write the run's crash-recovery record, atomically.

    Never raises. The results are still intact in memory at this point, so
    failing the tool call would throw away work the user has already paid for
    to punish a disk that is only *maybe* going to matter. It is never a
    silent pass either: the failure is queued as a warning the next response
    carries, saying plainly that this run is no longer recoverable, which is
    the fact the person driving the audit needs in order to decide whether to
    keep going.

    The broad except is deliberate and this is its reason: every failure mode
    here (a full disk, a read-only directory, a serialisation bug) has the
    same correct response, and a crash-recovery mechanism that can itself
    crash the run it is protecting is worse than not having one.
    """
    path = _progress_path(run.output_dir)
    try:
        save_run_progress(path, _run_progress(run, completed=completed))
    except Exception as exc:
        _queue_persist_warning(
            run,
            f"Could not save this run's crash-recovery state to {path}: {exc}. The run itself "
            "is unaffected and its results are still held in memory, but if this server stops "
            "before render_report, they cannot be resumed and the audit has to be run again. "
            "Tell the user.",
        )


def _remove_progress_file(run: RunTracker) -> None:
    """Delete the crash-recovery file once the run's real output is written."""
    path = _progress_path(run.output_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _queue_persist_warning(
            run,
            f"Could not remove the crash-recovery file {path}: {exc}. It has already been "
            "marked completed, so it will not be offered as a resumable run; it can be "
            "deleted by hand.",
        )


def _find_prior_run(output_dir: Path) -> PriorRun | None:
    """The unfinished run persisted in this output directory, if any.

    None means there is genuinely nothing to resume: either no file, or a file
    describing a run that already rendered its report. A file that cannot be
    read comes back as a PriorRun with progress=None, never as None.
    """
    path = _progress_path(output_dir)
    if not path.is_file():
        return None
    try:
        progress = load_run_progress_file(path)
    except RunStateLoadError as exc:
        return PriorRun(path=path, progress=None, error=str(exc))
    if progress.completed:
        # render_report marks the record completed and then removes it. A
        # record that survived that (an unlink that failed, a file restored
        # from a backup) describes a finished run, and offering to resume it
        # would invite the agent to re-audit an audit that is already done.
        return None
    return PriorRun(path=path, progress=progress, error=None)


def _prior_run_summary(progress: RunProgress, path: Path) -> dict[str, Any]:
    """What the agent needs in order to describe a prior run to the user."""
    config = progress.config
    recorded = list(progress.domain_results)
    return {
        "path": str(path),
        "readable": True,
        "repo_name": progress.meta.repo_name,
        "repo_commit": progress.meta.repo_commit,
        "started": progress.meta.started,
        "assistant": progress.meta.assistant,
        "model": progress.meta.model,
        "configured": config is not None,
        "selected_domain_ids": config.selected_domain_ids
        if config is not None
        else None,
        "recorded_domain_ids": recorded,
        "missing_domain_ids": (
            [d for d in config.selected_domain_ids if d not in progress.domain_results]
            if config is not None
            else None
        ),
        "finding_count": sum(len(r.findings) for r in progress.domain_results.values()),
        "filed_issue_count": len(progress.filed_issues),
    }


def _resume_offer(prior: PriorRun, repo_name: str) -> dict[str, Any]:
    """The answer begin_run gives when it finds a prior run and was not told
    what to do about it: a description of what is there, and nothing done.

    Deliberately carries no "meta" key, unlike every started-run response: an
    agent that ignores this and reads the result as a run it just started gets
    a KeyError rather than a plausible-looking run that does not exist.
    """
    if prior.progress is None:
        return {
            "run_started": False,
            "resumable": False,
            "prior_run": {
                "path": str(prior.path),
                "readable": False,
                "error": prior.error,
            },
            "reason": (
                f"A previous unfinished audit run is saved at {prior.path}, but its state "
                f"cannot be read: {prior.error}"
            ),
            "instruction": (
                "Nothing has been started. Tell the user that a previous unfinished run was "
                "left in this output directory and its saved state cannot be read, quoting "
                "the error, and that its recorded results cannot be recovered. Do not start "
                "over on your own: ask them first, and only then call begin_run again with "
                "resume=False, which overwrites that file. Pointing output_dir somewhere "
                "else instead keeps the unreadable file for them to inspect."
            ),
        }

    summary = _prior_run_summary(prior.progress, prior.path)
    if prior.progress.meta.repo_name != repo_name:
        return {
            "run_started": False,
            "resumable": False,
            "prior_run": summary,
            "reason": (
                f"The unfinished run saved at {prior.path} audits repository "
                f"{prior.progress.meta.repo_name!r}, but this call is for "
                f"{repo_name!r}. Results from one repository are never continued into a run "
                "for another."
            ),
            "instruction": (
                "Nothing has been started. This is almost always the wrong output_dir: give "
                "begin_run an output_dir inside the repository you are auditing. If the user "
                "genuinely wants the other repository's unfinished run thrown away, call "
                "begin_run again with resume=False, which discards it."
            ),
        }

    return {
        "run_started": False,
        "resumable": True,
        "prior_run": summary,
        "instruction": (
            "Nothing has been started. Show the user this prior run (when it started, which "
            "domains it already covers, how many findings it holds) and ask whether to "
            "continue it. Call begin_run again with resume=True to continue it, keeping those "
            "results and auditing only missing_domain_ids, or resume=False to discard it and "
            "start a fresh run. Do not choose for them: resume=False permanently throws away "
            "audit work that has already been done."
        ),
    }


def _persist_stack_choice(
    run: RunTracker, deliverables_dir: Path, decision: dict[str, Any]
) -> None:
    """Write the stack choice decision to disk as a sibling to run-state.json.

    The decision is persisted to stack-choice.json so the user can revisit the
    decision later if the code changes. Never raises: failures are queued as
    warnings but do not interrupt the run, consistent with other persistence
    failures in this module (see _persist_run, _remove_progress_file).
    """
    stack_choice_path = deliverables_dir / "stack-choice.json"
    try:
        atomic_write_text(stack_choice_path, json.dumps(decision, indent=2))
    except Exception as exc:
        # Queue warning rather than print to stderr, for consistency with
        # _persist_run and other non-fatal persistence failures. The warning
        # travels in the render_report response and is visible to the caller.
        _queue_persist_warning(
            run,
            f"Could not write stack choice decision to {stack_choice_path}: {exc}. The run "
            "is unaffected and its results are still on disk, but the stack choice decision "
            "record will not be available. Tell the user.",
        )


def _require_run(state: AppState) -> RunTracker:
    if state.run is None:
        raise ValueError("No audit run in progress. Call begin_run first.")
    return state.run


def _feedback_target(state: AppState) -> tuple[RunTracker, FinishedRun | None]:
    """The run submit_feedback should send for: the run in progress, or
    else the last run this process finished.

    Only submit_feedback falls back to a finished run. Every other tool
    keeps requiring a live run, because recording results or filing issues
    against a run whose report is already written would silently produce
    a report that no longer matches its own run state.
    """
    if state.run is not None:
        return state.run, None
    if state.finished is not None:
        return state.finished.tracker, state.finished
    raise ValueError("No audit run in progress. Call begin_run first.")


def _require_config(run: RunTracker) -> AuditConfig:
    if run.config is None:
        raise ValueError(
            "This run has no configuration yet. Call start_config and get_config first."
        )
    return run.config


def _validate_selected_domains(state: AppState, config: AuditConfig) -> None:
    valid_ids = {d.id for d in state.pack.domains}
    unknown = sorted(set(config.selected_domain_ids) - valid_ids)
    if unknown:
        raise ValueError(
            f"selected_domain_ids includes id(s) not in the loaded rules pack: {unknown}. "
            f"Valid ids: {sorted(valid_ids)}"
        )


def _resume_run(
    state: AppState,
    prior: PriorRun,
    output_dir: Path,
    repo_dir: Path | None,
    repo_name: str,
    repo_commit: str,
    assistant: str,
    model: str,
) -> dict[str, Any]:
    """Rebuild the tracker from a persisted run and install it as the run in
    progress.

    Raises rather than resuming when the saved run cannot honestly be
    continued: an unreadable record, a different repository, or a
    configuration naming domains the currently loaded rules pack does not
    have. Mismatches that are merely suspicious rather than disqualifying (the
    repository has moved on a commit, the rules pack has) come back as
    warnings, because both are legitimate mid-audit and only the user can say
    whether they matter.
    """
    if prior.progress is None:
        raise ValueError(
            f"Cannot resume the run saved at {prior.path}: {prior.error}. Nothing was "
            "started and the file was not touched. Its recorded results cannot be "
            "recovered; call begin_run with resume=False to discard it and start fresh, "
            "or point output_dir elsewhere to keep it for inspection."
        )
    progress = prior.progress
    if progress.meta.repo_name != repo_name:
        raise ValueError(
            f"Refusing to resume: the run saved at {prior.path} audits repository "
            f"{progress.meta.repo_name!r}, but this call is for {repo_name!r}. Recorded "
            "results are attributed to the repository they were found in; continuing one "
            "repository's run as another's would publish findings against a repository "
            "they were never checked on. Check output_dir, or pass resume=False to discard "
            "the saved run and start fresh."
        )

    warnings: list[str] = []
    config = progress.config
    if config is not None:
        try:
            _validate_selected_domains(state, config)
        except ValueError as exc:
            raise ValueError(
                f"Cannot resume the run saved at {prior.path}: {exc} The rules pack loaded "
                "now does not define every domain that run selected, so the run cannot be "
                "completed against it. Point the server at the rules pack that run used, or "
                "start a fresh run with resume=False."
            ) from exc

    if progress.meta.repo_commit != repo_commit:
        warnings.append(
            f"The saved run was started against repo_commit {progress.meta.repo_commit}, and "
            f"this call gave {repo_commit}. The resumed run keeps the original commit, because "
            "that is what the already-recorded results were audited against; anything recorded "
            "from here on is against the newer working tree. Tell the user, and offer to start "
            "fresh (resume=False) if the repository has moved on materially."
        )
    resumed_meta = progress.meta
    if (progress.meta.assistant, progress.meta.model) != (assistant, model):
        # The saved pair describes whoever recorded the results already in the
        # file; this call's pair describes whoever will record the rest. Both
        # are true of the finished run, so keep both rather than choosing.
        #
        # Keeping only the saved pair was the original bug (#93): the caller
        # handed in the correct current values on every resume and they were
        # discarded without a word, so the report's provenance header credited
        # whichever assistant happened to start the run. Overwriting instead
        # would be the same bug pointing the other way, erasing the model that
        # produced the already-recorded findings.
        earlier = f"{progress.meta.assistant}/{progress.meta.model}"
        resumed_meta = progress.meta.model_copy(
            update={
                "assistant": assistant,
                "model": model,
                "earlier_contributors": [*progress.meta.earlier_contributors, earlier],
            }
        )
        warnings.append(
            f"The saved run was started by {earlier}, and this call is {assistant}/{model}. "
            "The report will name the current pair as the assistant and model, and list the "
            "earlier one as a previous contributor, because the recorded results were not all "
            "produced by the same one. Tell the user."
        )

    # subtree_only=True (#168): the pack directory may sit inside a larger
    # clone, and load_pack reads only this subtree, so dirt elsewhere in
    # that clone must not count. See _git_commit's docstring.
    current_pack_commit = _git_commit(state.pack.root, subtree_only=True)
    if progress.meta.rules_pack_commit != current_pack_commit:
        warnings.append(
            f"The saved run was started against rules pack commit "
            f"{progress.meta.rules_pack_commit}, and the pack loaded now is at "
            f"{current_pack_commit}. Its recorded results were reached against different rule "
            "text; the report will carry the original commit. Tell the user."
        )

    resolved_repo_dir = repo_dir
    if resolved_repo_dir is None and progress.repo_dir is not None:
        candidate = Path(progress.repo_dir)
        if candidate.is_dir():
            resolved_repo_dir = candidate
        else:
            warnings.append(
                f"The saved run recorded repo_dir {candidate}, which is no longer a directory. "
                "file_issues cannot detect the target repository from it; pass repo='owner/name' "
                "explicitly, or repo_dir to this call."
            )

    # Which domains had their rules fetched is a fact about the RUN, not about
    # this process, so it is restored from the record rather than started over:
    # an assistant that fetched d05 before the interruption and applies it after
    # is not asked to fetch it twice, and the report does not accuse it of
    # skipping the rules it read.
    #
    # A record written before this was tracked says nothing either way. Its
    # already-recorded domains go to the unknown list rather than being assumed
    # fetched (which would launder them clean) or assumed unfetched (which would
    # accuse a run that may well have done the work). Recording starts from here,
    # so any domain audited after the resume is judged on its own evidence.
    saved_fetched = progress.rules_fetched_domain_ids
    rules_fetched = set(saved_fetched or ())
    rules_fetch_unknown = set(progress.rules_fetch_unknown_domain_ids)
    if saved_fetched is None:
        rules_fetch_unknown |= set(progress.domain_results)
        if rules_fetch_unknown:
            warnings.append(
                "The saved run predates the record of which domains had their rule text "
                f"fetched, so for its {len(rules_fetch_unknown)} already-recorded domain(s) "
                "that is unknown, and the report will say so rather than reporting either "
                "answer. Domains audited from here on are recorded normally."
            )
    rules_fetch_unknown -= rules_fetched

    run = RunTracker(
        meta=resumed_meta,
        output_dir=output_dir,
        # See RunTracker.schema_version: this run's domain_results were just
        # loaded from progress, which may hold verdicts recorded under a
        # more lenient version of the not-applicable note rule. Carrying the
        # file's own declared version forward is what keeps the record
        # honest through however many more resumes this run takes (issue
        # #155); defaulting to the current version here, the way the fresh
        # RunProgress/RunState construction below already did before this
        # fix, is exactly the bug.
        schema_version=progress.schema_version,
        repo_dir=resolved_repo_dir,
        config=config,
        rules_fetched=rules_fetched,
        rules_fetch_unknown=rules_fetch_unknown,
        # Restored so a resumed run keeps what the saved record stamped and
        # adds only what it stamps itself. A saved file that predates these
        # maps restores as empty, which leaves its already-recorded domains
        # simply absent rather than stamped with the resume time (issue #205).
        domain_rules_fetched_at=dict(progress.domain_rules_fetched_at or {}),
        domain_recorded_at=dict(progress.domain_recorded_at or {}),
        # Only meaningful alongside a resolved config: a saved "interactive"
        # mode with no config describes a config page served by a process that
        # is gone, and restoring it would leave get_config waiting on a server
        # that no longer exists. Without it, start_config opens a fresh page,
        # which is the honest state of things.
        config_mode=progress.config_mode if config is not None else None,
        domain_results=dict(progress.domain_results),
        filed_issues=dict(progress.filed_issues),
        feedback_issue_url=progress.feedback_issue_url,
        resumed=True,
    )
    state.run = run
    # Resuming closes the previous run's late-feedback window, exactly as
    # starting a fresh one does: feedback from here belongs to this run.
    state.finished = None
    # Rewrite immediately, so any detail this call resolved differently (a new
    # repo_dir) is in the record before the next interruption, not only in
    # memory.
    _persist_run(run)

    recorded = list(run.domain_results)
    missing = (
        [d for d in config.selected_domain_ids if d not in run.domain_results]
        if config is not None
        else None
    )
    return _with_warnings(
        run,
        {
            "run_started": True,
            "resumed": True,
            "meta": run.meta.model_dump(mode="json"),
            "output_dir": str(output_dir),
            "repo_dir": str(resolved_repo_dir)
            if resolved_repo_dir is not None
            else None,
            "config": config.model_dump(mode="json") if config is not None else None,
            "selected_domain_ids": config.selected_domain_ids
            if config is not None
            else None,
            "recorded_domain_ids": recorded,
            "missing_domain_ids": missing,
            "filed_issues": dict(run.filed_issues),
            "warnings": warnings,
            "instruction": (
                "This run's configuration is already resolved: do not call start_config or "
                "get_config again. Audit only the domains in missing_domain_ids; the ones in "
                "recorded_domain_ids already have results and must not be re-audited unless "
                "the user asks. Tell the user which domains were recovered."
                if config is not None
                else (
                    "The saved run was interrupted before its configuration was chosen, so "
                    "there are no results to recover: call start_config next, as for a fresh "
                    "run."
                )
            ),
        },
    )


def _config_summary(run: RunTracker) -> dict[str, Any]:
    assert run.config is not None  # only called once config is set
    return {
        "mode": run.config_mode,
        "config": run.config.model_dump(mode="json"),
        "selected_domain_ids": run.config.selected_domain_ids,
    }


def _issue_preview(pending: list[PendingIssue], repo: str | None) -> dict[str, Any]:
    """The confirm=False answer: what would be filed, and nothing else.

    Deliberately touches neither gh nor the target repository, so previewing
    can never be the call that creates something on someone's repo.
    """
    return {
        "repo": repo,
        "count": len(pending),
        "titles": [issue.finding.issue_title for issue in pending],
        "instruction": (
            "Show this list of issue titles, and the target repository once known, to "
            "the user and ask for their explicit approval. Call file_issues again with "
            "confirm=True only after the user has explicitly agreed to file these on "
            "their repository."
        ),
    }


def _resolve_target_repo(run: RunTracker, repo: str | None) -> str:
    """The 'owner/name' repository to file this run's issues on.

    An explicit `repo` wins and is taken at face value. Otherwise it is
    detected from the audited repository directory recorded by begin_run,
    which needs a working gh. Every way of failing to work it out raises,
    naming what to do about it: filing on a guessed repository is worse than
    not filing.
    """
    if repo is not None:
        return repo
    if run.repo_dir is None:
        raise ValueError(
            "No repo_dir was recorded for this run (pass it to begin_run) and no repo "
            "argument was given to file_issues; cannot detect which GitHub repository "
            "to file issues on."
        )
    if not gh_available():
        raise ValueError(
            "gh is not available or not authenticated (gh auth status failed). Install "
            "and authenticate gh, or use issue_mode='report' instead."
        )
    detected = detect_repo(run.repo_dir)
    if detected is None:
        raise ValueError(
            f"Could not detect a GitHub repository from {run.repo_dir}: no GitHub "
            "remote found. Pass repo='owner/name' explicitly to file_issues."
        )
    return detected


def _file_pending_issues(
    run: RunTracker,
    target_repo: str,
    pending: list[PendingIssue],
    rule_index: dict[str, Rule],
) -> dict[str, Any]:
    """File one issue per pending finding, recording each url on the run as
    it goes.

    Stops at the first failure and raises, naming what was filed and what was
    not: the bookkeeping is updated per issue, so a retry resumes rather than
    re-filing. Returns the filed map, the run's full filed map, the label
    outcome and any warnings.
    """
    # Once per run, not once per issue: whether the label exists is one
    # fact about the target repository.
    label_status = ensure_label(target_repo)
    labels = [label_status.name] if label_status.usable else []

    filed_this_call: dict[str, str] = {}
    warnings: list[str] = [label_status.warning] if label_status.warning else []
    for issue in pending:
        finding = issue.finding
        # A filed issue is a published claim. It never goes out without
        # the evidence backing it: a finding whose rule is missing from
        # the pack or has no cited source stops filing loudly, before
        # anything is created on the target repository.
        rule = rule_index.get(finding.rule_id)
        if rule is None or not rule.source:
            problem = (
                "is not in the rules pack"
                if rule is None
                else "has no cited source in the rules pack"
            )
            raise ValueError(
                f"Refusing to file issue for finding on rule {finding.rule_id}: the rule "
                f"{problem}. A filed issue is a published claim; this tool does not "
                "publish claims without evidence. Nothing was filed for this finding. "
                f"Already filed before this stop: {run.filed_issues or 'none'}."
            )
        # issue.domain_id came from iterating run.domain_results in
        # _run_issues, so it is always a key of it here.
        domain_result = run.domain_results[issue.domain_id]
        confidence = (
            domain_result.self_assessment.confidence
            if domain_result.self_assessment is not None
            else None
        )
        rules_fetched = _rules_fetched_state(run, issue.domain_id)
        # Issue #211: the base the confidence claim rests on, so an issue
        # filed through this gh CLI path says the same thing about its
        # domain as the same finding's card and copy text in the report. A
        # could-not-run domain has no verdicts and so no denominator.
        unevaluated = (
            None
            if domain_result.status == "could-not-run"
            else (
                sum(
                    1
                    for rv in domain_result.rule_verdicts
                    if rv.verdict is Verdict.COULD_NOT_EVALUATE
                ),
                len(domain_result.rule_verdicts),
            )
        )
        trailing_line = build_issue_trailing_line(
            finding,
            rule,
            confidence=confidence,
            rules_fetched=rules_fetched,
            unevaluated=unevaluated,
        )
        # issue_title and issue_body are assistant-authored and untrusted,
        # same as body_md; stripped here for the same reason report.py's
        # issues section strips them (issue #128), so the two filing paths
        # (this gh CLI path, and the report's own copy/paste or PAT filing)
        # can never disagree about the same finding's text.
        issue_title = strip_markdown_emphasis(finding.issue_title)
        issue_body = strip_markdown_emphasis(finding.issue_body)
        body = f"{issue_body}\n\n{trailing_line}"
        try:
            created = create_issue(target_repo, issue_title, body, labels)
        except IssueFilingError as exc:
            unfiled = [p.key for p in pending if p.key not in run.filed_issues]
            raise ValueError(
                f"Filing stopped after {len(filed_this_call)} of {len(pending)} issue(s) "
                f"this call. Filed: {run.filed_issues}. Not filed: {unfiled}. Failure "
                f"filing finding '{issue.key}' on {target_repo}: {exc}"
            ) from exc
        run.filed_issues[issue.key] = created.url
        filed_this_call[issue.key] = created.url
        # Per issue, not once at the end: an issue that exists on the user's
        # repository but is missing from the saved state gets filed a second
        # time by a resumed run. Duplicate issues on someone else's repository
        # are the one failure here that cannot be undone from this side.
        _persist_run(run)
        # create_issue's own missing-label retry can still fire (a label
        # deleted or renamed mid-run), and it reports the same fact for
        # every issue after that. One line per distinct warning.
        for warning in created.warnings:
            if warning not in warnings:
                warnings.append(warning)

    return {
        "filed": filed_this_call,
        "all_filed_issue_urls": dict(run.filed_issues),
        "label": {"name": label_status.name, "state": label_status.state},
        "warnings": warnings,
    }


def _server_arg_parser() -> argparse.ArgumentParser:
    """The engineering-audit-mcp CLI's argument parser, shared so every
    resolver below recognises the full flag set. Two resolvers each parsing
    the same argv with a parser that only knows its own flag would make
    each choke on a flag the other defines, as argparse errors loudly
    (SystemExit code 2) on anything unrecognised rather than ignoring it.
    """
    parser = argparse.ArgumentParser(prog="engineering-audit-mcp", add_help=False)
    parser.add_argument("--rules-dir", default=None)
    parser.add_argument("--no-update-check", action="store_true", default=False)
    return parser


def _resolve_rules_dir(argv: list[str]) -> Path:
    """Resolve the rules pack directory from --rules-dir or the environment.

    Refuses to proceed (raises SystemExit with a clear message) if neither is
    set, or if the resolved path is not an existing directory: an audit tool
    that silently started with no rules pack would produce a report that
    looks like a clean audit while having checked nothing.
    """
    # argparse rather than a hand-rolled scan: a trailing '--rules-dir' with
    # no value must error loudly (SystemExit code 2), not silently fall
    # through to the environment variable, which could be a stale, wrong
    # pack.
    args = _server_arg_parser().parse_args(argv)
    rules_dir_value: str | None = args.rules_dir

    if rules_dir_value is None:
        rules_dir_value = os.environ.get("ENGINEERING_AUDIT_RULES_DIR")

    if not rules_dir_value:
        raise SystemExit(
            "engineering-audit-mcp: no rules pack directory given. Pass --rules-dir <path> "
            "or set the ENGINEERING_AUDIT_RULES_DIR environment variable."
        )

    rules_dir = Path(rules_dir_value).expanduser()
    if not rules_dir.is_dir():
        raise SystemExit(
            "engineering-audit-mcp: rules pack directory does not exist or is not a "
            f"directory: {rules_dir}"
        )
    return rules_dir


# The environment variable half of the update-check opt-out. Read by
# _update_check_enabled_from_env below, and only there: the resolved setting
# is carried explicitly on AppState (see AppState.update_check_enabled) and
# read from there by begin_run, so this constant no longer doubles as an
# internal message bus between main() and begin_run. It stays a supported
# input for anyone starting the server without going through main() (an
# embedder calling build_server directly), which is why build_server still
# falls back to reading it when its own caller does not resolve the setting
# itself.
_NO_UPDATE_CHECK_ENV_VAR = "ENGINEERING_AUDIT_NO_UPDATE_CHECK"


def _update_check_disabled_by_flag(argv: list[str]) -> bool:
    """Whether --no-update-check was passed on the command line.

    Does not look at ENGINEERING_AUDIT_NO_UPDATE_CHECK itself; main() is the
    one place that reconciles the flag and the environment variable, by
    passing build_server an explicit update_check_enabled value (see main()
    below), so nothing downstream has to check the flag and the environment
    variable separately.
    """
    return _server_arg_parser().parse_args(argv).no_update_check


def _update_check_enabled_from_env() -> bool:
    """Whether ENGINEERING_AUDIT_NO_UPDATE_CHECK, taken alone, leaves the
    update check enabled.

    Checked by value, not by presence: an empty string counts as unset, so
    a config-management tool that leaves the variable declared but blank
    does not silently disable the check. On by default, since the check
    exists to catch exactly the case where nobody is looking for a reason
    to turn it off (a stale, pinned install serving an old build forever).

    This is only the environment-variable input on its own; it does not know
    about --no-update-check. build_server calls this to resolve its default
    when the caller does not pass update_check_enabled explicitly; main()
    passes an explicit value instead, because it also has the flag to fold
    in.
    """
    return not os.environ.get(_NO_UPDATE_CHECK_ENV_VAR)


def _register_pack_tools(mcp: MCPServer, state: AppState) -> None:
    """Read-only inspection of the loaded rules pack."""

    @mcp.tool()
    def list_domains() -> dict[str, Any]:
        """List every domain loaded from the rules pack, and report any files
        in the pack directory that were skipped because they had no Trigger
        line."""
        return {
            "domains": [
                {
                    "id": domain.id,
                    "number": domain.number,
                    "slug": domain.slug,
                    "title": domain.title,
                    "trigger": domain.trigger,
                    "rule_count": len(domain.rules),
                }
                for domain in state.pack.domains
            ],
            "skipped_files": [
                {"path": str(skipped.path), "reason": skipped.reason}
                for skipped in state.pack.skipped
            ],
        }

    @mcp.tool()
    def get_domain(domain_id: str) -> str:
        """Return the full document text for one domain, given its id (e.g. 'd01').

        This tool serves the full rule text: it is meant for the local agent
        driving the audit, which needs the rules to apply them. Nothing else
        in this package returns rule body text.

        Because of that, this call is recorded against the run in progress: it
        is the one observable event that could have supplied the rules a
        verdict is meant to rest on. record_domain_result says so when verdicts
        arrive for a domain this was never called for, and the report names
        that domain. The claim either way is only ever that the text was
        fetched, never that it was read.

        A fetch made when no run is in progress belongs to no run and is not
        recorded: call begin_run first, then fetch each domain as you come to
        it.
        """
        domain = state.pack.get_domain(domain_id)
        if domain is None:
            valid_ids = (
                ", ".join(d.id for d in state.pack.domains) or "(no domains loaded)"
            )
            raise ValueError(f"Unknown domain id '{domain_id}'. Valid ids: {valid_ids}")
        run = state.run
        if run is not None:
            # setdefault, not assignment: this names the FIRST time the rules
            # were served for this domain, and a re-fetch later in the same
            # run does not move it. On a resume the saved stamp is restored
            # before this runs, so a re-fetch after resuming keeps the
            # original rather than rewriting history.
            run.domain_rules_fetched_at.setdefault(domain_id, _now_utc_iso())
        if run is not None and domain_id not in run.rules_fetched:
            run.rules_fetched.add(domain_id)
            # Positive evidence beats an absence of it: a domain carried in
            # from a record that never tracked fetching is unknown right up
            # until it is fetched here, and then it is simply fetched.
            run.rules_fetch_unknown.discard(domain_id)
            # Persisted now rather than at the next record_domain_result: a
            # server killed between the fetch and the verdict must not come
            # back reporting that the rules for this domain were never
            # requested. Only on the first fetch of each domain, so a re-read
            # costs nothing.
            _persist_run(run)
        return get_domain_text(domain)


def _register_run_tools(mcp: MCPServer, state: AppState) -> None:
    """Run lifecycle: starting a run and stamping its provenance."""

    @mcp.tool()
    def begin_run(
        assistant: str,
        model: str,
        repo_name: str,
        repo_commit: str,
        started: str,
        output_dir: str,
        tool_version: str | None = None,
        environment: dict[str, str] | None = None,
        repo_dir: str | None = None,
        replace: bool = False,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        """Start a fresh audit run and create its output directory, or resume
        an interrupted one.

        assistant/model/repo_name/repo_commit/started are supplied by the
        calling agent; tool_version defaults to the installed package version
        if omitted. repo_dir is
        the path to the repository being audited, on disk; it is optional,
        but file_issues needs it to detect the GitHub repository to file
        against, unless a repo is given explicitly on that call instead.
        Calling this twice without finishing the first run (via
        render_report) is an error, since it would silently discard whatever
        domain results have already been recorded; pass replace=True to
        explicitly discard the in-progress run and start over.

        A run's progress is saved to a crash-recovery file in output_dir as it
        goes, so a server that stops mid-run (host restart, dropped
        connection, machine asleep) loses at most the domain in flight. When
        this call finds such a file for an unfinished run in output_dir it
        starts nothing and returns a description of it plus an instruction:
        run_started is False, "meta" is absent, and "resumable" says whether
        it can be continued at all. Call begin_run again with resume=True to
        continue that run (its recorded domains are kept, and the response
        lists which domains are still missing), or resume=False to discard it
        and start fresh. resume=False is the only way to overwrite saved
        results, and replace=True counts as the same explicit decision.
        Resuming a run for a DIFFERENT repository is refused outright, as is
        resuming one whose saved state cannot be read; either way, nothing is
        started and nothing is deleted until told.

        environment records the host facts the report header cannot carry, and
        its keys are a closed set: 'os' (e.g. "macOS 15.2", "Ubuntu 24.04"),
        'host_cli' (the CLI application driving this audit, e.g. "codex",
        "claude-code") and 'host_cli_version' (that CLI's version string).
        Collect them from the machine you are running on rather than guessing,
        and omit any key you cannot determine: an omitted fact and a guessed
        one are not the same thing. Any other key is refused outright, because
        this metadata is included in feedback issues filed publicly on the
        tool's own repository. Do not name the assistant, the model or the tool
        version here; all three are already fixed rows in the report header.

        The recorded metadata also stamps two provenance SHAs, best-effort:
        tool_commit (the git commit the installed tool build was made from,
        via its PEP 610 install record) and rules_pack_commit (the loaded
        rules pack directory's git HEAD, '-dirty' suffixed if it has
        uncommitted changes). Either is None when it could not be
        determined, which the report renders as "unknown" rather than
        guessing: a report must be traceable to the exact tool build and
        rules version that produced it, not just a package version number
        that can lag behind either.

        started is the caller's own claim about when the run began, taken on
        trust like everything else the calling agent asserts. This call also
        stamps meta.server_started from the server's own clock at the moment
        it runs, independent of that claim; render_report does the same for
        meta.server_finished. Neither figure is treated as more authoritative
        than the other in the rendered report: a resumed run genuinely spans a
        wall-clock gap that is not audit work, so the server's elapsed time is
        not automatically the truer duration, but an assistant-supplied
        duration that was never checked against anything is worse. The report
        states both and flags it when they diverge by more than expected,
        rather than presenting an unmeasured number as fact.

        The run also performs a best-effort tool update check, comparing
        tool_commit against the tool's latest tagged release on GitHub, and
        the rules pack against its own remote the same way. Each result
        lands in the returned meta (update_check and pack_update_check
        respectively), prefixed "current", "stale", "could-not-check" or
        "not-checked" (see engineering_audit.update_check for the exact
        strings). The calling agent MUST tell the user when either reports
        stale or could-not-check, rather than silently proceeding as if the
        installed build were confirmed current: this tool is installed via a
        pinned uvx reference, and a stale pin or cache would otherwise serve
        an old build forever with nothing to say so. This check runs
        automatically and discloses only the caller's IP address and the
        fact that this repository's tags were queried, no repository
        content, findings or paths; it can be turned off with
        --no-update-check or the ENGINEERING_AUDIT_NO_UPDATE_CHECK
        environment variable, in which case both fields read "not-checked",
        which is not something to warn the user about, since turning it off
        was their own choice.

        When the loaded rules pack declares itself a subset of a larger
        pack (its pack.toml carries an 'edition' key; issue #255), the
        response carries a rules_pack_notice naming the edition and, when
        declared, where the full pack can be requested. Relay it to the
        user once, when telling them the run has started, and never again.
        Absent means the pack made no such claim.
        """
        # First, before any branch can return early: an environment the tool
        # will not accept is a bad call, and the caller has to hear that
        # whether this turns into a fresh run, a resume offer or nothing at
        # all. Validating here rather than on RunMeta is deliberate; see
        # validate_environment and RunMeta.environment for why the model
        # itself stays permissive.
        validate_environment(environment)

        if state.run is not None and not replace:
            raise ValueError(
                f"A run is already in progress (started at {state.run.meta.started}, "
                f"repo {state.run.meta.repo_name}). Finish it with render_report, or "
                "pass replace=True to discard it and start a new run."
            )

        output_dir_path = Path(output_dir).expanduser()
        repo_dir_path: Path | None = None
        if repo_dir is not None:
            repo_dir_path = Path(repo_dir).expanduser()
            if not repo_dir_path.is_dir():
                raise ValueError(
                    f"repo_dir '{repo_dir}' does not exist or is not a directory."
                )

        # Crash recovery, before anything is created or discarded. An
        # unfinished run saved here is work someone has already paid for, so
        # it is never written over without being offered first. replace=True
        # is already an explicit "throw away the run in progress and start
        # over", so it counts as declining rather than raising a second
        # question the caller has already answered.
        prior = _find_prior_run(output_dir_path)
        decision = resume if resume is not None else (False if replace else None)
        if resume is True and prior is None:
            raise ValueError(
                f"resume=True, but there is no unfinished run saved in {output_dir_path} "
                f"(no {PROGRESS_FILENAME} there). Nothing was started. Check that output_dir "
                "is the same directory the interrupted run used, or call begin_run with "
                "resume=False to start a fresh run there."
            )
        if prior is not None and decision is None:
            return _resume_offer(prior, repo_name)

        if state.run is not None and state.run.config_server is not None:
            # Discarding an in-progress run must not leak its interactive
            # config page's HTTP server: a second one would bind a different
            # port and leave the first orphaned but still listening.
            state.run.config_server.shutdown()

        if decision is True:
            assert prior is not None  # resume=True with no prior run raised above
            return _resume_run(
                state,
                prior,
                output_dir_path,
                repo_dir_path,
                repo_name,
                repo_commit,
                assistant,
                model,
            )

        tool_version_value = tool_version or _default_tool_version()
        tool_commit_value = _default_tool_commit()
        pack_version_value = _git_release_version(state.pack.root)
        # subtree_only=True (#168): see the comment on the resume path above,
        # and _git_commit's docstring, for why the pack and the tool are
        # scoped differently.
        pack_commit_value = _git_commit(state.pack.root, subtree_only=True)
        # Read once here, like rules_pack_version/rules_pack_commit above, and
        # carried through RunMeta rather than re-read at render time (issue
        # #170): a saved run-state.json must reproduce the same compatibility
        # notice later even if the pack directory has since moved on or gone.
        pack_metadata = read_pack_metadata(state.pack.root)
        update_check_enabled = state.update_check_enabled
        meta = RunMeta(
            tool_version=tool_version_value,
            tool_commit=tool_commit_value,
            rules_pack_name=state.pack.root.name,
            rules_pack_version=pack_version_value,
            rules_pack_commit=pack_commit_value,
            rules_pack_format=pack_metadata.format if pack_metadata else None,
            rules_pack_requires_tool=(
                pack_metadata.requires_tool if pack_metadata else None
            ),
            rules_pack_edition=pack_metadata.edition if pack_metadata else None,
            rules_pack_full_pack_url=(
                pack_metadata.full_pack_url if pack_metadata else None
            ),
            update_check=check_for_update(
                tool_commit_value,
                tool_version_value,
                enabled=update_check_enabled,
                # Issue #219: the host decides what the fix command is, and
                # begin_run is already told which host it is. Absent or
                # unrecognised falls back to a documentation pointer rather
                # than a guessed command.
                host_cli=(environment or {}).get("host_cli"),
            ),
            pack_update_check=check_pack_for_update(
                str(state.pack.root),
                pack_commit_value,
                pack_version_value,
                enabled=update_check_enabled,
            ),
            assistant=assistant,
            model=model,
            repo_name=repo_name,
            repo_commit=repo_commit,
            started=started,
            server_started=_now_utc_iso(),
            environment=environment,
        )
        output_dir_path.mkdir(parents=True, exist_ok=True)

        run = RunTracker(meta=meta, output_dir=output_dir_path, repo_dir=repo_dir_path)
        state.run = run
        # A new run closes the previous one's late-feedback window: feedback
        # sent from here on belongs to this run, never to the last one.
        state.finished = None
        # First save, before a single domain is audited: an interruption
        # between here and the first result still leaves a record saying a run
        # was started and never finished, rather than nothing at all.
        _persist_run(run)
        response: dict[str, Any] = {
            "run_started": True,
            "resumed": False,
            "meta": meta.model_dump(mode="json"),
            "output_dir": str(output_dir_path),
            "repo_dir": str(repo_dir_path) if repo_dir_path else None,
        }
        pack_edition_notice = _pack_edition_notice(meta)
        if pack_edition_notice is not None:
            response["rules_pack_notice"] = pack_edition_notice
        staleness_instruction = _staleness_instruction(meta)
        if staleness_instruction is not None:
            response["instruction"] = staleness_instruction
            # A trace independent of the agent (issue #254, same reasoning
            # as start_config's line for #246): stderr, never stdout, which
            # carries the MCP protocol.
            for line in _stale_statuses(meta):
                print(f"engineering-audit: {line}", file=sys.stderr)
        if prior is not None:
            # Saying what was thrown away is the difference between a discard
            # the user chose and one they will only find out about later.
            response["discarded_prior_run"] = (
                _prior_run_summary(prior.progress, prior.path)
                if prior.progress is not None
                else {"path": str(prior.path), "readable": False, "error": prior.error}
            )
        return _with_warnings(run, response)


def _pack_edition_notice(meta: RunMeta) -> str | None:
    """One factual line for the agent to relay when the loaded pack declares
    itself a subset of a larger one (issue #255), or None when it makes no
    such claim.

    Fires only on the pack's own pack.toml declaration, never on anything
    the tool inferred: domain count in particular is not evidence of a
    partial install, because a small custom pack is a complete pack. The
    relay ask is embedded in the notice itself, the same structural choice
    as _config_page_instruction above: whether the user learns the full
    pack exists must not depend on the agent deciding a metadata field was
    worth mentioning.
    """
    if not meta.rules_pack_edition:
        return None
    notice = (
        f"This run uses the '{meta.rules_pack_edition}' rules pack, as declared "
        "by the pack itself."
    )
    if meta.rules_pack_full_pack_url:
        notice += (
            f" The full pack is available on request: {meta.rules_pack_full_pack_url}"
            " If the user already has the full pack on disk, this registration "
            "still points at the subset: re-register with --rules-dir aimed at "
            "the full pack's domains/ directory (the README's Rules access "
            "section has the command). The tool never searches the disk for a "
            "fuller pack itself, so re-pointing is the only way to switch."
        )
    return (
        f"{notice} Mention this to the user once, when telling them the run has "
        "started; do not repeat it on later calls."
    )


def _stale_statuses(meta: RunMeta) -> list[str]:
    """The labelled status line for each staleness check that positively
    confirmed a newer release (issue #254). Empty for everything else:
    "could-not-check" and "not-checked" must never nag as if stale, because
    nothing was established (see update_check.py's own discipline), and
    "current" needs no line at all.
    """
    lines = []
    if (meta.update_check or "").startswith("stale"):
        lines.append(f"Tool: {meta.update_check}")
    if (meta.pack_update_check or "").startswith("stale"):
        lines.append(f"Rules pack: {meta.pack_update_check}")
    return lines


def _staleness_instruction(meta: RunMeta) -> str | None:
    """The instruction accompanying begin_run's response when a staleness
    check confirmed a newer release (issue #254), or None when none did.

    The status strings already carry the per-host remedy command (issue
    #219), so quoting them verbatim puts the fix one paste away. Until now
    the ask to relay them lived only in begin_run's docstring, which is the
    agent-goodwill channel #246 replaced for the config page URL; this is
    the same replacement for staleness. The tool never updates itself: the
    user chose the pin, and the remedy is theirs to run or ignore.
    """
    lines = _stale_statuses(meta)
    if not lines:
        return None
    return (
        "Before doing anything else, tell the user their install is behind the "
        "latest release, quoting the following verbatim, remedy included: "
        + " ".join(lines)
        + " Then carry on with the run: a stale build still audits; what it "
        "cannot do is claim its findings came from the current release."
    )


def _config_page_instruction(url: str, opened_in_browser: bool | None) -> str:
    """The instruction accompanying every interactive start_config response
    (issue #246).

    The whole run blocks on the configuration page, so a user who does not
    know it opened sees only a hung audit until the get_config wait times
    out. Whether they hear about the page used to depend entirely on the
    driving agent choosing to relay it; in a real run it chose not to, and
    the user found the page by accident. Carrying the instruction in the
    response makes the signal structural, the same way _issue_preview and
    get_config already instruct rather than hope.

    ``opened_in_browser`` is None on the already-started path, where whether
    a tab opened on the earlier call is no longer known.
    """
    if opened_in_browser is True:
        situation = (
            "A configuration page has just opened in a tab in the user's browser. "
            "Before doing anything else, tell the user that"
        )
    elif opened_in_browser is False:
        situation = (
            "No browser tab could be opened from here. Before doing anything else, "
            "ask the user to open the configuration page themselves"
        )
    else:
        situation = "The configuration page for this run is already up. Remind the user"
    return (
        f"{situation}, and show this URL on its own line so it renders as a "
        f"clickable link: {url} . The audit now waits on that form; a user who "
        "does not know the page exists sees only a hung run."
    )


def _register_config_tools(mcp: MCPServer, state: AppState) -> None:
    """Resolving the run's configuration, interactively or from a preset."""

    @mcp.tool()
    def start_config() -> dict[str, Any]:
        """Begin configuring the audit run.

        If the ENGINEERING_AUDIT_CONFIG environment variable names a path to
        a valid AuditConfig JSON file, it is loaded immediately (the
        documented headless/CI path); an invalid or unreadable file is a
        loud error, never a silently-applied default. Otherwise this starts
        the interactive localhost configuration page, opens it in the user's
        browser when one is available (best-effort; the response's
        opened_in_browser field says whether a tab actually opened), and
        returns its URL for the agent to show the user as the fallback.
        """
        run = _require_run(state)

        if run.config is not None:
            return _config_summary(run)
        if run.config_server is not None:
            # Already started in interactive mode: return the existing URL
            # rather than starting a second server on a different port.
            existing_url = run.config_url
            # Set in the same block as config_server below; one without the
            # other would be a bug in this function, not a reachable state.
            assert existing_url is not None
            return {
                "mode": "interactive",
                "url": existing_url,
                "instruction": _config_page_instruction(existing_url, None),
            }

        preset_path = os.environ.get("ENGINEERING_AUDIT_CONFIG")
        if preset_path:
            path = Path(preset_path).expanduser()
            if not path.is_file():
                raise ValueError(
                    f"ENGINEERING_AUDIT_CONFIG is set to '{preset_path}', which is not a file. "
                    "Fix the path, or unset ENGINEERING_AUDIT_CONFIG to use the interactive "
                    "configuration page instead."
                )
            try:
                raw_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"could not read ENGINEERING_AUDIT_CONFIG file '{path}': {exc}"
                ) from exc
            try:
                config = AuditConfig.model_validate_json(raw_text)
            except ValidationError as exc:
                raise ValueError(
                    f"ENGINEERING_AUDIT_CONFIG file '{path}' is not a valid AuditConfig: {exc}"
                ) from exc
            _validate_selected_domains(state, config)
            if config.deliverables_dir is not None:
                # The interactive page validates a custom path before it
                # ever reaches AuditConfig (see config_page.py's
                # _parse_submission); a preset file skips that page
                # entirely, so the same checks (parent exists, is
                # writable, no report already sitting there) run again
                # here. "The page is not the only possible caller" is
                # exactly this path (issue #109).
                try:
                    resolved = resolve_deliverables_dir(config.deliverables_dir)
                except UnresolvableOutputLocation as exc:
                    # Same clean-error treatment as any other unusable
                    # deliverables_dir here: an unknown ~user must reach the
                    # assistant as something it can act on, not the bare
                    # RuntimeError Path.expanduser()/resolve() themselves
                    # raise (issue #152).
                    raise ValueError(
                        f"ENGINEERING_AUDIT_CONFIG file '{path}' names a deliverables_dir "
                        f"that cannot be used: {exc}"
                    ) from exc
                path_error = validate_deliverables_dir(resolved)
                if path_error:
                    raise ValueError(
                        f"ENGINEERING_AUDIT_CONFIG file '{path}' names a deliverables_dir "
                        f"that cannot be used: {path_error}"
                    )
                config = config.model_copy(update={"deliverables_dir": str(resolved)})
            run.config = config
            run.config_mode = "preset"
            _persist_run(run)
            return _with_warnings(run, _config_summary(run))

        config_server = ConfigServer(
            state.pack.domains,
            output_dir=run.output_dir,
            gitignore_warning=_output_dir_ignore_warning(run.repo_dir, run.output_dir),
        )
        url = config_server.start()
        run.config_server = config_server
        run.config_mode = "interactive"
        run.config_url = url
        # The waiting clock starts when the page starts, not when get_config is
        # first called: what the deadline is about is how long the person has
        # had the page in front of them, not how promptly the assistant got
        # round to polling.
        run.config_wait_started_at = time.monotonic()
        # Best-effort convenience, never load-bearing: the URL in the response
        # stays the contract, because a remote or display-less session has no
        # browser to open and must still work. The swallow is safe precisely
        # because the result records whether a tab actually opened, so the
        # agent can say "check your browser" or "open this URL" and never the
        # wrong one. This only runs on the interactive path; the preset
        # (headless) path returned above and never touches a browser.
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error:
            opened = False
        # A log trace that exists independent of the agent (issue #246):
        # stderr, never stdout, which carries the MCP protocol.
        print(
            f"engineering-audit: configuration page at {url} "
            f"(browser tab opened: {'yes' if opened else 'no'})",
            file=sys.stderr,
        )
        return {
            "mode": "interactive",
            "url": url,
            "opened_in_browser": opened,
            "instruction": _config_page_instruction(url, opened),
        }

    @mcp.tool()
    def get_config(timeout_s: float = 300) -> dict[str, Any]:
        """Fetch the resolved audit configuration, or report that the user has
        not submitted the configuration page yet.

        Requires start_config to have been called first. Every response carries
        a "status" field, and it is the only field worth branching on:

        - "configured": the configuration is resolved and is in the response's
          "config" and "selected_domain_ids". Stop calling this tool.
        - "waiting": the interactive page is up and nobody has submitted it
          yet. This is NOT a failure and NOT a configuration. Tell the user the
          audit is waiting on them at the "url" in the response, then CALL THIS
          TOOL AGAIN. Keep calling it while the status says "waiting".
        - a raised error: the run's overall deadline (timeout_s) elapsed with
          no submission. Tell the user the audit is not proceeding. Never fall
          back to a domain selection nobody chose.

        In preset mode the configuration is already known and comes back as
        "configured" on the first call.

        This tool deliberately blocks for at most a short interval per call
        (about 25 seconds) and then returns "waiting", rather than holding one
        call open for the whole of timeout_s. Hosts impose their own per-tool
        timeouts, independent of timeout_s (Codex has
        mcp_servers.<name>.tool_timeout_sec), and a call held open past one of
        those is cancelled by the host, which can take the whole MCP process
        and this run's configuration page down with it (issue #85). timeout_s
        remains
        the run's overall waiting budget and is enforced here, cumulatively,
        across however many calls it takes: it is measured from the moment the
        page opened, so polling more often does not buy the user more time, and
        polling less often does not cost them any. To keep waiting past the
        deadline, call again with a larger timeout_s; that is an explicit
        decision to extend, not a silent one.
        """
        run = _require_run(state)
        if run.config_mode is None:
            raise ValueError("start_config must be called before get_config.")

        if run.config is not None:
            return {"status": "configured", **_config_summary(run)}

        assert (
            run.config_server is not None
        )  # config_mode == "interactive" implies this
        assert run.config_wait_started_at is not None  # set alongside config_server

        waited_s = time.monotonic() - run.config_wait_started_at
        remaining_s = timeout_s - waited_s
        # Never block longer than the caller's remaining budget, and never
        # longer than one poll interval whatever that budget is. A remaining
        # budget at or below zero still gets a zero-length wait rather than a
        # straight-to-timeout return: the user may have submitted the page in
        # the moment between the last poll and this call, and a submission
        # already sitting there must be picked up, not thrown away on a
        # technicality.
        block_s = max(0.0, min(_CONFIG_POLL_INTERVAL_S, remaining_s))
        try:
            config = run.config_server.wait(block_s)
        except ConfigTimeoutError as exc:
            waited_s = time.monotonic() - run.config_wait_started_at
            if waited_s >= timeout_s:
                raise ValueError(
                    f"No configuration submitted within {timeout_s} seconds. The user has "
                    f"not submitted the configuration page at {run.config_url}. Tell the "
                    "user the audit is waiting on them there and is not proceeding. Do not "
                    "proceed with a default configuration: call get_config again with a "
                    "larger timeout_s if they still intend to submit it."
                ) from exc
            return {
                "status": "waiting",
                "mode": "interactive",
                "url": run.config_url,
                "waited_s": round(waited_s, 1),
                "timeout_s": timeout_s,
                "remaining_s": round(max(0.0, timeout_s - waited_s), 1),
                "instruction": (
                    "Nobody has submitted the configuration page yet. This is not a "
                    "configuration and not an error. Tell the user the audit is waiting "
                    f"on them at {run.config_url}, then call get_config again. Keep "
                    "calling it while status is 'waiting'. Do not start auditing any "
                    "domain until status is 'configured'."
                ),
            }

        _validate_selected_domains(state, config)
        run.config = config
        run.config_server.shutdown()
        # Saved as soon as it is resolved: the configuration is the one step
        # of a run that costs a person's time rather than the agent's, and an
        # interruption before the first domain must not make them fill the
        # page in again.
        _persist_run(run)
        return _with_warnings(run, {"status": "configured", **_config_summary(run)})


def _register_result_tools(mcp: MCPServer, state: AppState) -> None:
    """Recording per-domain results, and reporting progress over them."""

    @mcp.tool()
    def record_domain_result(
        result: DomainResult, replace: bool = False
    ) -> dict[str, Any]:
        """Record the audit result for one domain.

        The payload itself is pydantic-validated by DomainResult (finding and
        verdict consistency, could-not-run reason, could-not-evaluate and
        not-applicable notes, both of which are the verdict's stated reason,
        every finding's precondition, the completed domain's
        uninspected_evidence, and that every consulted_sources entry has a
        non-blank url, title and why). On top of that: the domain must be one of the domains
        selected for this run, a completed result must carry a verdict for
        every rule the domain defines, and every consulted_sources rule_id
        must be one of this domain's own rules; a completed result missing a
        verdict raises IncompleteResultError listing exactly which rule ids
        are missing, and an unattributable consulted source raises
        UnknownRuleIdError, so the agent can fix and resubmit rather than a
        skipped rule silently passing or a citation silently pointing at
        nothing. Re-recording an already-recorded domain requires
        replace=True, to guard against an accidental overwrite.

        Verdicts for a domain get_domain was never called for during this run
        are recorded, not refused, and the response says "rules_fetched": false
        and carries a warning naming what that means. The report names the
        domain too. Recording rather than refusing is deliberate: refusing
        would be trivially satisfied by fetching the text and ignoring it,
        which destroys the signal, while the verdicts and the fact that they
        were unsupported both survive this way. Tell the user when you see it.

        Two fields are refused outright rather than recorded with a warning,
        because unlike an unfetched domain there is no signal to preserve by
        letting them through: a finding without a `precondition` (issue #178)
        and a completed domain without `uninspected_evidence` (issue #179).
        Both are one sentence the auditor already knows the answer to, and in
        both cases being unable to write it is the finding. A finding whose
        precondition cannot be named belongs at not-applicable, and a domain
        that cannot say what it did not read has not established what its
        absence claims are worth. See AUDIT.md step 3 and step 4.
        """
        run = _require_run(state)
        config = _require_config(run)

        domain_id = result.domain_id
        if domain_id not in config.selected_domain_ids:
            raise ValueError(
                f"domain '{domain_id}' is not one of the domains selected for this run: "
                f"{config.selected_domain_ids}"
            )

        if result.status == "completed":
            domain = state.pack.get_domain(domain_id)
            if domain is None:
                raise ValueError(
                    f"domain '{domain_id}' is not in the loaded rules pack"
                )
            validate_completeness(domain, result)

        if result.consulted_sources:
            # Checked regardless of status: a source consulted while
            # deciding a domain could not run at all is still attributed to
            # a rule in this domain, not to whatever the "completed" branch
            # above already fetched.
            domain = state.pack.get_domain(domain_id)
            if domain is None:
                raise ValueError(
                    f"domain '{domain_id}' is not in the loaded rules pack"
                )
            validate_consulted_sources(domain, result)

        # Issue #216. Checked regardless of status, and at the record
        # boundary rather than on Finding itself: the model is also what a
        # stored run-state.json is read through, so enforcing this there
        # would refuse every run already on disk that carries such a
        # location. Strict on write, readable forever.
        validate_finding_locations(result)

        if domain_id in run.domain_results and not replace:
            raise ValueError(
                f"domain '{domain_id}' already has a recorded result; pass replace=True to "
                "overwrite it."
            )

        run.domain_results[domain_id] = result
        # Plain assignment, not setdefault: unlike the fetch stamp this names
        # when the result currently held was accepted, so a replace=True
        # re-record legitimately moves it (issue #205).
        run.domain_recorded_at[domain_id] = _now_utc_iso()
        # Saved before the response goes back, so a server that dies between
        # this domain and the next one loses nothing that has been reported as
        # recorded. A save failure is reported in warnings, never raised: the
        # result is accepted either way.
        _persist_run(run)
        response: dict[str, Any] = {
            "domain_id": domain_id,
            "status": result.status,
            "finding_count": len(result.findings),
            "rules_fetched": _rules_fetched_state(run, domain_id),
        }
        warning = _rules_fetch_warning(run, result)
        if warning is not None:
            response["warnings"] = [warning]
        return _with_warnings(run, response)

    @mcp.tool()
    def run_status() -> dict[str, Any]:
        """Report progress for the current run: which selected domains have
        recorded results, which are still missing, and the findings count so
        far. Read-only over the run itself; it also carries any queued
        crash-recovery warning that no earlier response has reported yet."""
        run = _require_run(state)
        config = _require_config(run)

        selected = config.selected_domain_ids
        recorded = [d for d in selected if d in run.domain_results]
        missing = [d for d in selected if d not in run.domain_results]
        could_not_run = [
            d
            for d in selected
            if d in run.domain_results
            and run.domain_results[d].status == "could-not-run"
        ]
        finding_count = sum(len(r.findings) for r in run.domain_results.values())
        return _with_warnings(
            run,
            {
                "selected_domain_ids": selected,
                "recorded_domain_ids": recorded,
                "missing_domain_ids": missing,
                "could_not_run_domain_ids": could_not_run,
                "finding_count": finding_count,
            },
        )


def _register_issue_tools(mcp: MCPServer, state: AppState) -> None:
    """GitHub issue filing for recorded findings, via the user's own gh."""

    @mcp.tool()
    def file_issues(confirm: bool = False, repo: str | None = None) -> dict[str, Any]:
        """Preview or file GitHub issues for every recorded finding, via the
        user's own `gh` CLI.

        Requires config.issue_mode == "github": if the user chose in-report
        delivery instead, this raises rather than filing issues nobody asked
        for. Requires at least one recorded domain result.

        confirm=False (the default) NEVER files anything and never invokes
        gh at all: it returns a preview {repo, count, titles, instruction}
        so the calling agent can show the user exactly what is about to be
        filed on their repository, and get explicit agreement, before a
        single issue goes out. Filing on someone's repo is outward-facing;
        this confirmation step is mandatory, not decorative.

        confirm=True files one issue per finding that has not already been
        filed, so retrying after a partial failure does not double-file the
        ones that succeeded. Filed issues are tracked, and returned, per
        finding under a key of the form "<rule id>#<n>" (n counting that
        rule's findings in recording order), not per rule id: a domain result
        may carry two findings for the same rule, and both of their issue
        urls have to survive. The target repository is `repo` if given,
        otherwise detected from the audited repository directory recorded
        by begin_run's repo_dir. If any issue fails to file, filing stops
        immediately and the error lists exactly which findings were filed
        (with their URLs) and which were not, so a retry knows where to
        resume.

        Each filed issue carries the "engineering-audit" label. The label is
        checked once per call and created on the target repository if it is
        missing; the response's label field reports which of present,
        created or unavailable happened. Unavailable (creation failed) files
        the issues unlabelled and says so once, in warnings, rather than
        once per issue.
        """
        run = _require_run(state)
        config = _require_config(run)

        if config.issue_mode != "github":
            raise ValueError(
                f"This run's issue_mode is {config.issue_mode!r}, not 'github': the user chose "
                "in-report delivery, not GitHub issue filing. Not filing anything."
            )
        if not run.domain_results:
            raise ValueError(
                "No domain results recorded yet. Call record_domain_result for at least one "
                "domain before filing issues."
            )

        pending = _pending_issues(run)
        if not confirm:
            return _with_warnings(run, _issue_preview(pending, repo))

        target_repo = _resolve_target_repo(run, repo)
        return _with_warnings(
            run,
            {
                "repo": target_repo,
                **_file_pending_issues(
                    run, target_repo, pending, state.pack.rule_index
                ),
            },
        )


def _register_feedback_tools(mcp: MCPServer, state: AppState) -> None:
    """The optional feedback channel to the tool author."""

    @mcp.tool()
    def submit_feedback(
        extra_text: str | None = None,
        report_conclusion: str | None = None,
        report_fix_first: str | None = None,
    ) -> dict[str, Any]:
        """Send optional run feedback to the tool author.

        Requires a resolved configuration. There is nothing to send unless
        config.feedback_text was set on the configuration page, or the
        calling agent supplies extra_text; if neither is present this
        raises rather than filing an empty, pointless issue.

        The feedback body always carries the free text plus a run-metadata
        section (tool version, rules pack, assistant, model, repository,
        timestamps), and then each telemetry section the user consented to
        on the configuration page (coverage totals, findings rollup by
        severity/domain id, self-assessment, environment, consulted sources
        by rule id/url/why, rule verdict distribution by domain and in
        total, run duration and the divergence verdict between its two
        measurements, which domains had their rule text fetched via
        get_domain, and the reader's own conclusions after reading the
        report); an unconsented section is left out entirely. Finding text
        itself is never included, only counts.

        report_conclusion and report_fix_first (issue #135) are the
        reader's own answers, in their own words, to the two questions the
        finished report's own feedback form asks: in one sentence, what did
        this report tell them about their repository, and what would they
        fix first. Pass these only if the human using this session actually
        read the finished report and dictated an answer back; never guess
        or paraphrase one on their behalf. Both are ignored unless the
        reader_conclusions section was consented to on the configuration
        page, same as every other telemetry section here.

        Files a labelled issue on the tool author's feedback repository via
        gh. If gh is unavailable or filing fails for any reason, the
        feedback is never lost: this returns a mailto fallback instead,
        with the same body, so the agent can offer to open the user's mail
        client or hand over the text to paste in manually.

        May be called either before or after render_report. Called after,
        it sends feedback for the run just finished and rewrites that run's
        report.html and run-state.json so both carry the feedback issue's
        link; the response's report_updated field says whether that rewrite
        succeeded, and a failed rewrite is reported as a warning rather than
        an error, because the issue is already filed by then and raising
        would invite a retry that double-files it.
        """
        run, finished = _feedback_target(state)
        config = _require_config(run)

        free_text = config.feedback_text or extra_text
        if not (free_text and free_text.strip()):
            raise ValueError(
                "Nothing to send: no feedback_text was set on this run's configuration and no "
                "extra_text was given."
            )

        body = build_feedback_body(
            free_text,
            run.meta,
            config.telemetry_consent,
            run.domain_results,
            # sorted(), not fetch order: this run is still live, so
            # run.rules_fetched is always a concrete set here, never the
            # None that a stored RunState.rules_fetched_domain_ids can be
            # (see build_feedback_sections' own docstring for what None
            # means). A domain carried over from an untracked resume still
            # lands in rules_fetch_unknown, exactly as it does everywhere
            # else this pair is read (see _rules_fetch_summary above).
            rules_fetched_domain_ids=sorted(run.rules_fetched),
            rules_fetch_unknown_domain_ids=sorted(run.rules_fetch_unknown),
            reader_conclusion_headline=report_conclusion,
            reader_conclusion_fix_first=report_fix_first,
        )
        subject = feedback_subject(run.meta)

        if not gh_available():
            return {
                "mode": "mailto",
                "mailto_url": build_mailto_url(FEEDBACK_EMAIL, subject, body),
                "body": body,
            }

        try:
            created = create_issue(FEEDBACK_REPO, subject, body, ["feedback"])
        except IssueFilingError:
            return {
                "mode": "mailto",
                "mailto_url": build_mailto_url(FEEDBACK_EMAIL, subject, body),
                "body": body,
            }

        run.feedback_issue_url = created.url
        if finished is None:
            # Only the still-running case saves: a finished run's recovery
            # file is already gone, and rewriting one now would advertise a
            # completed run as unfinished work to resume.
            _persist_run(run)
            return _with_warnings(
                run,
                {
                    "mode": "issue",
                    "url": created.url,
                    "warnings": list(created.warnings),
                },
            )

        warnings = list(created.warnings)
        updated_state = finished.run_state.model_copy(
            update={"feedback_issue_url": created.url}
        )
        try:
            write_report(updated_state, state.pack, finished.report_path)
            atomic_write_text(finished.run_state_path, updated_state.to_json())
        except (OSError, ReportError) as exc:
            warnings.append(
                f"Feedback issue {created.url} was filed, but the already-written report at "
                f"{finished.report_path} could not be updated to link it: {exc}. The report "
                "and run-state.json still say no feedback was sent; do not resend the "
                "feedback, it is filed."
            )
            return {
                "mode": "issue",
                "url": created.url,
                "warnings": warnings,
                "report_updated": False,
            }

        finished.run_state = updated_state
        return {
            "mode": "issue",
            "url": created.url,
            "warnings": warnings,
            "report_updated": True,
            "report_path": str(finished.report_path),
        }


def _register_grill_tools(mcp: MCPServer, state: AppState) -> None:
    """Grill-side provisional standards generation.

    At the end of the grill Hot Seat step, after the user confirms the shared
    understanding, the grill skill calls write_grill_standards_artefacts to
    generate a rule set from the grill's captured rules, marked provisional,
    and render and write the three standards documents.
    """

    @mcp.tool()
    def write_grill_standards_artefacts(
        grill_rules: str,
        output_dir: str,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Generate provisional standards artefacts from grill-captured rules.

        Called at the end of the grill Hot Seat step after the user confirms the
        shared understanding. Generates a rule set from the grill's captured rules,
        marks all rules as provisional with today's date, renders the three
        standards documents with provisional annotations, and writes them to disk
        using managed-block markers. No user approval is required; documents are
        written immediately.

        Args:
            grill_rules: JSON array of rule objects captured from the grill, each
                with rule_id, domain_id, text_short, text_body, source, and optionally
                stack_profile. All rules will be marked provisional with today's date.
            output_dir: Path to the directory where rule-set.json will be written
                (typically the project's audit-output directory).
            project_dir: Optional path to the project root. If provided, the three
                standards documents are written to project_dir/docs/. If not provided,
                they are written to output_dir.

        Returns:
            Dictionary with keys:
            - success: Boolean indicating successful write
            - rule_set_path: Path to the written rule-set.json
            - document_paths: Dict mapping document names to their written paths
            - rules_count: Number of rules in the rule set
            - created_date: ISO date string when the rule set was created
            - errors: List of error messages if success is False
        """
        try:
            # Parse grill rules from JSON
            rules_data = json.loads(grill_rules)
            if not isinstance(rules_data, list):
                return {
                    "success": False,
                    "errors": ["grill_rules must be a JSON array of rule objects"],
                }

            # Convert to Rule objects with provisional status
            today = datetime.now().date().isoformat()
            rules: list[StandardsRule] = []

            for rule_data in rules_data:
                try:
                    # Create a Rule object with provisional status
                    rule = StandardsRule(
                        rule_id=rule_data["rule_id"],
                        domain_id=rule_data.get("domain_id"),
                        text_short=rule_data["text_short"],
                        text_body=rule_data["text_body"],
                        source=rule_data["source"],
                        stack_profile=rule_data.get("stack_profile"),
                        status="provisional",
                        verified_date=today,
                        grill_intent_note=rule_data.get(
                            "grill_intent_note",
                            "Recorded from engineering-grill intent.",
                        ),
                    )
                    rules.append(rule)
                except (KeyError, ValueError) as exc:
                    field_hint = ""
                    if isinstance(exc, KeyError):
                        field_hint = f" Missing required field: {exc.args[0]}"
                    return {
                        "success": False,
                        "errors": [
                            f"Rule object invalid or missing required fields (rule_id, text_short, text_body, source).{field_hint}"
                        ],
                    }

            # Create provisional rule set
            rule_set = RuleSet(
                version="1.0",
                project="engineering-audit",
                rules=rules,
            )

            # Render all three documents
            rendered = render_all(rule_set)

            # Determine directories
            output_path = Path(output_dir)
            project_path = Path(project_dir) if project_dir else None

            # Write standards and rule set
            try:
                write_standards(output_path, rendered, rule_set, project_path)
            except Exception as exc:
                return {
                    "success": False,
                    "errors": [
                        f"Failed to write standards to output directory ({output_path}): {exc}. Check that the directory exists and is writable."
                    ],
                }

            # Compute document paths for response
            docs_dir = (project_path / "docs") if project_path else output_path
            document_paths = {
                "agent-standard": str(docs_dir / "coding-standard.agent.md"),
                "human-standard": str(docs_dir / "engineering-standard.md"),
                "engineering-policy": str(docs_dir / "engineering-policy.md"),
            }

            return {
                "success": True,
                "rule_set_path": str(output_path / "rule-set.json"),
                "document_paths": document_paths,
                "rules_count": len(rules),
                "created_date": today,
            }

        except json.JSONDecodeError as exc:
            return {
                "success": False,
                "errors": [
                    f"Malformed JSON in grill_rules: {exc}. Check the JSON syntax and ensure the input is properly formatted."
                ],
            }
        except Exception as exc:
            return {
                "success": False,
                "errors": [f"Unexpected error: {type(exc).__name__}: {exc}"],
            }


def _register_report_tools(mcp: MCPServer, state: AppState) -> None:
    """Finishing a run: rendering and writing out its report."""

    @mcp.tool()
    def render_report(finished: str) -> dict[str, Any]:
        """Finish the run and render its report.

        Requires a resolved configuration. Sets meta.finished to the given
        ISO timestamp, renders the deterministic HTML report (which itself
        refuses to render an incomplete run: a selected domain with no
        recorded result, or a completed result missing a rule verdict, raises
        rather than producing a report that looks clean over a gap), and
        writes both report.html and run-state.json to the run's deliverables
        directory: config.deliverables_dir if the configuration page (or a
        preset AuditConfig) named one, otherwise the run's own output_dir,
        unchanged from how every run before that choice existed behaved.
        output_dir itself is never affected by this choice; it stays the
        run's working directory for the crash-recovery progress file
        regardless of where the finished deliverables land (issue #109).
        Any issue URLs filed this run via file_issues, and any
        feedback issue filed via submit_feedback, are carried on the
        RunState itself, so the written run-state.json is self-sufficient:
        it (and its schema_version) can be handed to
        engineering-audit-render later to re-render the same report without
        this server, this run tracker, or either URL, still in memory.

        This call also stamps meta.server_finished from the server's own
        clock, alongside the caller-supplied finished. See begin_run's
        server_started for why the report keeps both this figure and the
        caller's rather than trusting either one alone.

        The finished run stays reachable for one last submit_feedback (the
        order AUDIT.md documents), which rewrites both files to carry the
        feedback issue's link. It stops being reachable at the next
        begin_run.

        Both files are written atomically, and the run's crash-recovery file
        is removed once they are on disk: from here the run-state.json is the
        record, and a later begin_run on this output directory starts clean
        rather than offering to resume a run that is already finished.

        The response also carries "rules_fetched": which domains had their
        rule text fetched this run, which recorded verdicts without it, and
        which were carried in from a saved run that never recorded it. Any
        domain in the second list is named in the report and must be named to
        the user as well: it says the verdicts for that domain were reached
        without the rules they are verdicts on.
        """
        run = _require_run(state)
        config = _require_config(run)

        finished_meta = RunMeta(
            **{
                **run.meta.model_dump(),
                "finished": finished,
                "server_finished": _now_utc_iso(),
            }
        )

        run_state = RunState(
            # See RunTracker.schema_version: a run resumed from a file that
            # predates the not-applicable note requirement carries that
            # version through to its own finished run-state.json too, so
            # engineering-audit-render can still re-render it later (issue
            # #155).
            schema_version=run.schema_version,
            meta=finished_meta,
            config=config,
            domain_results=run.domain_results,
            filed_issue_urls=dict(run.filed_issues),
            # Carried into the finished record, not just the recovery file, so
            # a report re-rendered from run-state.json months later still
            # carries the signal rather than quietly losing it (issue #110).
            rules_fetched_domain_ids=sorted(run.rules_fetched),
            rules_fetch_unknown_domain_ids=sorted(run.rules_fetch_unknown),
            # Carried into the finished record for the same reason the fetch
            # lists are: a report re-rendered from run-state.json months later
            # should still carry where the run's time went (issue #205).
            domain_rules_fetched_at=dict(run.domain_rules_fetched_at),
            domain_recorded_at=dict(run.domain_recorded_at),
            feedback_issue_url=run.feedback_issue_url,
        )

        deliverables_dir = deliverables_dir_for(run.output_dir, config.deliverables_dir)
        report_path = write_report(
            run_state,
            state.pack,
            deliverables_dir / REPORT_FILENAME,
        )
        run_state_path = deliverables_dir / RUN_STATE_FILENAME
        atomic_write_text(run_state_path, run_state.to_json())

        # The run's real output is on disk now, so the crash-recovery file has
        # done its job. It is marked completed before being removed, so that a
        # removal which fails cannot resurrect a finished run as resumable
        # work at the next begin_run.
        _persist_run(run, completed=True)
        _remove_progress_file(run)

        # Standards generation and approval (after report is written, before run teardown)
        standards_status = "skipped"
        stack_choice_decision = None
        try:
            if run.config_server is not None:
                # Only process standards if there's a config server (interactive mode)
                verdicts = verdicts_from_domain_results(run.domain_results)
                audit_rules = audit_rules_from_domain_results(
                    run.domain_results, state.pack
                )
                prior_rule_set = load_prior_rule_set(deliverables_dir)

                # Stack mismatch stop: compare grill stack to observed stack (ticket 07)
                # Must happen before the merge step, as per ticket 06 specification.
                used_rule_set = prior_rule_set
                if run.repo_dir is not None and prior_rule_set is not None:
                    # Detect observed stack from repo_dir
                    observed_stack = detect_stack(run.repo_dir)

                    # Extract grill stack from prior_rule_set (convert frozenset to tuple)
                    grill_stack = tuple(
                        sorted(grill_stack_from_rule_set(prior_rule_set))
                    )

                    # Check if stacks differ AND grill stack is not empty
                    # Empty grill stack is normal (no prior rules exist for stacks yet);
                    # it must never halt an audit.
                    if grill_stack and stacks_differ(grill_stack, observed_stack):
                        # Stacks differ and grill stack is non-empty: present to user
                        difference = describe_stack_difference(
                            grill_stack, observed_stack
                        )
                        run.config_server.set_stack_mismatch_data(
                            grill_stack, observed_stack, difference
                        )

                        # Wait for user choice with timeout
                        stack_timeout_s = 3600  # 1 hour, same as approval timeout
                        try:
                            choice = run.config_server.wait_stack_choice(
                                stack_timeout_s
                            )
                            # Apply choice to get the resolved rule set
                            used_rule_set = resolve_stack_choice(
                                prior_rule_set,
                                state.pack.root,
                                grill_stack,
                                observed_stack.identifiers,
                                choice,
                            )
                            # Record the choice for output
                            stack_choice_decision = build_stack_choice_decision(
                                grill_stack, observed_stack, choice
                            )
                        except ConfigTimeoutError:
                            # User did not respond in time: skip standards generation
                            standards_status = "timeout"
                            # Leave used_rule_set as prior_rule_set (no merge happens)

                # Persist stack choice decision as soon as it's made, independent of approval
                if stack_choice_decision is not None:
                    _persist_stack_choice(run, deliverables_dir, stack_choice_decision)

                # Proceed to merge only if not already timed out
                if standards_status != "timeout":
                    merged = merge_rule_set(used_rule_set, verdicts, audit_rules)
                    rendered = render_all(merged, state.pack)
                    diffs = build_diffs(deliverables_dir, rendered, run.repo_dir)
                    summary_counts = derive_summary_counts(prior_rule_set, merged)

                    # Present for approval
                    run.config_server.set_approval_data(diffs, summary_counts)

                    # Wait for user approval with a timeout
                    approval_timeout_s = 3600  # 1 hour
                    try:
                        action = run.config_server.wait_approval(approval_timeout_s)
                        if action == "approve":
                            write_standards(
                                deliverables_dir, rendered, merged, run.repo_dir
                            )
                            standards_status = "approved"
                        else:
                            standards_status = "cancelled"
                    except ConfigTimeoutError:
                        standards_status = "timeout"

            else:
                # No config server means non-interactive; skip standards
                standards_status = "skipped"
        except Exception as e:
            # Standards failures must not corrupt the run; log but continue
            standards_status = f"error: {type(e).__name__}: {str(e)}"
            _queue_persist_warning(
                run,
                f"Standards generation failed: {standards_status}. "
                f"Review the standards_status field in the response for details.",
            )

        all_findings = [f for r in run.domain_results.values() for f in r.findings]
        severity_counts = Counter(f.severity.value for f in all_findings)
        findings_summary = {
            "total_findings": len(all_findings),
            "by_severity": {
                sev: severity_counts.get(sev, 0) for sev in _SEVERITY_ORDER
            },
        }

        # A rendered report is a finished run: free the slot so the next
        # begin_run does not need replace=True to start clean, while keeping
        # the run itself reachable for a final submit_feedback.
        state.run = None
        state.finished = FinishedRun(
            tracker=run,
            run_state=run_state,
            report_path=report_path,
            run_state_path=run_state_path,
        )

        rules_fetch_summary = _rules_fetch_summary(run)
        response: dict[str, Any] = {
            "report_path": str(report_path),
            "run_state_path": str(run_state_path),
            "findings_summary": findings_summary,
            "rules_fetched": rules_fetch_summary,
            "standards_status": standards_status,
        }

        # Include stack choice info if a choice was made
        if stack_choice_decision is not None:
            response["stack_choice"] = stack_choice_decision.get("choice")
        unsupported = rules_fetch_summary["verdicts_without_rules_fetched_domain_ids"]
        if unsupported:
            response["warnings"] = [
                f"{len(unsupported)} domain(s) recorded verdicts without their rule text ever "
                f"being fetched this run: {', '.join(unsupported)}. The report names them under "
                "'Rules fetched'. Say this to the user when you hand the report over; do not "
                "describe the run as complete without it."
            ]
        return _with_warnings(run, response)


class TelemetryStripError(RuntimeError):
    """Raised when _strip_ambient_otel_middleware cannot confirm the SDK's
    ambient OpenTelemetry middleware was actually removed.

    This project's design requires explicit consent for any telemetry, so a
    server that might still be carrying ambient OpenTelemetry middleware
    must not be handed back to a caller. Two conditions raise this, both
    covering the same failure described in issue #107: a future mcp 2.x
    renaming or relocating OpenTelemetryMiddleware while mcp.server._otel
    still exists, so the isinstance-based strip below silently matches
    nothing.

    - Nothing matched OpenTelemetryMiddleware to strip in the first place:
      the SDK's default middleware shape has changed underneath us, which is
      not the same thing as there being nothing to clean up.
    - Something matched by name after the strip ran: the private symbol no
      longer identifies the SDK's real telemetry middleware, so isinstance()
      let it through.
    """


def _looks_like_otel_middleware(middleware: object) -> bool:
    """Name-based backstop for the isinstance() strip in
    _strip_ambient_otel_middleware, deliberately independent of the
    OpenTelemetryMiddleware import so a rename of that class does not blind
    both checks at once."""
    name = type(middleware).__name__.lower()
    return "opentelemetry" in name or "otel" in name


def _strip_ambient_otel_middleware(mcp: MCPServer) -> None:
    """Strip the SDK's default OpenTelemetry middleware from mcp in place,
    then assert the postcondition rather than assuming the strip worked.

    The SDK installs OpenTelemetry span middleware on every server by
    default. This project's design requires explicit consent for any
    telemetry, so it is stripped here rather than left ambient.

    Raises TelemetryStripError, refusing to hand back a server that might
    still be emitting telemetry, if either check fails: no
    OpenTelemetryMiddleware instance was found to remove, or a middleware
    that still looks like OpenTelemetry by name survives the strip. See
    issue #107.
    """
    before = list(mcp.middleware)
    stripped = [m for m in before if isinstance(m, OpenTelemetryMiddleware)]
    mcp.middleware[:] = [
        m for m in before if not isinstance(m, OpenTelemetryMiddleware)
    ]

    if not stripped:
        raise TelemetryStripError(
            "no OpenTelemetryMiddleware instance was found on the SDK's default "
            "middleware to strip; the SDK's default middleware shape has changed "
            "and this tool can no longer confirm ambient telemetry is disabled"
        )

    survivors = [
        type(m).__name__ for m in mcp.middleware if _looks_like_otel_middleware(m)
    ]
    if survivors:
        raise TelemetryStripError(
            "telemetry middleware still present after stripping "
            f"OpenTelemetryMiddleware: {survivors}; the private import no longer "
            "identifies the SDK's real telemetry middleware"
        )


def build_server(
    rules_dir: Path, *, update_check_enabled: bool | None = None
) -> tuple[MCPServer, AppState]:
    """Load the rules pack and construct the MCPServer app.

    Tool registration is split per concern into the _register_*_tools
    functions above, which this calls in sequence; the call order is also the
    order the tools are advertised in, and the order AUDIT.md walks them in.

    update_check_enabled carries the resolved update-check setting onto the
    returned AppState, for begin_run to read (see AppState.update_check_enabled
    and _register_run_tools). Passing True or False fixes the setting
    explicitly: main() does this, having already folded --no-update-check
    together with ENGINEERING_AUDIT_NO_UPDATE_CHECK into one value. The
    default, None, resolves from the environment variable alone
    (_update_check_enabled_from_env), which keeps that variable a supported
    input for any caller of build_server, not only main(); a caller with no
    CLI flag of its own to fold in never needs to pass this argument at all.

    Raises RulesPackError (or RulesPackParseError) if the pack cannot be
    loaded, or TelemetryStripError if the SDK's ambient OpenTelemetry
    middleware could not be confirmed removed; neither is caught here so
    callers that want the exception (tests, alternative entry points) can
    see it directly. main() is the one place that turns either into a clean
    CLI error.
    """
    resolved_update_check_enabled = (
        _update_check_enabled_from_env()
        if update_check_enabled is None
        else update_check_enabled
    )
    state = AppState(
        pack=load_pack(rules_dir),
        update_check_enabled=resolved_update_check_enabled,
    )

    mcp = MCPServer("engineering-audit")
    _strip_ambient_otel_middleware(mcp)

    _register_pack_tools(mcp, state)
    _register_run_tools(mcp, state)
    _register_config_tools(mcp, state)
    _register_result_tools(mcp, state)
    _register_issue_tools(mcp, state)
    _register_feedback_tools(mcp, state)
    _register_grill_tools(mcp, state)
    _register_report_tools(mcp, state)

    return mcp, state


def main() -> None:
    argv = sys.argv[1:]
    rules_dir = _resolve_rules_dir(argv)
    # The one place that reconciles --no-update-check with
    # ENGINEERING_AUDIT_NO_UPDATE_CHECK: passed to build_server(s) as an
    # explicit value rather than by setting the environment variable, so a
    # `git`/`gh` subprocess this process spawns later never inherits a
    # variable nobody asked it to carry. False when the flag was passed
    # (the flag can only disable, matching the environment variable's own
    # only-disables shape); None otherwise, so build_server falls back to
    # reading the environment variable itself, which stays a supported input
    # on its own.
    update_check_enabled = False if _update_check_disabled_by_flag(argv) else None
    try:
        mcp, _state = build_server(rules_dir, update_check_enabled=update_check_enabled)
    except RulesPackError as exc:
        raise SystemExit(
            f"engineering-audit-mcp: could not load rules pack: {exc}"
        ) from exc
    except TelemetryStripError as exc:
        raise SystemExit(
            f"engineering-audit-mcp: refusing to start, ambient telemetry could not "
            f"be confirmed disabled: {exc}"
        ) from exc
    mcp.run()


if __name__ == "__main__":
    main()
