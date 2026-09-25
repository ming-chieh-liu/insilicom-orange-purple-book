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

from service import graph_judging, identification, judging, label_role, review_role
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
TIER_JUDGED: str = "B"
TIER_GRAPH: str = "C"

#: Label-route evidence sources that identify a trial without the shortlist.
DIRECT_SOURCES: frozenset[str] = frozenset({"label_nct", "review_name_match"})

#: D29 conflict values. A row can carry several, ``;``-joined.
ATTRIBUTION_MOVED = "attribution_moved"
ROLE_DISAGREES = "role_disagrees"
JUDGED_CONTRADICTS_DIRECT = "judged_contradicts_direct"
INDICATION_MISMATCH = "indication_mismatch"

#: D19 cross-check: the literature the graph connects to this application's
#: drug and indication names this trial (``graph_trials.tsv``). No judge — a
#: set-membership test — so it corroborates a link and never makes one.
KG_LITERATURE = "kg_literature"

#: Letter-derived indication sources, and the label source they must sit inside.
LETTER_SOURCES: frozenset[str] = frozenset({"letter", "letter_located"})
LABEL_INDICATION_SOURCE = "label_section_1"


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
    #: when two judges identified it from prose, ``review_name_match`` when the
    #: label's trial name is a protocol id or acronym the same submission's
    #: review resolved (D29), ``review_protocol`` for a review-only link.
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
    #: Every route that named this trial for this submission (D29):
    #: ``label_nct`` · ``label_description`` · ``review_protocol`` ·
    #: ``kg_abstract`` (a judged tier-C pick) · ``kg_literature`` (the D19
    #: cross-check: the graph's literature for this drug names the trial).
    sources: str = ""
    #: D29, ``;``-joined: ``attribution_moved`` (the routes' earliest
    #: submissions differ; D16 across both decided) · ``role_disagrees`` (label
    #: and review disagree on whether the trial supports this approval; held
    #: at tier B) · ``judged_contradicts_direct`` (a judge picked another trial
    #: for a description an FDA document identified; the direct one kept) ·
    #: ``indication_mismatch`` (the letter's indication is not inside label §1).
    conflict: str = ""


class ReviewContext(NamedTuple):
    """A trial a review names in a role that makes no link (D26)."""

    nct_id: str
    application: str
    submission_type: str
    submission_no: str
    protocol_id: str
    role: str


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
    """The tier a link earns: **how the trial's identity was established** (D32).

    Tier A when an FDA document names the trial by an identifier — the NCT id
    printed on the label, or (D29) the label's trial name matching a protocol id
    the same submission's FDA review named. Review-only links are tier A by D27
    and are built without this function.

    Tier B for everything an agent **inferred**: a §14 description matched to a
    shortlisted registry trial by two judges. Whatever the patient counts say.
    D17 used to promote a judged link to A when the label's and the registry's
    counts agreed within 15%, but the P4 prompt tells the judge the count is
    its strongest signal — so the judge chose on the count and the tier then
    rewarded the same count; and a ±15% match was unique in only 45 of 2,363
    known cases. The counts stay on the row (``count_check``, both
    enrollments) as information; they no longer set the tier. ``drug_basis``
    and ``injected`` are likewise recorded, not decisive: a judged link is B
    already.

    >>> tier_for("label_nct", "no_count"), tier_for("review_name_match", "counts_diverge", "stem")
    ('A', 'A')
    >>> tier_for("label_description", "counts_agree"), tier_for("label_description", "no_count")
    ('B', 'B')
    >>> tier_for("label_description", "counts_agree", "", True)
    'B'
    """
    return TIER_STATED if evidence_source in DIRECT_SOURCES else TIER_JUDGED


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
    stated = str(unit.get("stated_nct") or unit.get("review_nct") or "")
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
    "ReviewContext",
    "KG_LITERATURE",
    "add_conflict",
    "chosen_indications",
    "indication_mismatches",
    "refuted_pairs",
    "review_judgements",
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
    review_context: tuple[ReviewContext, ...] = ()
    attribution_moved: int = 0
    by_conflict: tuple[tuple[str, int], ...] = ()
    #: Label links with no D28 role yet; the old polarity verdict filtered them.
    label_role_missing: int = 0

    @property
    def coverage(self) -> float:
        """Share of trial-relevant submissions carrying at least one link.

        >>> AssembleReport((), (), (), (), (), 3, 4).coverage
        0.75
        >>> AssembleReport((), (), (), (), (), 0, 0).coverage
        0.0
        """
        return self.submissions_linked / self.submissions_total if self.submissions_total else 0.0


def add_conflict(held: str, new: str) -> str:
    """``held`` with ``new`` appended once.

    >>> add_conflict("", "role_disagrees")
    'role_disagrees'
    >>> add_conflict("attribution_moved", "role_disagrees")
    'attribution_moved;role_disagrees'
    >>> add_conflict("role_disagrees", "role_disagrees")
    'role_disagrees'
    >>> add_conflict("role_disagrees", "")
    'role_disagrees'
    """
    return ";".join(dict.fromkeys(p for p in (*held.split(";"), new) if p))


def indication_mismatches(out_dir: Path) -> set[str]:
    """Submissions whose letter indication is not contained in label §1 (D29).

    Label §1 is cumulative, so it legitimately lists more than this approval
    added; the test is containment of the letter's MeSH ids, not equality.
    Only submissions carrying both sources are tested.
    """
    letter: dict[str, set[str]] = {}
    label: dict[str, set[str]] = {}
    for row in _read_tsv(out_dir / "submission_indications.tsv"):
        mesh = row.get("mesh_id") or ""
        if not mesh:
            continue
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        if row.get("source") in LETTER_SOURCES:
            letter.setdefault(key, set()).add(mesh)
        elif row.get("source") == LABEL_INDICATION_SOURCE:
            label.setdefault(key, set()).add(mesh)
    return {key for key, ids in letter.items() if key in label and not ids <= label[key]}


def _read_tsv(path: Path) -> Iterator[dict[str, str]]:
    """Rows of a TSV, nothing if the file is absent."""
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def chosen_indications(out_dir: Path) -> dict[str, tuple[str, str]]:
    """``application|type|number`` → ``(mesh ids, terms)`` of the submission's
    chosen indication, each ``;``-joined (D30).

    A link built from a label description carries its own trial's disease from
    the §14 heading; a review-only link has no heading, so it takes the
    approval's chosen indication instead.
    """
    ids: dict[str, list[str]] = {}
    terms: dict[str, list[str]] = {}
    for row in _read_tsv(out_dir / "submission_indications.tsv"):
        if row.get("chosen") != "true" or not row.get("mesh_id"):
            continue
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        if row["mesh_id"] not in ids.setdefault(key, []):
            ids[key].append(row["mesh_id"])
            terms.setdefault(key, []).append(row.get("term") or "")
    return {key: (";".join(v), ";".join(terms[key])) for key, v in ids.items()}


def review_judgements(adjudication_dir: Path) -> list[tuple[str, str, str, str]]:
    """``(submission key, nct, role, protocol id)`` for every review-route unit
    decided under the current role prompt (D26, D27)."""
    protocol = {
        str(u["unit_id"]): str(u.get("protocol_id") or "")
        for u in judging.read_jsonl(adjudication_dir / "review_role_units.jsonl")
    }
    out: list[tuple[str, str, str, str]] = []
    for unit_id, row in judging.recorded(adjudication_dir / "review_role_verdicts.jsonl").items():
        if row.get("prompt_version") != review_role.PROMPT_VERSION:
            continue
        role = str(row.get("final") or "")
        if role not in review_role.ROLES or unit_id.count("|") != 3:
            continue
        key, nct = unit_id.rsplit("|", 1)
        out.append((key, nct, role, protocol.get(unit_id, "")))
    return out


def build(out_dir: Path, adjudication_dir: Path, *,
          apply_label_roles: bool = True) -> AssembleReport:
    """Join the candidate units, the verdicts and the spine into the map.

    ``apply_label_roles=False`` skips every support filter on label links — the
    D28 role and the old polarity verdict alike — and is how D28's units are
    built: every link P5 would make, judged where it is attributed.
    """
    from collections import Counter

    verdicts = identification.read_verdicts(
        adjudication_dir / "identify_verdicts.jsonl"
    )
    # Section-14 polarity: trials the label cites but does not present as
    # evidence. The fenofibrate label discusses ACCORD Lipid under a heading
    # reading "Lack of Efficacy in Cardiovascular Outcomes Trials"; a regex
    # sees a trial id, and only a reader sees that it failed.
    refuted = refuted_pairs(adjudication_dir / "verdicts.jsonl")
    # D28: the role each label link plays in its own submission's label. Where
    # it exists it replaces the polarity verdict above, which read today's
    # label per application; the old verdict is the fallback until D28 has
    # judged every link.
    label_roles = label_role.read_roles(adjudication_dir / "label_role_verdicts.jsonl")
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
    # D24: labeling/manufacturing changes *with clinical data* are read for D16
    # but not judged. A spine written before ``trial_scope`` existed has none.
    guard = {key for key, row in spine.items() if row.get("trial_scope") == "guard"}

    links: list[Link] = []
    unlinked: list[Unlinked] = []
    linked_submissions: set[str] = set()
    #: ``(trial, submission)`` already written. The declared grain is one row
    #: per pair, and a label often cites one trial under two section-14
    #: headings -- KEYNOTE-158 under both "MSI-High" and "Endometrial
    #: Carcinoma" -- which is two units and would otherwise be two identical
    #: rows.
    emitted: set[tuple[str, str]] = set()
    #: ``(trial, submission)`` the label side rejected -- a D28 role that does not
    #: link, or the old polarity verdict -- so a review judging it evidence is a
    #: recorded disagreement.
    label_rejected: set[tuple[str, str]] = set()
    units = list(read_jsonl(out_dir / "candidates.jsonl"))
    guard_led = _guard_led_units(units, guard)

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
    label_earliest = dict(earliest)
    # D16 across routes (D26): a trial a review names in a linking role counts
    # as appearing at that review's submission, so whichever route saw it
    # first decides where the link goes.
    judged_reviews = review_judgements(adjudication_dir)
    review_links = [
        (key, nct, protocol) for key, nct, role, protocol in judged_reviews
        if role in review_role.LINKING and key in spine
    ]
    review_earliest: dict[tuple[str, str], str] = {}
    for key, nct, _protocol in review_links:
        pair = (key.split("|", 1)[0], nct)
        when = spine[key].get("approval_date", "")
        if pair not in review_earliest or when < review_earliest[pair]:
            review_earliest[pair] = when
        if pair not in earliest or when < earliest[pair]:
            earliest[pair] = when
    moved = {
        pair for pair in label_earliest
        if pair in review_earliest and review_earliest[pair] != label_earliest[pair]
    }

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

        direct = str(unit.get("review_nct") or "")
        contradicted = False
        if stated:
            nct, source = stated, "label_nct"
        elif direct:
            # D29: the review identified this description; a judge's pick from
            # before the skip existed is kept only as a recorded disagreement.
            nct, source = direct, "review_name_match"
            earlier = verdicts.get(str(unit.get("unit_id") or ""))
            contradicted = bool(earlier and earlier.identified and earlier.final != direct)
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
                        else "label_change_not_judged"
                        if key in guard
                        else "later_appearance_of_the_same_trial"
                        if str(unit.get("unit_id") or "") in guard_led
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

        role = label_roles.get((key, nct))
        if not apply_label_roles:
            rejection = ""
        elif role is not None:
            rejection = "" if role in review_role.LINKING else f"label_role_{role.lower()}"
        elif (nct, str(unit["application"])) in refuted:
            rejection = "judged_not_supporting"
        else:
            rejection = ""
        if rejection:
            label_rejected.add((nct, key))
            unlinked.append(
                Unlinked(
                    str(unit["application"]),
                    str(unit["submission_type"]),
                    str(unit["submission_no"]),
                    when,
                    name,
                    rejection,
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
                sources="label_description;review_protocol"
                if source == "review_name_match" else source,
                conflict=add_conflict(
                    ATTRIBUTION_MOVED if (str(unit["application"]), nct) in moved else "",
                    JUDGED_CONTRADICTS_DIRECT if contradicted else "",
                ),
            )
        )
        linked_submissions.add(key)

    # Review-route links (D27): tier A outright, merged into a label link that
    # already names the same trial for the same submission.
    position = {(link.nct_id, f"{link.application}|{link.submission_type}|{link.submission_no}"): i
                for i, link in enumerate(links)}
    chosen = chosen_indications(out_dir)
    for key, nct, protocol in review_links:
        application = key.split("|", 1)[0]
        when = spine[key].get("approval_date", "")
        if earliest.get((application, nct)) != when:
            continue
        conflict = ATTRIBUTION_MOVED if (application, nct) in moved else ""
        if (nct, key) in label_rejected:
            conflict = add_conflict(conflict, ROLE_DISAGREES)
        if (nct, key) in position:
            held = links[position[(nct, key)]]
            links[position[(nct, key)]] = held._replace(
                sources=";".join(dict.fromkeys([*held.sources.split(";"), "review_protocol"])),
                evidence_tier=TIER_STATED,
                conflict=add_conflict(held.conflict, conflict),
            )
            continue
        _, submission_type, submission_no = key.split("|")
        links.append(Link(
            nct_id=nct, application=application, submission_type=submission_type,
            submission_no=submission_no, approval_date=when,
            action_type=spine[key].get("action_type", ""),
            indication_mesh=chosen.get(key, ("", ""))[0],
            indication_term=chosen.get(key, ("", ""))[1],
            drug_mesh="", trial_name=protocol, label_enrollment="",
            registry_enrollment="", evidence_tier=TIER_STATED,
            evidence_source="review_protocol", count_check="no_count",
            sources="review_protocol", conflict=conflict,
        ))
        position[(nct, key)] = len(links) - 1
        linked_submissions.add(key)
    # Tier C (D14, D31): a graph pick the judges agreed on. It never moves or
    # replaces a stronger link: where another route already links the trial for
    # this submission it only adds ``kg_abstract`` to that row's sources (the D19
    # cross-check); where another route attributes the trial to another
    # submission of the application, it adds nothing. Among tier-C picks
    # themselves D16 still holds -- the earliest submission keeps it.
    linked_trials = {(link.application, link.nct_id) for link in links}
    graph_first: dict[tuple[str, str], tuple[str, tuple[int, str], str]] = {}
    for key, nct in graph_judging.read_picks(adjudication_dir / "graph_verdicts.jsonl"):
        if key not in spine:
            continue
        application, _, submission_no = key.split("|")
        if (nct, key) in position:
            held = links[position[(nct, key)]]
            links[position[(nct, key)]] = held._replace(sources=";".join(
                dict.fromkeys([*held.sources.split(";"), graph_judging.EVIDENCE_SOURCE])))
            continue
        if (application, nct) in linked_trials:
            continue
        rank_key = (spine[key].get("approval_date", ""), _submission_sort(submission_no))
        held_first = graph_first.get((application, nct))
        if held_first is None or rank_key < (held_first[0], held_first[1]):
            graph_first[(application, nct)] = (rank_key[0], rank_key[1], key)
    for (application, nct), (when, _, key) in sorted(graph_first.items()):
        _, submission_type, submission_no = key.split("|")
        links.append(Link(
            nct_id=nct, application=application, submission_type=submission_type,
            submission_no=submission_no, approval_date=when,
            action_type=spine[key].get("action_type", ""),
            indication_mesh=chosen.get(key, ("", ""))[0],
            indication_term=chosen.get(key, ("", ""))[1],
            drug_mesh="", trial_name="", label_enrollment="", registry_enrollment="",
            evidence_tier=TIER_GRAPH, evidence_source=graph_judging.EVIDENCE_SOURCE,
            count_check="no_count", sources=graph_judging.EVIDENCE_SOURCE,
        ))
        linked_submissions.add(key)

    context_rows: list[ReviewContext] = []
    for key, nct, role, protocol in judged_reviews:
        if role in review_role.LINKING:
            continue
        application, submission_type, submission_no = key.split("|")
        context_rows.append(ReviewContext(nct, application, submission_type, submission_no,
                                          protocol, role))
    context = tuple(sorted(context_rows))

    # D29 role_disagrees: the label route links a trial the same submission's
    # review judged as *not* evidence for it (FAILED, NOT_FOR_THIS_APPROVAL…),
    # or the review links one the label side rejected (D28 role, else old polarity). Either way
    # the row stays, but not at tier A until someone resolves it.
    non_linking = {(key, nct) for key, nct, role, _ in judged_reviews
                   if role not in review_role.LINKING}
    mismatched = indication_mismatches(out_dir)
    literature: dict[str, set[str]] = {}
    for row in _read_tsv(out_dir / "graph_trials.tsv"):
        literature.setdefault(row["application"], set()).add(row["nct_id"])
    for i, link in enumerate(links):
        key = f"{link.application}|{link.submission_type}|{link.submission_no}"
        if (link.evidence_source != graph_judging.EVIDENCE_SOURCE
                and link.nct_id in literature.get(link.application, ())):
            link = link._replace(sources=";".join(
                dict.fromkeys([*link.sources.split(";"), KG_LITERATURE])))
            links[i] = link
        conflict = link.conflict
        if (key, link.nct_id) in non_linking or (
            (link.nct_id, key) in label_rejected and "review_protocol" in link.sources
        ):
            conflict = add_conflict(conflict, ROLE_DISAGREES)
        if key in mismatched:
            conflict = add_conflict(conflict, INDICATION_MISMATCH)
        tier = TIER_JUDGED if ROLE_DISAGREES in conflict.split(";") else link.evidence_tier
        if (conflict, tier) != (link.conflict, link.evidence_tier):
            links[i] = link._replace(conflict=conflict, evidence_tier=tier)

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
        review_context=context,
        attribution_moved=sum(
            1 for link in links if ATTRIBUTION_MOVED in link.conflict.split(";")
        ),
        label_role_missing=sum(
            1 for link in links
            if set(link.sources.split(";")) & label_role.LABEL_SOURCES
            and (f"{link.application}|{link.submission_type}|{link.submission_no}",
                 link.nct_id) not in label_roles
        ),
        by_conflict=tuple(Counter(
            part for link in links for part in link.conflict.split(";") if part
        ).most_common()),
    )


def _guard_led_units(
    units: list[dict[str, object]], guard: set[str]
) -> set[str]:
    """Unit ids that are later appearances of a trial first carried by a guard.

    P4 groups a label's trial descriptions by ``(application, trial)`` and
    judges only the earliest (D16). When that earliest submission is a D24
    guard, the whole group is withheld, so its later members never receive a
    verdict. They are not waiting for a judge — the trial's first appearance is
    on a submission that is deliberately not judged — so they are reported as
    later appearances, not as ``not_yet_judged``. Units that state their own NCT
    are left to the ordinary D16 path.

    >>> units = [
    ...     {"unit_id": "A|SUPPL|3|X", "application": "A", "submission_type": "SUPPL",
    ...      "submission_no": "3", "approval_date": "2010-01-01"},
    ...     {"unit_id": "A|SUPPL|9|X", "application": "A", "submission_type": "SUPPL",
    ...      "submission_no": "9", "approval_date": "2015-01-01"},
    ... ]
    >>> sorted(_guard_led_units(units, {"A|SUPPL|3"}))
    ['A|SUPPL|9|X']
    >>> sorted(_guard_led_units(units, set()))
    []
    """
    if not guard:
        return set()
    first: dict[tuple[str, str], dict[str, object]] = {}
    members: dict[tuple[str, str], list[str]] = {}
    for unit in units:
        if unit.get("stated_nct") or unit.get("review_nct"):
            continue
        unit_id = str(unit.get("unit_id") or "")
        group = (str(unit.get("application") or ""), unit_id.rsplit("|", 1)[-1])
        members.setdefault(group, []).append(unit_id)
        held = first.get(group)
        if held is None or str(unit.get("approval_date") or "") < str(
            held.get("approval_date") or ""
        ):
            first[group] = unit
    out: set[str] = set()
    for group, head in first.items():
        head_key = (
            f"{head['application']}|{head['submission_type']}|{head['submission_no']}"
        )
        if head_key in guard:
            out.update(u for u in members[group] if u != head.get("unit_id"))
    return out


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
        f"    attribution moved between routes (D16) {report.attribution_moved:>9,}",
        f"  review-named trials kept as context      {len(report.review_context):>9,}",
        f"  label links without a D28 role yet       {report.label_role_missing:>9,}",
        "",
        "  by evidence tier:",
    ]
    for tier, count in report.by_tier:
        lines.append(f"    {tier:<38}{count:>9,}")
    if report.by_conflict:
        lines += ["", "  conflicts (D29; a link can carry several):"]
        for name, count in report.by_conflict:
            lines.append(f"    {name:<38}{count:>9,}")
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
