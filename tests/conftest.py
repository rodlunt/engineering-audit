"""Suite-wide pytest fixtures.

See issue #190: `begin_run` runs a tool and a rules-pack staleness check,
each shelling out to `git ls-remote https://github.com/rodlunt/engineering-audit`
(src/engineering_audit/update_check.py). tests/test_server.py's `_begin_run`
helper is called from about ninety sites, so an unmodified suite run made
roughly 180 live round trips to github.com, most of the suite's wall-clock
time and something that cannot succeed at all when run offline.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the suite to ENGINEERING_AUDIT_NO_UPDATE_CHECK=1, so
    `build_server`'s default resolution (see
    `engineering_audit.server._update_check_enabled_from_env`) skips the
    live `git ls-remote` call unless a test asks for it explicitly.

    Uses the `monkeypatch` fixture rather than writing `os.environ`
    directly: `monkeypatch` is function-scoped, and pytest caches a single
    instance per test, so this fixture and any `monkeypatch` parameter a
    test itself declares are the same object. A test that calls
    `monkeypatch.setenv(...)` or `monkeypatch.delenv(...)` on this variable
    therefore overrides the value set here for the remainder of that test,
    and pytest restores whatever was there before this fixture ran once the
    test finishes, no double-restore or ordering hazard either way.

    tests/test_server.py:462-668 deliberately drives
    ENGINEERING_AUDIT_NO_UPDATE_CHECK and --no-update-check resolution
    itself, in both directions, via that same monkeypatch instance, so this
    default never makes those tests vacuous: each one still fails if the
    behaviour it names regresses.
    """
    monkeypatch.setenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", "1")
