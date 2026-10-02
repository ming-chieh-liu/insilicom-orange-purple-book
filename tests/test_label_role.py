"""D28: every label link judged for its role on its own submission's label."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import assemble, label_role
from service.label_role import LinkTarget
from source import label_pdf

_BODY = (
    "14 CLINICAL STUDIES\n"
    "14.1 Lipids\nIn a trial of 1,485 patients fenofibrate lowered LDL.\n"
    "14.2 Lack of Efficacy in Cardiovascular Outcomes Trials\n"
    "The ACCORD Lipid trial (NCT00000620) randomized 5518 patients and showed no benefit.\n"
)


def test_label_stated_trials_become_targets_at_their_earliest_submission() -> None:
    """D46: units come from S4's label rows, one per (application, stated NCT)."""
    rows = [{"application": "NDA1", "submission_type": "SUPPL", "submission_no": "10",
             "nct_id": "NCT1", "acronym": "ACCORD", "heading": "Lipids", "enrollment": "5518"},
            {"application": "NDA1", "submission_type": "SUPPL", "submission_no": "9",
             "nct_id": "NCT1", "acronym": "ACCORD", "heading": "Lipids", "enrollment": "5518"},
            {"application": "NDA1", "submission_type": "SUPPL", "submission_no": "9",
             "nct_id": "NCT2", "acronym": "", "heading": "", "heading_full": "Lipids, full",
             "enrollment": ""}]
    targets = label_role.targets_from_label(rows, {"NDA1|SUPPL|9": "2010", "NDA1|SUPPL|10": "2010"})
    assert targets == [LinkTarget("NDA1|SUPPL|9", "NCT1", "ACCORD", "Lipids", "5518"),
                       LinkTarget("NDA1|SUPPL|9", "NCT2", "", "Lipids, full", "")]

def test_passages_find_the_trial_under_its_own_subsection() -> None:
    text = label_role.passages(_BODY, LinkTarget("k", "NCT00000620", "ACCORD Lipid", "", "5518"))
    assert text.startswith("[14.2 Lack of Efficacy in Cardiovascular Outcomes Trials]")
    assert "showed no benefit" in text


def test_a_count_is_searched_only_where_the_id_sentence_writes_it() -> None:
    """1,485 is written nowhere near NCT9, so it says nothing about that trial."""
    assert label_role.passages(_BODY, LinkTarget("k", "NCT9", "n=1485", "", "1485")) == ""
    body = ("14 CLINICAL STUDIES\n14.1 Lipids\nTrial A (NCT00000009) enrolled 1,485 patients."
            + " Filler." * 300 + " Of the 1485 patients, most finished.\n")
    text = label_role.passages(body, LinkTarget("k", "NCT00000009", "", "", "1485"))
    assert text.count("[14.1 Lipids]") == 2 and "most finished" in text


def test_a_name_s4_paired_wrongly_is_neither_shown_nor_searched() -> None:
    """The figure caption names KEYNOTE-189; the id belongs to Study JMDB."""
    body = ("14 CLINICAL STUDIES\n14.1 NSCLC\nKEYNOTE-189 results." + " Filler." * 300
            + " Figure 1: Survival in KEYNOTE-189 *Based on the final analysis The "
              "efficacy of pemetrexed was evaluated in Study JMDB (NCT00087711).\n")
    target = LinkTarget("A|ORIG|1", "NCT00087711", "KEYNOTE-189", "NSCLC", "")
    assert label_role.name_beside_id(body, target) == ""
    [unit] = label_role.build_units([target], {"A|ORIG|1": body}, {}).units
    assert unit["target"] == "the study registered as NCT00087711"
    assert unit["text"].count("[14.1 NSCLC]") == 1 and "KEYNOTE-189 results" not in unit["text"]


def test_overlapping_windows_are_merged_into_one_passage() -> None:
    body = "14 CLINICAL STUDIES\n14.1 Lung\nStudy KEYNOTE-189 (NCT02578680) enrolled 616 patients."
    text = label_role.passages(body, LinkTarget("k", "NCT02578680", "KEYNOTE-189", "", "616"))
    assert text.count("[14.1 Lung]") == 1 and text.count("enrolled") == 1


def test_an_unnamed_trial_falls_back_to_its_subsection() -> None:
    text = label_role.passages(_BODY, LinkTarget("k", "NCT9", "Lipids", "Lipids", ""))
    assert text.startswith("[14.1 Lipids] 14.1 Lipids In a trial")


def test_passages_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label_role, "MAX_PASSAGES", 2)
    monkeypatch.setattr(label_role, "WINDOW", 5)
    body = "14 CLINICAL STUDIES\n" + " filler text here ".join(["NCT7"] * 6)
    assert label_role.passages(body, LinkTarget("k", "NCT7", "", "", "")).count("[14") == 2


def test_units_are_built_or_dropped_with_a_reason() -> None:
    targets = [
        LinkTarget("A|ORIG|1", "NCT00000620", "ACCORD Lipid", "", ""),
        LinkTarget("A|ORIG|1", "NCT5", "ZETA-9", "Nowhere", ""),
        LinkTarget("B|ORIG|1", "NCT6", "X", "", ""),
    ]
    report = label_role.build_units(targets, {"A|ORIG|1": _BODY}, {"A|ORIG|1": "APPROVAL"})
    assert report.outcomes == {"built": 1, "trial_not_found_in_section_14": 1,
                               "no_label_text": 1}
    [unit] = report.units
    assert unit["unit_id"] == "A|ORIG|1|NCT00000620" and unit["approval"] == "APPROVAL"
    assert unit["target"] == "ACCORD Lipid (registry id NCT00000620)"
    assert "built" in label_role.render_units(report, Path("u.jsonl"))


def test_letter_sentences_are_read(tmp_path: Path) -> None:
    assert label_role.read_sentences(tmp_path) == {}
    (tmp_path / "letter_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsentence\n"
        "A\tORIG\t1\tprovides for X\nB\tORIG\t1\t\n", encoding="utf-8")
    assert label_role.read_sentences(tmp_path) == {"A|ORIG|1": "provides for X"}


def test_the_judging_round_trip(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text("\n".join(json.dumps({"unit_id": u}) for u in
                               ("A|ORIG|1|NCT1", "BLA125514|SUPPL|35|NCT02578680")) + "\n",
                     encoding="utf-8")
    batches, raw, verdicts = tmp_path / "b", tmp_path / "r", tmp_path / "v.jsonl"
    prepared = label_role.prepare(units, batches, verdicts)
    [batch] = prepared.new_batches
    assert batch.read_text(encoding="utf-8").splitlines()[0] == "BLA125514|SUPPL|35|NCT02578680"
    raw.mkdir()
    stem = batch.name.removesuffix(".ids")
    (raw / f"{stem}-A.txt").write_text(
        "A|ORIG|1|NCT1 = FAILED\nBLA125514|SUPPL|35|NCT02578680 = PIVOTAL\n", encoding="utf-8")
    (raw / f"{stem}-B.txt").write_text(
        "A|ORIG|1|NCT1 = FAILED\nBLA125514|SUPPL|35|NCT02578680 = PIVOTAL\n", encoding="utf-8")
    combined = label_role.combine(units, raw, batches, verdicts, decided_on="2026-09-25")
    assert combined.newly_recorded == 2
    assert label_role.read_roles(verdicts) == {
        ("A|ORIG|1", "NCT1"): "FAILED", ("BLA125514|SUPPL|35", "NCT02578680"): "PIVOTAL"}
    assert "Label-link roles" in label_role.render(combined)


def test_a_registry_id_written_with_a_space_is_found() -> None:
    body = "14 CLINICAL STUDIES\n14.1 Polyps\nTwo trials (NCT 01622569, NCT 01624662)."
    text = label_role.passages(body, LinkTarget("k", "NCT01624662", "", "Other", ""))
    assert text.startswith("[14.1 Polyps] …")
