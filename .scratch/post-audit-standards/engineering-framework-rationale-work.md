# Work required in engineering-framework: rationale field for the human standard

Target repository: `https://github.com/rodlunt/engineering-framework` (local checkout found at
`/Users/charliemac/Desktop/Coding/RodLunt/engineering-framework`, `domains/` directory).

This document is the external half of ticket 10
(`.scratch/post-audit-standards/issues/10-rationale-field-human-standard.md`). It does not
belong in this repository; per `CONTRIBUTING.md` ("Rules pack content"), rule content lives in
engineering-framework, not here.

## Why this is needed, and what unblocks when it lands

The human coding standard (`render_human_standard` in
`src/engineering_audit/rendering.py`) is meant to show, per rule: status, full text, rationale
(the why), and audit findings. Status, full text and findings already render. As of this audit,
the **local loader and renderer side of ticket 10 is already built**: `Domain.rationale` and
`Rule.rationale` exist in `src/engineering_audit/rules.py`, the parser reads a `**Rationale:**`
domain block and a rule-footer `Rationale:` field defensively, and
`render_human_standard` already looks both up and renders a `**Rationale:**` block per rule
when either is present (see `src/engineering_audit/rendering.py:220-238`). Two tests already
cover this (`tests/test_rendering.py`, `test_render_human_standard_renders_rationale_when_pack_has_it`
and `test_render_human_standard_omits_rationale_section_when_pack_lacks_it`).

The only thing missing is data: the maintained pack in `domains/` has **no** `**Rationale:**`
domain block and **no** rule-footer `Rationale:` field anywhere. A `grep -rn "Rationale"` across
`domains/*.md` today turns up exactly two hits, both inside quoted ISO 9001 source text in
`domains/12-ethics-professional-judgement.md` (lines 216 and 230), which are not the metadata
field at all (see the "false positive check" note below).

Once engineering-framework adds the field and backfills text, the human standard will render a
`**Rationale:**` section under every rule with no further work on the engineering-audit side.

## The exact format change, BEFORE and AFTER

Real domain file: `domains/01-data-modelling.md`, rule 1 (`D01-R01`). This is copied verbatim
from the current pack, then shown with the two rationale additions the loader expects.

### BEFORE (current, real file)

```markdown
# Domain 01: Designing a Data Model

**Status:** PROVEN (proving run complete 2026-08-05 against the finance tool; see
`proving-runs/2026-08-05-domain-01-finance-tool.md`; eval set seeded at
`evals/01-data-modelling.md`)
**Authored:** 2026-08-05 by claude-fable-5 (knowledge cutoff January 2026)
**Last refresh:** 2026-08-08 by claude-opus-5 via agent dispatch, September review cycle: ...
**Earliest review due:** 2027-02-08 (fastest tier present: fast, 6 months)

**Trigger:** you are about to design or change how data is stored: modelling entities or
facts, choosing keys, constraints or nullability, mapping a model to tables, normalising,
writing DDL or a migration, wiring relationships and cascades in code, or granting database
access.

**Load this when:** designing a new schema or data model, adding a table, column or
relationship to an existing one, mapping a class model to persistence, choosing ORM
relationship configuration, or granting database access. Run the group that matches the
moment; run all five for a new schema.

Rules follow the checkpoint shape defined in `METHOD.md`. Each carries a stable rule
identifier so its wording can change without breaking references to it.

---

## A. Before you draw: get the facts right

### 1. Decompose the requirement into elementary facts before drawing or typing anything.

An elementary fact is an atomic assertion that particular objects play particular roles:
"Employee 101 works in Department Sales". Verbalising the domain this way forces the two
questions a rushed schema skips: what are the object types, and how is each one identified
(its reference scheme)? A schema that starts at `CREATE TABLE` inherits whatever shape the
first UI form or API payload suggested, and every downstream rule in this document gets harder
to apply because the facts were never separated. This is CSDP step 1, taught as the most
important step of the whole procedure.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role
Modeling: an overview*, orm.net), CSDP step 1.*
*Verification: Rule id: D01-R01. Volatility: durable. Verified: 2026-08-05 (current, primary
source).*
```

### AFTER (with the rationale field added, both levels)

Two additions, shown inline with `+` for clarity (do not literally include the `+` characters
in the file):

```markdown
**Trigger:** you are about to design or change how data is stored: modelling entities or
facts, choosing keys, constraints or nullability, mapping a model to tables, normalising,
writing DDL or a migration, wiring relationships and cascades in code, or granting database
access.

+ **Rationale:** Facts and constraints modelled incorrectly at design time are expensive to
+ correct once code, migrations and integrations depend on the wrong shape; this domain exists
+ to catch modelling mistakes before they are built on.

**Load this when:** designing a new schema or data model, adding a table, column or
relationship to an existing one, mapping a class model to persistence, choosing ORM
relationship configuration, or granting database access. Run the group that matches the
moment; run all five for a new schema.
```

and, on the rule's own footer:

```markdown
*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role
Modeling: an overview*, orm.net), CSDP step 1.*
*Verification: Rule id: D01-R01. Volatility: durable. Verified: 2026-08-05 (current, primary
+ source). Rationale: Separating facts before typing prevents the single most common source of
+ schema rework: a table shape that quietly bakes in one UI form's assumptions instead of the
+ domain's actual object types and identifiers.*
```

Both additions are read from the **same file**, in the **same pass**, by the existing loader;
no new file, no new section marker, no change to file naming or the `**Trigger:**`/`**Load this
when:**` line shapes is required.

## Field name and level: domain, rule, or both

Both. The loader (`src/engineering_audit/rules.py`) reads two distinct fields, and either may
be present independently of the other:

- **Domain level** — field name `**Rationale:**` (bold, exact spelling, exact colon
  placement), a standalone block on its own line(s), matched by
  `_DOMAIN_RATIONALE_RE = r"\*\*Rationale:\*\*\s*(?P<rationale>.*?)(?:\n\s*\n|\Z)"`. It follows
  the same shape as the existing `**Load this when:**` block: bold marker, free text, ends at
  the next blank line (or end of file). Placement in the file is not otherwise constrained by
  the regex (it is a `re.MULTILINE`/`re.DOTALL` search over the whole document), but for
  consistency with `**Trigger:**` and `**Load this when:**` it should sit in the domain's
  metadata header, before the `---` separator and the first `##` heading. Stored on
  `Domain.rationale: str | None`.

- **Rule level** — field name `Rationale:` (no leading `**`, since it lives inside the italic
  footer line alongside `Source:`, `Rule id:`, `Volatility:` and `Verified:`), appended after
  the existing `Rule id: ... Volatility: ... Verified: ...` sequence in the rule's
  `*Verification:*` line (v2-format packs; this pack is v2 per `pack.toml`'s `format = 2`).
  Matched by `_extract_rationale`, which scopes its search to the rule's own footer paragraph
  (from the winning `Rule id:` match to the next blank line), so a rule whose body prose
  legitimately uses the word "Rationale" (for example a rule about ADR conventions, which
  discusses "Context, Decision, Rationale, Consequences") is never mistaken for the footer
  field — this is exercised directly by
  `tests/test_rules.py::test_rule_rationale_footer_field_not_matched_from_body_prose`. Stored
  on `Rule.rationale: str | None`. Unlike `Volatility:`, capture does not stop at the first
  full stop, so a multi-sentence rationale is captured whole
  (`test_rule_rationale_is_not_truncated_at_the_first_sentence`).

There is no single combined "one rationale field" option in the loader as written: domain and
rule rationale are two independent fields, and `render_human_standard` renders both together
under one `**Rationale:**` heading when either is present (`- Domain: ...` / `- Rule: ...`
bullets).

## Backwards-compatibility contract (why old packs are safe)

The loader treats both fields as fully optional and reads them defensively:

- If a domain file has no `**Rationale:**` block, `Domain.rationale` is `None` (not `""`).
  `_parse_domain` only sets it when the regex matches; see
  `src/engineering_audit/rules.py:526-531`.
- If a rule's footer has no `Rationale:` fragment, `Rule.rationale` is `None`. `_extract_rationale`
  returns `None` when its regex finds no match; see `src/engineering_audit/rules.py:424-426`.
- `None` is used deliberately rather than an empty string, so "field never declared" and
  "field declared but empty" stay distinguishable if that distinction is ever needed later.
- `render_human_standard` renders no `**Rationale:**` heading at all when both are `None`/falsy
  — no blank section, no "not yet available" placeholder (`src/engineering_audit/rendering.py:231`).
- Nothing about loading, parsing, or any other renderer (`render_agent_standard`,
  `render_policy`) depends on this field at all.

**Conclusion for the framework repo maintainer:** every domain file that ships today, with zero
rationale content anywhere, already loads and renders correctly under the current
engineering-audit release. Adding the field is purely additive: existing files do not need to
change format, only gain new lines, and can be migrated domain-by-domain or rule-by-rule
without ever breaking a partially-migrated pack.

## Checklist of the work

1. **Schema/format decision** (one-time, framework repo):
   - Confirm the exact field names above (`**Rationale:**` for domain, `Rationale:` inside the
     `*Verification:*` line for rule) as the pack's own documented contract — i.e. add a
     "Rationale field" subsection to `METHOD.md` alongside the existing "Footer format" section
     (`METHOD.md:42-77`), analogous to how `Rule id:`, `Volatility:` and `Verified:` are
     documented under "Currency" (`METHOD.md:100-140`).
   - Note in that documentation how a rule's `Rationale:` differs in intent from the existing
     "one short paragraph of why" that `METHOD.md`'s Rule shape already requires in every
     rule's body text (`METHOD.md:34-35`). Today's body paragraph already explains the concrete
     failure a rule prevents, and that whole paragraph is what `text_body` (rendered as "full
     text") already carries into the human standard. **This is a genuinely open question for
     the framework maintainer, not something this document can resolve**: whether the new
     `Rationale:` field should restate/compress that same why for a reader who only sees the
     rendered standard's dedicated section (duplication, but robust to `text_body` extraction
     changing), or should say something the body paragraph does not — e.g. why this specific
     rule as opposed to a plausible alternative, or why it is scoped the way it is. Either
     choice is compatible with the loader; the loader does not enforce a distinction.
   - Optionally extend `wiring/check-rule-footers.sh` (which already enforces footer format
     rules 2 and 3 from `METHOD.md`, see `METHOD.md:76-77`) to check the new field once adopted
     — see "Validation" below.

2. **Backfill rationale text**, once the format is agreed. Current pack size, counted directly
   from the local checkout (`domains/*.md`, excluding `pack.toml`):

   | Domain file | Rule count (`###` headings) |
   |---|---|
   | 01-data-modelling.md | 15 |
   | 02-requirements-elicitation.md | 16 |
   | 03-modelling-before-building.md | 15 |
   | 04-code-structure-patterns.md | 13 |
   | 05-testing-strategy.md | 18 |
   | 06-repo-branches-cicd.md | 15 |
   | 07-secure-coding.md | 16 |
   | 08-threat-modelling-risk.md | 15 |
   | 09-incident-response.md | 16 |
   | 10-api-design.md | 14 |
   | 11-architecture-deployment.md | 16 |
   | 12-ethics-professional-judgement.md | 17 |
   | 13-estimating-and-pricing.md | 16 |
   | 14-fault-diagnosis.md | 19 |
   | 15-interface-design.md | 17 |
   | 16-presenting-data.md | 21 |
   | 17-coding-standards.md | 11 |
   | **Total** | **17 domains, 270 rules** |

   Every one of the 17 domains needs one `**Rationale:**` block; every one of the 270 rules
   needs one `Rationale:` fragment in its footer, for the human standard to show rationale
   everywhere rather than patchily. Partial backfill is safe (see the compatibility contract
   above) but leaves some rules in the rendered standard with no rationale section, which will
   look inconsistent to a reader working through the whole document.

3. **Verify against the local tool**: run the audit tool's `render_human_standard` (or the
   existing `tests/test_rendering.py` fixtures as a template) against a handful of migrated
   domain files locally to confirm the `**Rationale:**` section renders as expected before
   backfilling the remaining 260-odd rules. No engineering-audit release changes are needed to
   do this verification — the current release already reads and renders the field.

## What good rationale text looks like

A rationale answers "why does this rule exist" — the cost of *not* following it, or the
reasoning that makes it the right rule rather than some other one — not "what does this rule
say" restated in different words. It should be readable on its own by someone who has not read
the rule's body paragraph.

**Worked example**, using `D01-R02` ("Validate every fact-type split with a population check
before trusting it") from the real pack:

- **Bad** (restates the rule, adds nothing): "This rule exists because you should validate
  fact-type splits with a population check."
- **Good**: "A wrongly-split fact type (for example treating a ternary Student-Unit-Grade
  relationship as two separate binary ones) looks identical to a correct split until real data
  is joined back together; without this check the error ships silently and surfaces later as a
  grade that cannot be attributed to the right unit, which is far more expensive to unpick once
  code and reports depend on the wrong shape."

The good example: names the concrete failure mode, explains why the failure is easy to miss
without the check, and says why catching it early (rather than fixing it later) is what
justifies the rule's cost. One or two sentences is enough; it should not simply repeat the
rule's own body paragraph or its `Source:` citation.

## Validation the framework repo should add

- Extend `wiring/check-rule-footers.sh` to optionally check for a `Rationale:` fragment in the
  footer once the field is adopted pack-wide, mirroring how it already enforces the `Source:`
  self-containment and 800-character cap (`METHOD.md:76-77`). Suggested behaviour: warn (not
  fail) during a migration window while the backfill is incomplete, then fail once the pack
  declares itself fully migrated (the same pattern `pack.toml`'s `format` key already uses to
  mark the `Source:`/`Verification:` split as pack-wide, see `pack.toml`'s comments in the local
  checkout).
- Consider a length ceiling on `Rationale:` analogous to `Source:`'s 800-character cap
  (`METHOD.md:64-65`), so a verification narrative does not leak into this field the way it
  once did into `Source:` (`METHOD.md:79-81`). The engineering-audit loader itself imposes no
  length limit on rationale (`_extract_rationale` captures the whole footer paragraph after the
  marker with no cap), so any ceiling has to be enforced on the framework side if one is
  wanted.
- Add a test to the framework repo's own test suite (`tests/` there) asserting every domain
  file has both a `**Rationale:**` block and every rule has a `Rationale:` footer fragment,
  once the backfill is declared complete — the equivalent of the existing footer-shape checks,
  so a future new rule cannot be added without one.

## Note on a possible false positive

`grep -rn "Rationale" domains/*.md` in the current pack returns two hits, both in
`domains/12-ethics-professional-judgement.md` (lines 216 and 230), inside quoted ISO 9001 source
text (`... Rationale: "Sustained success is achieved when ..." Rule id: D12-R14 ...`). This is
**not** the metadata field: it is quoted prose that happens to contain the word "Rationale:"
*before* the rule's own `Rule id:` marker in the same sentence. `_extract_rationale` only
searches forward from the winning `Rule id:` match to the next blank line, so this occurrence
falls outside its search window and is correctly not picked up (`Rule.rationale` is `None` for
both `D12-R14` and `D12-R15` today). Flagged here only so whoever does the backfill on domain 12
does not mistake this quoted text for an already-present rationale field.
