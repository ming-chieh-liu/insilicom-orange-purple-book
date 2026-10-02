"""Tests for the exact, one-to-one PubChem <-> MeSH bridge (D39)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from source import unii_bridge, unii_mesh

_DESC = """<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D000001</DescriptorUI>
    <DescriptorName><String>Aspirin</String></DescriptorName>
    <RegistryNumberList><RegistryNumber>R16CO5Y76E</RegistryNumber></RegistryNumberList>
    <RelatedRegistryNumberList><RegistryNumber>50-78-2</RegistryNumber></RelatedRegistryNumberList>
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
    <SupplementalRecordUI>C000629551</SupplementalRecordUI>
    <SupplementalRecordName><String>zanubrutinib</String></SupplementalRecordName>
    <RegistryNumberList><RegistryNumber>AG9MHG098Z</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000001</SupplementalRecordUI>
    <SupplementalRecordName><String>shares a UNII</String></SupplementalRecordName>
    <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
  <SupplementalRecord>
    <SupplementalRecordUI>C000000002</SupplementalRecordUI>
    <SupplementalRecordName><String>shares it too</String></SupplementalRecordName>
    <RegistryNumberList><RegistryNumber>SHAREDUNII</RegistryNumber></RegistryNumberList>
  </SupplementalRecord>
</SupplementalRecordSet>
"""

_RECORDS = (
    "UNII\tDISPLAY NAME\tPUBCHEM\n"
    "AG9MHG098Z\tZANUBRUTINIB\t135565884\n"
    "R16CO5Y76E\tASPIRIN\t2244\n"
    "SHAREDUNII\tAMBIGUOUS\t77\n"
    "TWOCIDSXXX\tTWO CIDS\t5|6\n"
)


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    desc, supp, records = tmp_path / "desc.xml", tmp_path / "supp.xml", tmp_path / "UNII_Data.zip"
    desc.write_text(_DESC, encoding="utf-8")
    supp.write_text(_SUPP, encoding="utf-8")
    with zipfile.ZipFile(records, "w") as archive:
        archive.writestr("UNII_Records_4Aug2026.txt", _RECORDS)
    return desc, supp, records


def test_only_one_to_one_pairings_become_bridges(tmp_path: Path) -> None:
    """A UNII shared by two MeSH records, or with two CIDs, bridges nothing."""
    desc, supp, records = _files(tmp_path)
    bridges = unii_bridge.build(
        unii_bridge.unii_to_pubchem(unii_bridge.read_unii_records(records)),
        unii_bridge.mesh_to_unii(
            (m, list(u)) for m, u in unii_mesh.mesh_index([desc, supp]).uniis.items()),
    )
    assert bridges == [
        unii_bridge.Bridge("AG9MHG098Z", "PubChem:CID135565884", "MESH:C000629551"),
        unii_bridge.Bridge("R16CO5Y76E", "PubChem:CID2244", "MESH:D000001"),
    ]


def test_mesh_names_cover_descriptors_and_supplementary_records(tmp_path: Path) -> None:
    desc, supp, _ = _files(tmp_path)
    names = unii_mesh.mesh_index([desc, supp, tmp_path / "absent.xml"]).names
    assert names["MESH:D000001"] == "Aspirin"
    assert names["MESH:C000629551"] == "zanubrutinib"


def test_missing_inputs_yield_nothing(tmp_path: Path) -> None:
    assert list(unii_bridge.read_unii_records(tmp_path / "absent.zip")) == []
    assert unii_bridge.read_bridges(tmp_path / "absent.tsv") == []
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "")
    assert list(unii_bridge.read_unii_records(empty)) == []


def test_bridges_round_trip_through_the_tsv(tmp_path: Path) -> None:
    path = tmp_path / "id_bridge.tsv"
    path.write_text("unii\tpubchem\tmesh\nU\tPubChem:CID1\tMESH:C1\n", encoding="utf-8")
    assert unii_bridge.equivalents(unii_bridge.read_bridges(path)) == {
        "PubChem:CID1": "MESH:C1", "MESH:C1": "PubChem:CID1",
    }
