"""Export scrubbed taster copies of selected rule domains.

Usage: uv run python scripts/export-taster-domains.py --rules-dir <path> --out-dir <path> NN [NN ...]

A taster copy keeps the domain's H1 title and everything from the
``**Trigger:**`` line to the end of the last rule, verbatim: rule text and
source citations are the point of a taster and are not edited. What it drops
is the maintenance machinery that belongs to the private repository: the
Status/Authored/Last refresh/Earliest review due header block (which names
internal proving-run subjects) and the ``## Revision log`` section. A
provenance paragraph is inserted after the H1 so a reader knows this is a
point-in-time export, not the maintained original.

Fails loudly: an unknown domain number, a file without a Trigger line, or an
export that no longer loads through the rules-pack loader all raise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engineering_audit.rules import load_pack  # noqa: E402

PROVENANCE = (
    "*This is a taster copy of one domain from a maintained private rules pack "
    "(16 domains, 260 rules, each with a cited source and a review cadence), "
    "exported as a point-in-time snapshot. The maintained original, its revision "
    "history and its proving records live in the private repository; open an issue "
    "on this repository to ask about access.*"
)


def export_domain(source_path: Path, out_dir: Path) -> Path:
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    h1_index = next(
        (i for i, line in enumerate(lines) if line.startswith("# Domain ")), None
    )
    trigger_index = next(
        (i for i, line in enumerate(lines) if line.startswith("**Trigger:**")), None
    )
    if h1_index is None or trigger_index is None:
        raise SystemExit(
            f"{source_path}: missing the H1 or **Trigger:** line; not a domain document"
        )

    body = lines[trigger_index:]
    revision_index = next(
        (i for i, line in enumerate(body) if line.strip() == "## Revision log"), None
    )
    if revision_index is not None:
        body = body[:revision_index]

    out_lines = [lines[h1_index], "", PROVENANCE, ""] + body
    out_text = "\n".join(out_lines).rstrip() + "\n"

    out_path = out_dir / source_path.name
    out_path.write_text(out_text, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(prog="export-taster-domains")
    parser.add_argument("--rules-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("numbers", nargs="+", help="domain numbers, e.g. 01 05 16")
    args = parser.parse_args()

    if args.out_dir.exists() and not args.out_dir.is_dir():
        raise SystemExit(f"--out-dir is not a directory: {args.out_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    exported: list[Path] = []
    for number in args.numbers:
        matches = sorted(args.rules_dir.glob(f"{int(number):02d}-*.md"))
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one domain file matching {int(number):02d}-*.md in "
                f"{args.rules_dir}, found {len(matches)}"
            )
        exported.append(export_domain(matches[0], args.out_dir))

    # The export is only a rules pack if the loader agrees it is one: a taster
    # that silently fails to load would look like an empty offering.
    pack = load_pack(args.out_dir)
    loaded = {d.number: len(d.rules) for d in pack.domains}
    for path in exported:
        print(f"exported: {path}")
    print(f"loader check: {len(pack.domains)} domain(s), rules per domain {loaded}")


if __name__ == "__main__":
    main()
