"""Shared plumbing for agent-judged tasks: batches, answer files, verdicts.

Every judged task in this project follows the same convention
(``CLAUDE.md`` — agent-delegated work requires a runbook): units are written to
a JSON-lines file, unit ids are chunked into ``<step>-batch-NNN.ids`` files,
each judge's answers come back as ``<unit_id> = <ANSWER>`` lines saved as
``<step>-…-{A,B,C}.txt``, and decided units are appended to a verdicts file
where the last row per unit wins. These helpers are that convention, once.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

#: Unit ids per batch file.
BATCH_SIZE: int = 25

ANSWER = re.compile(r"^(\S+) = (.+?)\s*$")


def parse_answers(lines: Iterable[str]) -> dict[str, str]:
    """``unit_id = ANSWER`` lines to a dict; anything else is ignored.

    >>> parse_answers(["a|ORIG|1 = S4,S5", "junk", "b|SUPPL|2 = NONE"])
    {'a|ORIG|1': 'S4,S5', 'b|SUPPL|2': 'NONE'}
    """
    out: dict[str, str] = {}
    for line in lines:
        match = ANSWER.match(line.strip())
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def read_answer_files(raw_dir: Path, step: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """All ``<step>-*-{A,B,C}.txt`` answers under ``raw_dir``, merged per judge."""
    merged: dict[str, dict[str, str]] = {"A": {}, "B": {}, "C": {}}
    for path in sorted(raw_dir.glob(f"{step}-*-[ABC].txt")):
        merged[path.stem[-1]].update(parse_answers(path.read_text(encoding="utf-8").splitlines()))
    return merged["A"], merged["B"], merged["C"]


def batched_ids(batch_dir: Path, step: str) -> set[str]:
    """Unit ids already written to a ``<step>-batch-*.ids`` file."""
    out: set[str] = set()
    for path in batch_dir.glob(f"{step}-batch-*.ids"):
        out.update(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return out


def write_batches(batch_dir: Path, step: str, unit_ids: list[str],
                  size: int = BATCH_SIZE) -> list[Path]:
    """Chunk ``unit_ids`` into new batch files, numbered after the existing ones."""
    batch_dir.mkdir(parents=True, exist_ok=True)
    taken = [int(m.group(1)) for p in batch_dir.glob(f"{step}-batch-*.ids")
             if (m := re.search(r"batch-(\d+)", p.name))]
    start = max(taken, default=0) + 1
    written: list[Path] = []
    for offset in range(0, len(unit_ids), size):
        path = batch_dir / f"{step}-batch-{start + offset // size:03d}.ids"
        path.write_text("\n".join(unit_ids[offset:offset + size]) + "\n", encoding="utf-8")
        written.append(path)
    return written


def write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> int:
    """Rewrite a units file; returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    """Rows of a JSON-lines file; nothing if it is absent."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def recorded(verdicts: Path) -> dict[str, dict[str, object]]:
    """The last recorded verdict per unit — last row wins, append-only."""
    return {str(row["unit_id"]): row for row in read_jsonl(verdicts) if row.get("unit_id")}


__all__ = [
    "ANSWER",
    "BATCH_SIZE",
    "batched_ids",
    "parse_answers",
    "read_answer_files",
    "read_jsonl",
    "recorded",
    "write_batches",
    "write_jsonl",
]
