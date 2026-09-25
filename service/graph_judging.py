"""Tier C judging: batch the graph units, pair the judges, record verdicts.

``--mode graph-links`` writes ``adjudication/graph_units.jsonl`` (D14, D31).
This module takes it from there, per ``docs/runbooks/select-graph-trial.md``:
two judges per unit, a fresh third on disagreement, an answer accepted only if
it is ``NONE`` or one of **that unit's own** candidate ids, verdicts appended
and stamped with **this** task's prompt version.

It exists because the tier-C runbook used to borrow P4's ``identify-record``,
which validates against a ``candidate_ids`` field graph units do not have — so
every named trial would have been refused — and stamped P4's prompt version on
tier-C verdicts.

Versioned by folder, as the role tasks are: batches in
``graph_batches/v<N>/``, answers in ``graph_raw/v<N>/``.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from service import identification
from service.judging import batched_ids, read_jsonl, recorded, write_batches

#: ``docs/prompts/graph-trial-selection.md``.
PROMPT_VERSION: int = 2

STEP = "graph"

#: Ids per judge batch, as the runbook says.
BATCH_SIZE: int = 20

#: The ``evidence_source`` a tier-C link carries in the map (D29's name).
EVIDENCE_SOURCE = "kg_abstract"


class GraphJudgingReport(NamedTuple):
    """Where tier-C judging stands."""

    units: int
    decided: int
    new_batches: tuple[Path, ...]
    tiebreak_batches: tuple[Path, ...]
    newly_recorded: int
    refused: int
    by_answer: tuple[tuple[str, int], ...]


def candidate_ids(units_path: Path) -> dict[str, set[str]]:
    """Unit id → the NCT ids that unit showed its judges."""
    out: dict[str, set[str]] = {}
    for unit in read_jsonl(units_path):
        raw = unit.get("candidates")
        out[str(unit["unit_id"])] = {
            str(c.get("nct_id") or "") for c in raw if isinstance(c, dict)
        } if isinstance(raw, list) else set()
    return out


def _decided(verdicts: Path) -> dict[str, dict[str, object]]:
    return {u: r for u, r in recorded(verdicts).items()
            if r.get("prompt_version") == PROMPT_VERSION and r.get("final")}


def prepare(units_path: Path, batch_dir: Path, verdicts: Path,
            limit: int | None = None) -> GraphJudgingReport:
    """Batch every unit with no current-version verdict and no batch yet."""
    unit_ids = [str(u["unit_id"]) for u in read_jsonl(units_path)]
    held = _decided(verdicts)
    done = batched_ids(batch_dir, STEP) | set(held)
    pending = [u for u in unit_ids if u not in done]
    if limit is not None:
        pending = pending[:limit]
    batches = write_batches(batch_dir, STEP, pending, size=BATCH_SIZE) if pending else []
    return GraphJudgingReport(len(unit_ids), len(held), tuple(batches), (), 0, 0, ())


def combine(units_path: Path, raw_dir: Path, batch_dir: Path, verdicts: Path,
            decided_on: str) -> GraphJudgingReport:
    """Pair A and B per batch with any tiebreaks; append what is decided.

    An answer naming an id the unit never showed is refused, not stored:
    that is either a misread or an invented trial, and a fabricated edge is the
    failure this task exists to avoid.
    """
    allowed = candidate_ids(units_path)
    tiebreaks: dict[str, str] = {}
    for path in sorted(raw_dir.glob(f"{STEP}-tiebreak-batch-*-C.txt")):
        tiebreaks.update(identification.read_judge_file(path))
    held = _decided(verdicts)
    rows: list[dict[str, object]] = []
    pending: list[str] = []
    refused = 0
    for a_path in sorted(raw_dir.glob(f"{STEP}-batch-*-A.txt")):
        b_path = a_path.with_name(a_path.name.replace("-A.txt", "-B.txt"))
        if not b_path.exists():
            continue
        agreed, needs, _absent = identification.combine(
            identification.read_judge_file(a_path),
            identification.read_judge_file(b_path),
            tiebreaks,
        )
        for row in agreed:
            unit_id = str(row["unit_id"])
            if unit_id in held:
                continue
            if not identification.is_allowed(str(row["final"]), allowed.get(unit_id, set())):
                refused += 1
                continue
            rows.append({**row, "prompt_version": PROMPT_VERSION, "decided": decided_on})
        pending.extend(u for u in needs if u not in held)
    if rows:
        verdicts.parent.mkdir(parents=True, exist_ok=True)
        with verdicts.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    already = batched_ids(batch_dir, f"{STEP}-tiebreak")
    new_ties = write_batches(batch_dir, f"{STEP}-tiebreak",
                             [u for u in pending if u not in already and u not in tiebreaks],
                             size=BATCH_SIZE)
    now = _decided(verdicts)
    answers = Counter("NONE" if r["final"] == identification.NONE else "identified"
                      for r in now.values())
    return GraphJudgingReport(len(allowed), len(now), (), tuple(new_ties), len(rows),
                              refused, tuple(sorted(answers.items())))


def read_picks(verdicts: Path) -> list[tuple[str, str]]:
    """``(submission key, nct)`` for every current-version tier-C pick.

    >>> import tempfile
    >>> p = Path(tempfile.mkdtemp()) / "v.jsonl"
    >>> _ = p.write_text('{"unit_id": "NDA1|ORIG|1|graph", "final": "NCT00000001", '
    ...                  '"prompt_version": 2}\\n{"unit_id": "NDA2|ORIG|1|graph", '
    ...                  '"final": "NONE", "prompt_version": 2}\\n'
    ...                  '{"unit_id": "NDA3|ORIG|1|graph", "final": "NCT00000003", '
    ...                  '"prompt_version": 1}\\n', encoding="utf-8")
    >>> read_picks(p)
    [('NDA1|ORIG|1', 'NCT00000001')]
    """
    out: list[tuple[str, str]] = []
    for unit_id, row in _decided(verdicts).items():
        final = str(row["final"])
        if final == identification.NONE or not unit_id.endswith("|graph"):
            continue
        out.append((unit_id.removesuffix("|graph"), final))
    return sorted(out)


def render(report: GraphJudgingReport) -> str:
    """The prepare/combine report, with the next action spelled out."""
    lines = [
        f"Tier C judging — prompt v{PROMPT_VERSION} (docs/runbooks/select-graph-trial.md)",
        "",
        f"  units                                    {report.units:>9,}",
        f"  decided under v{PROMPT_VERSION}                          {report.decided:>9,}",
        f"  newly recorded                           {report.newly_recorded:>9,}",
        f"  refused (not on the unit's list)         {report.refused:>9,}",
    ]
    for answer, count in report.by_answer:
        lines.append(f"    {answer:<38}{count:>9,}")
    lines += ["", "  next:"]
    for path in report.new_batches:
        lines.append(f"    run judges A and B: {path.name}")
    for path in report.tiebreak_batches:
        lines.append(f"    run a tiebreak judge: {path.name}")
    if not (report.new_batches or report.tiebreak_batches):
        lines.append("    nothing pending for the answers on disk")
    return "\n".join(lines)


__all__ = [
    "EVIDENCE_SOURCE",
    "GraphJudgingReport",
    "PROMPT_VERSION",
    "candidate_ids",
    "combine",
    "prepare",
    "read_picks",
    "render",
]
