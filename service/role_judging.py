"""The six-role judgement shared by the review route (D26) and label links (D28).

Both tasks ask one question of one ``(submission, trial)`` unit — what role
does this trial play in this approval? — with the same six answers, the same
two judges and the same fresh third on disagreement. Only the prompt, the
version, the folders and the calibration units differ, so those are
parameters here and each task module holds its own.

Rules, per ``docs/runbooks/adjudicate-section-14.md``: a unit is decided only
by a verdict under the task's **current** prompt version; nothing is recorded
that a judge did not return; verdicts are appended, never rewritten.
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

ROLES: frozenset[str] = frozenset({
    "PIVOTAL", "SUPPORTING", "PK_CLINPHARM", "NOT_FOR_THIS_APPROVAL", "FAILED", "OTHER",
})

#: Roles that make a trial → submission link.
LINKING: frozenset[str] = frozenset({"PIVOTAL", "SUPPORTING"})


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


def decided(verdicts: Path, version: int) -> dict[str, dict[str, object]]:
    """Verdicts under ``version`` with a valid final role, by unit id."""
    return {u: r for u, r in recorded(verdicts).items()
            if r.get("prompt_version") == version and r.get("final") in ROLES}


def prepare(units_path: Path, batch_dir: Path, verdicts: Path, *, version: int,
            calibration: tuple[str, ...], step: str,
            limit: int | None = None) -> RoleReport:
    """Batch every unit without a current-version verdict, calibration first."""
    unit_ids = [str(u["unit_id"]) for u in read_jsonl(units_path)]
    held = decided(verdicts, version)
    done = batched_ids(batch_dir, step) | set(held)
    present = set(unit_ids)
    ordered = [u for u in calibration if u in present] + \
              [u for u in unit_ids if u not in calibration]
    pending = [u for u in ordered if u not in done]
    if limit is not None:
        pending = pending[:limit]
    batches = write_batches(batch_dir, step, pending) if pending else []
    return RoleReport(len(unit_ids), len(held), tuple(batches), (), 0, ())


def combine(units_path: Path, raw_dir: Path, batch_dir: Path, verdicts: Path,
            decided_on: str, *, version: int, step: str) -> RoleReport:
    """Pair every answer on disk; batch tiebreaks; record new decisions."""
    unit_ids = [str(u["unit_id"]) for u in read_jsonl(units_path)]
    a, b, c = read_answer_files(raw_dir, step)
    before = decided(verdicts, version)
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
            "final": final, "prompt_version": version, "decided": decided_on,
        })
    new_tiebreaks = write_batches(batch_dir, f"{step}-tiebreak", [
        u for u in tiebreak if u not in batched_ids(batch_dir, f"{step}-tiebreak")
    ])
    if rows:
        verdicts.parent.mkdir(parents=True, exist_ok=True)
        with verdicts.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    now = decided(verdicts, version)
    counts: dict[str, int] = {}
    for row in now.values():
        counts[str(row["final"])] = counts.get(str(row["final"]), 0) + 1
    return RoleReport(len(unit_ids), len(now), (), tuple(new_tiebreaks), len(rows),
                      tuple(sorted(counts.items(), key=lambda kv: -kv[1])))


def render(report: RoleReport, title: str, version: int) -> str:
    """The prepare/combine report, with the next action spelled out."""
    lines = [
        f"{title} — prompt v{version}",
        "",
        f"  units                                    {report.units:>9,}",
        f"  decided under v{version}                          {report.decided:>9,}",
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
    "LINKING",
    "ROLES",
    "RoleReport",
    "combine",
    "decided",
    "pair",
    "prepare",
    "render",
]
