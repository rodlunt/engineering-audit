# 11: Two different functions named derive_summary_counts

**What to build:** either one `derive_summary_counts` function instead of two, or two functions with names that make their different purposes clear without reading the signature, with no behaviour change and the existing test suite still passing.

**Blocked by:** None.

**Status:** done

This is a minor maintenance trap found during the issue 05 survey, and it is low severity: it is not a bug today, since production imports the correct one. It is included here honestly as a naming hazard, not inflated into anything more.

The facts:

- `derive_summary_counts(rule_set)` exists in `src/engineering_audit/standards_approval.py:63`. It takes a single rule set and is pure. It is exported in that module's `__all__` (`src/engineering_audit/standards_approval.py:23`) and is used only by tests (`tests/test_standards_approval.py` and `tests/test_config_page.py`).
- `derive_summary_counts(prior, merged)` exists in `src/engineering_audit/standards_integration.py:352`. It takes a prior rule set and a merged rule set and is the one wired into production, imported and called from `src/engineering_audit/server.py:107` and `src/engineering_audit/server.py:2821`.
- Same name, different module, different signature, different semantics (one counts a single rule set's statuses, the other counts what changed between a prior set and a merged set). Nothing currently calls the wrong one, but the two are easy to confuse for anyone reading an import line or an editor's autocomplete without checking which module it came from.

- [x] Decide, and record the decision in the PR description, whether both functions are genuinely needed as separate pieces of logic, or whether one can be expressed in terms of the other (e.g. the single-argument version calling the two-argument version with `prior=None`, or vice versa).
- [x] If both are kept, rename at least one so the two purposes are distinguishable by name alone, without opening either module (for example, something that names what is being compared, such as a single-set variant versus a prior-versus-merged variant), and update every call site and import in `src/` and `tests/` to match.
- [x] If one is expressed in terms of the other or removed, no caller's behaviour changes: the same inputs to whichever function(s) remain produce the same `SummaryCount` output as before, and no production call site (`src/engineering_audit/server.py`) changes what it passes or receives.
- [x] The existing tests for both functions (`tests/test_standards_approval.py`, `tests/test_standards_integration.py`, `tests/test_config_page.py`) pass with only import and name updates, not logic changes, proving behaviour is unchanged.

## Closing note

Decision recorded: both functions were genuinely needed as separate logic (one counts a single rule set's statuses, the other counts what changed between a prior set and a merged set), so both were renamed rather than one being expressed in terms of the other.

- `derive_summary_counts(rule_set)` → `derive_rule_set_summary_counts(rule_set)` at `src/engineering_audit/standards_approval.py:63` (and in that module's `__all__` at line 23).
- `derive_summary_counts(prior, merged)` → `derive_diff_summary_counts(prior, merged)` at `src/engineering_audit/standards_integration.py:352` (and in that module's `__all__` at line 46).
- Production call site updated: `src/engineering_audit/server.py:107` (import) and `server.py:2821` (call, still `derive_diff_summary_counts(prior_rule_set, merged)` — a like-for-like rename, no signature or logic change).
- Test call sites updated to match, with no logic changes: `tests/test_standards_approval.py` (import at line 14, calls at lines 26, 58, 79, 106, 126, 180), `tests/test_standards_integration.py` (import at line 33, calls at lines 635, 675, 708), and `tests/test_config_page.py` (imports and calls at lines 760/763, 1820/1832, 1850/1877, 1906/1921, 1961/1976, 2007/2021, 2255/2267).
- Verified directly: `grep -rn "derive_summary_counts" src/ tests/` returns zero hits. The ambiguous bare name no longer exists anywhere in the repository; every call site now names, by function name alone, which of the two semantics it means.
- Full suite (`uv run pytest -q`) passes unchanged (1222 passed).
