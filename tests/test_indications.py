"""Tests for the drug → disease layer: label section 1, coded to MeSH."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from config.settings import MeshVocabulary, RawData
from service import indications

# --- the vocabulary ---------------------------------------------------------

_XML = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D001249</DescriptorUI>
    <DescriptorName><String>Asthma</String></DescriptorName>
    <TreeNumberList><TreeNumber>C08.127.108</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>Asthma</String></Term>
      <Term><String>Bronchial Asthma</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D003924</DescriptorUI>
    <DescriptorName><String>Diabetes Mellitus, Type 2</String></DescriptorName>
    <TreeNumberList><TreeNumber>C18.452.394.750.149</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>Diabetes Mellitus, Type 2</String></Term>
      <Term><String>Type 2 Diabetes</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D000068579</DescriptorUI>
    <DescriptorName><String>Trastuzumab</String></DescriptorName>
    <TreeNumberList><TreeNumber>D12.776.124</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>Trastuzumab</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
"""

_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "000001\tNDA\t\tACME",
    "000003\tANDA\t\tGENERICO",
]
_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient\tReferenceStandard",
    "000001\t001\tTABLET;ORAL\t10MG\t0\tBRANDA\tALBUTEROL\t0",
    "000003\t001\tTABLET;ORAL\t10MG\t0\tGENA\tALBUTEROL\t0",
]
_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    "000001\t19\tORIG\t1\tAP\t1996-12-17 00:00:00\t\tUNKNOWN",
    "000003\t1\tORIG\t1\tAP\t2010-01-05 00:00:00\t\t",
]
_OB_PRODUCTS = [
    "Ingredient~DF;Route~Trade_Name~Applicant~Strength~Appl_Type~Appl_No~Product_No"
    "~TE_Code~Approval_Date~RLD~RS~Type~Applicant_Full_Name",
    "ALBUTEROL~TABLET;ORAL~BRANDA~ACME~10MG~N~000001~001~AB~Dec 17, 1996~Yes~No~RX~ACME INC",
]
_OB_PATENT = [
    "Appl_Type~Appl_No~Product_No~Patent_No~Patent_Expire_Date_Text"
    "~Drug_Substance_Flag~Drug_Product_Flag~Patent_Use_Code~Delist_Flag~Submission_Date",
    "N~000001~001~7625884~Aug 24, 2026~~~U-141~~",
]
_OB_EXCLUSIVITY = [
    "Appl_Type~Appl_No~Product_No~Exclusivity_Code~Exclusivity_Date",
    "N~000001~001~NCE~Jul 13, 2026",
]
_UNII_NAMES = [
    "NAME\tTYPE\tUNII\tDISPLAY_NAME",
    "Albuterol\tof\tQF8SVZ843E\tALBUTEROL",
]

_LABELS = [
    # The current version of this application's label.
    {
        "set_id": "set-a",
        "effective_time": "20240101",
        "openfda": {"application_number": ["NDA000001"]},
        "indications_and_usage": [
            "1 INDICATIONS AND USAGE BRANDA is indicated for\nthe treatment of asthma."
        ],
    },
    # An older version of the SAME application, naming a disease the drug is no
    # longer indicated for. Must lose to the 2024 version.
    {
        "set_id": "set-a",
        "effective_time": "20110101",
        "openfda": {"application_number": ["NDA000001"]},
        "indications_and_usage": ["Indicated for type 2 diabetes."],
    },
    # A generic reproducing the brand's section 1. Unlike section 14, this is
    # kept: the generic really is indicated for that disease.
    {
        "set_id": "set-generic",
        "effective_time": "20200101",
        "openfda": {"application_number": ["ANDA000003"]},
        "indications_and_usage": ["Indicated for the treatment of asthma."],
    },
    # No indications section at all.
    {
        "set_id": "set-none",
        "effective_time": "20240101",
        "openfda": {"application_number": ["NDA000001"]},
    },
    # Indications, but no application anywhere: unlinked.
    {
        "set_id": "set-orphan",
        "effective_time": "20240101",
        "openfda": {},
        "indications_and_usage": ["Indicated for asthma."],
    },
]
_NDC: list[dict[str, object]] = []


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\r\n".join(lines).encode() + b"\r\n")


@pytest.fixture
def vocabulary(tmp_path: Path) -> MeshVocabulary:
    path = tmp_path / "desc.xml"
    path.write_text(_XML, encoding="utf-8")
    return MeshVocabulary(descriptors=path)


@pytest.fixture
def corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("Applications.txt", "\r\n".join(_APPLICATIONS) + "\r\n")
        zf.writestr("Products.txt", "\r\n".join(_PRODUCTS) + "\r\n")
        zf.writestr("Submissions.txt", "\r\n".join(_SUBMISSIONS) + "\r\n")
    _write(root / "orangebook" / "x" / "products.txt", _OB_PRODUCTS)
    _write(root / "orangebook" / "x" / "patent.txt", _OB_PATENT)
    _write(root / "orangebook" / "x" / "exclusivity.txt", _OB_EXCLUSIVITY)
    (root / "unii").mkdir(parents=True)
    with zipfile.ZipFile(root / "unii" / "UNIIs.zip", "w") as zf:
        zf.writestr("UNII_Names_4Aug2026.txt", "\n".join(_UNII_NAMES) + "\n")
    (root / "label").mkdir(parents=True)
    with zipfile.ZipFile(root / "label" / "drug-label-0001-of-0014.json.zip", "w") as zf:
        zf.writestr("d.json", json.dumps({"results": _LABELS}))
    (root / "ndc").mkdir(parents=True)
    with zipfile.ZipFile(root / "ndc" / "drug-ndc-0001-of-0001.json.zip", "w") as zf:
        zf.writestr("d.json", json.dumps({"results": _NDC}))
    return RawData(root=root)


# --- the vocabulary index ---------------------------------------------------


def test_only_disease_descriptors_are_indexed(vocabulary: MeshVocabulary) -> None:
    """Trastuzumab is a MeSH descriptor but a drug, and an indications section
    names the drug in its first sentence — indexing it would code every label
    with its own ingredient."""
    index = indications.build_index(vocabulary)
    assert set(index.names) == {"D001249", "D003924"}
    assert "trastuzumab" not in index.by_term


def test_entry_terms_reach_the_descriptor(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert index.by_term["bronchial asthma"] == "D001249"
    assert index.by_term["type 2 diabetes"] == "D003924"


def test_the_longest_term_bounds_the_sweep(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert index.max_words == 4  # "diabetes mellitus, type 2"


def test_a_term_on_two_descriptors_is_counted_not_silently_resolved(
    tmp_path: Path,
) -> None:
    """MeSH lists the same entry term on more than one descriptor. Picking one
    is unavoidable; doing it silently would hide how often the vocabulary is
    ambiguous, so first-in-file wins and the collision is counted."""
    path = tmp_path / "dup.xml"
    path.write_text(
        """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D0001</DescriptorUI>
    <DescriptorName><String>First</String></DescriptorName>
    <TreeNumberList><TreeNumber>C01.001</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>shared name</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D0002</DescriptorUI>
    <DescriptorName><String>Second</String></DescriptorName>
    <TreeNumberList><TreeNumber>C01.002</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>shared name</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
""",
        encoding="utf-8",
    )
    index = indications.build_index(MeshVocabulary(descriptors=path))
    assert index.by_term["shared name"] == "D0001"
    assert index.collisions == 1


# --- coding -----------------------------------------------------------------


def test_a_disease_named_in_the_text_is_coded(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert indications.code("indicated for the treatment of asthma", index) == {
        "D001249"
    }


def test_a_multi_word_term_is_found(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert indications.code("adults with type 2 diabetes", index) == {"D003924"}


def test_a_match_inside_a_longer_match_is_dropped(tmp_path: Path) -> None:
    """MeSH has a descriptor for the bare word 'Disease' as well as for
    'Alzheimer Disease'. Without suppressing the contained match, every label
    naming any disease also coded the generic one, which made 'Disease' the
    most-indicated concept in the corpus at 3,207 applications."""
    path = tmp_path / "generic.xml"
    path.write_text(
        """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D004194</DescriptorUI>
    <DescriptorName><String>Disease</String></DescriptorName>
    <TreeNumberList><TreeNumber>C23.550</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>Disease</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D000544</DescriptorUI>
    <DescriptorName><String>Alzheimer Disease</String></DescriptorName>
    <TreeNumberList><TreeNumber>C10.228.140.380</TreeNumber></TreeNumberList>
    <ConceptList><Concept><TermList>
      <Term><String>Alzheimer Disease</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
""",
        encoding="utf-8",
    )
    index = indications.build_index(MeshVocabulary(descriptors=path))
    assert indications.code("indicated for alzheimer disease", index) == {"D000544"}
    # "Disease" is never an indication, so it codes nothing even alone.
    assert indications.code("in disease of any kind", index) == set()


def test_coding_is_case_insensitive(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert indications.code("BRONCHIAL ASTHMA", index) == {"D001249"}


def test_text_naming_nothing_codes_to_nothing(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert indications.code("indicated for something uncatalogued", index) == set()


def test_a_negated_indication_is_still_coded(vocabulary: MeshVocabulary) -> None:
    """The documented limit of a dictionary sweep: it reads words, not logic.

    A 'Limitations of Use' paragraph saying the drug is NOT for a disease codes
    that disease anyway. This is asserted rather than left implicit because it
    is why these codes are context for a judge and not a filter.
    """
    index = indications.build_index(vocabulary)
    assert indications.code("not indicated for asthma", index) == {"D001249"}


# --- the pass ---------------------------------------------------------------


def test_each_application_gets_its_most_recent_label(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    """An application accumulates label versions; the current one is what it is
    approved for now."""
    report = indications.build(corpus, vocabulary)
    coded = {r.mesh_term for r in report.rows if r.application == "NDA000001"}
    assert coded == {"Asthma"}
    text = {t.application: t for t in report.texts}["NDA000001"]
    assert text.effective_time == "20240101"


def test_a_generic_keeps_its_indication(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    """Unlike section 14, section 1 on an ANDA label is not a false claim: the
    generic really is indicated for that disease."""
    report = indications.build(corpus, vocabulary)
    rows = [r for r in report.rows if r.application == "ANDA000003"]
    assert [r.mesh_term for r in rows] == ["Asthma"]
    assert rows[0].app_type == "ANDA"


def test_the_row_carries_the_drug_identity(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    report = indications.build(corpus, vocabulary)
    row = next(r for r in report.rows if r.application == "NDA000001")
    assert (row.drug, row.unii) == ("ALBUTEROL", "QF8SVZ843E")
    assert row.evidence == indications.FROM_LABEL


def test_a_label_with_no_indications_section_is_not_counted(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    report = indications.build(corpus, vocabulary)
    assert report.labels == len(_LABELS)
    assert report.labels_with_section_1 == 4


def test_a_label_naming_no_application_is_unlinked(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    from service import approval_map

    report = indications.build(corpus, vocabulary)
    assert report.paths[approval_map.VIA_NONE] == 1
    assert "set-orphan" not in {t.application for t in report.texts}


def test_the_text_table_holds_one_row_per_application(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    report = indications.build(corpus, vocabulary)
    assert {t.application for t in report.texts} == {"NDA000001", "ANDA000003"}
    assert report.applications == 2


def test_newlines_are_flattened_out_of_the_text(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    """TSV has no quoting, so the text is normalised here rather than repaired
    by the repository one warning at a time."""
    report = indications.build(corpus, vocabulary)
    text = {t.application: t.text for t in report.texts}["NDA000001"]
    assert "\n" not in text
    assert "for the treatment of asthma" in text


def test_the_text_is_capped(corpus: RawData, vocabulary: MeshVocabulary) -> None:
    report = indications.build(corpus, vocabulary)
    assert all(len(t.text) <= indications.TEXT_LIMIT for t in report.texts)


def test_branded_applications_are_counted_separately(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    report = indications.build(corpus, vocabulary)
    assert report.branded_applications == 1  # NDA000001; the ANDA is not branded


def test_an_application_whose_text_codes_to_nothing_is_counted(
    tmp_path: Path, corpus: RawData
) -> None:
    """Text present but nothing coded is a different fact from no text at all."""
    empty = tmp_path / "empty.xml"
    empty.write_text("<DescriptorRecordSet></DescriptorRecordSet>", encoding="utf-8")
    report = indications.build(corpus, MeshVocabulary(descriptors=empty))
    assert report.applications_coded == 0
    assert report.applications_uncoded == 2
    assert report.rows == ()


def test_the_report_renders_its_numbers(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    rendered = indications.render(indications.build(corpus, vocabulary))
    assert "Indications — label section 1, coded to MeSH" in rendered
    assert "Asthma" in rendered


def test_a_generic_carries_its_ingredient(
    corpus: RawData, vocabulary: MeshVocabulary
) -> None:
    """The trial map resolves branded applications only, because an ANDA is not
    approved on the brand's trials. An indication is different: the generic
    really is indicated for that disease, and its ingredient is known — so the
    drug column must not be blank on it."""
    report = indications.build(corpus, vocabulary)
    row = next(r for r in report.rows if r.application == "ANDA000003")
    assert (row.drug, row.unii) == ("ALBUTEROL", "QF8SVZ843E")


# --- D34: non-indications, generic roots, terms from outside MeSH ------------


def test_registry_aliases_need_enough_single_condition_studies() -> None:
    uc = (("Urothelial Carcinoma",), ("MESH:D002295",))
    assert indications.registry_aliases([uc] * 3) == {"urothelial carcinoma": "D002295"}
    assert indications.registry_aliases([uc] * 2) == {}
    split = [(("Healthy",), ("MESH:D1",)), (("Healthy",), ("MESH:D2",)),
             (("Healthy",), ("MESH:D3",))]
    assert indications.registry_aliases(split) == {}
    assert indications.registry_aliases([(("",), ("MESH:D1",))] * 5) == {}
    assert indications.registry_aliases([(("X y",), ())] * 5) == {}


def test_extra_terms_widen_the_sweep_but_never_overrule_mesh(vocabulary: MeshVocabulary) -> None:
    base = indications.build_index(vocabulary)
    some_term, some_ui = next(iter(base.by_term.items()))
    extra = [
        ("Brand New Disease Name", some_ui),        # taken
        (some_term + " x", "D999999"),              # not a disease descriptor
        ("single", some_ui),                        # one word
        ("Disease Words Here", "D004194"),          # a non-indication
        (some_term, "MESH:D000001"),                # MeSH already has it
    ]
    index = indications.build_index(vocabulary, extra=extra)
    assert index.added_terms == 1
    assert index.by_term["brand new disease name"] == some_ui
    assert index.by_term[some_term] == base.by_term[some_term]
    assert indications.code("treating brand new disease name today", index) == {some_ui}


def test_branch_roots_are_generic(vocabulary: MeshVocabulary) -> None:
    index = indications.build_index(vocabulary)
    assert "D002277" in index.generic


def test_a_root_gives_way_to_a_specific_disease(tmp_path: Path) -> None:
    def record(ui: str, name: str, tree: str) -> str:
        return (f"<DescriptorRecord><DescriptorUI>{ui}</DescriptorUI>"
                f"<DescriptorName><String>{name}</String></DescriptorName>"
                f"<TreeNumberList><TreeNumber>{tree}</TreeNumber></TreeNumberList>"
                f"<ConceptList><Concept><TermList><Term><String>{name}</String></Term>"
                f"</TermList></Concept></ConceptList></DescriptorRecord>")
    path = tmp_path / "desc.xml"
    path.write_text(
        '<?xml version="1.0"?><DescriptorRecordSet>'
        + record("D009369", "Neoplasms", "C04")
        + record("D008545", "Melanoma", "C04.557.465.625.650.510")
        + "</DescriptorRecordSet>",
        encoding="utf-8",
    )
    index = indications.build_index(MeshVocabulary(descriptors=path))
    assert "D009369" in index.generic and "D008545" not in index.generic
    assert indications.code("neoplasms including melanoma", index) == {"D008545"}
    assert indications.code("neoplasms", index) == {"D009369"}
