"""Tests for identifying which registered trial a label describes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import identification


def _unit(unit_id: str, candidates: int = 5, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "unit_id": unit_id,
        "excerpt": "The efficacy was studied in 616 patients.",
        "acronym": "KEYNOTE-189",
        "enrollment": "616",
        "stated_nct": "",
        "candidates": [
            {
                "nct_id": f"NCT{n:08d}",
                "brief_title": f"Study {n}",
                "acronym": "",
                "org_study_id": "",
                "phases": "PHASE3",
                "enrollment": str(600 + n),
                "start_date": "2016-01-01",
                "conditions": "NSCLC",
                "interventions": "Pembrolizumab",
            }
            for n in range(candidates)
        ],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Which units need a judge
# ---------------------------------------------------------------------------


def test_a_unit_stating_its_own_nct_is_not_read(tmp_path: Path) -> None:
    """Sending it would spend two judge calls confirming what the label said."""
    path = tmp_path / "candidates.jsonl"
    path.write_text(
        json.dumps(_unit("a"))
        + "\n"
        + json.dumps(_unit("b", stated_nct="NCT00000001"))
        + "\n",
        encoding="utf-8",
    )
    assert [u["unit_id"] for u in identification.read_units(path)] == ["a"]


def test_a_unit_with_no_candidates_is_not_read(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    path.write_text(json.dumps(_unit("a", candidates=0)) + "\n", encoding="utf-8")
    assert identification.read_units(path) == []


def test_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert identification.read_units(tmp_path / "absent.jsonl") == []


# ---------------------------------------------------------------------------
# Choosing the pilot
# ---------------------------------------------------------------------------


def test_a_pilot_takes_decidable_units_first() -> None:
    """What makes a unit decidable is the label having given a trial name and a
    patient count. Banding by shortlist size alone filled a pilot with scraped
    table text and false acronyms, which a judge correctly answers NONE to —
    measuring the extractor, not the prompt."""
    units = [
        _unit("no-name", candidates=8, acronym="", enrollment="600"),
        _unit("decidable", candidates=8),
        _unit("no-count", candidates=8, enrollment=""),
    ]
    assert identification.prioritise(units, 1)[0]["unit_id"] == "decidable"


def test_the_pilot_prefers_the_band_over_the_extremes() -> None:
    """One candidate gives the judge nothing to choose between; two hundred
    tests the shortlist rather than the prompt."""
    units = [
        _unit("degenerate", candidates=1),
        _unit("banded", candidates=10),
        _unit("huge", candidates=300),
    ]
    assert identification.prioritise(units, 1)[0]["unit_id"] == "banded"


def test_a_full_run_keeps_every_unit() -> None:
    units = [_unit("a", candidates=1), _unit("b", candidates=99)]
    assert len(identification.prioritise(units, None)) == 2


def test_the_order_is_deterministic() -> None:
    units = [_unit("b", candidates=8), _unit("a", candidates=8)]
    assert [u["unit_id"] for u in identification.prioritise(units, None)] == ["a", "b"]


# ---------------------------------------------------------------------------
# What a judge is shown
# ---------------------------------------------------------------------------


def test_the_candidate_block_leads_with_the_count() -> None:
    """The prompt tells the judge the count discriminates, so it comes first."""
    rendered = identification.render_candidates(
        [{"nct_id": "NCT00000001", "enrollment": "616", "phases": "PHASE3"}]
    )
    assert rendered.startswith("1. NCT00000001 | enrolled: 616")


def test_a_missing_field_says_so_rather_than_being_blank() -> None:
    """A blank column reads as a value of nothing; 'not stated' is a fact."""
    rendered = identification.render_candidates([{"nct_id": "NCT00000001"}])
    assert "enrolled: not stated" in rendered


def test_written_units_carry_their_candidate_ids(tmp_path: Path) -> None:
    """record_verdicts validates against these, so they must travel with the
    unit rather than be recomputed."""
    path = tmp_path / "units.jsonl"
    identification.write_units([_unit("a", candidates=2)], path)
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert written["candidate_ids"] == ["NCT00000000", "NCT00000001"]


def test_a_unit_with_no_name_says_so_to_the_judge(tmp_path: Path) -> None:
    path = tmp_path / "units.jsonl"
    identification.write_units([_unit("a", acronym="", enrollment="")], path)
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "no name" in written["acronym"]
    assert "no count" in written["enrollment"]


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "allowed", "expected"),
    [
        ("NCT00000001", ["NCT00000001"], True),
        ("NONE", ["NCT00000001"], True),
        ("NONE", [], True),
        # Not on this unit's list: misread, or invented.
        ("NCT00000002", ["NCT00000001"], False),
        ("maybe the first one", ["NCT00000001"], False),
        ("", ["NCT00000001"], False),
        ("nct00000001", ["NCT00000001"], False),
    ],
)
def test_an_answer_must_be_one_this_unit_was_shown(
    answer: str, allowed: list[str], expected: bool
) -> None:
    """A wrong identification fabricates an edge — it asserts a drug was
    approved on evidence that had nothing to do with it."""
    assert identification.is_allowed(answer, allowed) is expected


def test_a_judge_file_skips_anything_that_is_not_an_answer(tmp_path: Path) -> None:
    path = tmp_path / "batch-001-A.txt"
    path.write_text(
        "unit-1 = NCT00000001\n"
        "I think unit-2 is probably NCT00000002\n"
        "unit-3 = NONE\n"
        "unit-4 = maybe\n",
        encoding="utf-8",
    )
    assert identification.read_judge_file(path) == {
        "unit-1": "NCT00000001",
        "unit-3": "NONE",
    }


def test_a_missing_judge_file_is_empty(tmp_path: Path) -> None:
    assert identification.read_judge_file(tmp_path / "absent.txt") == {}


# ---------------------------------------------------------------------------
# Combining two judges
# ---------------------------------------------------------------------------


def test_two_judges_agreeing_decide_the_unit() -> None:
    decided, pending, absent = identification.combine(
        {"u": "NCT00000001"}, {"u": "NCT00000001"}, {}
    )
    assert decided[0]["final"] == "NCT00000001"
    assert not pending and not absent


def test_agreeing_on_none_is_a_decision_not_a_gap() -> None:
    """Two judges independently declining is the answer, and a high NONE rate
    is not failure."""
    decided, pending, _ = identification.combine({"u": "NONE"}, {"u": "NONE"}, {})
    assert decided[0]["final"] == "NONE"
    assert not pending


def test_disagreement_waits_for_a_third_judge() -> None:
    decided, pending, _ = identification.combine(
        {"u": "NCT00000001"}, {"u": "NONE"}, {}
    )
    assert decided == []
    assert pending == ["u"]


def test_the_third_judge_is_final() -> None:
    decided, pending, _ = identification.combine(
        {"u": "NCT00000001"}, {"u": "NONE"}, {"u": "NONE"}
    )
    assert decided[0]["final"] == "NONE"
    assert decided[0]["tiebreak"] == "NONE"
    assert not pending


def test_a_unit_only_one_judge_answered_is_not_decided() -> None:
    """Never write a verdict you did not receive."""
    decided, pending, absent = identification.combine(
        {"u": "NCT00000001"}, {}, {}
    )
    assert decided == [] and pending == []
    assert absent == ["u"]


def test_a_unit_only_the_second_judge_answered_is_counted() -> None:
    _, _, absent = identification.combine({}, {"u": "NONE"}, {})
    assert absent == ["u"]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_a_verdict_is_stamped_with_the_prompt_version(tmp_path: Path) -> None:
    """A verdict under v1 and one under v2 are otherwise indistinguishable."""
    path = tmp_path / "verdicts.jsonl"
    identification.record_verdicts(
        [{"unit_id": "u", "final": "NONE"}], path, {"u": set()}
    )
    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["prompt_version"] == identification.PROMPT_VERSION


def test_an_answer_not_on_the_list_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.jsonl"
    written, refused = identification.record_verdicts(
        [{"unit_id": "u", "final": "NCT00000009"}], path, {"u": {"NCT00000001"}}
    )
    assert (written, refused) == (0, 1)
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_recording_is_append_only_and_the_last_row_wins(tmp_path: Path) -> None:
    """A re-judgement corrects an earlier one without rewriting history."""
    path = tmp_path / "verdicts.jsonl"
    allowed = {"u": {"NCT00000001"}}
    identification.record_verdicts([{"unit_id": "u", "final": "NONE"}], path, allowed)
    identification.record_verdicts(
        [{"unit_id": "u", "final": "NCT00000001"}], path, allowed
    )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert identification.read_verdicts(path)["u"].final == "NCT00000001"


def test_candidate_ids_are_read_back_for_validation(tmp_path: Path) -> None:
    path = tmp_path / "units.jsonl"
    identification.write_units([_unit("a", candidates=2)], path)
    assert identification.candidate_ids_by_unit(path)["a"] == {
        "NCT00000000",
        "NCT00000001",
    }


def test_the_summary_counts_identifications_against_abstentions() -> None:
    verdicts = {
        "a": identification.Verdict("a", "N", "N", None, "NCT00000001", 1),
        "b": identification.Verdict("b", "N", "N", None, "NONE", 1),
        "c": identification.Verdict("c", "X", "Y", "NONE", "NONE", 1),
    }
    assert dict(identification.summarise(verdicts)) == {
        "decided": 3,
        "identified a trial": 1,
        "answered NONE": 2,
        "needed a tiebreak": 1,
    }


def test_a_malformed_jsonl_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"unit_id": "a"}\nnot json\n[1,2]\n\n', encoding="utf-8")
    assert [r["unit_id"] for r in identification.iter_json_lines(path)] == ["a"]


def test_the_unit_id_shape() -> None:
    assert (
        identification.unit_id_for("BLA125514", "SUPPL", "35", "KEYNOTE-189")
        == "BLA125514|SUPPL|35|KEYNOTE-189"
    )


def test_a_unit_id_containing_an_equals_sign_still_parses(tmp_path: Path) -> None:
    """A trial the label did not name is keyed on its enrollment count, so the
    unit id itself contains '='. Splitting on the first one yields a truncated
    id and an unparseable answer, and the unit vanishes without a warning —
    thirteen of sixty did, in the first real run."""
    path = tmp_path / "batch-001-A.txt"
    path.write_text(
        "BLA125516|ORIG|1|n=226 = NCT00026312\n"
        "NDA019941|SUPPL|11|n=200 = NONE\n",
        encoding="utf-8",
    )
    assert identification.read_judge_file(path) == {
        "BLA125516|ORIG|1|n=226": "NCT00026312",
        "NDA019941|SUPPL|11|n=200": "NONE",
    }


# ---------------------------------------------------------------------------
# A biosimilar's section 14 is borrowed, so it is not judged
# ---------------------------------------------------------------------------


def _bio_unit(app: str, mesh: str, uid: str) -> dict[str, object]:
    return {
        "unit_id": uid, "application": app, "submission_type": "ORIG",
        "submission_no": "1", "approval_date": "2024-01-01", "stated_nct": "",
        "drug_mesh": mesh, "acronym": "STUDY 1", "enrollment": "766",
        "excerpt": "STUDY 1 was a randomized trial in 766 patients",
        "candidates": [{"nct_id": "NCT00267969", "enrollment": "766"}],
    }


def test_a_stem_resolved_drug_is_not_sent_to_a_judge(tmp_path: Path) -> None:
    """Eight ustekinumab biosimilars were all assigned NCT00267969 — a Stelara
    trial — and both judges agreed every time, because each label copies the
    originator's section 14 verbatim. The excerpt is truthful; the link is
    not. A 351(k) rests on its own PK and similarity data."""
    p = tmp_path / "units.jsonl"
    p.write_text(
        json.dumps(_bio_unit("BLA761373", "MESH:D000069549", "u1")) + "\n",
        encoding="utf-8",
    )
    assert identification.read_units(p, frozenset({"MESH:D000069549"})) == []


def test_an_ordinary_drug_is_still_judged(tmp_path: Path) -> None:
    p = tmp_path / "units.jsonl"
    p.write_text(
        json.dumps(_bio_unit("BLA125514", "MESH:C582435", "u1")) + "\n",
        encoding="utf-8",
    )
    assert len(identification.read_units(p, frozenset({"MESH:D000069549"}))) == 1


def test_with_no_borrowed_ids_nothing_is_excluded(tmp_path: Path) -> None:
    """The default must not change behaviour for callers that pass nothing."""
    p = tmp_path / "units.jsonl"
    p.write_text(
        json.dumps(_bio_unit("BLA761373", "MESH:D000069549", "u1")) + "\n",
        encoding="utf-8",
    )
    assert len(identification.read_units(p)) == 1


def test_borrowed_ids_reads_only_stem_rows(tmp_path: Path) -> None:
    m = tmp_path / "drug_map.tsv"
    m.write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "USTEKINUMAB-TTWE\tMESH:D000069549\tb1\tUstekinumab\tChemical\tstem\n"
        "PEMBROLIZUMAB\tMESH:C582435\tb2\tPembrolizumab\tChemical\texact\n",
        encoding="utf-8",
    )
    assert identification.borrowed_ids(m) == frozenset({"MESH:D000069549"})


def test_borrowed_ids_on_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert identification.borrowed_ids(tmp_path / "nope.tsv") == frozenset()
