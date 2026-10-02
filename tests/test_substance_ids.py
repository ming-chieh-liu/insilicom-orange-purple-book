"""Tests for the one substance dictionary, UNII <-> MeSH <-> PubChem <-> RxCUI (D45)."""

from __future__ import annotations

from pathlib import Path

from source import substance_ids as si
from source import unii_bridge
from source.unii_mesh import MeshIndex, mesh_index

_DESC = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D064098</DescriptorUI>
    <DescriptorName><String>Esomeprazole</String></DescriptorName>
    <ConceptList>
      <Concept PreferredConceptYN="Y">
        <RegistryNumberList><RegistryNumber>N3PA6559FT</RegistryNumber></RegistryNumberList>
        <RelatedRegistryNumberList>
          <RelatedRegistryNumber>R6DXU4WAY9</RelatedRegistryNumber>
          <RelatedRegistryNumber>OTHERSALT0</RelatedRegistryNumber>
          <RelatedRegistryNumber>161796-78-7</RelatedRegistryNumber>
        </RelatedRegistryNumberList>
        <TermList><Term><String>Esomeprazole</String></Term></TermList>
      </Concept>
      <Concept PreferredConceptYN="N">
        <RegistryNumberList><RegistryNumber>0</RegistryNumber></RegistryNumberList>
        <TermList><Term><String>Nexium</String></Term></TermList>
      </Concept>
      <Concept PreferredConceptYN="N">
        <RegistryNumberList><RegistryNumber>ISOMER0000</RegistryNumber></RegistryNumberList>
        <TermList><Term><String>isoprazole</String></Term></TermList>
      </Concept>
      <Concept PreferredConceptYN="N">
        <RegistryNumberList><RegistryNumber>STRONTIUM0</RegistryNumber></RegistryNumberList>
        <TermList><Term><String>esomeprazole strontium</String></Term></TermList>
      </Concept>
    </ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
"""

_SUPP = """<?xml version="1.0"?>
<SupplementalRecordSet>
  <SupplementalRecord>
    <SupplementalRecordUI>C000629551</SupplementalRecordUI>
    <SupplementalRecordName><String>zanubrutinib</String></SupplementalRecordName>
    <ConceptList><Concept PreferredConceptYN="Y">
      <RegistryNumberList><RegistryNumber>AG9MHG098Z</RegistryNumber></RegistryNumberList>
      <TermList><Term><String>zanubrutinib</String></Term></TermList>
    </Concept></ConceptList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000709826</SupplementalRecordUI>
    <SupplementalRecordName><String>SMOFlipid</String></SupplementalRecordName>
    <ConceptList><Concept PreferredConceptYN="Y">
      <RegistryNumberList><RegistryNumber>C9H2L21V7U</RegistryNumber></RegistryNumberList>
      <TermList><Term><String>SMOFlipid</String></Term></TermList>
    </Concept></ConceptList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000001</SupplementalRecordUI>
    <SupplementalRecordName><String>first claim</String></SupplementalRecordName>
    <ConceptList><Concept PreferredConceptYN="Y">
      <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
    </Concept></ConceptList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000002</SupplementalRecordUI>
    <SupplementalRecordName><String>second claim</String></SupplementalRecordName>
    <ConceptList><Concept PreferredConceptYN="Y">
      <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
    </Concept></ConceptList>
  </SupplementalRecord>
</SupplementalRecordSet>
"""

_RECORDS = [
    {"UNII": "N3PA6559FT", "DISPLAY_NAME": "ESOMEPRAZOLE", "PUBCHEM": "9568614", "RXCUI": "283742"},
    {"UNII": "R6DXU4WAY9", "DISPLAY_NAME": "ESOMEPRAZOLE MAGNESIUM", "PUBCHEM": "9568613",
     "RXCUI": ""},
    {"UNII": "OTHERSALT0", "DISPLAY_NAME": "SOMETHING ELSE", "PUBCHEM": "", "RXCUI": ""},
    {"UNII": "STRONTIUM0", "DISPLAY_NAME": "ESOMEPRAZOLE STRONTIUM", "PUBCHEM": "", "RXCUI": ""},
    {"UNII": "ISOMER0000", "DISPLAY_NAME": "ISOPRAZOLE", "PUBCHEM": "", "RXCUI": ""},
    {"UNII": "AG9MHG098Z", "DISPLAY_NAME": "ZANUBRUTINIB", "PUBCHEM": "135565884", "RXCUI": ""},
    {"UNII": "C9H2L21V7U", "DISPLAY_NAME": "MEDIUM-CHAIN TRIGLYCERIDES", "PUBCHEM": "",
     "RXCUI": ""},
    {"UNII": "SHAREDUNII", "DISPLAY_NAME": "FIRST CLAIM", "PUBCHEM": "77", "RXCUI": "5"},
    {"UNII": "TWINUNII00", "DISPLAY_NAME": "TWIN", "PUBCHEM": "77", "RXCUI": "5"},
    {"UNII": "", "DISPLAY_NAME": "NO UNII", "PUBCHEM": "1", "RXCUI": ""},
]


def _index(tmp_path: Path) -> MeshIndex:
    desc, supp = tmp_path / "desc.xml", tmp_path / "supp.xml"
    desc.write_text(_DESC, encoding="utf-8")
    supp.write_text(_SUPP, encoding="utf-8")
    return mesh_index([desc, supp, tmp_path / "absent.xml"])


def test_the_index_separates_preferred_other_and_related_registry_numbers(
        tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert index.uniis["MESH:D064098"] == {"N3PA6559FT", "ISOMER0000", "STRONTIUM0"}
    assert index.preferred["MESH:D064098"] == {"N3PA6559FT"}
    # CAS numbers are not UNIIs; related forms are kept apart from the record's own.
    assert index.related["MESH:D064098"] == {"R6DXU4WAY9", "OTHERSALT0"}


def test_each_unii_maps_to_one_record_by_its_role(tmp_path: Path) -> None:
    dictionary, report = si.build(_index(tmp_path), _RECORDS, {})
    rows = dictionary.by_unii
    assert (rows["N3PA6559FT"].mesh_id, rows["N3PA6559FT"].mesh_role) == (
        "MESH:D064098", si.ROLE_PREFERRED)
    # Another concept: a salt form when its name contains the record's name.
    assert rows["STRONTIUM0"].mesh_role == si.ROLE_FORM
    assert rows["ISOMER0000"].mesh_role == si.ROLE_PRIMARY
    # A salt filed as a related form, its name containing the record's name.
    salt = rows["R6DXU4WAY9"]
    assert (salt.mesh_id, salt.mesh_role, salt.moiety_unii) == (
        "MESH:D064098", si.ROLE_RELATED, "N3PA6559FT")
    # A related number whose name does not contain the record's: left out.
    assert "OTHERSALT0" not in rows
    # NLM's curation slip (no shared name) and a UNII two records claim: no MeSH,
    # and with no other exact id, no row.
    assert "C9H2L21V7U" not in rows and "SHAREDUNII" not in rows
    assert dictionary.mesh_of("N3PA6559FT") == "MESH:D064098"
    assert dictionary.mesh_of("NOWHERE000") == ""
    assert report.related == 1 and report.several_records == 1 and report.no_shared_name == 2


def test_pubchem_and_rxcui_are_kept_only_one_to_one(tmp_path: Path) -> None:
    dictionary, _ = si.build(_index(tmp_path), _RECORDS, {})
    esomeprazole = dictionary.by_unii["N3PA6559FT"]
    assert (esomeprazole.pubchem, esomeprazole.rxcui) == ("PubChem:CID9568614", "RXCUI:283742")
    # Two UNIIs share CID 77 and RxCUI 5: neither keeps them.
    assert "SHAREDUNII" not in dictionary.by_unii and "TWINUNII00" not in dictionary.by_unii
    assert dictionary.by_unii["R6DXU4WAY9"].rxcui == ""


def test_the_moiety_is_the_preferred_concepts_unii(tmp_path: Path) -> None:
    dictionary, report = si.build(_index(tmp_path), _RECORDS, {})
    assert dictionary.moiety_of == {"MESH:D064098": "N3PA6559FT",
                                    "MESH:C000629551": "AG9MHG098Z"}
    assert report.moieties == 2


def test_extra_names_can_supply_the_shared_name(tmp_path: Path) -> None:
    records = [r | {"DISPLAY_NAME": ""} if r["UNII"] == "AG9MHG098Z" else r for r in _RECORDS]
    without, _ = si.build(_index(tmp_path), records, {})
    assert without.mesh_of("AG9MHG098Z") == ""
    with_fda, _ = si.build(_index(tmp_path), records, {}, {"AG9MHG098Z": ["Zanubrutinib"]})
    assert with_fda.mesh_of("AG9MHG098Z") == "MESH:C000629551"
    with_list, _ = si.build(_index(tmp_path), records, {"AG9MHG098Z": {"zanubrutinib"}})
    assert with_list.mesh_of("AG9MHG098Z") == "MESH:C000629551"


def test_bridges_pair_each_moiety_with_its_cid(tmp_path: Path) -> None:
    dictionary, report = si.build(_index(tmp_path), _RECORDS, {})
    assert si.bridges(dictionary) == [
        unii_bridge.Bridge("AG9MHG098Z", "PubChem:CID135565884", "MESH:C000629551"),
        unii_bridge.Bridge("N3PA6559FT", "PubChem:CID9568614", "MESH:D064098"),
    ]
    text = si.render(report)
    assert "related form (salt, ester)" in text and "salt/ester concept" in text
    assert report.form == 1 and report.primary == 1
