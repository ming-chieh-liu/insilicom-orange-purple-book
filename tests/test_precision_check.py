"""P4 precision by evidence group, on withheld answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import precision_check as pc


def _cand(nct: str, acronym: str = "", enrollment: str = "100") -> dict[str, object]:
    return {"nct_id": nct, "acronym": acronym, "enrollment": enrollment,
            "brief_title": "t", "org_study_id": "", "phases": "PHASE3",
            "start_date": "2015-01-01", "conditions": "", "interventions": ""}


def _unit(uid: str, app: str, stated: str, *, acronym: str = "", enrollment: str = "",
          date: str = "2020-01-01", excerpt: str = "text",
          candidates: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"unit_id": uid, "application": app, "submission_type": "SUPPL",
            "submission_no": uid.split("|")[2], "approval_date": date, "stated_nct": stated,
            "acronym": acronym, "enrollment": enrollment, "excerpt": excerpt,
            "candidates": candidates if candidates is not None else
            [_cand("NCT00000001", "ALPHA-1", "300"), _cand("NCT00000002", "", "240")]}


def _write(path: Path, units: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(u) for u in units) + "\n", encoding="utf-8")
    return path


def test_groups_use_only_what_the_judge_sees(tmp_path: Path) -> None:
    units = _write(tmp_path / "c.jsonl", [
        _unit("A|SUPPL|1|ALPHA-1", "A", "NCT00000001", acronym="ALPHA-1"),
        _unit("B|SUPPL|1|n=240", "B", "NCT00000002", enrollment="240"),
        _unit("C|SUPPL|1|n=250", "C", "NCT00000002", enrollment="250"),
        _unit("D|SUPPL|1|#1", "D", "NCT00000002"),
        _unit("E|SUPPL|1|#1", "E", "NCT00000009"),                       # answer not offered
        _unit("F|SUPPL|1|X", "F", "NCT00000001", excerpt="see NCT00000001"),  # leaks an id
        _unit("G|SUPPL|1|X", "G", ""),                            # no known answer
    ])
    assert dict(pc.eligible_counts(units)) == {
        "name_unique": 1, "count_exact_unique": 1, "count_approximate": 1, "weak": 1,
        "answer_absent": 1}


def test_the_sample_is_earliest_only_and_spread_across_applications(tmp_path: Path) -> None:
    units = _write(tmp_path / "c.jsonl", [
        _unit("A|SUPPL|5|ALPHA-1", "A", "NCT00000001", acronym="ALPHA-1", date="2021"),
        _unit("A|SUPPL|2|ALPHA-1", "A", "NCT00000001", acronym="ALPHA-1", date="2019"),
        _unit("A|SUPPL|3|n=240", "A", "NCT00000002", enrollment="240"),
        _unit("A|SUPPL|4|X", "A", "NCT00000003", acronym="ALPHA-1",
              candidates=[_cand("NCT00000003", "ALPHA-1")]),
        _unit("A|SUPPL|6|Y", "A", "NCT00000004", acronym="ALPHA-1",
              candidates=[_cand("NCT00000004", "ALPHA-1")]),
    ])
    chosen, truth = pc.sample(units, per_group=10)
    assert "A|SUPPL|5|ALPHA-1" not in truth and truth["A|SUPPL|2|ALPHA-1"]["truth"] == "NCT00000001"
    assert sum(1 for t in truth.values() if t["group"] == "name_unique") == pc.PER_APPLICATION
    assert all(u["stated_nct"] == "" for u in chosen)


def test_prepare_combine_score_round_trip(tmp_path: Path) -> None:
    units = _write(tmp_path / "c.jsonl", [
        _unit("A|SUPPL|1|ALPHA-1", "A", "NCT00000001", acronym="ALPHA-1"),
        _unit("B|SUPPL|1|n=240", "B", "NCT00000002", enrollment="240"),
        _unit("C|SUPPL|1|n=250", "C", "NCT00000002", enrollment="250"),
        _unit("E|SUPPL|1|#1", "E", "NCT00000009"),
    ])
    folder = tmp_path / "exp"
    count, batches = pc.prepare(units, folder, per_group=5)
    assert count == 4 and len(batches) == 1
    with pytest.raises(FileExistsError):
        pc.prepare(units, folder, per_group=5)
    raw = folder / "raw"
    raw.mkdir()
    (raw / "prec-batch-001-A.txt").write_text(
        "A|SUPPL|1|ALPHA-1 = NCT00000001\nB|SUPPL|1|n=240 = NCT00000002\nC|SUPPL|1|n=250 = NCT00000001\n"
        "E|SUPPL|1|#1 = NCT00000001\n", encoding="utf-8")
    (raw / "prec-batch-001-B.txt").write_text(
        "A|SUPPL|1|ALPHA-1 = NCT00000001\nB|SUPPL|1|n=240 = NCT00000002\nC|SUPPL|1|n=250 = NONE\n"
        "E|SUPPL|1|#1 = NCT00000001\n", encoding="utf-8")
    new, pending, missing = pc.combine(folder, "2026-09-25")
    assert (new, pending, missing) == (3, ["C|SUPPL|1|n=250"], [])
    [tie] = list((folder / "batches").glob("prec-tiebreak-batch-*.ids"))
    assert tie.read_text(encoding="utf-8").split() == ["C|SUPPL|1|n=250"]
    (raw / "prec-tiebreak-batch-001-C.txt").write_text("C|SUPPL|1|n=250 = NONE\n",
                                                       encoding="utf-8")
    assert pc.combine(folder, "2026-09-25")[0] == 1
    assert pc.combine(folder, "2026-09-25")[0] == 0          # nothing twice
    by = {s.group: s for s in pc.score(folder)}
    assert (by["name_unique"].correct, by["count_exact_unique"].correct) == (1, 1)
    assert by["count_approximate"].abstained == 1
    assert by["answer_absent"].wrong == 1                      # a pick where none exists
    text = pc.render(pc.score(folder), pc.eligible_counts(units))
    assert "answer_absent" in text and "100.0%" in text


def test_an_answer_off_the_list_is_refused(tmp_path: Path) -> None:
    units = _write(tmp_path / "c.jsonl", [_unit("A|SUPPL|1|ALPHA-1", "A", "NCT00000001",
                                                acronym="ALPHA-1")])
    folder = tmp_path / "exp"
    pc.prepare(units, folder, per_group=5)
    (folder / "raw").mkdir()
    for judge in "AB":
        (folder / "raw" / f"prec-batch-001-{judge}.txt").write_text(
            "A|SUPPL|1|ALPHA-1 = NCT77777777\n", encoding="utf-8")
    assert pc.combine(folder, "d")[0] == 0


def test_a_batch_without_its_second_judge_waits(tmp_path: Path) -> None:
    units = _write(tmp_path / "c.jsonl", [_unit("A|SUPPL|1|ALPHA-1", "A", "NCT00000001",
                                                acronym="ALPHA-1")])
    folder = tmp_path / "exp"
    pc.prepare(units, folder, per_group=5)
    (folder / "raw").mkdir()
    (folder / "raw" / "prec-batch-001-A.txt").write_text("A|SUPPL|1|ALPHA-1 = NCT00000001\n",
                                                         encoding="utf-8")
    assert pc.combine(folder, "d") == (0, [], [])
