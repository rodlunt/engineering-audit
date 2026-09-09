# 07: Stack mismatch detection and user choice

**What to build:** detection and user-prompted resolution of stack mismatches. When the tech stack recorded at grill time does not match the stack observed during audit, the audit stops before the merge step and asks the user which stack is correct. The user's choice determines which stack profiles are loaded and which rules apply. The audit never silently swaps the stack profile, because a silent swap would delete agreed-upon rules without the user's knowledge.

**Blocked by:** 06.

**Status:** done

- [x] Before the merge step in ticket 06, the audit compares the stack_profile field of provisional (grill-time) rules in the prior rule set to the stack observed during the current audit. If they differ (e.g. grill said Python+FastAPI, audit observes Python+Django), the audit halts and presents the mismatch to the user.
- [x] The user is shown both stacks with supporting evidence and asked to choose: Use grill stack (proceed with provisional rules as-is, code may not match intent) or Use audit stack (reload stack profiles for the observed stack, add new rules, drop unchecked stack-specific rules). The choice is explicit and logged.
- [x] If the user chooses to use the audit stack, the tool reloads the stack profiles for the observed stack and includes new stack-profile rules that were not in the prior set. Stack-profile rules that did not exist in the grill are added with verified-pass status from this audit. Grill stack-specific rules are retained but marked as no longer applicable if the new stack does not require them.
- [x] The choice is recorded in the audit output so the user knows which stack was chosen and can revisit the decision if the code changes later.

---

**Note found during issue 05:** the stack-mismatch page has the same unreachability problem the approval page had before issue 05 fixed it. It is correctly routed on GET and renders fine, but nothing ever navigates a real browser to it: there is no `webbrowser.open` call for it, and no script in any template that fetches or navigates to it. In practice, a user is never shown the stack mismatch. This was observed while fixing issue 05 and deliberately left alone there, because it belongs to this issue's scope.

Issue 05 solved the equivalent problem for the approval page with a readiness endpoint (`/approval-ready`) plus a poller added to the page the user already has open (`config-submitted.html`), which navigates the browser across once the target page is ready. The same approach probably applies here: a readiness endpoint for the stack-mismatch page, and a poller on whichever page the user has open at the point the mismatch is detected, rather than relying on anything to actively open a new browser tab.

## Closing note

This issue is closed as done. The four main acceptance criteria — comparing grill-time stack against the observed stack before the merge step, halting and presenting both stacks with evidence, letting the user choose grill or audit stack with the reload/add/retain-marked-inapplicable behaviour, and recording the choice in the audit output — were already implemented and genuinely tested in commits 9dd8592 and dfdbd19, in src/engineering_audit/config_page.py (set_stack_mismatch_data, wait_stack_choice, _render_stack_mismatch_page, _format_stack_evidence, and the /stack-mismatch and /submit-stack-choice routes), src/engineering_audit/templates/stack-mismatch-page.html, and the surrounding stack-detection and merge integration.

The trailing note above, found during issue 05, was not yet fixed: the stack-mismatch page was routed and rendered correctly but unreachable by a real user, since nothing ever navigated a browser to it. Mirroring issue 05's fix for the equivalent problem on the approval page:

- A `/stack-mismatch-ready` GET route was added to `do_GET`, backed by a new `_serve_stack_mismatch_ready` handler that returns 204 once `set_stack_mismatch_data` has been called and 404 otherwise, guarded by `self._lock`, exactly mirroring `_serve_approval_ready`.
- A second, independent poller was added to `config-submitted.html` — the one tab a human still has open once the audit starts running — that polls `/stack-mismatch-ready` and navigates `window.location.href` to `/stack-mismatch` on 204. It runs alongside, not instead of, the existing approval-ready poller, since either an approval or a mismatch (or neither) can come up first depending on the run.
- `_render_submitted_page` now threads through `stack_mismatch_ready_path`, `stack_mismatch_page_path`, and `stack_mismatch_poll_interval_ms` template variables in addition to the existing approval ones. There is only one render site for this template (`_render_submitted_page` in config_page.py), so no other call sites needed updating.

Per the hardening rule in CONTRIBUTING.md ("a check whose failure can be read as a pass does not count"), `_serve_stack_mismatch_ready` only returns 204 when `server._stack_mismatch_data is not None` is genuinely true, read under the lock, matching the existing approval-ready endpoint's guarantee.

Tests: tests/test_stack_mismatch_page.py gained `test_stack_mismatch_ready_endpoint_answers_404_before_data_is_set` and `test_stack_mismatch_ready_endpoint_answers_204_once_data_is_set`. tests/test_config_page.py gained `test_submitted_page_carries_a_script_that_polls_for_stack_mismatch_readiness`, which submits the config form over real HTTP, reads the actually-served config-submitted.html response, and asserts it contains both the new stack-mismatch poller/navigation target and the pre-existing approval poller/navigation target, proving the two coexist rather than one replacing the other. Full suite (`uv run pytest -q`), mypy, and ruff (check and format) are all clean.
