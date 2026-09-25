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


def test_label_sourced_links_become_targets_once() -> None:
    link = assemble.Link(
        nct_id="NCT1", application="NDA1", submission_type="ORIG", submission_no="1",
        approval_date="2010", action_type="", indication_mesh="", indication_term="Lipids",
        drug_mesh="", trial_name="ACCORD", label_enrollment="5518", registry_enrollment="",
        evidence_tier="A", evidence_source="label_nct", count_check="no_count",
        sources="label_nct;review_protocol",
    )
    review_only = link._replace(nct_id="NCT2", evidence_source="review_protocol",
                                sources="review_protocol")
    unsourced = link._replace(nct_id="NCT3", sources="", evidence_source="label_description")
    targets = label_role.targets_of([link, link, review_only, unsourced])
    assert targets == [LinkTarget("NDA1|ORIG|1", "NCT1", "ACCORD", "Lipids", "5518"),
                       LinkTarget("NDA1|ORIG|1", "NCT3", "ACCORD", "Lipids", "5518")]


def test_passages_find_the_trial_under_its_own_subsection() -> None:
    text = label_role.passages(_BODY, LinkTarget("k", "NCT00000620", "ACCORD Lipid", "", "5518"))
    assert text.startswith("[14.2 Lack of Efficacy in Cardiovascular Outcomes Trials]")
    assert "showed no benefit" in text


def test_a_comma_written_count_is_found() -> None:
    text = label_role.passages(_BODY, LinkTarget("k", "NCT9", "n=1485", "", "1485"))
    assert text.startswith("[14.1 Lipids]")


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
    assert unit["target"].startswith("ACCORD Lipid (registry id NCT00000620")
    assert "built" in label_role.render_units(report, Path("u.jsonl"))


def test_label_urls_key_bare_digits_as_nda_or_bla(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = [
        label_pdf.LabelDocument("125514", "SUPPL", "35", "u1", ""),
        label_pdf.LabelDocument("125514", "SUPPL", "35", "u-dup", ""),
        label_pdf.LabelDocument("021612", "ORIG", "1", "u2", ""),
        label_pdf.LabelDocument("999999", "ORIG", "1", "u3", ""),
    ]
    monkeypatch.setattr(label_role.label_pdf, "read_label_documents", lambda data: iter(docs))
    urls = label_role.label_urls(object(), {"BLA125514|SUPPL|35", "NDA021612|ORIG|1"})  # type: ignore[arg-type]
    assert urls == {"BLA125514|SUPPL|35": "u1", "NDA021612|ORIG|1": "u2"}


def test_sections_come_from_the_cache_only(tmp_path: Path) -> None:
    cached = label_pdf.cache_path("https://x/a.pdf", tmp_path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"%PDF")
    got = label_role.read_sections(
        {"A|ORIG|1": "https://x/a.pdf", "B|ORIG|1": "https://x/missing.pdf"},
        tmp_path, parsers=1, extract=lambda path: "SECTION 14",
    )
    assert got == {"A|ORIG|1": "SECTION 14"}


def test_a_label_with_no_section_14_yields_nothing(tmp_path: Path) -> None:
    cached = label_pdf.cache_path("https://x/a.pdf", tmp_path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"%PDF")
    assert label_role.read_sections({"A|ORIG|1": "https://x/a.pdf"}, tmp_path,
                                    parsers=1, extract=lambda path: "") == {}


def test_the_default_extractor_takes_section_14(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(label_role.label_pdf, "extract_text",
                        lambda path: ("1 INDICATIONS\nx\n" + _BODY, 1, 1))
    assert label_role._section_14(Path("x.pdf")).startswith("14 CLINICAL STUDIES")
    monkeypatch.setattr(label_role.label_pdf, "extract_text", lambda path: None)
    assert label_role._section_14(Path("x.pdf")) == ""


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
