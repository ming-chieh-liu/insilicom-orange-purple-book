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
    #: ``true`` when the trial entered the shortlist through the literature
    #: rather than the drug filter. See :func:`tier_for`.
    injected: str = ""


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
    evidence_source: str,
    count_check: str,
    drug_basis: str = "",
    injected: bool = False,
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

    **An injected candidate never reaches tier A either.** It entered the
    shortlist because the literature named it, not because it passed the drug
    filter — one of only two hard facts the pool rests on. The judge still
    identified it from the label's own prose, so the link is real, but it rests
    on one fewer independent check.

    >>> tier_for("label_description", "counts_agree", "", True)
    'B'
    """
    if evidence_source == "label_nct":
        return TIER_STATED
    if drug_basis == "stem" or injected:
        return TIER_JUDGED
    return TIER_CORROBORATED if count_check == "counts_agree" else TIER_JUDGED


#: The one polarity verdict that means the label presents a trial as evidence.
#: Everything else -- ``NOT_SUPPORTING`` in v2, ``NEGATIVE`` and ``UNCLEAR`` in
#: v1 -- means it does not.
SUPPORTING: str = "SUPPORTING"


def refuted_pairs(path: Path) -> set[tuple[str, str]]:
    """``(nct_id, application)`` pairs section 14 does not present as evidence.

    **The OR rule, from the section-14 runbook.** A trial discussed in several
    subsections yields several verdicts, and if *any* says SUPPORTING the pair
    is supporting: Tyvaso cites one trial as both its pivotal study and its
    durability follow-up, and a judge may reasonably call the second
    not-supporting while the first plainly supports. A pair is refuted only
    when **every** verdict on it is unsupportive; a pair with no verdict is
    left alone, because unjudged is not refuted.

    **Known limitation.** These verdicts were made against openFDA's
    *current-snapshot* labels, at application grain, while the links they
    filter come from *per-submission* labels. Section 14 is cumulative and
    pruned, so a trial pivotal in 2015 can appear under a "lack of efficacy"
    heading today -- and this would then drop a correct 2015 link on the
    strength of a 2026 reading. Measured coverage is 2,005 of 2,293 pairs; how
    often the two readings disagree is not measured and would need the era
    labels judged separately.
    """
    supporting: set[tuple[str, str]] = set()
    against: set[tuple[str, str]] = set()
    for row in identification.iter_json_lines(path):
        nct = str(row.get("nct_id") or "")
        application = str(row.get("application") or "")
        if not nct or not application:
            continue
        target = supporting if row.get("final") == SUPPORTING else against
        target.add((nct, application))
    return against - supporting


def _submission_sort(number: str) -> tuple[int, str]:
    """Order submission numbers numerically, since the file stores strings.

    >>> sorted(["10", "9", "1"], key=_submission_sort)
    ['1', '9', '10']
    >>> _submission_sort("ORIG")
    (999999, 'ORIG')
    """
    return (int(number), number) if number.isdigit() else (999_999, number)


def _label_group(unit: dict[str, object]) -> str:
    """What identifies the label a unit came from, for tie-breaking.

    The PDF digest where P2 recorded one. Where it did not, the submission's
    own key -- unique by construction, so the unit groups only with itself and
    is never merged away on missing evidence. The same principle as
    :func:`service.candidates.within_date`: absent data must not hide a real
    answer.
    """
    sha = str(unit.get("label_sha") or "")
    return sha or (
        f"?{unit.get('application')}|{unit.get('submission_type')}"
        f"|{unit.get('submission_no')}"
    )


def _identified(
    unit: dict[str, object], verdicts: dict[str, identification.Verdict]
) -> str:
    """The trial this unit resolves to, by either route, or ``""``."""
    stated = str(unit.get("stated_nct") or "")
    if stated:
        return stated
    verdict = verdicts.get(str(unit.get("unit_id") or ""))
    return verdict.final if verdict is not None and verdict.identified else ""


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
    "refuted_pairs",
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
    # Section-14 polarity: trials the label cites but does not present as
    # evidence. The fenofibrate label discusses ACCORD Lipid under a heading
    # reading "Lack of Efficacy in Cardiovascular Outcomes Trials"; a regex
    # sees a trial id, and only a reader sees that it failed.
    refuted = refuted_pairs(adjudication_dir / "verdicts.jsonl")
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
    # A 351(k) copies the originator's section 14 verbatim, so its prose
    # describes a trial that legally cannot have supported it. P4 does not
    # judge those units at all (see identification.read_units), and they are
    # recorded here rather than left looking unjudged.
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
    #: ``(trial, submission)`` already written. The declared grain is one row
    #: per pair, and a label often cites one trial under two section-14
    #: headings -- KEYNOTE-158 under both "MSI-High" and "Endometrial
    #: Carcinoma" -- which is two units and would otherwise be two identical
    #: rows.
    emitted: set[tuple[str, str]] = set()
    units = list(read_jsonl(out_dir / "candidates.jsonl"))

    # D16, in two steps, and over **both** branches together. A trial can be
    # named in prose on an early submission and by id on a later one --
    # NCT01194219 is judged on NDA205437 SUPPL 7 and stated on SUPPL 11 -- so
    # each branch enforcing D16 privately still lets the pair through twice.
    #
    # Step one: the earliest date any submission of this application carries
    # the trial. Section 14 is cumulative, so later labels keep it without
    # having rested on it.
    earliest: dict[tuple[str, str], str] = {}
    for unit in units:
        found = _identified(unit, verdicts)
        if not found:
            continue
        pair = (str(unit["application"]), found)
        when = str(unit.get("approval_date") or "")
        if pair not in earliest or when < earliest[pair]:
            earliest[pair] = when

    # Step two: among the submissions tied at that date, the **label** decides
    # how many survive. FDA routinely approves several supplements on one day,
    # so dates alone let all of them through -- 140 (trial, application) pairs
    # spanned more than one submission and 138 were exact date ties. Measured
    # across those ties, 70 share one PDF and 70 have genuinely different ones:
    #
    # * **Same label bytes** -- one document counted several times. Temodar's
    #   NDA021029 SUPPL 36, 37 and 38 were all approved 2023-09-14 off a single
    #   ``021029s036_037_038lbl.pdf``, so NCT00006353 was claimed as evidence
    #   for three approvals when it was evidence for one event filed three
    #   ways. Keep the lowest submission number.
    # * **Different label bytes** -- distinct approvals that happen to share a
    #   date, like NDA208419 SUPPL 2, 3 and 4. Keep them all; they are
    #   different claims.
    keeper: dict[tuple[str, str, str, str], str] = {}
    for unit in units:
        found = _identified(unit, verdicts)
        when = str(unit.get("approval_date") or "")
        if not found or earliest.get((str(unit["application"]), found)) != when:
            continue
        group = (str(unit["application"]), found, when, _label_group(unit))
        number = str(unit["submission_no"])
        if group not in keeper or _submission_sort(number) < _submission_sort(
            keeper[group]
        ):
            keeper[group] = number

    submissions_with_units: set[str] = set()
    for unit in units:
        key = (
            f"{unit['application']}|{unit['submission_type']}"
            f"|{unit['submission_no']}"
        )
        submissions_with_units.add(key)
        event = spine.get(key, {})
        name = str(unit.get("acronym") or unit.get("heading") or "")
        stated = str(unit.get("stated_nct") or "")
        registry = _registry_counts(unit)
        label_count = str(unit.get("enrollment") or "")

        if stated:
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
                        "biosimilar_copied_label"
                        if stem_ids
                        and set(str(unit.get("drug_mesh") or "").split(";"))
                        & stem_ids
                        else "not_yet_judged"
                        if unit.get("candidates")
                        else _why(unit),
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

        when = str(unit.get("approval_date") or "")
        pair = (str(unit["application"]), nct)
        group = (str(unit["application"]), nct, when, _label_group(unit))
        if earliest.get(pair) != when:
            reason = "later_appearance_of_the_same_trial"
        elif keeper.get(group) != str(unit["submission_no"]):
            # Not later -- simultaneous. A sibling submission approved the same
            # day off the same label document already carries it.
            reason = "same_day_sibling_submission"
        else:
            reason = ""
        if reason:
            unlinked.append(
                Unlinked(
                    str(unit["application"]),
                    str(unit["submission_type"]),
                    str(unit["submission_no"]),
                    when,
                    name,
                    reason,
                )
            )
            continue

        raw_candidates = unit.get("candidates")
        was_injected = isinstance(raw_candidates, list) and any(
            isinstance(c, dict) and c.get("nct_id") == nct and c.get("injected")
            for c in raw_candidates
        )

        if (nct, str(unit["application"])) in refuted:
            unlinked.append(
                Unlinked(
                    str(unit["application"]),
                    str(unit["submission_type"]),
                    str(unit["submission_no"]),
                    when,
                    name,
                    "judged_not_supporting",
                )
            )
            continue

        if (nct, key) in emitted:
            continue
        emitted.add((nct, key))

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
                evidence_tier=tier_for(source, check, basis, was_injected),
                evidence_source=source,
                count_check=check,
                drug_basis=basis,
                injected="true" if was_injected else "",
            )
        )
        linked_submissions.add(key)

    # Only submissions that produced **no unit at all** get a catch-all row.
    # A submission whose units each failed for a stated reason is already
    # explained by those rows, and adding a second, vaguer row on top of them
    # double-counted 1,503 submissions — inflating the largest unlinked
    # category by 22% and, worse, admitting them to tier C through
    # :data:`service.graph_links.EXHAUSTED` although P4 had not looked yet.
    # That is exactly the pre-emption the EXHAUSTED filter exists to prevent:
    # 695 of 1,327 tier-C units were built for submissions still queued for a
    # judge.
    #
    # The reason is ``no_label`` rather than the old
    # ``no_description_extracted`` because that is what it measures. Splitting
    # the 6,757 showed **zero** submissions where a label PDF existed and no
    # description came out of it — section-14 extraction loses nothing. All
    # 5,254 genuinely unitless submissions simply have no type-2 label PDF in
    # ApplicationDocs, 3,351 of them pre-2000.
    for key, row in spine.items():
        if key not in linked_submissions and key not in submissions_with_units:
            unlinked.append(
                Unlinked(
                    row["application"],
                    row["submission_type"],
                    row["submission_no"],
                    row["approval_date"],
                    "",
                    "no_label",
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
