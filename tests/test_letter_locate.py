"""Tests for the agent-located letter indication task (locate → extract → verify)."""

from __future__ import annotations

import json
from pathlib import Path

from service import letter_locate as ll

_LETTERS = [
    # The keyword rule found nothing: goes to the agents.
    {"application": "BLA000001", "submission_type": "ORIG", "submission_no": "1",
     "approval_date": "2020-01-01", "letter_url": "u1", "status": "no_sentence",
     "indication_bearing": "false"},
    # A sentence the rule judged not indication-bearing: also goes.
    {"application": "BLA000001", "submission_type": "SUPPL", "submission_no": "2",
     "approval_date": "2021-01-01", "letter_url": "u2", "status": "ok",
     "indication_bearing": "false"},
    # Already served by the rule: does not go.
    {"application": "BLA000001", "submission_type": "SUPPL", "submission_no": "3",
     "approval_date": "2022-01-01", "letter_url": "u3", "status": "ok",
     "indication_bearing": "true"},
    # Candidate, but its file cannot be read.
    {"application": "BLA000001", "submission_type": "SUPPL", "submission_no": "4",
     "approval_date": "2023-01-01", "letter_url": "u4", "status": "no_sentence",
     "indication_bearing": "false"},
]
_TEXT = {
    "u1": "Please refer to your BLA. Erelzi is indicated for rheumatoid arthritis. "
          "APPROVAL & LABELING We have completed our review.",
    "u2": "This supplement provides for a new strength. It is approved.",
}
U1, U2 = "BLA000001|ORIG|1", "BLA000001|SUPPL|2"


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "units": tmp_path / "locate_units.jsonl",
        "raw": tmp_path / "raw",
        "batches": tmp_path / "batches",
        "verify": tmp_path / "verify_units.jsonl",
        "verdicts": tmp_path / "verdicts.jsonl",
    }


def _prepare(p: dict[str, Path], limit: int | None = None) -> ll.PrepareReport:
    return ll.prepare(_LETTERS, _TEXT.get, {"000001": {"ERELZI"}}, p["units"],
                      p["batches"], limit=limit)


def _answer(p: dict[str, Path], name: str, lines: list[str]) -> None:
    p["raw"].mkdir(exist_ok=True)
    (p["raw"] / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _combine(p: dict[str, Path]) -> ll.CombineReport:
    return ll.combine(p["units"], p["raw"], p["batches"], p["verify"], p["verdicts"],
                      decided="2026-09-24")


def test_prepare_selects_what_the_rule_missed_and_batches_once(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    report = _prepare(p)
    assert (report.candidates, report.units, report.unreadable) == (3, 2, 1)
    assert [x.name for x in report.new_batches] == ["locate-batch-001.ids"]
    unit = next(ll.read_jsonl(p["units"]))
    assert unit["approval"] == "ERELZI · BLA000001 ORIG 1 · approved 2020-01-01"
    assert "[S2] Erelzi is indicated for rheumatoid arthritis." in str(unit["segments"])
    # A second run batches nothing new: no letter is judged twice.
    assert _prepare(p).new_batches == ()


def test_prepare_respects_a_pilot_limit(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    batch = _prepare(p, limit=1).new_batches[0]
    assert batch.read_text().split() == [U1]


def test_the_whole_flow_records_only_what_code_extracted(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _prepare(p)
    _answer(p, "locate-batch-001-A.txt", [f"{U1} = S2", f"{U2} = NONE"])
    _answer(p, "locate-batch-001-B.txt", [f"{U1} = S2", f"{U2} = NONE"])
    first = _combine(p)
    assert (first.locate_agreed, first.none_located, first.spans) == (2, 1, 1)
    assert [x.name for x in first.verify_batches] == ["verify-batch-001.ids"]
    verify_unit = next(ll.read_jsonl(p["verify"]))
    assert verify_unit["text"] == "Erelzi is indicated for rheumatoid arthritis."
    assert first.newly_recorded == 1  # only the NONE is decided so far

    _answer(p, "verify-batch-001-A.txt", [f"{U1} = STATES_INDICATION"])
    _answer(p, "verify-batch-001-B.txt", [f"{U1} = STATES_INDICATION"])
    second = _combine(p)
    assert (second.accepted, second.newly_recorded) == (1, 1)
    assert ll.accepted_spans(p["verdicts"]) == {
        U1: "Erelzi is indicated for rheumatoid arthritis."
    }
    # Idempotent: nothing new on disk, nothing appended.
    assert _combine(p).newly_recorded == 0
    assert "nothing pending" in ll.render(_combine(p))


def test_disagreements_go_to_a_third_judge(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _prepare(p)
    _answer(p, "locate-batch-001-A.txt", [f"{U1} = S2", f"{U2} = NONE"])
    _answer(p, "locate-batch-001-B.txt", [f"{U1} = S2", f"{U2} = S1"])
    report = _combine(p)
    assert [x.name for x in report.locate_tiebreaks] == ["locate-tiebreak-batch-001.ids"]
    assert "run locate tiebreak" in ll.render(report)
    _answer(p, "locate-tiebreak-batch-001-C.txt", [f"{U2} = NONE"])
    _answer(p, "verify-batch-001-A.txt", [f"{U1} = STATES_INDICATION"])
    _answer(p, "verify-batch-001-B.txt", [f"{U1} = NOT_INDICATION"])
    report = _combine(p)
    assert report.none_located == 1
    assert [x.name for x in report.verify_tiebreaks] == ["verify-tiebreak-batch-001.ids"]
    _answer(p, "verify-tiebreak-batch-001-C.txt", [f"{U1} = NOT_INDICATION"])
    report = _combine(p)
    assert report.rejected == 1
    assert ll.accepted_spans(p["verdicts"]) == {}


def test_an_invented_segment_is_refused_and_nothing_is_recorded(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _prepare(p)
    _answer(p, "locate-batch-001-A.txt", [f"{U1} = S99"])
    _answer(p, "locate-batch-001-B.txt", [f"{U1} = S99"])
    report = _combine(p)
    assert report.invalid == (U1,)
    assert not p["verdicts"].exists() or not p["verdicts"].read_text().strip()


def test_a_missing_units_file_combines_to_nothing(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    assert _combine(p).locate_answered == 0
    assert list(ll.read_jsonl(p["units"])) == []
    assert ll.recorded(p["verdicts"]) == {}
    p["verdicts"].write_text(json.dumps({"unit_id": U1, "final": "accepted"}) + "\n")
    assert ll.accepted_spans(p["verdicts"]) == {}  # no text, no span
