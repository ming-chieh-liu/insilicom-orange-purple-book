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

from service import identification
from utility.logging import get_logger

_log = get_logger(__name__)

#: Bumped whenever ``docs/prompts/trial-candidate-match.md`` changes, and stamped
#: on every row this module writes.
PROMPT_VERSION: int = 1

#: What a decided description is stamped with in ``identify_verdicts.jsonl``,
#: which P5 reads. Above the pick-one prompt's version 1, so a D33 decision
#: supersedes an older answer for the same unit (last row wins).
IDENTIFY_VERSION: int = 2

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


def tier_id_for(unit_id: str, tier: int) -> str:
    """The key one tier is recorded against.

    >>> tier_id_for("BLA125514|SUPPL|35|KEYNOTE-189", 2)
    'BLA125514|SUPPL|35|KEYNOTE-189#T2'
    """
    return f"{unit_id}#T{tier}"


def tier_count(candidates: int) -> int:
    """How many tiers a shortlist of this length has.

    >>> tier_count(62), tier_count(10), tier_count(0)
    (7, 1, 0)
    """
    return math.ceil(candidates / TIER_SIZE)


def render_candidate(index: int, candidate: dict[str, object]) -> str:
    """One candidate block, fields in the order the prompt lists them."""

    def field(key: str, width: int = 300) -> str:
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
            f"   Interventions: {field('interventions')}",
            f"   Enrolled: {field('enrollment')}",
            f"   Start date: {field('start_date')}",
            f"   Primary completion: {field('primary_completion')}",
        ]
    )


def approval_line(unit: dict[str, object]) -> str:
    """The APPROVAL field.

    >>> approval_line({"application": "BLA125514", "submission_type": "SUPPL",
    ...                "submission_no": "35", "approval_date": "2017-05-10",
    ...                "heading": "Non-Small Cell Lung Cancer"})
    'BLA125514 SUPPL 35 · approved 2017-05-10 · section 14 heading: Non-Small Cell Lung Cancer'
    """
    return (
        f"{unit.get('application') or ''} {unit.get('submission_type') or ''} "
        f"{unit.get('submission_no') or ''} · approved {unit.get('approval_date') or 'unknown'}"
        f" · section 14 heading: {unit.get('heading') or 'none'}"
    )


def build_tier(unit: dict[str, object], tier: int) -> TierUnit:
    """Tier ``tier`` (1-based) of a description."""
    raw = unit.get("candidates")
    candidates: list[dict[str, object]] = raw if isinstance(raw, list) else []
    start = (tier - 1) * TIER_SIZE
    chosen = candidates[start : start + TIER_SIZE]
    unit_id = str(unit.get("unit_id") or "")
    return TierUnit(
        tier_id=tier_id_for(unit_id, tier),
        unit_id=unit_id,
        tier=tier,
        approval=approval_line(unit),
        excerpt=str(unit.get("excerpt") or ""),
        trial_name=str(unit.get("acronym") or "(the label gives no name)"),
        enrollment=str(unit.get("enrollment") or "(the label gives no count)"),
        candidate_ids=tuple(str(c["nct_id"]) for c in chosen),
        candidates="\n".join(
            render_candidate(start + offset + 1, candidate)
            for offset, candidate in enumerate(chosen)
        ),
    )


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
        if row.get("prompt_version") != PROMPT_VERSION:
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
    has a final answer. Every tier No means NONE.
    """
    raw = unit.get("candidates")
    candidates: list[dict[str, object]] = raw if isinstance(raw, list) else []
    unit_id = str(unit.get("unit_id") or "")
    by_tier: dict[int, dict[str, CandidateAnswer]] = {}
    for answer in answers:
        if answer.unit_id == unit_id:
            by_tier.setdefault(answer.tier, {})[answer.nct_id] = answer
    tiers = tier_count(len(candidates))
    for tier in range(1, tiers + 1):
        expected = [
            str(c["nct_id"])
            for c in candidates[(tier - 1) * TIER_SIZE : tier * TIER_SIZE]
        ]
        got = by_tier.get(tier, {})
        if any(got.get(nct) is None or not got[nct].final for nct in expected):
            return None
        yes = [nct for nct in expected if got[nct].final == YES]
        if yes:
            return Decision(unit_id, yes[0], tier, tier)
    return Decision(unit_id, identification.NONE, 0, tiers)


def next_tier(unit: dict[str, object], answers: Iterable[CandidateAnswer]) -> int | None:
    """The tier this description needs judged next, or None if decided."""
    raw = unit.get("candidates")
    candidates: list[dict[str, object]] = raw if isinstance(raw, list) else []
    unit_id = str(unit.get("unit_id") or "")
    final = {
        (answer.tier, answer.nct_id): answer.final
        for answer in answers
        if answer.unit_id == unit_id and answer.final
    }
    for tier in range(1, tier_count(len(candidates)) + 1):
        expected = [
            str(c["nct_id"])
            for c in candidates[(tier - 1) * TIER_SIZE : tier * TIER_SIZE]
        ]
        got = [final.get((tier, nct)) for nct in expected]
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


def append_answers(answers: Iterable[CandidateAnswer], path: Path) -> int:
    """Append candidate answers, stamped with the prompt version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for answer in answers:
            handle.write(
                json.dumps({**answer._asdict(), "prompt_version": PROMPT_VERSION}) + "\n"
            )
            written += 1
    return written


def record_decisions(
    decisions: Iterable[Decision],
    candidate_ids: dict[str, set[str]],
    path: Path,
    already: set[str],
) -> tuple[int, int]:
    """Append decided descriptions to ``identify_verdicts.jsonl`` for P5.

    Each answer is checked against the description's own shortlist, as the
    pick-one route does. ``already`` holds units recorded under this version,
    so a re-run appends nothing twice. Returns ``(written, refused)``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = refused = 0
    with path.open("a", encoding="utf-8") as handle:
        for decision in decisions:
            if decision.unit_id in already:
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
) -> list[dict[str, object]]:
    """The descriptions P4 judges: the pick-one route's set, in sample order.

    Same filters as :func:`identification.read_units` (earliest submission
    only, no label-stated NCT, no borrowed biosimilar label, no D24 guard), so
    both routes judge the same descriptions. ``limit`` takes a deterministic
    sample, so a pilot and the full run that follows it agree on membership.
    """
    units = identification.read_units(
        candidates_path,
        identification.borrowed_ids(drug_map),
        identification.guard_submissions(approval_events),
    )
    return sample_order(units, limit)


def prepare_round(units: list[dict[str, object]], adjudication: Path) -> str:
    """Write every undecided description's next tier and the judge batches."""
    answers = read_answers(adjudication / ANSWERS_FILE)
    known = read_tiers(adjudication / TIERS_FILE)
    fresh: list[TierUnit] = []
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
    write_tiers(known.values(), adjudication / TIERS_FILE)
    pending_ids = [
        tier.tier_id
        for tier in known.values()
        if not _tier_recorded(tier, answers)
    ]
    batch_root = adjudication / BATCH_DIR
    round_no = len(list(batch_root.glob("round-*"))) + 1
    round_dir = batch_root / f"round-{round_no:02d}"
    written = 0
    if pending_ids:
        round_dir.mkdir(parents=True, exist_ok=True)
        for number, chunk in enumerate(batches(pending_ids, BATCH_TIERS), 1):
            (round_dir / f"batch-{number:03d}.ids").write_text(
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
    written = append_answers(result.recorded, adjudication / ANSWERS_FILE)
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
    answers = read_answers(adjudication / ANSWERS_FILE)
    verdicts_path = adjudication / "identify_verdicts.jsonl"
    already = {
        row_id
        for row_id, verdict in identification.read_verdicts(verdicts_path).items()
        if verdict.prompt_version == IDENTIFY_VERSION
    }
    decisions = [d for d in (decide(u, answers) for u in units) if d is not None]
    allowed: dict[str, set[str]] = {}
    for unit in units:
        raw = unit.get("candidates")
        listed = raw if isinstance(raw, list) else []
        allowed[str(unit.get("unit_id") or "")] = {
            str(c["nct_id"]) for c in listed if isinstance(c, dict)
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
    "IDENTIFY_VERSION",
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
