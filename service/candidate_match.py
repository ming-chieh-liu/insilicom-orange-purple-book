"""P4 per-candidate matching (D33): Yes/No per candidate, ranked tiers of ten.

The previous P4 (:mod:`service.identification`, kept for reuse) showed a judge a
description and its whole shortlist and asked for one answer. D33 asks, for each
candidate separately, whether it *is* the trial the description names.

P3 returns every shortlist in rank order, so the work goes in **tiers**: ranks
1-10, then 11-20, and so on through the whole list, with no cap at 100. A
description stops at the first tier holding a Yes, and its answer is the
highest-ranked Yes in that tier. A description whose every tier is No is NONE.
Measured on the 11,792 descriptions whose label states the NCT id: the true
trial sits at median rank 2, and 74% of shortlisted answers are in the top 10,
so most descriptions finish in one tier.

**One judge for the full run, two for the pilot.** The project convention asks
for two independent judges per unit; D33 makes a recorded exception for cost,
resting on the pilot's measured agreement between two judges. With two judges a
candidate's final answer needs agreement, and a disagreement goes to a third
judge whose answer is final; with one judge, judge A's answer is final.

Tiers are judged in rounds. ``prepare`` writes only each description's *next*
tier, so a run is resumable and never judges a tier it will not need.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

import csv

from service import drug_names, graph_links, graph_trials, identification
from utility.logging import get_logger

_log = get_logger(__name__)

#: Bumped whenever ``docs/prompts/trial-candidate-match.md`` changes, and stamped
#: on every row this module writes.
PROMPT_VERSION: int = 4

#: The no-label prompt (D35) — cases C and D, where there is no §14 excerpt —
#: ``docs/prompts/trial-candidate-match-nolabel.md``, with its own version.
NOLABEL_PROMPT: str = "trial-candidate-match-nolabel"
NOLABEL_PROMPT_VERSION: int = 5
#: The excerpt prompt's name, stamped beside its version.
EXCERPT_PROMPT: str = "trial-candidate-match"

#: The literature prompt (D37) — routes B+, C+ and D, whose candidates the
#: literature names and are shown with their abstracts —
#: ``docs/prompts/trial-literature-match.md``.
LITERATURE_PROMPT: str = "trial-literature-match"
LITERATURE_PROMPT_VERSION: int = 4

#: Every prompt's current version, for stamping answers.
PROMPT_VERSIONS: dict[str, int] = {
    EXCERPT_PROMPT: PROMPT_VERSION,
    NOLABEL_PROMPT: NOLABEL_PROMPT_VERSION,
    LITERATURE_PROMPT: LITERATURE_PROMPT_VERSION,
}

#: The (prompt, version) pairs whose answers are current.
CURRENT_PROMPTS: frozenset[tuple[str, int]] = frozenset(PROMPT_VERSIONS.items())

#: Abstracts shown per literature candidate (D37).
ABSTRACTS_SHOWN: int = 3
#: Characters of one abstract shown. 4,500 covers 99% of saved abstracts
#: (2026-10-02, literature v4): at 2,500, 9.2% of trial–abstract pairs lost the
#: NCT id the abstract writes at its end, and the conclusions with it.
ABSTRACT_KEEP: int = 4500

#: What a decided description is stamped with in ``identify_verdicts.jsonl``,
#: which P5 reads. Above the pick-one prompt's version 1, so a D33 decision
#: supersedes an older answer for the same unit (last row wins). **3 since
#: D35**: version 2 was the first D33 pilot, judged on the pre-D35 shortlists,
#: and P5 reads only decisions of this version or later
#: (``assemble.MIN_IDENTIFY_VERSION``). **4 since 2026-10-02**: the 100
#: version-3 decisions were judged with older templates (registry v2/v3,
#: literature v1-v3); the fresh run on registry v4, no-label v4 and
#: literature v4 must not inherit them.
IDENTIFY_VERSION: int = 4

#: Candidates per tier.
TIER_SIZE: int = 10

YES: str = "Yes"
NO: str = "No"

#: ``<tier_id> | <NCT id> = Yes|No`` — one line per candidate in a judge file.
ANSWER_LINE = re.compile(r"^(?P<tier>.+?)\s*\|\s*(?P<nct>NCT\d{8})\s*=\s*(?P<answer>Yes|No)\s*$")


class TierUnit(NamedTuple):
    """One tier of one description, as a judge reads it."""

    tier_id: str
    unit_id: str
    tier: int
    approval: str
    excerpt: str
    trial_name: str
    enrollment: str
    candidate_ids: tuple[str, ...]
    candidates: str
    #: Which prompt the tier is judged with (D35): :data:`EXCERPT_PROMPT` for a
    #: §14 description, :data:`NOLABEL_PROMPT` for cases C and D, which read
    #: ``drug``, ``indication`` and ``approval_date`` instead of an excerpt.
    prompt: str = EXCERPT_PROMPT
    drug: str = ""
    indication: str = ""
    approval_date: str = ""
    #: :data:`LITERATURE_PROMPT` tiers: what the FDA documents say about the
    #: approval — the §14 subsection (B+), the indication (C+) or nothing (D).
    label_block: str = ""
    #: :data:`NOLABEL_PROMPT` tiers (v5): what this product is — a new form of an
    #: earlier-approved drug, or not (:func:`attach_products`).
    product: str = ""


class CandidateAnswer(NamedTuple):
    """One candidate's recorded answer."""

    tier_id: str
    unit_id: str
    tier: int
    rank: int
    nct_id: str
    judge_a: str
    judge_b: str | None
    tiebreak: str | None
    final: str


class Decision(NamedTuple):
    """A description's outcome once its tiers say enough."""

    unit_id: str
    final: str
    tier: int
    tiers_judged: int
    #: The tier ids (content digests included) the outcome was read from, so a
    #: decision reached on tiers that have since changed can be told apart.
    basis: tuple[str, ...] = ()
    #: D37 route of the deciding tier: ``B+``, ``B-``, ``C+``, ``C-`` or ``D``;
    #: empty for NONE.
    route: str = ""


def tier_id_for(unit_id: str, tier: int, digest: str = "") -> str:
    """The key one tier is recorded against.

    ``digest`` is :func:`input_digest` of what the judge is shown. It is part
    of the key so that an answer given to one version of a tier can never be
    read as the answer to another: when P3 rebuilds a shortlist or P2i changes
    an indication, the tier gets a new id and is judged again, while an
    unchanged tier keeps its id and its answers.

    >>> tier_id_for("BLA125514|SUPPL|35|KEYNOTE-189", 2, "0123abcd")
    'BLA125514|SUPPL|35|KEYNOTE-189#T2@0123abcd'
    >>> tier_id_for("BLA125514|SUPPL|35|KEYNOTE-189", 2)
    'BLA125514|SUPPL|35|KEYNOTE-189#T2'
    """
    return f"{unit_id}#T{tier}" + (f"@{digest}" if digest else "")


#: Length of the content digest carried in a tier id.
DIGEST_LENGTH: int = 12

#: The tier fields each prompt shows its judge — the only ones the digest reads.
VISIBLE_FIELDS: dict[str, tuple[str, ...]] = {
    EXCERPT_PROMPT: ("approval", "excerpt", "trial_name", "enrollment", "candidates"),
    NOLABEL_PROMPT: ("drug", "indication", "approval_date", "product", "candidates"),
    LITERATURE_PROMPT: ("approval", "label_block", "candidates"),
}


def input_digest(tier: "TierUnit") -> str:
    """A digest of exactly what the judge of ``tier`` reads, prompt version included.

    >>> base = dict(tier_id="", unit_id="u", tier=1, approval="a", excerpt="e",
    ...             trial_name="t", enrollment="n", candidate_ids=("NCT00000001",),
    ...             candidates="c", prompt=NOLABEL_PROMPT, drug="d",
    ...             indication="i", approval_date="2020-01-01")
    >>> one = input_digest(TierUnit(**base))
    >>> one == input_digest(TierUnit(**{**base, "excerpt": "unseen by this prompt"}))
    True
    >>> one == input_digest(TierUnit(**{**base, "indication": "changed"}))
    False
    """
    fields = {name: getattr(tier, name) for name in VISIBLE_FIELDS[tier.prompt]}
    payload = json.dumps(
        {"prompt": tier.prompt, "version": PROMPT_VERSIONS[tier.prompt],
         "candidate_ids": list(tier.candidate_ids), **fields},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def tier_count(candidates: int) -> int:
    """How many tiers a shortlist of this length has.

    >>> tier_count(62), tier_count(10), tier_count(0)
    (7, 1, 0)
    """
    return math.ceil(candidates / TIER_SIZE)


def render_candidate(index: int, candidate: dict[str, object]) -> str:
    """One candidate block, fields in the order the prompt lists them."""

    def field(key: str, width: int = 1000) -> str:
        return str(candidate.get(key) or "not stated")[:width]

    return "\n".join(
        [
            f"{index}. {candidate['nct_id']}",
            f"   Title: {field('brief_title', 200)}",
            f"   Acronym: {field('acronym')}",
            f"   Protocol id: {field('org_study_id')}",
            f"   Sponsor: {field('lead_sponsor')}",
            f"   Phase: {field('phases')}",
            f"   Conditions: {field('conditions')}",
        ]
        + _normalised("Conditions", candidate.get("condition_codes"))
        + [
            f"   Interventions: {field('interventions')}",
        ]
        + _normalised("Interventions", candidate.get("intervention_codes"))
        + [
            f"   Enrolled: {field('enrollment')}",
            f"   Start date: {field('start_date')}",
            f"   Primary completion: {field('primary_completion')}",
            f"   Status: {field('overall_status')}",
        ]
        + (
            [f"   Why stopped: {field('why_stopped')}"]
            if candidate.get("why_stopped")
            else []
        )
        + (
            [f"   Named in literature on: {field('found_under')}"]
            if candidate.get("found_under")
            else []
        )
    )


def _with_codes(text: str, codes: object) -> str:
    """A field's text and, on its own line, our coding of it (D39, prompt v3).

    >>> _with_codes("BRUKINSA (zanubrutinib)", "zanubrutinib [MeSH C000629551]")
    'BRUKINSA (zanubrutinib)\\n(normalised: zanubrutinib [MeSH C000629551])'
    >>> _with_codes("x", "")
    'x'
    """
    return f"{text}\n(normalised: {codes})" if codes else text


def _normalised(label: str, codes: object) -> list[str]:
    """``   <label> (normalised): …`` when codes exist (D39, prompt v3)."""
    return [f"   {label} (normalised): {str(codes)[:1000]}"] if codes else []


def approval_line(unit: dict[str, object]) -> str:
    """The APPROVAL field: drug, submission, date and their codes.

    The §14 heading is not repeated here (2026-10-01): B+ and B− show the
    whole subsection, which opens with its heading, and the indication's code
    follows on its own line.

    >>> approval_line({"application": "BLA125514", "submission_type": "SUPPL",
    ...                "submission_no": "35", "approval_date": "2017-05-10",
    ...                "heading": "Non-Small Cell Lung Cancer"})
    'BLA125514 SUPPL 35 · approved 2017-05-10'
    >>> approval_line({"application": "BLA125514", "drug": "KEYTRUDA (pembrolizumab)"})
    'KEYTRUDA (pembrolizumab) · BLA125514   · approved unknown'
    >>> print(approval_line({"application": "NDA1", "drug_codes": "Zanubrutinib [MeSH C000629551]",
    ...                      "indication_codes": "Waldenstrom Macroglobulinemia [MeSH D008258]"}))
    NDA1   · approved unknown
    Drug (normalised): Zanubrutinib [MeSH C000629551]
    Indication (normalised): Waldenstrom Macroglobulinemia [MeSH D008258]
    """
    drug = str(unit.get("drug") or "")
    line = (
        (f"{drug} · " if drug else "")
        + f"{unit.get('application') or ''} {unit.get('submission_type') or ''} "
        f"{unit.get('submission_no') or ''} · approved {unit.get('approval_date') or 'unknown'}"
    )
    # D39 (prompt v3): our coding of the drug and the indication, beside the text.
    for label, key in (("Drug", "drug_codes"), ("Indication", "indication_codes")):
        if unit.get(key):
            line += f"\n{label} (normalised): {unit[key]}"
    return line


def _listed(unit: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = unit.get(key)
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def literature_tiers(unit: dict[str, object]) -> int:
    """How many tiers the unit's literature set ("+" route) fills (D37)."""
    return tier_count(len(_listed(unit, "literature")))


def total_tiers(unit: dict[str, object]) -> int:
    """Literature tiers, then registry-shortlist tiers ("+" before "−", D37).

    >>> total_tiers({"literature": [{}] * 3, "candidates": [{}] * 25})
    4
    """
    return literature_tiers(unit) + tier_count(len(_listed(unit, "candidates")))


def route_of(unit: dict[str, object], tier: int) -> str:
    """The D37 route a tier belongs to.

    >>> route_of({"case": "B", "literature": [{}]}, 1), route_of({"case": "B", "literature": [{}]}, 2)
    ('B+', 'B-')
    >>> route_of({"case": "D", "literature": [{}]}, 1)
    'D'
    """
    case = str(unit.get("case") or "B")
    if case == "D":
        return "D"
    return f"{case}+" if tier <= literature_tiers(unit) else f"{case}-"


def label_block(unit: dict[str, object]) -> str:
    """What the FDA documents say about the approval, for the literature prompt.

    >>> label_block({"case": "D"})
    'No description of the trial is available. Approved for: not recorded.'
    >>> label_block({"case": "C", "indication_text": "provides for asthma"})
    'No description of the trial is available. Approved for: provides for asthma'
    """
    case = str(unit.get("case") or "B")
    copy = str(unit.get("copy_of") or "")
    product = (f"\nTHIS PRODUCT: {copy} Its evidence is a trial of this product — its new "
               "form, route or formulation, or a new use — not the trials of the original "
               "drug." if copy else "")
    if case == "D":
        return ("No description of the trial is available. Approved for: not recorded."
                + product)
    if case == "C":
        return ("No description of the trial is available. Approved for: "
                + str(unit.get("indication_text") or "not recorded") + product)
    text = str(unit.get("subsection") or unit.get("excerpt") or "")
    name = str(unit.get("acronym") or "(none found)")
    size = str(unit.get("enrollment") or "(none found)")
    return (
        "Section 14 (Clinical Studies) of this approval's label (it may describe "
        "more than one trial):\n"
        f"<section_14>\n{text}\n</section_14>\n"
        f"THE TRIAL IN QUESTION: a program read its name as: {name} (may be wrong)\n"
        f"and its size as: {size} patients (may be wrong)"
    )


def render_literature_candidate(index: int, candidate: dict[str, object]) -> str:
    """A registry block followed by the abstracts naming the trial (D37)."""
    raw = candidate.get("abstracts")
    papers = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    lines = [render_candidate(index, candidate),
             f"   Abstracts naming this trial ({len(papers)} shown):"]
    for paper in papers[:ABSTRACTS_SHOWN]:
        lines.append(f"     PMID {paper.get('pmid')} — {paper.get('title') or ''}")
        text = " ".join(str(paper.get("abstract") or "(no abstract)").split())
        lines.append(f"     {text[:ABSTRACT_KEEP]}")
    return "\n".join(lines)


def build_tier(unit: dict[str, object], tier: int) -> TierUnit:
    """Tier ``tier`` (1-based) of a unit: literature tiers first, then registry (D37)."""
    unit_id = str(unit.get("unit_id") or "")
    literature = _listed(unit, "literature")
    lit_tiers = tier_count(len(literature))
    if tier <= lit_tiers:
        start = (tier - 1) * TIER_SIZE
        chosen = literature[start : start + TIER_SIZE]
        approval = approval_line(unit)
        built = TierUnit(
            prompt=LITERATURE_PROMPT,
            label_block=label_block(unit),
            tier_id="",
            unit_id=unit_id,
            tier=tier,
            approval=approval,
            excerpt="",
            trial_name="",
            enrollment="",
            candidate_ids=tuple(str(c["nct_id"]) for c in chosen),
            candidates="\n\n".join(
                render_literature_candidate(start + offset + 1, candidate)
                for offset, candidate in enumerate(chosen)
            ),
        )
        return built._replace(tier_id=tier_id_for(unit_id, tier, input_digest(built)))
    candidates = _listed(unit, "candidates")
    start = (tier - lit_tiers - 1) * TIER_SIZE
    chosen = candidates[start : start + TIER_SIZE]
    no_label = str(unit.get("case") or "B") in ("C", "D")
    built = TierUnit(
        prompt=NOLABEL_PROMPT if no_label else EXCERPT_PROMPT,
        drug=_with_codes(str(unit.get("drug") or ""), unit.get("drug_codes"))
        if no_label else "",
        indication=(_with_codes(str(unit.get("indication_text") or "not recorded"),
                                unit.get("indication_codes"))
                    if no_label else ""),
        approval_date=str(unit.get("approval_date") or "unknown") if no_label else "",
        product=(str(unit.get("copy_of") or "") or NOT_A_COPY) if no_label else "",
        tier_id="",
        unit_id=unit_id,
        tier=tier,
        approval=approval_line(unit),
        # D37: the whole §14 subsection where S4 kept it, else the sentence.
        excerpt=str(unit.get("subsection") or unit.get("excerpt") or ""),
        trial_name=str(unit.get("acronym") or "(the label gives no name)"),
        enrollment=str(unit.get("enrollment") or "(the label gives no count)"),
        candidate_ids=tuple(str(c["nct_id"]) for c in chosen),
        candidates="\n".join(
            render_candidate(start + offset + 1, candidate)
            for offset, candidate in enumerate(chosen)
        ),
    )
    return built._replace(tier_id=tier_id_for(unit_id, tier, input_digest(built)))


def sample_order(units: list[dict[str, object]], limit: int | None) -> list[dict[str, object]]:
    """A deterministic, content-blind order, so a pilot is a fair sample.

    Hashing the unit id avoids the bias of choosing by shortlist size or by
    whether the label named the trial, both of which change the cost and the
    answer rate a pilot is meant to measure.
    """

    def key(unit: dict[str, object]) -> str:
        return hashlib.sha256(str(unit.get("unit_id") or "").encode()).hexdigest()

    ordered = sorted(units, key=key)
    return ordered if limit is None else ordered[:limit]


def read_answers(path: Path) -> list[CandidateAnswer]:
    """Every candidate answer recorded so far, last row per (tier, nct) winning."""
    out: dict[tuple[str, str], CandidateAnswer] = {}
    for row in identification.iter_json_lines(path):
        prompt = str(row.get("prompt") or EXCERPT_PROMPT)
        if (prompt, row.get("prompt_version")) not in CURRENT_PROMPTS:
            continue
        answer = CandidateAnswer(
            tier_id=str(row.get("tier_id") or ""),
            unit_id=str(row.get("unit_id") or ""),
            tier=int(str(row.get("tier") or 0)),
            rank=int(str(row.get("rank") or 0)),
            nct_id=str(row.get("nct_id") or ""),
            judge_a=str(row.get("judge_a") or ""),
            judge_b=str(row["judge_b"]) if row.get("judge_b") else None,
            tiebreak=str(row["tiebreak"]) if row.get("tiebreak") else None,
            final=str(row.get("final") or ""),
        )
        out[(answer.tier_id, answer.nct_id)] = answer
    return list(out.values())


def decide(
    unit: dict[str, object], answers: Iterable[CandidateAnswer]
) -> Decision | None:
    """The description's outcome, or None while it still needs a tier.

    Tiers are read in order. The first tier with any Yes decides: the answer
    is its highest-ranked Yes. A tier is only read once every candidate in it
    has a final answer. Every tier No means NONE. D37: the literature tiers
    ("+") come before the registry tiers ("−"), so a Yes in the literature
    stops the unit before its shortlist is read.
    """
    unit_id = str(unit.get("unit_id") or "")
    by_tier_id: dict[str, dict[str, CandidateAnswer]] = {}
    for answer in answers:
        if answer.unit_id == unit_id:
            by_tier_id.setdefault(answer.tier_id, {})[answer.nct_id] = answer
    tiers = total_tiers(unit)
    basis: list[str] = []
    for tier in range(1, tiers + 1):
        built = build_tier(unit, tier)
        basis.append(built.tier_id)
        expected = list(built.candidate_ids)
        # Only answers given to this exact version of the tier count.
        got = by_tier_id.get(built.tier_id, {})
        if any(got.get(nct) is None or not got[nct].final for nct in expected):
            return None
        yes = [nct for nct in expected if got[nct].final == YES]
        if yes:
            return Decision(unit_id, yes[0], tier, tier, tuple(basis), route_of(unit, tier))
    return Decision(unit_id, identification.NONE, 0, tiers, tuple(basis))


def next_tier(unit: dict[str, object], answers: Iterable[CandidateAnswer]) -> int | None:
    """The tier this description needs judged next, or None if decided."""
    unit_id = str(unit.get("unit_id") or "")
    final = {
        (answer.tier_id, answer.nct_id): answer.final
        for answer in answers
        if answer.unit_id == unit_id and answer.final
    }
    for tier in range(1, total_tiers(unit) + 1):
        built = build_tier(unit, tier)
        got = [final.get((built.tier_id, nct)) for nct in built.candidate_ids]
        if any(value is None for value in got):
            return tier
        if YES in got:
            return None
    return None


def write_tiers(tiers: Iterable[TierUnit], path: Path) -> int:
    """Write the tier units a round's judges read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for tier in tiers:
            handle.write(json.dumps(tier._asdict()) + "\n")
            written += 1
    return written


def read_tiers(path: Path) -> dict[str, TierUnit]:
    """Tier id → the tier as it was written for the judges."""
    out: dict[str, TierUnit] = {}
    for row in identification.iter_json_lines(path):
        raw_ids = row.get("candidate_ids")
        out[str(row.get("tier_id") or "")] = TierUnit(
            tier_id=str(row.get("tier_id") or ""),
            unit_id=str(row.get("unit_id") or ""),
            tier=int(str(row.get("tier") or 0)),
            approval=str(row.get("approval") or ""),
            excerpt=str(row.get("excerpt") or ""),
            trial_name=str(row.get("trial_name") or ""),
            enrollment=str(row.get("enrollment") or ""),
            candidate_ids=tuple(str(v) for v in raw_ids) if isinstance(raw_ids, list) else (),
            candidates=str(row.get("candidates") or ""),
            prompt=str(row.get("prompt") or EXCERPT_PROMPT),
            drug=str(row.get("drug") or ""),
            indication=str(row.get("indication") or ""),
            approval_date=str(row.get("approval_date") or ""),
            label_block=str(row.get("label_block") or ""),
            product=str(row.get("product") or ""),
        )
    return out


def read_judge_file(path: Path) -> dict[tuple[str, str], str]:
    """``(tier_id, nct_id) → Yes|No`` from one judge file; other lines skipped."""
    out: dict[tuple[str, str], str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ANSWER_LINE.match(line.strip())
        if match:
            out[(match["tier"].strip(), match["nct"])] = match["answer"]
    return out


class CombineResult(NamedTuple):
    """What one combine pass produced."""

    recorded: list[CandidateAnswer]
    tiebreak_needed: list[str]
    incomplete_tiers: list[str]
    agreement: tuple[int, int]


def combine(
    tiers: dict[str, TierUnit],
    judge_a: dict[tuple[str, str], str],
    judge_b: dict[tuple[str, str], str] | None,
    tiebreaks: dict[tuple[str, str], str],
) -> CombineResult:
    """Turn judge files into candidate answers, one complete tier at a time.

    A tier is recorded only when every candidate in it has a final answer: a
    tier is the unit the stop rule reads, and half of one says nothing. With
    ``judge_b`` None (the full run, D33) judge A's answer is final. With two
    judges, agreement is final and a disagreement needs the third judge.
    ``agreement`` counts (agreeing, compared) candidates for the report.
    """
    recorded: list[CandidateAnswer] = []
    tiebreak_needed: list[str] = []
    incomplete: list[str] = []
    agree = compared = 0
    for tier_id, tier in tiers.items():
        rows: list[CandidateAnswer] = []
        complete = True
        for rank_offset, nct in enumerate(tier.candidate_ids):
            a = judge_a.get((tier_id, nct))
            if a is None:
                complete = False
                continue
            b = judge_b.get((tier_id, nct)) if judge_b is not None else None
            if judge_b is not None and b is None:
                complete = False
                continue
            third = tiebreaks.get((tier_id, nct))
            if b is None:
                final: str | None = a
            else:
                compared += 1
                if a == b:
                    agree += 1
                    final = a
                else:
                    final = third
                    if third is None:
                        tiebreak_needed.append(f"{tier_id} | {nct}")
                        complete = False
            rows.append(
                CandidateAnswer(
                    tier_id=tier_id,
                    unit_id=tier.unit_id,
                    tier=tier.tier,
                    rank=(tier.tier - 1) * TIER_SIZE + rank_offset + 1,
                    nct_id=nct,
                    judge_a=a,
                    judge_b=b,
                    tiebreak=third if b is not None and a != b else None,
                    final=final or "",
                )
            )
        if complete:
            recorded.extend(rows)
        else:
            incomplete.append(tier_id)
    return CombineResult(recorded, tiebreak_needed, incomplete, (agree, compared))


def append_answers(
    answers: Iterable[CandidateAnswer], path: Path, prompts: dict[str, str] | None = None
) -> int:
    """Append candidate answers, stamped with the prompt and its version.

    ``prompts`` maps a tier id to the prompt it was judged with; a tier absent
    from it was judged with the excerpt prompt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    versions = PROMPT_VERSIONS
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for answer in answers:
            prompt = (prompts or {}).get(answer.tier_id, EXCERPT_PROMPT)
            handle.write(
                json.dumps({**answer._asdict(), "prompt": prompt,
                            "prompt_version": versions[prompt]}) + "\n"
            )
            written += 1
    return written


def record_decisions(
    decisions: Iterable[Decision],
    candidate_ids: dict[str, set[str]],
    path: Path,
    already: dict[str, tuple[str, ...]],
) -> tuple[int, int]:
    """Append decided descriptions to ``identify_verdicts.jsonl`` for P5.

    Each answer is checked against the description's own shortlist, as the
    pick-one route does. ``already`` maps each unit recorded under this
    version to the tiers its latest decision was read from: a re-run appends
    nothing twice, while a unit whose tiers changed and were judged again is
    recorded anew (last row wins for P5). Returns ``(written, refused)``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = refused = 0
    with path.open("a", encoding="utf-8") as handle:
        for decision in decisions:
            if already.get(decision.unit_id) == decision.basis:
                continue
            if not identification.is_allowed(
                decision.final, candidate_ids.get(decision.unit_id, set())
            ):
                refused += 1
                continue
            handle.write(
                json.dumps(
                    {
                        "unit_id": decision.unit_id,
                        "judge_a": decision.final,
                        "judge_b": "",
                        "tiebreak": None,
                        "final": decision.final,
                        "prompt_version": IDENTIFY_VERSION,
                        "method": "candidate_match",
                        "tier": decision.tier,
                        "tiers_judged": decision.tiers_judged,
                        "route": decision.route,
                        "basis": list(decision.basis),
                    }
                )
                + "\n"
            )
            written += 1
    return written, refused


#: Files this task keeps under the adjudication directory.
TIERS_FILE = "candidate_match_tiers.jsonl"
ANSWERS_FILE = "candidate_match_answers.jsonl"
BATCH_DIR = "candidate_match_batches"
RAW_DIR = "candidate_match_raw"
TIEBREAK_FILE = "candidate_match_tiebreak_needed.txt"

#: Tiers per judge batch file.
BATCH_TIERS: int = 10


def select_units(
    candidates_path: Path,
    approval_events: Path,
    drug_map: Path,
    limit: int | None,
    case: str = "B",
) -> list[dict[str, object]]:
    """The descriptions P4 judges: the pick-one route's set, in sample order.

    Same filters as :func:`identification.read_units` (earliest submission
    only, no label-stated NCT, no borrowed biosimilar label, no D24 guard), so
    both routes judge the same descriptions. ``limit`` takes a deterministic
    sample, so a pilot and the full run that follows it agree on membership.
    """
    if case != "B":
        # D35 cases C and D: one unit per submission, already routed by P3.
        wanted = set(case)
        units = [
            row for row in identification.iter_json_lines(candidates_path)
            if str(row.get("case") or "B") in wanted
            and (row.get("candidates") or row.get("literature"))
        ]
    else:
        units = identification.read_units(
            candidates_path,
            identification.borrowed_ids(drug_map),
            identification.guard_submissions(approval_events),
        )
    ordered = sample_order(units, limit)
    attach_abstracts(ordered, candidates_path.parent)
    attach_products(ordered, candidates_path.parent)
    return ordered


#: ORIG submission classes that make a product a new version of a molecule
#: an earlier application already brought to market (2026-10-03).
NEW_VERSION_CLASSES: dict[str, str] = {
    "TYPE 3": "a new dosage form",
    "TYPE 5": "a new formulation or a new manufacturer's version",
}
#: The product line when it is not such a version.
NOT_A_COPY: str = ("not a new form of an earlier-approved drug, as far as Drugs@FDA's "
                   "records show.")


def attach_products(units: list[dict[str, object]], out_dir: Path) -> None:
    """Set ``copy_of`` on each unit whose application is a new dosage form or
    formulation (ORIG class ``TYPE 3``/``TYPE 5``) of a molecule an earlier
    application of the same moiety group already brought to market: the text
    the C/D judges read, e.g. "a new dosage form of a drug first approved on
    1991-06-12 as CARDIZEM (NDA020027)." Empty otherwise (2026-10-03).
    """
    events = out_dir / "approval_events.tsv"
    if not events.is_file():
        return
    groups = drug_names.moiety_groups(out_dir / "drug_names.tsv")
    brand: dict[str, str] = {}
    names = out_dir / "drug_names.tsv"
    if names.is_file():
        with names.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                brand[row["application"]] = (row.get("brand_names") or "").split(";")[0].strip()
    orig: dict[str, tuple[str, str]] = {}
    with events.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["submission_type"] == "ORIG" and row.get("approval_date"):
                orig[row["application"]] = (row.get("class_code") or "", row["approval_date"])
    first: dict[str, tuple[str, str]] = {}
    for application, (_, approved) in orig.items():
        group = groups.get(application, application)
        if group not in first or approved < first[group][0]:
            first[group] = (approved, application)
    for unit in units:
        application = str(unit.get("application") or "")
        code, approved = orig.get(application, ("", ""))
        earliest, original = first.get(groups.get(application, application), ("", application))
        if code in NEW_VERSION_CLASSES and original != application and earliest < approved:
            shown = brand.get(original) or "another product"
            unit["copy_of"] = (f"{NEW_VERSION_CLASSES[code]} of a drug first approved on "
                               f"{earliest} as {shown} ({original}).")
        else:
            unit["copy_of"] = ""


def attach_abstracts(units: list[dict[str, object]], out_dir: Path) -> None:
    """Give each literature candidate the abstracts its PMIDs name (D37).

    Read from S7's abstracts files beside ``candidates.jsonl``. A PMID with no
    record is left out; the judge sees how many were shown.
    """
    papers = graph_trials.read_abstracts(
        out_dir / graph_trials.ABSTRACTS_FILE,
        out_dir / graph_links.DRUG_ONLY_ABSTRACTS_FILE,
    )
    for unit in units:
        for candidate in _listed(unit, "literature"):
            pmids = [p for p in str(candidate.get("pmids") or "").split(";") if p]
            candidate["abstracts"] = [papers[p] for p in pmids if p in papers]


def recorded_bases(path: Path) -> dict[str, tuple[str, ...]]:
    """Unit → the tiers its latest decision at :data:`IDENTIFY_VERSION` was read from.

    A decision recorded before tiers carried digests has an empty basis, so
    it is superseded by the first decision reached on digest-keyed tiers.
    """
    out: dict[str, tuple[str, ...]] = {}
    for row in identification.iter_json_lines(path):
        if row.get("prompt_version") != IDENTIFY_VERSION:
            continue
        raw = row.get("basis")
        out[str(row.get("unit_id") or "")] = (
            tuple(str(v) for v in raw) if isinstance(raw, list) else ()
        )
    return out


def migrate_legacy_ids(adjudication: Path) -> int:
    """Give tiers written before digests existed their digest-keyed id.

    A legacy tier (id without ``@``) is re-keyed from its own stored content —
    exactly what its judges were shown — and each of its recorded answers is
    appended again under the new id, marked ``migrated_from``. Answers are
    never rewritten. Idempotent: a tier already migrated is skipped. Returns
    the number of answers carried over.
    """
    tiers_path = adjudication / TIERS_FILE
    answers_path = adjudication / ANSWERS_FILE
    known = read_tiers(tiers_path)
    legacy = {tid: t for tid, t in known.items() if tid and "@" not in tid}
    if not legacy:
        return 0
    renamed = {
        tid: tier._replace(tier_id=tier_id_for(tier.unit_id, tier.tier, input_digest(tier)))
        for tid, tier in legacy.items()
    }
    rows = list(identification.iter_json_lines(answers_path))
    have = {(str(r.get("tier_id")), str(r.get("nct_id"))) for r in rows}
    carried = 0
    answers_path.parent.mkdir(parents=True, exist_ok=True)
    with answers_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            old = str(row.get("tier_id") or "")
            if old not in renamed:
                continue
            new = renamed[old].tier_id
            if (new, str(row.get("nct_id"))) in have:
                continue
            handle.write(json.dumps({**row, "tier_id": new, "migrated_from": old}) + "\n")
            have.add((new, str(row.get("nct_id"))))
            carried += 1
    kept = {tid: t for tid, t in known.items() if tid not in legacy}
    for tier in renamed.values():
        kept[tier.tier_id] = tier
    write_tiers(kept.values(), tiers_path)
    return carried


def prepare_round(units: list[dict[str, object]], adjudication: Path) -> str:
    """Write every undecided description's next tier and the judge batches.

    Only each description's *current* next tier is batched. A tier written
    earlier whose content has since changed keeps its old id in the tiers
    file, as the record of what was judged, but is never batched again.
    """
    migrate_legacy_ids(adjudication)
    answers = read_answers(adjudication / ANSWERS_FILE)
    known = read_tiers(adjudication / TIERS_FILE)
    fresh: list[TierUnit] = []
    current: list[TierUnit] = []
    decided = 0
    for unit in units:
        tier = next_tier(unit, answers)
        if tier is None:
            decided += 1
            continue
        built = build_tier(unit, tier)
        if built.tier_id not in known:
            fresh.append(built)
        known[built.tier_id] = built
        current.append(built)
    write_tiers(known.values(), adjudication / TIERS_FILE)
    pending_ids = [
        tier.tier_id for tier in current if not _tier_recorded(tier, answers)
    ]
    batch_root = adjudication / BATCH_DIR
    round_no = len(list(batch_root.glob("round-*"))) + 1
    round_dir = batch_root / f"round-{round_no:02d}"
    written = 0
    if pending_ids:
        round_dir.mkdir(parents=True, exist_ok=True)
        # One prompt per batch file, so each judge is given exactly one prompt:
        # ``batch-NNN`` for §14 descriptions, ``nolabel-NNN`` for cases C/D.
        for prefix, prompt in (("batch", EXCERPT_PROMPT), ("nolabel", NOLABEL_PROMPT),
                               ("literature", LITERATURE_PROMPT)):
            ids = [t for t in pending_ids if known[t].prompt == prompt]
            for number, chunk in enumerate(batches(ids, BATCH_TIERS), 1):
                (round_dir / f"{prefix}-{number:03d}.ids").write_text(
                    "\n".join(chunk) + "\n", encoding="utf-8"
                )
                written += 1
    return (
        "P4 per-candidate matching (D33) — prepare\n\n"
        f"  descriptions in scope                  {len(units):>9,}\n"
        f"    decided                              {decided:>9,}\n"
        f"    needing a tier                       {len(units) - decided:>9,}\n"
        f"  tiers written this round (new)         {len(fresh):>9,}\n"
        f"  tiers awaiting answers                 {len(pending_ids):>9,}\n"
        f"  batch files                            {written:>9,}"
        + (f"  -> {round_dir}" if written else "")
    )


def _tier_recorded(tier: TierUnit, answers: list[CandidateAnswer]) -> bool:
    done = {(a.tier_id, a.nct_id) for a in answers if a.final}
    return all((tier.tier_id, nct) in done for nct in tier.candidate_ids)


def _merge(paths: Iterable[Path]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for path in sorted(paths):
        out.update(read_judge_file(path))
    return out


def combine_round(adjudication: Path, judges: int) -> str:
    """Record every tier whose answers are complete; list tiebreaks needed."""
    migrate_legacy_ids(adjudication)
    raw = adjudication / RAW_DIR
    tiers = read_tiers(adjudication / TIERS_FILE)
    existing = read_answers(adjudication / ANSWERS_FILE)
    done = {(a.tier_id, a.nct_id) for a in existing if a.final}
    judge_a = _merge(raw.glob("**/*-A.txt"))
    judge_b = _merge(raw.glob("**/*-B.txt")) if judges == 2 else None
    third = _merge(raw.glob("**/*-C.txt"))
    open_tiers = {
        tier_id: tier
        for tier_id, tier in tiers.items()
        if not all((tier_id, nct) in done for nct in tier.candidate_ids)
    }
    result = combine(open_tiers, judge_a, judge_b, third)
    written = append_answers(result.recorded, adjudication / ANSWERS_FILE,
                             {tier_id: tier.prompt for tier_id, tier in tiers.items()})
    (adjudication / TIEBREAK_FILE).write_text(
        "".join(line + "\n" for line in result.tiebreak_needed), encoding="utf-8"
    )
    agree, compared = result.agreement
    rate = f"{agree / compared:.1%}" if compared else "n/a"
    return (
        f"P4 per-candidate matching (D33) — combine, {judges} judge(s)\n\n"
        f"  candidate answers recorded             {written:>9,}\n"
        f"  tiers still incomplete                 {len(result.incomplete_tiers):>9,}\n"
        f"  candidates needing a third judge       {len(result.tiebreak_needed):>9,}"
        + (f"  -> {adjudication / TIEBREAK_FILE}" if result.tiebreak_needed else "")
        + f"\n  judge agreement (per candidate)        {agree:>6,}/{compared:,}  {rate}"
    )


def record_round(units: list[dict[str, object]], adjudication: Path) -> str:
    """Append every newly decided description to ``identify_verdicts.jsonl``."""
    verdicts_path = adjudication / "identify_verdicts.jsonl"
    migrate_legacy_ids(adjudication)
    answers = read_answers(adjudication / ANSWERS_FILE)
    already = recorded_bases(verdicts_path)
    decisions = [d for d in (decide(u, answers) for u in units) if d is not None]
    allowed: dict[str, set[str]] = {}
    for unit in units:
        allowed[str(unit.get("unit_id") or "")] = {
            str(c["nct_id"]) for c in _listed(unit, "candidates") + _listed(unit, "literature")
        }
    written, refused = record_decisions(decisions, allowed, verdicts_path, already)
    identified = [d for d in decisions if d.final != identification.NONE]
    by_tier: dict[int, int] = {}
    for decision in identified:
        by_tier[decision.tier] = by_tier.get(decision.tier, 0) + 1
    tiers_line = ", ".join(f"T{k}: {v}" for k, v in sorted(by_tier.items())) or "-"
    return (
        "P4 per-candidate matching (D33) — record\n\n"
        f"  descriptions in scope                  {len(units):>9,}\n"
        f"    decided                              {len(decisions):>9,}\n"
        f"      identified a trial                 {len(identified):>9,}  ({tiers_line})\n"
        f"      NONE                               {len(decisions) - len(identified):>9,}\n"
        f"    still judging                        {len(units) - len(decisions):>9,}\n"
        f"  newly written to identify_verdicts     {written:>9,}\n"
        f"  refused (answer not on its shortlist)  {refused:>9,}"
    )


def batches(ids: list[str], size: int) -> Iterator[list[str]]:
    """Split tier ids into judge batches.

    >>> list(batches(["a", "b", "c"], 2))
    [['a', 'b'], ['c']]
    """
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


__all__ = [
    "ANSWER_LINE",
    "CandidateAnswer",
    "CombineResult",
    "Decision",
    "CURRENT_PROMPTS",
    "EXCERPT_PROMPT",
    "LITERATURE_PROMPT",
    "LITERATURE_PROMPT_VERSION",
    "PROMPT_VERSIONS",
    "attach_abstracts",
    "attach_products",
    "label_block",
    "literature_tiers",
    "route_of",
    "total_tiers",
    "IDENTIFY_VERSION",
    "NOLABEL_PROMPT",
    "NOLABEL_PROMPT_VERSION",
    "NO",
    "PROMPT_VERSION",
    "TIER_SIZE",
    "TierUnit",
    "YES",
    "append_answers",
    "approval_line",
    "batches",
    "build_tier",
    "combine",
    "combine_round",
    "decide",
    "input_digest",
    "migrate_legacy_ids",
    "recorded_bases",
    "prepare_round",
    "record_round",
    "select_units",
    "next_tier",
    "read_answers",
    "read_judge_file",
    "read_tiers",
    "record_decisions",
    "render_candidate",
    "sample_order",
    "tier_count",
    "tier_id_for",
    "write_tiers",
]
