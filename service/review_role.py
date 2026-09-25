"""Role judging for trials FDA reviews name (D26): prepare batches, combine answers.

``--mode reviews`` writes one unit per ``(submission, trial)`` to
``adjudication/review_role_units.jsonl``. Judges answer with one of six roles
(``docs/prompts/review-trial-role.md``); this module batches the undecided
units and pairs the answers, per ``docs/runbooks/review-trial-role.md``:
two judges, a fresh third on disagreement, nothing recorded that was not
returned.

**Versioned by folder.** Answers under prompt v2 live in
``review_role_raw/v2/`` and batches in ``review_role_batches/v2/``, so they never
mix with the pilot's v1 answers; a unit is decided only by a verdict carrying
the current :data:`PROMPT_VERSION`, so units judged under v1 are judged again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from service.judging import (
    batched_ids,
    read_answer_files,
    read_jsonl,
    recorded,
    write_batches,
)
from utility.logging import get_logger

_log = get_logger(__name__)

PROMPT_VERSION: int = 2

ROLES: frozenset[str] = frozenset({
    "PIVOTAL", "SUPPORTING", "PK_CLINPHARM", "NOT_FOR_THIS_APPROVAL", "FAILED", "OTHER",
})

#: Roles that make a trial → submission link (tier A, D27).
LINKING: frozenset[str] = frozenset({"PIVOTAL", "SUPPORTING"})

#: Units whose role is known from the approval history, plus the pilot's two
#: possible misses; batched first so every run re-checks them.
CALIBRATION: tuple[str, ...] = (
    "NDA202811|ORIG|1|NCT00938717",    # Linzess MCP-103-302 → PIVOTAL
    "NDA214787|SUPPL|10|NCT04292899",  # Veklury SIMPLE, earlier approval → NOT_FOR_THIS_APPROVAL
    "BLA125409|SUPPL|51|NCT01358877",  # Perjeta APHINITY, ongoing → NOT_FOR_THIS_APPROVAL
    "NDA202880|ORIG|1|NCT02126917",    # Zohydro alcohol interaction → PK_CLINPHARM
    "NDA214324|ORIG|1|NCT03950739",    # Tyvaso DPI BREEZE — v1 said OTHER; possible miss
    "NDA209500|SUPPL|6|NCT02600494",   # Caplyta ITI-007-401 — v1 said OTHER; possible miss
)

STEP = "role"


class RoleReport(NamedTuple):
    """Where role judging stands."""

    units: int
    decided: int
    new_batches: tuple[Path, ...]
    tiebreak_batches: tuple[Path, ...]
    newly_recorded: int
    by_role: tuple[tuple[str, int], ...]


def pair(a: str, b: str, c: str) -> tuple[str, bool]:
    """``(final, needs_tiebreak)``; ``""`` while undecided.

    >>> pair("PIVOTAL", "PIVOTAL", "")
    ('PIVOTAL', False)
    >>> pair("PIVOTAL", "OTHER", "")
    ('', True)
    >>> pair("PIVOTAL", "OTHER", "OTHER")
    ('OTHER', False)
    >>> pair("PIVOTAL", "maybe", "")
    ('', False)
    """
    first, second, third = (x if x in ROLES else "" for x in (a, b, c))
    if not (first and second):
        return "", False
    if first == second:
        return first, False
    if third:
        return third, False
    return "", True


def _decided(verdicts: Path) -> dict[str, dict[str, object]]:
    return {u: r for u, r in recorded(verdicts).items()
            if r.get("prompt_version") == PROMPT_VERSION and r.get("final") in ROLES}


def prepare(units_path: Path, batch_dir: Path, verdicts: Path,
            limit: int | None = None) -> RoleReport:
    """Batch every unit without a current-version verdict, calibration first."""
    unit_ids = [str(u["unit_id"]) for u in read_jsonl(units_path)]
    decided = _decided(verdicts)
    done = batched_ids(batch_dir, STEP) | set(decided)
    present = set(unit_ids)
    ordered = [u for u in CALIBRATION if u in present] + \
              [u for u in unit_ids if u not in CALIBRATION]
    pending = [u for u in ordered if u not in done]
    if limit is not None:
        pending = pending[:limit]
    batches = write_batches(batch_dir, STEP, pending) if pending else []
    return RoleReport(len(unit_ids), len(decided), tuple(batches), (), 0, ())


def combine(units_path: Path, raw_dir: Path, batch_dir: Path, verdicts: Path,
            decided_on: str) -> RoleReport:
    """Pair every answer on disk; batch tiebreaks; record new decisions."""
    unit_ids = [str(u["unit_id"]) for u in read_jsonl(units_path)]
    a, b, c = read_answer_files(raw_dir, STEP)
    before = _decided(verdicts)
    tiebreak: list[str] = []
    rows: list[dict[str, object]] = []
    for unit_id in unit_ids:
        final, needs = pair(a.get(unit_id, ""), b.get(unit_id, ""), c.get(unit_id, ""))
        if needs:
            tiebreak.append(unit_id)
        if not final:
            continue
        held = before.get(unit_id)
        if held is not None and held.get("final") == final:
            continue
        rows.append({
            "unit_id": unit_id, "judge_a": a.get(unit_id), "judge_b": b.get(unit_id),
            "tiebreak": c.get(unit_id) if a.get(unit_id) != b.get(unit_id) else None,
            "final": final, "prompt_version": PROMPT_VERSION, "decided": decided_on,
        })
    new_tiebreaks = write_batches(batch_dir, f"{STEP}-tiebreak", [
        u for u in tiebreak if u not in batched_ids(batch_dir, f"{STEP}-tiebreak")
    ])
    if rows:
        verdicts.parent.mkdir(parents=True, exist_ok=True)
        with verdicts.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    now = _decided(verdicts)
    counts: dict[str, int] = {}
    for row in now.values():
        counts[str(row["final"])] = counts.get(str(row["final"]), 0) + 1
    return RoleReport(len(unit_ids), len(now), (), tuple(new_tiebreaks), len(rows),
                      tuple(sorted(counts.items(), key=lambda kv: -kv[1])))


def render(report: RoleReport) -> str:
    """The prepare/combine report, with the next action spelled out."""
    lines = [
        f"Review-trial roles — prompt v{PROMPT_VERSION} (docs/runbooks/review-trial-role.md)",
        "",
        f"  units                                    {report.units:>9,}",
        f"  decided under v{PROMPT_VERSION}                          {report.decided:>9,}",
        f"  newly recorded                           {report.newly_recorded:>9,}",
    ]
    for role, count in report.by_role:
        lines.append(f"    {role:<38}{count:>9,}")
    lines += ["", "  next:"]
    for path in report.new_batches:
        lines.append(f"    run judges A and B: {path.name}")
    for path in report.tiebreak_batches:
        lines.append(f"    run a tiebreak judge: {path.name}")
    if not (report.new_batches or report.tiebreak_batches):
        lines.append("    nothing pending for the answers on disk")
    return "\n".join(lines)


__all__ = [
    "CALIBRATION",
    "LINKING",
    "PROMPT_VERSION",
    "ROLES",
    "RoleReport",
    "combine",
    "pair",
    "prepare",
    "render",
]
