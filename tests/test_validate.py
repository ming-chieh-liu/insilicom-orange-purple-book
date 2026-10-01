"""Tests for scoring the map against links the FDA states outright."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import validate


def _unit(unit_id: str, truth: str, candidate_ids: list[str]) -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "application": "BLA125514",
        "submission_type": "SUPPL",
        "submission_no": "35",
        "approval_date": "2018-08-20",
        "heading": "NSCLC",
        "acronym": "KEYNOTE-189",
        "enrollment": "616",
        "excerpt": "…616 patients…",
        "stated_nct": truth,
        "candidates": [
            {"nct_id": n, "enrollment": "616", "brief_title": n}
            for n in candidate_ids
        ],
    }


@pytest.fixture
def units(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.jsonl"
    rows = [
        # answer is in its own shortlist
        _unit("a", "NCT00000001", ["NCT00000001", "NCT00000002"]),
        # answer is not: unreachable by any judge
        _unit("b", "NCT00000003", ["NCT00000004"]),
        # no stated answer: not part of the gold set
        _unit("c", "", ["NCT00000005"]),
        # stated, but no candidates at all
        _unit("d", "NCT00000006", []),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Shortlist recall — P3's score, costing no agent calls
# ---------------------------------------------------------------------------


def test_recall_counts_only_units_with_a_known_answer(units: Path) -> None:
    assert validate.shortlist_recall(units).checked == 2


def test_recall_measures_whether_the_answer_was_offered(units: Path) -> None:
    """A judge cannot find what was never in its list, so this is the ceiling
    on everything P4 can do."""
    recall = validate.shortlist_recall(units)
    assert recall.present == 1
    assert recall.rate == 0.5


def test_recall_names_what_was_missed(units: Path) -> None:
    """So a shortlist failure can be told from a judging failure — the
    distinction CHECKMATE-648 hid for two commits."""
    recall = validate.shortlist_recall(units)
    assert any("NCT00000003" in example for example in recall.absent_examples)


def test_recall_respects_a_limit(units: Path) -> None:
    assert validate.shortlist_recall(units, limit=1).checked == 1


def test_recall_of_nothing_is_zero(tmp_path: Path) -> None:
    empty = tmp_path / "none.jsonl"
    empty.write_text("", encoding="utf-8")
    assert validate.shortlist_recall(empty).rate == 0.0


# ---------------------------------------------------------------------------
# Withholding the answer
# ---------------------------------------------------------------------------


def test_withholding_removes_the_answer_but_keeps_the_unit(
    units: Path, tmp_path: Path
) -> None:
    """The pipeline short-circuits on stated_nct, so it has to be cleared for
    the unit to reach a judge at all."""
    out = tmp_path / "withheld.jsonl"
    truth = validate.withhold(units, out, limit=10)
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert truth == {"a": "NCT00000001"}
    assert all("stated_nct" not in w or not w.get("stated_nct") for w in written)


def test_withholding_skips_units_whose_answer_was_never_offered(
    units: Path, tmp_path: Path
) -> None:
    """Unanswerable by construction: they are shortlist failures and belong to
    the recall number, not the judge's score."""
    truth = validate.withhold(units, tmp_path / "w.jsonl", limit=10)
    assert "b" not in truth


def test_withholding_respects_its_limit(units: Path, tmp_path: Path) -> None:
    assert len(validate.withhold(units, tmp_path / "w.jsonl", limit=0)) == 0


# ---------------------------------------------------------------------------
# Scoring the judge
# ---------------------------------------------------------------------------


def _verdicts(tmp_path: Path, answers: dict[str, str]) -> Path:
    path = tmp_path / "verdicts.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "unit_id": k,
                    "judge_a": v,
                    "judge_b": v,
                    "tiebreak": None,
                    "final": v,
                    "prompt_version": 1,
                }
            )
            for k, v in answers.items()
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_a_matching_answer_is_correct(tmp_path: Path) -> None:
    path = _verdicts(tmp_path, {"a": "NCT00000001"})
    precision, rows = validate.score({"a": "NCT00000001"}, path)
    assert (precision.correct, precision.wrong, precision.abstained) == (1, 0, 0)
    assert rows[0].correct


def test_a_different_answer_is_wrong(tmp_path: Path) -> None:
    path = _verdicts(tmp_path, {"a": "NCT00000009"})
    precision, _ = validate.score({"a": "NCT00000001"}, path)
    assert precision.wrong == 1


def test_an_abstention_is_neither(tmp_path: Path) -> None:
    """NONE is the absence of an answer, not a wrong one. Counting it as an
    error would push the judge toward guessing, which is the failure the whole
    task is built to avoid."""
    path = _verdicts(tmp_path, {"a": "NONE"})
    precision, _ = validate.score({"a": "NCT00000001"}, path)
    assert (precision.correct, precision.wrong, precision.abstained) == (0, 0, 1)
    assert precision.rate == 0.0


def test_precision_excludes_abstentions_from_its_denominator() -> None:
    assert validate.Precision(scored=10, correct=6, wrong=2, abstained=2).rate == 0.75


def test_the_abstention_rate_uses_every_unit() -> None:
    got = validate.Precision(scored=10, correct=6, wrong=2, abstained=2)
    assert got.abstention_rate == 0.2


def test_an_unjudged_unit_is_not_scored(tmp_path: Path) -> None:
    """Never score a verdict that was not returned."""
    path = _verdicts(tmp_path, {})
    precision, rows = validate.score({"a": "NCT00000001"}, path)
    assert precision.scored == 0 and rows == ()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_the_tier_breakdown_reads_the_map(tmp_path: Path) -> None:
    path = tmp_path / "map.tsv"
    path.write_text(
        "nct_id\tevidence_tier\tevidence_source\n"
        "NCT1\tA\tlabel_nct\n"
        "NCT2\tB\tlabel_description\n"
        "NCT3\tA\tlabel_nct\n",
        encoding="utf-8",
    )
    assert dict(validate.tier_breakdown(path))["A / label_nct"] == 2


def test_a_missing_map_yields_no_breakdown(tmp_path: Path) -> None:
    assert validate.tier_breakdown(tmp_path / "absent.tsv") == ()


def test_the_report_leads_with_the_ceiling() -> None:
    rendered = validate.render(validate.Recall(100, 54, ("x -> NCT1",)), (("A", 5),))
    assert "SHORTLIST RECALL" in rendered
    assert "54.0%" in rendered


# ---------------------------------------------------------------------------
# The gold set must not show the judge its own answer
# ---------------------------------------------------------------------------


def test_a_unit_whose_excerpt_prints_the_id_is_not_a_gold_case(
    tmp_path: Path,
) -> None:
    """2,192 of 3,352 eligible units carry an NCT id in the excerpt, because
    collapse() merges sentences and keeps the most informative one. Judging
    those measures copying an identifier, not identifying a trial."""
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "stated_nct": "NCT01866319",
                "excerpt": "investigated in KEYNOTE-006 (NCT01866319), a trial",
                "candidates": [{"nct_id": "NCT01866319", "enrollment": "834"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    truth = validate.withhold(units, tmp_path / "out.jsonl", 10)
    assert truth == {}


def test_a_unit_named_only_in_prose_is_a_gold_case(tmp_path: Path) -> None:
    """The label named the trial by acronym; the answer is known from a
    sibling sentence the judge never sees. This is the task P4 performs."""
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "stated_nct": "NCT01866319",
                "excerpt": "investigated in KEYNOTE-006, a randomized trial in "
                "834 patients",
                "candidates": [{"nct_id": "NCT01866319", "enrollment": "834"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    truth = validate.withhold(units, tmp_path / "out.jsonl", 10)
    assert truth == {"u1": "NCT01866319"}


def test_the_written_unit_does_not_carry_the_answer(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "stated_nct": "NCT01866319",
                "excerpt": "investigated in KEYNOTE-006, a randomized trial",
                "candidates": [{"nct_id": "NCT01866319", "enrollment": "834"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    validate.withhold(units, out, 10)
    written = out.read_text(encoding="utf-8").splitlines()[0]
    # The answer must not reach the judge by any route: not as a field, and
    # not inside the excerpt. The candidate list legitimately contains it —
    # that is the question — so only the prose is checked.
    assert "stated_nct" not in json.loads(written)
    assert "NCT01866319" not in json.loads(written)["excerpt"]


def test_the_sample_is_spread_rather_than_taken_from_the_head(
    tmp_path: Path,
) -> None:
    """candidates.jsonl is in application order, so the first N eligible units
    are one sponsor and one era. The sample must not be the file's head."""
    units = tmp_path / "units.jsonl"
    units.write_text(
        "\n".join(
            json.dumps(
                {
                    "unit_id": f"u{i:03d}",
                    "stated_nct": f"NCT{i:08d}",
                    "excerpt": "investigated in a randomized trial",
                    "candidates": [{"nct_id": f"NCT{i:08d}", "enrollment": "10"}],
                }
            )
            for i in range(100)
        )
        + "\n",
        encoding="utf-8",
    )
    truth = validate.withhold(units, tmp_path / "out.jsonl", 10)
    assert set(truth) != {f"u{i:03d}" for i in range(10)}


def test_the_sample_is_reproducible(tmp_path: Path) -> None:
    """Same input, same units — a precision figure has to be re-derivable."""
    units = tmp_path / "units.jsonl"
    units.write_text(
        "\n".join(
            json.dumps(
                {
                    "unit_id": f"u{i:03d}",
                    "stated_nct": f"NCT{i:08d}",
                    "excerpt": "investigated in a randomized trial",
                    "candidates": [{"nct_id": f"NCT{i:08d}", "enrollment": "10"}],
                }
            )
            for i in range(100)
        )
        + "\n",
        encoding="utf-8",
    )
    first = validate.withhold(units, tmp_path / "a.jsonl", 10)
    second = validate.withhold(units, tmp_path / "b.jsonl", 10)
    assert first == second


def test_shortlist_recall_scores_only_a_trials_earliest_appearance(tmp_path: Path) -> None:
    """D16: a later label repeating the trial is not a unit anyone judges."""
    path = tmp_path / "candidates.jsonl"
    rows = [
        {"unit_id": "A|ORIG|1|X", "application": "A", "approval_date": "2010-01-01",
         "stated_nct": "NCT1", "candidates": [{"nct_id": "NCT1"}]},
        {"unit_id": "A|SUPPL|2|X", "application": "A", "approval_date": "2012-01-01",
         "stated_nct": "NCT1", "candidates": [{"nct_id": "NCT9"}]},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    recall = validate.shortlist_recall(path)
    assert (recall.checked, recall.present) == (1, 1)


def test_withheld_units_are_earliest_appearances_only(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    rows = [
        {"unit_id": "A|ORIG|1|X", "application": "A", "approval_date": "2010-01-01",
         "stated_nct": "NCT00000001", "excerpt": "a trial", "candidates": [{"nct_id": "NCT00000001"}]},
        {"unit_id": "A|SUPPL|2|X", "application": "A", "approval_date": "2012-01-01",
         "stated_nct": "NCT00000001", "excerpt": "a trial", "candidates": [{"nct_id": "NCT00000001"}]},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    truth = validate.withhold(path, tmp_path / "units.jsonl", 10)
    assert truth == {"A|ORIG|1|X": "NCT00000001"}
    assert validate.PRECISION_DIR == "precision_d35"


def test_first_appearances_follow_moiety_groups_and_skip_generic_names() -> None:
    """D37: same moiety → one group; "Study 1" is not a trial's name."""
    rows: list[dict[str, object]] = [
        {"unit_id": "orig", "application": "A", "approval_date": "2011", "stated_nct": "NCT1"},
        {"unit_id": "newform", "application": "B", "approval_date": "2021", "stated_nct": "NCT1"},
        {"unit_id": "s1a", "application": "A", "approval_date": "2012", "acronym": "Study 1"},
        {"unit_id": "s1b", "application": "A", "approval_date": "2015", "acronym": "Study 1",
         "stated_nct": "NCT2"},
    ]
    assert validate.first_appearances(rows, {"A": "X", "B": "X"}) == {"orig", "s1b"}
    assert validate.first_appearances(rows, {}) == {"orig", "newform", "s1b"}


def test_recall_counts_the_literature_route_too(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    path.write_text(json.dumps({"unit_id": "u", "application": "A", "approval_date": "2020",
                                "stated_nct": "NCT1", "candidates": [],
                                "literature": [{"nct_id": "NCT1"}]}) + "\n", encoding="utf-8")
    got = validate.shortlist_recall(path)
    assert (got.checked, got.present) == (1, 1)
