"""Best-effort check for a newer released build of this tool.

`server.py`'s `_default_tool_commit` stamps provenance (the git commit the
installed build was made from), which makes a stale install *diagnosable*
once someone goes looking. It does nothing to make staleness *discoverable*:
the tool is installed via
``uvx --from git+https://github.com/rodlunt/engineering-audit@<pin>``, so a
pin left pointing at an old tag, or a stale uvx cache, serves an old build
forever with nothing to say so. This module is the active half: it asks the
tool's own GitHub repository what the latest release actually is and
compares it to what is installed.

Like `_default_tool_commit`, this is telemetry, never load-bearing: a run
must be able to proceed whether or not the network, git, or GitHub are
available. The parsing and decision logic (:func:`_resolve_update_status`)
is split from the network call (:func:`check_for_update`) for the same
reason `_parse_direct_url_commit` is split from `_default_tool_commit`:
so the decision logic is testable with plain string fixtures, no network or
subprocess involved.

The hard rule this module exists to uphold: a check that could not run must
never be reported as "current". Reporting "current" on a failure would tell
the user their stale build is fine, which is worse than saying nothing.
"could-not-check" is its own honest, distinct state.

The same discipline applies to a check that was never attempted at all. Both
functions below accept ``enabled``, set from ``--no-update-check`` or the
``ENGINEERING_AUDIT_NO_UPDATE_CHECK`` environment variable (see server.py's
``begin_run``); when it is False, the network call is skipped entirely and
the status is "not-checked: ...", never "could-not-check" and never
"current". "could-not-check" means an attempt was made and failed;
"not-checked" means no attempt was made because the user turned it off. A
reader who cannot tell those apart cannot tell "the network was down" from
"nobody asked", and conflating either with "current" would tell the user
their stale build is fine when nothing was actually verified.
"""

from __future__ import annotations

import os
import re
import subprocess

__all__ = ["TOOL_REPO_URL", "check_for_update", "check_pack_for_update"]

# The tool's own repository. `feedback.py`'s FEEDBACK_REPO is a separate
# "owner/name" slug used for `gh` issue filing (a different API, a different
# spelling); this is the full clone URL `git ls-remote` needs, so it is kept
# as its own constant rather than derived from FEEDBACK_REPO.
TOOL_REPO_URL = "https://github.com/rodlunt/engineering-audit"

_TAG_RE = re.compile(r"^refs/tags/v(\d+)\.(\d+)\.(\d+)$")
_PEELED_SUFFIX = "^{}"


def _run_ls_remote(repo_url: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``git ls-remote <repo_url> HEAD "refs/tags/v*"``, or None if it
    could not be run at all (git missing, or it timed out).

    Deliberately not a local ``git -C`` call: this needs the state of the
    *remote* repository, not the local checkout (there may not even be one,
    e.g. when this tool itself was installed as a wheel with no working
    tree). The 5s timeout mirrors `server.py`'s `_run_git`: a flaky or
    unreachable network must not hang a run waiting on optional telemetry.
    """
    try:
        return subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD", "refs/tags/v*"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _resolve_update_status(
    ls_remote_output: str,
    installed_commit: str | None,
    installed_version: str,
) -> str:
    """Pure decision core: turn raw ``git ls-remote`` output plus what is
    installed into one of the three status strings.

    Parsing: each line is ``<sha>\\t<ref>``. The ``HEAD`` line gives the tip
    of the default branch. A line matching ``refs/tags/vX.Y.Z`` exactly
    (three numeric parts, no pre-release or build suffix) is a release tag;
    for an annotated tag, git also lists a peeled ``refs/tags/vX.Y.Z^{}``
    line carrying the commit the tag actually points at, which is preferred
    over the tag object's own sha when both are present, since a comparison
    against ``installed_commit`` (always a commit sha) must use commit
    shas throughout. The highest version is chosen by comparing the three
    integers as a tuple, not lexicographically: a string comparison would
    rank "0.9.0" above "0.10.0".
    """
    if installed_commit is None:
        return (
            "could-not-check: installed build's commit is unknown (not a git install)"
        )

    head_sha: str | None = None
    tag_shas: dict[tuple[int, int, int], str] = {}

    for line in ls_remote_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if not sha:
            continue
        if ref == "HEAD":
            head_sha = sha
            continue
        peeled = ref.endswith(_PEELED_SUFFIX)
        bare_ref = ref[: -len(_PEELED_SUFFIX)] if peeled else ref
        match = _TAG_RE.match(bare_ref)
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if peeled or version not in tag_shas:
            tag_shas[version] = sha

    if not tag_shas:
        return "could-not-check: no version tags found on the remote"

    latest_version = max(tag_shas)
    latest_sha = tag_shas[latest_version]
    latest_label = "v" + ".".join(str(part) for part in latest_version)

    if installed_commit == latest_sha:
        return f"current ({latest_label})"
    if head_sha is not None and installed_commit == head_sha:
        return f"current (ahead of {latest_label}, matches main)"
    return (
        f"stale: latest release is {latest_label} ({latest_sha[:12]}), installed build is "
        f"{installed_version} @ {installed_commit[:12]}"
    )


def check_for_update(
    installed_commit: str | None,
    installed_version: str,
    repo_url: str = TOOL_REPO_URL,
    enabled: bool = True,
) -> str:
    """Best-effort: compare the installed build against the tool's latest
    GitHub release tag, and return one of four status strings (see
    :func:`_resolve_update_status` for the "current"/"stale"/"could-not-check"
    prefixes; ``enabled=False`` adds the fourth, "not-checked").

    Runs a single ``git ls-remote`` against ``repo_url`` (no local
    repository needed) and delegates parsing to :func:`_resolve_update_status`.
    ``installed_commit`` is checked first, before touching the network at
    all: if it is None there is nothing to compare against, so this returns
    ``could-not-check`` without ever invoking git.

    ``enabled=False`` is checked before that, and before anything else: no
    subprocess is started and no timeout is paid, because the check was
    turned off deliberately, not attempted and failed. The returned string
    starts with "not-checked", never "could-not-check" and never "current".

    This must never raise: any failure (git missing, network unreachable,
    timeout, non-zero exit) is caught here and folded into a
    ``could-not-check`` string, with a short stderr snippet when git ran but
    failed. The one thing this function must never do, on any failure path,
    is return a string starting with "current": an update check that could
    not run has no evidence the installed build is current, and reporting
    it as such would be worse than not checking at all.

    A ``-dirty``-suffixed ``installed_commit`` (issue #169: the git fallback
    for an editable/checkout install, via server.py's ``_default_tool_commit``)
    is handled the same way :func:`check_pack_for_update` already handles a
    dirty pack commit: a modified working tree may be ahead of, behind, or
    equal to the latest release, so no comparison is meaningful, and this
    returns ``could-not-check`` with a modified-build reason before the
    network is ever touched. A git-URL install can never be dirty (the build
    is immutable), so in practice only the #169 fallback path reaches this
    branch.
    """
    if not enabled:
        return "not-checked: update check disabled by configuration"
    if installed_commit is None:
        return _resolve_update_status("", None, installed_version)
    if installed_commit.endswith("-dirty"):
        return (
            "could-not-check: installed build has uncommitted changes, so it matches "
            "no release"
        )

    try:
        result = _run_ls_remote(repo_url)
    except Exception as exc:  # noqa: BLE001 - telemetry only, never fatal; see module docstring
        return f"could-not-check: unexpected error running git ls-remote ({exc})"

    if result is None:
        return "could-not-check: git is not available or the remote check timed out"
    if result.returncode != 0:
        stderr_snippet = (
            result.stderr.strip().splitlines()[0] if result.stderr.strip() else ""
        )
        suffix = f": {stderr_snippet}" if stderr_snippet else ""
        return f"could-not-check: git ls-remote failed{suffix}"

    try:
        return _resolve_update_status(
            result.stdout, installed_commit, installed_version
        )
    except Exception as exc:  # noqa: BLE001 - telemetry only, never fatal; see module docstring
        return f"could-not-check: unexpected error parsing remote refs ({exc})"


def _pack_remote_url(pack_dir: str) -> str | None:
    """The pack's ``origin`` URL, or None if it has no remote or git could not
    run. A pack served from a plain directory with no remote is a normal,
    supported case, not an error."""
    try:
        result = subprocess.run(
            ["git", "-C", pack_dir, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def check_pack_for_update(
    pack_dir: str,
    pack_commit: str | None,
    pack_version: str | None,
    enabled: bool = True,
) -> str:
    """Best-effort: compare the loaded rules pack against its own remote's
    latest release tag. Same four-state contract as :func:`check_for_update`,
    including ``enabled``.

    This exists because the tool checked itself for staleness and did not
    check its ruleset, which is the thing that actually determines what gets
    audited. A run against a pack a year behind the published one produces
    confident findings against superseded rules and, before this, said
    nothing about it.

    A pack with no remote, no tags, or no git at all reports could-not-check.
    That is the common case for a third-party or vendored pack and is a
    perfectly legitimate way to run the tool, so it must never read as
    "current": a check that could not run is not evidence of freshness.
    ``enabled=False`` is checked first and returns "not-checked" instead,
    without starting git at all: turned off deliberately is a different fact
    from attempted and failed.

    ``GIT_TERMINAL_PROMPT=0`` is set throughout because the standard pack is a
    private repository. Without it, a machine lacking cached credentials would
    have git block on an interactive password prompt inside what is meant to
    be optional telemetry, hanging the run.
    """
    if not enabled:
        return "not-checked: rules pack update check disabled by configuration"
    if pack_commit is None:
        return "could-not-check: rules pack's commit is unknown (not a git checkout)"
    if pack_commit.endswith("-dirty"):
        # Deliberately not "stale": a modified working tree may be ahead of the
        # latest release, behind it, or neither. What is certain is that it
        # corresponds to no released version, so no comparison is meaningful.
        return "could-not-check: rules pack has uncommitted changes, so it matches no release"

    remote_url = _pack_remote_url(pack_dir)
    if remote_url is None:
        return "could-not-check: rules pack has no origin remote to compare against"

    try:
        result = _run_ls_remote(remote_url)
    except Exception as exc:  # noqa: BLE001 - telemetry only, never fatal; see module docstring
        return f"could-not-check: unexpected error running git ls-remote on the pack ({exc})"

    if result is None:
        return (
            "could-not-check: git is not available or the pack's remote check timed out"
        )
    if result.returncode != 0:
        stderr_snippet = (
            result.stderr.strip().splitlines()[0] if result.stderr.strip() else ""
        )
        suffix = f": {stderr_snippet}" if stderr_snippet else ""
        return f"could-not-check: git ls-remote on the pack failed{suffix}"

    try:
        return _resolve_update_status(
            result.stdout, pack_commit, pack_version or "unknown"
        )
    except Exception as exc:  # noqa: BLE001 - telemetry only, never fatal; see module docstring
        return (
            f"could-not-check: unexpected error parsing the pack's remote refs ({exc})"
        )
