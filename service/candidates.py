"""Narrow 600,762 registered studies to a shortlist an agent can judge.

P2 leaves 22,666 descriptions of trials, and 16,478 of them name no registry
id — only prose: *"investigated in KEYNOTE-189, a randomized, double-blind,
active-controlled trial conducted in 616 patients with metastatic nonsquamous
NSCLC"*. Nobody, human or model, can pick that out of 600,762 studies. This
module cuts the field down until comparison is possible.

**Four filters, in decreasing cheapness.** Measured on five real drug and
condition pairs spanning oncology and non-oncology:

======================  ======  ======  ======  ======  ======
filter                   pembro  ramuc   semag   nivo    apix
======================  ======  ======  ======  ======  ======
drug alone                2,458     171     585     ...     ...
+ condition                 545      68     308     ...     ...
+ phase                     124      14      88     ...     ...
+ started before approval    16      11      33      14      10
======================  ======  ======  ======  ======  ======

The date filter is the one that earns its place: **a trial cannot have
supported an approval granted before it began.** That is a hard, checkable
fact, not a heuristic.

**And then enrollment settles it.** Across all five candidate sets above, every
candidate had a distinct patient count — no collisions at all. So the ``616``
in the label prose identifies KEYNOTE-189 among sixteen candidates on its own.
This module does not *use* enrollment to decide; it carries it, because
deciding is P4's job and a filter that silently dropped the right answer would
be worse than a shortlist.

**Descriptions are collapsed first.** A label describes one trial across
several sentences — the Keytruda spot check yields three rows all naming
KEYNOTE-189 — so the 22,666 rows become 12,380 distinct
``(submission, trial)`` units. At two judges each that is the difference
between 45,000 agent calls and 25,000, before the rows that already state an
NCT id are set aside as needing no judgement at all.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData, TrialArchive
from service import approval_events, normalize, section14
from source import trial_studies
from utility.logging import get_logger

_log = get_logger(__name__)

#: How many candidates a judge is shown. Swept against the 6,368 known answers
#: after the ranking rewrite, reporting recall over the units P4 actually
#: judges and the rows each judge reads:
#:
#: ====  ========  =========  ==================
#: cap   recall    rows/unit  gain per +10 rows
#: ====  ========  =========  ==================
#: 40      70.7%       36.1   --
#: 60      74.9%       52.3   +2.6 pts
#: 100     78.4%       82.0   +1.2 pts
#: 150     80.5%      115.6   +0.6 pts
#: 250     82.1%      174.4   +0.3 pts
#: ====  ========  =========  ==================
#:
#: 100 is the knee. **A higher cap can only raise recall**, so the number to
#: judge on is cost, not recall: beyond 100 each further 10 rows buys under a
#: point. For comparison the whole ranking rewrite bought 2.4 points in the
#: pipeline, and this constant buys 7.7.
#:
#: **Not re-measured for precision.** The pilot's 96.3% was measured at 40, and
#: a judge choosing among 2.3x more candidates has more chances to pick wrong.
#: Raising this without re-running that pilot was a deliberate call, not an
#: oversight.
CANDIDATE_CAP: int = 100

#: D35 cases: a §14 description (B), an indication with no judgeable §14
#: description (C), no indication at all (D). Case A needs no unit of its own:
#: it is a B-shaped description whose label or review states the trial.
CASE_B: str = "B"
CASE_C: str = "C"
CASE_D: str = "D"

#: Case D's literature candidates, written by ``--mode graph-links`` (D35).
DRUG_ONLY_FILE: str = "drug_only_trials.tsv"

#: Where :func:`collapse` records the name it keyed on, so ``build`` and the
#: P4 reader use the same one rather than each deriving it again.
TRIAL_KEY: str = "trial_key"

#: Trial phases that can support an approval. Phase 1 and 2 trials do support
#: accelerated approvals, so nothing is excluded by phase alone — the field is
#: carried for the judge rather than filtered on.
PHASES_KEPT: frozenset[str] = frozenset(
    {"PHASE1", "PHASE2", "PHASE3", "PHASE4", "PHASE1_PHASE2", "PHASE2_PHASE3", "NA"}
)


class Candidate(NamedTuple):
    """One registered study, as a judge needs to see it."""

    nct_id: str
    brief_title: str
    acronym: str
    org_study_id: str
    phases: str
    enrollment: str
    start_date: str
    conditions: str
    interventions: str
    #: Whether the study looks like a biosimilar's own trial rather than the
    #: reference product's. Set when an intervention name carries the
    #: four-letter biologic suffix or the title says "biosimilar". Used only to
    #: order the shortlist for an approval whose drug resolved via its stem —
    #: see :func:`shortlist`.
    biosimilar: bool = False
    #: Whether this study reached the shortlist through the literature rather
    #: than the drug filter. The drug filter is one of only two hard facts the
    #: pool rests on, so a candidate that skipped it is weaker evidence and P5
    #: holds its link below tier A — the same guard stem-derived drugs get.
    injected: bool = False
    #: The registering sponsor and the primary completion date, shown to the
    #: per-candidate judge (P4 v2, D33) alongside the fields above.
    lead_sponsor: str = ""
    primary_completion: str = ""
    #: Case D (D35): the diseases whose treatment literature named this trial,
    #: so a judge sees what it is about when the approval's indication is not
    #: recorded.
    found_under: str = ""


class Unit(NamedTuple):
    """One trial description and the studies it could be."""

    unit_id: str
    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    heading: str
    indication_mesh: str
    drug_mesh: str
    stated_nct: str
    acronym: str
    enrollment: str
    excerpt: str
    candidates: tuple[Candidate, ...]
    #: Digest of the label PDF this description came from, so P5 can tell one
    #: label counted several times from several labels sharing a date.
    label_sha: str = ""
    #: The trial the same submission's FDA review identified by protocol id,
    #: when the label's trial name matches that id or the registry acronym
    #: (D29). Set only where :attr:`stated_nct` is empty.
    review_nct: str = ""
    #: Every disease this description is ranked on (D34): all its §14
    #: heading's ids, else every id the submission's own letter and label §1
    #: name. :attr:`indication_mesh` stays the single id P5 reports.
    indication_ids: tuple[str, ...] = ()
    #: D35 case: ``B`` (a §14 description), ``C`` (indication, no judgeable
    #: §14 description) or ``D`` (no indication). A and B share one shape.
    case: str = CASE_B
    #: Cases C and D: what the judge reads instead of an excerpt — the
    #: submission's chosen indication text, and the approved ingredient names.
    indication_text: str = ""
    drug: str = ""

    @property
    def needs_judgement(self) -> bool:
        """Whether P4 has to decide this one.

        A description that states its own NCT id is already identified; sending
        it to a judge would spend two calls to confirm what the label said. One
        whose name an FDA review of the same submission already resolved is
        identified too (D29).
        """
        return not (self.stated_nct or self.review_nct)


class CandidatesReport(NamedTuple):
    """What the pass produced."""

    units: tuple[Unit, ...]
    descriptions: int
    collapsed: int
    already_identified: int
    no_drug: int
    no_candidates: int
    over_cap: int
    median_candidates: int
    #: Descriptions resolved by a same-submission review's protocol id (D29).
    review_identified: int = 0
    #: D35 routing of every trial-relevant submission: ``(key, route)``.
    routes: tuple[tuple[str, str], ...] = ()
    case_c_with_candidates: int = 0
    case_d_with_candidates: int = 0

    @property
    def to_judge(self) -> int:
        return sum(1 for unit in self.units if unit.needs_judgement)


#: A study naming a suffixed biologic, or calling itself a biosimilar study.
#: Measured over the archive: 2,879 studies name a suffixed product and 302 say
#: "biosimilar". Deliberately loose — this only orders a list, and a false
#: positive costs a judge one line while a false negative buries the answer.
BIOSIMILAR_TRIAL = re.compile(r"(?i)\b[a-z]{5,}-[a-z]{4}\b|biosimilar")


def looks_biosimilar(title: str, interventions: Iterable[str]) -> bool:
    """Whether a study is plausibly a biosimilar's own trial.

    >>> looks_biosimilar("A Study of ABP 501", ["Adalimumab-atto", "Placebo"])
    True
    >>> looks_biosimilar("Biosimilar Trastuzumab in HER2+ Breast Cancer", [])
    True
    >>> looks_biosimilar("KEYNOTE-189", ["Pembrolizumab", "Carboplatin"])
    False
    """
    return bool(BIOSIMILAR_TRIAL.search(" ; ".join([title, *interventions])))


#: Shortest label trial name that may be matched against a registry record.
#:
#: **Not load-bearing.** Swept from 4 to 8 against the 6,368 known answers and
#: recall moved by at most one unit — 70.2% at every value. The false acronyms
#: the section-14 extractor produces (``IIIC 1``, ``TABLE 2``, ``AUC0-6``) do
#: match at this floor, and that turns out to be survivable: the name key only
#: *promotes* a study into a 40-slot list, so a spurious match costs a slot
#: rather than evicting the right answer. Kept to exclude the pathological
#: one- and two-character case.
MIN_NAME_LENGTH: int = 5


def _squash(text: str) -> str:
    """Letters and digits only, lower-cased, for comparing trial names.

    A label writes ``KEYNOTE-189`` where the registry writes ``KEYNOTE 189``,
    ``Keynote-189`` or the sponsor id ``3475-189``. Punctuation and case are
    noise in that comparison; the digits are not.

    >>> _squash("KEYNOTE-189")
    'keynote189'
    >>> _squash("CheckMate 648")
    'checkmate648'
    """
    return "".join(c for c in text.lower() if c.isalnum())


def names_match(label_name: str, candidate: "Candidate") -> bool:
    """Whether the label's trial name appears in this registry record.

    Checked against the acronym, the sponsor's protocol id and the title,
    because a registry record carries the name in whichever of those the
    sponsor chose to use.

    **This is the single strongest ranking signal and it was unused.** Measured
    over the 1,489 units whose answer sat in the pool but ranked below the cap:
    the label's name appears in the true trial's record for **610 of them
    (41%)**, and for **597 (40%)** it appears in *no other study in the pool* —
    so those units would rank the right answer first instead of near 300.

    >>> c = Candidate("NCT1", "A Study of X", "KEYNOTE-189", "3475-189",
    ...               "PHASE3", "616", "2016-02-26", "", "")
    >>> names_match("KEYNOTE-189", c)
    True
    >>> names_match("TABLE 2", c)
    False
    """
    squashed = _squash(label_name)
    if len(squashed) < MIN_NAME_LENGTH:
        return False
    return squashed in _squash(
        f"{candidate.acronym} {candidate.org_study_id} {candidate.brief_title}"
    )


#: Label "names" that are a numbering, not a name: ``STUDY 1``, ``TRIAL 2``.
#: Squashed they pass :data:`MIN_NAME_LENGTH`, and a sponsor could register
#: exactly such an id, so they never resolve a description on their own.
GENERIC_NAME = re.compile(r"(?i)^(study|trial|protocol|part|table|cohort)\W*\w{0,3}$")


def review_names(out_dir: Path) -> dict[str, dict[str, str]]:
    """``application|type|number`` → squashed name → NCT, for every trial the
    submission's FDA reviews identified (D26 ``accepted`` mentions).

    Both the protocol id as the review wrote it and the matched study's
    registry acronym count as names. A name that points at two trials within
    one submission is ambiguous and dropped — an exact match or nothing.

    Every accepted mention counts, not only the review route's earliest one:
    this is identity, and D16 is applied later, in P5, over all routes.
    """
    found: dict[str, dict[str, set[str]]] = {}
    for row in _read_tsv(out_dir / "review_trial_mentions.tsv"):
        if row.get("outcome") != "accepted" or not row.get("nct_id"):
            continue
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        for name in (row.get("protocol_id") or "", row.get("acronym") or ""):
            squashed = _squash(name)
            if len(squashed) >= MIN_NAME_LENGTH and not GENERIC_NAME.match(name.strip()):
                found.setdefault(key, {}).setdefault(squashed, set()).add(row["nct_id"])
    return {
        key: {name: next(iter(ncts)) for name, ncts in names.items() if len(ncts) == 1}
        for key, names in found.items()
    }


def review_match(label_name: str, names: dict[str, str]) -> str:
    """The NCT a review resolved for this label trial name, or ``""``.

    >>> review_match("MCP-103-302", {"mcp103302": "NCT1"})
    'NCT1'
    >>> review_match("STUDY 1", {"study1": "NCT2"})
    ''
    >>> review_match("KEYNOTE", {"keynote189": "NCT3"})
    ''
    """
    if not label_name or GENERIC_NAME.match(label_name.strip()):
        return ""
    return names.get(_squash(label_name), "")


def _years_before(start: str, approval: str) -> int:
    """Whole years between a trial starting and an approval, or a large number.

    A trial that began twenty years before an approval is rarely its evidence,
    while one that began three years before usually is. Used only to order the
    pool, never to exclude: the date *filter* has already dropped anything
    starting after the approval.

    >>> _years_before("2016-02-26", "2018-08-20")
    2
    >>> _years_before("", "2018-08-20")
    999
    """
    if not start or not approval:
        return 999
    try:
        return int(approval[:4]) - int(start[:4])
    except ValueError:  # pragma: no cover - a malformed date in the archive
        return 999


def graph_supported(out_dir: Path) -> dict[str, frozenset[str]]:
    """Application → the trials the literature connects to its drug.

    Read from ``graph_trials.tsv``, which :mod:`service.graph_trials` writes in
    one Mongo pass. An absent file yields nothing, so P3 runs unchanged where
    the precompute has not been done.
    """
    out: dict[str, set[str]] = {}
    for row in _read_tsv(out_dir / "graph_trials.tsv"):
        out.setdefault(row["application"], set()).add(row["nct_id"])
    return {app: frozenset(ncts) for app, ncts in out.items()}


def pick_indication(heading_ids: list[str], chosen_ids: list[str]) -> str:
    """The one disease a trial description is ranked on, or ``""`` (D30).

    Its own §14 heading first, when that names exactly one disease; two
    diseases in one heading are evidence for neither. When the heading names
    none ("14.1 Study 1", a trial name), the submission's chosen indication —
    its letter or its label's section 1 — stands in, again only if it is one.

    >>> pick_indication(["MESH:D002289"], ["MESH:D008545"])
    'MESH:D002289'
    >>> pick_indication(["MESH:D008545", "MESH:D008175"], ["MESH:D002289"])
    ''
    >>> pick_indication([], ["MESH:D002289"])
    'MESH:D002289'
    >>> pick_indication([], ["MESH:D002289", "MESH:D008545"])
    ''
    """
    if heading_ids:
        return heading_ids[0] if len(heading_ids) == 1 else ""
    return chosen_ids[0] if len(chosen_ids) == 1 else ""


#: :func:`route` values besides the cases.
ROUTE_LABEL = "label"          # the submission holds a §14 description of its own (A/B)
ROUTE_GUARD = "guard"          # D24: read for D16, never judged
ROUTE_BIOSIMILAR = "biosimilar_copied_label"
ROUTE_REVIEW = "review"        # D35 (2026-09-30): the FDA review names a pivotal/supporting trial


class SubmissionRoute(NamedTuple):
    """One row of ``submission_routes.tsv``: which D35 case a submission is."""

    submission: str
    route: str


def owned_submissions(units: Iterable[Unit]) -> set[str]:
    """Submissions that hold the **earliest** appearance of some §14 trial.

    The same grouping P4 and P5 use (D16): a stated or review-resolved id
    groups by the id, anything else by the trial key at the end of the unit
    id. A submission whose every description is a later appearance of an
    earlier approval's trial owns none — its own evidence is not on the label,
    so it is routed to case C or D.

    >>> u = lambda key, name, date, nct="": Unit(f"{key}|{name}", key.split("|")[0],
    ...     key.split("|")[1], key.split("|")[2], date, "", "", "", nct, "", "", "", ())
    >>> sorted(owned_submissions([u("NDA1|ORIG|1", "X", "2010"), u("NDA1|SUPPL|2", "X", "2012"),
    ...                           u("NDA1|SUPPL|3", "Y", "2013")]))
    ['NDA1|ORIG|1', 'NDA1|SUPPL|3']
    """
    listed = list(units)
    first: dict[tuple[str, str], str] = {}
    keyed: list[tuple[tuple[str, str], str, str]] = []
    for unit in listed:
        if unit.case != CASE_B:
            continue
        trial = unit.stated_nct or unit.review_nct or unit.unit_id.rsplit("|", 1)[-1]
        group = (unit.application, trial)
        key = f"{unit.application}|{unit.submission_type}|{unit.submission_no}"
        keyed.append((group, unit.approval_date, key))
        if group not in first or unit.approval_date < first[group]:
            first[group] = unit.approval_date
    return {key for group, when, key in keyed if first[group] == when}


def route(
    submissions: Iterable[tuple[str, str]],
    owned: set[str],
    chosen_ids: Mapping[str, list[str]],
    drug_of_app: Mapping[str, set[str]],
    borrowed: set[str],
    review_linked: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """D35: which case each trial-relevant submission ``(key, trial_scope)`` is.

    ``label`` when it owns a §14 description (cases A/B, handled per
    description); ``guard`` for a D24 submission; ``biosimilar_copied_label``
    for a 351(k) whose drug resolved only through its reference product's
    stem, whose trials legally cannot be its evidence; ``review`` when its FDA
    review names a trial judged PIVOTAL or SUPPORTING (``review_linked``) — the
    evidence is already identified at tier A, so no weaker C/D search is run;
    otherwise ``C`` when it has a chosen indication and ``D`` when it has none.
    A review naming only non-linking trials (PK, failed, …) does not count.

    >>> route([("A|ORIG|1", "full"), ("B|ORIG|1", "guard"), ("C|ORIG|1", "full"),
    ...        ("D|ORIG|1", "full"), ("E|ORIG|1", "full")],
    ...       {"A|ORIG|1"}, {"C|ORIG|1": ["MESH:D1"]}, {"E": {"MESH:REF"}}, {"MESH:REF"})
    [('A|ORIG|1', 'label'), ('B|ORIG|1', 'guard'), ('C|ORIG|1', 'C'), ('D|ORIG|1', 'D'), ('E|ORIG|1', 'biosimilar_copied_label')]
    """
    out: list[tuple[str, str]] = []
    for key, scope in submissions:
        application = key.split("|", 1)[0]
        if key in owned:
            case = ROUTE_LABEL
        elif scope == "guard":
            case = ROUTE_GUARD
        elif drug_of_app.get(application, set()) & borrowed:
            case = ROUTE_BIOSIMILAR
        elif key in review_linked:
            case = ROUTE_REVIEW
        elif chosen_ids.get(key):
            case = CASE_C
        else:
            case = CASE_D
        out.append((key, case))
    return out


def read_chosen_text(out_dir: Path) -> dict[str, str]:
    """The text of each submission's chosen indication, for a case-C judge."""
    out: dict[str, str] = {}
    for row in _read_tsv(out_dir / "submission_indications.tsv"):
        if row.get("chosen") == "true" and row.get("text"):
            key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
            out.setdefault(key, row["text"])
    return out


def read_drug_only(out_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Case D's literature candidates per submission, best first (D35)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for row in _read_tsv(out_dir / DRUG_ONLY_FILE):
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        out.setdefault(key, []).append((row["nct_id"], row.get("diseases") or ""))
    return out


def pick_indications(
    heading_ids: list[str], submission_ids: list[str]
) -> tuple[str, ...]:
    """Every disease a trial description is ranked on (D34).

    Its own §14 heading's ids when the heading names any — *all* of them,
    because a heading naming two diseases is about both; otherwise every id the
    submission's own documents name. Unlike :func:`pick_indication` this never
    refuses: a candidate matching any of them ranks as a condition match. The
    refusal cost 36% of judged descriptions their indication, and a ranking
    key that is merely broad does far less harm than one that is absent.

    >>> pick_indications(["MESH:D2", "MESH:D1"], ["MESH:D9"])
    ('MESH:D1', 'MESH:D2')
    >>> pick_indications([], ["MESH:D9", "MESH:D8", "MESH:D9"])
    ('MESH:D8', 'MESH:D9')
    >>> pick_indications([], [])
    ()
    """
    return tuple(sorted(set(heading_ids or submission_ids)))


def read_submission_ids(out_dir: Path) -> dict[str, list[str]]:
    """Every coded id a submission's own documents name, by submission key.

    Letter, agent-located letter and the submission's label §1 — not today's
    openFDA label, which is not the submission's. §1 is cumulative, so on a
    supplement it also names the older indications; that is why it is here:
    a trial carried forward from an earlier approval is about one of them, and
    the letter names only what this submission added (D34).
    """
    own = {"letter", "letter_located", "label_section_1"}
    ids: dict[str, list[str]] = {}
    for row in _read_tsv(out_dir / "submission_indications.tsv"):
        mesh_id = row.get("mesh_id") or ""
        if row.get("source") in own and mesh_id:
            key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
            held = ids.setdefault(key, [])
            if mesh_id not in held:
                held.append(mesh_id)
    return ids


def read_indications(
    out_dir: Path,
) -> tuple[dict[tuple[str, str], list[str]], dict[str, list[str]]]:
    """``submission_indications.tsv`` as P3 needs it (D30).

    Returns ``(heading ids by (submission key, heading), chosen ids by
    submission key)``. The table is the only place indications are coded; P3
    no longer codes a heading itself.
    """
    headings: dict[tuple[str, str], list[str]] = {}
    chosen: dict[str, list[str]] = {}
    for row in _read_tsv(out_dir / "submission_indications.tsv"):
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        mesh_id = row.get("mesh_id") or ""
        if row.get("source") == "label_14_heading":
            ids = headings.setdefault((key, row.get("source_key") or ""), [])
            if mesh_id and mesh_id not in ids:
                ids.append(mesh_id)
        elif row.get("chosen") == "true" and mesh_id:
            chosen.setdefault(key, [])
            if mesh_id not in chosen[key]:
                chosen[key].append(mesh_id)
    return headings, chosen


def _read_tsv(path: Path) -> Iterator[dict[str, str]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def _name_of(row: dict[str, str]) -> str:
    """The trial name a row groups under, before the unnamed fallbacks.

    Acronym first, then a stated id, then the subsection heading — the same
    order :func:`collapse` uses. Rows that state an id never reach the
    fallbacks, so this is exact for the pre-pass that counts ids per group.
    """
    return row.get("acronym") or row.get("nct_id") or row.get("heading") or ""


#: How close before a registry id a trial name must end to be that id's name.
#: ``KEYNOTE-087 (NCT02453594)`` is 2 characters; a heading that ran into the
#: sentence (``… Survival in KEYNOTE-204 KEYNOTE-087 … (NCT02453594)``) is not.
PAIR_WINDOW: int = 40


def paired_name(excerpt: str, nct: str) -> str | None:
    """The trial name written immediately before ``nct``; ``""`` when none is,
    ``None`` when the id is not in the excerpt at all.

    >>> paired_name("investigated in KEYNOTE-087 (NCT02453594), a trial", "NCT02453594")
    'KEYNOTE-087'
    >>> paired_name("Survival in KEYNOTE-204 KEYNOTE-087 The efficacy of X was "
    ...             "investigated in the study (NCT02453594)", "NCT02453594")
    ''
    >>> paired_name("CHECKMATE-577 advanced ESCC CHECKMATE-648 (NCT 03143153) was", "NCT03143153")
    'CHECKMATE-648'
    >>> paired_name("no id here", "NCT00000001") is None
    True
    """
    found = re.search(r"NCT\s?" + re.escape(nct[3:]), excerpt)
    if not found:
        return None
    before = excerpt[: found.start()]
    names = [
        m.group(1) for m in section14.ACRONYM.finditer(before)
        if m.end() >= found.start() - PAIR_WINDOW and not m.group(1).startswith("NCT")
    ]
    return names[-1] if names else ""


def split_pairings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Undo the extractor's first-name pairing where the text contradicts it.

    Section 14 extraction pairs a sentence's registry id with the sentence's
    **first** trial name, and PDF headings run into sentences — so SOLO-2 was
    paired with Study 19's id, CHECKMATE-577 with CHECKMATE-648's, KEYNOTE-204
    with KEYNOTE-087's. Measured: of 3,091 rows carrying both, 1,073 have the
    id more than 40 characters from the name or outside the excerpt, and every
    one of the 21 "wrong" judge picks in the P4 precision test traced here.

    A row whose id is not written right after its name becomes two: the id with
    the name that *is* beside it (or alone, keyed on the id), and the name
    without the id. The id still links (the FDA printed it on this label); the
    name still gets its own unit for a judge.

    >>> text = "Progression-Free Survival – SOLO-2 Study 19 The efficacy of Lynparza was evaluated in Study 19 (NCT00753545)"
    >>> [(r["acronym"], r["nct_id"]) for r in split_pairings(
    ...     [{"acronym": "SOLO-2", "nct_id": "NCT00753545", "excerpt": text}])]
    [('', 'NCT00753545'), ('SOLO-2', '')]
    >>> split_pairings([{"acronym": "KEYNOTE-189", "nct_id": "NCT02578680",
    ...                  "excerpt": "in KEYNOTE-189 (NCT02578680)"}])[0]["acronym"]
    'KEYNOTE-189'
    """
    out: list[dict[str, str]] = []
    for row in rows:
        nct, name = row.get("nct_id") or "", row.get("acronym") or ""
        if nct and name:
            beside = paired_name(row.get("excerpt") or "", nct)
            if beside != name:
                out.append({**row, "acronym": beside or ""})
                out.append({**row, "nct_id": ""})
                continue
        out.append(row)
    return out


def collapse(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per ``(submission, trial)``, keeping the most informative.

    A label describes one trial over several sentences, and only some of them
    state the patient count. Keeping the row that has one — and its NCT id
    where any sentence gave one — is what makes the unit judgeable.

    **A stated registry id is never merged away.** Grouping on the acronym
    alone assumed one acronym meant one trial, and a section-14 subsection
    routinely cites two: the melanoma subsection names ``KEYNOTE-006`` beside
    both ``NCT01866319`` (which is KEYNOTE-006) and ``NCT01704287`` (which is
    KEYNOTE-002), because the extractor tags each id with the nearest acronym.
    Collapsing them kept one id and discarded the other — **382 label-stated
    ids across 381 groups**, and silently, since the surviving unit looked
    served. Those are the strongest evidence the pipeline has: the FDA wrote
    the id on the label.

    So the id joins the key when the group holds more than one. Where a group
    holds one id or none, the acronym still rules and rows without an id merge
    into it, which is what stops one trial becoming two units — the reason the
    acronym was the key in the first place. Where it holds several, rows
    stating no id cannot be assigned to either and stay their own unit for a
    judge; P5's ``(trial, submission)`` grain guard absorbs the answer if the
    judge picks one already linked.
    """
    rows = split_pairings(rows)
    # Which distinct stated ids each (submission, trial-name) group holds.
    ids_in_group: dict[tuple[str, ...], set[str]] = {}
    for row in rows:
        if row.get("nct_id"):
            name = _name_of(row)
            ids_in_group.setdefault(
                (
                    row["application"],
                    row["submission_type"],
                    row["submission_no"],
                    name,
                ),
                set(),
            ).add(row["nct_id"])

    best: dict[tuple[str, ...], dict[str, str]] = {}
    seen_unnamed: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        # Acronym first, not the NCT id. A label names a trial by acronym in
        # every sentence that mentions it but gives the registry id in at most
        # one, so keying on the id splits one trial into two units -- and the
        # unit without the id then goes to a judge to identify a trial the
        # label had already identified.
        name = row.get("acronym") or row.get("nct_id") or row.get("heading") or ""
        if not name:
            # Neither a name nor a subsection heading: a pre-PLR label, or one
            # whose Clinical Studies section is a single unnumbered block.
            # Falling through to an empty key merges every such trial in the
            # submission into one unit -- measured at 2,381 rows collapsing
            # into 1,032, one of them swallowing eighteen distinct trials and
            # keeping a single excerpt. The enrollment count separates them
            # where the label states one, and a sequence number where it does
            # not, so each description stays its own judgement.
            submission = (
                row["application"],
                row["submission_type"],
                row["submission_no"],
            )
            stated = row.get("enrollment") or ""
            if stated:
                name = f"n={stated}"
            else:
                seen_unnamed[submission] += 1
                name = f"#{seen_unnamed[submission]}"
        group = (
            row["application"],
            row["submission_type"],
            row["submission_no"],
            name,
        )
        # Several ids under one name: each keeps its own unit, and the name
        # becomes the id so two units cannot share a unit_id.
        if len(ids_in_group.get(group, set())) > 1 and row.get("nct_id"):
            name = row["nct_id"]
        key = (
            row["application"],
            row["submission_type"],
            row["submission_no"],
            name,
        )
        held = best.get(key)
        if held is None:
            # The resolved name is written onto the row: build() and
            # identification.read_units() both key on it, and recomputing it
            # independently let two distinct units share a unit_id.
            best[key] = {**row, TRIAL_KEY: name}
            continue
        # Prefer a stated NCT id, then a stated enrollment, then a longer
        # excerpt — in that order, because that is the order a judge needs them.
        if not held.get("nct_id") and row.get("nct_id"):
            held["nct_id"] = row["nct_id"]
        if not held.get("enrollment") and row.get("enrollment"):
            held["enrollment"] = row["enrollment"]
            held["excerpt"] = row.get("excerpt", held.get("excerpt", ""))
        elif len(row.get("excerpt", "")) > len(held.get("excerpt", "")):
            held["excerpt"] = row["excerpt"]
    return list(best.values())


def index_studies(
    archive: TrialArchive,
    drug_of: dict[str, str],
    condition_of: dict[str, str],
) -> tuple[dict[str, Candidate], dict[str, set[str]], dict[str, set[str]]]:
    """The registry, indexed by the two MeSH keys the filters use.

    Returned as postings lists rather than a scan: 22,666 descriptions against
    600,762 studies is 13 billion comparisons done naively, and a dict of sets
    turns it into a set intersection per unit.

    **Two sources, both applied.** ``derivedSection`` is ClinicalTrials.gov's
    own MeSH assignment, curated by NLM and present on 46% of interventions and
    79% of conditions. The biokde synonym index covers the rest *and* the
    PubChem-keyed half of the FDA side, which no MeSH id can reach. Neither
    subsumes the other, so a study is indexed under both. The synonym path is
    guarded by :data:`service.normalize.DESIGN_WORDS`, because it is inferred
    from entity recognition and has ``Control`` indexed as a chlordiazepoxide.
    """
    studies: dict[str, Candidate] = {}
    by_drug: dict[str, set[str]] = {}
    by_condition: dict[str, set[str]] = {}
    for facts in trial_studies.read_study_facts(archive):
        if facts.phases and not (set(facts.phases) & PHASES_KEPT):
            continue
        studies[facts.nct_id] = Candidate(
            nct_id=facts.nct_id,
            brief_title=facts.brief_title,
            acronym=facts.acronym,
            org_study_id=facts.org_study_id,
            phases=";".join(facts.phases),
            enrollment=facts.enrollment,
            start_date=facts.start_date,
            conditions=";".join(facts.conditions)[:300],
            interventions=";".join(facts.interventions)[:300],
            biosimilar=looks_biosimilar(facts.brief_title, facts.interventions),
            lead_sponsor=facts.lead_sponsor,
            primary_completion=facts.primary_completion,
        )
        # CT.gov's own curation first, the synonym index only where it is
        # absent. NLM assigns MeSH to 46% of studies' interventions and 79% of
        # their conditions, and does not tag a control arm as a chemical; the
        # synonym index is inferred from PubMed entity recognition and does.
        # See D21. Both feed the same postings lists, so a study indexed either
        # way is retrievable the same way.
        # BOTH sources, not either/or. Making derived MeSH *replace* the
        # synonym index was tried and lost ground: CT.gov assigns MeSH ids,
        # while half the FDA side is keyed on PubChem because the graph has no
        # MeSH record for those drugs (F-P1-5). A PubChem-keyed drug can only
        # ever be reached through the synonym index, so dropping it there cut
        # absolute recall from 2,604 to 2,253 even as the rate appeared to
        # rise. Indexing under both ids costs nothing and loses nothing --
        # a study is retrievable by whichever vocabulary its drug resolved to.
        for mesh in facts.derived_drug_mesh:
            by_drug.setdefault(mesh, set()).add(facts.nct_id)
        for name in facts.interventions:
            mesh_id = drug_of.get(name.casefold())
            if mesh_id:
                by_drug.setdefault(mesh_id, set()).add(facts.nct_id)
        for mesh in facts.derived_condition_mesh:
            by_condition.setdefault(mesh, set()).add(facts.nct_id)
        for name in facts.conditions:
            mesh_id = condition_of.get(name.casefold())
            if mesh_id:
                by_condition.setdefault(mesh_id, set()).add(facts.nct_id)
    _log.info(
        "%d studies indexed; %d drug keys, %d condition keys",
        len(studies),
        len(by_drug),
        len(by_condition),
    )
    return studies, by_drug, by_condition


def indication_allows(
    indication_ids: Iterable[str],
    trial_ids: Iterable[str],
    ancestors: Mapping[str, frozenset[str]],
) -> bool:
    """Whether the D35 indication filter keeps a trial.

    Kept when the trial's condition id and one of the description's
    indication ids are the same descriptor, or one sits anywhere above the
    other in the MeSH tree — and **whenever either side has no id at all**:
    CHECKMATE-648 registers "Various Advanced Cancer", which codes to nothing,
    and a filter that dropped id-less trials lost it. Siblings do not count:
    they recover almost nothing and match NSCLC to SCLC.

    >>> tree = {"MESH:D3": frozenset({"MESH:D1", "MESH:D2"}), "MESH:D2": frozenset({"MESH:D1"})}
    >>> indication_allows(["MESH:D2"], ["MESH:D3"], tree)   # descendant
    True
    >>> indication_allows(["MESH:D3"], ["MESH:D1"], tree)   # ancestor
    True
    >>> indication_allows(["MESH:D3"], ["MESH:D9"], tree)   # unrelated
    False
    >>> indication_allows([], ["MESH:D9"], tree), indication_allows(["MESH:D3"], [], tree)
    (True, True)
    """
    wanted = {i for i in indication_ids if i}
    have = {t for t in trial_ids if t}
    if not wanted or not have or wanted & have:
        return True
    empty: frozenset[str] = frozenset()
    return any(
        w in ancestors.get(t, empty) or t in ancestors.get(w, empty)
        for w in wanted
        for t in have
    )


def shortlist(
    drug_mesh: set[str],
    indication_mesh: str | Iterable[str],
    approval_date: str,
    studies: dict[str, Candidate],
    by_drug: dict[str, set[str]],
    by_condition: dict[str, set[str]],
    stated_enrollment: str = "",
    prefer_biosimilar: bool = False,
    label_name: str = "",
    from_graph: frozenset[str] = frozenset(),
    condition_ids: Mapping[str, frozenset[str]] | None = None,
    ancestors: Mapping[str, frozenset[str]] | None = None,
) -> tuple[Candidate, ...]:
    """The studies that could have supported this approval.

    **D35 filters the drug pool on the indication before the cap** when
    ``condition_ids`` (NCT → condition ids) and ``ancestors`` are given: see
    :func:`indication_allows`. Filtering first lets a correct trial that the
    ranking had put below the cap move into it — measured on 2,412
    label-stated answers, 115 are dropped and 87 recovered, net −28. The
    literature's candidates (``from_graph``) are **not** filtered: they were
    found through this drug and its indication already. The earlier text below
    predates D35 and is kept for its measurements.

    Two hard filters and then a ranking. Drug is hard: the approval is for that
    molecule. Date is hard: a trial cannot have supported an approval granted
    before it began.

    **Condition is not hard, and treating it as a filter loses real answers.**
    CHECKMATE-648 is in the registry as ``NCT03143153``, enrollment 970,
    exactly the number the Opdivo label states — and it registers its condition
    as *Various Advanced Cancer*, which maps to no indication at all. Drug and
    date left a pool of 1,055 containing it; intersecting on Esophageal
    Neoplasms left 2 and threw it away. A registry condition and a label
    heading are two vocabularies describing the same disease differently, and
    that disagreement is the norm, not an edge case.

    So when the pool is longer than a judge can read, it is **ranked** rather
    than cut. Ranking cannot lose the answer the way filtering can — the worst
    it does is place it lower — but only if the order means something, and for
    a long time it did not.

    **The order, and why it is this order.** Measured over the 2,298 units whose
    known answer was absent from their own shortlist: 809 were never in the pool
    at all, and **1,489 were in it and sorted below the cap**. Within those the
    true answer sat at median rank 304 where the label stated a patient count
    and 119 where it did not — the count-first sort was *worse* than no sort.

    1. ``prefer_biosimilar`` — narrow and conditional, see below.
    2. **The label's trial name matching the registry record.** Present for 41%
       of the buried answers and *unique in the pool* for 40%, which makes it
       the strongest signal available — and it was not used at all.
    3. **The literature connecting this trial to this drug** — the same graph
       walk tier C uses, precomputed by :mod:`service.graph_trials`. It finds
       trials bibliographically rather than by MeSH intervention coding, so it
       fails independently of the drug filter: of the 1,425 units whose answer
       the shortlist still missed, the graph names the true trial for **448**,
       lifting the attainable ceiling from 77.8% to 84.7%.
    4. Condition match: 22%.
    5. Phase 3 ahead of earlier phases: 70% of true answers are phase 3.
    6. Nearest start date to the approval.
    7. **Enrollment distance, demoted.** D8 measured that every candidate in a
       drug-and-condition set had a distinct count, and that holds *within a
       small relevant set*. As the primary sort over a median pool of 1,344 it
       orders by a number thousands of unrelated trials share. It belongs after
       the keys that establish relevance, doing the job it was validated for.
    8. The id, for determinism.

    Deciding remains P4's job, on the evidence the judge reads.

    **Injected candidates sit outside the cap.** They are appended after the
    truncation rather than ranked against the drug-filtered pool, so a unit may
    be shown slightly more than :data:`CANDIDATE_CAP` studies. Letting them
    compete cost 48 units an answer they already had while gaining 456 --
    positive, but avoidable for a handful of extra rows.

    ``prefer_biosimilar`` adds one more key, ahead of the others, and is set
    when the approval's drug resolved only through its stem. A biosimilar
    retrieves on its **reference product's** id, so the pool is dominated by
    the reference product's trials: measured across the 109 biosimilar
    approvals, the drug and date bounds leave a median of 318 candidates of
    which only about 6 are biosimilar-related, and just 9 of the 109 fit under
    the cap unaided. Sorting those 6 to the front is what puts the answer
    inside a 40-slot shortlist. It is still a ranking, so a wrongly unflagged
    study is demoted rather than lost.
    """
    pool: set[str] = set()
    for mesh in drug_mesh:
        pool |= by_drug.get(mesh, set())
    # Trials the literature names for this application, whether or not the drug
    # filter could reach them. **This is the only route to them.** 53% of the
    # drug map is keyed on PubChem while the trial index is keyed on MeSH —
    # biokde gives an entity one external id, never both, so those two id
    # spaces cannot be bridged. Measured: of 339 units whose answer the pool
    # lacked entirely, 337 were this. Injected candidates are marked, because
    # they skipped the drug filter.
    injected = {nct for nct in from_graph if nct in studies} - pool
    if not (pool or injected):
        return ()
    if approval_date:
        started_before = {
            nct
            for nct in pool | injected
            if not studies[nct].start_date
            or studies[nct].start_date <= approval_date
        }
        pool &= started_before
        injected &= started_before
    # One id or several (D34): a candidate matching any of them is a match.
    wanted = (
        {indication_mesh} if isinstance(indication_mesh, str) else set(indication_mesh)
    ) - {""}
    if wanted and condition_ids is not None and ancestors is not None:
        empty: frozenset[str] = frozenset()
        pool = {
            nct for nct in pool
            if indication_allows(wanted, condition_ids.get(nct, empty), ancestors)
        }
    matching: set[str] = set()
    for mesh in wanted:
        matching |= by_condition.get(mesh, set())
    target = int(stated_enrollment) if stated_enrollment.isdigit() else None

    def rank(nct: str) -> tuple[int, int, int, int, int, int, int, str]:
        candidate = studies[nct]
        enrolled = candidate.enrollment
        if target is None or not enrolled.isdigit():
            distance = 10**9
        else:
            distance = abs(int(enrolled) - target)
        return (
            0 if prefer_biosimilar and candidate.biosimilar else 1,
            0 if names_match(label_name, candidate) else 1,
            0 if nct in from_graph else 1,
            0 if nct in matching else 1,
            0 if "PHASE3" in candidate.phases else 1,
            _years_before(candidate.start_date, approval_date),
            distance,
            nct,
        )

    if len(pool) > CANDIDATE_CAP:
        pool = set(sorted(pool, key=rank)[:CANDIDATE_CAP])
    # Injected candidates are **exempt from the cap, never in competition for
    # it**. They are the only route to a drug whose id space the trial index
    # cannot be joined to, and making them fight for the same 100 slots evicted
    # the right answer from 48 units that previously had it -- a 456/48 trade
    # that is positive but needlessly lossy. Exempting them costs little: the
    # graph names a median of 5 trials per application, p99 38, max 87.
    pool |= injected
    # The shortlist is returned **in rank order**, every candidate included,
    # so a judge can work down it in tiers (1-10, 11-20, ...) and stop at the
    # first tier holding a match. Injected candidates are ranked by the same key
    # as everything else; being named by the literature is key 3, so they rise.
    return tuple(
        studies[nct]._replace(injected=nct in injected)
        for nct in sorted(pool, key=rank)
    )


__all__ = [
    "CANDIDATE_CAP",
    "CASE_B",
    "CASE_C",
    "CASE_D",
    "DRUG_ONLY_FILE",
    "ROUTE_BIOSIMILAR",
    "ROUTE_GUARD",
    "ROUTE_LABEL",
    "ROUTE_REVIEW",
    "indication_allows",
    "owned_submissions",
    "read_chosen_text",
    "read_drug_only",
    "route",
    "SubmissionRoute",
    "TRIAL_KEY",
    "PHASES_KEPT",
    "Candidate",
    "CandidatesReport",
    "Unit",
    "build",
    "BIOSIMILAR_TRIAL",
    "MIN_NAME_LENGTH",
    "pick_indication",
    "pick_indications",
    "read_indications",
    "read_submission_ids",
    "collapse",
    "paired_name",
    "split_pairings",
    "graph_supported",
    "looks_biosimilar",
    "names_match",
    "review_match",
    "review_names",
    "index_studies",
    "render",
    "shortlist",
]


def build(
    data: RawData,
    archive: TrialArchive,
    out_dir: Path,
    ancestors: Mapping[str, frozenset[str]] | None = None,
    review_linked: frozenset[str] = frozenset(),
) -> CandidatesReport:
    """Collapse the descriptions, index the registry, and shortlist each unit.

    D35: with ``ancestors`` (the MeSH tree, :func:`source.mesh_descriptors.ancestors`)
    the drug pool is filtered on the indication before the cap. After the §14
    units, every trial-relevant submission is routed (:func:`route`), and one
    unit is added per case-C submission (indication, no judgeable §14
    description) and per case-D submission (no indication; candidates from
    ``drug_only_trials.tsv``).
    """
    descriptions = list(_read_tsv(out_dir / "label_trials.tsv"))
    collapsed = collapse(descriptions)
    _log.info("%d descriptions collapsed to %d units", len(descriptions), len(collapsed))

    # Both sides of each filter come from P1, keyed the way the registry
    # spells them: case-folded, because a trial writes "Keytruda" where the
    # FDA writes "PEMBROLIZUMAB".
    drug_of = {
        row["source_name"].casefold(): row["mesh_id"]
        for row in _read_tsv(out_dir / "trial_drug_map.tsv")
    }
    condition_of = {
        row["source_name"].casefold(): row["mesh_id"]
        for row in _read_tsv(out_dir / "trial_condition_map.tsv")
    }
    from_graph_by_app = graph_supported(out_dir)
    drug_rows = list(_read_tsv(out_dir / "drug_map.tsv"))
    fda_drug = {row["source_name"]: row["mesh_id"] for row in drug_rows}
    # Ingredients whose id came from the suffix-stripped stem rather than the
    # name itself. For those the pool is the reference product's trials, so the
    # shortlist is ordered to put biosimilar-looking studies first.
    via_stem = {
        row["source_name"]
        for row in drug_rows
        if row.get("basis") == "stem"
    }

    spine = approval_events.build(data)
    approval_of = {
        f"{row.application}|{row.submission_type}|{row.submission_no}":
            row.approval_date
        for row in spine.rows
    }
    ingredients = normalize.ingredients_of(
        data,
        frozenset(
            row.application for row in spine.rows if row.trial_relevant == "true"
        ),
    )
    heading_ids, chosen_ids = read_indications(out_dir)
    document_ids = read_submission_ids(out_dir)
    if not heading_ids and not chosen_ids:
        _log.warning("submission_indications.tsv is empty or missing — run "
                     "--mode indication-normalize first; units get no indication")
    studies, by_drug, by_condition = index_studies(archive, drug_of, condition_of)
    reviewed = review_names(out_dir)
    condition_ids: dict[str, frozenset[str]] | None = None
    if ancestors is not None:
        inverted: dict[str, set[str]] = {}
        for mesh, ncts in by_condition.items():
            for nct in ncts:
                inverted.setdefault(nct, set()).add(mesh)
        condition_ids = {nct: frozenset(ids) for nct, ids in inverted.items()}

    units: list[Unit] = []
    no_drug = no_candidates = over_cap = already = from_review = 0
    sizes: list[int] = []
    for row in collapsed:
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        application_names = ingredients.get(row["application"], ())
        drug_mesh = {
            fda_drug[name] for name in application_names if name in fda_drug
        }
        stem_sourced = any(name in via_stem for name in application_names)
        heading = (row.get("heading") or "").strip()
        indication_mesh = pick_indication(
            heading_ids.get((key, heading), []), chosen_ids.get(key, [])
        )
        indication_ids = pick_indications(
            heading_ids.get((key, heading), []),
            chosen_ids.get(key, []) + document_ids.get(key, []),
        )
        stated = row.get("nct_id", "")
        # D29: a direct FDA identification skips the shortlist entirely.
        resolved = "" if stated else review_match(row.get("acronym", ""),
                                                   reviewed.get(key, {}))
        # A unit already identified (stated id, or D29) still gets a shortlist:
        # P4 skips it, but P6 scores shortlist recall on exactly these units
        # (D18) -- they are the only ones whose answer is known for free.
        if not drug_mesh:
            no_drug += 1
            shortlisted: tuple[Candidate, ...] = ()
        else:
            shortlisted = shortlist(
                drug_mesh,
                indication_ids,
                approval_of.get(key, ""),
                studies,
                by_drug,
                by_condition,
                row.get("enrollment", ""),
                prefer_biosimilar=stem_sourced,
                # The acronym, NOT the collapsed trial key. TRIAL_KEY falls
                # back to a subsection heading or "n=616" when the label names
                # no trial, and those are non-empty 70% of the time — so an
                # `or` never reaches the acronym, and a heading like "Adult
                # Patients with Growth Hormone Deficiency" matches some other
                # study's title and promotes it over the right answer. Passing
                # the key instead of the acronym cost 4.2 points of recall.
                label_name=row.get("acronym", ""),
                from_graph=from_graph_by_app.get(row["application"], frozenset()),
                condition_ids=condition_ids,
                ancestors=ancestors,
            )
        if stated:
            already += 1
        elif resolved:
            from_review += 1
        elif not shortlisted:
            no_candidates += 1
        else:
            sizes.append(len(shortlisted))
            if len(shortlisted) > CANDIDATE_CAP:
                over_cap += 1
        name = row.get(TRIAL_KEY) or stated or row.get("acronym", "") or heading
        units.append(
            Unit(
                unit_id=f"{key}|{name}"[:200],
                application=row["application"],
                submission_type=row["submission_type"],
                submission_no=row["submission_no"],
                approval_date=approval_of.get(key, ""),
                heading=heading,
                indication_mesh=indication_mesh,
                drug_mesh=";".join(sorted(drug_mesh)),
                stated_nct=stated,
                acronym=row.get("acronym", ""),
                enrollment=row.get("enrollment", ""),
                excerpt=row.get("excerpt", ""),
                candidates=shortlisted,
                label_sha=row.get("label_sha", ""),
                review_nct=resolved,
                indication_ids=indication_ids,
            )
        )
    # D35: route every trial-relevant submission, then add the C and D units.
    borrowed = {fda_drug[name] for name in via_stem if name in fda_drug}
    drug_of_app = {
        application: {fda_drug[n] for n in names if n in fda_drug}
        for application, names in ingredients.items()
    }
    routes = route(
        [(f"{r.application}|{r.submission_type}|{r.submission_no}", r.trial_scope)
         for r in spine.rows if r.trial_relevant == "true"],
        owned_submissions(units), chosen_ids, drug_of_app, borrowed, review_linked,
    )
    chosen_text = read_chosen_text(out_dir)
    drug_only = read_drug_only(out_dir)
    for needed, mode in ((DRUG_ONLY_FILE, "graph-links"), ("graph_trials.tsv", "graph-trials")):
        if not (out_dir / needed).is_file():
            _log.warning("%s is missing — run --mode %s first; its literature "
                         "candidates are absent from this pass", needed, mode)
    events = {
        f"{r.application}|{r.submission_type}|{r.submission_no}": r for r in spine.rows
    }
    with_c = with_d = 0
    for key, case in routes:
        if case not in (CASE_C, CASE_D):
            continue
        event = events[key]
        drugs = drug_of_app.get(event.application, set())
        names = "; ".join(sorted(ingredients.get(event.application, ())))
        if case == CASE_C:
            ids = pick_indications([], chosen_ids.get(key, []) + document_ids.get(key, []))
            listed = shortlist(
                drugs, ids, event.approval_date, studies, by_drug, by_condition,
                prefer_biosimilar=False,
                from_graph=from_graph_by_app.get(event.application, frozenset()),
                condition_ids=condition_ids, ancestors=ancestors,
            ) if drugs else ()
            with_c += bool(listed)
            text = chosen_text.get(key, "")
            suffix = "indication"
        else:
            ids = ()
            listed = tuple(
                studies[nct]._replace(injected=True, found_under=diseases)
                for nct, diseases in drug_only.get(key, [])
                if nct in studies
            )[:CANDIDATE_CAP]
            with_d += bool(listed)
            text = ""
            suffix = "drug_only"
        units.append(
            Unit(
                unit_id=f"{key}|{suffix}",
                application=event.application,
                submission_type=event.submission_type,
                submission_no=event.submission_no,
                approval_date=event.approval_date,
                heading="",
                indication_mesh=ids[0] if len(ids) == 1 else "",
                drug_mesh=";".join(sorted(drugs)),
                stated_nct="",
                acronym="",
                enrollment="",
                excerpt="",
                candidates=listed,
                indication_ids=ids,
                case=case,
                indication_text=text,
                drug=names,
            )
        )
    sizes.sort()
    median = sizes[len(sizes) // 2] if sizes else 0
    return CandidatesReport(
        units=tuple(units),
        descriptions=len(descriptions),
        collapsed=len(collapsed),
        already_identified=already,
        review_identified=from_review,
        no_drug=no_drug,
        no_candidates=no_candidates,
        over_cap=over_cap,
        median_candidates=median,
        routes=tuple(routes),
        case_c_with_candidates=with_c,
        case_d_with_candidates=with_d,
    )


def render(report: CandidatesReport) -> str:
    """The pass report, as ``--mode candidates`` prints it."""
    return "\n".join(
        [
            "Candidate generation — narrowing the registry per description",
            "",
            f"  section-14 descriptions read             {report.descriptions:>9,}",
            f"  collapsed to (submission, trial) units   {report.collapsed:>9,}",
            "",
            f"  already identified (label states an NCT) "
            f"{report.already_identified:>9,}",
            f"  identified by the submission's review    "
            f"{report.review_identified:>9,}",
            f"  NEEDING JUDGEMENT                        {report.to_judge:>9,}",
            "",
            f"    no drug resolved for the application   {report.no_drug:>9,}",
            f"    no candidate survived the filters      {report.no_candidates:>9,}",
            f"    shortlist longer than {CANDIDATE_CAP:<3}              "
            f"{report.over_cap:>9,}",
            "",
            f"  MEDIAN CANDIDATES PER UNIT               "
            f"{report.median_candidates:>9,}",
            "",
            "  D35 routing of trial-relevant submissions:",
            *(f"    {route_name:<38}{count:>9,}"
              for route_name, count in sorted(Counter(r for _, r in report.routes).items())),
            f"  case C units with candidates             {report.case_c_with_candidates:>9,}",
            f"  case D units with candidates             {report.case_d_with_candidates:>9,}",
        ]
    )
