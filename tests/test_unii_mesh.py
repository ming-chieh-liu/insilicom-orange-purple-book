"""Tests for the MeSH index (D42, D44, D45)."""

from __future__ import annotations

from pathlib import Path

from source import unii_mesh

_DESC = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D064098</DescriptorUI>
    <DescriptorName><String>Esomeprazole</String></DescriptorName>
    <ConceptList><Concept PreferredConceptYN="Y">
      <TermList>
        <Term><String>Esomeprazole</String></Term>
        <Term><String>Nexium</String></Term>
      </TermList>
      <RegistryNumberList><RegistryNumber>N3PA6559FT</RegistryNumber></RegistryNumberList>
      <RelatedRegistryNumberList>
        <RelatedRegistryNumber>R6DXU4WAY9</RelatedRegistryNumber>
        <RelatedRegistryNumber>161796-78-7</RelatedRegistryNumber>
      </RelatedRegistryNumberList>
    </Concept></ConceptList>
    <PharmacologicalActionList><PharmacologicalAction><DescriptorReferredTo>
      <DescriptorName><String>Proton Pump Inhibitors</String></DescriptorName>
    </DescriptorReferredTo></PharmacologicalAction></PharmacologicalActionList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D000002</DescriptorUI>
    <DescriptorName><String>No Registry</String></DescriptorName>
  </DescriptorRecord>
</DescriptorRecordSet>
"""

_SUPP = """<?xml version="1.0"?>
<SupplementalRecordSet>
  <SupplementalRecord>
    <SupplementalRecordUI>C000709826</SupplementalRecordUI>
    <SupplementalRecordName><String>SMOFlipid</String></SupplementalRecordName>
    <ConceptList><Concept><TermList><Term><String>SMOFlipid</String></Term></TermList>
    </Concept></ConceptList>
    <RegistryNumberList><RegistryNumber>C9H2L21V7U</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000001</SupplementalRecordUI>
    <SupplementalRecordName><String>first claim</String></SupplementalRecordName>
    <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000002</SupplementalRecordUI>
    <SupplementalRecordName><String>second claim</String></SupplementalRecordName>
    <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
</SupplementalRecordSet>
"""


def test_the_index_reads_registry_numbers_by_kind(tmp_path: Path) -> None:
    desc, supp = tmp_path / "desc.xml", tmp_path / "supp.xml"
    desc.write_text(_DESC, encoding="utf-8")
    supp.write_text(_SUPP, encoding="utf-8")
    index = unii_mesh.mesh_index([desc, supp, tmp_path / "absent.xml"])
    assert index.uniis["MESH:D064098"] == {"N3PA6559FT"}
    assert index.preferred["MESH:D064098"] == {"N3PA6559FT"}
    assert index.related["MESH:D064098"] == {"R6DXU4WAY9"}
    # Entry terms only: a pharmacological action is not a name of the drug.
    assert "proton pump inhibitors" not in index.by_term
    assert "MESH:D000002" not in index.uniis
    # Registry numbers outside a concept are not read.
    assert "MESH:C000709826" not in index.uniis


def test_unii_names_skip_rows_without_a_unii() -> None:
    assert unii_mesh.unii_names([{"UNII": "", "NAME": "x"},
                                 {"UNII": "N3PA6559FT", "NAME": "Nexium",
                                  "DISPLAY_NAME": ""}]) == {"N3PA6559FT": {"nexium"}}


def test_the_index_keeps_terms_names_and_supplementary_headings(tmp_path: Path) -> None:
    desc, supp = tmp_path / "desc.xml", tmp_path / "supp.xml"
    desc.write_text(_DESC, encoding="utf-8")
    supp.write_text(_SUPP.replace(
        "<SupplementalRecordName><String>SMOFlipid</String></SupplementalRecordName>",
        "<SupplementalRecordName><String>SMOFlipid</String></SupplementalRecordName>"
        "<HeadingMappedToList><HeadingMappedTo><DescriptorReferredTo>"
        "<DescriptorUI>*D005217</DescriptorUI></DescriptorReferredTo></HeadingMappedTo>"
        "</HeadingMappedToList>"), encoding="utf-8")
    index = unii_mesh.mesh_index([desc, supp])
    assert index.by_term["nexium"] == {"MESH:D064098"}
    assert index.names["MESH:D000002"] == "No Registry"
    assert index.headings == {"MESH:C000709826": {"MESH:D005217"}}
    assert unii_mesh.exact_key(" Nexium  ") == "nexium"
