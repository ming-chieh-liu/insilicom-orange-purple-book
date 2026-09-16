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
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from config.settings import MeshVocabulary, RawData, TrialArchive
from service import approval_events, indications, normalize
from source import trial_studies
from utility.logging import get_logger

_log = get_logger(__name__)

#: Above this many candidates a unit is emitted but flagged: the shortlist is
#: too long to be a comparison, and P4 should treat it as weak evidence.
CANDIDATE_CAP: int = 40

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

    @property
    def needs_judgement(self) -> bool:
        """Whether P4 has to decide this one.

        A description that states its own NCT id is already identified; sending
        it to a judge would spend two calls to confirm what the label said.
        """
        return not self.stated_nct


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

    @property
    def to_judge(self) -> int:
        return sum(1 for unit in self.units if unit.needs_judgement)


def code_heading(
    heading: str, exact: dict[str, str], index: indications.TermIndex
) -> str:
    """The MeSH descriptor a section-14 heading names, or ``""``.

    Two stages, because neither alone is enough. An exact lookup against the
    graph's disease synonyms is precise but only answers clean headings — 19%
    of the 1,397 distinct ones, because most carry qualifiers
    (``ALK-Positive Metastatic NSCLC Previously Treated with...``). The entry
    term sweep reaches 65% but is coarser, returning a parent where the exact
    lookup would have returned the disease. Exact first, sweep as fallback.
    """
    direct = exact.get(heading.casefold())
    if direct:
        return direct
    coded = indications.code(heading, index)
    return sorted(coded)[0] if len(coded) == 1 else ""


def _read_tsv(path: Path) -> Iterator[dict[str, str]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def collapse(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per ``(submission, trial)``, keeping the most informative.

    A label describes one trial over several sentences, and only some of them
    state the patient count. Keeping the row that has one — and its NCT id
    where any sentence gave one — is what makes the unit judgeable.
    """
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


def shortlist(
    drug_mesh: set[str],
    indication_mesh: str,
    approval_date: str,
    studies: dict[str, Candidate],
    by_drug: dict[str, set[str]],
    by_condition: dict[str, set[str]],
    stated_enrollment: str = "",
) -> tuple[Candidate, ...]:
    """The studies that could have supported this approval.

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
    than cut: condition matches first, then whatever is closest to the patient
    count the label states. Ranking cannot lose the answer the way filtering
    can — the worst it does is place it lower. The count is used only to order
    the list; deciding remains P4's job, on the evidence the judge reads.
    """
    pool: set[str] = set()
    for mesh in drug_mesh:
        pool |= by_drug.get(mesh, set())
    if not pool:
        return ()
    if approval_date:
        pool = {
            nct
            for nct in pool
            if not studies[nct].start_date
            or studies[nct].start_date <= approval_date
        }
    if len(pool) > CANDIDATE_CAP:
        matching = by_condition.get(indication_mesh, set()) if indication_mesh else set()
        target = int(stated_enrollment) if stated_enrollment.isdigit() else None

        def rank(nct: str) -> tuple[int, int, str]:
            enrolled = studies[nct].enrollment
            if target is None or not enrolled.isdigit():
                distance = 10**9
            else:
                distance = abs(int(enrolled) - target)
            return (0 if nct in matching else 1, distance, nct)

        pool = set(sorted(pool, key=rank)[:CANDIDATE_CAP])
    return tuple(sorted((studies[nct] for nct in pool), key=lambda c: c.nct_id))


__all__ = [
    "CANDIDATE_CAP",
    "TRIAL_KEY",
    "PHASES_KEPT",
    "Candidate",
    "CandidatesReport",
    "Unit",
    "build",
    "code_heading",
    "collapse",
    "index_studies",
    "render",
    "shortlist",
]


def build(
    data: RawData,
    archive: TrialArchive,
    vocabulary: MeshVocabulary,
    out_dir: Path,
) -> CandidatesReport:
    """Collapse the descriptions, index the registry, and shortlist each unit."""
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
    exact_condition = {
        row["source_name"].casefold(): row["mesh_id"]
        for row in _read_tsv(out_dir / "trial_condition_map.tsv")
    }
    fda_drug = {
        row["source_name"]: row["mesh_id"]
        for row in _read_tsv(out_dir / "drug_map.tsv")
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
    term_index = indications.build_index(vocabulary)
    studies, by_drug, by_condition = index_studies(archive, drug_of, condition_of)

    units: list[Unit] = []
    no_drug = no_candidates = over_cap = already = 0
    sizes: list[int] = []
    for row in collapsed:
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        drug_mesh = {
            fda_drug[name]
            for name in ingredients.get(row["application"], ())
            if name in fda_drug
        }
        heading = row.get("heading", "")
        indication_mesh = code_heading(heading, exact_condition, term_index)
        if indication_mesh and not indication_mesh.startswith("MESH:"):
            indication_mesh = f"MESH:{indication_mesh}"
        if not drug_mesh:
            no_drug += 1
            shortlisted: tuple[Candidate, ...] = ()
        else:
            shortlisted = shortlist(
                drug_mesh,
                indication_mesh,
                approval_of.get(key, ""),
                studies,
                by_drug,
                by_condition,
                row.get("enrollment", ""),
            )
        stated = row.get("nct_id", "")
        if stated:
            already += 1
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
            )
        )
    sizes.sort()
    median = sizes[len(sizes) // 2] if sizes else 0
    return CandidatesReport(
        units=tuple(units),
        descriptions=len(descriptions),
        collapsed=len(collapsed),
        already_identified=already,
        no_drug=no_drug,
        no_candidates=no_candidates,
        over_cap=over_cap,
        median_candidates=median,
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
            f"  NEEDING JUDGEMENT                        {report.to_judge:>9,}",
            "",
            f"    no drug resolved for the application   {report.no_drug:>9,}",
            f"    no candidate survived the filters      {report.no_candidates:>9,}",
            f"    shortlist longer than {CANDIDATE_CAP:<3}              "
            f"{report.over_cap:>9,}",
            "",
            f"  MEDIAN CANDIDATES PER UNIT               "
            f"{report.median_candidates:>9,}",
        ]
    )
