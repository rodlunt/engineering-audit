"""Console entry point: re-render report.html from a saved run-state.json.

The MCP server's render_report tool writes run-state.json alongside
report.html on every finished run, and that file now carries everything a
report needs (schema_version, filed issue URLs, feedback issue URL) with no
outside state. This entry point exists for the case where the report needs
regenerating after the fact: the rules pack was corrected, the report
template changed, or report.html itself was lost while run-state.json
survived. It applies the same loud-failure rules as the live MCP path: a
run-state file this tool cannot parse, including one written by a newer,
incompatible version of the tool, is a hard, non-zero-exit error naming the
problem, never a best-effort partial render.

Usage: engineering-audit-render <run-state.json> [--rules-dir <path>] [--out <path>]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from engineering_audit.report import ReportError, write_report
from engineering_audit.rules import RulesPackError, load_pack
from engineering_audit.run_state_io import RunStateLoadError, load_run_state_file
from engineering_audit.schema import RunState

__all__ = ["main"]


def _resolve_rules_dir(rules_dir_arg: str | None) -> Path:
    """Resolve the rules pack directory from --rules-dir or the environment.

    Mirrors server.py's _resolve_rules_dir: refuses to proceed (SystemExit
    with a clear message) if neither is set, or if the resolved path is not
    an existing directory. A render with no rules pack would either crash
    obscurely or, worse, render a report with rule ids nobody can look up.
    """
    rules_dir_value = rules_dir_arg or os.environ.get("ENGINEERING_AUDIT_RULES_DIR")
    if not rules_dir_value:
        raise SystemExit(
            "engineering-audit-render: no rules pack directory given. Pass --rules-dir <path> "
            "or set the ENGINEERING_AUDIT_RULES_DIR environment variable."
        )
    rules_dir = Path(rules_dir_value).expanduser()
    if not rules_dir.is_dir():
        raise SystemExit(
            "engineering-audit-render: rules pack directory does not exist or is not a "
            f"directory: {rules_dir}"
        )
    return rules_dir


def _load_run_state(run_state_path: Path) -> RunState:
    try:
        return load_run_state_file(run_state_path)
    except RunStateLoadError as exc:
        raise SystemExit(f"engineering-audit-render: {exc}") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="engineering-audit-render",
        description="Re-render report.html from a saved run-state.json.",
    )
    parser.add_argument(
        "run_state_path", help="Path to a run-state.json produced by render_report."
    )
    parser.add_argument(
        "--rules-dir",
        default=None,
        help="Rules pack directory (or set ENGINEERING_AUDIT_RULES_DIR).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for report.html (default: beside the state file).",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    run_state_path = Path(args.run_state_path).expanduser()
    run_state = _load_run_state(run_state_path)

    rules_dir = _resolve_rules_dir(args.rules_dir)
    try:
        pack = load_pack(rules_dir)
    except RulesPackError as exc:
        raise SystemExit(
            f"engineering-audit-render: could not load rules pack: {exc}"
        ) from exc

    out_path = (
        Path(args.out).expanduser()
        if args.out
        else run_state_path.parent / "report.html"
    )

    try:
        written = write_report(run_state, pack, out_path)
    except ReportError as exc:
        raise SystemExit(
            f"engineering-audit-render: could not render report: {exc}"
        ) from exc

    print(written)


if __name__ == "__main__":
    main()
