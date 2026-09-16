"""Tests for the normalisation policy: one id space for both sides of the edge."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from config.settings import BiokdeConnection, RawData, TrialArchive
from dao.biokde import BiokdeRepository, Entity
from source.moiety import Moiety
from service import normalize

# "Gastric Cancer" is a MeSH *concept*; "Stomach Neoplasms" is the *descriptor*
# that heads it. Both must land on D013274 or the join fails on obvious rows.
_CONCEPTS = {"M0333444": "D013274", "D013274": "D013274", "C582435": "C582435"}


class _FakeRepository:
    """A graph with the shapes that matter, and nothing else."""

    def __init__(
        self,
        entities: dict[str, tuple[Entity, ...]] | None = None,
        synonyms: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._entities = entities or {}
        self._synonyms = synonyms or {}
        self.synonym_ids_asked: list[str] = []

    def resolve(self, names: Iterable[str]) -> dict[str, tuple[Entity, ...]]:
        return {n: self._entities[n] for n in names if n and n in self._entities}

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]:
        return {i: f"bk-{i}" for i in ids if i in self._synonyms or i.startswith("MESH:")}

    def synonyms(self, ids: Iterable[str]) -> dict[str, frozenset[str]]:
        self.synonym_ids_asked = list(ids)
        return {
            i: self._synonyms[i]
            for i in self.synonym_ids_asked
            if i in self._synonyms
        }


def _repository(**kwargs: object) -> BiokdeRepository:
    return _FakeRepository(**kwargs)  # type: ignore[return-value,arg-type]


# ---------------------------------------------------------------------------
# Id hygiene
# ---------------------------------------------------------------------------


def test_a_bare_mesh_id_is_prefixed_for_the_graph() -> None:
    """indications.tsv writes D002137; the graph stores MESH:D002137. Asking
    with the bare form matches nothing and reports honest-looking zeros."""
    assert normalize.as_graph_id("D002137") == "MESH:D002137"


def test_an_already_prefixed_id_is_left_alone() -> None:
    assert normalize.as_graph_id("PubChem:CID1") == "PubChem:CID1"


def test_a_non_mesh_looking_string_is_not_prefixed() -> None:
    assert normalize.as_graph_id("XYZ") == "XYZ"


def test_the_placeholder_id_is_not_an_identifier() -> None:
    """The graph writes NA where an entity has no external id. Keyed on that,
    every unidentified drug becomes the same drug."""
    assert not normalize.is_identifier("NA")
    assert normalize.is_identifier("MESH:D016190")


def test_a_placeholder_id_never_becomes_a_mapping() -> None:
    entities = {
        "Acoltremon": (Entity("NA", "bkNA", "Acoltremon", "Chemical", "NA"),),
        "Carboplatin": (Entity("MESH:D016190", "bkMESH:D016190", "Carboplatin", "Chemical", "Drug"),),
    }
    mapped, coverage = normalize.normalise(
        ["Acoltremon", "Carboplatin"], _repository(entities=entities), {}, "drugs"
    )
    assert "Acoltremon" not in mapped
    assert coverage.resolved == 1


def test_placeholder_ids_are_never_expanded_to_synonyms() -> None:
    """Expanding NA would pull every id-less entity's names into one bucket and
    match them all to each other."""
    repository = _FakeRepository(synonyms={"MESH:C582435": frozenset({"Keytruda"})})
    normalize.synonym_index(
        ["NA", "MESH:C582435"], repository, {}  # type: ignore[arg-type]
    )
    assert repository.synonym_ids_asked == ["MESH:C582435"]


# ---------------------------------------------------------------------------
# The concept → descriptor rollup
# ---------------------------------------------------------------------------


def test_a_concept_id_rolls_up_to_its_descriptor() -> None:
    assert normalize.roll_up("MESH:M0333444", _CONCEPTS) == "MESH:D013274"


def test_a_descriptor_id_is_unchanged() -> None:
    assert normalize.roll_up("MESH:D013274", _CONCEPTS) == "MESH:D013274"


def test_a_non_mesh_id_is_never_rolled() -> None:
    assert normalize.roll_up("PubChem:CID1", _CONCEPTS) == "PubChem:CID1"


def test_two_spellings_of_one_disease_reach_the_same_id() -> None:
    """The whole point of the rollup."""
    entities = {
        "Gastric Cancer": (Entity("MESH:M0333444", "bkMESH:M0333444", "Cancer Of Stomach", "Disease", "NA"),),
        "Stomach Neoplasms": (Entity("MESH:D013274", "bkMESH:D013274", "Stomach Neoplasms", "Disease", "NA"),),
    }
    mapped, coverage = normalize.normalise(
        ["Gastric Cancer", "Stomach Neoplasms"],
        _repository(entities=entities),
        _CONCEPTS,
        "conditions",
    )
    assert mapped["Gastric Cancer"].mesh_id == mapped["Stomach Neoplasms"].mesh_id
    assert mapped["Gastric Cancer"].basis == "rolled-up"
    assert coverage.rolled_up == 1


# ---------------------------------------------------------------------------
# Choosing between entities one name reached
# ---------------------------------------------------------------------------


def test_mesh_is_preferred_over_another_vocabulary() -> None:
    entities = (
        Entity("PubChem:SID187051801", "bkPubChem:SID187051801", "GTPL7499", "Chemical", "Drug"),
        Entity("MESH:C582435", "bkMESH:C582435", "Pembrolizumab", "Chemical", "Drug"),
    )
    best = normalize.preferred(entities, {})
    assert best is not None and best.id == "MESH:C582435"


def test_a_descriptor_is_preferred_over_a_supplementary_record() -> None:
    entities = (
        Entity("MESH:C111111", "bkMESH:C111111", "Supplementary", "Disease", "NA"),
        Entity("MESH:D013274", "bkMESH:D013274", "Stomach Neoplasms", "Disease", "NA"),
    )
    best = normalize.preferred(entities, {})
    assert best is not None and best.id == "MESH:D013274"


def test_a_curated_drug_is_preferred_over_an_untyped_chemical() -> None:
    entities = (
        Entity("MESH:C000002", "bkMESH:C000002", "Untyped", "Chemical", "NA"),
        Entity("MESH:C000001", "bkMESH:C000001", "Curated", "Chemical", "Drug"),
    )
    best = normalize.preferred(entities, {})
    assert best is not None and best.subtype == "Drug"


def test_the_choice_does_not_depend_on_result_order() -> None:
    entities = (
        Entity("MESH:C582435", "bkMESH:C582435", "Pembrolizumab", "Chemical", "Drug"),
        Entity("PubChem:SID1", "bkPubChem:SID1", "Other", "Chemical", "Drug"),
    )
    forward = normalize.preferred(entities, {})
    backward = normalize.preferred(tuple(reversed(entities)), {})
    assert forward == backward


def test_no_entities_means_no_choice() -> None:
    assert normalize.preferred((), {}) is None


def test_a_non_mesh_id_is_kept_not_dropped() -> None:
    """semaglutide and Ozempic resolve only to PubChem. Dropping them would
    lose whole modern drug classes."""
    entities = {"SEMAGLUTIDE": (Entity("PubChem:CID56843331", "bkPubChem:CID56843331", "x", "Chemical", "Drug"),)}
    mapped, coverage = normalize.normalise(
        ["SEMAGLUTIDE"], _repository(entities=entities), {}, "drugs"
    )
    assert mapped["SEMAGLUTIDE"].mesh_id == "PubChem:CID56843331"
    assert coverage.non_mesh == 1


def test_an_entity_type_filter_excludes_a_same_named_gene() -> None:
    """'ASPIRIN' also reaches the gene ASPM in the graph."""
    entities = {
        "ASPIRIN": (
            Entity("NCBI:259266", "bkNCBI:259266", "ASPM", "Gene", "NA"),
            Entity("PubChem:CID2244", "bkPubChem:CID2244", "Aspirin", "Chemical", "Drug"),
        )
    }
    mapped, _ = normalize.normalise(
        ["ASPIRIN"], _repository(entities=entities), {}, "drugs", "Chemical"
    )
    assert mapped["ASPIRIN"].entity_type == "Chemical"


# ---------------------------------------------------------------------------
# The synonym index, used to match the trial side
# ---------------------------------------------------------------------------


def test_short_synonyms_are_dropped() -> None:
    """MESH:C582435 carries PB, PEM and PBZ. A trial arm called 'PB' matching
    pembrolizumab would be an accident, not a match."""
    repository = _FakeRepository(
        synonyms={"MESH:C582435": frozenset({"PB", "PEM", "Pembrolizumab"})}
    )
    index = normalize.synonym_index(
        ["MESH:C582435"], repository, {}  # type: ignore[arg-type]
    )
    assert "pb" not in index
    assert index["pembrolizumab"] == "MESH:C582435"


def test_the_index_is_case_folded() -> None:
    repository = _FakeRepository(synonyms={"MESH:C582435": frozenset({"Keytruda"})})
    index = normalize.synonym_index(
        ["MESH:C582435"], repository, {}  # type: ignore[arg-type]
    )
    assert index["keytruda"] == "MESH:C582435"


def test_a_synonym_two_ids_claim_is_dropped_not_arbitrated() -> None:
    """Keeping the first one seen would make the result depend on query order."""
    repository = _FakeRepository(
        synonyms={
            "MESH:D000001": frozenset({"Ambiguous"}),
            "MESH:D000002": frozenset({"Ambiguous", "Distinct"}),
        }
    )
    index = normalize.synonym_index(
        ["MESH:D000001", "MESH:D000002"], repository, {}  # type: ignore[arg-type]
    )
    assert "ambiguous" not in index
    assert index["distinct"] == "MESH:D000002"


def test_synonyms_are_rolled_up_too() -> None:
    repository = _FakeRepository(
        synonyms={"MESH:M0333444": frozenset({"Gastric Cancer"})}
    )
    index = normalize.synonym_index(
        ["MESH:M0333444"], repository, _CONCEPTS  # type: ignore[arg-type]
    )
    assert index["gastric cancer"] == "MESH:D013274"


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "resolved", "expected"), [(10, 8, 0.8), (0, 0, 0.0), (4, 4, 1.0)]
)
def test_the_coverage_rate(asked: int, resolved: int, expected: float) -> None:
    assert normalize.Coverage("x", asked, resolved, 0, 0, ()).rate == expected


def test_the_coverage_report_names_what_failed() -> None:
    entities = {"Known": (Entity("MESH:D1", "bkMESH:D1", "Known", "Chemical", "Drug"),)}
    _, coverage = normalize.normalise(
        ["Known", "Unknown"], _repository(entities=entities), {}, "drugs"
    )
    assert coverage.unresolved == ("Unknown",)
    assert "Unknown" in normalize.render_coverage((coverage,))


def test_source_of_names_the_vocabulary() -> None:
    assert normalize.source_of("MESH:D1") == "MESH:"
    assert normalize.source_of("PubChem:CID1") == "PubChem:"
    assert normalize.source_of("TTD:D1") == ""


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


def test_the_pass_maps_every_side_it_can(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    repository = _FakeRepository(
        entities={
            "PEMBROLIZUMAB": (
                Entity("MESH:C582435", "bkMESH:C582435", "Pembrolizumab", "Chemical", "Drug"),
            )
        },
        synonyms={"MESH:C582435": frozenset({"Keytruda", "Pembrolizumab"})},
    )
    report = normalize.build(
        full_corpus,
        empty_archive,
        repository,  # type: ignore[arg-type]
        _CONCEPTS,
        ["D002289"],
    )
    assert report.drugs[0].mesh_id == "MESH:C582435"
    assert report.application_rate == 1.0
    assert len(report.coverages) == 4


def test_bare_indication_ids_are_prefixed_by_the_pass(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    """indications.tsv holds D002289; the graph holds MESH:D002289."""
    report = normalize.build(
        full_corpus, empty_archive, _FakeRepository(),  # type: ignore[arg-type]
        _CONCEPTS, ["D002289"],
    )
    assert report.indications[0].mesh_id == "MESH:D002289"


def test_an_application_whose_drugs_all_fail_is_counted(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    report = normalize.build(
        full_corpus, empty_archive, _FakeRepository(),  # type: ignore[arg-type]
        _CONCEPTS, [],
    )
    assert report.applications_total == 1
    assert report.applications_unresolved == 1
    assert report.application_rate == 0.0


def test_the_trial_side_matches_through_the_synonym_index(tmp_path: Path) -> None:
    index = {"keytruda": "MESH:C582435"}
    rows, coverage = normalize._match_trial_strings(
        [("Keytruda", "Placebo"), ("KEYTRUDA",)], index, "trial intervention"
    )
    assert {r.name for r in rows} == {"Keytruda", "KEYTRUDA"}
    assert coverage.asked == 3
    assert coverage.resolved == 2
    assert all(r.basis == "synonym" for r in rows)


def test_the_internal_node_id_reaches_the_output() -> None:
    """P5 walks drug -> disease edges in pubmedRelations, which keys on
    biokdeid. P1 already resolves it; throwing it away makes P5 redo the work."""
    entities = {
        "PEMBROLIZUMAB": (
            Entity("MESH:C582435", "6308113", "Pembrolizumab", "Chemical", "Drug"),
        )
    }
    mapped, _ = normalize.normalise(
        ["PEMBROLIZUMAB"], _repository(entities=entities), {}, "drugs"
    )
    assert mapped["PEMBROLIZUMAB"].biokdeid == "6308113"


def test_a_trial_side_row_inherits_the_internal_id() -> None:
    rows, _ = normalize._match_trial_strings(
        [("Keytruda",)],
        {"keytruda": "MESH:C582435"},
        "trial intervention",
        {"MESH:C582435": "6308113"},
    )
    assert rows[0].biokdeid == "6308113"


def test_an_indication_gets_an_internal_id_too(
    full_corpus: RawData, empty_archive: TrialArchive
) -> None:
    """Indications arrive already coded to MeSH, so they never go through
    resolve() and would otherwise carry no biokdeid — leaving P5 unable to walk
    the disease end of a drug-disease edge."""
    report = normalize.build(
        full_corpus, empty_archive, _FakeRepository(),  # type: ignore[arg-type]
        _CONCEPTS, ["D002289"],
    )
    assert report.indications[0].biokdeid == "bk-MESH:D002289"


# ---------------------------------------------------------------------------
# Salt to moiety folding
# ---------------------------------------------------------------------------


def _moiety(salt: str, name: str) -> Moiety:
    return Moiety(salt=salt, moiety=name, salt_unii="U1", moiety_unii="M1")


def test_a_salt_resolves_through_its_moiety() -> None:
    """The FDA writes the salt, a trial writes the moiety. Where the graph
    gives them different ids the drug matches no trials at all, silently."""
    entities = {
        "ESOMEPRAZOLE MAGNESIUM": (
            Entity("PubChem:CID130564", "bk1", "x", "Chemical", "Drug"),
        ),
        "ESOMEPRAZOLE": (
            Entity("MESH:D009853", "bk2", "Esomeprazole", "Chemical", "Drug"),
        ),
    }
    mapped, _ = normalize.normalise(
        ["ESOMEPRAZOLE MAGNESIUM"],
        _repository(entities=entities),
        {},
        "drugs",
        "Chemical",
        {"ESOMEPRAZOLE MAGNESIUM": _moiety("ESOMEPRAZOLE MAGNESIUM", "ESOMEPRAZOLE")},
    )
    assert mapped["ESOMEPRAZOLE MAGNESIUM"].mesh_id == "MESH:D009853"
    assert mapped["ESOMEPRAZOLE MAGNESIUM"].basis == "moiety"


def test_the_salt_still_wins_when_it_is_better_vocabularied() -> None:
    """The moiety is a second thing to ask about, not an override."""
    entities = {
        "SALT FORM": (Entity("MESH:D000001", "bk1", "Salt", "Chemical", "Drug"),),
        "MOIETY": (Entity("PubChem:CID1", "bk2", "Moiety", "Chemical", "Drug"),),
    }
    mapped, _ = normalize.normalise(
        ["SALT FORM"],
        _repository(entities=entities),
        {},
        "drugs",
        "Chemical",
        {"SALT FORM": _moiety("SALT FORM", "MOIETY")},
    )
    assert mapped["SALT FORM"].mesh_id == "MESH:D000001"
    assert mapped["SALT FORM"].basis == "exact"


def test_a_name_with_no_moiety_entry_is_unaffected() -> None:
    entities = {"CARBOPLATIN": (Entity("MESH:D016190", "bk", "C", "Chemical", "Drug"),)}
    mapped, _ = normalize.normalise(
        ["CARBOPLATIN"], _repository(entities=entities), {}, "drugs", "Chemical", {}
    )
    assert mapped["CARBOPLATIN"].basis == "exact"


def test_an_unresolvable_salt_is_rescued_by_its_moiety() -> None:
    """54 of the 402 names P1 could not resolve are salts whose moiety the
    graph does know."""
    entities = {
        "ACORAMIDIS HYDROCHLORIDE": (),
        "ACORAMIDIS": (Entity("MESH:C000001", "bk", "Acoramidis", "Chemical", "Drug"),),
    }
    mapped, _ = normalize.normalise(
        ["ACORAMIDIS HYDROCHLORIDE"],
        _repository(entities=entities),
        {},
        "drugs",
        "Chemical",
        {"ACORAMIDIS HYDROCHLORIDE": _moiety("ACORAMIDIS HYDROCHLORIDE", "ACORAMIDIS")},
    )
    assert mapped["ACORAMIDIS HYDROCHLORIDE"].mesh_id == "MESH:C000001"
