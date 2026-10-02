"""Exact ids for registry interventions and conditions, iKraph2 first (D44, D45).

The trial side is normalised the same way as the FDA side, and **only by
exact matches** — of an id, or of a whole name (case and spacing folded,
nothing else). For each trial string or NLM-coded MeSH id this finds:

* **drugs** — the one iKraph2 drug entity (``TherapyPreventions``, else
  ``Chemical``) through the shared resolver of :mod:`service.drug_identity`,
  so a substance lands on the entity the FDA side gives it (D45);
* **diseases** — the one iKraph2 ``Disease`` entity; its MeSH id as stored.

Nothing is rolled up here: a concept id stays a concept id, and S8's
indication filter places it in the MeSH tree when it compares (D44). What no
exact match reaches stays as it came — an NLM MeSH id is never dropped for
lacking an iKraph2 entity — or, for a string, uncoded.

**Own ids, then exact names (D45).** A string is read first as an id — a name
in the FDA UNII list (brand and code names included: ``Keytruda``, ``MK-3475``)
naming exactly one UNII, or one MeSH record's entry term — and then by its
exact name in iKraph2, through the same steps as an FDA ingredient
(:func:`service.drug_identity.pick`): TherapyPreventions by id, by name, then
Chemical by id, by name. Nothing is folded to another substance; a salt
reaches its moiety's entity only where iKraph2 itself lists the salt's name as
an alias of the moiety's UNII-keyed entity.

========================  ============================================  =================
route                     how                                           basis
========================  ============================================  =================
NLM MeSH id (drug)        the record's own UNII and MeSH id →           ``nlm_mesh``
                          the resolver's id steps
NLM MeSH id (disease)     the one Disease entity holding the id         ``nlm_mesh``
name in the FDA UNII      the one UNII named so → its bundle →          ``unii_name``
list (drugs)              the resolver's entity steps
name as a MeSH entry      the one MeSH record listing it → its          ``mesh_term``
term                      bundle (drugs) or its Disease entity
name in iKraph2           drugs: the resolver's name steps (TP          ``ikraph2_name``
                          after TP ids, Chemical after Chemical ids);
                          diseases: only where the MeSH term's record
                          has no entity
========================  ============================================  =================
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple, Protocol

from dao.ikraph2_entities import CHEMICAL, DISEASE, DRUG, Node
from service import drug_identity
from service.drug_identity import CANONICAL, Bundle
from service.normalize import (
    DESIGN_WORDS, MIN_STRIPPED_LENGTH, MIN_SYNONYM_LENGTH, dose_stripped, nlm_agrees,
)
from source.moiety import is_biosimilar
from source.substance_ids import Dictionary
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


def name_pick(nodes: list[Node], types: tuple[str, ...]) -> Node | None:
    """The one entity a name names: among the entities for which it is the
    canonical name if there are any, else among those holding it as an alias;
    then :func:`one_type`. Used for diseases; drugs name-match through
    :func:`service.drug_identity.pick`.

    >>> n = lambda b, t, r: Node(b, t, "", "", 0, r)
    >>> name_pick([n("1", DISEASE, "canonical"), n("2", DISEASE, "alias")], (DISEASE,)).biokdeid
    '1'
    >>> name_pick([n("1", DISEASE, "alias"), n("2", DISEASE, "alias")], (DISEASE,)) is None
    True
    """
    canonical = [n for n in nodes if n.role == CANONICAL]
    hit = one_type(canonical or nodes, types)
    return hit[0] if hit else None


def drug_mesh(mesh_ids: Iterable[str], index: MeshIndex, dictionary: Dictionary,
              graph: Graph) -> dict[str, Resolved]:
    """NLM intervention MeSH id → its record's own UNII and its one iKraph2 drug entity."""
    wanted = sorted({m for m in mesh_ids if m.startswith("MESH:")})
    bundles = {m: drug_identity.from_mesh(m, dictionary, index.names) for m in wanted}
    found = graph.by_db_id(sorted({k for b in bundles.values()
                                   for k in drug_identity.keys(b, dictionary)}))
    out: dict[str, Resolved] = {}
    for mesh, bundle in bundles.items():
        node, _ = drug_identity.pick(bundle, dictionary, found)
        out[mesh] = Resolved(mesh, node.biokdeid if node else "", _join(bundle.uniis), mesh,
                             _join(drug_identity.pubchems(bundle, dictionary)), "drug",
                             BASIS_NLM)
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
                 unii_by_name: Mapping[str, frozenset[str]], dictionary: Dictionary,
                 graph: Graph, nlm_of: Mapping[str, set[str]] | None = None
                 ) -> dict[str, Resolved]:
    """Trial intervention string → its own exact ids and its one iKraph2 entity,
    by the steps an FDA ingredient takes (:func:`service.drug_identity.pick`).

    A string no route reaches is retried without its dose, route and form
    (``dose_stripped``) — still an exact lookup of what remains, under the
    2026-09-30 guards: at least five characters, and a MeSH answer NLM's
    coding of the same trials does not contradict.
    """
    distinct = [s for s in dict.fromkeys(strings) if s and _usable(s)]
    stripped = {s: dose_stripped(s) for s in distinct}
    asked = {exact_key(s) for s in distinct} | {exact_key(t) for t in stripped.values() if t}
    named = graph.named(asked, DRUG_TYPES)
    lookups: dict[str, tuple[Bundle, tuple[str, ...]]] = {}
    for key in asked:
        uniis = unii_by_name.get(key, frozenset())
        own = [next(iter(uniis))] if len(uniis) == 1 else []
        bundle, by_term = drug_identity.id_bundle(key, own, index, dictionary,
                                                  is_biosimilar(key))
        lookups[key] = (bundle, (BASIS_UNII_NAME,) * bool(own) + (BASIS_MESH_TERM,) * by_term)
    found = graph.by_db_id(sorted({i for bundle, _ in lookups.values()
                                   for i in drug_identity.keys(bundle, dictionary)}))

    def resolve(key: str) -> Resolved | None:
        bundle, id_bases = lookups[key]
        node, route = drug_identity.pick(bundle, dictionary, found, named.get(key, []))
        bases = [BASIS_IKRAPH2] * (route in drug_identity.NAME_ROUTES) + list(id_bases)
        if not bases:
            return None
        return Resolved("", node.biokdeid if node else "", _join(bundle.uniis), bundle.mesh_id,
                        _join(drug_identity.pubchems(bundle, dictionary)), "drug", _join(bases))

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
    """Trial condition string → its one iKraph2 Disease entity and MeSH id, exactly.

    The MeSH entry term comes first: where its record has a Disease entity, that
    is the entity. An iKraph2 name match is used only where it does not, and
    never when the entity it names holds a different MeSH descriptor or
    supplementary id than the term's record (D45).
    """
    distinct = [s for s in dict.fromkeys(strings) if s and _usable(s)]
    asked = {exact_key(s) for s in distinct}
    named = graph.named(asked, (DISEASE,))
    term_meshes = {k: next(iter(index.by_term[k])) for k in asked
                   if len(index.by_term.get(k, ())) == 1}
    entity_of = graph.diseases_by_db_id(term_meshes.values())
    out: dict[str, Resolved] = {}
    for text in distinct:
        key = exact_key(text)
        mesh = term_meshes.get(key, "")
        by_term = entity_of.get(mesh, []) if mesh else []
        by_name = name_pick(named.get(key, []), (DISEASE,))
        name_mesh = _ids_of(by_name)[1] if by_name else ""
        if len(by_term) == 1:
            node = by_term[0]
            name_used = by_name is not None and by_name.biokdeid == node
        else:
            node = ""
            name_used = by_name is not None and not (
                mesh and name_mesh[:6] in ("MESH:D", "MESH:C") and name_mesh != mesh)
        if name_used and by_name and not node:
            node = by_name.biokdeid
        bases = ([BASIS_IKRAPH2] if name_used else []) + ([BASIS_MESH_TERM] if mesh else [])
        if bases:
            out[text] = Resolved(text, node, "", mesh or (name_mesh if name_used else ""), "",
                                 "disease", _join(bases))
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
           "DRUG_TYPES", "Graph", "IK2", "Resolved", "SEPARATOR", "UNII",
           "condition_strings", "disease_mesh", "drug_mesh", "drug_strings", "keys_of",
           "name_pick", "one_type", "unii_names"]
