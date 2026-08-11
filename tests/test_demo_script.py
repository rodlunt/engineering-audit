"""Regression tests for scripts/generate-demo-report.py.

The committed docs/demo/report.html must always match a fresh run of the
generator: if a renderer change lands without the demo report being
regenerated to match, that is exactly the kind of silent drift the
hardening rules in this project exist to catch, so it fails the suite
rather than being noticed only when someone happens to open the file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-demo-report.py"
COMMITTED_REPORT = REPO_ROOT / "docs" / "demo" / "report.html"


def _load_demo_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_demo_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_script_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    module = _load_demo_module()
    out_a = tmp_path / "run-a.html"
    out_b = tmp_path / "run-b.html"

    module.write_demo_report(out_a)
    module.write_demo_report(out_b)

    assert out_a.read_bytes() == out_b.read_bytes()


def test_committed_demo_report_matches_a_fresh_run(tmp_path: Path) -> None:
    module = _load_demo_module()
    fresh = tmp_path / "fresh-report.html"
    module.write_demo_report(fresh)

    assert COMMITTED_REPORT.is_file(), (
        "docs/demo/report.html is missing; run "
        "`uv run python scripts/generate-demo-report.py` and commit the result."
    )
    assert fresh.read_bytes() == COMMITTED_REPORT.read_bytes(), (
        "docs/demo/report.html is stale relative to scripts/generate-demo-report.py "
        "and/or the renderer; regenerate it and commit the result."
    )


def test_demo_run_state_shows_the_disabled_filed_rendering_and_could_not_evaluate_note() -> None:
    # Cheap, direct assertions on the built RunState (not the rendered HTML)
    # that the demo actually exercises the scenarios it is meant to
    # demonstrate: a pre-filed issue, a could-not-evaluate verdict and a
    # not-applicable one.
    module = _load_demo_module()
    pack = module.load_pack(module.FIXTURE_PACK)
    run_state = module.build_demo_run_state(pack)

    assert run_state.filed_issue_urls == {
        "D01-R02#1": "https://github.com/rodlunt/engineering-audit/issues/3"
    }
    d01 = run_state.domain_results["d01"]
    finding = d01.findings[0]
    assert finding.severity.value == "high"
    # Three-part body: what is wrong, why it matters, how to fix it.
    assert len([p for p in finding.body_md.split("\n\n") if p.strip()]) == 3

    could_not_evaluate = [
        rv for rv in d01.rule_verdicts if rv.verdict.value == "could-not-evaluate"
    ]
    assert len(could_not_evaluate) == 1
    assert could_not_evaluate[0].note

    not_applicable = [rv for rv in d01.rule_verdicts if rv.verdict.value == "not-applicable"]
    assert len(not_applicable) == 1
    assert not_applicable[0].note
