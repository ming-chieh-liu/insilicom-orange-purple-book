"""Score the map against links the FDA stated outright.

The project has a gold standard it did not have to build: 5,596 units where the
label prints its own NCT id. Those are not judgements — the FDA wrote the
number — so they can be hidden from a judge and used to ask the only question
that matters about P4. *Given a description whose answer we already know, and a
shortlist we generated the usual way, does the judge find it?*

Two numbers come out, and they answer different questions.

**Recall of the shortlist** is P3's score: how often the true trial is among the
candidates at all. A judge cannot find what was never offered, and this is the
ceiling on everything downstream. It is measured without spending a single
agent call, by checking whether the known id survives the drug, date and
ranking filters.

**Precision of the judge** is P4's score, and needs the judge to actually run
on units whose answers are withheld.

Keeping them apart matters because they fail differently and are fixed
differently. CHECKMATE-648 was a shortlist failure that looked exactly like a
judging failure until the shortlist was checked.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import NamedTuple

from service import identification
from service.section14 import NCT
from utility.logging import get_logger

_log = get_logger(__name__)


class Recall(NamedTuple):
    """How often the shortlist contains an answer we already know."""

    checked: int
    present: int
    absent_examples: tuple[str, ...]

    @property
    def rate(self) -> float:
        """The ceiling on everything P4 can do.

        >>> Recall(100, 75, ()).rate
        0.75
        >>> Recall(0, 0, ()).rate
        0.0
        """
        return self.present / self.checked if self.checked else 0.0


class Scored(NamedTuple):
    """One withheld-answer unit and what the judge said about it."""

    unit_id: str
    truth: str
    answered: str

    @property
    def correct(self) -> bool:
        return self.answered == self.truth


class Precision(NamedTuple):
    """How often the judge is right when it commits to an answer."""

    scored: int
    correct: int
    wrong: int
    abstained: int

    @property
    def rate(self) -> float:
        """Correct answers as a share of the answers actually given.

        Abstentions are excluded: NONE is not a wrong answer, it is the absence
        of one, and counting it as an error would push the judge toward guessing
        — which is the failure this whole task is built to avoid.

        >>> Precision(10, 6, 2, 2).rate
        0.75
        >>> Precision(0, 0, 0, 0).rate
        0.0
        """
        committed = self.correct + self.wrong
        return self.correct / committed if committed else 0.0

    @property
    def abstention_rate(self) -> float:
        """Share of units the judge declined.

        >>> Precision(10, 6, 2, 2).abstention_rate
        0.2
        """
        return self.abstained / self.scored if self.scored else 0.0


def shortlist_recall(units_path: Path, limit: int | None = None) -> Recall:
    """Of the units whose label states an NCT id, how many shortlist it?

    Costs nothing: the answer is in the unit, and so is the candidate list.
    """
    checked = present = 0
    missing: list[str] = []
    for unit in identification.iter_json_lines(units_path):
        truth = str(unit.get("stated_nct") or "")
        raw = unit.get("candidates")
        if not truth or not isinstance(raw, list) or not raw:
            continue
        checked += 1
        ids = {str(c.get("nct_id") or "") for c in raw if isinstance(c, dict)}
        if truth in ids:
            present += 1
        elif len(missing) < 20:
            missing.append(f"{unit.get('unit_id')} -> {truth}")
        if limit is not None and checked >= limit:
            break
    _log.info("shortlist recall: %d of %d", present, checked)
    return Recall(checked=checked, present=present, absent_examples=tuple(missing))


def withhold(units_path: Path, out_path: Path, limit: int) -> dict[str, str]:
    """Write judging units from known-answer cases, with the answer removed.

    What is withheld is the ``stated_nct`` field the pipeline would otherwise
    short-circuit on, so the unit reaches a judge at all.

    **A unit whose excerpt still prints an NCT id is not usable here**, and
    that is most of them: 2,192 of the 3,352 eligible units, 65.4%. They exist
    because :func:`service.candidates.collapse` merges a label's sentences —
    one sentence supplies the id and a different, more informative one is kept
    as the excerpt — so the unit knows the answer while the excerpt still shows
    it somewhere. A judge would read it off, and the score would measure
    copying an identifier rather than identifying a trial from its description.
    That is not the task P4 performs: a real P4 unit has no id in its prose,
    which is precisely why it needs a judge.

    The remaining 1,160 are the honest gold set — the label named the trial in
    prose only, and the answer is known from a sibling sentence the judge never
    sees.

    Selection is **spread deterministically** rather than taken from the head
    of the file. ``candidates.jsonl`` is in application order, so the first N
    eligible units are all low application numbers — one sponsor, one era, one
    therapeutic area. Hashing the unit id gives a stable, arbitrary order that
    is reproducible across runs without importing a seed.
    """
    eligible: list[dict[str, object]] = []
    for unit in identification.iter_json_lines(units_path):
        stated = str(unit.get("stated_nct") or "")
        raw = unit.get("candidates")
        if not stated or not isinstance(raw, list) or not raw:
            continue
        ids = {str(c.get("nct_id") or "") for c in raw if isinstance(c, dict)}
        if stated not in ids:
            # Unanswerable: P3 never offered it. Counted by shortlist_recall,
            # not by the judge's score.
            continue
        if NCT.search(str(unit.get("excerpt") or "")):
            # The answer is printed in the text the judge reads.
            continue
        eligible.append(unit)
    eligible.sort(key=lambda u: hashlib.sha256(
        str(u.get("unit_id") or "").encode()
    ).hexdigest())
    chosen = [{**unit, "stated_nct": ""} for unit in eligible[:limit]]
    truth = {str(unit["unit_id"]): str(unit["stated_nct"]) for unit in eligible[:limit]}
    identification.write_units(chosen, out_path)
    _log.info(
        "wrote %d withheld-answer units from %d leak-free eligible",
        len(chosen),
        len(eligible),
    )
    return truth


def score(truth: dict[str, str], verdicts_path: Path) -> tuple[Precision, tuple[Scored, ...]]:
    """Compare the judges' answers against the answers that were withheld."""
    decided = identification.read_verdicts(verdicts_path)
    rows: list[Scored] = []
    correct = wrong = abstained = 0
    for unit_id, answer in truth.items():
        verdict = decided.get(unit_id)
        if verdict is None:
            continue
        rows.append(Scored(unit_id, answer, verdict.final))
        if verdict.final == identification.NONE:
            abstained += 1
        elif verdict.final == answer:
            correct += 1
        else:
            wrong += 1
    return (
        Precision(
            scored=len(rows), correct=correct, wrong=wrong, abstained=abstained
        ),
        tuple(rows),
    )


def tier_breakdown(map_path: Path) -> tuple[tuple[str, int], ...]:
    """What the deliverable currently claims, by tier."""
    from collections import Counter

    if not map_path.is_file():
        return ()
    with map_path.open(encoding="utf-8") as handle:
        counts = Counter(
            f"{row['evidence_tier']} / {row['evidence_source']}"
            for row in csv.DictReader(handle, delimiter="\t")
        )
    return tuple(sorted(counts.items()))


def render(recall: Recall, tiers: tuple[tuple[str, int], ...]) -> str:
    """The validation report, as ``--mode validate`` prints it."""
    lines = [
        "Validation against the links the FDA states outright",
        "",
        "  SHORTLIST RECALL — the ceiling on everything P4 can do",
        f"    units with a known answer              {recall.checked:>9,}",
        f"    whose answer P3 shortlisted            {recall.present:>9,}"
        f"  ({100 * recall.rate:.1f}%)",
        f"    unreachable by any judge               "
        f"{recall.checked - recall.present:>9,}",
    ]
    if recall.absent_examples:
        lines.append("    examples not shortlisted:")
        for example in recall.absent_examples[:5]:
            lines.append(f"      {example[:88]}")
    lines += ["", "  CURRENT MAP, by tier and source:"]
    for name, count in tiers:
        lines.append(f"    {name:<38}{count:>9,}")
    return "\n".join(lines)


__all__ = [
    "Precision",
    "Recall",
    "Scored",
    "render",
    "score",
    "shortlist_recall",
    "tier_breakdown",
    "withhold",
]
