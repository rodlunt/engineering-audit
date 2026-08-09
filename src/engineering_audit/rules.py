"""Loader for a rules pack directory.

A rules pack is a directory of markdown files, one per domain, each named
``NN-slug.md``. A domain file that carries a ``**Trigger:**`` line is parsed into
a :class:`Domain`; a domain file with no ``**Trigger:**`` line is skipped, but the
skip is recorded rather than silently dropped (see the hardening note below).

Hardening note: a rules pack loader is exactly the kind of check a skipped-check
bug hides inside. A file with no trigger is a legitimate, expected skip (matching
the private generator's own behaviour of only shipping triggered domains) and is
reported as such. A file that *does* declare a trigger but cannot be parsed is a
different thing entirely: a broken pack, not an absent domain, so it raises
:class:`RulesPackParseError` rather than being swallowed into the skip list. A
caller that only inspects ``RulesPack.domains`` and ``RulesPack.skipped`` can
always tell "no domains loaded" apart from "some domains loaded, and a broken one
went missing along the way".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Rule",
    "Domain",
    "SkippedFile",
    "RulesPack",
    "RulesPackError",
    "RulesPackParseError",
    "load_pack",
    "get_domain_text",
]

_TRIGGER_RE = re.compile(r"^\*\*Trigger:\*\*\s*(?P<trigger>.+)$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s*Domain\s+(?P<number>\d+)\s*:\s*(?P<title>.+?)\s*$", re.MULTILINE)
_LOAD_WHEN_RE = re.compile(
    r"\*\*Load this when:\*\*\s*(?P<load_when>.*?)(?:\n\s*\n|\Z)", re.DOTALL
)
# Rule headings come in numbered series that may carry a letter prefix, e.g.
# '### 8. Title' and '### T1. Title' (tier-2 rules in the real pack use a T
# series). The label keeps the full token; Rule.number keeps the digits.
_RULE_HEADING_RE = re.compile(
    r"^###\s*(?P<label>[A-Za-z]*\d+)\.\s*(?P<title>.+?)\s*$", re.MULTILINE
)
_ANY_H3_RE = re.compile(r"^###\s.*$", re.MULTILINE)
_RULE_ID_RE = re.compile(r"Rule id:\s*(?P<rule_id>[A-Za-z0-9]+-[A-Za-z0-9]+)\s*\.")
_VOLATILITY_RE = re.compile(r"Volatility:\s*(?P<volatility>[^.]+)\.")
_FILENAME_SLUG_RE = re.compile(r"^\d{2}-(?P<slug>.+)$")


class RulesPackError(Exception):
    """Raised for a pack-level problem: a bad directory, or a pack that loaded
    zero usable domains (which looks exactly like an empty pack to a caller
    that only checks truthiness, so it is raised rather than returned quietly)."""


class RulesPackParseError(RulesPackError):
    """Raised when a file that declares a **Trigger:** line cannot be parsed as
    a valid domain. A triggered file is a promise that a domain lives here; a
    broken promise must be loud, never a silent drop from the pack."""


@dataclass(frozen=True)
class Rule:
    """A single audit rule extracted from a domain document."""

    id: str
    title: str
    number: int
    volatility: str | None = None


@dataclass(frozen=True)
class Domain:
    """A single rule domain: one markdown document in the rules pack."""

    id: str
    number: int
    slug: str
    title: str
    trigger: str
    load_when: str
    rules: list[Rule]
    path: Path


@dataclass(frozen=True)
class SkippedFile:
    """A file in the rules pack directory that was not loaded as a domain, and
    why. Kept distinct from a parse failure: this is an expected, reported
    skip, not a hidden error."""

    path: Path
    reason: str


@dataclass(frozen=True)
class RulesPack:
    """The result of loading a rules pack directory."""

    root: Path
    domains: list[Domain] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    def get_domain(self, domain_id: str) -> Domain | None:
        for domain in self.domains:
            if domain.id == domain_id:
                return domain
        return None


def _slug_from_filename(path: Path) -> str:
    stem = path.stem
    match = _FILENAME_SLUG_RE.match(stem)
    return match.group("slug") if match else stem


def _parse_rules(path: Path, text: str) -> list[Rule]:
    headings = list(_RULE_HEADING_RE.finditer(text))
    if not headings:
        raise RulesPackParseError(
            f"{path}: declares a Trigger but has no '### N. Rule title' headings"
        )

    # Every '###' heading must be a recognised rule heading. A heading the
    # rule pattern does not match would otherwise have its whole block
    # silently absorbed into the previous rule (wrong footer id, hidden
    # rules), which is exactly the silent drop this loader exists to prevent.
    all_h3_lines = [line.rstrip() for line in _ANY_H3_RE.findall(text)]
    matched_lines = {m.group(0).rstrip() for m in headings}
    unmatched = [line for line in all_h3_lines if line not in matched_lines]
    if len(all_h3_lines) != len(headings):
        detail = f": {unmatched[:5]}" if unmatched else ""
        raise RulesPackParseError(
            f"{path}: {len(all_h3_lines) - len(headings)} '###' heading(s) do not match "
            f"the '### <label>. <title>' rule-heading shape and would be silently "
            f"absorbed into the previous rule{detail}"
        )

    rules: list[Rule] = []
    seen_ids: dict[str, str] = {}
    for index, heading_match in enumerate(headings):
        start = heading_match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end]
        heading_label = heading_match.group("label")
        heading_number = int(re.sub(r"\D", "", heading_label))
        heading_title = heading_match.group("title").strip()

        # A rule block can carry a prose cross-reference to another rule's id
        # before its own metadata footer (e.g. "as covered by Rule id: ..."),
        # so take the *last* match in the block: the footer line is always
        # the final occurrence, never the first.
        id_matches = list(_RULE_ID_RE.finditer(block))
        if not id_matches:
            raise RulesPackParseError(
                f"{path}: rule {heading_number} ('{heading_title}') has no parseable "
                "'Rule id: ...' metadata line"
            )
        rule_id = id_matches[-1].group("rule_id").upper()
        if rule_id in seen_ids:
            raise RulesPackParseError(
                f"{path}: rule id {rule_id} appears on both '{seen_ids[rule_id]}' and "
                f"'{heading_title}'. Duplicate ids make verdicts unattributable; each "
                "rule needs its own id."
            )
        seen_ids[rule_id] = heading_title

        volatility_match = _VOLATILITY_RE.search(block)
        volatility = (
            volatility_match.group("volatility").strip().lower()
            if volatility_match is not None
            else None
        )

        rules.append(Rule(id=rule_id, title=heading_title, number=heading_number, volatility=volatility))
    return rules


def _parse_domain(path: Path, text: str, trigger: str) -> Domain:
    h1_match = _H1_RE.search(text)
    if h1_match is None:
        raise RulesPackParseError(
            f"{path}: declares a Trigger but has no '# Domain NN: Title' first heading"
        )
    number = int(h1_match.group("number"))
    title = h1_match.group("title").strip()

    load_when_match = _LOAD_WHEN_RE.search(text)
    load_when = " ".join(load_when_match.group("load_when").split()) if load_when_match else ""

    rules = _parse_rules(path, text)

    return Domain(
        id=f"d{number:02d}",
        number=number,
        slug=_slug_from_filename(path),
        title=title,
        trigger=trigger.strip(),
        load_when=load_when,
        rules=rules,
        path=path,
    )


def load_pack(rules_dir: Path) -> RulesPack:
    """Load every domain file in ``rules_dir``.

    A file with no ``**Trigger:**`` line is skipped and recorded in
    ``RulesPack.skipped``. A file that declares a trigger but cannot be parsed
    raises :class:`RulesPackParseError` immediately: it never becomes a silent
    gap in ``RulesPack.domains``.
    """
    rules_dir = Path(rules_dir)
    if not rules_dir.is_dir():
        raise RulesPackError(f"Rules pack directory does not exist or is not a directory: {rules_dir}")

    domain_paths = sorted(rules_dir.glob("*.md"))
    if not domain_paths:
        raise RulesPackError(f"Rules pack directory contains no .md files: {rules_dir}")

    domains: list[Domain] = []
    skipped: list[SkippedFile] = []
    for path in domain_paths:
        text = path.read_text(encoding="utf-8")
        trigger_match = _TRIGGER_RE.search(text)
        if trigger_match is None:
            skipped.append(SkippedFile(path=path, reason="no **Trigger:** line found"))
            continue
        domains.append(_parse_domain(path, text, trigger_match.group("trigger")))

    if not domains:
        raise RulesPackError(
            f"Rules pack directory has {len(skipped)} file(s), none with a "
            f"**Trigger:** line, so zero domains loaded: {rules_dir}"
        )

    domains.sort(key=lambda d: d.number)
    return RulesPack(root=rules_dir, domains=domains, skipped=skipped)


def get_domain_text(domain: Domain) -> str:
    """Return the full, unmodified document text for a domain."""
    return domain.path.read_text(encoding="utf-8")
