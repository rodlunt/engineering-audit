#!/usr/bin/env python3
"""Generate the demo report committed at docs/demo/report.html.

Builds a small, deterministic RunState against tests/fixture_pack (invented
content only, no relation to any real repository or organisation) and
renders it with the same renderer the live tool uses, so the demo report
shows exactly what the tool actually produces rather than a hand-built
mockup that could drift from reality.

Run via:

    uv run python scripts/generate-demo-report.py

Deterministic by construction: every timestamp below is fixed, filed_issue_urls
carries a fixed, invented URL, and nothing here reads the clock, the
environment or any external source. Re-running this script must therefore
produce a byte-identical docs/demo/report.html. tests/test_demo_script.py
runs it twice and against the committed file to prove that; a change here
or in the renderer that is not followed by regenerating the committed file
fails that test rather than drifting unnoticed.
"""

from __future__ import annotations

from pathlib import Path

from engineering_audit.report import write_report
from engineering_audit.rules import RulesPack, load_pack
from engineering_audit.schema import (
    AuditConfig,
    ConsultedSource,
    Coverage,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    RunState,
    SelfAssessment,
    Severity,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PACK = REPO_ROOT / "tests" / "fixture_pack"
OUT_PATH = REPO_ROOT / "docs" / "demo" / "report.html"

# Invented for this demo only: not a real GitHub issue, and not fetched or
# validated against a live repository. It exists purely to show what an
# already-filed finding looks like: disabled, unticked, linking out.
_DEMO_FILED_ISSUE_URL = "https://github.com/rodlunt/engineering-audit/issues/3"

# Invented for this demo only, the same way _DEMO_FILED_ISSUE_URL is: shows
# what the "Sources consulted this run" report section looks like with a
# real entry, rather than only ever exercising the "none recorded" fallback.
_DEMO_CONSULTED_SOURCE = ConsultedSource(
    rule_id="D01-R01",
    url="https://example.invalid/garden-bed-standards",
    title="Garden Bed Allocation Standard, section 3",
    why="checked the shared-bed flag's documented meaning before verdicting this rule",
    accessed="2026-08-09T09:02:00+00:00",
)


def build_demo_run_state(pack: RulesPack) -> RunState:
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    assert d01 is not None and d02 is not None

    d01_verdicts = [RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in d01.rules]
    d01_verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
    d01_verdicts[2] = RuleVerdict(
        rule_id="D01-R03",
        verdict=Verdict.COULD_NOT_EVALUATE,
        note="the garden bed ledger file could not be located in this demo repository",
    )
    # One not-applicable verdict, carrying the reason every not-applicable
    # verdict now has to carry, so the demo report shows the Not applicable
    # block doing its job rather than only its all-clear message.
    d01_verdicts[3] = RuleVerdict(
        rule_id="D01-R04",
        verdict=Verdict.NOT_APPLICABLE,
        note=(
            "this demo repository stores no beard-length average to recalculate: the "
            "roster summary computes it on read"
        ),
    )

    d01_result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=d01_verdicts,
        findings=[
            Finding(
                rule_id="D01-R02",
                severity=Severity.HIGH,
                title="Two gnomes share bed-14 without the shared-bed flag",
                location="ledger/beds.py:42",
                body_md=(
                    "bed-14 holds two gnomes, gnome-07 and gnome-19, and neither record "
                    "carries the shared-bed flag.\n\n"
                    "The nightly census only counts the first occupant of an unflagged "
                    "bed, so gnome-19 is invisible to every maintenance round until the "
                    "flag is set: its next scheduled hat-colour check will simply never "
                    "fire.\n\n"
                    "Set shared_bed=True on both gnome-07 and gnome-19's ledger entries "
                    "for bed-14, then re-run the nightly census once to confirm both now "
                    "appear."
                ),
                issue_title="Set shared-bed flag for bed-14",
                issue_body=(
                    "bed-14 has two occupants (gnome-07, gnome-19) and neither carries "
                    "the shared-bed flag. See ledger/beds.py:42."
                ),
            )
        ],
        self_assessment=SelfAssessment(
            confidence="high",
            limits="did not check gnome beds outside the main garden plot",
        ),
        coverage=Coverage(
            files_inspected=14,
            files_skipped=1,
            note="one binary hat-colour swatch skipped",
        ),
        consulted_sources=[_DEMO_CONSULTED_SOURCE],
    )

    d02_result = DomainResult(
        domain_id="d02",
        status="completed",
        rule_verdicts=[
            RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in d02.rules
        ],
        findings=[],
        self_assessment=SelfAssessment(
            confidence="medium", limits="did not check archived shipment routes"
        ),
        coverage=Coverage(files_inspected=6, files_skipped=0),
    )

    meta = RunMeta(
        tool_version="0.1.0",
        rules_pack_name=pack.root.name,
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="rodlunt/demo-widgets-app",
        repo_commit="abc1234def5678",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:12:00+00:00",
    )

    config = AuditConfig(
        selected_domain_ids=["d01", "d02"],
        issue_mode="github",
        feedback_text="",
    )

    return RunState(
        meta=meta,
        config=config,
        domain_results={"d01": d01_result, "d02": d02_result},
        filed_issue_urls={"D01-R02#1": _DEMO_FILED_ISSUE_URL},
        # The demo depicts a run that fetched the rules for both domains it
        # verdicted, which is the ordinary case: the report's Rules fetched
        # block then shows what a run with nothing to answer for looks like,
        # including the limit it states about what "fetched" does and does not
        # prove.
        rules_fetched_domain_ids=["d01", "d02"],
        feedback_issue_url=None,
    )


def write_demo_report(out_path: Path) -> Path:
    pack = load_pack(FIXTURE_PACK)
    run_state = build_demo_run_state(pack)
    return write_report(run_state, pack, out_path)


def main() -> None:
    written = write_demo_report(OUT_PATH)
    print(written)


if __name__ == "__main__":
    main()
