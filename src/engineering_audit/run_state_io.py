"""Reading and writing this tool's run-state files on disk.

Two jobs live here, both about a file that has to survive being handed to a
different process than the one that wrote it.

Loading: engineering-audit-render, engineering-audit-eval and the server's
own resume path all need to turn a path into a
:class:`~engineering_audit.schema.RunState` or
:class:`~engineering_audit.schema.RunProgress` with the same loud-failure
behaviour: a missing file, an unreadable file, invalid JSON, a document that
fails schema validation (including a JSON document whose top level is not an
object), and a schema_version newer than this tool understands must all be
reported clearly, never as a raw traceback and never as "no file here". This
module is the one place that logic lives, so the callers cannot drift out of
step with each other; each CLI catches :class:`RunStateLoadError` and wraps it
in its own program name and exit behaviour.

Writing: every write goes through :func:`atomic_write_text`, because a run
that is interrupted mid-write must not leave a truncated file that parses
later as a valid but wrong state. That is worse than losing the file
outright: nothing downstream can tell the difference.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from engineering_audit.schema import RunProgress, RunState, RunStateVersionError

__all__ = [
    "PROGRESS_FILENAME",
    "RunStateLoadError",
    "atomic_write_text",
    "load_run_progress_file",
    "load_run_state_file",
    "save_run_progress",
]

# The crash-recovery file's name inside a run's output directory. Deliberately
# not run-state.json: that name is the finished deliverable, and a half-run
# wearing it would be picked up by engineering-audit-render and read as a
# complete audit that simply found nothing in the domains it never reached.
PROGRESS_FILENAME = "run-state.progress.json"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class RunStateLoadError(Exception):
    """Raised when a run-state or run-progress file cannot be turned into its
    model: it does not exist, cannot be read or decoded, is not valid JSON,
    fails schema validation (including a top level that is not a JSON object),
    or names a schema_version newer than this tool understands. Callers turn
    this into their own clean, non-zero CLI exit, or into a reported warning;
    it is never allowed to surface as an unhandled exception, and never to be
    confused with the file being absent."""


def _load_file(path: Path, parse: Callable[[str], _ModelT], label: str) -> _ModelT:
    """Read and parse one file, raising RunStateLoadError on any failure.

    The message carries no CLI-specific prefix; each caller prepends its own
    program name when reporting it, so the callers' error text differs only in
    that prefix, never in substance.
    """
    if not path.is_file():
        raise RunStateLoadError(f"{label} does not exist: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RunStateLoadError(f"could not read {path}: {exc}") from exc
    try:
        return parse(raw_text)
    except RunStateVersionError as exc:
        raise RunStateLoadError(str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RunStateLoadError(f"{path} is not a valid {label}: {exc}") from exc


def load_run_state_file(path: Path) -> RunState:
    """Load and parse a run-state.json file, raising RunStateLoadError on any
    failure."""
    return _load_file(path, RunState.from_json, "run-state file")


def load_run_progress_file(path: Path) -> RunProgress:
    """Load and parse a run's crash-recovery file, raising RunStateLoadError
    on any failure.

    A caller deciding whether to offer a resume must treat that error as "a
    prior run is here and I cannot read it", never as "there is no prior
    run": the second silently discards work.
    """
    return _load_file(path, RunProgress.from_json, "run-progress file")


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory entry, so a completed rename survives
    a machine crash and not only a process crash.

    Failure is swallowed on purpose, and this is the written reason: opening a
    directory for fsync is unsupported on some platforms and filesystems
    (Windows most obviously), and what it buys is durability across a power
    loss, not correctness. The rename has already happened by the time this
    runs, so a reader still sees either the whole previous file or the whole
    new one, which is the property every caller here depends on. There is no
    action a caller could take on this failure, so it does not travel.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, replacing whatever is there.

    The content goes to a temporary file in the *same directory*, is flushed
    and fsynced, and is then renamed over the target. Same directory is not a
    detail: os.replace is only atomic within one filesystem, and a temp file
    under the system temp directory can easily land on another one, which
    turns the rename into a copy that can be interrupted halfway.

    Raises OSError if the write or the rename fails, having removed its own
    temporary file. The previous contents of ``path`` are untouched in that
    case: a failed write leaves the last good state, never a truncated one.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # The temp file is this function's own litter. Left behind, a
        # repeatedly failing write would fill the user's output directory with
        # dotfiles. The failure itself is re-raised untouched.
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def save_run_progress(path: Path, progress: RunProgress) -> None:
    """Write a run's crash-recovery record atomically."""
    atomic_write_text(path, progress.to_json())
