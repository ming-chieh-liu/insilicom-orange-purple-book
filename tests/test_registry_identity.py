"""Tests for S3's exact ids for registry interventions and conditions (D44)."""

from __future__ import annotations

from collections.abc import Iterable

from dao.ikraph2_entities import Node
from service import registry_identity as ri
from source.unii_mesh import MeshIndex


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

_MESH = MeshIndex(
    uniis={"MESH:C582435": frozenset({"DPT0O3T46P"}), "MESH:C999": frozenset({"NOTPHERE00"})},
    by_term={"pembrolizumab": frozenset({"MESH:C582435"}), "nsclc": frozenset({"MESH:D002289"}),
             "ambiguous term": frozenset({"MESH:D1", "MESH:D2"}), "rare thing": frozenset({"MESH:D2"})},
    names={}, headings={})
_UNII_NAMES = {"keytruda": frozenset({"DPT0O3T46P"}), "mk-3475": frozenset({"DPT0O3T46P"}),
               "shared name": frozenset({"U1", "U2"})}


def test_an_nlm_drug_id_reaches_the_drug_entity_through_its_unii() -> None:
    rows = ri.drug_mesh(["MESH:C582435", "MESH:C999", "NOT-MESH"], _MESH, FakeGraph())
    assert set(rows) == {"MESH:C582435", "MESH:C999"}
    pembro = rows["MESH:C582435"]
    # The TherapyPreventions entity via the UNII, not the Chemical holding the MeSH id.
    assert (pembro.biokdeid, pembro.unii, pembro.mesh_id) == (
        "2700000295", "DPT0O3T46P", "MESH:C582435")
    # No drug entity for its UNII: the Chemical entity holding the exact id.
    assert rows["MESH:C999"].biokdeid == "77"


def test_an_nlm_disease_id_keeps_its_mesh_and_gains_one_entity_only() -> None:
    rows = ri.disease_mesh(["MESH:D002289", "MESH:D2", "MESH:D404"], FakeGraph())
    assert rows["MESH:D002289"].biokdeid == "55633"
    assert rows["MESH:D2"].biokdeid == ""        # two entities: none chosen
    assert rows["MESH:D404"] == ri.Resolved("MESH:D404", "", "", "MESH:D404", "", "disease",
                                            ri.BASIS_NLM)


def test_intervention_strings_resolve_by_every_exact_route() -> None:
    rows = ri.drug_strings(["Pembrolizumab", "Keytruda", "MK-3475", "Two Drugs", "Placebo",
                            "PB", "Shared Name", "Nothing Known"], _MESH, _UNII_NAMES,
                           FakeGraph())
    assert set(rows) == {"Pembrolizumab", "Keytruda", "MK-3475"}
    pembro = rows["Pembrolizumab"]
    assert (pembro.biokdeid, pembro.unii, pembro.mesh_id) == (
        "2700000295", "DPT0O3T46P", "MESH:C582435")
    assert pembro.basis == "ikraph2_name;mesh_term"
    # The brand: iKraph2's own brand entity, plus pembrolizumab through the UNII list.
    keytruda = rows["Keytruda"]
    assert set(keytruda.biokdeid.split(";")) == {"2700044997", "2700000295"}
    assert ri.keys_of(keytruda) >= {"IK2:2700000295", "UNII:DPT0O3T46P", "MESH:C582435"}
    assert rows["MK-3475"].basis == ri.BASIS_UNII_NAME


def test_a_dose_is_stripped_only_as_a_last_resort_and_under_nlm() -> None:
    rows = ri.drug_strings(["Pembrolizumab 200 mg IV", "Pembrolizumab 2 mg/kg"], _MESH,
                           _UNII_NAMES, FakeGraph(),
                           nlm_of={"Pembrolizumab 2 mg/kg": {"MESH:D000001"}})
    assert rows["Pembrolizumab 200 mg IV"].basis.startswith(ri.DOSE_STRIPPED)
    # NLM coded those trials otherwise: the MeSH id goes, the entity stays.
    contradicted = rows["Pembrolizumab 2 mg/kg"]
    assert (contradicted.mesh_id, contradicted.biokdeid) == ("", "2700000295")


def test_condition_strings_resolve_to_one_disease_entity_and_its_mesh_as_stored() -> None:
    rows = ri.condition_strings(["Non-Small Cell  Lung Cancer", "Eosinophilic Asthma", "NSCLC",
                                 "Ambiguous Term", "Rare Thing", "BD"], _MESH, FakeGraph())
    assert rows["Non-Small Cell  Lung Cancer"].biokdeid == "55633"
    # Kept as stored: a concept id, not rolled up to its descriptor.
    assert rows["Eosinophilic Asthma"].mesh_id == "MESH:M0001"
    assert (rows["NSCLC"].mesh_id, rows["NSCLC"].biokdeid) == ("MESH:D002289", "55633")
    assert "Ambiguous Term" not in rows and "BD" not in rows
    assert (rows["Rare Thing"].mesh_id, rows["Rare Thing"].biokdeid) == ("MESH:D2", "")


def test_the_unii_list_indexes_every_name() -> None:
    assert ri.unii_names([{"UNII": "", "NAME": "x"},
                          {"UNII": "U1", "NAME": " Jinarc ", "DISPLAY_NAME": ""}]) == {
        "jinarc": frozenset({"U1"})}
