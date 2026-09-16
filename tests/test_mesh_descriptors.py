"""Tests for reading the MeSH descriptor release."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from config.settings import MeshVocabulary
from source import mesh_descriptors
from source.mesh_descriptors import (
    DISEASE_BRANCHES,
    MIN_TERM_LENGTH,
    Descriptor,
    diseases,
    read,
)

# Two disease descriptors and one that is not a disease, shaped exactly as the
# release writes them: an entry term list under Concept/Term, and one or more
# TreeNumber elements.
_XML = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D001249</DescriptorUI>
    <DescriptorName><String>Asthma</String></DescriptorName>
    <TreeNumberList><TreeNumber>C08.127.108</TreeNumber>
      <TreeNumber>C20.543.480.680.095</TreeNumber></TreeNumberList>
    <ConceptList><Concept>
      <ConceptUI>M0001393</ConceptUI>
      <TermList>
        <Term><String>Asthma</String></Term>
        <Term><String>Bronchial Asthma</String></Term>
        <Term><String>AB</String></Term>
      </TermList>
    </Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D001714</DescriptorUI>
    <DescriptorName><String>Bipolar Disorder</String></DescriptorName>
    <TreeNumberList><TreeNumber>F03.084.100</TreeNumber></TreeNumberList>
    <ConceptList><Concept>
      <ConceptUI>M0002344</ConceptUI>
      <TermList>
        <Term><String>Bipolar Disorder</String></Term>
        <Term><String>Manic Depression</String></Term>
      </TermList>
    </Concept></ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D000068579</DescriptorUI>
    <DescriptorName><String>Trastuzumab</String></DescriptorName>
    <TreeNumberList><TreeNumber>D12.776.124.486.485.114.224</TreeNumber></TreeNumberList>
    <ConceptList><Concept>
      <TermList><Term><String>Trastuzumab</String></Term></TermList>
    </Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
"""


@pytest.fixture()
def vocabulary(tmp_path: Path) -> MeshVocabulary:
    path = tmp_path / "desc.xml"
    path.write_text(_XML, encoding="utf-8")
    return MeshVocabulary(descriptors=path)


def test_every_descriptor_is_read(vocabulary: MeshVocabulary) -> None:
    assert [d.ui for d in read(vocabulary)] == [
        "D001249",
        "D001714",
        "D000068579",
    ]


def test_the_preferred_name_is_kept(vocabulary: MeshVocabulary) -> None:
    first = next(iter(read(vocabulary)))
    assert first.name == "Asthma"


def test_entry_terms_are_lower_cased_and_deduplicated(
    vocabulary: MeshVocabulary,
) -> None:
    """The FDA does not write MeSH: 'Bronchial Asthma' is how a label may say it."""
    first = next(iter(read(vocabulary)))
    assert first.terms == ("asthma", "bronchial asthma")


def test_short_entry_terms_are_dropped(vocabulary: MeshVocabulary) -> None:
    """'AB' is a MeSH acronym that collides with ordinary text; 4 chars is the floor."""
    first = next(iter(read(vocabulary)))
    assert "ab" not in first.terms
    assert MIN_TERM_LENGTH == 4


def test_every_tree_number_is_kept(vocabulary: MeshVocabulary) -> None:
    """A descriptor sits in several places and any of them may be the disease one."""
    first = next(iter(read(vocabulary)))
    assert first.tree_numbers == ("C08.127.108", "C20.543.480.680.095")


def test_only_the_disease_branches_are_diseases(vocabulary: MeshVocabulary) -> None:
    """Trastuzumab is a MeSH descriptor, but a drug — branch D, not C or F03."""
    assert [d.ui for d in diseases(vocabulary)] == ["D001249", "D001714"]
    assert DISEASE_BRANCHES == ("C", "F03")


def test_mental_disorders_count_as_diseases(vocabulary: MeshVocabulary) -> None:
    """MeSH files them under F03, outside the C branch, so C alone would lose them."""
    assert "D001714" in {d.ui for d in diseases(vocabulary)}


def test_a_descriptor_with_no_tree_number_is_not_a_disease() -> None:
    assert not Descriptor("D9", "x", ("x",), ()).is_disease


def test_a_missing_file_is_a_warning_and_no_rows(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The vocabulary lives outside this repo, so absence is reported, not raised."""
    absent = MeshVocabulary(descriptors=tmp_path / "nope.xml")
    with caplog.at_level(logging.WARNING):
        assert list(read(absent)) == []
    assert "no MeSH descriptor file" in caplog.text


def test_a_record_without_an_id_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "d.xml"
    path.write_text(
        "<DescriptorRecordSet><DescriptorRecord>"
        "<DescriptorName><String>x</String></DescriptorName>"
        "</DescriptorRecord></DescriptorRecordSet>",
        encoding="utf-8",
    )
    assert list(read(MeshVocabulary(descriptors=path))) == []


def test_exists_reports_whether_the_release_is_present(
    vocabulary: MeshVocabulary, tmp_path: Path
) -> None:
    assert vocabulary.exists
    assert not MeshVocabulary(descriptors=tmp_path / "absent.xml").exists


def test_allowable_qualifiers_are_not_entry_terms(tmp_path: Path) -> None:
    """A descriptor carries its allowable qualifiers — 'blood', 'diagnosis',
    'drug therapy' — alongside its entry terms. They are shared by thousands of
    descriptors and are synonyms for nothing.

    Taking every <String> in the record indexed them, and because MeSH files
    'Abdomen, Acute' first, that descriptor claimed all of them and became the
    most-indicated disease in the corpus at 5,633 applications.
    """
    path = tmp_path / "q.xml"
    path.write_text(
        """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D000006</DescriptorUI>
    <DescriptorName><String>Abdomen, Acute</String></DescriptorName>
    <TreeNumberList><TreeNumber>C23.888.821.030</TreeNumber></TreeNumberList>
    <AllowableQualifiersList>
      <AllowableQualifier><QualifierReferredTo>
        <QualifierName><String>drug therapy</String></QualifierName>
      </QualifierReferredTo></AllowableQualifier>
      <AllowableQualifier><QualifierReferredTo>
        <QualifierName><String>diagnosis</String></QualifierName>
      </QualifierReferredTo></AllowableQualifier>
    </AllowableQualifiersList>
    <ConceptList><Concept><TermList>
      <Term><String>Abdomen, Acute</String></Term>
      <Term><String>Acute Abdomen</String></Term>
    </TermList></Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
""",
        encoding="utf-8",
    )
    terms = next(iter(read(MeshVocabulary(descriptors=path)))).terms
    assert terms == ("abdomen, acute", "acute abdomen")
    assert "drug therapy" not in terms
    assert "diagnosis" not in terms


def test_concept_ids_are_read(vocabulary: MeshVocabulary) -> None:
    """MeSH numbers a concept separately from the descriptor heading it, and
    other vocabularies cite either."""
    by_ui = {d.ui: d for d in mesh_descriptors.read(vocabulary)}
    assert by_ui["D001249"].concept_uis == ("M0001393",)


def test_a_descriptor_with_no_concept_ui_has_none(
    vocabulary: MeshVocabulary,
) -> None:
    by_ui = {d.ui: d for d in mesh_descriptors.read(vocabulary)}
    assert by_ui["D000068579"].concept_uis == ()


def test_the_rollup_maps_concepts_to_their_descriptor(
    vocabulary: MeshVocabulary,
) -> None:
    rollup = mesh_descriptors.concept_to_descriptor(vocabulary)
    assert rollup["M0001393"] == "D001249"
    assert rollup["M0002344"] == "D001714"


def test_a_descriptor_maps_to_itself(vocabulary: MeshVocabulary) -> None:
    """So a caller can pass any MeSH id through and get a descriptor or
    nothing, without first asking which kind it holds."""
    rollup = mesh_descriptors.concept_to_descriptor(vocabulary)
    assert rollup["D001249"] == "D001249"


def test_an_unknown_id_is_absent_from_the_rollup(
    vocabulary: MeshVocabulary,
) -> None:
    assert "M9999999" not in mesh_descriptors.concept_to_descriptor(vocabulary)
