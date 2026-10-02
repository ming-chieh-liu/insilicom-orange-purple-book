"""S7, drug × indication: the trials the literature names for each application.

**What it produces** (``graph_trials.tsv``, ``literature_abstracts.jsonl``). For
every trial-relevant application, the drug's iKraph2 entity (S2, rule R, D45)
is crossed with the diseases of its submissions' chosen indications (S5, D30);
each drug–disease edge's evidence PMIDs come from the iKraph2 API (release
pinned, D42/D43), best evidence first up to :data:`ABSTRACT_BUDGET` abstracts,
and the abstract text from iKraph1 ``pubmedDocs``. A trial is taken from an
abstract by its NCT id, or by a full sponsor protocol id that names exactly one
registry study (D35, :func:`service.review_trials.protocol_trials`). Each row
keeps up to :data:`ABSTRACTS_KEPT` PMIDs and the disease it was found under; the
abstracts are saved for the S9 judges.

**What it is for.** These are the literature candidates of routes **B+** (a
section 14 description) and **C+** (an indication only), judged with their
abstracts before the registry shortlist (D37). S8 drops trials that never ran
or began after the approval, the drug check drops a trial whose every
non-control arm is another FDA drug, and the MeSH-tree indication check applies
(id-less trials kept). The literature finds trials by bibliography rather than
by registry coding, so it fails independently of the registry search: on 60
known answers the shortlist missed, 27% were named in the edge's abstracts.

**It cannot make a link on its own.** An abstract naming ``NCT02578680`` beside
a drug and a disease says the literature connects them, not that an approval
rested on that trial; only an S9 judge's Yes does.

**It is latency-bound, so it reports progress** every :data:`PROGRESS_EVERY`
applications, and prefetches each pair's edges several drugs at once. The
iKraph2 answers are cached per release (``output/ikraph2_cache_<release>``).
Submissions with no indication are served by :mod:`service.graph_links`.
"""

from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from dao.graph_evidence import ABSTRACT_BUDGET, Abstract, LiteratureSource
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

#: PMIDs kept per trial for the judge (D37): enough to show what the trial
#: tested, few enough to keep a tier of ten readable.
ABSTRACTS_KEPT: int = 3


class GraphTrial(NamedTuple):
    """One trial the literature connects to one application."""

    application: str
    nct_id: str
    #: How many of the edge's abstracts named it. Better attestation, not
    #: evidence of being pivotal — that stays the judge's call.
    mentions: int
    #: ``nct`` when an abstract printed the registry id, ``protocol`` when the
    #: trial was reached only through a sponsor protocol id (D35).
    via: str = "nct"
    #: Up to :data:`ABSTRACTS_KEPT` PMIDs naming the trial, trial reports first
    #: (D37): what the literature-route judge reads.
    pmids: str = ""
    #: The disease biokdeids whose drug x disease edges led to it (D37), so a
    #: submission is offered only trials found under its own indication.
    found_under: str = ""


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
    repository: LiteratureSource,
    applications: frozenset[str],
    ingredients: dict[str, frozenset[str]],
    budget: int = ABSTRACT_BUDGET,
    resolve: Callable[[str], list[tuple[str, str]]] | None = None,
) -> GraphTrialsReport:
    """One row per ``(application, nct_id)`` the literature connects.

    ``mentions`` counts how many of the edge's abstracts named the trial. A
    trial cited by several papers is not thereby the pivotal one, but it is
    better attested; S8 orders a unit's literature candidates with it.

    ``resolve`` (D35) also reads full sponsor protocol ids in the abstracts,
    each kept only when it names exactly one registry study; see
    :func:`service.review_trials.protocol_trials`.
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

    def drugs_of(application: str) -> set[str]:
        return {
            node
            for name in ingredients.get(application, frozenset())
            if name in drug_of
            for node in drug_of[name].split(";")
            if node
        }

    # D42: the iKraph2 API is slow per cold edge, so every pair the loop below
    # will ask about is fetched first, several drugs at once.
    repository.prefetch(
        (drug, disease)
        for application in sorted(applications)
        for drug in sorted(drugs_of(application))[:MAX_DRUGS]
        for disease in sorted(by_application.get(application, set()))[:MAX_DISEASES]
    )
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
        drugs = drugs_of(application)
        diseases = by_application.get(application, set())
        if drugs:
            with_drug += 1
        if diseases:
            with_disease += 1
        if not (drugs and diseases):
            continue
        pmids: set[str] = set()
        disease_of_pmid: dict[str, set[str]] = defaultdict(set)
        for drug in sorted(drugs)[:MAX_DRUGS]:
            for disease in sorted(diseases)[:MAX_DISEASES]:
                for pmid in repository.pmids_for(drug, disease):
                    pmids.add(pmid)
                    disease_of_pmid[pmid].add(disease)
        if not pmids:
            continue
        with_edge += 1
        counts: dict[str, int] = defaultdict(int)
        by_id: set[str] = set()
        papers: dict[str, list[tuple[bool, str]]] = defaultdict(list)
        under: dict[str, set[str]] = defaultdict(set)
        for mention in repository.mentions_in(sorted(pmids)[:budget], resolve):
            counts[mention.nct_id] += 1
            if not mention.protocol_id:
                by_id.add(mention.nct_id)
            papers[mention.nct_id].append((not mention.is_trial_report, mention.pmid))
            under[mention.nct_id].update(disease_of_pmid.get(mention.pmid, ()))
        if not counts:
            continue
        with_trials += 1
        rows.extend(
            GraphTrial(
                application, nct, mentions, "nct" if nct in by_id else "protocol",
                pmids=";".join(p for _, p in sorted(set(papers[nct]))[:ABSTRACTS_KEPT]),
                found_under=";".join(sorted(under[nct])),
            )
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


#: Abstracts the literature routes show their judges (D37), one JSON object per
#: PMID: ``pmid``, ``title``, ``abstract``, ``publication_types``.
ABSTRACTS_FILE = "literature_abstracts.jsonl"


def pmids_of(rows: Iterable[Any]) -> list[str]:
    """Every PMID the rows keep, in first-seen order.

    >>> pmids_of([GraphTrial("NDA1", "NCT1", 2, pmids="3;1"), GraphTrial("NDA1", "NCT2", 1, pmids="1")])
    ['3', '1']
    """
    seen: dict[str, None] = {}
    for row in rows:
        for pmid in str(getattr(row, "pmids", "") or "").split(";"):
            if pmid:
                seen.setdefault(pmid, None)
    return list(seen)


def save_abstracts(abstracts: Iterable[Abstract], path: Path) -> int:
    """Write the abstracts file; returns how many records were written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for item in abstracts:
            handle.write(json.dumps({**item._asdict(),
                                     "publication_types": list(item.publication_types)}) + "\n")
            written += 1
    return written


def read_abstracts(*paths: Path) -> dict[str, dict[str, Any]]:
    """PMID → its record, from one or more abstracts files; absent files skipped."""
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    out.setdefault(str(record.get("pmid")), record)
    return out


def render(report: GraphTrialsReport) -> str:
    """The pass report, as ``--mode graph-trials`` prints it."""
    return "\n".join(
        [
            "S7 graph trials — literature candidates for routes B+ and C+ (drug × indication)",
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
    "ABSTRACTS_FILE",
    "ABSTRACTS_KEPT",
    "pmids_of",
    "read_abstracts",
    "save_abstracts",
    "MAX_DISEASES",
    "MAX_DRUGS",
    "GraphTrial",
    "GraphTrialsReport",
    "build",
    "render",
]
