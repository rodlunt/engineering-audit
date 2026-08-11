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
    "deliverables_dir_for",
]

# The two files render_report writes. Named here, once, so the config-time
# overwrite check and render_report's own write_report/atomic_write_text
# calls in server.py cannot drift apart on what "the deliverables" means.
REPORT_FILENAME = "report.html"
RUN_STATE_FILENAME = "run-state.json"


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
    """
    if path.exists():
        if not path.is_dir():
            return f"'{path}' already exists and is not a directory."
        if not os.access(path, os.W_OK):
            return f"'{path}' exists but is not writable."
        existing = [
            name
            for name in (REPORT_FILENAME, RUN_STATE_FILENAME)
            if (path / name).exists()
        ]
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


def deliverables_dir_for(output_dir: Path, deliverables_dir: str | None) -> Path:
    """The directory report.html and run-state.json are written to for this
    run: the configuration's explicit choice if it made one, otherwise
    output_dir unchanged, exactly as it behaved before this run gained a
    choice at all."""
    if deliverables_dir is None:
        return output_dir
    return Path(deliverables_dir)
