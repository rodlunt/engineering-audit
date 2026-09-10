# 10: Rationale field for rules, so the human standard can explain why

**What to build:** a `rationale` (or equivalently named "why") field on domains and rules in the rules pack schema, backfilled with text for existing content, and read by `render_human_standard` so the human-readable standard document shows not just what a rule says but why it exists.

**Blocked by:** an external change to the rules pack repository. This cannot be finished inside this repository alone; see below.

**Status:** done

This came out of issue 02. Only part of the work can happen here.

The facts:

- `render_human_standard` in `src/engineering_audit/rendering.py:142` accepts an optional `rules_pack` parameter and never reads from it. Its docstring (`src/engineering_audit/rendering.py:152`) says rationale rendering is planned but currently unused ("Optional RulesPack for future rationale lookup (not yet used)").
- The human coding standard is the verbose document engineers actually read. It is meant to show, per rule: status, full text, rationale (the why), and any audit findings. Everything except the rationale currently works.
- The blocker: the rules pack has no rationale field anywhere to read. The `Domain` class in `src/engineering_audit/rules.py:96` has fields for `id`, `number`, `slug`, `title`, `trigger`, `load_when`, `rules`, and `path`. There is no `rationale`, `why`, or `intent` field on `Domain`, and the `Rule` type in the same module has no equivalent field either.
- Per `CONTRIBUTING.md` ("Rules pack content"), rule content does not live in this repository: "The tooling here reads any rules directory in the documented format, and the maintained pack has its own home; rule suggestions belong in Ideas Discussions, not PRs here." Adding the field to the schema and writing the rationale text for existing domains and rules cannot be done in this repository.

What is external (out of scope for this repository, tracked separately against the rules pack):

- Add a `rationale` (or equivalently named) field to the rules pack's schema for both domains and individual rules.
- Backfill rationale text for every existing domain and rule in the maintained pack.

What is local (in scope here, and blocked on the above landing first):

- [x] Once the rules pack schema carries the field, extend the loader in `src/engineering_audit/rules.py` (the `Domain` dataclass and the `Rule` type) to read and expose it, defaulting to `None` or empty for packs that predate the field so old packs still load without error.
- [x] `render_human_standard` in `src/engineering_audit/rendering.py` reads the rationale from the `rules_pack` argument it already accepts and renders it alongside each rule's status, full text, and any audit findings, in the human-readable document.
- [x] When a domain or rule has no rationale (an old pack, or a pack that has not backfilled it yet), the document renders sensibly without it: no blank heading, no placeholder text implying something is broken, just the absence of that section for that rule.
- [x] A test using a rules pack fixture that includes rationale text on at least one domain and one rule asserts the rendered human standard contains that rationale text in the expected place, and a second test using a fixture without the field asserts rendering still succeeds and omits the rationale section cleanly.

## Closing note

The local half of this ticket is done. `Rule.rationale: str | None = None` (`src/engineering_audit/rules.py:114`) and `Domain.rationale: str | None = None` (`src/engineering_audit/rules.py:132`) both default to `None`, so a pack that predates the field — which today means every pack, including the maintained one — loads without error and simply carries no rationale. Domain-level rationale is read by `_DOMAIN_RATIONALE_RE` off a `**Rationale:**` block, mirroring the existing `**Load this when:**` shape. Rule-level rationale is read by the `_extract_rationale` helper (same module), which scopes its search to the winning rule's own footer paragraph so it cannot be fooled by a rule whose body prose happens to contain the word "Rationale".

`render_human_standard` (`src/engineering_audit/rendering.py:224-238`) looks up `rules_pack.get_domain(...)` and `rules_pack.rule_index.get(...)` for the current rule, and emits a `**Rationale:**` block — with `- Domain:` and `- Rule:` bullets for whichever of the two are present — after the full rule text (line 217) and before the findings section (line 241). The omission behaviour lives at `rendering.py:231`: the heading and body are appended only `if domain_rationale or rule_rationale`, so a rule or pack with neither produces no heading, no blank section and no placeholder text.

Both behaviours are covered by tests using pack fixtures rather than the real pack: `test_render_human_standard_renders_rationale_when_pack_has_it` (`tests/test_rendering.py:1110-1165`) builds a small fixture pack with a domain-level `**Rationale:**` block and a rule-level footer `Rationale:` field, and asserts both render, in order, after the rule's full text; `test_render_human_standard_omits_rationale_section_when_pack_lacks_it` (`tests/test_rendering.py:1167-1198`) uses the existing fixture pack (which has neither field) and asserts no `**Rationale:**` heading appears anywhere in the output.

What is honestly NOT done: the external half of this ticket has not landed. The maintained rules pack (`rodlunt/engineering-framework`) still has no `**Rationale:**` domain block and no rule-footer `Rationale:` field anywhere in `domains/*.md` — the two `grep` hits for "Rationale" in that pack are quoted ISO 9001 source text in `domains/12-ethics-professional-judgement.md`, not the metadata field. That means that, in practice, running this tool against the real, maintained pack today renders no rationale section at all: the loader and renderer are proven correct, but there is nothing for them to read yet. The local implementation was verified with test fixtures specifically so it would not have to wait on the external pack landing first. The full scope of the remaining work — schema/format decision, backfilling all 17 domains and 270 rules, and validation tooling — is written up in `.scratch/post-audit-standards/engineering-framework-rationale-work.md`, which targets the separate `rodlunt/engineering-framework` repository and is out of scope for this one per `CONTRIBUTING.md` ("Rules pack content").
