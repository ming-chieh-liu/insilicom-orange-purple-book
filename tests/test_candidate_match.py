"""Tests for P4 per-candidate matching (D33)."""

from __future__ import annotations

import json
from pathlib import Path

from service import candidate_match as cm
from service import identification


def _unit(n: int, unit_id: str = "NDA000001|ORIG|1|TRIAL-1") -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "application": "NDA000001",
        "submission_type": "ORIG",
        "submission_no": "1",
        "approval_date": "2020-01-01",
        "heading": "Asthma",
        "acronym": "TRIAL-1",
        "enrollment": "500",
        "excerpt": "A randomized trial in 500 patients.",
        "candidates": [
            {"nct_id": f"NCT{index:08d}", "brief_title": f"Study {index}",
             "lead_sponsor": "Acme", "primary_completion": "2019-01-01"}
            for index in range(1, n + 1)
        ],
    }


def _answers(unit: dict[str, object], tier: int, yes: set[str]) -> list[cm.CandidateAnswer]:
    built = cm.build_tier(unit, tier)
    return [
        cm.CandidateAnswer(built.tier_id, built.unit_id, tier, (tier - 1) * 10 + i + 1,
                           nct, cm.YES if nct in yes else cm.NO, None, None,
                           cm.YES if nct in yes else cm.NO)
        for i, nct in enumerate(built.candidate_ids)
    ]


def test_tiers_are_slices_of_ten_in_rank_order() -> None:
    unit = _unit(23)
    assert cm.tier_count(23) == 3
    third = cm.build_tier(unit, 3)
    assert third.candidate_ids == ("NCT00000021", "NCT00000022", "NCT00000023")
    assert third.candidates.startswith("21. NCT00000021")
    assert "Sponsor: Acme" in third.candidates
    assert "Primary completion: 2019-01-01" in third.candidates
    assert third.tier_id == "NDA000001|ORIG|1|TRIAL-1#T3"


def test_missing_label_fields_are_spelled_out() -> None:
    unit = {**_unit(1), "acronym": "", "enrollment": ""}
    tier = cm.build_tier(unit, 1)
    assert tier.trial_name == "(the label gives no name)"
    assert tier.enrollment == "(the label gives no count)"


def test_a_yes_in_the_first_tier_decides_with_the_highest_ranked_yes() -> None:
    unit = _unit(25)
    answers = _answers(unit, 1, {"NCT00000007", "NCT00000003"})
    decision = cm.decide(unit, answers)
    assert decision == cm.Decision(str(unit["unit_id"]), "NCT00000003", 1, 1)
    assert cm.next_tier(unit, answers) is None


def test_an_all_no_tier_moves_to_the_next() -> None:
    unit = _unit(25)
    answers = _answers(unit, 1, set())
    assert cm.decide(unit, answers) is None
    assert cm.next_tier(unit, answers) == 2


def test_every_tier_no_is_none() -> None:
    unit = _unit(12)
    answers = _answers(unit, 1, set()) + _answers(unit, 2, set())
    decision = cm.decide(unit, answers)
    assert decision is not None and decision.final == identification.NONE
    assert decision.tiers_judged == 2


def test_an_incomplete_tier_decides_nothing() -> None:
    unit = _unit(10)
    answers = _answers(unit, 1, {"NCT00000002"})[:5]
    assert cm.decide(unit, answers) is None
    assert cm.next_tier(unit, answers) == 1


def test_a_later_yes_is_read_only_after_earlier_tiers_are_no() -> None:
    unit = _unit(30)
    answers = _answers(unit, 1, set()) + _answers(unit, 2, {"NCT00000015"})
    decision = cm.decide(unit, answers)
    assert decision is not None and (decision.final, decision.tier) == ("NCT00000015", 2)


def test_sample_order_is_deterministic_and_limited() -> None:
    units = [_unit(1, f"NDA{i}|ORIG|1|X") for i in range(5)]
    first = cm.sample_order(units, 3)
    assert first == cm.sample_order(list(reversed(units)), 3)
    assert len(first) == 3
    assert len(cm.sample_order(units, None)) == 5


def test_judge_file_lines_parse_and_prose_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text(
        "NDA1|ORIG|1|n=226#T1 | NCT00000001 = Yes\n"
        "NDA1|ORIG|1|n=226#T1 | NCT00000002 = No\n"
        "I think the first one fits.\n",
        encoding="utf-8",
    )
    got = cm.read_judge_file(path)
    assert got == {
        ("NDA1|ORIG|1|n=226#T1", "NCT00000001"): "Yes",
        ("NDA1|ORIG|1|n=226#T1", "NCT00000002"): "No",
    }
    assert cm.read_judge_file(tmp_path / "missing.txt") == {}


def _tier_map(unit: dict[str, object]) -> dict[str, cm.TierUnit]:
    tier = cm.build_tier(unit, 1)
    return {tier.tier_id: tier}


def test_one_judge_mode_takes_judge_a() -> None:
    unit = _unit(2)
    tiers = _tier_map(unit)
    tid = next(iter(tiers))
    a = {(tid, "NCT00000001"): "No", (tid, "NCT00000002"): "Yes"}
    result = cm.combine(tiers, a, None, {})
    assert [x.final for x in result.recorded] == ["No", "Yes"]
    assert [x.rank for x in result.recorded] == [1, 2]
    assert result.incomplete_tiers == [] and result.agreement == (0, 0)


def test_two_judges_agree_or_need_a_third() -> None:
    unit = _unit(2)
    tiers = _tier_map(unit)
    tid = next(iter(tiers))
    a = {(tid, "NCT00000001"): "No", (tid, "NCT00000002"): "Yes"}
    b = {(tid, "NCT00000001"): "No", (tid, "NCT00000002"): "No"}
    pending = cm.combine(tiers, a, b, {})
    assert pending.recorded == [] and pending.incomplete_tiers == [tid]
    assert pending.tiebreak_needed == [f"{tid} | NCT00000002"]
    assert pending.agreement == (1, 2)
    settled = cm.combine(tiers, a, b, {(tid, "NCT00000002"): "Yes"})
    assert [x.final for x in settled.recorded] == ["No", "Yes"]
    assert settled.recorded[1].tiebreak == "Yes"


def test_a_tier_missing_an_answer_is_not_recorded() -> None:
    unit = _unit(2)
    tiers = _tier_map(unit)
    tid = next(iter(tiers))
    result = cm.combine(tiers, {(tid, "NCT00000001"): "No"}, None, {})
    assert result.recorded == [] and result.incomplete_tiers == [tid]
    two = cm.combine(tiers, {(tid, "NCT00000001"): "No", (tid, "NCT00000002"): "No"},
                     {(tid, "NCT00000001"): "No"}, {})
    assert two.recorded == [] and two.incomplete_tiers == [tid]


def test_tiers_and_answers_round_trip(tmp_path: Path) -> None:
    unit = _unit(3)
    tier = cm.build_tier(unit, 1)
    tiers_path = tmp_path / "tiers.jsonl"
    assert cm.write_tiers([tier], tiers_path) == 1
    assert cm.read_tiers(tiers_path)[tier.tier_id] == tier
    answers = _answers(unit, 1, {"NCT00000002"})
    path = tmp_path / "answers.jsonl"
    assert cm.append_answers(answers, path) == 3
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tier_id": "x", "prompt_version": 99}) + "\n")
    assert sorted(cm.read_answers(path)) == sorted(answers)


def test_decisions_are_validated_and_not_repeated(tmp_path: Path) -> None:
    path = tmp_path / "identify_verdicts.jsonl"
    decisions = [
        cm.Decision("U1", "NCT00000001", 1, 1),
        cm.Decision("U2", "NCT00000009", 1, 1),
        cm.Decision("U3", identification.NONE, 0, 2),
        cm.Decision("U4", "NCT00000004", 1, 1),
    ]
    allowed = {"U1": {"NCT00000001"}, "U2": {"NCT00000001"}, "U3": set(), "U4": {"NCT00000004"}}
    written, refused = cm.record_decisions(decisions, allowed, path, already={"U4"})
    assert (written, refused) == (2, 1)
    verdicts = identification.read_verdicts(path)
    assert verdicts["U1"].final == "NCT00000001"
    assert verdicts["U1"].prompt_version == cm.IDENTIFY_VERSION
    assert verdicts["U3"].final == identification.NONE


def test_approval_line_tolerates_missing_fields() -> None:
    assert cm.approval_line({}) == "   · approved unknown · section 14 heading: none"


def _write_candidates(path: Path, units: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(u) + "\n" for u in units), encoding="utf-8")


def test_a_round_trip_through_prepare_combine_and_record(tmp_path: Path) -> None:
    adj = tmp_path / "adj"
    cands = tmp_path / "candidates.jsonl"
    unit = _unit(12)
    _write_candidates(cands, [unit])
    units = cm.select_units(cands, tmp_path / "none.tsv", tmp_path / "none.tsv", None)
    assert [u["unit_id"] for u in units] == [unit["unit_id"]]

    report = cm.prepare_round(units, adj)
    assert "tiers written this round (new)                 1" in report
    batch = (adj / cm.BATCH_DIR / "round-01" / "batch-001.ids").read_text().split()
    tid = batch[0]
    assert tid.endswith("#T1")

    raw = adj / cm.RAW_DIR / "round-01"
    raw.mkdir(parents=True)
    ids = cm.build_tier(unit, 1).candidate_ids
    (raw / "batch-001-A.txt").write_text(
        "".join(f"{tid} | {nct} = No\n" for nct in ids), encoding="utf-8"
    )
    assert "candidate answers recorded                    10" in cm.combine_round(adj, 1)
    # The combine is idempotent: nothing recorded twice.
    assert "candidate answers recorded                     0" in cm.combine_round(adj, 1)

    second = cm.prepare_round(units, adj)
    assert "tiers written this round (new)                 1" in second
    tid2 = (adj / cm.BATCH_DIR / "round-02" / "batch-001.ids").read_text().split()[0]
    assert tid2.endswith("#T2")
    (raw / "batch-002-A.txt").write_text(
        f"{tid2} | NCT00000011 = No\n{tid2} | NCT00000012 = Yes\n", encoding="utf-8"
    )
    cm.combine_round(adj, 1)
    recorded = cm.record_round(units, adj)
    assert "identified a trial                         1  (T2: 1)" in recorded
    assert "newly written to identify_verdicts             1" in recorded
    verdict = identification.read_verdicts(adj / "identify_verdicts.jsonl")
    assert verdict[str(unit["unit_id"])].final == "NCT00000012"
    # Recording again writes nothing new, and a fully decided set prepares nothing.
    assert "newly written to identify_verdicts             0" in cm.record_round(units, adj)
    assert "batch files                                    0" in cm.prepare_round(units, adj)


def test_two_judge_combine_writes_the_tiebreak_list(tmp_path: Path) -> None:
    adj = tmp_path / "adj"
    unit = _unit(1)
    cm.prepare_round([unit], adj)
    tid = cm.build_tier(unit, 1).tier_id
    raw = adj / cm.RAW_DIR / "round-01"
    raw.mkdir(parents=True)
    (raw / "batch-001-A.txt").write_text(f"{tid} | NCT00000001 = Yes\n", encoding="utf-8")
    (raw / "batch-001-B.txt").write_text(f"{tid} | NCT00000001 = No\n", encoding="utf-8")
    report = cm.combine_round(adj, 2)
    assert "0/1  0.0%" in report
    assert (adj / cm.TIEBREAK_FILE).read_text() == f"{tid} | NCT00000001\n"
    (raw / "tiebreak-001-C.txt").write_text(f"{tid} | NCT00000001 = Yes\n", encoding="utf-8")
    assert "candidate answers recorded                     1" in cm.combine_round(adj, 2)
    assert (adj / cm.TIEBREAK_FILE).read_text() == ""


# --- D35: cases C and D, judged with the no-label prompt ---------------------


def _case_unit(case: str, n: int = 3) -> dict[str, object]:
    return {
        **_unit(n, f"NDA000001|SUPPL|7|{'indication' if case == 'C' else 'drug_only'}"),
        "case": case, "excerpt": "", "acronym": "", "enrollment": "",
        "indication_text": "provides for asthma" if case == "C" else "",
        "drug": "DRUGX",
        "candidates": [
            {"nct_id": f"NCT{i:08d}", "brief_title": "t",
             "found_under": "Asthma" if case == "D" else ""}
            for i in range(1, n + 1)
        ],
    }


def test_a_no_label_tier_carries_drug_indication_and_date() -> None:
    c = cm.build_tier(_case_unit("C"), 1)
    assert (c.prompt, c.drug, c.indication, c.approval_date) == (
        cm.NOLABEL_PROMPT, "DRUGX", "provides for asthma", "2020-01-01")
    d = cm.build_tier(_case_unit("D"), 1)
    assert d.indication == "not recorded" and "Named in literature on: Asthma" in d.candidates
    b = cm.build_tier(_unit(2), 1)
    assert (b.prompt, b.drug, b.indication) == (cm.EXCERPT_PROMPT, "", "")


def test_select_units_takes_cases_c_and_d_from_the_candidates_file(tmp_path: Path) -> None:
    path = tmp_path / "candidates.jsonl"
    empty = {**_case_unit("D"), "unit_id": "X|ORIG|1|drug_only", "candidates": []}
    _write_candidates(path, [_unit(2), _case_unit("C"), _case_unit("D"), empty])
    none = tmp_path / "none.tsv"
    assert sorted(str(u["case"]) for u in cm.select_units(path, none, none, None, case="CD")) == [
        "C", "D"]
    assert [u["case"] for u in cm.select_units(path, none, none, None, case="C")] == ["C"]
    assert len(cm.select_units(path, none, none, None)) == 1


def test_no_label_tiers_get_their_own_batch_files_and_prompt_stamp(tmp_path: Path) -> None:
    adj = tmp_path / "adj"
    units = [_unit(2), _case_unit("C", 2)]
    cm.prepare_round(units, adj)
    round_dir = adj / cm.BATCH_DIR / "round-01"
    assert sorted(p.name for p in round_dir.iterdir()) == ["batch-001.ids", "nolabel-001.ids"]
    tid = (round_dir / "nolabel-001.ids").read_text().split("\n")[0]
    assert cm.read_tiers(adj / cm.TIERS_FILE)[tid].prompt == cm.NOLABEL_PROMPT
    raw = adj / cm.RAW_DIR / "round-01"
    raw.mkdir(parents=True)
    (raw / "nolabel-001-A.txt").write_text(
        f"{tid} | NCT00000001 = No\n{tid} | NCT00000002 = Yes\n", encoding="utf-8")
    cm.combine_round(adj, 1)
    rows = [json.loads(line) for line in (adj / cm.ANSWERS_FILE).read_text().splitlines()]
    assert {(r["prompt"], r["prompt_version"]) for r in rows} == {
        (cm.NOLABEL_PROMPT, cm.NOLABEL_PROMPT_VERSION)}
    assert len(cm.read_answers(adj / cm.ANSWERS_FILE)) == 2
    assert "identified a trial                         1" in cm.record_round(units, adj)
