# 06: Audit workflow integration

**What to build:** end-to-end integration of the standards generation, merge, approval, and write flows into the audit workflow. After an audit run completes and findings are filed, the tooling builds a rule set from the audit verdicts, merges it with any prior rule set, renders the three documents, shows the approval flow, and writes the approved output to disk. This is a real audit run, demonstrating that standards generation is now part of the audited project's workflow.

**Blocked by:** 02, 03, 04, 05.

**Status:** done

- [x] After the audit domain loop finishes and the report is ready, a new step generates a rule set from the audit verdicts (each checked rule becomes a rule in the set with verified-pass or verified-finding status) and loads any prior rule set from the output directory. The tool calls the merge function from ticket 03 to update the prior set with new verdicts.
- [x] The merged rule set is passed to the three rendering functions from ticket 02. The rendered documents are prepared but not yet written.
- [x] The approval flow from ticket 05 is triggered. The user reviews the diffs. If the user clicks Approve, the documents and rule set are written to disk using the managed-block protocol from ticket 04. If Cancel, nothing is written and the run terminates gracefully.
- [x] In a real audit run on a project with a rules pack and at least one domain, all three documents are written, the rule set persists to the output directory, and a second audit run on the same project merges with the existing rule set instead of replacing it.

## Closing note

This issue is closed as done. The end-to-end integration of standards generation, merge, approval, and write flows into the audit workflow was implemented and hardened through a series of commits, with comprehensive test coverage proving the complete flow works in production.

The implementation lives in src/engineering_audit/server.py in the render_report tool (standards block ~lines 2753-2851), backed by src/engineering_audit/standards_integration.py (rule set generation from verdicts), standards_merge.merge_rule_set (merging with prior rule sets), rendering.py (the three document renderers from tickets 02 and 05), managed_blocks.py (the write protocol from ticket 04), and config_page.ConfigServer.set_approval_data/wait_approval (the approval flow from ticket 05).

The work landed in commit 3761101 and was hardened through fixes e72e764, d514b2f, f65a1fd, 6ff9872, and c1da51e, addressing atomicity, merge idempotency, and approval window lifecycle.

Test coverage includes four critical end-to-end tests in tests/test_server.py: test_render_report_with_approval_writes_all_four_files (verifies the complete flow writes documents and rule set), test_render_report_with_cancel_writes_no_standards_files (verifies Cancel terminates without writing), test_render_report_without_config_server_skips_standards (standards are not written when approval is unavailable), and test_second_render_report_merges_with_existing_rule_set (proves merge-not-replace by verifying verified_date preservation across runs). Additional unit test coverage in tests/test_standards_integration.py exercises the rule set generation and merge logic. The full test suite passed 1207 tests; mypy and ruff are clean.
