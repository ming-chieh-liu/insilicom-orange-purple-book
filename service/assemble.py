"""Turn every judged and stated link into the deliverable, with its tier.

The output of this project is one claim repeated a few thousand times: *this
trial was the evidence for that approval*. Some of those claims are certain and
some are guesses, and a table that renders both the same way is worse than one
that renders fewer.

**The tier is the count.** Where the label states a patient count and the
registry states one, agreement between them is the strongest evidence available
that the two are the same trial — measured across five drug and condition sets,
every candidate had a distinct enrollment. So tier A is reserved for links whose
counts agree, and everything the judges identified without that corroboration is
tier B.

Checking eight identifications by hand is what showed why this has to be a
column rather than a habit. Four matched exactly — CHECKMATE-648 at 970 against
970. One was almost certainly wrong: a unit keyed ``TABLE 3`` carried a label
"count" of 3, which is a table number the section-14 extractor read as an
enrollment, leaving the judge nothing to match on but prose. In the output those
two rows were indistinguishable. Now one is tier A and the other is tier B with
its counts side by side.

**D16 applies to every link, not only judged ones.** Section 14 is cumulative,
so a trial stays in the label of every later approval. KEYNOTE-189 is named
outright in 37 Keytruda labels; linking it to all 37 asserts that one trial was
the evidence for 37 separate approvals. It was the evidence for the earliest.
P4 already deduplicated the judged path — this is the same rule on the branch
where the label states its own id, which was missed the first time and showed
up immediately in the acceptance check.

**Nothing is discarded for being weak.** A tier-B row is a real judgement by two
independent agents; it simply has not been corroborated. The unlinked file
carries the rest, with a reason, so recall stays auditable rather than implied.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from service import identification
from utility.logging import get_logger

_log = get_logger(__name__)

#: How far the label's count may sit from the registry's and still corroborate.
#: Labels report the analysed or randomised population where the registry
#: reports everyone enrolled, so a small gap is normal. Measured on the
#: identifications checked by hand: the four correct ones agreed exactly, and
#: the divergences were 33%, 61% and 92% out.
COUNT_TOLERANCE: float = 0.15

#: Evidence tiers. A is corroborated, B is judged, C is the graph's backstop.
TIER_STATED: str = "A"
TIER_CORROBORATED: str = "A"
TIER_JUDGED: str = "B"
TIER_GRAPH: str = "C"


class Link(NamedTuple):
    """One trial → submission claim, as ``trial_submission_map.tsv`` holds it."""

    nct_id: str
    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    action_type: str
    indication_mesh: str
    indication_term: str
    drug_mesh: str
    trial_name: str
    #: What the label said, and what the registry says. Side by side, because
    #: their agreement is the tier and a reader should be able to check it.
    label_enrollment: str
    registry_enrollment: str
    evidence_tier: str
    #: ``label_nct`` when the label named the trial itself, ``label_description``
    #: when two judges identified it from prose.
    evidence_source: str
    #: ``counts_agree`` \\| ``counts_diverge`` \\| ``no_count``.
    count_check: str
    #: How the approval's drug reached its id. ``stem`` means the FDA name
    #: carried a four-letter biologic suffix that resolved to nothing, so the
    #: suffix was stripped and the **reference product's** id used to retrieve.
    #: That is a retrieval convenience, not an identity claim, so such a link
    #: is held below tier A however well the counts agree — see
    #: :func:`tier_for`.
    drug_basis: str = ""


class Unlinked(NamedTuple):
    """One approval event, or one description, that produced no link."""

    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    trial_name: str
    reason: str


def counts_agree(label: str, registry: str) -> str:
    """Whether the two stated counts corroborate each other.

    >>> counts_agree("970", "970")
    'counts_agree'
    >>> counts_agree("374", "895")
    'counts_diverge'
    >>> counts_agree("", "895")
    'no_count'
    >>> counts_agree("3", "41")
    'counts_diverge'
    """
    if not (label.isdigit() and registry.isdigit()):
        return "no_count"
    bigger = max(int(label), int(registry))
    if not bigger:
        return "no_count"
    gap = abs(int(label) - int(registry)) / bigger
    return "counts_agree" if gap <= COUNT_TOLERANCE else "counts_diverge"


def tier_for(
    evidence_source: str, count_check: str, drug_basis: str = ""
) -> str:
    """The tier a link earns.

    A label that names its own trial is tier A outright — there is nothing to
    corroborate, the FDA stated it. A judged link reaches tier A only when the
    counts agree.

    **A stem-derived drug never reaches tier A by count agreement.** When the
    FDA name carried a biologic suffix that resolved to nothing, retrieval fell
    back to the suffix-stripped stem, which for a biosimilar is a *different*
    product — its reference. The candidate pool is then dominated by the
    reference product's trials, and agreeing patient counts are exactly what a
    wrong match in that pool looks like. A 351(k) rests on its own trials
    (D11), so the claim stays at tier B and the basis is written on the row.
    A label that stated the id outright is unaffected: the FDA said it.

    >>> tier_for("label_nct", "no_count")
    'A'
    >>> tier_for("label_description", "counts_agree")
    'A'
    >>> tier_for("label_description", "counts_diverge")
    'B'
    >>> tier_for("label_description", "no_count")
    'B'
    >>> tier_for("label_description", "counts_agree", "stem")
    'B'
    >>> tier_for("label_nct", "no_count", "stem")
    'A'
    """
    if evidence_source == "label_nct":
        return TIER_STATED
    if drug_basis == "stem":
        return TIER_JUDGED
    return TIER_CORROBORATED if count_check == "counts_agree" else TIER_JUDGED


def read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    """Each JSON object in a JSONL file."""
    yield from identification.iter_json_lines(path)


def _registry_counts(unit: dict[str, object]) -> dict[str, str]:
    """NCT id → the enrollment the shortlist recorded for it."""
    out: dict[str, str] = {}
    raw = unit.get("candidates")
    if isinstance(raw, list):
        for candidate in raw:
            if isinstance(candidate, dict):
                out[str(candidate.get("nct_id") or "")] = str(
                    candidate.get("enrollment") or ""
                )
    return out


__all__ = [
    "COUNT_TOLERANCE",
    "TIER_CORROBORATED",
    "TIER_GRAPH",
    "TIER_JUDGED",
    "TIER_STATED",
    "AssembleReport",
    "Link",
    "Unlinked",
    "build",
    "render",
    "counts_agree",
    "read_jsonl",
    "tier_for",
]


class AssembleReport(NamedTuple):
    """The deliverable, and what did not make it."""

    links: tuple[Link, ...]
    unlinked: tuple[Unlinked, ...]
    by_tier: tuple[tuple[str, int], ...]
    by_source: tuple[tuple[str, int], ...]
    by_reason: tuple[tuple[str, int], ...]
    submissions_linked: int
    submissions_total: int

    @property
    def coverage(self) -> float:
        """Share of trial-relevant submissions carrying at least one link.

        >>> AssembleReport((), (), (), (), (), 3, 4).coverage
        0.75
        >>> AssembleReport((), (), (), (), (), 0, 0).coverage
        0.0
        """
        return self.submissions_linked / self.submissions_total if self.submissions_total else 0.0


def build(out_dir: Path, adjudication_dir: Path) -> AssembleReport:
    """Join the candidate units, the verdicts and the spine into the map."""
    from collections import Counter

    verdicts = identification.read_verdicts(
        adjudication_dir / "identify_verdicts.jsonl"
    )
    # Which MeSH ids were reached only by stripping a biologic suffix. Read
    # from drug_map rather than recomputed, so P5 and P3 cannot disagree about
    # which drugs are stem-sourced.
    stem_ids: set[str] = set()
    drug_map = out_dir / "drug_map.tsv"
    if drug_map.is_file():
        with drug_map.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("basis") == "stem" and row.get("mesh_id"):
                    stem_ids.add(row["mesh_id"])
    spine: dict[str, dict[str, str]] = {}
    with (out_dir / "approval_events.tsv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["trial_relevant"] != "true":
                continue
            key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
            spine[key] = row

    links: list[Link] = []
    unlinked: list[Unlinked] = []
    linked_submissions: set[str] = set()
    # D16: one link per (application, trial), on the earliest approval whose
    # label carries it. Cumulative section 14 otherwise multiplies every trial
    # across every later submission.
    earliest: dict[tuple[str, str], str] = {}
    units = list(read_jsonl(out_dir / "candidates.jsonl"))
    for unit in units:
        stated = str(unit.get("stated_nct") or "")
        if not stated:
            continue
        pair = (str(unit["application"]), stated)
        when = str(unit.get("approval_date") or "")
        if pair not in earliest or when < earliest[pair]:
            earliest[pair] = when

    for unit in units:
        key = (
            f"{unit['application']}|{unit['submission_type']}"
            f"|{unit['submission_no']}"
        )
        event = spine.get(key, {})
        name = str(unit.get("acronym") or unit.get("heading") or "")
        stated = str(unit.get("stated_nct") or "")
        registry = _registry_counts(unit)
        label_count = str(unit.get("enrollment") or "")

        if stated:
            if earliest.get((str(unit["application"]), stated)) != str(
                unit.get("approval_date") or ""
            ):
                unlinked.append(
                    Unlinked(
                        str(unit["application"]),
                        str(unit["submission_type"]),
                        str(unit["submission_no"]),
                        str(unit.get("approval_date") or ""),
                        name,
                        "later_appearance_of_the_same_trial",
                    )
                )
                continue
            nct, source = stated, "label_nct"
        else:
            verdict = verdicts.get(str(unit.get("unit_id") or ""))
            if verdict is None:
                unlinked.append(
                    Unlinked(
                        str(unit["application"]),
                        str(unit["submission_type"]),
                        str(unit["submission_no"]),
                        str(unit.get("approval_date") or ""),
                        name,
                        "not_yet_judged" if unit.get("candidates") else _why(unit),
                    )
                )
                continue
            if not verdict.identified:
                unlinked.append(
                    Unlinked(
                        str(unit["application"]),
                        str(unit["submission_type"]),
                        str(unit["submission_no"]),
                        str(unit.get("approval_date") or ""),
                        name,
                        "judged_none",
                    )
                )
                continue
            nct, source = verdict.final, "label_description"

        check = counts_agree(label_count, registry.get(nct, ""))
        drug_mesh = str(unit.get("drug_mesh") or "")
        basis = (
            "stem"
            if stem_ids and set(drug_mesh.split(";")) & stem_ids
            else ""
        )
        links.append(
            Link(
                nct_id=nct,
                application=str(unit["application"]),
                submission_type=str(unit["submission_type"]),
                submission_no=str(unit["submission_no"]),
                approval_date=str(unit.get("approval_date") or ""),
                action_type=event.get("action_type", ""),
                indication_mesh=str(unit.get("indication_mesh") or ""),
                indication_term=str(unit.get("heading") or ""),
                drug_mesh=drug_mesh,
                trial_name=name,
                label_enrollment=label_count,
                registry_enrollment=registry.get(nct, ""),
                evidence_tier=tier_for(source, check, basis),
                evidence_source=source,
                count_check=check,
                drug_basis=basis,
            )
        )
        linked_submissions.add(key)

    for key, row in spine.items():
        if key not in linked_submissions:
            unlinked.append(
                Unlinked(
                    row["application"],
                    row["submission_type"],
                    row["submission_no"],
                    row["approval_date"],
                    "",
                    "no_description_extracted",
                )
            )

    return AssembleReport(
        links=tuple(sorted(links)),
        unlinked=tuple(sorted(unlinked)),
        by_tier=tuple(sorted(Counter(l.evidence_tier for l in links).items())),
        by_source=tuple(sorted(Counter(l.evidence_source for l in links).items())),
        by_reason=tuple(Counter(u.reason for u in unlinked).most_common()),
        submissions_linked=len(linked_submissions),
        submissions_total=len(spine),
    )


def _why(unit: dict[str, object]) -> str:
    """Why a unit never reached a judge."""
    if not unit.get("drug_mesh"):
        return "no_drug_resolved"
    return "no_candidates"


def render(report: AssembleReport) -> str:
    """The pass report, as ``--mode assemble`` prints it."""
    lines = [
        "Trial → submission map",
        "",
        f"  links                                    {len(report.links):>9,}",
        "",
        "  by evidence tier:",
    ]
    for tier, count in report.by_tier:
        lines.append(f"    {tier:<38}{count:>9,}")
    lines += ["", "  by evidence source:"]
    for source, count in report.by_source:
        lines.append(f"    {source:<38}{count:>9,}")
    lines += [
        "",
        f"  trial-relevant submissions               {report.submissions_total:>9,}",
        f"    with at least one link                 {report.submissions_linked:>9,}"
        f"  ({100 * report.coverage:.1f}%)",
        "",
        f"  unlinked rows                            {len(report.unlinked):>9,}",
    ]
    for reason, count in report.by_reason:
        lines.append(f"    {reason:<38}{count:>9,}")
    return "\n".join(lines)
