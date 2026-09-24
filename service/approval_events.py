"""The approval **event** spine: one row per approval, not one per application.

An application is not a single approval. Keytruda's ``BLA125514`` has 127
approved submissions — one original and 108 efficacy supplements — spread over
2014 to 2026, and it gained 25 indications after the original. Recording only
``first_approval`` per application collapses twelve years of separate FDA
decisions into one date, and then attributes every trial to it.

**Which events are trial-driven is decided by the submission class code (D23).**
Every submission carries exactly one ``SubmissionClassCode``; action types are
recorded only on supplements — all 5,879 approved NDA/BLA originals join to a
lookup row whose description is blank — so the class is the one field that
describes both. An original is in scope when its class is one of
:data:`ORIG_SCOPE_CODES` (a new molecule, ingredient, combination, dosage form,
formulation or indication; ``UNKNOWN`` and blank included, since ``UNKNOWN`` is
mostly recent approvals FDA has not yet classed). A supplement is in scope when
its class is one of :data:`SUPPL_SCOPE_CODES`, unless its only efficacy actions
are the two that leave the drug's efficacy claim unchanged
(:data:`OUT_OF_SCOPE_ACTIONS`: a new route, an Rx-to-OTC switch).

**Two tiers of in-scope (D24).** A supplement whose only efficacy actions are
:data:`GUARD_ACTIONS` — a labeling or manufacturing change *with clinical data*
— rests on named studies but supports a label claim rather than a new use. It
is ``trial_scope = guard``: P2 reads its label so that D16 sees the trials it
carries, label-stated NCT ids link, and P4 does not judge its prose. Everything
else in scope is ``trial_scope = full``.

Measured on the 2026-09-07 release, approved NDA/BLA: 5,766 originals + 4,009
full supplements + 1,520 guard supplements = 11,295 trial-relevant events,
against D4's 7,921. Match on the **code**, never the id (ids are release-local)
nor the description: ``BIOSIMILAR`` and ``TYPE 10- BLA`` have an empty
description in the lookup.

**What this module does not claim.** An event is a date, a class and an action,
not an indication. Only the label filed *with that submission* names what it was
approved for; those are the type-2 PDFs in ``ApplicationDocs`` that P2 reads.

Measured 2026-09-15.
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

#: The application types this project maps. ANDAs are excluded because generics
#: are approved on bioequivalence studies, which are small and unregistered.
SPINE_TYPES: frozenset[str] = frozenset({"NDA", "BLA"})

#: Original-submission class codes that are trial-relevant (D23). Everything a
#: new approval can be except a partial Rx-to-OTC switch (``TYPE 8``), a medical
#: gas (``MEDGAS``) and a supplement code misapplied to an original (``S``).
#: ``BIOEQUIV`` and ``BIOSIMILAR`` occur on no original today; they are listed so
#: a future release cannot silently drop them. The empty string is a blank class.
ORIG_SCOPE_CODES: frozenset[str] = frozenset(
    {
        "EFFICACY", "BIOEQUIV", "BIOSIMILAR", "UNKNOWN", "",
        "TYPE 1", "TYPE 1/4", "TYPE 2", "TYPE 2/3", "TYPE 2/4",
        "TYPE 3", "TYPE 3/4", "TYPE 4", "TYPE 4/5", "TYPE 5",
        "TYPE 6", "TYPE 7", "TYPE 9", "TYPE 9- BLA", "TYPE 10", "TYPE 10- BLA",
    }
)

#: Supplement class codes that can be trial-relevant (D23). ``BIOSIMILAR``
#: carries real efficacy supplements (two new indications in this release).
SUPPL_SCOPE_CODES: frozenset[str] = frozenset({"EFFICACY", "BIOSIMILAR", "BIOEQUIV"})

#: Efficacy actions that leave the efficacy claim unchanged. A supplement whose
#: efficacy actions are *only* these is out of scope (D23).
OUT_OF_SCOPE_ACTIONS: frozenset[str] = frozenset(
    {"Efficacy-New Route Of Administration", "Efficacy-Rx To OTC Switch"}
)

#: Efficacy actions whose supplement is in scope only as a D16 guard (D24).
GUARD_ACTIONS: frozenset[str] = frozenset(
    {
        "Efficacy-Labeling Change With Clinical Data",
        "Efficacy-Manufacturing Change With Clinical Data",
    }
)

#: ``trial_scope`` values: the full pipeline, or P2 + label-stated ids only.
SCOPE_FULL: str = "full"
SCOPE_GUARD: str = "guard"

#: Action descriptions of the efficacy family begin with this.
EFFICACY_ACTION_PREFIX: str = "Efficacy"

#: The only non-null property code in this snapshot.
ORPHAN: str = "Orphan"


class EventRow(NamedTuple):
    """One approval event, as ``approval_events.tsv`` holds it."""

    application: str
    app_type: str
    submission_type: str
    submission_no: str
    submission_class: str
    action_type: str
    approval_date: str
    era: str
    review_priority: str
    orphan: str
    indication_changing: str
    trial_relevant: str
    #: The submission's ``SubmissionClassCode`` (``TYPE 1``, ``EFFICACY``…).
    class_code: str = ""
    #: ``full``, ``guard`` or empty when not trial-relevant (D23, D24).
    trial_scope: str = ""
    #: Why the row is in or out of scope, for audit.
    scope_basis: str = ""


class EventsReport(NamedTuple):
    """The spine, plus what the pass measured."""

    rows: tuple[EventRow, ...]
    submissions_seen: int
    approved: int
    applications: int
    indication_changing: int
    trial_relevant: int
    originals: int
    core_supplements: int
    by_class: tuple[tuple[str, int], ...]
    by_action: tuple[tuple[str, int], ...]
    by_era: tuple[tuple[str, int], ...]
    most_supplemented: tuple[tuple[str, int], ...]
    #: Supplements in scope only as a D16 guard; ``core_supplements`` counts the
    #: full-pipeline ones.
    guard_supplements: int = 0


def era_of(approval_date: str) -> str:
    """The registration era a date falls in, as a column rather than a filter.

    ClinicalTrials.gov opened in 2000, ICMJE made registration a condition of
    publication in 2005 and FDAAA made it law in 2007. Trials behind earlier
    approvals were never registered, so linkability falls off a cliff going
    back — but that is a fact to record, not a reason to drop the rows.

    >>> era_of("1994-03-01")
    'pre-2000'
    >>> era_of("2004-11-30")
    '2000-2007'
    >>> era_of("2018-08-20")
    '2008+'
    >>> era_of("")
    ''
    """
    if len(approval_date) < 4 or not approval_date[:4].isdigit():
        return ""
    year = int(approval_date[:4])
    if year < 2000:
        return "pre-2000"
    if year < 2008:
        return "2000-2007"
    return "2008+"


def _classes(data: RawData) -> dict[str, tuple[str, str]]:
    """``SubmissionClassCodeID`` → ``(SubmissionClassCode, description)``."""
    out: dict[str, tuple[str, str]] = {}
    for row in read_rows(data.submission_class_lookup):
        code = (row.get("SubmissionClassCodeID") or "").strip()
        if code:
            out[code] = (
                (row.get("SubmissionClassCode") or "").strip(),
                (row.get("SubmissionClassCodeDescription") or "").strip(),
            )
    return out


def _action_descriptions(data: RawData) -> dict[str, str]:
    """``ActionTypes_LookupID`` → its description.

    Several ids share a description (there are four ``Labeling-Container/Carton
    Labels`` rows), and id 46 is blank — the one originals join to.
    """
    out: dict[str, str] = {}
    for row in read_rows(data.action_types_lookup):
        code = (row.get("ActionTypes_LookupID") or "").strip()
        if code:
            out[code] = (row.get("ActionTypes_LookupDescription") or "").strip()
    return out


def _key(application_digits: str, submission_type: str, submission_no: str) -> str:
    """The submission key, with the padding Drugs@FDA leaves on ``SubmissionType``."""
    return f"{application_digits}|{submission_type.strip()}|{submission_no.strip()}"


def _actions(data: RawData) -> dict[str, set[str]]:
    """Submission key → the action descriptions recorded against it.

    A submission may carry several; the join table is one row per pairing.
    """
    descriptions = _action_descriptions(data)
    out: dict[str, set[str]] = defaultdict(set)
    for row in read_rows(data.submission_action_types):
        described = descriptions.get((row.get("ActionTypes_LookupID") or "").strip(), "")
        if not described:
            continue
        key = _key(
            appno.pad(row.get("ApplNo")),
            row.get("SubmissionType") or "",
            row.get("SubmissionNo") or "",
        )
        out[key].add(described)
    return out


def _orphans(data: RawData) -> set[str]:
    """The submission keys flagged ``Orphan``."""
    out: set[str] = set()
    for row in read_rows(data.submission_property_type):
        if (row.get("SubmissionPropertyTypeCode") or "").strip() != ORPHAN:
            continue
        out.add(
            _key(
                appno.pad(row.get("ApplNo")),
                row.get("SubmissionType") or "",
                row.get("SubmissionNo") or "",
            )
        )
    return out


def is_indication_changing(submission_type: str, submission_class: str) -> bool:
    """Whether this event can change what the drug is approved to treat.

    >>> is_indication_changing("ORIG", "Type 1 - New Molecular Entity")
    True
    >>> is_indication_changing("SUPPL", "Efficacy")
    True
    >>> is_indication_changing("SUPPL", "Labeling")
    False
    """
    return submission_type.strip() == ORIGINAL or submission_class.strip() == EFFICACY


def scope_of(
    submission_type: str, class_code: str, actions: frozenset[str] | set[str]
) -> tuple[str, str]:
    """``(trial_scope, scope_basis)`` for one approved submission (D23, D24).

    ``trial_scope`` is ``full``, ``guard`` or ``""`` (not trial-relevant).

    >>> scope_of("ORIG", "TYPE 1", set())
    ('full', 'ORIG class TYPE 1')
    >>> scope_of("ORIG", "MEDGAS", set())
    ('', 'ORIG class MEDGAS not in scope')
    >>> scope_of("SUPPL", "EFFICACY", {"Efficacy-New Patient Population"})
    ('full', 'SUPPL EFFICACY: Efficacy-New Patient Population')
    >>> scope_of("SUPPL", "EFFICACY", {"Efficacy-Labeling Change With Clinical Data"})
    ('guard', 'SUPPL EFFICACY: Efficacy-Labeling Change With Clinical Data')
    >>> scope_of("SUPPL", "EFFICACY", {"Efficacy-Rx To OTC Switch"})
    ('', 'SUPPL EFFICACY: only Efficacy-Rx To OTC Switch')
    >>> scope_of("SUPPL", "LABELING", {"Labeling"})
    ('', 'SUPPL class LABELING not in scope')
    """
    submission_type = submission_type.strip()
    class_code = class_code.strip()
    if submission_type == ORIGINAL:
        if class_code in ORIG_SCOPE_CODES:
            return SCOPE_FULL, f"ORIG class {class_code or '(blank)'}"
        return "", f"ORIG class {class_code} not in scope"
    if class_code not in SUPPL_SCOPE_CODES:
        return "", f"SUPPL class {class_code or '(blank)'} not in scope"
    efficacy = sorted(a for a in actions if a.startswith(EFFICACY_ACTION_PREFIX))
    listed = "; ".join(efficacy) or "no efficacy action"
    if efficacy and set(efficacy) <= OUT_OF_SCOPE_ACTIONS:
        return "", f"SUPPL {class_code}: only {listed}"
    if efficacy and set(efficacy) <= GUARD_ACTIONS:
        return SCOPE_GUARD, f"SUPPL {class_code}: {listed}"
    return SCOPE_FULL, f"SUPPL {class_code}: {listed}"


def is_trial_relevant(
    submission_type: str, class_code: str, actions: frozenset[str] | set[str]
) -> bool:
    """Whether this approval is in scope at all, guard or full.

    >>> is_trial_relevant("ORIG", "UNKNOWN", set())
    True
    >>> is_trial_relevant("ORIG", "TYPE 8", set())
    False
    >>> is_trial_relevant("SUPPL", "EFFICACY", {"Efficacy-New Indication"})
    True
    >>> is_trial_relevant("SUPPL", "MANUF (CMC)", {"Manufacturing (CMC)-Facility"})
    False
    """
    return bool(scope_of(submission_type, class_code, actions)[0])


def build(data: RawData, types: frozenset[str] | None = SPINE_TYPES) -> EventsReport:
    """Read ``Submissions.txt`` once and return every approval event.

    ``types`` restricts the spine to the application types this project maps;
    pass ``None`` for every type, which is what :mod:`service.attribution`
    needs so its existing ANDA rows keep their candidate dates.
    """
    classes = _classes(data)
    actions = _actions(data)
    orphans = _orphans(data)
    app_types: dict[str, str] = {}
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row.get("ApplType"), row.get("ApplNo"))
        if parsed.is_application:
            app_types[parsed.canonical] = parsed.app_type
            canonical_of[parsed.digits] = parsed.canonical

    rows: list[EventRow] = []
    by_class: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    by_era: Counter[str] = Counter()
    seen = approved = 0
    per_application: Counter[str] = Counter()
    for row in read_rows(data.submissions):
        seen += 1
        if (row.get("SubmissionStatus") or "").strip() != APPROVED:
            continue
        approved += 1
        digits = appno.pad(row.get("ApplNo"))
        application = canonical_of.get(digits)
        if not application:
            continue
        app_type = app_types.get(application, "")
        if types is not None and app_type not in types:
            continue
        submission_type = (row.get("SubmissionType") or "").strip()
        submission_no = (row.get("SubmissionNo") or "").strip()
        class_code, submission_class = classes.get(
            (row.get("SubmissionClassCodeID") or "").strip(), ("", "")
        )
        key = _key(digits, submission_type, submission_no)
        acted = actions.get(key, set())
        by_class[submission_class or "(none stated)"] += 1
        for name in acted:
            by_action[name] += 1
        changing = is_indication_changing(submission_type, submission_class)
        scope, basis = scope_of(submission_type, class_code, acted)
        parsed_date = fda_date.parse(
            row.get("SubmissionStatusDate") or "", direction=fda_date.PAST_ONLY
        )
        approval_date = str(parsed_date.value) if parsed_date.value else ""
        era = era_of(approval_date)
        by_era[era or "(undated)"] += 1
        if changing:
            per_application[application] += 1
        rows.append(
            EventRow(
                application=application,
                app_type=app_type,
                submission_type=submission_type,
                submission_no=submission_no,
                submission_class=submission_class,
                action_type=";".join(sorted(acted)),
                approval_date=approval_date,
                era=era,
                review_priority=(row.get("ReviewPriority") or "").strip(),
                orphan="true" if key in orphans else "false",
                indication_changing="true" if changing else "false",
                trial_relevant="true" if scope else "false",
                class_code=class_code,
                trial_scope=scope,
                scope_basis=basis,
            )
        )
    ordered = tuple(sorted(rows))
    relevant_rows = [row for row in ordered if row.trial_relevant == "true"]
    _log.info(
        "%d approval events over %d applications; %d trial-relevant",
        len(ordered),
        len(per_application),
        len(relevant_rows),
    )
    return EventsReport(
        rows=ordered,
        submissions_seen=seen,
        approved=approved,
        applications=len({row.application for row in ordered}),
        indication_changing=sum(1 for row in ordered if row.indication_changing == "true"),
        trial_relevant=len(relevant_rows),
        originals=sum(1 for row in relevant_rows if row.submission_type == ORIGINAL),
        core_supplements=sum(
            1 for row in relevant_rows
            if row.submission_type != ORIGINAL and row.trial_scope == SCOPE_FULL
        ),
        by_class=tuple(by_class.most_common(10)),
        by_action=tuple(by_action.most_common(12)),
        by_era=tuple(sorted(by_era.items())),
        most_supplemented=tuple(per_application.most_common(5)),
        guard_supplements=sum(1 for row in relevant_rows if row.trial_scope == SCOPE_GUARD),
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
        f"  events in scope (NDA + BLA)              {len(report.rows):>9,}",
        f"    applications covered                   {report.applications:>9,}",
        f"    indication-changing (ORIG or Efficacy) {report.indication_changing:>9,}",
        "",
        f"  TRIAL-RELEVANT                           {report.trial_relevant:>9,}",
        f"    originals                              {report.originals:>9,}",
        f"    supplements, full pipeline             {report.core_supplements:>9,}",
        f"    supplements, D16 guard only (D24)      {report.guard_supplements:>9,}",
        "",
        "  by era:",
    ]
    for name, count in report.by_era:
        lines.append(f"    {name:<40}{count:>9,}")
    lines += ["", "  by submission class:"]
    for name, count in report.by_class:
        lines.append(f"    {name:<40}{count:>9,}")
    lines += ["", "  by action type:"]
    for name, count in report.by_action:
        lines.append(f"    {name:<40}{count:>9,}")
    lines += ["", "  most-supplemented applications:"]
    for application, count in report.most_supplemented:
        lines.append(f"    {application:<40}{count:>9,}")
    return "\n".join(lines)


__all__ = [
    "APPROVED",
    "EFFICACY",
    "ORIGINAL",
    "ORPHAN",
    "EFFICACY_ACTION_PREFIX",
    "GUARD_ACTIONS",
    "ORIG_SCOPE_CODES",
    "OUT_OF_SCOPE_ACTIONS",
    "SCOPE_FULL",
    "SCOPE_GUARD",
    "SPINE_TYPES",
    "SUPPL_SCOPE_CODES",
    "EventRow",
    "EventsReport",
    "build",
    "era_of",
    "indication_changing_dates",
    "is_indication_changing",
    "is_trial_relevant",
    "render",
    "scope_of",
]
