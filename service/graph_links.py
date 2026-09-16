"""Tier C: candidate trials for approvals the label cannot serve.

4,539 trial-relevant approvals — every one before 2008, 57% of the whole set —
currently carry no link at all. Their labels are pre-PLR and have no numbered
Clinical Studies section, or have one that names no trial, and the trials
behind them predate registration. For those the graph is the only source left.

**What it can and cannot say.** A ``pubmedRelations`` edge means the literature
associates this drug with this disease. It does not mean any particular paper
describes the trial that supported the approval, and the papers behind one edge
include reviews, case reports, other drugs' trials and post-approval studies.
So this module gathers candidates and a judge decides — the same shape as P4,
but a weaker question, which is why the result is tier C and not tier A.

**Measured before building, on 150 pre-2008 submissions.** 95% resolve a drug
id and only 31% a disease id, so the bottleneck is indication coverage rather
than the graph. 29% have an edge at all and 16% yield at least one registry id,
with a median of 2 distinct ids where they do — a far easier list to judge than
P4's median of 40. The 16% is a floor: that measurement read at most 150 of a
median 461 supporting abstracts per edge.

**The date bound still holds.** A trial cannot have supported an approval
granted before it began, so a candidate whose start date postdates the approval
is dropped here rather than shown to a judge.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

from dao.graph_evidence import ABSTRACT_BUDGET, GraphEvidenceRepository, Mention
from utility.logging import get_logger

_log = get_logger(__name__)


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


class GraphReport(NamedTuple):
    """What a tier-C pass found."""

    units: tuple[GraphUnit, ...]
    events_considered: int
    with_drug: int
    with_disease: int
    with_edge: int
    with_candidates: int
    by_candidate_count: tuple[tuple[int, int], ...]

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
            )
        )
    return sorted(out, key=lambda c: (not c.trial_report, -c.mentions, c.nct_id))


def within_date(
    candidates: list[GraphCandidate], approval_date: str
) -> list[GraphCandidate]:
    """Drop trials that began after the approval they would be evidence for.

    The one hard fact available here, and the same rule P3 applies. A candidate
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


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


__all__ = [
    "ABSTRACT_BUDGET",
    "GraphCandidate",
    "GraphReport",
    "GraphUnit",
    "build",
    "rank",
    "render",
    "within_date",
]


def build(
    out_dir: Path,
    repository: GraphEvidenceRepository,
    studies: dict[str, tuple[str, str, str]],
    unserved: set[str],
    ingredients: dict[str, frozenset[str]],
    limit: int | None = None,
    budget: int = ABSTRACT_BUDGET,
) -> GraphReport:
    """Gather graph candidates for approvals the label route did not serve.

    ``unserved`` is the set of submission keys with no link — read from
    ``unlinked_submissions.tsv`` rather than recomputed, so tier C works on
    exactly what tiers A and B left behind and cannot quietly disagree with
    them. ``studies`` maps an NCT id to its start date, enrollment and phases,
    for the date bound and for the judge to compare against.
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
    by_application: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in _read_tsv(out_dir / "indications.tsv"):
        found = disease_of.get(row["mesh_id"])
        if found:
            by_application[row["application"]].add(found)
    units: list[GraphUnit] = []
    considered = with_drug = with_disease = with_edge = with_candidates = 0
    sizes: Counter[int] = Counter()

    for key in sorted(unserved):
        if key not in spine:
            continue
        row = spine[key]
        considered += 1
        drugs = {
            drug_of[name]
            for name in ingredients.get(row["application"], frozenset())
            if name in drug_of
        }
        diseases = by_application.get(row["application"], set())
        if drugs:
            with_drug += 1
        if diseases:
            with_disease += 1
        if not (drugs and diseases):
            continue

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

        mentions = repository.mentions_in(pmids[:budget])
        candidates = within_date(rank(mentions), row["approval_date"])
        candidates = [
            candidate._replace(
                start_date=studies.get(candidate.nct_id, ("", "", ""))[0],
                enrollment=studies.get(candidate.nct_id, ("", "", ""))[1],
                phases=studies.get(candidate.nct_id, ("", "", ""))[2],
            )
            for candidate in candidates
        ]
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
    )


def render(report: GraphReport) -> str:
    """The pass report, as ``--mode graph-links`` prints it."""
    lines = [
        "Tier C — literature candidates for approvals the label could not serve",
        "",
        f"  unserved approvals considered            {report.events_considered:>9,}",
        f"    with a drug id                         {report.with_drug:>9,}",
        f"    with a disease id                      {report.with_disease:>9,}",
        f"    with a drug<->disease edge             {report.with_edge:>9,}",
        f"    YIELDING CANDIDATES                    {report.with_candidates:>9,}"
        f"  ({100 * report.yield_rate:.1f}%)",
        "",
        "  candidates per unit:",
    ]
    for size, count in report.by_candidate_count[:10]:
        lines.append(f"    {size:>3} candidate(s){'':<22}{count:>9,}")
    return "\n".join(lines)
