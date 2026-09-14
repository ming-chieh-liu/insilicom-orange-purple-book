"""Which *approval* did a trial support — not merely which application.

``trial_approval_map.tsv`` stamps every row with the application's
``first_approval``. For 69.1% of its rows that application went on to receive
at least one efficacy supplement, so the date names an approval the trial may
have had nothing to do with. Pembrolizumab is the clearest case: all 51 of its
trial rows read ``2014-09-04``, the melanoma approval, including trials that
began years later and supported entirely different indications.

**The refutation is arithmetic, not judgement.** A trial cannot have supported
an approval granted before the trial began. So where the trial's start date is
after the stamped approval date, that attribution is *wrong* — provably, with
no model or heuristic involved. Measured: 534 of 2,679 rows, 19.9%.

**The repair is only partial, and the row says which.** Once a row is refuted,
the supporting approval must be one of the application's indication-changing
events dated at or after the trial started. Sometimes exactly one qualifies and
the row is corrected outright; usually several do, and then the honest output
is the window, not a guess:

    resolved     one indication-changing approval fits          52 rows
    ambiguous    several fit; the earliest and count are given 477 rows
    unresolved   none fits — the spine has no event that late     5 rows
    original     trial predates the approval; attribution stands

Naming a single approval where several fit would be a guess dressed as a fact,
and this project has already found what that costs — the section-14 regex
asserted evidence it could not support, and 12% of its links turned out not to
be evidence at all.

**What would finish the job.** The label filed *with* a submission names the
trials behind it. Drugs@FDA publishes 29,838 such labels as PDFs, keyed by
application plus submission number — the key this module emits. Reading them is
the step that would turn ``ambiguous`` into ``resolved``.

Measured 2026-09-14.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: The trial predates the stamped approval, so the original attribution is not
#: refuted. Not a claim that it is *right* — only that arithmetic cannot deny it.
ORIGINAL: str = "original"

#: Refuted, and exactly one indication-changing approval fits.
RESOLVED: str = "resolved"

#: Refuted, and several fit. The window is emitted instead of a choice.
AMBIGUOUS: str = "ambiguous"

#: Refuted, and the spine holds no indication-changing approval that late.
UNRESOLVED: str = "unresolved"

#: The trial archive has no start date for this study, so nothing is checkable.
UNDATED: str = "undated"


class AttributionRow(NamedTuple):
    """One trial → approval claim, re-examined against the approval events."""

    nct_id: str
    application: str
    app_type: str
    drug: str
    unii: str
    sponsor: str
    has_results: str
    polarity: str
    #: What the map said.
    stamped_approval: str
    #: When the trial began, from the archive's study documents.
    trial_start: str
    #: One of the five constants above.
    attribution: str
    #: For ``resolved``, the approval it belongs to. For ``ambiguous``, the
    #: earliest that fits — the start of the window, not a choice.
    approval_date: str
    #: How many indication-changing approvals fit. 1 when resolved.
    candidate_approvals: str


class AttributionReport(NamedTuple):
    """The re-examined table, plus what it found."""

    rows: tuple[AttributionRow, ...]
    counts: tuple[tuple[str, int], ...]
    refuted: int
    applications_touched: int


def classify(
    stamped: str, trial_start: str, later_events: list[str]
) -> tuple[str, str, int]:
    """``(attribution, approval_date, candidates)`` for one row.

    ``later_events`` are the application's indication-changing approval dates
    at or after ``trial_start``, sorted.

    >>> classify("2014-09-04", "2016-03-01", ["2018-08-20"])
    ('resolved', '2018-08-20', 1)
    >>> classify("2014-09-04", "2016-03-01", ["2018-08-20", "2020-06-10"])
    ('ambiguous', '2018-08-20', 2)
    >>> classify("2014-09-04", "2016-03-01", [])
    ('unresolved', '', 0)
    >>> classify("2014-09-04", "2011-01-01", ["2018-08-20"])
    ('original', '2014-09-04', 0)
    >>> classify("2014-09-04", "", [])
    ('undated', '2014-09-04', 0)
    """
    if not trial_start:
        return UNDATED, stamped, 0
    if not stamped or trial_start <= stamped:
        return ORIGINAL, stamped, 0
    if not later_events:
        return UNRESOLVED, "", 0
    if len(later_events) == 1:
        return RESOLVED, later_events[0], 1
    return AMBIGUOUS, later_events[0], len(later_events)


def attribute(
    map_rows: list[dict[str, str]],
    events: dict[str, list[str]],
    start_dates: dict[str, str],
) -> AttributionReport:
    """Re-examine every mapping row against the approval-event spine.

    ``map_rows`` are ``trial_approval_map.tsv`` as ``csv.DictReader`` yields
    them, so this reads the shipped table rather than rebuilding it — the same
    shape ``adjudicate-prepare`` uses against ``links.tsv``.
    """
    rows: list[AttributionRow] = []
    counts: Counter[str] = Counter()
    touched: set[str] = set()
    for row in map_rows:
        application = row.get("application", "")
        stamped = row.get("first_approval", "")
        start = start_dates.get(row.get("nct_id", ""), "")
        later = [date for date in events.get(application, ()) if start and date >= start]
        attribution, approval_date, candidates = classify(stamped, start, later)
        counts[attribution] += 1
        if attribution in (RESOLVED, AMBIGUOUS, UNRESOLVED):
            touched.add(application)
        rows.append(
            AttributionRow(
                nct_id=row.get("nct_id", ""),
                application=application,
                app_type=row.get("app_type", ""),
                drug=row.get("drug", ""),
                unii=row.get("unii", ""),
                sponsor=row.get("sponsor", ""),
                has_results=row.get("has_results", ""),
                polarity=row.get("polarity", ""),
                stamped_approval=stamped,
                trial_start=start,
                attribution=attribution,
                approval_date=approval_date,
                candidate_approvals=str(candidates),
            )
        )
    refuted = sum(counts[name] for name in (RESOLVED, AMBIGUOUS, UNRESOLVED))
    _log.info("%d of %d rows refuted by trial start date", refuted, len(rows))
    return AttributionReport(
        rows=tuple(rows),
        counts=tuple(counts.most_common()),
        refuted=refuted,
        applications_touched=len(touched),
    )


def render(report: AttributionReport) -> str:
    """The report, as ``--mode attribute`` prints it."""
    total = len(report.rows)
    lines = [
        "Trial → approval attribution, checked against the approval events",
        "",
        f"  rows examined                            {total:>9,}",
        f"  refuted (trial began after the stamped approval) {report.refuted:>3,}"
        f"   {report.refuted / total:>6.1%}" if total else "",
        f"  applications affected                    {report.applications_touched:>9,}",
        "",
        "  outcome:",
    ]
    explain = {
        ORIGINAL: "trial predates the approval; attribution stands",
        RESOLVED: "one later approval fits; row corrected",
        AMBIGUOUS: "several fit; window given, not a guess",
        UNRESOLVED: "no indication-changing approval that late",
        UNDATED: "no start date in the archive",
    }
    for name, count in report.counts:
        lines.append(
            f"    {name:<12}{count:>8,}  {count / total:>6.1%}  {explain.get(name, '')}"
            if total
            else f"    {name:<12}{count:>8,}"
        )
    return "\n".join(line for line in lines if line)


__all__ = [
    "AMBIGUOUS",
    "ORIGINAL",
    "RESOLVED",
    "UNDATED",
    "UNRESOLVED",
    "AttributionReport",
    "AttributionRow",
    "attribute",
    "classify",
    "render",
]
