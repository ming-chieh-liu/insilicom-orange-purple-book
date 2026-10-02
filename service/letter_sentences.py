"""Each submission's approval-letter "provides for …" sentence, as S4 wrote it.

Read by S4's review step (the approval line its units carry) and by S6's label
units. Kept apart from both so that a change to S6's judging code never makes
S4 look stale to ``make`` (2026-10-02: it used to live in
:mod:`service.label_role`, and every S6 edit flagged S4's reviews).
"""

from __future__ import annotations

import csv
from pathlib import Path

#: The table S4's letter reader writes (D25).
LETTER_FILE = "letter_indications.tsv"


def read_sentences(out_dir: Path) -> dict[str, str]:
    """``application|type|number`` → the letter sentence, where one was read."""
    out: dict[str, str] = {}
    path = out_dir / LETTER_FILE
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("sentence"):
                out[f"{row['application']}|{row['submission_type']}|{row['submission_no']}"] = \
                    row["sentence"]
    return out


__all__ = ["LETTER_FILE", "read_sentences"]
