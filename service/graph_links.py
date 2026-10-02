"""S7, drug only: literature candidates for submissions with no indication (route D).

**What it produces** (``drug_only_trials.tsv``, ``drug_only_abstracts.jsonl``,
``adjudication/graph_units.jsonl``). For every trial-relevant, non-guard
submission with no chosen indication (S5, D30; :func:`no_indication_scope`), the
drug's iKraph2 entity (S2, rule R) is walked along every "treats" edge to a
disease (D31), best evidence first up to the abstract budget; evidence PMIDs
come from the iKraph2 API (D42), abstract text from iKraph1. Trials are taken
from the abstracts by NCT id or by a protocol id naming exactly one registry
study (D35); a trial that never ran is dropped (D37) and one that began after
the approval is dropped (:func:`within_date`). Each candidate keeps the diseases
it was found under, which the D judge sees.

**What it can and cannot say.** An edge means the literature associates the
drug with a disease, not that a paper describes the trial behind the approval;
the papers include reviews, case reports and other drugs' trials. So this only
gathers candidates; S9 judges them with their abstracts (literature template),
and a link found this way is route **D**, the weakest tier (D35, D37).

The older paths of this module — tier C over ``unlinked_submissions.tsv`` and the
drug × indication walk (now :mod:`service.graph_trials`) — are kept only as
documented history and are not run (D35).
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from dao.graph_evidence import (
    ABSTRACT_BUDGET,
    LiteratureSource,
    Mention,
    TreatmentEdge,
)
from service import indication_normalize
from utility.logging import get_logger

_log = get_logger(__name__)

#: Reasons in ``unlinked_submissions.tsv`` that mean the label route is
#: **exhausted**, and so the graph route is the only source left.
#:
#: A submission only ever carries ``no_label`` when it produced **no unit at
#: all**. Before that was enforced it also carried the catch-all alongside a
#: specific reason, and 695 of 1,327 tier-C units were built for submissions
#: P4 had not yet judged — the very pre-emption this filter exists to stop.
#:
#: Two reasons are deliberately absent. ``not_yet_judged`` means P4 has not
#: looked yet, not that it failed — running tier C there would produce weak
#: graph links for approvals P4 may well resolve at tier A, and a tier-C link
#: is never promoted. ``later_appearance_of_the_same_trial`` is not a miss at
#: all: it is D16 working, and the trial already has a correct link on the
#: earlier submission.
EXHAUSTED: frozenset[str] = frozenset(
    {
        "no_label",
        "judged_none",
        "no_candidates",
        "no_drug_resolved",
        "scanned",
        "no_section_14",
    }
)


def exhausted_only(rows: list[dict[str, str]]) -> set[str]:
    """Submission keys the label route genuinely could not serve.

    >>> exhausted_only([{"application": "NDA1", "submission_type": "ORIG",
    ...                  "submission_no": "1", "reason": "judged_none"}])
    {'NDA1|ORIG|1'}
    >>> exhausted_only([{"application": "NDA1", "submission_type": "ORIG",
    ...                  "submission_no": "1", "reason": "not_yet_judged"}])
    set()
    """
    return {
        f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        for row in rows
        if row.get("reason") in EXHAUSTED
    }


class GraphCandidate(NamedTuple):
    """One registry id the literature offers, with why it is here."""

    nct_id: str
    #: How many of the edge's abstracts mentioned it. A trial cited by several
    #: papers is not thereby the pivotal one, but it is better attested.
    mentions: int
    #: Whether any mentioning paper is typed as a trial report rather than a
    #: review or a case report.
    trial_report: bool
    example_pmid: str
    example_title: str
    start_date: str
    enrollment: str
    phases: str
    #: Drug-only units (D31): the diseases whose treatment edges' papers
    #: mentioned this trial, best-scored first, so the judge sees what the
    #: trial is about when the approval's own indication is unknown.
    diseases: str = ""
    primary_completion: str = ""
    #: The protocol id an abstract used for it, when no abstract printed the
    #: NCT id (D35); empty otherwise.
    protocol_id: str = ""
    #: PMIDs naming it, trial reports first, at most three (D37).
    pmids: str = ""


#: Abstracts naming case D's trials (D37), in the format of
#: :data:`service.graph_trials.ABSTRACTS_FILE`.
DRUG_ONLY_ABSTRACTS_FILE = "drug_only_abstracts.jsonl"

#: How a unit's candidates were gathered.
BASIS_INDICATION = "indication"
BASIS_DRUG_ONLY = "drug_only"

#: Treatment edges a drug-only unit walks, best-scored first. Pembrolizumab
#: alone has 1,390; the abstract budget, not this, is what usually binds.
MAX_TREATMENT_EDGES: int = 50


class GraphUnit(NamedTuple):
    """One approval the label could not serve, and what the graph offers."""

    unit_id: str
    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    drug_biokdeid: str
    disease_biokdeid: str
    indication_term: str
    pmids_examined: int
    candidates: tuple[GraphCandidate, ...]
    #: :data:`BASIS_INDICATION` — the drug and this approval's indication — or
    #: :data:`BASIS_DRUG_ONLY` when no indication is known (D31).
    basis: str = BASIS_INDICATION
    #: The approved ingredient names, for the prompt's ``{{drug}}``.
    drug: str = ""


class GraphReport(NamedTuple):
    """What a tier-C pass found."""

    units: tuple[GraphUnit, ...]
    events_considered: int
    with_drug: int
    with_disease: int
    with_edge: int
    with_candidates: int
    by_candidate_count: tuple[tuple[int, int], ...]
    #: D31: approvals with a drug but no indication, searched on the drug alone.
    drug_only_considered: int = 0
    drug_only_with_candidates: int = 0

    @property
    def yield_rate(self) -> float:
        """Share of considered events that reached at least one candidate.

        >>> GraphReport((), 150, 142, 47, 43, 24, ()).yield_rate
        0.16
        >>> GraphReport((), 0, 0, 0, 0, 0, ()).yield_rate
        0.0
        """
        return (
            round(self.with_candidates / self.events_considered, 4)
            if self.events_considered
            else 0.0
        )


def rank(mentions: list[Mention]) -> list[GraphCandidate]:
    """Group mentions by trial, best attested first.

    A trial reported by a paper typed as a trial report outranks one mentioned
    only in a review, and within that, one mentioned by several papers outranks
    one mentioned once. Neither makes it pivotal — that is the judge's call —
    but it decides what a judge sees first when the list is long.
    """
    grouped: dict[str, list[Mention]] = defaultdict(list)
    for mention in mentions:
        grouped[mention.nct_id].append(mention)
    out: list[GraphCandidate] = []
    for nct, group in grouped.items():
        typed = next((m for m in group if m.is_trial_report), group[0])
        by_protocol = "" if any(not m.protocol_id for m in group) else group[0].protocol_id
        ordered = sorted({(not m.is_trial_report, m.pmid) for m in group})
        out.append(
            GraphCandidate(
                nct_id=nct,
                mentions=len(group),
                trial_report=any(m.is_trial_report for m in group),
                example_pmid=typed.pmid,
                example_title=typed.title,
                start_date="",
                enrollment="",
                phases="",
                protocol_id=by_protocol,
                pmids=";".join(p for _, p in ordered[:3]),
            )
        )
    return sorted(out, key=lambda c: (not c.trial_report, -c.mentions, c.nct_id))


def within_date(
    candidates: list[GraphCandidate], approval_date: str
) -> list[GraphCandidate]:
    """Drop trials that began after the approval they would be evidence for.

    The one hard fact available here, and the same rule S8 applies. A candidate
    with no recorded start date is kept: missing data must not hide a real
    answer.
    """
    if not approval_date:
        return candidates
    return [
        candidate
        for candidate in candidates
        if not candidate.start_date or candidate.start_date <= approval_date
    ]


def completed_by(  # unused since D37; kept for the measurement it records
    candidates: list[GraphCandidate], approval_date: str
) -> list[GraphCandidate]:
    """Drop trials whose primary completion postdates the approval (D31).

    Applied to drug-only units, whose candidates are the drug's whole treatment
    literature and so mostly post-approval studies. A trial still collecting
    its primary endpoint cannot have been the evidence. An unrecorded date is
    kept, as in :func:`within_date`.

    >>> c = GraphCandidate("N1", 1, True, "1", "t", "2001", "", "", "", "2004-06")
    >>> [x.nct_id for x in completed_by([c], "2005-01-01")], completed_by([c], "2003-01-01")
    (['N1'], [])
    """
    if not approval_date:
        return candidates
    return [
        candidate
        for candidate in candidates
        if not candidate.primary_completion or candidate.primary_completion <= approval_date
    ]


def treatment_pmids(
    edges: list[TreatmentEdge], budget: int
) -> tuple[list[str], dict[str, list[str]]]:
    """PMIDs to read for a drug-only unit, and the diseases each came from.

    Edges are taken best-scored first and their PMIDs in order until the
    abstract budget is spent, so a drug with a thousand edges reads its
    strongest treatment literature rather than an arbitrary slice.

    >>> e = [TreatmentEdge("d1", "Melanoma", 9.0, ("1", "2")),
    ...      TreatmentEdge("d2", "NSCLC", 5.0, ("2", "3"))]
    >>> treatment_pmids(e, 3)
    (['1', '2', '3'], {'1': ['Melanoma'], '2': ['Melanoma', 'NSCLC'], '3': ['NSCLC']})
    >>> treatment_pmids(e, 1)[0]
    ['1']
    """
    order: list[str] = []
    diseases: dict[str, list[str]] = defaultdict(list)
    for edge in edges[:MAX_TREATMENT_EDGES]:
        for pmid in edge.pmids:
            if pmid not in diseases and len(order) >= budget:
                continue
            if pmid not in diseases:
                order.append(pmid)
            if edge.disease_name not in diseases[pmid]:
                diseases[pmid].append(edge.disease_name)
    return order, dict(diseases)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


__all__ = [
    "DRUG_ONLY_ABSTRACTS_FILE",
    "ABSTRACT_BUDGET",
    "EXHAUSTED",
    "GraphCandidate",
    "GraphReport",
    "GraphUnit",
    "BASIS_DRUG_ONLY",
    "BASIS_INDICATION",
    "DrugOnlyTrial",
    "build",
    "completed_by",
    "drug_only_rows",
    "no_indication_scope",
    "exhausted_only",
    "rank",
    "render",
    "treatment_pmids",
    "within_date",
]


def build(
    out_dir: Path,
    repository: LiteratureSource,
    studies: Mapping[str, tuple[str, ...]],
    unserved: set[str],
    ingredients: dict[str, frozenset[str]],
    limit: int | None = None,
    budget: int = ABSTRACT_BUDGET,
    resolve: Callable[[str], list[tuple[str, str]]] | None = None,
) -> GraphReport:
    """Gather graph candidates for approvals the label route did not serve.

    ``unserved`` is the set of submission keys with no link — read from
    ``unlinked_submissions.tsv`` rather than recomputed, so tier C works on
    exactly what tiers A and B left behind and cannot quietly disagree with
    them. ``studies`` maps an NCT id to its start date, enrollment and phases,
    for the date bound and for the judge to compare against, plus its primary
    completion date as a fourth field where known.

    **Drug-only fallback (D31).** An approval whose drug resolves but which has
    no indication at all is not skipped: every negative-correlation (treats)
    edge from the drug to a disease is walked instead, the papers behind the
    best-scored ones read, and their registry ids offered with the disease each
    came from. Such a unit is marked :data:`BASIS_DRUG_ONLY` and is also held to
    the primary-completion bound. The indication path is unchanged.

    Under D35 the caller passes the submissions with **no indication** (case
    D), so the drug-only path is the one that runs; its walk depends only on
    the drug, so it is done once per drug set and reused across that drug's
    submissions. ``resolve`` also reads full protocol ids in the abstracts.
    """
    spine = {
        f"{r['application']}|{r['submission_type']}|{r['submission_no']}": r
        for r in _read_tsv(out_dir / "approval_events.tsv")
        if r["trial_relevant"] == "true"
    }
    drug_of = {
        r["source_name"]: r["biokdeid"]
        for r in _read_tsv(out_dir / "drug_map.tsv")
        if r["biokdeid"]
    }
    disease_of = {
        r["mesh_id"].removeprefix("MESH:"): (r["biokdeid"], r["preferred_name"])
        for r in _read_tsv(out_dir / "indication_map.tsv")
        if r["biokdeid"]
    }
    # D30: each submission's own chosen indication (letter, else its label's
    # section 1, else today's label), with its biokdeid. Only when that table
    # is absent does the application-level current label stand in.
    chosen = indication_normalize.read_chosen(out_dir / "submission_indications.tsv")
    by_application: dict[str, set[tuple[str, str]]] = defaultdict(set)
    if not chosen:
        for row in _read_tsv(out_dir / "indications.tsv"):
            found = disease_of.get(row["mesh_id"])
            if found:
                by_application[row["application"]].add(found)
    units: list[GraphUnit] = []
    considered = with_drug = with_disease = with_edge = with_candidates = 0
    drug_only = drug_only_found = 0
    sizes: Counter[int] = Counter()
    walked: dict[frozenset[str], tuple[list[Mention], dict[str, list[str]], int]] = {}

    def drugs_of(application: str) -> set[str]:
        return {
            node
            for name in ingredients.get(application, frozenset())
            if name in drug_of
            for node in drug_of[name].split(";")
            if node
        }

    def diseases_of(key: str, application: str) -> set[tuple[str, str]]:
        return chosen.get(key, set()) if chosen else by_application.get(application, set())

    # D42: warm the drug-only walks (the iKraph2 API is slow per cold edge),
    # several drugs at once, for the submissions the loop below will walk.
    in_scope = [k for k in sorted(unserved) if k in spine][:limit]
    repository.prefetch_treatments(
        drug
        for key in in_scope
        if not diseases_of(key, spine[key]["application"])
        for drug in sorted(drugs_of(spine[key]["application"]))[:3]
    )

    for key in sorted(unserved):
        if key not in spine:
            continue
        row = spine[key]
        considered += 1
        drugs = drugs_of(row["application"])
        diseases = diseases_of(key, row["application"])
        if drugs:
            with_drug += 1
        if diseases:
            with_disease += 1
        if not drugs:
            continue
        if not diseases:
            drug_only += 1
            unit = _drug_only_unit(key, row, drugs, repository, studies, budget,
                                   resolve, walked)
            if unit is not None:
                unit = unit._replace(
                    drug=_names(ingredients.get(row["application"], frozenset())))
            if unit is not None:
                with_edge += 1
                with_candidates += 1
                drug_only_found += 1
                sizes[len(unit.candidates)] += 1
                units.append(unit)
            if limit is not None and considered >= limit:
                break
            continue

        # DEPRECATED since D35: literature for a drug + indication is now a P3
        # candidate source (graph_trials.tsv); this path runs only when a
        # caller passes submissions that have an indication.
        _log.warning("graph_links: %s has an indication — the indication basis is "
                     "deprecated (D35); its literature is P3's graph_trials.tsv", key)
        pmids: list[str] = []
        pair: tuple[str, str] = ("", "")
        term = ""
        for drug in sorted(drugs)[:3]:
            for disease, name in sorted(diseases)[:5]:
                supporting = repository.pmids_for(drug, disease)
                if supporting:
                    pmids.extend(supporting)
                    if not pair[0]:
                        pair, term = (drug, disease), name
        pmids = sorted(set(pmids))
        if not pmids:
            continue
        with_edge += 1

        mentions = repository.mentions_in(pmids[:budget], resolve)
        candidates = within_date(rank(mentions), row["approval_date"])
        candidates = [_with_registry(candidate, studies) for candidate in candidates]
        candidates = within_date(candidates, row["approval_date"])
        if not candidates:
            continue
        with_candidates += 1
        sizes[len(candidates)] += 1
        units.append(
            GraphUnit(
                unit_id=f"{key}|graph",
                application=row["application"],
                submission_type=row["submission_type"],
                submission_no=row["submission_no"],
                approval_date=row["approval_date"],
                drug_biokdeid=pair[0],
                disease_biokdeid=pair[1],
                indication_term=term,
                pmids_examined=min(len(pmids), budget),
                candidates=tuple(candidates),
                drug=_names(ingredients.get(row["application"], frozenset())),
            )
        )
        if limit is not None and considered >= limit:
            break

    _log.info(
        "%d considered, %d with an edge, %d with candidates",
        considered,
        with_edge,
        with_candidates,
    )
    return GraphReport(
        units=tuple(units),
        events_considered=considered,
        with_drug=with_drug,
        with_disease=with_disease,
        with_edge=with_edge,
        with_candidates=with_candidates,
        by_candidate_count=tuple(sorted(sizes.items())),
        drug_only_considered=drug_only,
        drug_only_with_candidates=drug_only_found,
    )


def no_indication_scope(
    spine_rows: list[dict[str, str]], chosen: Mapping[str, object]
) -> set[str]:
    """Case D's search scope (D35): in-scope submissions with no indication.

    Every trial-relevant, non-guard submission (D24) whose indication table
    holds no chosen indication. S8 later keeps a D unit only for a submission
    no FDA document already serves, so this may be wider than the final set.

    >>> rows = [{"application": "NDA1", "submission_type": "ORIG", "submission_no": "1",
    ...          "trial_relevant": "true", "trial_scope": "core"},
    ...         {"application": "NDA2", "submission_type": "ORIG", "submission_no": "1",
    ...          "trial_relevant": "true", "trial_scope": "guard"},
    ...         {"application": "NDA3", "submission_type": "ORIG", "submission_no": "1",
    ...          "trial_relevant": "true", "trial_scope": "core"}]
    >>> sorted(no_indication_scope(rows, {"NDA3|ORIG|1": {("1", "x")}}))
    ['NDA1|ORIG|1']
    """
    return {
        f"{r['application']}|{r['submission_type']}|{r['submission_no']}"
        for r in spine_rows
        if r.get("trial_relevant") == "true" and r.get("trial_scope") != "guard"
        and f"{r['application']}|{r['submission_type']}|{r['submission_no']}" not in chosen
    }


class DrugOnlyTrial(NamedTuple):
    """One case-D candidate, as ``drug_only_trials.tsv`` holds it (D35)."""

    application: str
    submission_type: str
    submission_no: str
    nct_id: str
    mentions: int
    diseases: str
    protocol_id: str
    pmids: str = ""


def drug_only_rows(units: Iterable[GraphUnit]) -> list[DrugOnlyTrial]:
    """The drug-only units' candidates, flattened for S8, best first."""
    return [
        DrugOnlyTrial(u.application, u.submission_type, u.submission_no, c.nct_id,
                      c.mentions, c.diseases, c.protocol_id, c.pmids)
        for u in units if u.basis == BASIS_DRUG_ONLY
        for c in u.candidates
    ]


def _names(ingredients: frozenset[str]) -> str:
    """``"A; B"`` — the approved ingredient names, sorted.

    >>> _names(frozenset({"SOMATROPIN"}))
    'SOMATROPIN'
    """
    return "; ".join(sorted(ingredients))


def _with_registry(
    candidate: GraphCandidate, studies: Mapping[str, tuple[str, ...]]
) -> GraphCandidate:
    facts = tuple(studies.get(candidate.nct_id, ())) + ("", "", "", "")
    return candidate._replace(start_date=facts[0], enrollment=facts[1], phases=facts[2],
                              primary_completion=facts[3])


def _drug_only_unit(
    key: str,
    row: dict[str, str],
    drugs: set[str],
    repository: LiteratureSource,
    studies: Mapping[str, tuple[str, ...]],
    budget: int,
    resolve: Callable[[str], list[tuple[str, str]]] | None = None,
    walked: dict[frozenset[str], tuple[list[Mention], dict[str, list[str]], int]] | None = None,
) -> GraphUnit | None:
    """The D31 unit for an approval with a drug and no indication, or None.

    ``walked`` caches the literature walk per drug set: it does not depend on
    the submission, only the date bounds applied after it do.
    """
    cache = walked if walked is not None else {}
    held = cache.get(frozenset(drugs))
    if held is None:
        edges: list[TreatmentEdge] = []
        for drug in sorted(drugs)[:3]:
            edges.extend(repository.treatment_edges(drug))
        edges.sort(key=lambda e: (-e.score, e.disease_biokdeid))
        pmids, diseases_of = treatment_pmids(edges, budget)
        found = repository.mentions_in(pmids, resolve) if pmids else []
        held = (found, diseases_of, len(pmids))
        cache[frozenset(drugs)] = held
    mentions, diseases_of, examined = held
    if not examined:
        return None
    found_under: dict[str, list[str]] = defaultdict(list)
    for mention in mentions:
        for name in diseases_of.get(mention.pmid, []):
            if name not in found_under[mention.nct_id]:
                found_under[mention.nct_id].append(name)
    # A drug-wide search reaches any trial an abstract names, including ones
    # registered decades after the approval; one with no registry record has no
    # dates to check, so here it cannot pass the date bounds (a 1996 approval
    # was offered NCT06096863, a COVID-19 trial). The indication path keeps
    # its original leniency.
    candidates = [
        _with_registry(c, studies)._replace(diseases="; ".join(found_under[c.nct_id][:5]))
        for c in rank(mentions) if c.nct_id in studies
    ]
    # D37: start <= approval stays a hard filter; primary completion is no
    # longer one (9% of label-stated trials show a primary completion after
    # their approval). It is shown to the judge instead.
    candidates = within_date(candidates, row["approval_date"])
    if not candidates:
        return None
    return GraphUnit(
        unit_id=f"{key}|graph",
        application=row["application"],
        submission_type=row["submission_type"],
        submission_no=row["submission_no"],
        approval_date=row["approval_date"],
        drug_biokdeid=sorted(drugs)[0],
        disease_biokdeid="",
        indication_term="",
        pmids_examined=examined,
        candidates=tuple(candidates),
        basis=BASIS_DRUG_ONLY,
    )


def render(report: GraphReport) -> str:
    """The pass report, as ``--mode graph-links`` prints it."""
    lines = [
        "S7 drug-only literature — candidates for route D (no indication)",
        "",
        f"  unserved approvals considered            {report.events_considered:>9,}",
        f"    with a drug id                         {report.with_drug:>9,}",
        f"    with a disease id                      {report.with_disease:>9,}",
        f"    with a drug<->disease edge             {report.with_edge:>9,}",
        f"    YIELDING CANDIDATES                    {report.with_candidates:>9,}"
        f"  ({100 * report.yield_rate:.1f}%)",
        f"  no indication: drug-only search (D31)    {report.drug_only_considered:>9,}",
        f"    yielding candidates                    {report.drug_only_with_candidates:>9,}",
        "",
        "  candidates per unit:",
    ]
    for size, count in report.by_candidate_count[:10]:
        lines.append(f"    {size:>3} candidate(s){'':<22}{count:>9,}")
    return "\n".join(lines)
