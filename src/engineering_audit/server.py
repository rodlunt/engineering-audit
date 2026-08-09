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
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, distribution as _pkg_distribution
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError

# Private import: the SDK enables OpenTelemetry span middleware on every
# server unconditionally (mcp/server/lowlevel/server.py), and this tool's
# consent model forbids ambient telemetry, so it is stripped out in
# build_server() below. If this import breaks on an SDK upgrade, that is the
# loud ImportError we want rather than a silent no-op strip.
from mcp.server._otel import OpenTelemetryMiddleware

from engineering_audit.config_page import ConfigServer, ConfigTimeoutError
from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    FEEDBACK_REPO,
    build_feedback_body,
    build_issue_trailing_line,
    build_mailto_url,
    feedback_subject,
)
from engineering_audit.issues import (
    IssueFilingError,
    create_issue,
    detect_repo,
    ensure_label,
    gh_available,
)
from engineering_audit.report import ReportError, write_report
from engineering_audit.rules import Rule, RulesPack, RulesPackError, get_domain_text, load_pack
from engineering_audit.run_state_io import (
    PROGRESS_FILENAME,
    RunStateLoadError,
    atomic_write_text,
    load_run_progress_file,
    save_run_progress,
)
from engineering_audit.schema import (
    AuditConfig,
    DomainResult,
    Finding,
    RunMeta,
    RunProgress,
    RunState,
    validate_completeness,
    validate_consulted_sources,
)
from engineering_audit.update_check import check_for_update

__all__ = ["AppState", "FinishedRun", "PriorRun", "RunTracker", "build_server", "main"]

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def _default_tool_version() -> str:
    """Read the installed package version, or a clear placeholder if the
    package metadata is not available (e.g. running from a source checkout
    that was never installed)."""
    try:
        return _pkg_version("engineering-audit")
    except PackageNotFoundError:
        return "0.0.0-dev"


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


def _default_tool_commit() -> str | None:
    """Best-effort: the git commit the installed tool build was made from,
    read from the installed distribution's PEP 610 install record
    (direct_url.json, present when installed via ``pip``/``uv`` from a git
    URL; absent for a PyPI/wheel install or a source checkout that was
    never installed).

    This is provenance telemetry only, never load-bearing for the tool to
    run, so any failure here (package not installed, no direct_url.json,
    unreadable, malformed) is swallowed and reported as None: the caller
    renders that as "unknown" in the report rather than fabricating a
    commit the tool cannot actually vouch for.
    """
    try:
        direct_url_json = _pkg_distribution("engineering-audit").read_text("direct_url.json")
    except Exception:
        return None
    if direct_url_json is None:
        return None
    return _parse_direct_url_commit(direct_url_json)


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


def _git_commit(path: Path) -> str | None:
    """Best-effort: the full HEAD SHA of the git repository containing
    ``path``, with a ``-dirty`` suffix appended when the working tree has
    uncommitted changes.

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
    status = _run_git(["status", "--porcelain"], path)
    if status is None or status.returncode != 0:
        return None
    return f"{sha}-dirty" if status.stdout.strip() else sha


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
    repo_dir: Path | None = None
    config: AuditConfig | None = None
    config_mode: str | None = None
    config_url: str | None = None
    config_server: ConfigServer | None = None
    domain_results: dict[str, DomainResult] = field(default_factory=dict)
    # Keyed by finding key (see _pending_issues), not by rule id: a domain
    # result may legitimately carry two findings for the same rule, and a map
    # keyed by rule id drops one of the two issue urls without saying so, and
    # makes the second finding look already-filed on a retry.
    filed_issues: dict[str, str] = field(default_factory=dict)
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


def _filed_urls_by_rule(run: RunTracker) -> dict[str, str]:
    """Project the run's filed-issue bookkeeping onto the rule-id-keyed shape
    RunState.filed_issue_urls uses.

    The report marks a finding as already filed by looking its rule id up in
    that map, so it cannot hold two urls for one rule: where a rule has two
    findings, both show the first url filed for it. The tool's own file_issues
    result keeps every url under its own finding key; this projection is
    lossy only in the written run state, and only for that case.
    """
    by_rule: dict[str, str] = {}
    for issue in _run_issues(run):
        url = run.filed_issues.get(issue.key)
        if url is not None:
            by_rule.setdefault(issue.finding.rule_id, url)
    return by_rule


def _progress_path(output_dir: Path) -> Path:
    """The crash-recovery file for a run whose output directory is this.

    It lives beside the run's own report.html and run-state.json, and nowhere
    else: the output directory is the one path the agent already has to name
    and remember, so a resumed session finds the file by pointing begin_run at
    the same place it pointed the interrupted one. A central per-user recovery
    directory would need its own naming, its own cleanup, and its own answer
    to "which of these three is my run", none of which this needs.
    """
    return output_dir / PROGRESS_FILENAME


def _run_progress(run: RunTracker, *, completed: bool = False) -> RunProgress:
    """Snapshot the tracker as the record written to disk."""
    return RunProgress(
        meta=run.meta,
        config=run.config,
        config_mode=run.config_mode,
        repo_dir=str(run.repo_dir) if run.repo_dir is not None else None,
        domain_results=run.domain_results,
        filed_issues=run.filed_issues,
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
    response["warnings"] = [*(existing if isinstance(existing, list) else []), *run.persist_warnings]
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
        "selected_domain_ids": config.selected_domain_ids if config is not None else None,
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
            "prior_run": {"path": str(prior.path), "readable": False, "error": prior.error},
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


def _require_run(state: AppState) -> RunTracker:
    if state.run is None:
        raise ValueError(
            "No audit run in progress. Call begin_run first."
        )
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
    raise ValueError(
        "No audit run in progress. Call begin_run first."
    )


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
    current_pack_commit = _git_commit(state.pack.root)
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

    run = RunTracker(
        meta=progress.meta,
        output_dir=output_dir,
        repo_dir=resolved_repo_dir,
        config=config,
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
            "repo_dir": str(resolved_repo_dir) if resolved_repo_dir is not None else None,
            "config": config.model_dump(mode="json") if config is not None else None,
            "selected_domain_ids": config.selected_domain_ids if config is not None else None,
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
        trailing_line = build_issue_trailing_line(finding, rule)
        body = f"{finding.issue_body}\n\n{trailing_line}"
        try:
            created = create_issue(target_repo, finding.issue_title, body, labels)
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
    parser = argparse.ArgumentParser(prog="engineering-audit-mcp", add_help=False)
    parser.add_argument("--rules-dir", default=None)
    args = parser.parse_args(argv)
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
        """
        domain = state.pack.get_domain(domain_id)
        if domain is None:
            valid_ids = ", ".join(d.id for d in state.pack.domains) or "(no domains loaded)"
            raise ValueError(f"Unknown domain id '{domain_id}'. Valid ids: {valid_ids}")
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
        if omitted; environment is optional free-form metadata. repo_dir is
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

        The recorded metadata also stamps two provenance SHAs, best-effort:
        tool_commit (the git commit the installed tool build was made from,
        via its PEP 610 install record) and rules_pack_commit (the loaded
        rules pack directory's git HEAD, '-dirty' suffixed if it has
        uncommitted changes). Either is None when it could not be
        determined, which the report renders as "unknown" rather than
        guessing: a report must be traceable to the exact tool build and
        rules version that produced it, not just a package version number
        that can lag behind either.

        The run also performs a best-effort tool update check, comparing
        tool_commit against the tool's latest tagged release on GitHub. The
        result lands in the returned meta's update_check field, tri-state:
        "current", "stale", or "could-not-check" (see
        engineering_audit.update_check for the exact strings). The calling
        agent MUST tell the user when this reports stale or could-not-check,
        rather than silently proceeding as if the installed build were
        confirmed current: this tool is installed via a pinned uvx
        reference, and a stale pin or cache would otherwise serve an old
        build forever with nothing to say so.
        """
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
                state, prior, output_dir_path, repo_dir_path, repo_name, repo_commit
            )

        tool_version_value = tool_version or _default_tool_version()
        tool_commit_value = _default_tool_commit()
        meta = RunMeta(
            tool_version=tool_version_value,
            tool_commit=tool_commit_value,
            rules_pack_name=state.pack.root.name,
            rules_pack_commit=_git_commit(state.pack.root),
            update_check=check_for_update(tool_commit_value, tool_version_value),
            assistant=assistant,
            model=model,
            repo_name=repo_name,
            repo_commit=repo_commit,
            started=started,
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
        if prior is not None:
            # Saying what was thrown away is the difference between a discard
            # the user chose and one they will only find out about later.
            response["discarded_prior_run"] = (
                _prior_run_summary(prior.progress, prior.path)
                if prior.progress is not None
                else {"path": str(prior.path), "readable": False, "error": prior.error}
            )
        return _with_warnings(run, response)


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
            return {"mode": "interactive", "url": run.config_url}

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
            run.config = config
            run.config_mode = "preset"
            _persist_run(run)
            return _with_warnings(run, _config_summary(run))

        config_server = ConfigServer(state.pack.domains)
        url = config_server.start()
        run.config_server = config_server
        run.config_mode = "interactive"
        run.config_url = url
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
        return {"mode": "interactive", "url": url, "opened_in_browser": opened}

    @mcp.tool()
    def get_config(timeout_s: float = 300) -> dict[str, Any]:
        """Fetch the resolved audit configuration.

        Requires start_config to have been called first. In preset mode the
        configuration is already known and is returned immediately. In
        interactive mode this blocks for up to timeout_s seconds waiting for
        the user to submit the configuration page; on timeout it raises a
        clear error rather than proceeding with a default configuration the
        user never actually chose. Call it again (the user's submission is
        still awaited) rather than treating a timeout as a green light.
        """
        run = _require_run(state)
        if run.config_mode is None:
            raise ValueError("start_config must be called before get_config.")

        if run.config is not None:
            return _config_summary(run)

        assert run.config_server is not None  # config_mode == "interactive" implies this
        try:
            config = run.config_server.wait(timeout_s)
        except ConfigTimeoutError as exc:
            raise ValueError(
                f"{exc} The user has not submitted the configuration page at {run.config_url}. "
                "Tell the user the audit is waiting on them there, then call get_config again "
                "rather than proceeding with a default configuration."
            ) from exc

        _validate_selected_domains(state, config)
        run.config = config
        run.config_server.shutdown()
        # Saved as soon as it is resolved: the configuration is the one step
        # of a run that costs a person's time rather than the agent's, and an
        # interruption before the first domain must not make them fill the
        # page in again.
        _persist_run(run)
        return _with_warnings(run, _config_summary(run))


def _register_result_tools(mcp: MCPServer, state: AppState) -> None:
    """Recording per-domain results, and reporting progress over them."""

    @mcp.tool()
    def record_domain_result(result: DomainResult, replace: bool = False) -> dict[str, Any]:
        """Record the audit result for one domain.

        The payload itself is pydantic-validated by DomainResult (finding and
        verdict consistency, could-not-run reason, could-not-evaluate notes,
        and that every consulted_sources entry has a non-blank url, title
        and why). On top of that: the domain must be one of the domains
        selected for this run, a completed result must carry a verdict for
        every rule the domain defines, and every consulted_sources rule_id
        must be one of this domain's own rules; a completed result missing a
        verdict raises IncompleteResultError listing exactly which rule ids
        are missing, and an unattributable consulted source raises
        UnknownRuleIdError, so the agent can fix and resubmit rather than a
        skipped rule silently passing or a citation silently pointing at
        nothing. Re-recording an already-recorded domain requires
        replace=True, to guard against an accidental overwrite.
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
                raise ValueError(f"domain '{domain_id}' is not in the loaded rules pack")
            validate_completeness(domain, result)

        if result.consulted_sources:
            # Checked regardless of status: a source consulted while
            # deciding a domain could not run at all is still attributed to
            # a rule in this domain, not to whatever the "completed" branch
            # above already fetched.
            domain = state.pack.get_domain(domain_id)
            if domain is None:
                raise ValueError(f"domain '{domain_id}' is not in the loaded rules pack")
            validate_consulted_sources(domain, result)

        if domain_id in run.domain_results and not replace:
            raise ValueError(
                f"domain '{domain_id}' already has a recorded result; pass replace=True to "
                "overwrite it."
            )

        run.domain_results[domain_id] = result
        # Saved before the response goes back, so a server that dies between
        # this domain and the next one loses nothing that has been reported as
        # recorded. A save failure is reported in warnings, never raised: the
        # result is accepted either way.
        _persist_run(run)
        return _with_warnings(
            run,
            {
                "domain_id": domain_id,
                "status": result.status,
                "finding_count": len(result.findings),
            },
        )

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
            d for d in selected
            if d in run.domain_results and run.domain_results[d].status == "could-not-run"
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
                **_file_pending_issues(run, target_repo, pending, state.pack.rule_index),
            },
        )


def _register_feedback_tools(mcp: MCPServer, state: AppState) -> None:
    """The optional feedback channel to the tool author."""

    @mcp.tool()
    def submit_feedback(extra_text: str | None = None) -> dict[str, Any]:
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
        by rule id/url/why); an unconsented section is left out entirely.
        Finding text itself is never included, only counts.

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

        body = build_feedback_body(free_text, run.meta, config.telemetry_consent, run.domain_results)
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
                run, {"mode": "issue", "url": created.url, "warnings": list(created.warnings)}
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
        writes both report.html and run-state.json to the run's output
        directory. Any issue URLs filed this run via file_issues, and any
        feedback issue filed via submit_feedback, are carried on the
        RunState itself, so the written run-state.json is self-sufficient:
        it (and its schema_version) can be handed to
        engineering-audit-render later to re-render the same report without
        this server, this run tracker, or either URL, still in memory.

        The finished run stays reachable for one last submit_feedback (the
        order AUDIT.md documents), which rewrites both files to carry the
        feedback issue's link. It stops being reachable at the next
        begin_run.

        Both files are written atomically, and the run's crash-recovery file
        is removed once they are on disk: from here the run-state.json is the
        record, and a later begin_run on this output directory starts clean
        rather than offering to resume a run that is already finished.
        """
        run = _require_run(state)
        config = _require_config(run)

        finished_meta = RunMeta(**{**run.meta.model_dump(), "finished": finished})

        run_state = RunState(
            meta=finished_meta,
            config=config,
            domain_results=run.domain_results,
            filed_issue_urls=_filed_urls_by_rule(run),
            feedback_issue_url=run.feedback_issue_url,
        )

        report_path = write_report(
            run_state,
            state.pack,
            run.output_dir / "report.html",
        )
        run_state_path = run.output_dir / "run-state.json"
        atomic_write_text(run_state_path, run_state.to_json())

        # The run's real output is on disk now, so the crash-recovery file has
        # done its job. It is marked completed before being removed, so that a
        # removal which fails cannot resurrect a finished run as resumable
        # work at the next begin_run.
        _persist_run(run, completed=True)
        _remove_progress_file(run)

        all_findings = [f for r in run.domain_results.values() for f in r.findings]
        severity_counts = Counter(f.severity.value for f in all_findings)
        findings_summary = {
            "total_findings": len(all_findings),
            "by_severity": {sev: severity_counts.get(sev, 0) for sev in _SEVERITY_ORDER},
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

        return _with_warnings(
            run,
            {
                "report_path": str(report_path),
                "run_state_path": str(run_state_path),
                "findings_summary": findings_summary,
            },
        )


def build_server(rules_dir: Path) -> tuple[MCPServer, AppState]:
    """Load the rules pack and construct the MCPServer app.

    Tool registration is split per concern into the _register_*_tools
    functions above, which this calls in sequence; the call order is also the
    order the tools are advertised in, and the order AUDIT.md walks them in.

    Raises RulesPackError (or RulesPackParseError) if the pack cannot be
    loaded; this is intentionally not caught here so callers that want the
    exception (tests, alternative entry points) can see it directly. main()
    is the one place that turns it into a clean CLI error.
    """
    state = AppState(pack=load_pack(rules_dir))

    mcp = MCPServer("engineering-audit")
    # The SDK installs OpenTelemetry span middleware on every server by
    # default. This project's design requires explicit consent for any
    # telemetry, so it is stripped here rather than left ambient.
    mcp.middleware[:] = [
        m for m in mcp.middleware if not isinstance(m, OpenTelemetryMiddleware)
    ]

    _register_pack_tools(mcp, state)
    _register_run_tools(mcp, state)
    _register_config_tools(mcp, state)
    _register_result_tools(mcp, state)
    _register_issue_tools(mcp, state)
    _register_feedback_tools(mcp, state)
    _register_report_tools(mcp, state)

    return mcp, state


def main() -> None:
    rules_dir = _resolve_rules_dir(sys.argv[1:])
    try:
        mcp, _state = build_server(rules_dir)
    except RulesPackError as exc:
        raise SystemExit(f"engineering-audit-mcp: could not load rules pack: {exc}") from exc
    mcp.run()


if __name__ == "__main__":
    main()
