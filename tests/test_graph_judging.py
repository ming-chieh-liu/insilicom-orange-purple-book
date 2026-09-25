"""Tier C judging: versioned, validated against each unit's own candidates."""

from __future__ import annotations

import json
from pathlib import Path

from service import graph_judging


def _units(path: Path) -> Path:
    rows = [
        {"unit_id": "NDA1|ORIG|1|graph", "candidates": [{"nct_id": "NCT00000001"},
                                                         {"nct_id": "NCT00000002"}]},
        {"unit_id": "NDA2|ORIG|1|graph", "candidates": [{"nct_id": "NCT00000003"}]},
        {"unit_id": "NDA3|ORIG|1|graph", "candidates": [{"nct_id": "NCT00000004"}]},
        {"unit_id": "NDA4|ORIG|1|graph", "candidates": "not a list"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_the_round_trip_records_versioned_validated_verdicts(tmp_path: Path) -> None:
    units = _units(tmp_path / "graph_units.jsonl")
    batches, raw, verdicts = tmp_path / "b", tmp_path / "r", tmp_path / "graph_verdicts.jsonl"
    prepared = graph_judging.prepare(units, batches, verdicts)
    assert prepared.units == 4 and len(prepared.new_batches) == 1
    assert graph_judging.prepare(units, batches, verdicts).new_batches == ()   # resumable
    raw.mkdir()
    (raw / "graph-batch-001-A.txt").write_text(
        "NDA1|ORIG|1|graph = NCT00000001\nNDA2|ORIG|1|graph = NONE\n"
        "NDA3|ORIG|1|graph = NCT00000004\nNDA4|ORIG|1|graph = NCT00000009\n", encoding="utf-8")
    (raw / "graph-batch-001-B.txt").write_text(
        "NDA1|ORIG|1|graph = NCT00000001\nNDA2|ORIG|1|graph = NONE\n"
        "NDA3|ORIG|1|graph = NONE\nNDA4|ORIG|1|graph = NCT00000009\n", encoding="utf-8")
    report = graph_judging.combine(units, raw, batches, verdicts, "2026-09-25")
    assert (report.newly_recorded, report.refused) == (2, 1)   # NDA4 names an unshown id
    assert len(report.tiebreak_batches) == 1                    # NDA3 split
    rows = [json.loads(l) for l in verdicts.read_text(encoding="utf-8").splitlines()]
    assert {r["prompt_version"] for r in rows} == {graph_judging.PROMPT_VERSION}
    (raw / "graph-tiebreak-batch-001-C.txt").write_text(
        "NDA3|ORIG|1|graph = NCT00000004\n", encoding="utf-8")
    again = graph_judging.combine(units, raw, batches, verdicts, "2026-09-25")
    assert again.newly_recorded == 1 and again.tiebreak_batches == ()
    assert graph_judging.combine(units, raw, batches, verdicts, "d").newly_recorded == 0
    assert graph_judging.read_picks(verdicts) == [("NDA1|ORIG|1", "NCT00000001"),
                                                  ("NDA3|ORIG|1", "NCT00000004")]
    text = graph_judging.render(again)
    assert "identified" in text and "nothing pending" in text
    assert "run judges A and B" in graph_judging.render(prepared)
    assert "run a tiebreak judge" in graph_judging.render(report)


def test_a_batch_missing_its_second_judge_waits(tmp_path: Path) -> None:
    units = _units(tmp_path / "u.jsonl")
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "graph-batch-001-A.txt").write_text(
        "NDA1|ORIG|1|graph = NCT00000001\n", encoding="utf-8")
    report = graph_judging.combine(units, tmp_path / "r", tmp_path / "b",
                                   tmp_path / "v.jsonl", "d")
    assert report.newly_recorded == 0


def test_the_limit_caps_a_pilot(tmp_path: Path) -> None:
    units = _units(tmp_path / "u.jsonl")
    [batch] = graph_judging.prepare(units, tmp_path / "b", tmp_path / "v.jsonl",
                                    limit=2).new_batches
    assert len(batch.read_text(encoding="utf-8").split()) == 2
