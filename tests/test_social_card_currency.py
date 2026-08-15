"""The social preview card is a picture of the report, one indirection away.

`docs/social-card/card.html` embeds `docs/images/report-light.png`, so the
committed `docs/images/social-card.png` goes stale whenever the report's
appearance changes and nobody rebuilds it. That has now happened twice: once
before the card's source was committed at all, and again across #209 and #211
(see issue #225 and `docs/social-card/README.md`).

Committing the source made the card regenerable. It did not make staleness
discoverable, which is the same distinction `update_check.py`'s module
docstring draws about a stale install: a thing can be perfectly diagnosable
and still never diagnosed, because nothing ever looks. This is the something
that looks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CARD = "docs/images/social-card.png"
EMBEDDED_CAPTURE = "docs/images/report-light.png"
DEMO_REPORT = "docs/demo/report.html"


def _is_shallow_clone() -> bool:
    """True if this checkout has truncated history.

    This matters more than it looks. On a shallow clone every path resolves to
    the same single commit, so `git log -1 --format=%ct -- <path>` returns an
    identical timestamp for both files being compared, `card >= capture` holds
    trivially, and the check below reports success while establishing nothing.
    That is exactly the defect class this file exists to catch, occurring
    inside the guard meant to catch it: it was caught by CI reporting
    XPASS(strict), because actions/checkout defaults to fetch-depth 1.

    The workflow now sets fetch-depth: 0 so the guard genuinely runs there.
    This function is the belt to that braces: anywhere history is truncated,
    the checks skip loudly rather than passing vacuously.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: fail closed, treat as unusable
    return result.stdout.strip() != "false"


def _last_commit_epoch(path: str) -> int | None:
    """Committer timestamp of the last commit touching path, or None if git
    cannot answer (no git, not a repository, path never committed).

    Committer time rather than file mtime: mtime is set by checkout order and
    says nothing about which file was updated last, so it would make this
    check pass or fail at random on a fresh clone.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    stamp = result.stdout.strip()
    return int(stamp) if stamp.isdigit() else None


# Both links are currently broken (issue #225), so both are marked strict
# xfail rather than deleted, skipped or asserted loosely. Strict is the whole
# point: the moment the images are rebuilt these start passing, and a strict
# xfail that passes FAILS, which forces the marker off and leaves a live guard
# behind. Debt that clears itself, instead of a TODO nobody revisits.
_STALE = pytest.mark.xfail(
    strict=True,
    reason=(
        "issue #225: docs/images/report-light.png predates the report it "
        "captures, and docs/images/social-card.png predates the capture it "
        "embeds. Rebuild both per docs/social-card/README.md, then remove this "
        "marker; strict=True means leaving it on after the fix fails the suite."
    ),
)


@_STALE
def test_the_report_capture_is_not_older_than_the_report_it_captures() -> None:
    # The first link. docs/images/report-light.png is a picture of
    # docs/demo/report.html, so a renderer change that is not re-captured
    # leaves every downstream use of that image advertising an old layout,
    # including the README screenshots and the social card below.
    if _is_shallow_clone():
        pytest.skip(
            "shallow clone: every path resolves to the same commit, so file "
            "ordering cannot be established and a comparison here would pass "
            "without checking anything"
        )
    capture_epoch = _last_commit_epoch(EMBEDDED_CAPTURE)
    report_epoch = _last_commit_epoch(DEMO_REPORT)

    if capture_epoch is None or report_epoch is None:
        pytest.skip(
            "git could not date one of the two files, so currency could not be "
            f"checked ({EMBEDDED_CAPTURE}={capture_epoch}, "
            f"{DEMO_REPORT}={report_epoch})"
        )

    assert capture_epoch >= report_epoch, (
        f"{EMBEDDED_CAPTURE} was last committed before {DEMO_REPORT}, which it "
        "is a picture of. Re-capture it per docs/social-card/README.md."
    )


@_STALE
def test_social_card_is_not_older_than_the_report_capture_it_embeds() -> None:
    if _is_shallow_clone():
        pytest.skip(
            "shallow clone: every path resolves to the same commit, so file "
            "ordering cannot be established and a comparison here would pass "
            "without checking anything"
        )
    card_epoch = _last_commit_epoch(CARD)
    capture_epoch = _last_commit_epoch(EMBEDDED_CAPTURE)

    if card_epoch is None or capture_epoch is None:
        # Skipped, never passed. A check that could not run must not be
        # representable as one that passed; that is this project's own
        # hardening rule 2, and this file exists because a silent gap went
        # unnoticed for two releases.
        pytest.skip(
            "git could not date one of the two files, so currency could not be "
            f"checked ({CARD}={card_epoch}, {EMBEDDED_CAPTURE}={capture_epoch})"
        )

    assert card_epoch >= capture_epoch, (
        f"{CARD} was last committed before {EMBEDDED_CAPTURE}, which it embeds, "
        "so the published social preview is advertising a report layout that no "
        "longer exists. Rebuild it following docs/social-card/README.md, then "
        "upload it in GitHub Settings, Social preview: the upload is UI-only and "
        "GitHub does not read the file from this repository, so committing the "
        "regenerated PNG alone changes nothing a visitor sees."
    )


def test_both_files_this_check_depends_on_still_exist() -> None:
    # The control. If either path is renamed, _last_commit_epoch returns None
    # for it, the test above skips, and the guard silently stops guarding.
    # This fails loudly instead.
    for relative in (CARD, EMBEDDED_CAPTURE, DEMO_REPORT):
        assert (REPO_ROOT / relative).is_file(), (
            f"{relative} is missing or moved. The social-card currency check is "
            "keyed on both paths and degrades to a skip when one cannot be "
            "dated, so update it rather than leaving it inert."
        )
