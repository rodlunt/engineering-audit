"""Resolving and validating the directory an audit run's deliverables land in.

Issue #109: the interactive configuration page picks where `report.html` and
`run-state.json` are written; `begin_run`'s `output_dir` keeps holding the
run's crash-recovery progress file regardless, since that file is written
before the configuration page even exists (see `_progress_path` and
`RunTracker.output_dir` in server.py). This module holds the rules that
choice is held to, so both callers who can set it, the interactive page's
POST handler and the headless `ENGINEERING_AUDIT_CONFIG` preset path in
server.py's `start_config`, apply exactly the same checks. It has no
dependency on either, precisely so it can sit underneath both without either
one importing the other.

Deliberately filesystem-touching, unlike the rest of `schema.py`: this is a
data-model-independent I/O concern (does this path exist, can we write to
it, is there already a report there), not a shape a JSON document can be
checked against once. See `validate_environment` in schema.py for the same
reasoning applied to a different field: environment-dependent validation is
called explicitly by the caller, never baked into a pydantic validator that
would run again, silently, on every later load of a saved config.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "REPORT_FILENAME",
    "RUN_STATE_FILENAME",
    "resolve_deliverables_dir",
    "validate_deliverables_dir",
    "existing_deliverables_warning",
    "deliverables_dir_for",
]

# The two files render_report writes. Named here, once, so the config-time
# overwrite check and render_report's own write_report/atomic_write_text
# calls in server.py cannot drift apart on what "the deliverables" means.
REPORT_FILENAME = "report.html"
RUN_STATE_FILENAME = "run-state.json"


def _existing_deliverables(path: Path) -> list[str]:
    """The deliverable filenames already sitting directly inside path, in a
    stable order. Shared by validate_deliverables_dir (which refuses on
    them) and existing_deliverables_warning (which only warns), so the two
    can never disagree about what counts as "a report already there"."""
    return [
        name for name in (REPORT_FILENAME, RUN_STATE_FILENAME) if (path / name).exists()
    ]


def resolve_deliverables_dir(raw: str) -> Path:
    """Expand ``~`` and resolve ``raw`` to an absolute path.

    Pure path arithmetic: touches no filesystem state, so it is safe to call
    just to compute the value shown back to the user before anything has
    been validated or written, which is the whole point of showing it.
    """
    return Path(raw).expanduser().resolve()


def validate_deliverables_dir(path: Path) -> str | None:
    """Return a plain-language error if ``path`` cannot safely hold this
    run's deliverables, or None if it can.

    Checked once, at configuration time, before a single domain is audited:
    the point of this function is that a missing parent, an unwritable
    directory or a report already sitting there is discovered here, not from
    render_report after the whole audit has been paid for. Never overwrites
    an existing report silently: a directory that already holds either
    output file is rejected outright rather than clobbered.

    For the custom path only (the config page's own POST handler is the one
    caller): a path typed in deliberately for one run is worth refusing
    outright on collision. The default location gets the gentler
    existing_deliverables_warning below instead, for the reason given there.
    """
    if path.exists():
        if not path.is_dir():
            return f"'{path}' already exists and is not a directory."
        if not os.access(path, os.W_OK):
            return f"'{path}' exists but is not writable."
        existing = _existing_deliverables(path)
        if existing:
            return (
                f"'{path}' already contains {' and '.join(existing)} from a previous run. "
                "Choose an empty or different directory: this tool never overwrites an "
                "existing report silently."
            )
        return None

    parent = path.parent
    if not parent.is_dir():
        return f"The parent directory '{parent}' does not exist, so '{path}' cannot be created."
    if not os.access(parent, os.W_OK):
        return f"The parent directory '{parent}' is not writable, so '{path}' cannot be created."
    return None


def existing_deliverables_warning(path: Path) -> str | None:
    """A plain-language warning if ``path`` already holds a previous run's
    report.html or run-state.json, or None if it does not, or ``path`` does
    not exist yet.

    Issue #133: validate_deliverables_dir's refusal only ever ran on the
    custom-path branch, so the default in-repo location overwrote an
    existing report unconditionally. Refusing outright there would break the
    ordinary re-audit workflow, the common case: ``<repo>/audit-output/`` is
    where every run of that repository lands, so a second run colliding with
    it is normal, not a mistake to reject. This warns instead, meant for the
    configuration page to show next to the default choice, so the user
    learns a report will be replaced before the audit is paid for, not after
    render_report has already replaced it. Consistent with how the page's
    gitignore warning behaves: informative, never a refusal.
    """
    if not path.is_dir():
        return None
    existing = _existing_deliverables(path)
    if not existing:
        return None
    return (
        f"'{path}' already contains {' and '.join(existing)} from a previous run. "
        "Submitting this form will replace it."
    )


def deliverables_dir_for(output_dir: Path, deliverables_dir: str | None) -> Path:
    """The directory report.html and run-state.json are written to for this
    run: the configuration's explicit choice if it made one, otherwise
    output_dir unchanged, exactly as it behaved before this run gained a
    choice at all."""
    if deliverables_dir is None:
        return output_dir
    return Path(deliverables_dir)
