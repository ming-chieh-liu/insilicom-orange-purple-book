"""Tests for S2/S3 normalisation by exact ids (D44)."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from config.settings import RawData, TrialArchive
from service import normalize
from service.drug_identity import DrugIdentity
from source.substance_ids import Dictionary, Substance
from source.unii_mesh import MeshIndex
from dao.ikraph2_entities import Node


class FakeGraph:
    """iKraph2 as the resolver reads it: names, exact ids, disease entities."""

    def __init__(self, names: dict[str, list[Node]] | None = None,
                 ids: dict[str, list[Node]] | None = None,
                 diseases: dict[str, list[str]] | None = None) -> None:
        self.names = _NAMES if names is None else names
        self.ids = _IDS if ids is None else ids
        self.diseases = _DISEASES if diseases is None else diseases

    def named(self, keys: Iterable[str], types: Iterable[str]) -> dict[str, list[Node]]:
        kinds = set(types)
        out = {k: [n for n in self.names.get(k, []) if n.type in kinds] for k in keys}
        return {k: v for k, v in out.items() if v}

    def by_db_id(self, ids: Iterable[str]) -> dict[str, list[Node]]:
        return {i: self.ids[i] for i in ids if i in self.ids}

    def diseases_by_db_id(self, ids: Iterable[str]) -> dict[str, list[str]]:
        return {i: self.diseases[i] for i in ids if i in self.diseases}

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]:
        return {k: v[0] for k, v in self.diseases_by_db_id(ids).items() if len(v) == 1}


_PEMBRO = Node("2700000295", "TherapyPreventions", "PEMBROLIZUMAB", "UNII:DPT0O3T46P", 31352)
_KEYTRUDA = Node("2700044997", "TherapyPreventions", "keytruda", "", 90)
_PEMBRO_CHEM = Node("6308113", "Chemical", "pembrolizumab", "MESH:C582435", 4)
_NSCLC = Node("55633", "Disease", "carcinoma, non-small-cell lung", "MESH:D002289", 107101)
_EOS = Node("9001", "Disease", "eosinophilic asthma", "MESH:M0001", 50)
_NAMES: dict[str, list[Node]] = {
    "pembrolizumab": [_PEMBRO, _PEMBRO_CHEM],
    "keytruda": [_KEYTRUDA, _PEMBRO_CHEM],
    "non-small cell lung cancer": [_NSCLC],
    "eosinophilic asthma": [_EOS],
    "two drugs": [_PEMBRO, _KEYTRUDA],
}
_IDS: dict[str, list[Node]] = {
    "UNII:DPT0O3T46P": [_PEMBRO], "MESH:C582435": [_PEMBRO_CHEM],
    "MESH:C999": [Node("77", "Chemical", "only chemical", "MESH:C999", 1)],
}
_DISEASES: dict[str, list[str]] = {"MESH:D002289": ["55633"], "MESH:D2": ["1", "2"]}


def test_a_bare_mesh_id_is_prefixed_for_the_graph() -> None:
    assert normalize.as_graph_id("D002289") == "MESH:D002289"
    assert normalize.as_graph_id("MESH:D002289") == "MESH:D002289"
    assert normalize.as_graph_id("PubChem:CID1") == "PubChem:CID1"
    assert normalize.as_graph_id("weird") == "weird"


def test_placeholder_ids_identify_nothing() -> None:
    assert not normalize.is_identifier("NA")
    assert normalize.is_identifier("MESH:D1")


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
        drugs=(), indications=(), trial_drugs=(), trial_conditions=(), nlm_ids=(),
        coverages=(normalize.Coverage("drugs", 10, 9, 0, 0, ()),),
        applications_unresolved=2, applications_total=10,
    )
    assert report.application_rate == 0.8
    assert "trial-relevant applications" in normalize.render(report)


def test_an_empty_report_has_a_zero_rate() -> None:
    report = normalize.NormalizeReport((), (), (), (), (), (), 0, 0)
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


_MESH = MeshIndex(uniis={"MESH:C582435": frozenset({"DPT0O3T46P"})},
                  by_term={"pembrolizumab": frozenset({"MESH:C582435"})},
                  names={"MESH:C582435": "pembrolizumab"}, headings={})
_DICTIONARY = Dictionary(
    {"DPT0O3T46P": Substance("DPT0O3T46P", "PEMBROLIZUMAB", "MESH:C582435", "pembrolizumab",
                             "preferred", "DPT0O3T46P", "", "")},
    {"MESH:C582435": "DPT0O3T46P"})


def test_s2_takes_exact_ids_and_s3_codes_nothing_without_an_archive(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    identity = DrugIdentity("PEMBROLIZUMAB", "DPT0O3T46P", "MESH:C582435", "pembrolizumab",
                            "2700000295", "TherapyPreventions", "unii_registry")
    report = normalize.build(full_corpus, empty_archive, FakeGraph(), _MESH, ["D002289"],
                             {"PEMBROLIZUMAB": identity}, {}, _DICTIONARY)
    drug = report.drugs[0]
    assert (drug.mesh_id, drug.biokdeid, drug.basis) == (
        "MESH:C582435", "2700000295", "unii_registry")
    assert report.indications[0].mesh_id == "MESH:D002289"
    assert report.indications[0].biokdeid == "55633"
    assert report.application_rate == 1.0
    assert (report.trial_drugs, report.trial_conditions, report.nlm_ids) == ((), (), ())
    assert len(report.coverages) == 5


def test_an_ingredient_nothing_exact_reaches_has_no_row(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    empty = MeshIndex({}, {}, {}, {})
    report = normalize.build(full_corpus, empty_archive, FakeGraph(names={}), empty, [], {}, {},
                             Dictionary({}, {}))
    assert report.drugs == ()
    assert report.applications_unresolved == 1
    assert report.coverages[0].unresolved == ("PEMBROLIZUMAB",)
