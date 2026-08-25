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
from functools import cached_property
from pathlib import Path

__all__ = [
    "Rule",
    "Domain",
    "SkippedFile",
    "RulesPack",
    "RulesPackError",
    "RulesPackParseError",
    "RulesPackDuplicateIdError",
    "PackMetadata",
    "PACK_TOML_FILENAME",
    "PACK_FORMAT_MIN",
    "PACK_FORMAT_MAX",
    "load_pack",
    "read_pack_metadata",
    "citation",
    "get_domain_text",
]

_TRIGGER_RE = re.compile(r"^\*\*Trigger:\*\*\s*(?P<trigger>.+)$", re.MULTILINE)
_H1_RE = re.compile(
    r"^#\s*Domain\s+(?P<number>\d+)\s*:\s*(?P<title>.+?)\s*$", re.MULTILINE
)
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


class RulesPackDuplicateIdError(RulesPackError):
    """Raised when two different domain files in the same pack define the
    same rule id. _parse_rules already rejects a rule id reused within one
    file; this is the same check repeated across the whole pack, because
    anything that looks a rule up by id (finding attribution, issue filing,
    report rendering) would otherwise resolve to whichever domain happened to
    load last, silently."""


@dataclass(frozen=True)
class Rule:
    """A single audit rule extracted from a domain document."""

    id: str
    title: str
    number: int
    volatility: str | None = None
    source: str | None = None


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

    @cached_property
    def is_v2(self) -> bool:
        """True when this pack has migrated to the rule-footer-format-v2
        contract (a ``Source:``/``Verification:`` split footer).

        Detection is pack-wide, not per-rule: a ``Verification:`` marker
        found anywhere in any domain file is taken as proof the whole pack
        was authored to the v2 contract, where ``Source:`` is self-contained
        and publishable verbatim by construction (see the contract, and
        :func:`citation`). A pack with no ``Verification:`` marker anywhere,
        including any third-party pack this tool has never seen, is treated
        as unmigrated: :func:`citation` falls back to its own v1 safety-net
        capping for every rule in it.
        """
        return any(
            _VERIFICATION_MARKER_RE.search(get_domain_text(domain)) is not None
            for domain in self.domains
        )

    @cached_property
    def rule_index(self) -> dict[str, Rule]:
        """Every rule in this pack, keyed by rule id, built once and cached.

        Several callers (report rendering, issue filing, eval scoring) look
        up many rule ids against the same loaded pack; this is the one
        place that walk happens, so each of them stops hand-rolling its own
        copy of the same dict comprehension.
        """
        return {rule.id: rule for domain in self.domains for rule in domain.rules}

    @cached_property
    def _domain_id_by_rule_id(self) -> dict[str, str]:
        return {rule.id: domain.id for domain in self.domains for rule in domain.rules}

    def domain_id_for_rule(self, rule_id: str) -> str | None:
        """Return the id of the domain that defines rule_id, or None if no
        domain in this pack defines it."""
        return self._domain_id_by_rule_id.get(rule_id)


# --- Pack metadata (issue #170) ---------------------------------------------
#
# An optional pack.toml, sibling to the domain markdown files inside the
# rules directory, lets a pack declare two facts only its authors know: what
# rule-file format it is written in, and the oldest tool release its rules
# assume (its own current version is deliberately not one of them; see
# PackMetadata's docstring). server.py reads this once per run and carries
# the result through RunMeta; report.py compares it against PACK_FORMAT_MIN/
# MAX below and the running tool_version to decide whether a mismatch notice
# fires.

PACK_TOML_FILENAME = "pack.toml"

# The rule-file format versions this build's parser actually reads. is_v2
# above detects a *loaded* pack's footer shape by sniffing its content; this
# constant instead says what value a pack.toml 'format' key may declare for
# this tool to understand it, independent of any one pack. 1 is the original
# Source:-only footer shape (no pack.toml existed to declare it at the time,
# but citation()'s v1 fallback still reads it correctly today); 2 is the
# Source:/Verification: split is_v2 detects. Move this range only when the
# parser in this file actually gains or loses support for a shape, not when
# a pack's own format number changes.
PACK_FORMAT_MIN = 1
PACK_FORMAT_MAX = 2

_PACK_TOML_FORMAT_RE = re.compile(r"^format\s*=\s*(?P<value>\d+)\s*(?:#.*)?$")
_PACK_TOML_REQUIRES_TOOL_RE = re.compile(
    r'^requires_tool\s*=\s*"(?P<value>[^"]*)"\s*(?:#.*)?$'
)
_PACK_TOML_EDITION_RE = re.compile(r'^edition\s*=\s*"(?P<value>[^"]*)"\s*(?:#.*)?$')
_PACK_TOML_FULL_PACK_URL_RE = re.compile(
    r'^full_pack_url\s*=\s*"(?P<value>[^"]*)"\s*(?:#.*)?$'
)


@dataclass(frozen=True)
class PackMetadata:
    """The facts an optional pack.toml may declare about the loaded
    rules pack (issues #170 and #255): only things its authors know and
    git cannot derive.

    ``format``: the rule-file format the pack is written in, compared
    against :data:`PACK_FORMAT_MIN`/:data:`PACK_FORMAT_MAX`.

    ``requires_tool``: the oldest engineering-audit release the pack's
    rules assume, compared against the running tool's version. Deliberately
    not paired with a self-asserted pack *version*: issue #136 declined a
    version marker file because an asserted version is weaker than the
    git-derived one this tool already computes (see server.py's
    ``_git_release_version``). A requirement is a different claim git
    cannot derive and only the pack's authors know, which is why this file
    carries it.

    ``edition`` and ``full_pack_url`` (issue #255): a pack that is a
    deliberate subset of a larger one names what it is ("taster (3 of 16
    domains)") and where the full pack can be requested. The pack declares
    this about itself; the tool never infers it, and in particular never
    keys anything on domain count, because a small custom pack is a
    complete pack, not a partial install. Both absent means the pack made
    no such claim, which is the normal case for every pack that is not a
    shipped subset.

    Any field is None when pack.toml omits that key, or declares it in a
    shape :func:`read_pack_metadata` does not recognise: an unreadable
    value is reported as absent, never guessed at.
    """

    format: int | None
    requires_tool: str | None
    edition: str | None = None
    full_pack_url: str | None = None


def read_pack_metadata(rules_dir: Path) -> PackMetadata | None:
    """Read ``rules_dir/pack.toml``'s known keys (``format``,
    ``requires_tool``, ``edition``, ``full_pack_url``), or None if the file
    is absent or could not be read at all.

    A narrow line-based parser, not a TOML library: this project's floor is
    Python 3.10 and tomllib is 3.11+ (see requires-python in
    pyproject.toml), the project deliberately declares exactly two
    dependencies and ships an SBOM against them, and pack.toml only ever
    needs a handful of flat, known keys. Mirrors scripts/version_pins.py's
    ``read_pyproject_version``, which regex-extracts pyproject.toml's own
    version field for the identical reason.

    Only the known keys are recognised; any other line, including a key
    this parser does not know about, is silently ignored rather than
    treated as a parse failure, because a pack.toml is allowed to grow keys
    a given build predates. A key present but not in the exact shape this
    parser expects (an unquoted requires_tool, a non-numeric format) is
    likewise left as None on that field rather than raising: a malformed or
    unrecognised file is reported as unreadable metadata, never a crash and
    never a fabricated value. Absent (no pack.toml at all) and malformed
    (a pack.toml this parser could not make sense of) are kept distinguishable
    by their return shape: absent is None, malformed is a PackMetadata whose
    fields are None, so a caller that wants to tell the two apart still can,
    even though both currently render the same "nothing to say" notice.
    """
    pack_toml = Path(rules_dir) / PACK_TOML_FILENAME
    if not pack_toml.is_file():
        return None
    try:
        text = pack_toml.read_text(encoding="utf-8")
    except OSError:
        return None

    format_value: int | None = None
    requires_tool_value: str | None = None
    edition_value: str | None = None
    full_pack_url_value: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        format_match = _PACK_TOML_FORMAT_RE.match(line)
        if format_match is not None:
            format_value = int(format_match.group("value"))
            continue
        requires_match = _PACK_TOML_REQUIRES_TOOL_RE.match(line)
        if requires_match is not None:
            requires_tool_value = requires_match.group("value")
            continue
        edition_match = _PACK_TOML_EDITION_RE.match(line)
        if edition_match is not None:
            edition_value = edition_match.group("value") or None
            continue
        full_pack_url_match = _PACK_TOML_FULL_PACK_URL_RE.match(line)
        if full_pack_url_match is not None:
            full_pack_url_value = full_pack_url_match.group("value") or None
            continue
        # Unknown key, or a known key in a shape this parser does not
        # recognise: ignored, per the docstring above.
    return PackMetadata(
        format=format_value,
        requires_tool=requires_tool_value,
        edition=edition_value,
        full_pack_url=full_pack_url_value,
    )


def _slug_from_filename(path: Path) -> str:
    stem = path.stem
    match = _FILENAME_SLUG_RE.match(stem)
    return match.group("slug") if match else stem


def _extract_source(block: str, rule_id_start: int) -> str | None:
    """Extract a rule's cited source, if any, from its metadata footer.

    ``rule_id_start`` is the start of the winning ``Rule id:`` match (the
    last one in the block, per the reasoning in :func:`_parse_rules`: prose
    earlier in the block could reference a different rule's id). The search
    for ``Source:`` is deliberately narrowed to that same footer paragraph,
    not the whole block: the heuristic chosen here is "search backwards
    only within the footer's own line/segment", found by walking back to
    the nearest blank line (or the start of the block) before
    ``rule_id_start``. Restricting the window this way stops an unrelated
    "Source" mentioned in the rule's own body prose, or in trailing
    domain-level text such as a revision log, from being mistaken for the
    footer's own citation. Within that narrowed segment, the *last*
    ``Source:`` occurrence wins, for the same reason the rule id itself
    takes the last match.

    Under the rule-footer-format-v2 contract a footer may also carry a
    ``Verification:`` field after ``Source:``, holding the maintainer's own
    verification trail: what was checked and ruled out, drift corrections
    and their dates, and other detail that is the pack maintainer's
    business, never a report reader's. That field is never part of the
    citation, so capture stops at the first ``Verification:`` marker found
    after ``Source:``, regardless of whether the pack has otherwise
    migrated to v2 (see :attr:`RulesPack.is_v2`): a stray verification
    trail must never leak into a published reference from any pack.

    A footer with no ``Source:`` fragment at all is a deliberately
    unsourced rule (see the rules pack's own sourcing policy): that is a
    legitimate result, so this returns ``None`` rather than raising.
    """
    segment_start = block.rfind("\n\n", 0, rule_id_start)
    segment_start = 0 if segment_start == -1 else segment_start
    footer_segment = block[segment_start:rule_id_start]

    source_matches = list(re.finditer(r"Source:", footer_segment))
    if not source_matches:
        return None

    source_text = footer_segment[source_matches[-1].end() :]
    verification_match = _VERIFICATION_MARKER_RE.search(source_text)
    if verification_match is not None:
        source_text = source_text[: verification_match.start()]
    source_text = " ".join(source_text.split())  # collapse whitespace/newlines
    source_text = source_text.strip().rstrip(",.").strip()
    return source_text or None


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
        winning_id_match = id_matches[-1]
        rule_id = winning_id_match.group("rule_id").upper()
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

        source = _extract_source(block, winning_id_match.start())

        rules.append(
            Rule(
                id=rule_id,
                title=heading_title,
                number=heading_number,
                volatility=volatility,
                source=source,
            )
        )
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
    load_when = (
        " ".join(load_when_match.group("load_when").split()) if load_when_match else ""
    )

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
        raise RulesPackError(
            f"Rules pack directory does not exist or is not a directory: {rules_dir}"
        )

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

    # Each domain file is checked for internal duplicate rule ids as it is
    # parsed (see _parse_rules); this is the same check repeated across the
    # whole pack, once every file is loaded, so a copy-paste that reuses a
    # rule id in a different domain file is caught too.
    seen_rule_id_paths: dict[str, Path] = {}
    for domain in domains:
        for rule in domain.rules:
            first_path = seen_rule_id_paths.get(rule.id)
            if first_path is not None:
                raise RulesPackDuplicateIdError(
                    f"rule id {rule.id} is defined in both {first_path} and {domain.path}; "
                    "duplicate rule ids across files make verdicts unattributable, each "
                    "rule needs its own id"
                )
            seen_rule_id_paths[rule.id] = domain.path

    domains.sort(key=lambda d: d.number)
    return RulesPack(root=rules_dir, domains=domains, skipped=skipped)


_EXCERPT_START_RE = re.compile(r':\s*["“]')
_VERIFICATION_MARKER_RE = re.compile(r"Verification:")
# A sentence boundary is a '.', '!' or '?' immediately followed by
# whitespace or the end of the string. This is a heuristic, not a
# linguistically correct sentence splitter (it will treat a decimal point
# in "v4.0.1. Section" as a boundary, which is harmless here: the only use
# is finding somewhere safe to cut, and a version number's own trailing
# period is a perfectly safe place to cut), and it deliberately does not
# try to special-case abbreviations. See citation() for why the fallback
# when no boundary exists is "publish the full source", never a fragment.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_TRUNCATION_MARKER = "[reference truncated]"
# The bug this ceiling exists to catch (issue #86) shipped a 2,051-character
# reference, a verification narrative that had leaked into Source.
#
# 800 is chosen to match the rules pack's own authoring ceiling, which its
# footer lint enforces, rather than being picked independently. That
# alignment is the point: a tool ceiling tighter than the pack's would
# silently truncate citations the pack considers valid. Measured against the
# migrated standard pack (260 footers): median 169 characters, p90 296, max
# 732, so nothing legitimate is near this limit while the 2,051-character
# failure case is still caught. If the pack's lint ceiling moves, move this
# with it.
_MAX_CITATION_LENGTH = 800


def _cap_before_sentence_containing(text: str, marker_start: int) -> str | None:
    """Return `text` truncated to the end of the last complete sentence that
    finishes strictly before `marker_start`, or None if no sentence boundary
    exists in that span.

    None is a deliberate signal to the caller not to cut here at all: a cut
    made without a sentence boundary to land on is exactly the failure mode
    in issue #86 (fragments like "...rules 4 and 6. Rule 4", "...Table 1 and
    surrounding text. Beneficence", stopped mid-clause because the old
    heuristic cut at the colon itself rather than at a sentence's end).
    Publishing the excerpt whole, or falling through to the hard-ceiling
    truncation below, is preferred over ever risking a mid-clause fragment.
    """
    boundary = None
    for match in _SENTENCE_END_RE.finditer(text[:marker_start]):
        boundary = match.end()
    if boundary is None:
        return None
    capped = text[:boundary].strip()
    return capped or None


def _truncate_with_marker(text: str, limit: int) -> str:
    """Hard-truncate `text` to at most `limit` characters, backing up to the
    nearest word boundary, and append a visible marker.

    This is the fix for the other half of issue #86: a source with no
    excerpt marker at all was previously published whole and unbounded,
    which is how a 2,051-character verification narrative reached a
    client-facing report. A silent cut was the bug; the marker makes the
    truncation visible to the reader instead of quietly shortening the
    reference.
    """
    window = text[:limit]
    last_space = window.rfind(" ")
    if last_space > 0:
        window = window[:last_space]
    window = window.rstrip(" ,;.")
    return f"{window} {_TRUNCATION_MARKER}"


def citation(source: str, *, pack_is_v2: bool = False) -> str:
    """Return the citation part of a rule's parsed source, safe to publish
    verbatim in a client-facing report.

    ``pack_is_v2`` should be the loaded pack's :attr:`RulesPack.is_v2`. It
    selects the *excerpt-capping heuristic* only:

    - **v2 packs**: ``Source:`` is citation-only and self-contained by the
      pack's own authoring contract (any ``Verification:`` trail has
      already been excluded during parsing, see :func:`_extract_source`),
      so no excerpt capping is attempted.
    - **v1 fallback** (the default, and the only behaviour for any pack,
      including a third party's, that has not migrated): this tool cannot
      trust an unmigrated ``Source:`` field to be self-contained, so it
      applies a safety-net heuristic. Pack sources often follow the
      citation with a supporting quote, e.g. 'ISTQB syllabus v4.0.1,
      sections 3.1.2 and 3.1.3: "Static testing can..."'; where such an
      excerpt marker is found, the citation is capped at the end of the
      last complete sentence *before* it, never mid-sentence, and never at
      all if no such sentence boundary exists (see
      :func:`_cap_before_sentence_containing`).

    The :data:`_MAX_CITATION_LENGTH` ceiling then applies to **both**
    branches, unconditionally. It is deliberately not part of the v1-only
    path: pack-wide v2 detection flips on a single ``Verification:`` marker
    found anywhere, so a pack that is only *partly* migrated reports v2
    while still holding unmigrated footers with narrative in ``Source:``.
    Exempting the trusted branch from its own backstop is how a safety net
    ends up disabled exactly where it was still needed, and it would also
    turn a partial migration into a hard :class:`ReportError` at render
    time (see ``report._reference_line``) instead of a visibly truncated
    reference. A ceiling that only guards the path already believed safe is
    not a ceiling.
    """
    text = source
    if not pack_is_v2:
        excerpt_match = _EXCERPT_START_RE.search(text)
        if excerpt_match is not None:
            capped = _cap_before_sentence_containing(text, excerpt_match.start())
            if capped is not None:
                text = capped
    if len(text) > _MAX_CITATION_LENGTH:
        text = _truncate_with_marker(text, _MAX_CITATION_LENGTH)
    return text


def get_domain_text(domain: Domain) -> str:
    """Return the full, unmodified document text for a domain."""
    return domain.path.read_text(encoding="utf-8")
