"""Exact ids for registry interventions and conditions, iKraph2 first (D44).

The trial side is normalised the same way as the FDA side, and **only by
exact matches** — of an id, or of a whole name (case and spacing folded,
nothing else). For each trial string or NLM-coded MeSH id this finds:

* **drugs** — the iKraph2 drug entity (``TherapyPreventions``, else
  ``Chemical``) and its UNII; the exact MeSH or PubChem id beside them;
* **diseases** — the iKraph2 ``Disease`` entity; its MeSH id as stored.

Nothing is rolled up here: a concept id stays a concept id, and S8's
indication filter places it in the MeSH tree when it compares (D44). What no
exact match reaches stays as it came — an NLM MeSH id is never dropped for
lacking an iKraph2 entity — or, for a string, uncoded.

Routes, each exact and unique (a name or id reaching two entities of the
preferred type says nothing and is dropped):

========================  ============================================  =================
route                     how                                           basis
========================  ============================================  =================
NLM MeSH id (drug)        record's UNIIs (MeSH RegistryNumber) → the    ``nlm_mesh``
                          TherapyPreventions entities with that UNII;
                          none → the Chemical entity holding the id
NLM MeSH id (disease)     the one Disease entity holding the id         ``nlm_mesh``
name in iKraph2           the one entity of the type named so           ``ikraph2_name``
name in the FDA UNII      the one UNII named so (brand and code names   ``unii_name``
list (drugs)              included) → its TherapyPreventions entities
name as a MeSH entry      the one MeSH record listing it                ``mesh_term``
term
========================  ============================================  =================

``Keytruda`` reaches pembrolizumab through the UNII list (iKraph2 keeps the
brand as a separate entity without an id); every route that answers adds its
keys, so a trial is joined by whichever of them the FDA side shares.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple, Protocol

from dao.ikraph2_entities import CHEMICAL, DISEASE, DRUG, Node
from service.normalize import (
    DESIGN_WORDS, MIN_STRIPPED_LENGTH, MIN_SYNONYM_LENGTH, dose_stripped, nlm_agrees,
)
from source.unii_mesh import MeshIndex, exact_key
from utility.logging import get_logger

_log = get_logger(__name__)

#: Key prefix of an iKraph2 entity in S8's join (``IK2:2700000295``).
IK2 = "IK2:"
UNII = "UNII:"
SEPARATOR = ";"

BASIS_NLM = "nlm_mesh"
BASIS_IKRAPH2 = "ikraph2_name"
BASIS_UNII_NAME = "unii_name"
BASIS_MESH_TERM = "mesh_term"
#: Prefix of a basis reached only after the dose was stripped (2026-09-30).
DOSE_STRIPPED = "dose_stripped:"

DRUG_TYPES: tuple[str, ...] = (DRUG, CHEMICAL)


class Graph(Protocol):
    """What this module reads from iKraph2 (:class:`dao.ikraph2_entities.Ikraph2Entities`)."""

    def named(self, keys: Iterable[str], types: Iterable[str]) -> dict[str, list[Node]]: ...

    def by_db_id(self, ids: Iterable[str]) -> dict[str, list[Node]]: ...

    def diseases_by_db_id(self, ids: Iterable[str]) -> dict[str, list[str]]: ...

    def internal_ids(self, ids: Iterable[str]) -> dict[str, str]: ...


class Resolved(NamedTuple):
    """One trial string or NLM id and the exact ids it reached."""

    source_name: str
    biokdeid: str          # ";"-joined iKraph2 entities
    unii: str              # ";"-joined
    mesh_id: str           # ";"-joined MeSH ids, as stored (never rolled up)
    pubchem: str
    entity_type: str       # drug | disease
    basis: str             # ";"-joined routes that answered


def keys_of(row: Resolved) -> frozenset[str]:
    """Every key S8 joins on.

    >>> sorted(keys_of(Resolved("Keytruda", "2700000295", "DPT0O3T46P", "MESH:C582435", "",
    ...                         "drug", "unii_name")))
    ['IK2:2700000295', 'MESH:C582435', 'UNII:DPT0O3T46P']
    """
    found = {IK2 + b for b in _split(row.biokdeid)} | {UNII + u for u in _split(row.unii)}
    found |= set(_split(row.mesh_id)) | set(_split(row.pubchem))
    return frozenset(found)


def _split(text: str) -> list[str]:
    return [part for part in text.split(SEPARATOR) if part]


def _join(parts: Iterable[str]) -> str:
    return SEPARATOR.join(dict.fromkeys(p for p in parts if p))


def one_type(nodes: list[Node], types: tuple[str, ...]) -> list[Node]:
    """The single entity of the most preferred type present, or none.

    >>> n = lambda b, t: Node(b, t, "", "", 0)
    >>> [x.biokdeid for x in one_type([n("1", CHEMICAL), n("2", DRUG)], DRUG_TYPES)]
    ['2']
    >>> one_type([n("1", DRUG), n("2", DRUG)], DRUG_TYPES)
    []
    >>> [x.biokdeid for x in one_type([n("3", CHEMICAL)], DRUG_TYPES)]
    ['3']
    """
    for kind in types:
        of_kind = {n.biokdeid: n for n in nodes if n.type == kind}
        if of_kind:
            return list(of_kind.values()) if len(of_kind) == 1 else []
    return []


def unii_mesh_records(index: MeshIndex) -> dict[str, frozenset[str]]:
    """UNII → the MeSH records listing it (inverse of ``index.uniis``).

    >>> idx = MeshIndex({"MESH:D1": frozenset({"U1", "U2"}), "MESH:C9": frozenset({"U2"})},
    ...                 {}, {}, {})
    >>> sorted(unii_mesh_records(idx)["U2"])
    ['MESH:C9', 'MESH:D1']
    """
    out: dict[str, set[str]] = {}
    for mesh, found in index.uniis.items():
        for unii in found:
            out.setdefault(unii, set()).add(mesh)
    return {u: frozenset(m) for u, m in out.items()}


def drug_mesh(mesh_ids: Iterable[str], index: MeshIndex, graph: Graph) -> dict[str, Resolved]:
    """NLM intervention MeSH id → its UNIIs and iKraph2 drug entities, exactly."""
    wanted = sorted({m for m in mesh_ids if m.startswith("MESH:")})
    uniis = {m: sorted(index.uniis.get(m, ())) for m in wanted}
    found = graph.by_db_id([UNII + u for found in uniis.values() for u in found] + wanted)
    out: dict[str, Resolved] = {}
    for mesh in wanted:
        nodes = [n for u in uniis[mesh] for n in found.get(UNII + u, []) if n.type == DRUG]
        if not nodes:
            nodes = [n for n in found.get(mesh, []) if n.type == CHEMICAL]
        out[mesh] = Resolved(mesh, _join(n.biokdeid for n in nodes), _join(uniis[mesh]), mesh,
                             "", "drug", BASIS_NLM)
    return out


def disease_mesh(mesh_ids: Iterable[str], graph: Graph) -> dict[str, Resolved]:
    """NLM condition MeSH id → the one iKraph2 Disease entity holding it exactly."""
    wanted = sorted({m for m in mesh_ids if m.startswith("MESH:")})
    found = graph.diseases_by_db_id(wanted)
    return {mesh: Resolved(mesh, _join(found.get(mesh, []) if len(found.get(mesh, [])) == 1
                                       else []), "", mesh, "", "disease", BASIS_NLM)
            for mesh in wanted}


def _usable(text: str) -> bool:
    key = exact_key(text)
    return len(key) >= MIN_SYNONYM_LENGTH and key not in DESIGN_WORDS


def _ids_of(node: Node) -> tuple[str, str, str]:
    """(UNII, MeSH, PubChem) from an entity's one external id."""
    db_id = node.db_id
    return (db_id.removeprefix(UNII) if db_id.startswith(UNII) else "",
            db_id if db_id.startswith("MESH:") else "",
            db_id if db_id.startswith("PubChem:") else "")


def drug_strings(strings: Iterable[str], index: MeshIndex,
                 unii_by_name: Mapping[str, frozenset[str]], graph: Graph,
                 nlm_of: Mapping[str, set[str]] | None = None) -> dict[str, Resolved]:
    """Trial intervention string → the exact ids every route reached.

    A string no route reaches is retried without its dose, route and form
    (``dose_stripped``) — still an exact lookup of what remains, under the
    2026-09-30 guards: at least five characters, and a MeSH answer NLM's
    coding of the same trials does not contradict.
    """
    distinct = [s for s in dict.fromkeys(strings) if s and _usable(s)]
    stripped = {s: dose_stripped(s) for s in distinct}
    asked = {exact_key(s) for s in distinct} | {exact_key(t) for t in stripped.values() if t}
    named = graph.named(asked, DRUG_TYPES)
    uniis_named = {k: unii_by_name[k] for k in asked if len(unii_by_name.get(k, ())) == 1}
    by_unii = graph.by_db_id(UNII + next(iter(u)) for u in uniis_named.values())
    records_of = unii_mesh_records(index)

    def resolve(key: str) -> Resolved | None:
        nodes, uniis, meshes, pubchems, bases = [], [], [], [], []
        hit = one_type(named.get(key, []), DRUG_TYPES)
        if hit:
            unii, mesh, pubchem = _ids_of(hit[0])
            nodes.append(hit[0].biokdeid)
            uniis.append(unii)
            meshes.append(mesh)
            pubchems.append(pubchem)
            bases.append(BASIS_IKRAPH2)
        if key in uniis_named:
            unii = next(iter(uniis_named[key]))
            uniis.append(unii)
            nodes += [n.biokdeid for n in by_unii.get(UNII + unii, []) if n.type == DRUG]
            if len(records_of.get(unii, ())) == 1:
                meshes.append(next(iter(records_of[unii])))
            bases.append(BASIS_UNII_NAME)
        records = index.by_term.get(key, frozenset())
        if len(records) == 1:
            meshes.append(next(iter(records)))
            bases.append(BASIS_MESH_TERM)
        if not bases:
            return None
        return Resolved("", _join(nodes), _join(uniis), _join(meshes), _join(pubchems),
                        "drug", _join(bases))

    out: dict[str, Resolved] = {}
    for text in distinct:
        row = resolve(exact_key(text))
        if row is None and len(stripped[text]) >= MIN_STRIPPED_LENGTH:
            row = resolve(exact_key(stripped[text]))
            if row is not None:
                meshes = [m for m in _split(row.mesh_id)
                          if nlm_agrees(m, (nlm_of or {}).get(text))]
                if row.mesh_id and not meshes and not (row.biokdeid or row.unii):
                    row = None
                else:
                    row = row._replace(mesh_id=_join(meshes),
                                       basis=_join(DOSE_STRIPPED + b for b in _split(row.basis)))
        if row is not None:
            out[text] = row._replace(source_name=text)
    _log.info("trial interventions: %d of %d strings reached an exact id", len(out), len(distinct))
    return out


def condition_strings(strings: Iterable[str], index: MeshIndex,
                      graph: Graph) -> dict[str, Resolved]:
    """Trial condition string → its iKraph2 Disease entity and MeSH id, exactly."""
    distinct = [s for s in dict.fromkeys(strings) if s and _usable(s)]
    asked = {exact_key(s) for s in distinct}
    named = graph.named(asked, (DISEASE,))
    term_meshes = {k: next(iter(index.by_term[k])) for k in asked
                   if len(index.by_term.get(k, ())) == 1}
    entity_of = graph.diseases_by_db_id(term_meshes.values())
    out: dict[str, Resolved] = {}
    for text in distinct:
        key = exact_key(text)
        nodes, meshes, bases = [], [], []
        hit = one_type(named.get(key, []), (DISEASE,))
        if hit:
            nodes.append(hit[0].biokdeid)
            meshes.append(_ids_of(hit[0])[1])
            bases.append(BASIS_IKRAPH2)
        if key in term_meshes:
            mesh = term_meshes[key]
            meshes.append(mesh)
            if len(entity_of.get(mesh, [])) == 1:
                nodes.append(entity_of[mesh][0])
            bases.append(BASIS_MESH_TERM)
        if bases:
            out[text] = Resolved(text, _join(nodes), "", _join(meshes), "", "disease",
                                 _join(bases))
    _log.info("trial conditions: %d of %d strings reached an exact id", len(out), len(distinct))
    return out


def unii_names(rows: Iterable[dict[str, str]]) -> dict[str, frozenset[str]]:
    """:func:`exact_key` of each name in the FDA UNII list → the UNIIs it names.

    >>> sorted(unii_names([{"UNII": "DPT0O3T46P", "NAME": "Keytruda",
    ...                     "DISPLAY_NAME": "PEMBROLIZUMAB"}]))
    ['keytruda', 'pembrolizumab']
    """
    out: dict[str, set[str]] = {}
    for row in rows:
        unii = (row.get("UNII") or "").strip()
        if not unii:
            continue
        for column in ("NAME", "DISPLAY_NAME"):
            key = exact_key(row.get(column) or "")
            if key:
                out.setdefault(key, set()).add(unii)
    return {k: frozenset(v) for k, v in out.items()}


__all__ = ["BASIS_IKRAPH2", "BASIS_MESH_TERM", "BASIS_NLM", "BASIS_UNII_NAME", "DOSE_STRIPPED",
           "DRUG_TYPES", "Graph", "IK2", "Resolved", "SEPARATOR", "UNII", "condition_strings",
           "disease_mesh", "drug_mesh", "drug_strings", "keys_of", "one_type",
           "unii_mesh_records", "unii_names"]
