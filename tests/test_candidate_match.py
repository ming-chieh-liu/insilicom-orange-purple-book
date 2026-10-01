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
    assert third.tier_id == "NDA000001|ORIG|1|TRIAL-1#T3@" + cm.input_digest(third)


def test_missing_label_fields_are_spelled_out() -> None:
    unit = {**_unit(1), "acronym": "", "enrollment": ""}
    tier = cm.build_tier(unit, 1)
    assert tier.trial_name == "(the label gives no name)"
    assert tier.enrollment == "(the label gives no count)"


def test_a_yes_in_the_first_tier_decides_with_the_highest_ranked_yes() -> None:
    unit = _unit(25)
    answers = _answers(unit, 1, {"NCT00000007", "NCT00000003"})
    decision = cm.decide(unit, answers)
    assert decision == cm.Decision(str(unit["unit_id"]), "NCT00000003", 1, 1,
                                   (cm.build_tier(unit, 1).tier_id,), "B-")
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
    written, refused = cm.record_decisions(decisions, allowed, path, already={"U4": ()})
    assert (written, refused) == (2, 1)
    verdicts = identification.read_verdicts(path)
    assert verdicts["U1"].final == "NCT00000001"
    assert verdicts["U1"].prompt_version == cm.IDENTIFY_VERSION
    assert verdicts["U3"].final == identification.NONE


def test_approval_line_tolerates_missing_fields() -> None:
    assert cm.approval_line({}) == "   · approved unknown"


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
    assert "#T1@" in tid

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
    assert "#T2@" in tid2
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


# ---- answers are bound to what the judge was shown (2026-09-30) -------------------

def test_a_changed_shortlist_is_judged_again() -> None:
    unit = _unit(12)
    answers = _answers(unit, 1, set())
    assert cm.next_tier(unit, answers) == 2
    changed = {**unit, "candidates": list(reversed(unit["candidates"]))}  # type: ignore[call-overload]
    assert cm.build_tier(changed, 1).tier_id != cm.build_tier(unit, 1).tier_id
    assert cm.next_tier(changed, answers) == 1
    assert cm.decide(changed, answers) is None


def test_a_changed_indication_is_judged_again_but_an_unseen_field_is_not() -> None:
    unit = {**_unit(3), "case": "C", "drug": "X", "indication_text": "Asthma"}
    first = cm.build_tier(unit, 1)
    assert cm.build_tier({**unit, "indication_text": "COPD"}, 1).tier_id != first.tier_id
    # The no-label prompt never shows the excerpt, so it does not change the tier.
    assert cm.build_tier({**unit, "excerpt": "other"}, 1).tier_id == first.tier_id


def test_legacy_tiers_are_rekeyed_from_their_stored_content(tmp_path: Path) -> None:
    unit = _unit(3)
    built = cm.build_tier(unit, 1)
    legacy = built._replace(tier_id=cm.tier_id_for(built.unit_id, 1))
    cm.write_tiers([legacy], tmp_path / cm.TIERS_FILE)
    old_answers = [a._replace(tier_id=legacy.tier_id) for a in _answers(unit, 1, {"NCT00000002"})]
    cm.append_answers(old_answers, tmp_path / cm.ANSWERS_FILE)
    assert cm.migrate_legacy_ids(tmp_path) == 3
    assert cm.migrate_legacy_ids(tmp_path) == 0
    assert list(cm.read_tiers(tmp_path / cm.TIERS_FILE)) == [built.tier_id]
    answers = cm.read_answers(tmp_path / cm.ANSWERS_FILE)
    decision = cm.decide(unit, answers)
    assert decision is not None and decision.final == "NCT00000002"


def test_a_decision_on_changed_tiers_is_recorded_anew(tmp_path: Path) -> None:
    path = tmp_path / "identify_verdicts.jsonl"
    first = cm.Decision("U1", "NCT00000001", 1, 1, ("U1#T1@aaa",))
    allowed = {"U1": {"NCT00000001", "NCT00000002"}}
    assert cm.record_decisions([first], allowed, path, {}) == (1, 0)
    already = cm.recorded_bases(path)
    assert already == {"U1": ("U1#T1@aaa",)}
    assert cm.record_decisions([first], allowed, path, already) == (0, 0)
    again = cm.Decision("U1", "NCT00000002", 1, 1, ("U1#T1@bbb",))
    assert cm.record_decisions([again], allowed, path, already) == (1, 0)
    assert identification.read_verdicts(path)["U1"].final == "NCT00000002"


def test_a_superseded_tier_is_not_batched_again(tmp_path: Path) -> None:
    unit = _unit(3)
    cm.prepare_round([unit], tmp_path)
    changed = {**unit, "excerpt": "A different excerpt."}
    report = cm.prepare_round([changed], tmp_path)
    assert "tiers awaiting answers                         1" in report
    batch = sorted((tmp_path / cm.BATCH_DIR).glob("round-02/*.ids"))[0].read_text().split("\n")
    assert batch[0] == cm.build_tier(changed, 1).tier_id


# ---- D37: literature tiers first, judged with abstracts ------------------------------

def _lit_unit(lit: int, reg: int, case: str = "B") -> dict[str, object]:
    unit = _unit(reg)
    unit["case"] = case
    unit["subsection"] = "In KEYNOTE-1, 500 patients were randomized."
    unit["drug"] = "KEYTRUDA (pembrolizumab)"
    unit["literature"] = [
        {"nct_id": f"NCT9{i:07d}", "brief_title": f"Lit {i}", "pmids": "11",
         "abstracts": [{"pmid": "11", "title": "Paper", "abstract": "We tested pembrolizumab."}]}
        for i in range(1, lit + 1)
    ]
    return unit


def test_literature_tiers_come_first_with_their_own_prompt() -> None:
    unit = _lit_unit(3, 12)
    assert cm.total_tiers(unit) == 3 and cm.literature_tiers(unit) == 1
    first, second = cm.build_tier(unit, 1), cm.build_tier(unit, 2)
    assert first.prompt == cm.LITERATURE_PROMPT
    assert first.candidate_ids == ("NCT90000001", "NCT90000002", "NCT90000003")
    assert "PMID 11 — Paper" in first.candidates and "We tested pembrolizumab." in first.candidates
    assert "<section_14>\nIn KEYNOTE-1, 500 patients were randomized.\n</section_14>" in first.label_block
    assert first.approval.startswith("KEYTRUDA (pembrolizumab) · ")
    assert second.prompt == cm.EXCERPT_PROMPT
    assert second.candidate_ids[0] == "NCT00000001"
    assert second.excerpt == "In KEYNOTE-1, 500 patients were randomized."


def test_a_literature_yes_stops_the_unit_before_the_shortlist() -> None:
    unit = _lit_unit(3, 12)
    answers = _answers(unit, 1, {"NCT90000002"})
    decision = cm.decide(unit, answers)
    assert decision is not None and decision.final == "NCT90000002" and decision.route == "B+"
    assert cm.next_tier(unit, answers) is None


def test_an_all_no_literature_tier_moves_to_the_registry() -> None:
    unit = _lit_unit(3, 12)
    answers = _answers(unit, 1, set())
    assert cm.next_tier(unit, answers) == 2
    answers += _answers(unit, 2, {"NCT00000004"})
    decision = cm.decide(unit, answers)
    assert decision is not None and decision.route == "B-" and decision.tier == 2


def test_case_c_and_d_literature_blocks_say_what_is_known() -> None:
    c = cm.build_tier({**_lit_unit(1, 0, "C"), "indication_text": "provides for asthma"}, 1)
    assert c.label_block.endswith("Approved for: provides for asthma")
    assert "section 14 heading" not in c.approval
    d = _lit_unit(2, 0, "D")
    assert cm.route_of(d, 1) == "D" and cm.total_tiers(d) == 1


def test_select_units_attaches_abstracts_and_keeps_literature_only_units(tmp_path: Path) -> None:
    from service import graph_trials

    unit = {**_lit_unit(1, 0, "D"), "unit_id": "NDA1|ORIG|1|drug_only"}
    for c in unit["literature"]:  # type: ignore[attr-defined]
        c.pop("abstracts")
    (tmp_path / "candidates.jsonl").write_text(json.dumps(unit) + "\n", encoding="utf-8")
    (tmp_path / graph_trials.ABSTRACTS_FILE).write_text(
        json.dumps({"pmid": "11", "title": "T", "abstract": "A"}) + "\n", encoding="utf-8")
    [got] = cm.select_units(tmp_path / "candidates.jsonl", tmp_path / "x", tmp_path / "y", None, "D")
    assert got["literature"][0]["abstracts"] == [{"pmid": "11", "title": "T", "abstract": "A"}]  # type: ignore[index]


def test_prompt_v3_shows_normalised_codes_beside_the_raw_text() -> None:
    """D39: the judge sees our MeSH coding next to the registry's own words."""
    block = cm.render_candidate(1, {
        "nct_id": "NCT1", "conditions": "NSCLC", "interventions": "MK-3475",
        "condition_codes": "Carcinoma, Non-Small-Cell Lung [MeSH D002289]",
        "intervention_codes": "pembrolizumab [MeSH C582435]",
    })
    lines = block.splitlines()
    at = lines.index("   Conditions: NSCLC")
    assert lines[at + 1] == "   Conditions (normalised): Carcinoma, Non-Small-Cell Lung [MeSH D002289]"
    assert lines[at + 3] == "   Interventions (normalised): pembrolizumab [MeSH C582435]"
    bare = cm.render_candidate(1, {"nct_id": "NCT1"})
    assert "normalised" not in bare
    assert (cm.PROMPT_VERSION, cm.NOLABEL_PROMPT_VERSION, cm.LITERATURE_PROMPT_VERSION) == (4, 4, 3)


def test_a_no_label_tier_shows_codes_for_drug_and_indication() -> None:
    unit: dict[str, object] = {"unit_id": "U", "case": "C", "drug": "BRUKINSA (zanubrutinib)",
            "drug_codes": "zanubrutinib [MeSH C000629551]",
            "indication_text": "Waldenstrom's macroglobulinemia",
            "indication_codes": "Waldenstrom Macroglobulinemia [MeSH D008258]",
            "candidates": [{"nct_id": "NCT1"}],
            "literature": [{"nct_id": "NCT2"}]}
    literature = cm.build_tier(unit, 1)
    assert "section 14 heading" not in literature.approval
    assert "Drug (normalised): zanubrutinib [MeSH C000629551]" in literature.approval
    registry = cm.build_tier(unit, 2)
    assert registry.drug.endswith("(normalised: zanubrutinib [MeSH C000629551])")
    assert registry.indication.endswith("(normalised: Waldenstrom Macroglobulinemia [MeSH D008258])")
