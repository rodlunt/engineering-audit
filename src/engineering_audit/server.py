"""MCP stdio server exposing the rules pack to a local coding agent.

Milestone 1 ships the deterministic core only: the rules pack loader and two
read-only, pack-inspection tools. The config page, run tracking and report
tools land in later milestones; AppState carries slots for them now so this
module does not need reshaping when they arrive.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from engineering_audit.rules import RulesPack, RulesPackError, get_domain_text, load_pack

__all__ = ["AppState", "build_server", "main"]


@dataclass
class AppState:
    """Process-wide state for one server run.

    ``config`` and ``run_state`` are unused in milestone 1 (no config page or
    run tracking tool yet) and are left as None; they exist here so later
    milestones extend this dataclass instead of replacing it.
    """

    pack: RulesPack
    config: Any = None
    run_state: Any = None


def _resolve_rules_dir(argv: list[str]) -> Path:
    """Resolve the rules pack directory from --rules-dir or the environment.

    Refuses to proceed (raises SystemExit with a clear message) if neither is
    set, or if the resolved path is not an existing directory: an audit tool
    that silently started with no rules pack would produce a report that
    looks like a clean audit while having checked nothing.
    """
    rules_dir_value: str | None = None
    for index, arg in enumerate(argv):
        if arg == "--rules-dir" and index + 1 < len(argv):
            rules_dir_value = argv[index + 1]
            break
        if arg.startswith("--rules-dir="):
            rules_dir_value = arg.split("=", 1)[1]
            break

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


def build_server(rules_dir: Path) -> tuple[MCPServer, AppState]:
    """Load the rules pack and construct the MCPServer app.

    Raises RulesPackError (or RulesPackParseError) if the pack cannot be
    loaded; this is intentionally not caught here so callers that want the
    exception (tests, alternative entry points) can see it directly. main()
    is the one place that turns it into a clean CLI error.
    """
    pack = load_pack(rules_dir)
    state = AppState(pack=pack)

    mcp = MCPServer("engineering-audit")

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
