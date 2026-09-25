"""Which trials the literature associates with each application's drug.

A **ranking input for the label route**, not a source of links. Tier C already
walks the same edges to *produce* links where a label yields nothing (see
:mod:`service.graph_links`); this walks them to *order* the candidates P3 hands
a judge, which is a different and cheaper job.

**Why it is worth a separate pass.** Measured on 60 units whose known answer
the shortlist missed: 57 resolve both a drug and a disease id, 56 have an edge,
and **16 — 27% — have the correct trial named in the edge's abstracts**. The
graph finds those by bibliography rather than by MeSH intervention coding, so
it fails independently of the drug filter and of the ranking, which is what
makes it additive rather than redundant.

**It cannot invent a link on its own.** An abstract mentioning ``NCT02578680``
beside a drug and a disease says the literature connects them; it says nothing
about which approval rested on which trial. So this only moves a candidate up a
list the judge still reads, and a trial the judge rejects is still rejected.

Written once to ``graph_trials.tsv`` because the walk is the expensive part —
one Mongo pass over the applications in scope, then P3 reads a TSV.

**It is latency-bound, so it reports progress.** Measured on the first attempt:
6% CPU over 16 minutes, i.e. almost entirely waiting on round trips, on a host
also serving another team's NER job. A pass with no output is indistinguishable
from a stuck one, and :data:`PROGRESS_EVERY` exists so a caller can decide
whether to wait or to narrow the scope.
"""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from dao.graph_evidence import ABSTRACT_BUDGET, GraphEvidenceRepository
from service import indication_normalize
from utility.logging import get_logger

_log = get_logger(__name__)

#: How many drug ids and disease ids to cross for one application. An
#: application with eight ingredients and twenty indications would otherwise
#: cost 160 edge queries to add a handful of ids the first few already found.
MAX_DRUGS: int = 3
MAX_DISEASES: int = 5

#: Log a line every this many applications. The pass is bound by Mongo
#: round-trip latency — 6% CPU over 16 minutes on the first attempt — so
#: without this it is a black box for however long it runs, and a slow pass
#: cannot be told from a stuck one.
PROGRESS_EVERY: int = 100


class GraphTrial(NamedTuple):
    """One trial the literature connects to one application."""

    application: str
    nct_id: str
    #: How many of the edge's abstracts named it. Better attestation, not
    #: evidence of being pivotal — that stays the judge's call.
    mentions: int


class GraphTrialsReport(NamedTuple):
    """What a precompute pass found."""

    rows: tuple[GraphTrial, ...]
    applications: int
    with_drug: int
    with_disease: int
    with_edge: int
    with_trials: int

    @property
    def yield_rate(self) -> float:
        """Share of applications that yielded at least one registry id.

        >>> GraphTrialsReport((), 100, 95, 60, 55, 25).yield_rate
        0.25
        >>> GraphTrialsReport((), 0, 0, 0, 0, 0).yield_rate
        0.0
        """
        return round(self.with_trials / self.applications, 4) if self.applications else 0.0


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def build(
    out_dir: Path,
    repository: GraphEvidenceRepository,
    applications: frozenset[str],
    ingredients: dict[str, frozenset[str]],
    budget: int = ABSTRACT_BUDGET,
) -> GraphTrialsReport:
    """One row per ``(application, nct_id)`` the literature connects.

    ``mentions`` counts how many of the edge's abstracts named the trial. A
    trial cited by several papers is not thereby the pivotal one, but it is
    better attested, and P3 can use that to break ties among graph-supported
    candidates.
    """
    drug_of = {
        row["source_name"]: row["biokdeid"]
        for row in _read_tsv(out_dir / "drug_map.tsv")
        if row["biokdeid"]
    }
    disease_of = {
        row["mesh_id"].removeprefix("MESH:"): row["biokdeid"]
        for row in _read_tsv(out_dir / "indication_map.tsv")
        if row["biokdeid"]
    }
    # D30: the union of the application's submissions' chosen indications,
    # already carrying biokdeids; the application-level current label only
    # when that table is absent.
    by_application: dict[str, set[str]] = defaultdict(set)
    chosen = indication_normalize.read_chosen(out_dir / "submission_indications.tsv")
    for submission, chosen_diseases in chosen.items():
        by_application[submission.split("|", 1)[0]].update(b for b, _ in chosen_diseases)
    if not chosen:
        for row in _read_tsv(out_dir / "indications.tsv"):
            found = disease_of.get(row["mesh_id"])
            if found:
                by_application[row["application"]].add(found)

    rows: list[GraphTrial] = []
    with_drug = with_disease = with_edge = with_trials = 0
    started = time.monotonic()
    total = len(applications)
    for done, application in enumerate(sorted(applications), start=1):
        if done % PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed else 0.0
            _log.info(
                "%d/%d (%.0f%%), %.1f/s, ~%.0f min left, %d edges, %d yielding",
                done,
                total,
                100 * done / total,
                rate,
                ((total - done) / rate / 60) if rate else 0.0,
                with_edge,
                with_trials,
            )
        drugs = {
            drug_of[name]
            for name in ingredients.get(application, frozenset())
            if name in drug_of
        }
        diseases = by_application.get(application, set())
        if drugs:
            with_drug += 1
        if diseases:
            with_disease += 1
        if not (drugs and diseases):
            continue
        pmids: set[str] = set()
        for drug in sorted(drugs)[:MAX_DRUGS]:
            for disease in sorted(diseases)[:MAX_DISEASES]:
                pmids.update(repository.pmids_for(drug, disease))
        if not pmids:
            continue
        with_edge += 1
        counts: dict[str, int] = defaultdict(int)
        for mention in repository.mentions_in(sorted(pmids)[:budget]):
            counts[mention.nct_id] += 1
        if not counts:
            continue
        with_trials += 1
        rows.extend(
            GraphTrial(application, nct, mentions)
            for nct, mentions in sorted(counts.items())
        )
    _log.info(
        "%d applications, %d with an edge, %d yielding trials, %d rows",
        len(applications),
        with_edge,
        with_trials,
        len(rows),
    )
    return GraphTrialsReport(
        rows=tuple(rows),
        applications=len(applications),
        with_drug=with_drug,
        with_disease=with_disease,
        with_edge=with_edge,
        with_trials=with_trials,
    )


def render(report: GraphTrialsReport) -> str:
    """The pass report, as ``--mode graph-trials`` prints it."""
    return "\n".join(
        [
            "Graph trials — literature candidates, as a ranking input for P3",
            "",
            f"  applications considered            {report.applications:>9,}",
            f"    with a drug id                   {report.with_drug:>9,}",
            f"    with a disease id                {report.with_disease:>9,}",
            f"    with a drug<->disease edge       {report.with_edge:>9,}",
            f"    YIELDING TRIALS                  {report.with_trials:>9,}"
            f"  ({100 * report.yield_rate:.1f}%)",
            "",
            f"  (application, trial) rows          {len(report.rows):>9,}",
        ]
    )


__all__ = [
    "MAX_DISEASES",
    "MAX_DRUGS",
    "GraphTrial",
    "GraphTrialsReport",
    "build",
    "render",
]
