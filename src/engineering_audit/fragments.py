"""Generates assistant-context fragments from a rules pack.

Every domain in a rules pack carries a ``**Trigger:**`` sentence: the moment during
development at which that domain's rules become relevant. This module turns those
triggers into two ready-to-merge markdown fragments, one aimed at Codex's
``AGENTS.md`` convention and one at Gemini CLI's ``GEMINI.md`` convention, each
telling the agent to call the ``engineering-audit`` MCP server's ``get_domain`` tool
when a trigger fires.

Content-agnostic: this generator knows nothing about what a rules pack's domains are
about, only the shape :mod:`engineering_audit.rules` already parses. It works against
the fixture pack, the shipped pack, or any third-party pack in the same format.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engineering_audit.rules import RulesPack, RulesPackError, load_pack

__all__ = ["OutDirIsFileError", "generate_fragments", "main"]

_INTRO = (
    "These are decision-time triggers for an engineering rules pack served by the "
    "`engineering-audit` MCP server. Each trigger below names a moment during "
    "development; when it arrives, load the matching domain's rules before deciding "
    "what to do."
)


class OutDirIsFileError(Exception):
    """Raised when --out-dir names an existing file rather than a directory.

    Writing fragments into a path that is already a file would either fail with a
    confusing OS error or, worse, clobber an unrelated file; refusing up front with a
    clear message is the loud-failure choice.
    """


def _fragment_text(pack: RulesPack, merge_target: str) -> str:
    """Build one fragment's full text. Identical across both fragment files except
    the header line naming the file it is meant to be merged into; the intro and the
    per-domain bullets never vary between the two."""
    lines = [
        f"# Engineering Audit Triggers (merge into {merge_target})",
        "",
        _INTRO,
        "",
    ]
    # pack.domains is already sorted by domain number (see load_pack), so this
    # output is deterministic across runs for an unchanged pack.
    for domain in pack.domains:
        lines.append(
            f"- {domain.trigger} When this moment arrives, call the engineering-audit "
            f'MCP tool `get_domain("{domain.id}")` and apply the rules before deciding.'
        )
    lines.append("")
    return "\n".join(lines)


def generate_fragments(rules_dir: Path, out_dir: Path) -> RulesPack:
    """Load the rules pack at ``rules_dir`` and write ``AGENTS-fragment.md`` and
    ``GEMINI-fragment.md`` into ``out_dir``.

    Returns the loaded :class:`~engineering_audit.rules.RulesPack` so a caller (e.g.
    :func:`main`) can report skipped files itself; a silently short fragment must
    never be mistaken for a complete one. ``RulesPackError`` and
    ``RulesPackParseError`` from :func:`~engineering_audit.rules.load_pack` are
    propagated unchanged, never caught here: a broken pack must fail loudly, not
    produce a fragment missing the domains that failed to parse.
    """
    if out_dir.is_file():
        raise OutDirIsFileError(
            f"--out-dir '{out_dir}' is an existing file, not a directory"
        )

    pack = load_pack(rules_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "AGENTS-fragment.md").write_text(
        _fragment_text(pack, "AGENTS.md"), encoding="utf-8"
    )
    (out_dir / "GEMINI-fragment.md").write_text(
        _fragment_text(pack, "GEMINI.md"), encoding="utf-8"
    )
    return pack


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="engineering-audit-fragments")
    parser.add_argument(
        "--rules-dir", required=True, help="Path to a rules pack directory"
    )
    parser.add_argument(
        "--out-dir", required=True, help="Directory to write the fragments into"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    rules_dir = Path(args.rules_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()

    try:
        pack = generate_fragments(rules_dir, out_dir)
    except OutDirIsFileError as exc:
        raise SystemExit(f"engineering-audit-fragments: {exc}") from exc
    except RulesPackError as exc:
        raise SystemExit(
            f"engineering-audit-fragments: could not load rules pack: {exc}"
        ) from exc

    if pack.skipped:
        print(
            f"engineering-audit-fragments: skipped {len(pack.skipped)} file(s) with "
            "no Trigger line (excluded from both fragments):",
            file=sys.stderr,
        )
        for skipped in pack.skipped:
            print(f"  {skipped.path}: {skipped.reason}", file=sys.stderr)

    print(
        f"engineering-audit-fragments: wrote {len(pack.domains)} domain trigger(s) "
        f"to {out_dir}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
