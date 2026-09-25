"""P4 judge precision, measured per evidence group on withheld answers.

The question: given what P4 shows a judge — one §14 sentence, the label's trial
name and patient count, and a shortlist of up to ~100 registry lines — does the
judge pick the right trial? Measured on units whose label states its own NCT
id, with that id withheld and absent from the excerpt, so the answer is known
and the judge cannot read it off.

**Groups are assigned without the answer**, from what the judge itself could
see, so the same rule can later route production units:

``name_unique``
    The label's trial name matches exactly one candidate's acronym, protocol id
    or title (:func:`service.candidates.names_match`).
``count_exact_unique``
    Not the above; the label's count equals exactly one candidate's enrollment.
``count_approximate``
    Neither; the label states a count, but no candidate or several match it
    exactly.
``weak``
    No usable name match and no count.

A fifth group, ``answer_absent``, samples units whose true trial is **not** in
the shortlist: the only correct answer is NONE, and any pick is a fabricated
edge. Real P4 units are in this situation whenever P3 missed (D18), so a
precision figure without it overstates what the deliverable gets.

Everything lives under one experiment folder, never in
``identify_verdicts.jsonl``: these unit ids are real units, and a verdict
judged with the answer withheld must not become a production link.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from service import identification
from service.candidates import GENERIC_NAME, Candidate, names_match
from service.judging import write_batches
from service.section14 import NCT

NAME_UNIQUE = "name_unique"
COUNT_EXACT_UNIQUE = "count_exact_unique"
COUNT_APPROXIMATE = "count_approximate"
WEAK = "weak"
ANSWER_ABSENT = "answer_absent"
GROUPS: tuple[str, ...] = (NAME_UNIQUE, COUNT_EXACT_UNIQUE, COUNT_APPROXIMATE, WEAK,
                           ANSWER_ABSENT)

STEP = "prec"

#: Ids per judge batch — what the production P4 run uses, so the measurement
#: reflects the load a production judge carries.
BATCH_SIZE: int = 20


class Scored(NamedTuple):
    """Per-group outcome."""

    group: str
    decided: int
    correct: int
    wrong: int
    abstained: int

    @property
    def precision(self) -> float:
        """Correct share of the answers that named a trial.

        >>> Scored("g", 10, 8, 2, 0).precision
        0.8
        >>> Scored("g", 3, 0, 0, 3).precision
        0.0
        """
        named = self.correct + self.wrong
        return self.correct / named if named else 0.0


def _candidate(raw: dict[str, object]) -> Candidate:
    def text(field: str) -> str:
        return str(raw.get(field) or "")
    return Candidate(text("nct_id"), text("brief_title"), text("acronym"),
                     text("org_study_id"), text("phases"), text("enrollment"),
                     text("start_date"), text("conditions"), text("interventions"))


def group_of(unit: dict[str, object]) -> str:
    """The evidence group, from what a judge sees — never from the answer.

    >>> c = [{"nct_id": "N1", "acronym": "KEYNOTE-189", "enrollment": "616"},
    ...      {"nct_id": "N2", "acronym": "", "enrollment": "616"},
    ...      {"nct_id": "N3", "acronym": "", "enrollment": "240"}]
    >>> group_of({"acronym": "KEYNOTE-189", "enrollment": "", "candidates": c})
    'name_unique'
    >>> group_of({"acronym": "", "enrollment": "240", "candidates": c})
    'count_exact_unique'
    >>> group_of({"acronym": "", "enrollment": "616", "candidates": c})
    'count_approximate'
    >>> group_of({"acronym": "STUDY 1", "enrollment": "", "candidates": c})
    'weak'
    """
    raw = unit.get("candidates")
    candidates = [_candidate(c) for c in raw if isinstance(c, dict)] if isinstance(raw, list) \
        else []
    name = str(unit.get("acronym") or "")
    if name and not GENERIC_NAME.match(name.strip()):
        if sum(1 for c in candidates if names_match(name, c)) == 1:
            return NAME_UNIQUE
    count = str(unit.get("enrollment") or "").replace(",", "")
    if count.isdigit():
        exact = sum(1 for c in candidates if c.enrollment == count)
        return COUNT_EXACT_UNIQUE if exact == 1 else COUNT_APPROXIMATE
    return WEAK


def _order(unit_id: str) -> str:
    return hashlib.sha256(unit_id.encode()).hexdigest()


#: Most units one application contributes to a group. Without it the sample is
#: pembrolizumab and nivolumab — they carry the most label-stated ids — and a
#: famous trial is exactly where a judge's memory could stand in for reading.
PER_APPLICATION: int = 2


def _known_answer_units(units_path: Path) -> list[tuple[str, dict[str, object]]]:
    """``(group, unit)`` for every leak-free known-answer unit, **earliest only**.

    Production judges a trial once per application, at its earliest submission
    (D16); later appearances never reach a judge, so they are not sampled.
    """
    earliest: dict[tuple[str, str], dict[str, object]] = {}
    for unit in identification.iter_json_lines(units_path):
        stated = str(unit.get("stated_nct") or "")
        raw = unit.get("candidates")
        if not stated or not isinstance(raw, list) or not raw:
            continue
        if NCT.search(str(unit.get("excerpt") or "")):
            continue
        key = (str(unit.get("application") or ""), stated)
        held = earliest.get(key)
        if held is None or str(unit.get("approval_date") or "") < str(
                held.get("approval_date") or ""):
            earliest[key] = unit
    out: list[tuple[str, dict[str, object]]] = []
    for unit in earliest.values():
        raw = unit["candidates"]
        ids = {str(c.get("nct_id") or "") for c in raw if isinstance(c, dict)} \
            if isinstance(raw, list) else set()
        out.append((group_of(unit) if unit["stated_nct"] in ids else ANSWER_ABSENT, unit))
    return out


def sample(units_path: Path, per_group: int) -> tuple[list[dict[str, object]],
                                                       dict[str, dict[str, str]]]:
    """Up to ``per_group`` leak-free known-answer units per group.

    Returns ``(units with the answer removed, {unit_id: {truth, group}})``;
    ``truth`` is ``NONE`` for :data:`ANSWER_ABSENT`. Order is a hash of the unit
    id — spread across applications and eras, and reproducible.
    """
    pools: dict[str, list[dict[str, object]]] = {g: [] for g in GROUPS}
    for group, unit in _known_answer_units(units_path):
        pools[group].append(unit)
    chosen: list[dict[str, object]] = []
    truth: dict[str, dict[str, str]] = {}
    for group, pool in pools.items():
        pool.sort(key=lambda u: _order(str(u.get("unit_id") or "")))
        per_app: Counter[str] = Counter()
        picked: list[dict[str, object]] = []
        for unit in pool:
            application = str(unit.get("application") or "")
            if per_app[application] >= PER_APPLICATION:
                continue
            per_app[application] += 1
            picked.append(unit)
            if len(picked) == per_group:
                break
        for unit in picked:
            unit_id = str(unit["unit_id"])
            answer = str(unit["stated_nct"])
            truth[unit_id] = {"truth": identification.NONE if group == ANSWER_ABSENT
                              else answer, "group": group}
            chosen.append({**unit, "stated_nct": ""})
    return chosen, truth


def eligible_counts(units_path: Path) -> Counter[str]:
    """How many leak-free known-answer units (earliest only) each group has."""
    return Counter(group for group, _ in _known_answer_units(units_path))


def prepare(units_path: Path, folder: Path, per_group: int) -> tuple[int, list[Path]]:
    """Write the experiment's units, truth and judge batches.

    Refuses to overwrite an experiment already prepared: the truth file is the
    measurement's key, and rebuilding it mid-run would score answers against a
    different sample.
    """
    truth_path = folder / "truth.json"
    if truth_path.exists():
        raise FileExistsError(f"{truth_path} exists; this experiment is already prepared")
    units, truth = sample(units_path, per_group)
    identification.write_units(units, folder / "units.jsonl")
    truth_path.write_text(json.dumps(truth, indent=1, sort_keys=True), encoding="utf-8")
    ids = sorted(truth, key=_order)
    return len(units), write_batches(folder / "batches", STEP, ids, size=BATCH_SIZE)


def combine(folder: Path, decided_on: str) -> tuple[int, list[str], list[str]]:
    """Pair A and B per batch, apply tiebreaks, append decided units.

    Returns ``(newly recorded, units needing a tiebreak, units with an answer
    missing)``. Answers not on the unit's own candidate list are refused, as in
    production. Nothing is recorded that a judge did not return.
    """
    raw = folder / "raw"
    allowed = identification.candidate_ids_by_unit(folder / "units.jsonl")
    tiebreaks: dict[str, str] = {}
    for path in sorted(raw.glob(f"{STEP}-tiebreak-batch-*-C.txt")):
        tiebreaks.update(identification.read_judge_file(path))
    verdicts_path = folder / "verdicts.jsonl"
    done = set(identification.read_verdicts(verdicts_path))
    rows: list[dict[str, object]] = []
    pending: list[str] = []
    missing: list[str] = []
    for a_path in sorted(raw.glob(f"{STEP}-batch-*-A.txt")):
        b_path = a_path.with_name(a_path.name.replace("-A.txt", "-B.txt"))
        if not b_path.exists():
            continue
        agreed, needs, absent = identification.combine(
            identification.read_judge_file(a_path),
            identification.read_judge_file(b_path),
            tiebreaks,
        )
        missing.extend(absent)
        for row in agreed:
            unit_id = str(row["unit_id"])
            if unit_id in done or not identification.is_allowed(
                str(row["final"]), allowed.get(unit_id, set())
            ):
                continue
            rows.append({**row, "prompt_version": identification.PROMPT_VERSION,
                         "decided": decided_on})
        pending.extend(u for u in needs if u not in done)
    if rows:
        with verdicts_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    batched = set()
    for path in (folder / "batches").glob(f"{STEP}-tiebreak-batch-*.ids"):
        batched.update(path.read_text(encoding="utf-8").split())
    write_batches(folder / "batches", f"{STEP}-tiebreak",
                  [u for u in pending if u not in batched and u not in tiebreaks])
    return len(rows), pending, missing


def score(folder: Path) -> tuple[Scored, ...]:
    """Correct / wrong / abstained per group, over decided units."""
    truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    verdicts = identification.read_verdicts(folder / "verdicts.jsonl")
    tally: dict[str, list[int]] = {g: [0, 0, 0, 0] for g in GROUPS}
    for unit_id, row in truth.items():
        verdict = verdicts.get(unit_id)
        if verdict is None:
            continue
        counts = tally[row["group"]]
        counts[0] += 1
        if verdict.final == identification.NONE:
            if row["truth"] == identification.NONE:
                counts[1] += 1  # abstaining is the correct answer here
            else:
                counts[3] += 1
        elif verdict.final == row["truth"]:
            counts[1] += 1
        else:
            counts[2] += 1
    return tuple(Scored(g, *tally[g]) for g in GROUPS)


def render(scored: tuple[Scored, ...], eligible: Counter[str] | None = None) -> str:
    """The per-group table."""
    lines = ["P4 judge precision by evidence group (withheld answers, prompt v"
             f"{identification.PROMPT_VERSION})", "",
             f"  {'group':<20}{'pool':>7}{'decided':>9}{'correct':>9}{'wrong':>7}"
             f"{'NONE':>7}{'precision':>11}"]
    for row in scored:
        pool = f"{eligible[row.group]:,}" if eligible is not None else "-"
        lines.append(f"  {row.group:<20}{pool:>7}{row.decided:>9}{row.correct:>9}"
                     f"{row.wrong:>7}{row.abstained:>7}{100 * row.precision:>10.1f}%")
    lines += ["", "  answer_absent: correct = NONE; any named trial is wrong."]
    return "\n".join(lines)


__all__ = [
    "GROUPS",
    "Scored",
    "combine",
    "eligible_counts",
    "group_of",
    "prepare",
    "render",
    "sample",
    "score",
]
