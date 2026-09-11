"""Tests for section-14 polarity adjudication: units in, verdicts out.

This module is the deterministic half of the adjudication step — it prepares
what the agents read and applies what they decide, and makes no model calls. So
everything here is testable without an agent.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest

from config.settings import RawData
from service import adjudication as adj

# --- the answer vocabulary --------------------------------------------------


def test_a_judge_may_answer_only_supporting_or_not_supporting() -> None:
    """Binary by decision (2026-09-11): the deliverable asks one question."""
    assert adj.CURRENT_ANSWERS == (adj.SUPPORTING, adj.NOT_SUPPORTING)


def test_v1_answers_still_read_and_mean_not_supporting() -> None:
    """Prompt v1 offered NEGATIVE and UNCLEAR before the binary decision.

    Verdicts recorded under it cost real agent calls, so they stay readable
    rather than being thrown away and re-bought.
    """
    assert set(adj.LEGACY_ANSWERS) <= set(adj.ANSWERS)
    for legacy in adj.LEGACY_ANSWERS:
        assert adj._POLARITY_OF[legacy] == adj.NOT_SUPPORTING_POLARITY


def test_the_prompt_version_is_recorded_so_verdicts_stay_comparable() -> None:
    """Two verdicts answering differently worded questions are not the same fact."""
    assert adj.PROMPT_VERSION == 2


# --- splitting section 14 ---------------------------------------------------


def test_a_section_with_no_subsections_is_one_segment() -> None:
    """45% of labels number nothing; the whole section is then the unit."""
    assert adj._segments("14 CLINICAL STUDIES The efficacy of X …") == [
        "14 CLINICAL STUDIES The efficacy of X …"
    ]


def test_subsection_headings_split_the_section() -> None:
    text = "14.1 Melanoma Aaa bbb. 14.2 Lung Ccc ddd. 14.3 Lack of Efficacy Eee."
    segments = adj._segments(text)
    assert len(segments) == 3
    assert segments[0].startswith("14.1 Melanoma")
    assert segments[2].startswith("14.3 Lack of Efficacy")


def test_text_before_the_first_heading_is_kept_as_its_own_segment() -> None:
    """12 links name their trial in that preamble; dropping it would lose them."""
    segments = adj._segments("14 CLINICAL STUDIES preamble NCT00000001. 14.1 Melanoma Aaa.")
    assert len(segments) == 2
    assert "preamble" in segments[0]


@pytest.mark.parametrize("text", ["see 2.14.1 for details", "dose was 14.2 mg daily"])
def test_a_number_that_is_not_a_heading_does_not_split(text: str) -> None:
    """The lookbehind and the capitalised-word lookahead exist for these."""
    assert adj._segments(text) == [text]


# --- building units ---------------------------------------------------------


def _corpus(tmp_path: Path, labels: list[dict[str, object]]) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "label").mkdir(parents=True)
    with zipfile.ZipFile(root / "label" / "drug-label-0001-of-0014.json.zip", "w") as zf:
        zf.writestr("d.json", json.dumps({"results": labels}))
    return RawData(root=root)


def test_a_unit_is_one_trial_in_one_segment(tmp_path: Path) -> None:
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["14.1 Melanoma NCT00000001 worked. 14.2 Lung NCT00000002 worked."],
    }])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001"), ("NCT00000002", "NDA000001")})
    assert {u.unit_id for u in units} == {
        "NCT00000001|NDA000001|0", "NCT00000002|NDA000001|1",
    }
    assert all("NCT" in u.text for u in units)


def test_only_segments_naming_the_trial_are_sent(tmp_path: Path) -> None:
    """If 14.2 never names it, judging 14.2 would spend tokens to learn nothing."""
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["14.1 Melanoma NCT00000001 worked. 14.2 Lung nothing here."],
    }])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001")})
    assert len(units) == 1
    assert units[0].segment == 0


def test_a_trial_in_several_segments_yields_several_units(tmp_path: Path) -> None:
    """Tyvaso cites NCT00147199 under both an efficacy and a follow-up heading."""
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["14.1 PAH NCT00000001 worked. 14.2 Long-term NCT00000001 again."],
    }])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001")})
    assert {u.segment for u in units} == {0, 1}


def test_a_label_naming_two_applications_produces_units_for_both(
    tmp_path: Path,
) -> None:
    """openfda.application_number is an ARRAY; 664 labels name more than one."""
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001", "BLA000002"]},
        "clinical_studies": ["14.1 Aaa NCT00000001 worked."],
    }])
    units = adj.extract_units(
        data, {("NCT00000001", "NDA000001"), ("NCT00000001", "BLA000002")}
    )
    assert {u.application for u in units} == {"NDA000001", "BLA000002"}


def test_identical_repeated_text_is_judged_once(tmp_path: Path) -> None:
    """522 labels repeat their whole section in a second array element.

    Joining then splitting yields the same subsection twice; judging one text
    twice buys nothing and costs an agent call.
    """
    section = "14.1 Melanoma NCT00000001 worked."
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": [section, section],
    }])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001")})
    assert len(units) == 1


def test_a_trial_we_did_not_ask_about_is_not_judged(tmp_path: Path) -> None:
    """Units judge exactly what links.tsv emitted, not every id in the text."""
    data = _corpus(tmp_path, [{
        "set_id": "s1",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["14.1 Aaa NCT00000001 and NCT00000099 both appear."],
    }])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001")})
    assert {u.nct_id for u in units} == {"NCT00000001"}


def test_a_label_with_no_usable_application_is_skipped(tmp_path: Path) -> None:
    data = _corpus(tmp_path, [
        {"set_id": "s1", "openfda": {}, "clinical_studies": ["14.1 Aaa NCT00000001."]},
        {"set_id": "s2", "openfda": {"application_number": ["M020"]},
         "clinical_studies": ["14.1 Aaa NCT00000001."]},
    ])
    assert adj.extract_units(data, {("NCT00000001", "NDA000001")}) == ()


def test_a_pilot_puts_the_calibration_cases_first() -> None:
    """A pilot that never sees a known negative proves nothing."""
    units = [
        adj.JudgementUnit("z|z|0", "NCT99999999", "NDA999999", 0, "t"),
        adj.JudgementUnit(
            adj.unit_id_for(*adj.CALIBRATION[0], 5), *adj.CALIBRATION[0], 5, "t"
        ),
    ]
    first = adj.prioritise(units, limit=1)[0]
    assert (first.nct_id, first.application) == adj.CALIBRATION[0]


def test_prioritise_with_no_limit_keeps_everything() -> None:
    units = [adj.JudgementUnit(f"n{i}|a|0", f"NCT0000000{i}", "a", 0, "t") for i in range(3)]
    assert len(adj.prioritise(units, limit=None)) == 3


def test_units_round_trip_through_the_work_file(tmp_path: Path) -> None:
    unit = adj.JudgementUnit("NCT00000001|NDA000001|2", "NCT00000001", "NDA000001", 2, "text")
    path = tmp_path / "units.jsonl"
    assert adj.write_units([unit], path) == 1
    row = json.loads(path.read_text().splitlines()[0])
    assert row == unit.as_dict()


# --- reading a judge's answers ---------------------------------------------


def test_a_judges_answer_file_parses(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("NCT00000001|NDA000001|0 = SUPPORTING\nNCT00000002|NDA000001|1 = NOT_SUPPORTING\n")
    assert adj.read_judge_file(path) == {
        "NCT00000001|NDA000001|0": "SUPPORTING",
        "NCT00000002|NDA000001|1": "NOT_SUPPORTING",
    }


def test_a_judge_that_wrapped_its_output_in_prose_is_noticed_not_half_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Real judges did this: line numbers, preambles, markdown.

    One returned bare line numbers instead of unit ids, which is unusable — so
    a line that does not match is skipped loudly, never guessed at.
    """
    path = tmp_path / "a.txt"
    path.write_text(
        "Here are my answers:\n"
        "1 NCT00000001|NDA000001|0 = SUPPORTING\n"
        "NCT00000002|NDA000001|1 = SUPPORTING\n"
        "42 = SUPPORTING\n"
    )
    with caplog.at_level(logging.WARNING):
        answers = adj.read_judge_file(path)
    # Only the well-formed line survives; the bare number parses as an id and is
    # the caller's problem to notice, so it is reported in the combine step.
    assert "NCT00000002|NDA000001|1" in answers
    assert "NCT00000001|NDA000001|0" not in answers
    assert caplog.records


def test_an_answer_outside_the_vocabulary_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "a.txt"
    path.write_text("NCT00000001|NDA000001|0 = MAYBE\n")
    with caplog.at_level(logging.WARNING):
        assert adj.read_judge_file(path) == {}
    assert len(caplog.records) == 1


def test_a_missing_judge_file_warns_and_yields_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert adj.read_judge_file(Path("/nonexistent/a.txt")) == {}
    assert len(caplog.records) == 1


# --- combining two judges ---------------------------------------------------


def test_two_judges_agreeing_make_a_verdict() -> None:
    rows, needs, missing = adj.combine({"u": "SUPPORTING"}, {"u": "SUPPORTING"}, {})
    assert rows == [{"unit_id": "u", "judge_a": "SUPPORTING", "judge_b": "SUPPORTING",
                     "tiebreak": None}]
    assert (needs, missing) == ([], [])


def test_two_judges_disagreeing_wait_for_a_third() -> None:
    """Do not average, do not re-run, do not decide it yourself."""
    rows, needs, _ = adj.combine({"u": "SUPPORTING"}, {"u": "NOT_SUPPORTING"}, {})
    assert rows == []
    assert needs == ["u"]


def test_the_third_judge_settles_it() -> None:
    rows, needs, _ = adj.combine(
        {"u": "SUPPORTING"}, {"u": "NOT_SUPPORTING"}, {"u": "NOT_SUPPORTING"}
    )
    assert rows[0]["tiebreak"] == "NOT_SUPPORTING"
    assert needs == []


def test_one_judge_alone_is_a_sample_not_a_measurement() -> None:
    """A unit only becomes a verdict when both judges answered it."""
    rows, needs, missing = adj.combine({"u": "SUPPORTING"}, {}, {})
    assert (rows, needs, missing) == ([], [], ["u"])


# --- recording verdicts -----------------------------------------------------


def test_a_verdict_is_written_with_its_provenance(tmp_path: Path) -> None:
    path = tmp_path / "v.jsonl"
    written, refused = adj.record_verdicts(
        [{"unit_id": "NCT00000001|NDA000001|0", "judge_a": "SUPPORTING",
          "judge_b": "SUPPORTING", "tiebreak": None}], path
    )
    assert (written, refused) == (1, 0)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["final"] == "SUPPORTING"
    assert row["prompt_version"] == adj.PROMPT_VERSION
    assert row["nct_id"] == "NCT00000001" and row["application"] == "NDA000001"
    assert row["decided"]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        ({"unit_id": "not-a-unit-id", "judge_a": "SUPPORTING", "judge_b": "SUPPORTING"},
         "malformed unit id"),
        ({"unit_id": "n|a|0", "judge_a": "MAYBE", "judge_b": "SUPPORTING"},
         "answer outside the vocabulary"),
        ({"unit_id": "n|a|0", "judge_a": "SUPPORTING", "judge_b": "NOT_SUPPORTING"},
         "disagreement with no tiebreak"),
        ({"unit_id": "n|a|0", "judge_a": "SUPPORTING", "judge_b": "SUPPORTING",
          "tiebreak": "NOT_SUPPORTING"}, "tiebreak where judges agreed"),
        ({"unit_id": "n|a|0", "judge_a": "SUPPORTING", "judge_b": "NOT_SUPPORTING",
          "tiebreak": "MAYBE"}, "tiebreak outside the vocabulary"),
    ],
)
def test_a_verdict_that_cannot_be_read_is_refused_not_guessed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, row: dict[str, object], why: str
) -> None:
    """Writing a guess would put a fabricated judgement into a file that is
    treated as ground truth from then on."""
    path = tmp_path / "v.jsonl"
    with caplog.at_level(logging.WARNING):
        written, refused = adj.record_verdicts([row], path)
    assert (written, refused) == (0, 1), why
    assert caplog.records


def test_a_tiebroken_verdict_records_the_third_judges_answer(tmp_path: Path) -> None:
    """When the first two disagree, the third judge's answer is the final one."""
    path = tmp_path / "v.jsonl"
    written, refused = adj.record_verdicts(
        [{"unit_id": "NCT00000001|NDA000001|0", "judge_a": "SUPPORTING",
          "judge_b": "NOT_SUPPORTING", "tiebreak": "NOT_SUPPORTING"}], path
    )
    assert (written, refused) == (1, 0)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["final"] == "NOT_SUPPORTING"
    assert row["tiebreak"] == "NOT_SUPPORTING"
    assert adj.read_verdicts(path)["NCT00000001|NDA000001|0"].needed_tiebreak is True


def test_a_label_with_no_section_14_is_skipped(tmp_path: Path) -> None:
    """82% of labels have no section 14; they are read and passed over."""
    data = _corpus(tmp_path, [
        {"set_id": "s1", "openfda": {"application_number": ["NDA000001"]}},
        {"set_id": "s2", "openfda": {"application_number": ["NDA000001"]},
         "clinical_studies": ["14.1 Aaa NCT00000001 worked."]},
    ])
    units = adj.extract_units(data, {("NCT00000001", "NDA000001")})
    assert len(units) == 1


def test_a_blank_line_in_a_judge_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("NCT00000001|NDA000001|0 = SUPPORTING\n\n   \n")
    assert adj.read_judge_file(path) == {"NCT00000001|NDA000001|0": "SUPPORTING"}


def test_the_last_verdict_for_a_unit_wins(tmp_path: Path) -> None:
    """Append-only: a re-judgement corrects an earlier one without rewriting."""
    path = tmp_path / "v.jsonl"
    adj.record_verdicts([{"unit_id": "n|a|0", "judge_a": "SUPPORTING",
                          "judge_b": "SUPPORTING", "tiebreak": None}], path)
    adj.record_verdicts([{"unit_id": "n|a|0", "judge_a": "NOT_SUPPORTING",
                          "judge_b": "NOT_SUPPORTING", "tiebreak": None}], path)
    verdicts = adj.read_verdicts(path)
    assert len(verdicts) == 1
    assert verdicts["n|a|0"].final == "NOT_SUPPORTING"
    assert len(path.read_text().splitlines()) == 2  # history kept


def test_a_malformed_verdict_line_is_skipped_on_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "v.jsonl"
    path.write_text(
        '{"unit_id": "n|a|0", "final": "SUPPORTING"}\n'
        "{not json\n"
        '{"unit_id": "n|b|0", "final": "NOPE"}\n'
        "\n"
    )
    with caplog.at_level(logging.WARNING):
        verdicts = adj.read_verdicts(path)
    assert set(verdicts) == {"n|a|0"}
    assert len(caplog.records) == 2


def test_a_missing_verdict_file_is_normal(tmp_path: Path) -> None:
    """The default state of the world: nobody has judged anything yet."""
    assert adj.read_verdicts(tmp_path / "absent.jsonl") == {}


# --- polarity ---------------------------------------------------------------


def _verdict(unit_id: str, final: str) -> adj.Verdict:
    nct, application, _ = unit_id.split("|")
    return adj.Verdict(unit_id, nct, application, final, final, None, final)


def test_an_unjudged_pair_is_unjudged_not_refuted() -> None:
    assert adj.polarity_of("NCT00000001", "NDA000001", {}) == adj.UNJUDGED


def test_any_supporting_segment_makes_the_pair_supporting() -> None:
    """A label may describe one trial under both efficacy and safety headings.

    Tyvaso's NCT00147199 is judged supporting in 14.1 and not-supporting in
    14.2; the efficacy mention is the one that answers our question.
    """
    verdicts = {
        "NCT00000001|NDA000001|0": _verdict("NCT00000001|NDA000001|0", "NOT_SUPPORTING"),
        "NCT00000001|NDA000001|1": _verdict("NCT00000001|NDA000001|1", "SUPPORTING"),
    }
    assert adj.polarity_of("NCT00000001", "NDA000001", verdicts) == adj.SUPPORTING_POLARITY


def test_without_a_supporting_segment_the_pair_is_not_supporting() -> None:
    verdicts = {
        "NCT00000001|NDA000001|0": _verdict("NCT00000001|NDA000001|0", "NOT_SUPPORTING"),
    }
    assert (
        adj.polarity_of("NCT00000001", "NDA000001", verdicts)
        == adj.NOT_SUPPORTING_POLARITY
    )


def test_a_verdict_for_another_application_does_not_leak() -> None:
    """The same trial supports different approvals differently."""
    verdicts = {
        "NCT00000001|NDA000001|0": _verdict("NCT00000001|NDA000001|0", "SUPPORTING"),
    }
    assert adj.polarity_of("NCT00000001", "BLA000002", verdicts) == adj.UNJUDGED


def test_summarise_counts_finals_and_tiebreaks() -> None:
    verdicts = {
        "a|x|0": _verdict("a|x|0", "SUPPORTING"),
        "b|x|0": _verdict("b|x|0", "NOT_SUPPORTING"),
        "c|x|0": adj.Verdict("c|x|0", "c", "x", "SUPPORTING", "NOT_SUPPORTING",
                             "SUPPORTING", "SUPPORTING"),
    }
    rows = dict(adj.summarise(verdicts))
    assert rows["SUPPORTING"] == 2
    assert rows["NOT_SUPPORTING"] == 1
    assert rows["needed a tiebreak"] == 1


# --- the JSONL reader -------------------------------------------------------


def test_json_lines_skips_what_it_cannot_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "j.jsonl"
    path.write_text('{"a": 1}\n\n{bad\n"not an object"\n{"b": 2}\n')
    with caplog.at_level(logging.WARNING):
        rows = list(adj.iter_json_lines(path))
    assert rows == [{"a": 1}, {"b": 2}]
    assert caplog.records


def test_json_lines_warns_on_a_missing_file(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert list(adj.iter_json_lines(Path("/nonexistent/j.jsonl"))) == []
    assert len(caplog.records) == 1
