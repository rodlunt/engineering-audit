"""Tests that AUDIT.md actually asks for what the report renders.

Issues #181 and #192 were the same defect twice: a `DomainResult` field
(`coverage`, then `self_assessment`) existed, was optional in the schema, was
rendered in the report with an explicit "not reported" fallback, and was
never once instructed by AUDIT.md. Nothing forced the auditor to fill it in,
so it silently never got filled in. This module guards against a third
repeat: it derives, from the schema and the renderer's own source rather
than a hand-maintained list, which `DomainResult` fields are both optional
and rendered, and asserts each one is named in AUDIT.md.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

from engineering_audit import report as report_module
from engineering_audit.schema import DomainResult

AUDIT_MD = Path(__file__).parent.parent / "AUDIT.md"
REPORT_SOURCE = Path(report_module.__file__).read_text(encoding="utf-8")


def _optional_domain_result_fields() -> list[str]:
    """Field names on DomainResult whose type allows None.

    A field with a plain list default (``findings``, ``rule_verdicts``,
    ``consulted_sources``) is always populated as part of the core sweep and
    an empty list is itself meaningful, not an unfilled field. A field typed
    ``X | None`` is the shape this defect keeps recurring on: schema-legal
    to leave unset, with nothing short of AUDIT.md's own prose to prompt an
    auditor to set it.
    """
    hints = typing.get_type_hints(DomainResult)
    return sorted(
        name for name, hint in hints.items() if type(None) in typing.get_args(hint)
    )


def _rendered_by_report(field_name: str) -> bool:
    """True if report.py reads this DomainResult field off a result object.

    Matches ``result.<field_name>`` (word boundary), which is how every
    renderer in report.py accesses a DomainResult's attributes.
    """
    return re.search(rf"\bresult\.{re.escape(field_name)}\b", REPORT_SOURCE) is not None


def test_optional_domain_result_fields_the_report_renders_are_named_in_audit_md() -> (
    None
):
    audit_md = AUDIT_MD.read_text(encoding="utf-8")

    optional_fields = _optional_domain_result_fields()
    # Sanity check the derivation itself still matches the fields this test
    # was written against, so a schema change that adds or removes an
    # optional field is visible here rather than silently changing what
    # gets checked.
    assert optional_fields == [
        "coverage",
        "reason",
        "self_assessment",
        "uninspected_evidence",
    ]

    rendered_optional_fields = [f for f in optional_fields if _rendered_by_report(f)]
    assert rendered_optional_fields, (
        "expected at least one optional DomainResult field to be rendered by "
        "report.py; if this is empty the detection regex has drifted from "
        "report.py's own attribute access style"
    )

    missing = [f for f in rendered_optional_fields if f"`{f}`" not in audit_md]
    assert not missing, (
        f"AUDIT.md never instructs the auditor to record {missing}, but "
        "report.py renders it with a fallback for when it is absent. "
        "Add an instruction beside the existing coverage/self_assessment "
        "guidance in step 4.6 (issues #181, #192)."
    )


def test_self_assessment_confidence_levels_named_in_audit_md() -> None:
    # The schema accepts exactly high, medium or low (schema.py's
    # SelfAssessment._confidence_allowed). AUDIT.md must say so, not just
    # name the field, or an auditor has no way to know what values are
    # legal without reading the source.
    audit_md = AUDIT_MD.read_text(encoding="utf-8")
    assert "`self_assessment`" in audit_md
    for level in ("high", "medium", "low"):
        assert f"`{level}`" in audit_md, (
            f"AUDIT.md's self_assessment instruction should name the "
            f"'{level}' confidence level the schema accepts"
        )
