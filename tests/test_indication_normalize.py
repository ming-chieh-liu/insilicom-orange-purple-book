"""Tests for the single indication step (D30)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from service import indication_normalize as inorm
from service import indications

_INDEX = indications.TermIndex(
    {"acne vulgaris": "D000152", "gout": "D006073", "melanoma": "D008545",
     "alzheimer disease": "D000544"},
    {"D000152": "Acne Vulgaris", "D006073": "Gout", "D008545": "Melanoma",
     "D000544": "Alzheimer Disease"},
    2,
    0,
)
_EXACT = {"non-small cell lung cancer": "MESH:D002289"}


def _lookup(asked: list[str]) -> Callable[[Iterable[str]], dict[str, str]]:
    def internal_ids(ids: Iterable[str]) -> dict[str, str]:
        wanted = list(ids)
        asked.extend(wanted)
        return {i: f"b-{i}" for i in wanted if i != "MESH:D008545"}
    return internal_ids


def _build(asked: list[str] | None = None, **kw: object) -> inorm.IndicationReport:
    args: dict[str, object] = dict(
        letters={}, label_section_1={}, headings={}, openfda={}, exact=_EXACT,
        known_biokdeids={"MESH:D002289": "b-known"}, index=_INDEX,
        internal_ids=_lookup(asked if asked is not None else []),
    )
    args.update(kw)
    return inorm.build([("NDA000001", "ORIG", "1")], **args)  # type: ignore[arg-type]


def test_the_letter_wins_when_it_codes_a_disease() -> None:
    report = _build(letters={"NDA000001|ORIG|1": "for the treatment of acne vulgaris"},
                    label_section_1={"NDA000001|ORIG|1": "indicated for gout"})
    chosen = {(r.source, r.mesh_id) for r in report.rows if r.chosen == "true"}
    assert chosen == {("letter", "MESH:D000152")}
    assert {r.source for r in report.rows} == {"letter", "label_section_1"}


def test_label_section_1_is_chosen_when_the_letter_codes_nothing() -> None:
    report = _build(letters={"NDA000001|ORIG|1": "for a new strength"},
                    label_section_1={"NDA000001|ORIG|1": "indicated for gout"})
    assert [r.source for r in report.rows if r.chosen == "true"] == ["label_section_1"]


def test_openfda_is_only_a_fallback() -> None:
    fallback = _build(openfda={"NDA000001": {"D006073"}})
    assert [(r.source, r.chosen) for r in fallback.rows] == [("openfda_current", "true")]
    unused = _build(label_section_1={"NDA000001|ORIG|1": "for gout"},
                    openfda={"NDA000001": {"D008545"}})
    assert "openfda_current" not in {r.source for r in unused.rows}


def test_headings_are_per_trial_and_never_chosen() -> None:
    report = _build(headings={"NDA000001|ORIG|1": ["Non-Small Cell Lung Cancer", "Study 1"]})
    rows = {r.source_key: r for r in report.rows}
    assert rows["Non-Small Cell Lung Cancer"].mesh_id == "MESH:D002289"
    assert rows["Non-Small Cell Lung Cancer"].biokdeid == "b-known"
    assert rows["Study 1"].mesh_id == ""  # recorded, uncoded
    assert all(r.chosen == "false" for r in report.rows)


def test_every_id_carries_mesh_and_biokde_and_known_ids_are_not_asked_again() -> None:
    asked: list[str] = []
    report = _build(asked, label_section_1={
        "NDA000001|ORIG|1": "for Alzheimer's disease and melanoma"},
        headings={"NDA000001|ORIG|1": ["Non-Small Cell Lung Cancer"]})
    by_id = {r.mesh_id: r for r in report.rows if r.mesh_id}
    assert by_id["MESH:D000544"].biokdeid == "b-MESH:D000544"
    assert by_id["MESH:D000544"].term == "Alzheimer Disease"
    assert by_id["MESH:D008545"].biokdeid == ""  # the graph has no internal id for it
    assert "MESH:D002289" not in asked
    assert report.coded_ids == 3 and report.with_biokdeid == 2


def test_a_submission_with_nothing_contributes_no_rows() -> None:
    report = _build()
    assert report.rows == ()
    assert "in-scope submissions" in inorm.render(report, 1)


def test_read_chosen_keeps_only_chosen_rows_with_a_biokdeid(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "submission_indications.tsv"
    path.write_text(
        "application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext"
        "\tmesh_id\tbiokdeid\tterm\tchosen\n"
        "NDA1\tORIG\t1\tletter\t\tx\tMESH:D1\tb1\tOne\ttrue\n"
        "NDA1\tORIG\t1\tletter\t\tx\tMESH:D2\t\tTwo\ttrue\n"
        "NDA1\tORIG\t1\tlabel_14_heading\tH\tH\tMESH:D3\tb3\tThree\tfalse\n",
        encoding="utf-8",
    )
    assert inorm.read_chosen(path) == {"NDA1|ORIG|1": {("b1", "One")}}
    assert inorm.read_chosen(Path(str(tmp_path)) / "absent.tsv") == {}
