"""The approval **event** spine: one row per approval, not one per application.

An application is not a single approval. Keytruda's ``BLA125514`` has 127
approved submissions — one original and 108 efficacy supplements — spread over
2014 to 2026, and it gained 25 indications after the original. Recording only
``first_approval`` per application collapses twelve years of separate FDA
decisions into one date, and then attributes every trial to it.

**What counts as indication-changing.** An ``ORIG`` submission, or a supplement
whose class is ``Efficacy``. Those are the events at which FDA decides the drug
works for something; ``Labeling``, ``Manufacturing (CMC)`` and ``REMS``
supplements change the document, the plant or the risk programme, not what the
drug is approved to treat. Measured over the corpus: 192,477 approved
submissions, of which 86,727 are Labeling and 65,506 CMC — so filtering matters
enormously. It takes the candidate set for a mis-dated trial link from a median
of 6+ events down to a median of 3.

**What this module does not claim.** An event is a date and a class, not an
indication. Only the label filed *with that submission* names what the
submission was approved for, and those are PDFs in Drugs@FDA's
``ApplicationDocs`` — 29,838 of them — which this project does not yet read. So
the spine can prove a trial was attributed to the wrong approval and bound
which approval it belongs to; it cannot by itself say which.

Measured 2026-09-14.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import NamedTuple

from config.settings import RawData
from source.raw_table import read_rows
from utility import application_number as appno
from utility import fda_date
from utility.logging import get_logger

_log = get_logger(__name__)

#: Drugs@FDA marks an approved submission ``AP``. Everything else — tentative
#: approvals, withdrawals — is not an approval and is not an event here.
APPROVED: str = "AP"

#: The submission class that changes what a drug is approved to treat. Held as
#: the description rather than the code id, because the code ids are not stable
#: across releases while the descriptions are what the lookup table states.
EFFICACY: str = "Efficacy"

#: An original submission is indication-changing whatever its class: it is the
#: approval that created the indication in the first place.
ORIGINAL: str = "ORIG"


class EventRow(NamedTuple):
    """One approval event, as ``approval_events.tsv`` holds it."""

    application: str
    app_type: str
    submission_type: str
    submission_no: str
    submission_class: str
    approval_date: str
    indication_changing: str


class EventsReport(NamedTuple):
    """The spine, plus what the pass measured."""

    rows: tuple[EventRow, ...]
    submissions_seen: int
    approved: int
    applications: int
    indication_changing: int
    by_class: tuple[tuple[str, int], ...]
    most_supplemented: tuple[tuple[str, int], ...]


def _classes(data: RawData) -> dict[str, str]:
    """``SubmissionClassCodeID`` → its description, from the lookup table."""
    out: dict[str, str] = {}
    for row in read_rows(data.submission_class_lookup):
        code = (row.get("SubmissionClassCodeID") or "").strip()
        if code:
            out[code] = (row.get("SubmissionClassCodeDescription") or "").strip()
    return out


def is_indication_changing(submission_type: str, submission_class: str) -> bool:
    """Whether this event can change what the drug is approved to treat.

    >>> is_indication_changing("ORIG", "Type 1 - New Molecular Entity")
    True
    >>> is_indication_changing("SUPPL", "Efficacy")
    True
    >>> is_indication_changing("SUPPL", "Labeling")
    False
    >>> is_indication_changing("SUPPL", "Manufacturing (CMC)")
    False
    """
    return submission_type.strip() == ORIGINAL or submission_class.strip() == EFFICACY


def build(data: RawData) -> EventsReport:
    """Read ``Submissions.txt`` once and return every approval event."""
    classes = _classes(data)
    types: dict[str, str] = {}
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row.get("ApplType"), row.get("ApplNo"))
        if parsed.is_application:
            types[parsed.canonical] = parsed.app_type
            canonical_of[parsed.digits] = parsed.canonical

    rows: list[EventRow] = []
    by_class: Counter[str] = Counter()
    seen = approved = 0
    per_application: Counter[str] = Counter()
    for row in read_rows(data.submissions):
        seen += 1
        if (row.get("SubmissionStatus") or "").strip() != APPROVED:
            continue
        approved += 1
        application = canonical_of.get(appno.pad(row.get("ApplNo")))
        if not application:
            continue
        submission_type = (row.get("SubmissionType") or "").strip()
        submission_class = classes.get(
            (row.get("SubmissionClassCodeID") or "").strip(), ""
        )
        by_class[submission_class or "(none stated)"] += 1
        changing = is_indication_changing(submission_type, submission_class)
        parsed_date = fda_date.parse(
            row.get("SubmissionStatusDate") or "", direction=fda_date.PAST_ONLY
        )
        if changing:
            per_application[application] += 1
        rows.append(
            EventRow(
                application=application,
                app_type=types.get(application, ""),
                submission_type=submission_type,
                submission_no=(row.get("SubmissionNo") or "").strip(),
                submission_class=submission_class,
                approval_date=str(parsed_date.value) if parsed_date.value else "",
                indication_changing="true" if changing else "false",
            )
        )
    ordered = tuple(sorted(rows))
    _log.info("%d approval events over %d applications", len(ordered), len(per_application))
    return EventsReport(
        rows=ordered,
        submissions_seen=seen,
        approved=approved,
        applications=len({row.application for row in ordered}),
        indication_changing=sum(1 for row in ordered if row.indication_changing == "true"),
        by_class=tuple(by_class.most_common(10)),
        most_supplemented=tuple(per_application.most_common(5)),
    )


def indication_changing_dates(report: EventsReport) -> dict[str, list[str]]:
    """Application → the sorted dates of its indication-changing approvals.

    The shape :mod:`service.attribution` needs: for a trial that began after an
    application's original approval, which later approvals could it have
    supported?
    """
    out: dict[str, list[str]] = defaultdict(list)
    for row in report.rows:
        if row.indication_changing == "true" and row.approval_date:
            out[row.application].append(row.approval_date)
    return {application: sorted(dates) for application, dates in out.items()}


def render(report: EventsReport) -> str:
    """The coverage report, as ``--mode approval-events`` prints it."""
    lines = [
        "Approval events — one row per FDA approval, not per application",
        "",
        f"  submissions read                         {report.submissions_seen:>9,}",
        f"    approved (status AP)                   {report.approved:>9,}",
        f"  events on a typed application            {len(report.rows):>9,}",
        f"    applications covered                   {report.applications:>9,}",
        f"    indication-changing (ORIG or Efficacy) {report.indication_changing:>9,}",
        "",
        "  by submission class:",
    ]
    for name, count in report.by_class:
        lines.append(f"    {name:<40}{count:>9,}")
    lines += ["", "  most-supplemented applications:"]
    for application, count in report.most_supplemented:
        lines.append(f"    {application:<40}{count:>9,}")
    return "\n".join(lines)


__all__ = [
    "APPROVED",
    "EFFICACY",
    "ORIGINAL",
    "EventRow",
    "EventsReport",
    "build",
    "indication_changing_dates",
    "is_indication_changing",
    "render",
]
