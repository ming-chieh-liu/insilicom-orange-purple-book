"""Tests for UNII -> MeSH by MeSH's own registry numbers (D42)."""

from __future__ import annotations

from pathlib import Path

from source import unii_mesh

_DESC = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D064098</DescriptorUI>
    <DescriptorName><String>Esomeprazole</String></DescriptorName>
    <ConceptList><Concept><TermList>
      <Term><String>Esomeprazole</String></Term>
      <Term><String>Nexium</String></Term>
    </TermList></Concept></ConceptList>
    <RegistryNumberList><RegistryNumber>N3PA6559FT</RegistryNumber></RegistryNumberList>
    <RelatedRegistryNumberList>
      <RegistryNumber>R6DXU4WAY9</RegistryNumber><RegistryNumber>161796-78-7</RegistryNumber>
    </RelatedRegistryNumberList>
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


def _records(tmp_path: Path) -> list[unii_mesh.MeshRecord]:
    desc, supp = tmp_path / "desc.xml", tmp_path / "supp.xml"
    desc.write_text(_DESC, encoding="utf-8")
    supp.write_text(_SUPP, encoding="utf-8")
    return list(unii_mesh.mesh_records([desc, supp, tmp_path / "absent.xml"]))


def test_records_carry_entry_terms_and_only_unii_shaped_numbers(tmp_path: Path) -> None:
    records = {r.mesh: r for r in _records(tmp_path)}
    esomeprazole = records["MESH:D064098"]
    assert esomeprazole.uniis == {"N3PA6559FT", "R6DXU4WAY9"}
    # Entry terms only: a pharmacological action is not a name of the drug.
    assert esomeprazole.terms == {"esomeprazole", "nexium"}
    assert "MESH:D000002" not in records


def test_salts_fold_to_their_record_and_ambiguous_or_nameless_uniis_are_left_out(
        tmp_path: Path) -> None:
    wanted = {
        "R6DXU4WAY9": {"ESOMEPRAZOLE MAGNESIUM"},
        "C9H2L21V7U": {"MEDIUM-CHAIN TRIGLYCERIDES"},
        "SHAREDUNII": {"FIRST CLAIM"},
        "NOWHERE000": {"NOTHING"},
    }
    names = {"R6DXU4WAY9": {"esomeprazole magnesium", "esomeprazole"}}
    rows, report = unii_mesh.build(_records(tmp_path), wanted, names)
    assert rows == [unii_mesh.UniiMesh("R6DXU4WAY9", "MESH:D064098", "Esomeprazole", 2)]
    assert report == unii_mesh.MapReport(asked=4, mapped=1, no_record=1,
                                         several_records=1, no_shared_name=1)
    assert "no shared name (left out)" in unii_mesh.render(report)


def test_the_fda_name_alone_can_supply_the_shared_name(tmp_path: Path) -> None:
    rows, _ = unii_mesh.build(_records(tmp_path), {"N3PA6559FT": {"Esomeprazole"}}, {})
    assert [r.mesh_id for r in rows] == ["MESH:D064098"]


def test_unii_names_skip_rows_without_a_unii() -> None:
    assert unii_mesh.unii_names([{"UNII": "", "NAME": "x"},
                                 {"UNII": "N3PA6559FT", "NAME": "Nexium",
                                  "DISPLAY_NAME": ""}]) == {"N3PA6559FT": {"nexium"}}


def test_the_table_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "unii_mesh.tsv"
    assert unii_mesh.read(path) == {}
    path.write_text("unii\tmesh_id\tmesh_name\trecord_uniis\tbasis\n"
                    "R6DXU4WAY9\tMESH:D064098\tEsomeprazole\t2\tunii_registry\n",
                    encoding="utf-8")
    assert unii_mesh.read(path) == {"R6DXU4WAY9": "MESH:D064098"}
