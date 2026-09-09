# 09: A decision can be accepted before the approval window ever opens

**What to build:** a way for the submission handler to tell "the approval window has not opened yet" apart from "the approval window is open" and "the approval window has closed", and a rejection of any decision that arrives in that first state, so the approval flow's whole point (nothing is written until a human has looked at the diffs) cannot be defeated by a submission that races ahead of the window.

**Blocked by:** None.

**Status:** done

This was confirmed real by reading the code during issue 05, and deliberately left unfixed there as out of scope. It is a genuine gap in the approval flow, not a theoretical one: it means the review step the whole flow exists for can be skipped entirely, silently, by a request that happens to arrive at the wrong time.

The facts, verified:

- `ConfigServer._csrf_token` is generated once, in `ConfigServer.__init__` (`src/engineering_audit/config_page.py:332`), and is embedded in the very first config page rendered by `_render_form`. That page is served at `/`, before anything about the audit run has happened.
- The config server is started from `src/engineering_audit/server.py:1959` (`url = config_server.start()`), long before the audit domain loop runs.
- `set_approval_data` is not called until roughly `src/engineering_audit/server.py:2824` (`run.config_server.set_approval_data(diffs, summary_counts)`), after the entire audit run, the merge, and the render have all completed.
- `_approval_closed_reason` (`src/engineering_audit/config_page.py:354`) is `None` both before the approval window has ever opened and while it is genuinely open. `_handle_standards_submission` (`src/engineering_audit/config_page.py:543`) reads this single field to decide whether to accept a decision, so it cannot distinguish "not yet open" from "open". It also never checks that `self._approval_data` is set before recording a decision.
- Consequence: a POST to `/submit-standards`, carrying the long-lived CSRF token from the very first page load, sent at any point after the config server starts and before `set_approval_data` runs, is accepted. `server._approval_action` is set. The response says the standards update was processed. Nothing was ever shown to the user.
- When the real approval window later opens (`set_approval_data` runs), `wait_approval` (`src/engineering_audit/config_page.py:1077`) returns that pre-recorded decision immediately, without ever serving the approval page or its diffs. The user never sees the three documents they were meant to review before anything is written to disk.

This defeats the entire premise of the approval flow described in issue 05: that a human looks at the diffs before anything is written.

- [x] `ConfigServer` tracks approval-window state as three distinct states: not yet open, open, closed (with a reason). A single nullable "closed reason" field is not sufficient, because it cannot represent "not yet open" as distinct from "open"; a field that actively records "not opened yet" is required, not something inferred from the reason being empty.
- [x] `_handle_standards_submission` rejects a submission that arrives before the window has opened. The rejection is a clear failure, not a response that could be misread as success: an HTTP error status (not 200), and body text that plainly says the review page is not ready yet rather than anything resembling "processed". Per this repository's stated hardening rule, a check whose failure can be read as a pass does not count, so this response must be unmistakable as a rejection.
- [x] A rejected pre-window submission does not set `server._approval_action` and does not otherwise pre-record a decision anywhere that `wait_approval` could later pick up. When the real window opens later, `wait_approval` serves the approval page and waits for a genuine decision; it does not return instantly with a stale one.
- [x] `_handle_standards_submission` also checks that `self._approval_data` is set before accepting a decision, independent of the closed-window check, since the two are logically separate guards.
- [x] A test sends a `/submit-standards` POST with a valid CSRF token before `set_approval_data` has ever been called, and asserts: the response is a non-2xx status, the response body is honest about the window not being open, `server._approval_action` remains unset afterwards, and a subsequent call to `set_approval_data` followed by `wait_approval` blocks for a real decision rather than returning immediately.
- [x] A test drives the normal flow end to end (server starts, `set_approval_data` is called, a submission arrives while the window is open, `wait_approval` returns that decision) and passes, proving the new state tracking has not broken the working case.

## Closing note

Fixed by replacing the nullable `_approval_closed_reason` field with a genuine three-value state, `_ApprovalWindowState` (`NOT_YET_OPEN`, `OPEN`, `CLOSED`), stored in a new `ConfigServer._approval_window_state` attribute (`src/engineering_audit/config_page.py`, initialized in `__init__`). `set_approval_data` now transitions the state to `OPEN` under `self._lock` in the same critical section that sets `_approval_data`. `wait_approval` transitions it to `CLOSED` in both of its existing terminal cases (timeout and consumption), alongside the reason text it already set.

`_handle_standards_submission` now reads `window_state`, `closed_reason`, and whether `self._approval_data is not None` all inside the single existing `with server._lock:` block, and branches three ways:

- `NOT_YET_OPEN` (or, independently, `OPEN` with `_approval_data` still `None`, covering the separate guard the ticket asked for): `_approval_action` is never written, and the response is `409 Conflict` with a body that plainly says "The standards review window is not open yet" — never anything containing "processed".
- `OPEN` with approval data present: unchanged accept path, `_approval_action` is set and the response is the existing `200` "processed" body.
- `CLOSED`: unchanged from before this ticket — `410 Gone` with the honest closed-reason text.

This closes the gap described in the ticket: a POST carrying the CSRF token handed out at initial page load, sent before `set_approval_data` is ever called, is now rejected outright rather than silently recorded, and a later, genuine `wait_approval` call blocks for a real decision instead of returning that stale one instantly.

Tests added in `tests/test_config_page.py`: a new `TestSubmitStandardsRejectsPreWindowSubmission` class with `test_pre_window_post_is_rejected_and_wait_approval_still_blocks_for_real_decision`, which POSTs before `set_approval_data` has ever run and asserts the rejection is non-2xx, is not phrased as "processed", says the window is not open, and leaves `server._approval_action` unset; it then opens the window for real, proves a concurrently-started `wait_approval` genuinely blocks (does not return instantly) rather than replaying the rejected decision, and drives the real approve flow to completion.

Two pre-existing tests in `TestSubmitStandardsEndpoint` (`test_valid_approve_post_unblocks_a_concurrently_waiting_wait_approval` and `test_valid_cancel_post_unblocks_a_concurrently_waiting_wait_approval`) never called `set_approval_data` before posting a decision — they were, in effect, exercising the exact gap this ticket closes, and relying on the pre-fix behaviour of treating "not yet open" as "open" to pass. Both were updated to call `set_approval_data` before posting, so they now assert the intended behaviour (a decision made while the window is genuinely open) rather than the bug. `TestApprovalWindowClosesOnTimeoutOrConsumption`, including `test_happy_path_end_to_end_still_works`, needed no changes and passes unmodified, since every test in it already calls `set_approval_data` before posting.

Full suite (`uv run pytest -q`), `mypy`, `ruff check`, and `ruff format --check` are all clean after this change.
