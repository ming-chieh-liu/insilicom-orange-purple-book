"""Tests for the normalisation policy: one id space for both sides of the edge (D42)."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from config.settings import RawData, TrialArchive
from service import normalize
from service.drug_identity import DrugIdentity

# "Gastric Cancer" is a MeSH *concept*; "Stomach Neoplasms" is the *descriptor*
# that heads it. Both must land on D013274 or the join fails on obvious rows.
_CONCEPTS = {"M0333444": "D013274", "D013274": "D013274", "C582435": "C582435"}


class _FakeGraph:
    """iKraph2's names and exact-id lookups, as normalize.build reads them."""

    def __init__(self, names: dict[str, frozenset[str]] | None = None,
                 internal: dict[str, str] | None = None) -> None:
        self._names = names or {}
        self._internal = internal or {}
        self.names_asked: list[str] = []

    def names_of(self, biokdeids: Iterable[str]) -> dict[str, frozenset[str]]:
        asked = sorted(biokdeids)
        self.names_asked += asked
        return {i: self._names[i] for i in asked if i in self._names}

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]:
        return {i: self._internal[i] for i in ids if i in self._internal}


def test_a_bare_mesh_id_is_prefixed_for_the_graph() -> None:
    assert normalize.as_graph_id("D002289") == "MESH:D002289"
    assert normalize.as_graph_id("MESH:D002289") == "MESH:D002289"
    assert normalize.as_graph_id("PubChem:CID1") == "PubChem:CID1"
    assert normalize.as_graph_id("weird") == "weird"


def test_placeholder_ids_identify_nothing() -> None:
    assert not normalize.is_identifier("NA")
    assert normalize.is_identifier("MESH:D1")


def test_concept_ids_roll_up_and_others_pass_through() -> None:
    assert normalize.roll_up("MESH:M0333444", _CONCEPTS) == "MESH:D013274"
    assert normalize.roll_up("MESH:D013274", _CONCEPTS) == "MESH:D013274"
    assert normalize.roll_up("PubChem:CID1", _CONCEPTS) == "PubChem:CID1"


def test_the_synonym_index_drops_short_ambiguous_design_and_placeholder_names() -> None:
    index = normalize.synonym_index({
        "MESH:C582435": ["Keytruda", "PB", "Control", "MK-3475"],
        "MESH:C2": ["Opdivo", "mk-3475"],
        "MESH:M0333444": ["Gastric Cancer"],
        "NA": ["Anything"],
    }, _CONCEPTS)
    assert index == {"keytruda": "MESH:C582435", "opdivo": "MESH:C2",
                     "gastric cancer": "MESH:D013274"}


@pytest.mark.parametrize("asked,resolved,expected", [(10, 8, 0.8), (0, 0, 0.0)])
def test_the_coverage_rate(asked: int, resolved: int, expected: float) -> None:
    assert normalize.Coverage("x", asked, resolved, 0, 0, ()).rate == expected


def test_the_coverage_report_names_what_failed() -> None:
    text = normalize.render_coverage((normalize.Coverage("drugs", 2, 1, 0, 1, ("ODDITY",)),))
    assert "ODDITY" in text and "kept under a non-MeSH vocabulary" in text


def test_counted_reports_the_most_claimed_ids() -> None:
    mapped = {
        "a": normalize.Mapping("a", "MESH:D1", "", "", "", "exact"),
        "b": normalize.Mapping("b", "MESH:D1", "", "", "", "exact"),
        "c": normalize.Mapping("c", "MESH:D2", "", "", "", "exact"),
    }
    assert normalize.counted(mapped)[0] == ("MESH:D1", 2)


# ---------------------------------------------------------------------------
# Reading ingredients out of Products.txt
# ---------------------------------------------------------------------------


_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "000001\tNDA\t\tACME",
    "000009\tANDA\t\tGENERICO",
]
_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient"
    "\tReferenceStandard",
    "000001\t001\tTABLET;ORAL\t10MG\t0\tBRANDA\tASPIRIN\t0",
    # A combination product: one field, two drugs.
    "000001\t002\tTABLET;ORAL\t10MG\t0\tBRANDB\tASPIRIN; CAFFEINE\t0",
    "000009\t001\tTABLET;ORAL\t10MG\t0\tGENERIC\tIBUPROFEN\t0",
]


@pytest.fixture
def corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("Applications.txt", "\r\n".join(_APPLICATIONS) + "\r\n")
        zf.writestr("Products.txt", "\r\n".join(_PRODUCTS) + "\r\n")
    return RawData(root=root)


def test_ingredients_are_keyed_through_applications(corpus: RawData) -> None:
    """Products.txt carries no ApplType, so its rows cannot be keyed on their
    own — only Applications.txt says 000001 is an NDA."""
    got = normalize.ingredients_of(corpus, frozenset({"NDA000001"}))
    assert got["NDA000001"] == frozenset({"ASPIRIN", "CAFFEINE"})


def test_a_combination_product_yields_every_ingredient(corpus: RawData) -> None:
    """KEYNOTE-189 approved pembrolizumab *with* chemotherapy; a mapping that
    dropped the partners could not represent that."""
    got = normalize.ingredients_of(corpus, frozenset({"NDA000001"}))
    assert "CAFFEINE" in got["NDA000001"]


def test_applications_out_of_scope_are_skipped(corpus: RawData) -> None:
    got = normalize.ingredients_of(corpus, frozenset({"NDA000001"}))
    assert "ANDA000009" not in got


def test_the_report_renders_the_application_rate() -> None:
    report = normalize.NormalizeReport(
        drugs=(), indications=(), trial_drugs=(), trial_conditions=(),
        coverages=(normalize.Coverage("drugs", 10, 9, 0, 0, ()),),
        applications_unresolved=2, applications_total=10,
    )
    assert report.application_rate == 0.8
    assert "trial-relevant applications" in normalize.render(report)


def test_an_empty_report_has_a_zero_rate() -> None:
    report = normalize.NormalizeReport((), (), (), (), (), 0, 0)
    assert report.application_rate == 0.0


# ---------------------------------------------------------------------------
# The whole pass, end to end over fakes
# ---------------------------------------------------------------------------


_FULL_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "125514\tBLA\t\tMERCK",
]
_FULL_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient"
    "\tReferenceStandard",
    "125514\t001\tINJECTION\t100MG/4ML\t0\tKEYTRUDA\tPEMBROLIZUMAB\t0",
]
_FULL_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    "125514\t7\tORIG\t1\tAP\t2014-09-04 00:00:00\t\tPRIORITY",
]
_FULL_CLASS = [
    "SubmissionClassCodeID\tSubmissionClassCode\tSubmissionClassCodeDescription",
    "7\tTYPE 1\tType 1 - New Molecular Entity",
]
_FULL_ACTIONS = [
    "ActionTypes_LookupID\tActionTypes_LookupDescription"
    "\tSupplCategoryLevel1Code\tSupplCategoryLevel2Code",
    "46\t\t\tNOT APPLICABLE",
]
_FULL_JOIN = [
    "SubmissionType\tj_submissionActionTypeID\tApplNo\tSubmissionNo"
    "\tActionTypes_LookupID",
    "ORIG      \t1\t125514\t1\t46",
]
_FULL_PROPERTY = [
    "ApplNo\tSubmissionType\tSubmissionNo\tSubmissionPropertyTypeCode"
    "\tSubmissionPropertyTypeID",
    "125514\tORIG      \t1\tNull\t0",
]
_FULL_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType"
    "\tSubmissionNo\tApplicationDocsTitle\tApplicationDocsURL"
    "\tApplicationDocsDate",
    "1\t2\t125514\tORIG      \t1\t0\thttp://example/125514s000lbl.pdf"
    "\t2014-09-04 00:00:00",
]


@pytest.fixture
def full_corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    members = {
        "Applications.txt": _FULL_APPLICATIONS,
        "Products.txt": _FULL_PRODUCTS,
        "Submissions.txt": _FULL_SUBMISSIONS,
        "SubmissionClass_Lookup.txt": _FULL_CLASS,
        "ActionTypes_Lookup.txt": _FULL_ACTIONS,
        "Join_Submission_ActionTypes_Lookup.txt": _FULL_JOIN,
        "SubmissionPropertyType.txt": _FULL_PROPERTY,
        "ApplicationDocs.txt": _FULL_DOCS,
    }
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        for name, lines in members.items():
            zf.writestr(name, "\r\n".join(lines) + "\r\n")
    return RawData(root=root)


@pytest.fixture
def empty_archive(tmp_path: Path) -> TrialArchive:
    """No shards: the trial-side passes have nothing to walk."""
    return TrialArchive(root=tmp_path / "no-archive")


def _identity(name: str, mesh: str = "MESH:C582435", nodes: str = "2700000295") -> DrugIdentity:
    return DrugIdentity(name, "DPT0O3T46P", mesh, "pembrolizumab", nodes,
                        "TherapyPreventions", "unii_registry" if mesh else "unii_no_mesh")


def test_the_pass_takes_exact_ids_and_expands_every_spelling(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    graph = _FakeGraph(names={"2700000295": frozenset({"lambrolizumab"}),
                              "55633": frozenset({"non-small-cell lung cancer"})},
                       internal={"MESH:D002289": "55633"})
    report = normalize.build(
        full_corpus, empty_archive, graph, _CONCEPTS, ["D002289"],
        {"PEMBROLIZUMAB": _identity("PEMBROLIZUMAB")},
        extra_names={"PEMBROLIZUMAB": {"Keytruda"}, "MESH:C582435": {"MK-3475"}},
    )
    drug = report.drugs[0]
    assert (drug.mesh_id, drug.biokdeid, drug.basis) == (
        "MESH:C582435", "2700000295", "unii_registry")
    assert report.indications[0].mesh_id == "MESH:D002289"
    assert report.indications[0].biokdeid == "55633"
    assert set(graph.names_asked) == {"2700000295", "55633"}
    assert report.application_rate == 1.0
    assert len(report.coverages) == 4


def test_an_ingredient_no_exact_id_identifies_has_no_row(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    report = normalize.build(
        full_corpus, empty_archive, _FakeGraph(), _CONCEPTS, [],
        {"PEMBROLIZUMAB": _identity("PEMBROLIZUMAB", mesh="", nodes="")},
    )
    assert report.drugs == ()
    assert report.applications_unresolved == 1
    assert report.application_rate == 0.0
    assert report.coverages[0].unresolved == ("PEMBROLIZUMAB",)


def test_expanding_skips_rows_without_an_identifier() -> None:
    rows = [normalize.Mapping("X", "NA", "", "", "", "")]
    assert normalize.expand(rows, _FakeGraph(), {}) == {}


def test_the_trial_side_matches_through_the_synonym_index(tmp_path: Path) -> None:
    index = {"keytruda": "MESH:C582435"}
    rows, coverage = normalize._match_trial_strings(
        [("Keytruda", "Placebo"), ("KEYTRUDA",)], index, "trial intervention"
    )
    assert {r.name for r in rows} == {"Keytruda", "KEYTRUDA"}
    assert coverage.asked == 3
    assert coverage.resolved == 2
    assert all(r.basis == "synonym" for r in rows)


def test_a_trial_side_row_inherits_the_internal_id() -> None:
    rows, _ = normalize._match_trial_strings(
        [("Keytruda",)],
        {"keytruda": "MESH:C582435"},
        "trial intervention",
        {"MESH:C582435": "6308113"},
    )
    assert rows[0].biokdeid == "6308113"


def test_a_dose_stripped_name_resolves_only_under_the_guards() -> None:
    """2026-09-30: dose, route and form removed; short or NLM-contradicted refused."""
    index = {"cr845": "PubChem:CID24794466", "surgery": "MESH:D013455", "nacl": "MESH:D000111"}
    rows, coverage = normalize._match_trial_strings(
        [("CR845 0.5 mcg/kg", "Oral surgery", "NaCl 0.9%", "Unknown 5 mg")],
        index, "trial intervention", nlm_of={"Oral surgery": {"MESH:D013514"}}, strip_doses=True,
    )
    assert [(r.name, r.mesh_id, r.basis) for r in rows] == [
        ("CR845 0.5 mcg/kg", "PubChem:CID24794466", normalize.BASIS_DOSE_STRIPPED)
    ]
    assert coverage.resolved == 1
