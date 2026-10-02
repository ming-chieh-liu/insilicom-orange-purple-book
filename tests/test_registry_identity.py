"""Tests for S3's exact ids for registry interventions and conditions (D44, D45)."""

from __future__ import annotations

from collections.abc import Iterable

from dao.ikraph2_entities import Node
from service import registry_identity as ri
from source.substance_ids import Dictionary, Substance
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


def _named(node: Node, role: str = "canonical") -> Node:
    return node._replace(role=role)


_TP, _CHEM = "TherapyPreventions", "Chemical"
_PEMBRO = Node("2700000295", _TP, "PEMBROLIZUMAB", "UNII:DPT0O3T46P", 31352)
_KEYTRUDA = Node("2700044997", _TP, "keytruda", "", 90)
_PEMBRO_CHEM = Node("6308113", _CHEM, "pembrolizumab", "MESH:C582435", 4)
_AFATINIB = Node("2700001344", _TP, "AFATINIB", "UNII:41UD74L59M", 8098)
_ADALIMUMAB = Node("2700000500", _TP, "ADALIMUMAB", "UNII:FYS6T7F842", 9000)
_ADENOSINE = Node("17418", _CHEM, "adenosine", "MESH:D000241", 185845)
_ADENOSINES = Node("2700001601", _TP, "adenosines", "CHEBI:22260", 6677)
_TRACER = Node("7705595", _CHEM, "(s,s)-[11c]mrb", "MESH:C524178", 3)
_TALK = Node("2700001329", _TP, "motivational interviewing", "", 50)
_SURGERY_TP = Node("2700009999", _TP, "cardiac surgical procedure", "", 40)
_SURGERY_CHEM = Node("5550000", _CHEM, "cardiac surgery", "MESH:D006348", 2)
_TETRA_CHEM = Node("86762", _CHEM, "tetracycline", "MESH:D013752", 900)
_TETRACYCLINES = Node("2700000090", _TP, "tetracyclines", "CHEBI:26895", 5000)
_TP_BY_MESH = Node("88", _TP, "only tp by mesh", "MESH:C888", 1)
_NSCLC = Node("55633", "Disease", "carcinoma, non-small-cell lung", "MESH:D002289", 107101)
_EOS = Node("9001", "Disease", "eosinophilic asthma", "MESH:M0001", 50)
_VCFS = Node("154967", "Disease", "velocardiofacial syndrome", "MESH:M0474269", 9582)
_OTHER = Node("4040", "Disease", "something else", "MESH:D888", 3)
_NAMES: dict[str, list[Node]] = {
    "pembrolizumab": [_named(_PEMBRO), _named(_PEMBRO_CHEM)],
    "keytruda": [_named(_KEYTRUDA), _named(_PEMBRO_CHEM, "alias")],
    "adenosine": [_named(_ADENOSINE), _named(_ADENOSINES, "alias")],
    "afatinib dimaleate": [_named(_AFATINIB, "alias")],
    "adalimumab-aacf": [_named(_ADALIMUMAB, "alias")],
    "tracer": [_named(_TRACER)],
    "motivational interviewing": [_named(_TALK)],
    "cardiac surgery": [_named(_SURGERY_TP), _named(_SURGERY_CHEM, "alias")],
    "tetracycline": [_named(_TETRACYCLINES, "alias"), _named(_TETRA_CHEM)],
    "two drugs": [_named(_PEMBRO), _named(_KEYTRUDA)],
    "non-small cell lung cancer": [_NSCLC],
    "eosinophilic asthma": [_EOS],
    "22q11.2 deletion syndrome": [_VCFS],
    "conflicting": [_OTHER],
}
_IDS: dict[str, list[Node]] = {
    "UNII:DPT0O3T46P": [_PEMBRO], "MESH:C582435": [_PEMBRO_CHEM],
    "UNII:41UD74L59M": [_AFATINIB], "UNII:FYS6T7F842": [_ADALIMUMAB],
    "MESH:C999": [Node("77", _CHEM, "only chemical", "MESH:C999", 1)],
    "MESH:C888": [_TP_BY_MESH], "MESH:D006348": [_SURGERY_CHEM], "MESH:D013752": [_TETRA_CHEM],
}
_DISEASES: dict[str, list[str]] = {"MESH:D002289": ["55633"], "MESH:D2": ["1", "2"],
                                   "MESH:D004062": ["57660"]}

_MESH = MeshIndex(
    uniis={"MESH:C582435": frozenset({"DPT0O3T46P"}), "MESH:C999": frozenset({"NOTPHERE00"})},
    by_term={"pembrolizumab": frozenset({"MESH:C582435"}), "nsclc": frozenset({"MESH:D002289"}),
             "mismatch": frozenset({"MESH:C999"}),
             "ambiguous term": frozenset({"MESH:D1", "MESH:D2"}),
             "rare thing": frozenset({"MESH:D2"}),
             "22q11.2 deletion syndrome": frozenset({"MESH:D004062"}),
             "conflicting": frozenset({"MESH:D777"}),
             "cardiac surgery": frozenset({"MESH:D006348"}),
             "tetracycline": frozenset({"MESH:D013752"})},
    names={"MESH:C582435": "pembrolizumab", "MESH:C888": "tp by mesh"}, headings={})


def _row(unii: str, mesh: str, role: str, moiety: str, pubchem: str = "") -> Substance:
    return Substance(unii, unii, mesh, mesh.lower(), role, moiety, pubchem, "")


_DICTIONARY = Dictionary(
    by_unii={
        "DPT0O3T46P": _row("DPT0O3T46P", "MESH:C582435", "preferred", "DPT0O3T46P",
                           "PubChem:CID1"),
        "NOTPHERE00": _row("NOTPHERE00", "MESH:C999", "preferred", "NOTPHERE00"),
        "41UD74L59M": _row("41UD74L59M", "MESH:D000077716", "preferred", "41UD74L59M"),
        "AFATSALT00": _row("AFATSALT00", "MESH:D000077716", "related", "41UD74L59M"),
        "FYS6T7F842": _row("FYS6T7F842", "MESH:D000068879", "preferred", "FYS6T7F842"),
        "BIOSIM0000": _row("BIOSIM0000", "MESH:D000068879", "related", "FYS6T7F842"),
    },
    moiety_of={"MESH:C582435": "DPT0O3T46P", "MESH:C999": "NOTPHERE00",
               "MESH:D000077716": "41UD74L59M", "MESH:D000068879": "FYS6T7F842"})
_UNII_NAMES = {"keytruda": frozenset({"DPT0O3T46P"}), "mk-3475": frozenset({"DPT0O3T46P"}),
               "mismatch": frozenset({"DPT0O3T46P"}),
               "afatinib dimaleate": frozenset({"AFATSALT00"}),
               "afatinib hcl": frozenset({"AFATHCL000"}),
               "adalimumab-aacf": frozenset({"BIOSIM0000"}),
               "tracer": frozenset({"TRACER0000"}),
               "shared name": frozenset({"U1", "U2"})}


def test_an_nlm_drug_id_reaches_one_entity_through_the_resolver() -> None:
    rows = ri.drug_mesh(["MESH:C582435", "MESH:C999", "MESH:C888", "MESH:D000077716",
                         "NOT-MESH"], _MESH, _DICTIONARY, FakeGraph())
    assert set(rows) == {"MESH:C582435", "MESH:C999", "MESH:C888", "MESH:D000077716"}
    pembro = rows["MESH:C582435"]
    # The TherapyPreventions entity via the moiety UNII, not the Chemical holding the MeSH id.
    assert (pembro.biokdeid, pembro.unii, pembro.mesh_id, pembro.pubchem) == (
        "2700000295", "DPT0O3T46P", "MESH:C582435", "PubChem:CID1")
    # A TherapyPreventions entity keyed by the MeSH id itself.
    assert rows["MESH:C888"].biokdeid == "88"
    # No drug entity at all: the Chemical entity holding the exact id.
    assert rows["MESH:C999"].biokdeid == "77"
    assert rows["MESH:D000077716"].biokdeid == "2700001344"


def test_an_nlm_disease_id_keeps_its_mesh_and_gains_one_entity_only() -> None:
    rows = ri.disease_mesh(["MESH:D002289", "MESH:D2", "MESH:D404"], FakeGraph())
    assert rows["MESH:D002289"].biokdeid == "55633"
    assert rows["MESH:D2"].biokdeid == ""        # two entities: none chosen
    assert rows["MESH:D404"] == ri.Resolved("MESH:D404", "", "", "MESH:D404", "", "disease",
                                            ri.BASIS_NLM)


def _drugs(strings: list[str]) -> dict[str, ri.Resolved]:
    return ri.drug_strings(strings, _MESH, _UNII_NAMES, _DICTIONARY, FakeGraph())


def test_an_id_step_answers_before_any_name() -> None:
    rows = _drugs(["Pembrolizumab", "Keytruda", "MK-3475", "Two Drugs", "Placebo", "PB",
                   "Shared Name", "Nothing Known"])
    assert set(rows) == {"Pembrolizumab", "Keytruda", "MK-3475"}
    pembro = rows["Pembrolizumab"]
    assert (pembro.biokdeid, pembro.unii, pembro.mesh_id, pembro.basis) == (
        "2700000295", "DPT0O3T46P", "MESH:C582435", ri.BASIS_MESH_TERM)
    # The brand reaches pembrolizumab through the UNII list; iKraph2's id-less
    # brand entity is never reached, since an id step answered first.
    keytruda = rows["Keytruda"]
    assert (keytruda.biokdeid, keytruda.basis) == ("2700000295", ri.BASIS_UNII_NAME)
    assert ri.keys_of(keytruda) >= {"IK2:2700000295", "UNII:DPT0O3T46P", "MESH:C582435"}
    assert rows["MK-3475"].basis == ri.BASIS_UNII_NAME


def test_a_salt_keeps_its_own_ids_and_a_biosimilar_is_never_folded() -> None:
    rows = _drugs(["Afatinib Dimaleate", "Afatinib HCl", "Adalimumab-Aacf"])
    # Its own UNII only — no MeSH (a related form of afatinib's record) — and
    # the moiety's entity only because iKraph2 lists the salt's name as its alias.
    afatinib = rows["Afatinib Dimaleate"]
    assert (afatinib.biokdeid, afatinib.unii, afatinib.mesh_id, afatinib.basis) == (
        "2700001344", "AFATSALT00", "", "ikraph2_name;unii_name")
    # No alias in iKraph2: no entity. Nothing is folded.
    assert (rows["Afatinib HCl"].biokdeid, rows["Afatinib HCl"].unii) == ("", "AFATHCL000")
    biosimilar = rows["Adalimumab-Aacf"]
    # The reference product's MeSH id stays as a key; neither its id nor the
    # alias iKraph2 gives the biosimilar's name reaches its entity.
    assert (biosimilar.biokdeid, biosimilar.unii, biosimilar.mesh_id) == (
        "", "BIOSIM0000", "MESH:D000068879")


def test_names_come_after_tp_ids_and_before_chemical_ids() -> None:
    rows = _drugs(["Adenosine", "Tracer", "Motivational Interviewing", "Mismatch",
                   "Cardiac Surgery", "Tetracycline"])
    # A ChEBI class holds "adenosine" only as an alias, and is not UNII-keyed.
    assert rows["Adenosine"].biokdeid == "17418"
    tracer = rows["Tracer"]
    assert (tracer.biokdeid, tracer.unii, tracer.basis) == (
        "7705595", "TRACER0000", "ikraph2_name;unii_name")
    assert rows["Motivational Interviewing"].biokdeid == "2700001329"
    # The UNII list and a MeSH entry term name different records: the UNII wins.
    mismatch = rows["Mismatch"]
    assert (mismatch.mesh_id, mismatch.basis) == ("MESH:C582435", ri.BASIS_UNII_NAME)
    # A TherapyPreventions name comes before a Chemical found by id...
    surgery = rows["Cardiac Surgery"]
    assert (surgery.biokdeid, surgery.mesh_id) == ("2700009999", "MESH:D006348")
    # ...but a class holding the name as an alias does not.
    assert rows["Tetracycline"].biokdeid == "86762"


def test_a_dose_is_stripped_only_as_a_last_resort_and_under_nlm() -> None:
    rows = ri.drug_strings(["Pembrolizumab 200 mg IV", "Pembrolizumab 2 mg/kg"], _MESH,
                           _UNII_NAMES, _DICTIONARY, FakeGraph(),
                           nlm_of={"Pembrolizumab 2 mg/kg": {"MESH:D000001"}})
    assert rows["Pembrolizumab 200 mg IV"].basis.startswith(ri.DOSE_STRIPPED)
    # NLM coded those trials otherwise: the MeSH id goes, the entity stays.
    contradicted = rows["Pembrolizumab 2 mg/kg"]
    assert (contradicted.mesh_id, contradicted.biokdeid) == ("", "2700000295")


def test_conditions_take_the_mesh_term_entity_first() -> None:
    rows = ri.condition_strings(["Non-Small Cell  Lung Cancer", "Eosinophilic Asthma", "NSCLC",
                                 "Ambiguous Term", "Rare Thing", "BD",
                                 "22q11.2 Deletion Syndrome", "Conflicting"],
                                _MESH, FakeGraph())
    assert rows["Non-Small Cell  Lung Cancer"].biokdeid == "55633"
    # Kept as stored: a concept id, not rolled up to its descriptor.
    assert rows["Eosinophilic Asthma"].mesh_id == "MESH:M0001"
    assert (rows["NSCLC"].mesh_id, rows["NSCLC"].biokdeid) == ("MESH:D002289", "55633")
    assert "Ambiguous Term" not in rows and "BD" not in rows
    assert (rows["Rare Thing"].mesh_id, rows["Rare Thing"].biokdeid) == ("MESH:D2", "")
    # The term's record has an entity: the name's other entity is not added.
    deletion = rows["22q11.2 Deletion Syndrome"]
    assert (deletion.biokdeid, deletion.mesh_id, deletion.basis) == (
        "57660", "MESH:D004062", ri.BASIS_MESH_TERM)
    # The name's entity holds another descriptor than the term's record: dropped.
    conflicting = rows["Conflicting"]
    assert (conflicting.biokdeid, conflicting.mesh_id) == ("", "MESH:D777")


def test_the_unii_list_indexes_every_name() -> None:
    assert ri.unii_names([{"UNII": "", "NAME": "x"},
                          {"UNII": "U1", "NAME": " Jinarc ", "DISPLAY_NAME": ""}]) == {
        "jinarc": frozenset({"U1"})}
