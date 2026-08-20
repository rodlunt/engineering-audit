#!/usr/bin/env python3
"""Generate the demo report committed at docs/demo/report.html.

Builds a small, deterministic RunState against examples/taster-rules (the three
complete domains that ship in this repository) and renders it with the same
renderer the live tool uses, so the demo report shows exactly what the tool
actually produces rather than a hand-built mockup that could drift from reality.

The audited repository, acme/orders-api, and every finding in it are invented
for this demo: no relation to any real repository or organisation. They are
written to be *plausible* rather than whimsical, because this report is both the
public "live example" linked from the README and the source of the README
screenshots, and a shop window full of nonsense findings tells a reader nothing
about what the tool would say about their own code. Earlier versions of this
script built the demo from tests/fixture_pack, whose deliberately invented
domains (gnome husbandry, teacup logistics) made both of those artefacts
useless as evidence.

Run via:

    uv run python scripts/generate-demo-report.py

Deterministic by construction: every timestamp below is fixed, filed_issue_urls
carries a fixed, invented URL, and nothing here reads the clock, the environment
or any external source. Re-running this script must therefore produce a
byte-identical docs/demo/report.html. tests/test_demo_script.py runs it twice
and against the committed file to prove that; a change here or in the renderer
that is not followed by regenerating the committed file fails that test rather
than drifting unnoticed.
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
TASTER_PACK = REPO_ROOT / "examples" / "taster-rules"
OUT_PATH = REPO_ROOT / "docs" / "demo" / "report.html"

# Invented for this demo only: not a real GitHub issue, and not fetched or
# validated against a live repository. It exists purely to show what an
# already-filed finding looks like: disabled, unticked, linking out.
_DEMO_FILED_ISSUE_URL = "https://github.com/rodlunt/engineering-audit/issues/3"

# Invented for this demo only, the same way _DEMO_FILED_ISSUE_URL is: shows
# what the "Sources consulted this run" report section looks like with a real
# entry, rather than only ever exercising the "none recorded" fallback. The
# reserved .invalid TLD keeps it unmistakably a placeholder while still being
# the shape of thing a real run consults: the project's own decision record.
_DEMO_CONSULTED_SOURCE = ConsultedSource(
    rule_id="D01-R04",
    url="https://example.invalid/orders-api/schema-decision-log",
    title="Orders API schema decision log, entry 2026-05-14",
    why=(
        "checked whether the stored order total was a recorded denormalisation "
        "decision or an accident before verdicting this rule"
    ),
    accessed="2026-08-09T09:02:00+00:00",
)


def _verdicts(rules, overrides: dict[str, RuleVerdict]) -> list[RuleVerdict]:
    """Every rule passes unless the caller overrode it.

    Written as an override map rather than positional index assignment so a
    rule added to a taster domain cannot silently shift which rule the demo
    is claiming a finding against.
    """
    return [
        overrides.get(r.id, RuleVerdict(rule_id=r.id, verdict=Verdict.pass_))
        for r in rules
    ]


def _data_modelling_result(pack: RulesPack) -> DomainResult:
    domain = pack.get_domain("d01")
    assert domain is not None

    overrides = {
        "D01-R05": RuleVerdict(rule_id="D01-R05", verdict=Verdict.FINDING),
        "D01-R13": RuleVerdict(rule_id="D01-R13", verdict=Verdict.FINDING),
        "D01-R04": RuleVerdict(rule_id="D01-R04", verdict=Verdict.FINDING),
        "D01-R06": RuleVerdict(rule_id="D01-R06", verdict=Verdict.FINDING),
        "D01-R15": RuleVerdict(
            rule_id="D01-R15",
            verdict=Verdict.COULD_NOT_EVALUATE,
            note=(
                "database grants are provisioned outside this repository; no role "
                "definitions or GRANT statements are present in the checkout"
            ),
        ),
        "D01-R08": RuleVerdict(
            rule_id="D01-R08",
            verdict=Verdict.NOT_APPLICABLE,
            note="this schema declares no subtype hierarchy: every table is a base table",
        ),
    }

    return DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=_verdicts(domain.rules, overrides),
        findings=[
            Finding(
                rule_id="D01-R05",
                severity=Severity.HIGH,
                title="orders.customer_email carries no uniqueness constraint",
                location="migrations/0004_orders.sql:31",
                body_md=(
                    "orders.customer_email is the column the checkout flow looks a "
                    "returning customer up by, but the table declares no unique index "
                    "on it and no other column identifies the customer.\n\n"
                    "Nothing stops two rows claiming the same address, and the lookup "
                    "in api/checkout.py:88 takes the first row it gets back. Once a "
                    "duplicate exists, a returning customer is silently served another "
                    "customer's saved address and order history, and no error is "
                    "raised at any layer.\n\n"
                    "Add UNIQUE (customer_email) in a migration, after resolving the "
                    "existing duplicates that migration will refuse to run over, and "
                    "make the checkout lookup fetch exactly one row rather than the "
                    "first of many."
                ),
                issue_title="Add a uniqueness constraint to orders.customer_email",
                issue_body=(
                    "orders.customer_email is used as the customer lookup key in "
                    "api/checkout.py:88 but has no unique index. Two rows can claim "
                    "the same address, and the lookup returns the first match. See "
                    "migrations/0004_orders.sql:31."
                ),
                precondition=(
                    "the rule presumes a fact type identified by a column, present as "
                    "the customer lookup at api/checkout.py:80-95"
                ),
            ),
            Finding(
                rule_id="D01-R13",
                severity=Severity.MEDIUM,
                title="Deleting a customer cascades into their completed orders",
                location="models/customer.py:24",
                body_md=(
                    "Customer.orders is declared with SQLAlchemy's "
                    'cascade="all, delete-orphan", which the ORM\'s own examples '
                    "use for parent-owned child rows.\n\n"
                    "An order is not owned by the customer in that sense: it is a "
                    "financial record the business has to keep after the customer "
                    "asks to be removed. Under this cascade a routine account "
                    "deletion destroys completed orders and their line items, and "
                    "the only record that they existed is the payment provider's.\n\n"
                    "Change the relationship to restrict the delete, and give the "
                    "customer record the soft-delete or anonymisation path that the "
                    "erasure request actually needs."
                ),
                issue_title="Stop customer deletion cascading into completed orders",
                issue_body=(
                    'Customer.orders uses cascade="all, delete-orphan" '
                    "(models/customer.py:24), so deleting a customer deletes their "
                    "completed orders and line items. Orders are records the business "
                    "must retain; the cascade should restrict, and erasure should "
                    "anonymise instead."
                ),
                precondition=(
                    "the rule presumes an ORM-declared relationship with a cascade "
                    "setting, present at models/customer.py:20-30"
                ),
            ),
            Finding(
                rule_id="D01-R04",
                severity=Severity.MEDIUM,
                title="orders.total_cents is stored alongside the line items it duplicates",
                location="migrations/0004_orders.sql:44",
                body_md=(
                    "orders.total_cents is written at checkout and never "
                    "recomputed, while order_items holds the quantities and unit "
                    "prices it was derived from.\n\n"
                    "The decision log records the denormalisation as a reporting "
                    "optimisation, but nothing recomputes the column when a line item "
                    "is amended, and the refunds path in api/refunds.py:52 amends "
                    "line items. A partially refunded order therefore reports its "
                    "pre-refund total on every screen that reads the stored column.\n\n"
                    "Either recompute the total in the same transaction that amends "
                    "any line item, or drop the column and compute it on read; the "
                    "decision record should say which, and why."
                ),
                issue_title="Recompute or drop the stored orders.total_cents",
                issue_body=(
                    "orders.total_cents is derived from order_items but is not "
                    "recomputed when refunds amend line items (api/refunds.py:52), so "
                    "partially refunded orders report a stale total. See "
                    "migrations/0004_orders.sql:44."
                ),
                precondition=(
                    "the rule presumes stored data derivable from other stored data, "
                    "present as the order total at migrations/0004_orders.sql:44"
                ),
            ),
            Finding(
                rule_id="D01-R06",
                severity=Severity.LOW,
                title="Six columns on order_items are nullable by default, not by decision",
                location="migrations/0004_orders.sql:58",
                body_md=(
                    "Every column added to order_items after the first migration is "
                    "nullable, and the migrations carry no note explaining any of "
                    "them.\n\n"
                    "unit_price_cents and quantity are mandatory in every code "
                    "path that writes the table, so the nullability is not a modelled "
                    "optional role; it is the default that was never overridden. The "
                    "cost is that every reader has to handle a null the writer never "
                    "produces, and a genuinely optional column becomes "
                    "indistinguishable from these.\n\n"
                    "Set NOT NULL on the columns the application already treats as "
                    "mandatory, and leave a one-line reason on the ones that stay "
                    "optional."
                ),
                issue_title="Set NOT NULL on the order_items columns that are mandatory in code",
                issue_body=(
                    "order_items.unit_price_cents and quantity are nullable in the "
                    "schema but mandatory in every write path. Nullability was "
                    "defaulted rather than decided. See migrations/0004_orders.sql:58."
                ),
                precondition=(
                    "the rule presumes columns whose optionality can be compared "
                    "against the code that writes them, present at "
                    "migrations/0004_orders.sql:50-70"
                ),
            ),
        ],
        # A domain that read everything the repository pointed it at. The demo
        # carries both states deliberately: this one, and d05 below, which
        # reached its verdicts without opening something.
        uninspected_evidence=[],
        self_assessment=SelfAssessment(
            confidence="high",
            limits=(
                "read the migrations and the ORM models; did not inspect the running "
                "database, so constraints added outside migrations are not accounted for"
            ),
        ),
        coverage=Coverage(
            files_inspected=19,
            files_skipped=1,
            note="one vendored SQL dump skipped, 4.1 MB",
        ),
        consulted_sources=[_DEMO_CONSULTED_SOURCE],
    )


def _testing_result(pack: RulesPack) -> DomainResult:
    domain = pack.get_domain("d05")
    assert domain is not None

    overrides = {
        "D05-R08": RuleVerdict(rule_id="D05-R08", verdict=Verdict.FINDING),
        "D05-R17": RuleVerdict(rule_id="D05-R17", verdict=Verdict.FINDING),
        "D05-R05": RuleVerdict(rule_id="D05-R05", verdict=Verdict.FINDING),
        "D05-R18": RuleVerdict(
            rule_id="D05-R18",
            verdict=Verdict.COULD_NOT_EVALUATE,
            note=(
                "the load tests run from a separate pipeline repository that is not "
                "in this checkout; their infrastructure could not be compared to "
                "production from here"
            ),
        ),
    }

    return DomainResult(
        domain_id="d05",
        status="completed",
        rule_verdicts=_verdicts(domain.rules, overrides),
        findings=[
            Finding(
                rule_id="D05-R08",
                severity=Severity.MEDIUM,
                title="No layer of the suite exercises the API against a real database",
                location="tests/",
                body_md=(
                    "The suite is 212 unit tests with the database session mocked, "
                    "plus 4 end-to-end browser tests. There is no integration layer "
                    "between them.\n\n"
                    "Every defect that lives in the gap, a wrong cascade, a missing "
                    "constraint, a migration that does not apply cleanly, is "
                    "invisible to the unit tests because the session is a mock, and "
                    "reaches the browser tests only if one of the four happens to "
                    "walk over it. Two of the four findings in the data-modelling "
                    "domain of this same run sit exactly in that gap.\n\n"
                    "Add an integration layer that runs the real migrations against "
                    "a throwaway database and exercises the API routes over it, and "
                    "let the unit tests keep their mocks for the logic they cover."
                ),
                issue_title="Add an integration test layer running against a real database",
                issue_body=(
                    "The suite has 212 mocked-session unit tests and 4 end-to-end "
                    "tests, with nothing in between. Schema-level defects are "
                    "invisible to both. Add integration tests that run the real "
                    "migrations against a throwaway database."
                ),
                precondition=(
                    "the rule presumes an existing suite whose layers can be counted, "
                    "present at tests/ (216 collected tests)"
                ),
            ),
            Finding(
                rule_id="D05-R17",
                severity=Severity.MEDIUM,
                title="The load test passes or fails on a mean, not a percentile",
                location="perf/locustfile.py:71",
                body_md=(
                    "The performance gate asserts that the mean response time stays "
                    "under 400 ms.\n\n"
                    "A mean hides the tail that users actually complain about: the "
                    "same run that passes at a 180 ms mean can be serving one request "
                    "in twenty at over two seconds, and the gate reports green. There "
                    "is also no stated user-facing objective anywhere the number was "
                    "derived from, so 400 ms cannot be defended if it is ever "
                    "challenged.\n\n"
                    "Write down the objective first, then assert on the percentile it "
                    "implies, p95 and p99, and keep the mean as an observation rather "
                    "than the gate."
                ),
                issue_title="Gate the load test on p95/p99, not the mean",
                issue_body=(
                    "perf/locustfile.py:71 fails the run on mean response time under "
                    "400 ms. A mean hides the tail. Set a user-facing objective and "
                    "assert on p95 and p99 instead."
                ),
                precondition=(
                    "the rule presumes a performance test with a pass/fail assertion, "
                    "present at perf/locustfile.py:60-80"
                ),
            ),
            Finding(
                rule_id="D05-R05",
                severity=Severity.LOW,
                title="Test effort is spread evenly across modules regardless of risk",
                location="tests/",
                body_md=(
                    "Coverage sits between 78% and 84% in every module, including "
                    "api/pricing.py and api/refunds.py, which carry the money "
                    "arithmetic, and util/slugify.py, which does not.\n\n"
                    "Uniform coverage is a decision not to weight by risk. The "
                    "modules where a defect costs a refund dispute are being tested "
                    "to the same depth as a string helper, which means the budget is "
                    "being spent where it buys the least.\n\n"
                    "Pick the two or three modules whose failure is most expensive "
                    "and test them to a deliberately higher standard, and let the "
                    "cheap ones fall."
                ),
                issue_title="Weight test effort by risk instead of spreading it evenly",
                issue_body=(
                    "Coverage is 78-84% across every module, so pricing and refunds "
                    "are tested to the same depth as util/slugify.py. Weight effort "
                    "toward the modules whose failure is most expensive."
                ),
                precondition=(
                    "the rule presumes per-module coverage figures, present in the "
                    "committed coverage report at .coverage.xml"
                ),
            ),
        ],
        self_assessment=SelfAssessment(
            confidence="medium",
            limits=(
                "counted and read the tests in this checkout; did not run them, so "
                "what they assert is taken from their source rather than observed"
            ),
        ),
        coverage=Coverage(files_inspected=31, files_skipped=0),
        # The other state, so the demo report shows the Evidence boundary block
        # doing its job rather than only its all-clear message: the repository
        # points somewhere for part of its testing and the audit did not go
        # there, which any "the repository does not test X" verdict has to be
        # read against.
        uninspected_evidence=[
            "the load-test pipeline: perf/README.md:8 points at acme/perf-pipeline "
            "for the scheduled soak runs; not inspected, it is not in this checkout"
        ],
    )


def _presenting_data_result(pack: RulesPack) -> DomainResult:
    domain = pack.get_domain("d16")
    assert domain is not None

    overrides = {
        "D16-R07": RuleVerdict(rule_id="D16-R07", verdict=Verdict.FINDING),
        "D16-R16": RuleVerdict(rule_id="D16-R16", verdict=Verdict.FINDING),
        "D16-R18": RuleVerdict(
            rule_id="D16-R18",
            verdict=Verdict.NOT_APPLICABLE,
            note=(
                "the dashboard is used only by the two people who built it; there is "
                "no third party to test comprehension against"
            ),
        ),
    }

    return DomainResult(
        domain_id="d16",
        status="completed",
        rule_verdicts=_verdicts(domain.rules, overrides),
        findings=[
            Finding(
                rule_id="D16-R07",
                severity=Severity.HIGH,
                title="The revenue bar chart starts its axis at 40,000, unmarked",
                location="admin/templates/dashboard.html:112",
                body_md=(
                    "The weekly revenue bars are drawn on a y-axis running from "
                    "40,000 to 52,000, with no break marked and no note that the "
                    "axis is cropped.\n\n"
                    "Bar length is the thing the reader is comparing, so cropping the "
                    "baseline multiplies the apparent difference: a 6% week-on-week "
                    "move is drawn as a bar roughly three times taller than its "
                    "neighbour. This chart is the one screenshotted into the weekly "
                    "trading summary, so the exaggeration is being read as a "
                    "business result by people with no access to the numbers behind "
                    "it.\n\n"
                    "Start the bars at zero. If the interesting variation genuinely "
                    "disappears at that scale, the chart wanted to be a line of "
                    "week-on-week change, not a cropped bar."
                ),
                issue_title="Start the revenue bar chart at zero",
                issue_body=(
                    "admin/templates/dashboard.html:112 draws weekly revenue bars on "
                    "a 40,000-52,000 axis with no break marked, tripling the apparent "
                    "size of a 6% move. Bars must start at zero, or become a "
                    "change-over-time line."
                ),
                precondition=(
                    "the rule presumes a bar chart with a configurable axis, present "
                    "at admin/templates/dashboard.html:100-130"
                ),
            ),
            Finding(
                rule_id="D16-R16",
                severity=Severity.MEDIUM,
                title="Order status is carried by colour alone",
                location="admin/static/orders.css:44",
                body_md=(
                    "In the orders table, pending, shipped and refunded rows are "
                    "distinguished only by a background colour: green, blue and "
                    "amber. No text, icon or pattern repeats the distinction.\n\n"
                    "A reader with the most common form of colour vision deficiency "
                    "sees the green and amber rows as the same, which means the two "
                    "states with opposite financial consequences are the two that "
                    "merge. The table is also pasted into email, where the background "
                    "colours are stripped entirely.\n\n"
                    "Add the status as a word in its own column, and keep the colour "
                    "as reinforcement rather than as the encoding."
                ),
                issue_title="Add a text status column to the orders table",
                issue_body=(
                    "admin/static/orders.css:44 encodes order status only as a row "
                    "background colour. Green (pending) and amber (refunded) are "
                    "indistinguishable to most colour-blind readers, and colour is "
                    "stripped when the table is pasted into email."
                ),
                precondition=(
                    "the rule presumes a rendered artefact whose encodings can be "
                    "read off, present at admin/templates/orders.html:38-70"
                ),
            ),
        ],
        uninspected_evidence=[],
        self_assessment=SelfAssessment(
            confidence="medium",
            limits=(
                "read the templates and stylesheets; did not render the dashboard, so "
                "verdicts about the drawn result are inferred from the source"
            ),
        ),
        coverage=Coverage(files_inspected=12, files_skipped=0),
    )


def build_demo_run_state(pack: RulesPack) -> RunState:
    d01_result = _data_modelling_result(pack)
    d05_result = _testing_result(pack)
    d16_result = _presenting_data_result(pack)

    meta = RunMeta(
        tool_version="0.1.0",
        rules_pack_name=pack.root.name,
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="acme/orders-api",
        repo_commit="abc1234def5678",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:24:00+00:00",
    )

    config = AuditConfig(
        selected_domain_ids=["d01", "d05", "d16"],
        issue_mode="github",
        feedback_text="",
    )

    return RunState(
        meta=meta,
        config=config,
        domain_results={"d01": d01_result, "d05": d05_result, "d16": d16_result},
        filed_issue_urls={"D01-R05#1": _DEMO_FILED_ISSUE_URL},
        # The demo depicts a run that fetched the rules for every domain it
        # verdicted, which is the ordinary case: the report's Rules fetched
        # block then shows what a run with nothing to answer for looks like,
        # including the limit it states about what "fetched" does and does not
        # prove.
        rules_fetched_domain_ids=["d01", "d05", "d16"],
        feedback_issue_url=None,
    )


def write_demo_report(out_path: Path) -> Path:
    pack = load_pack(TASTER_PACK)
    run_state = build_demo_run_state(pack)
    return write_report(run_state, pack, out_path)


def main() -> None:
    written = write_demo_report(OUT_PATH)
    print(written)


if __name__ == "__main__":
    main()
