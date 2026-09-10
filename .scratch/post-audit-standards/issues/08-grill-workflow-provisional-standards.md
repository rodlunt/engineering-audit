# 08: Grill workflow, provisional standards

**What to build:** at the end of an engineering-grill interview, all three standards documents are generated from the grill's rule set and marked provisional (intent only, not yet audited against code). A later audit run upgrades them from provisional to verified-pass or verified-finding with evidence. This is phase two of the standards work and demonstrates that the grill can record intent before code exists.

**Blocked by:** 06, 07.

**Status:** done

- [x] At the end of the grill Hot Seat step, after the user confirms the shared understanding, a new step generates a rule set from the grill's captured rules, with all rules marked `status: provisional` and today's date recorded. The rule set is written to the configured audit output directory (or the project's existing audit output location if known).
- [x] The three documents are rendered from the provisional rule set using the same rendering functions as the audit, with all rules carrying the annotation "provisional (grill intent only, not yet audited against code)" to signal to readers that no code has been verified yet.
- [x] Each of the three documents is wrapped in managed-block markers, following the same protocol as the audit. The user does not need to approve at grill time; the documents are written immediately and the grill is complete.
- [x] A later audit run on the same project loads the provisional rule set from the output directory and merges the audit verdicts into it, upgrading provisional rules to verified-pass or verified-finding with the audit date. The grill's intent is preserved and evolved with evidence.

## Closing note

This issue is closed as done. The grill-side provisional standards generation was implemented in commits d4e58de, 18a4305, and b2b60e0.

The tool lives in src/engineering_audit/server.py's `write_grill_standards_artefacts`, registered by `_register_grill_tools`. It parses the grill-captured `grill_rules` JSON array into `StandardsRule` objects (marking every rule `status: provisional` with today's date, defaulting `grill_intent_note` to "Recorded from engineering-grill intent." when the grill did not supply one), renders all three documents via the shared `render_all`/rendering functions with the "grill intent only, not yet audited against code" annotation, and writes them immediately using the managed-block protocol via `write_standards` — no approval step at grill time. All four acceptance criteria were verified by reading the code and by a live run of the tool, confirming the rule set and all three documents are written correctly and that a later audit run merges verdicts into the provisional rule set rather than replacing it.

Test coverage: tests/test_grill_standards.py (TestGenerateProvisionalRuleSet, TestProvisionalRenderingAnnotation, TestWriteProvisionalStandards, TestAuditMergesProvisionalRules) covers the underlying rule-set generation, annotation, writing, and merge-on-audit behaviour. tests/test_grill_mcp_tool.py covers the MCP tool itself, including the new `test_write_grill_standards_artefacts_happy_path_writes_provisional_standards`, which closes the previous gap where no test exercised the tool's own success path — it asserts `success is True`, the returned `rules_count`/`created_date`/`rule_set_path`/`document_paths`, that all four artefacts exist on disk, that managed-block markers and the provisional annotation are present in the rendered content, and that the persisted rule set is `status: provisional` with an explicit `grill_intent_note` preserved on one rule and the default fallback applied to the other.
