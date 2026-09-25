"""Tests for review-route role judging: batches, pairing, recording."""

from __future__ import annotations

import json
from pathlib import Path

from service import review_role as rr

_CAL = rr.CALIBRATION[0]
_OTHER = "NDA999999|ORIG|1|NCT00000001"


def _setup(tmp_path: Path) -> dict[str, Path]:
    p = {"units": tmp_path / "units.jsonl", "batches": tmp_path / "batches",
         "raw": tmp_path / "raw", "verdicts": tmp_path / "verdicts.jsonl"}
    p["units"].write_text("\n".join(json.dumps({"unit_id": u}) for u in (_OTHER, _CAL)) + "\n")
    p["raw"].mkdir()
    return p


def _answer(p: dict[str, Path], name: str, lines: list[str]) -> None:
    (p["raw"] / name).write_text("\n".join(lines) + "\n")


def test_calibration_units_are_batched_first_and_only_once(tmp_path: Path) -> None:
    p = _setup(tmp_path)
    report = rr.prepare(p["units"], p["batches"], p["verdicts"], limit=1)
    assert report.new_batches[0].read_text().split() == [_CAL]
    assert rr.prepare(p["units"], p["batches"], p["verdicts"]).new_batches[0].read_text().split() == [_OTHER]
    assert rr.prepare(p["units"], p["batches"], p["verdicts"]).new_batches == ()
    assert "nothing pending" in rr.render(rr.prepare(p["units"], p["batches"], p["verdicts"]))


def test_combine_records_agreement_and_escalates_disagreement(tmp_path: Path) -> None:
    p = _setup(tmp_path)
    _answer(p, "role-batch-001-A.txt", [f"{_CAL} = PIVOTAL", f"{_OTHER} = OTHER"])
    _answer(p, "role-batch-001-B.txt", [f"{_CAL} = PIVOTAL", f"{_OTHER} = FAILED"])
    report = rr.combine(p["units"], p["raw"], p["batches"], p["verdicts"], "2026-09-25")
    assert report.newly_recorded == 1 and report.by_role == (("PIVOTAL", 1),)
    assert [x.name for x in report.tiebreak_batches] == ["role-tiebreak-batch-001.ids"]
    assert "run a tiebreak judge" in rr.render(report)
    _answer(p, "role-tiebreak-batch-001-C.txt", [f"{_OTHER} = FAILED"])
    report = rr.combine(p["units"], p["raw"], p["batches"], p["verdicts"], "2026-09-25")
    assert report.newly_recorded == 1 and report.decided == 2
    rows = [json.loads(line) for line in p["verdicts"].read_text().splitlines()]
    assert rows[-1]["tiebreak"] == "FAILED" and rows[-1]["prompt_version"] == 2
    # Idempotent.
    assert rr.combine(p["units"], p["raw"], p["batches"], p["verdicts"], "x").newly_recorded == 0


def test_a_v1_verdict_does_not_count_as_decided(tmp_path: Path) -> None:
    p = _setup(tmp_path)
    p["verdicts"].write_text(json.dumps({"unit_id": _CAL, "final": "OTHER", "prompt_version": 1}) + "\n")
    report = rr.prepare(p["units"], p["batches"], p["verdicts"])
    assert report.decided == 0
    assert _CAL in report.new_batches[0].read_text().split()
