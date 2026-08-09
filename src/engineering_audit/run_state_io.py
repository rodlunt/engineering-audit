"""Shared loader for a run-state.json file on disk.

Both engineering-audit-render and engineering-audit-eval need to turn a
path into a :class:`~engineering_audit.schema.RunState` with the same
loud-failure behaviour: a missing file, an unreadable file, invalid JSON, a
run-state that fails schema validation (including a JSON document whose top
level is not an object), and a schema_version newer than this tool
understands must all be reported clearly, never as a raw traceback. This
module is the one place that logic lives, so the two CLIs cannot drift out
of step with each other; each CLI catches :class:`RunStateLoadError` and
wraps it in its own program name and exit behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from engineering_audit.schema import RunState, RunStateVersionError

__all__ = ["RunStateLoadError", "load_run_state_file"]


class RunStateLoadError(Exception):
    """Raised when a run-state.json file cannot be turned into a RunState:
    it does not exist, cannot be read, is not valid JSON, fails schema
    validation (including a top level that is not a JSON object), or names
    a schema_version newer than this tool understands. Callers turn this
    into their own clean, non-zero CLI exit; it is never allowed to surface
    as an unhandled exception."""


def load_run_state_file(path: Path) -> RunState:
    """Load and parse a run-state.json file, raising RunStateLoadError on
    any failure.

    The message carries no CLI-specific prefix; each caller prepends its
    own program name when reporting it, so the two CLIs' error text differs
    only in that prefix, never in substance.
    """
    if not path.is_file():
        raise RunStateLoadError(f"run-state file does not exist: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunStateLoadError(f"could not read {path}: {exc}") from exc
    try:
        return RunState.from_json(raw_text)
    except RunStateVersionError as exc:
        raise RunStateLoadError(str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RunStateLoadError(f"{path} is not a valid run-state file: {exc}") from exc
