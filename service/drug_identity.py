"""A drug's exact ids and its one iKraph2 entity, the same way on both sides (D45).

S2 (an FDA ingredient) and S3 (a registry intervention, or an NLM MeSH id)
both resolve through this module, so one name reaches one entity whoever asks.
A name uses **only its own ids** — nothing is folded to another substance:

1. **Bundle** — the name's own UNII(s) (the FDA UNII list's for the name; for
   an FDA ingredient also the label extract's UNII for that salt), and from the
   substance dictionary (:mod:`source.substance_ids`) the MeSH id of a record
   whose **preferred** concept is that UNII, and that UNII's RxCUI and PubChem
   CID. A name that is exactly one MeSH record's entry term adds that record
   and its own UNII, unless the name's UNII belongs to another record.
2. **Entity** — the first of these steps that finds one:

   ========  ====================================================  ====================
   step      looks up                                              ``route``
   ========  ====================================================  ====================
   1         ``TherapyPreventions`` by UNII                        ``tp_unii``
   2         ``TherapyPreventions`` by the MeSH id                 ``tp_mesh``
   3         ``TherapyPreventions`` by RxCUI                       ``tp_rxcui``
   4         ``TherapyPreventions`` by exact name: its canonical   ``tp_name``
             name, else an alias of a UNII-keyed entity
   5         ``Chemical`` by the MeSH id                           ``chemical_mesh``
   6         ``Chemical`` by PubChem CID                           ``chemical_pubchem``
   7         ``Chemical`` by exact name, canonical first           ``chemical_name``
   ========  ====================================================  ====================

   The user's rule: drugs are ``TherapyPreventions`` by preference, ``Chemical``
   as the fallback. Each ``db_id`` belongs to one entity per type (measured
   2026-10-01), and a name step needs exactly one entity, so every step answers
   one entity or none. Step 4 is how a salt reaches its moiety's entity —
   iKraph2 itself lists ``abiraterone acetate`` as an alias of ``ABIRATERONE``
   (``UNII:G819A456D0``); the alias must sit on a UNII-keyed entity, so a ChEBI
   class ("adenosines") never answers for one of its members.

**A biosimilar is never folded** (D11): its MeSH id may be the reference
product's record, kept as a retrieval key (``stem``) but never used to find an
entity, and its name matches only an entity's canonical name, never an alias.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple, Protocol

from dao.ikraph2_entities import CHEMICAL, DRUG, Node
from source.moiety import Moiety, is_biosimilar
from source.substance_ids import ROLE_PREFERRED, Dictionary
from source.unii_mesh import MeshIndex, exact_key
from utility.logging import get_logger

_log = get_logger(__name__)

BASIS_NONE = "no_unii"
BASIS_NO_MESH = "unii_no_mesh"
BASIS_REGISTRY = "unii_registry"

ROUTE_TP_UNII = "tp_unii"
ROUTE_TP_MESH = "tp_mesh"
ROUTE_TP_RXCUI = "tp_rxcui"
ROUTE_TP_NAME = "tp_name"
ROUTE_CHEMICAL_MESH = "chemical_mesh"
ROUTE_CHEMICAL_PUBCHEM = "chemical_pubchem"
ROUTE_CHEMICAL_NAME = "chemical_name"
ROUTES: tuple[str, ...] = (ROUTE_TP_UNII, ROUTE_TP_MESH, ROUTE_TP_RXCUI, ROUTE_TP_NAME,
                           ROUTE_CHEMICAL_MESH, ROUTE_CHEMICAL_PUBCHEM, ROUTE_CHEMICAL_NAME)
#: Routes that found the entity by a name rather than an id.
NAME_ROUTES: frozenset[str] = frozenset({ROUTE_TP_NAME, ROUTE_CHEMICAL_NAME})

UNII = "UNII:"
#: iKraph2's role for a name that is the entity's own (vs ``alias``).
CANONICAL = "canonical"
#: Joins a list of ids in one TSV field.
SEPARATOR = ";"


class Graph(Protocol):
    """What resolution reads from iKraph2 (:class:`dao.ikraph2_entities.Ikraph2Entities`)."""

    def named(self, keys: Iterable[str], types: Iterable[str]) -> dict[str, list[Node]]: ...

    def by_db_id(self, ids: Iterable[str]) -> dict[str, list[Node]]: ...


class Bundle(NamedTuple):
    """Every exact id of one name's own substance."""

    uniis: tuple[str, ...]
    mesh_id: str
    mesh_name: str
    biosimilar: bool = False


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(i for i in items if i))


def from_uniis(own: Sequence[str], dictionary: Dictionary, biosimilar: bool = False) -> Bundle:
    """The bundle of a name known by its own UNII(s): the MeSH record whose
    preferred concept is one of them; for a biosimilar, any record listing it
    (the reference product's, a retrieval key only).

    >>> from source.substance_ids import Dictionary, Substance
    >>> d = Dictionary({"SALT000000": Substance("SALT000000", "X ACETATE", "MESH:D1", "X",
    ...                                         "related", "MOIETY0000", "", ""),
    ...                 "MOIETY0000": Substance("MOIETY0000", "X", "MESH:D1", "X",
    ...                                         "preferred", "MOIETY0000", "", "")},
    ...                {"MESH:D1": "MOIETY0000"})
    >>> from_uniis(["SALT000000"], d)
    Bundle(uniis=('SALT000000',), mesh_id='', mesh_name='', biosimilar=False)
    >>> from_uniis(["MOIETY0000"], d).mesh_id, from_uniis(["SALT000000"], d, True).mesh_id
    ('MESH:D1', 'MESH:D1')
    """
    uniis = _unique(own)
    for unii in uniis:
        row = dictionary.by_unii.get(unii)
        if row and row.mesh_id and (biosimilar or row.mesh_role == ROLE_PREFERRED):
            return Bundle(uniis, row.mesh_id, row.mesh_name, biosimilar)
    return Bundle(uniis, "", "", biosimilar)


def from_mesh(mesh: str, dictionary: Dictionary, names: Mapping[str, str]) -> Bundle:
    """The bundle of a MeSH id: the record and its own (preferred-concept) UNII."""
    return Bundle(_unique([dictionary.moiety_of.get(mesh, "")]), mesh, names.get(mesh, ""))


def id_bundle(key: str, own: Sequence[str], index: MeshIndex, dictionary: Dictionary,
              biosimilar: bool = False) -> tuple[Bundle, bool]:
    """A name's bundle from its own UNIIs and, where the name is exactly one MeSH
    record's entry term, that record — unless the name's UNIIs have a record of
    their own and it is another one. Returns the bundle and whether the term
    was used. A biosimilar takes the term's record as a key only, never its UNII.

    >>> from source.substance_ids import Dictionary, Substance
    >>> d = Dictionary({"U1": Substance("U1", "", "MESH:D1", "", "preferred", "U1", "", ""),
    ...                 "U2": Substance("U2", "", "MESH:D2", "", "preferred", "U2", "", "")},
    ...                {"MESH:D1": "U1", "MESH:D2": "U2"})
    >>> idx = MeshIndex({}, {"one": frozenset({"MESH:D1"}), "two": frozenset({"MESH:D2"})},
    ...                 {}, {})
    >>> id_bundle("one", [], idx, d)
    (Bundle(uniis=('U1',), mesh_id='MESH:D1', mesh_name='', biosimilar=False), True)
    >>> id_bundle("two", ["U1"], idx, d)
    (Bundle(uniis=('U1',), mesh_id='MESH:D1', mesh_name='', biosimilar=False), False)
    """
    by_own = from_uniis(own, dictionary, biosimilar)
    records = index.by_term.get(key, frozenset())
    if len(records) != 1:
        return by_own, False
    mesh = next(iter(records))
    if by_own.mesh_id and by_own.mesh_id != mesh:
        return by_own, False
    term_uniis = () if biosimilar else from_mesh(mesh, dictionary, index.names).uniis
    return Bundle(_unique([*by_own.uniis, *term_uniis]), by_own.mesh_id or mesh,
                  by_own.mesh_name or index.names.get(mesh, ""), biosimilar), True


def rxcuis(bundle: Bundle, dictionary: Dictionary) -> list[str]:
    """The bundle's RxCUIs."""
    return [r.rxcui for u in bundle.uniis
            if (r := dictionary.by_unii.get(u)) is not None and r.rxcui]


def pubchems(bundle: Bundle, dictionary: Dictionary) -> list[str]:
    """The bundle's PubChem CIDs."""
    return [r.pubchem for u in bundle.uniis
            if (r := dictionary.by_unii.get(u)) is not None and r.pubchem]


def keys(bundle: Bundle, dictionary: Dictionary) -> list[str]:
    """Every ``db_id`` the id steps may look up."""
    mesh = [bundle.mesh_id] if bundle.mesh_id and not bundle.biosimilar else []
    return list(_unique([*(UNII + u for u in bundle.uniis), *mesh,
                         *rxcuis(bundle, dictionary), *pubchems(bundle, dictionary)]))


def _one(nodes: Iterable[Node]) -> Node | None:
    found = {n.biokdeid: n for n in nodes}
    return next(iter(found.values())) if len(found) == 1 else None


def tp_by_name(nodes: Iterable[Node], biosimilar: bool = False) -> Node | None:
    """The one TherapyPreventions entity a name names: by its canonical name,
    else as an alias of a UNII-keyed entity (never for a biosimilar).

    >>> n = lambda b, db, r: Node(b, DRUG, "", db, 0, r)
    >>> tp_by_name([n("1", "UNII:A", "alias"), n("2", "CHEBI:1", "alias")]).biokdeid
    '1'
    >>> tp_by_name([n("1", "UNII:A", "alias")], biosimilar=True) is None
    True
    >>> tp_by_name([n("1", "", "canonical"), n("2", "UNII:B", "canonical")]) is None
    True
    """
    drugs = [n for n in nodes if n.type == DRUG]
    canonical = [n for n in drugs if n.role == CANONICAL]
    if canonical or biosimilar:
        return _one(canonical)
    return _one(n for n in drugs if n.db_id.startswith(UNII))


def chemical_by_name(nodes: Iterable[Node]) -> Node | None:
    """The one Chemical entity a name names, canonical names first.

    >>> n = lambda b, r: Node(b, CHEMICAL, "", "", 0, r)
    >>> chemical_by_name([n("1", "alias"), n("2", "canonical")]).biokdeid
    '2'
    """
    chemicals = [n for n in nodes if n.type == CHEMICAL]
    return _one([n for n in chemicals if n.role == CANONICAL] or chemicals)


def pick(bundle: Bundle, dictionary: Dictionary, found: Mapping[str, list[Node]],
         named: Sequence[Node] = ()) -> tuple[Node | None, str]:
    """The name's one entity and the step that found it (see the module table)."""
    mesh = [bundle.mesh_id] if bundle.mesh_id and not bundle.biosimilar else []

    def by_id(kind: str, ids: Iterable[str]) -> Node | None:
        return next((n for i in ids for n in found.get(i, []) if n.type == kind), None)

    steps = (
        (ROUTE_TP_UNII, lambda: by_id(DRUG, (UNII + u for u in bundle.uniis))),
        (ROUTE_TP_MESH, lambda: by_id(DRUG, mesh)),
        (ROUTE_TP_RXCUI, lambda: by_id(DRUG, rxcuis(bundle, dictionary))),
        (ROUTE_TP_NAME, lambda: tp_by_name(named, bundle.biosimilar)),
        (ROUTE_CHEMICAL_MESH, lambda: by_id(CHEMICAL, mesh)),
        (ROUTE_CHEMICAL_PUBCHEM, lambda: by_id(CHEMICAL, pubchems(bundle, dictionary))),
        (ROUTE_CHEMICAL_NAME, lambda: chemical_by_name(named)),
    )
    for route, step in steps:
        hit = step()
        if hit is not None:
            return hit, route
    return None, ""


class DrugIdentity(NamedTuple):
    """One FDA ingredient's exact ids (``drug_identity.tsv``)."""

    source_name: str
    uniis: str            # ";"-joined, the ingredient's own
    mesh_id: str          # "" where no own record
    mesh_name: str
    nodes: str            # the one iKraph2 biokdeid, or ""
    node_type: str        # TherapyPreventions | Chemical | ""
    basis: str
    route: str = ""       # the entity step that answered (ROUTES), or ""


class IdentityReport(NamedTuple):
    names: int
    with_unii: int
    with_mesh: int
    with_drug_node: int
    with_chemical_node: int
    without_node: int
    routes: tuple[tuple[str, int], ...] = ()


def uniis_of(names: Iterable[str], unii_of_name: dict[str, str],
             moieties: dict[str, Moiety]) -> dict[str, tuple[str, ...]]:
    """Ingredient name → its own UNIIs: the FDA UNII list's, and the label
    extract's for the salt. Never its moiety's (D45).

    >>> m = {"ESOMEPRAZOLE MAGNESIUM": Moiety("ESOMEPRAZOLE MAGNESIUM", "ESOMEPRAZOLE",
    ...      "R6DXU4WAY9", "N3PA6559FT")}
    >>> uniis_of(["Esomeprazole Magnesium", "NOTHING"], {}, m)
    {'Esomeprazole Magnesium': ('R6DXU4WAY9',)}
    """
    out: dict[str, tuple[str, ...]] = {}
    for name in dict.fromkeys(names):
        key = name.strip().upper()
        moiety = moieties.get(key) or Moiety("", "", "", "")
        own = _unique([unii_of_name.get(key, ""), moiety.salt_unii])
        if own:
            out[name] = own
    return out


def build(names: Iterable[str], uniis: Mapping[str, Sequence[str]], dictionary: Dictionary,
          index: MeshIndex, graph: Graph) -> tuple[dict[str, DrugIdentity], IdentityReport]:
    """Every FDA ingredient's identity, by the same steps as a trial string."""
    names = list(dict.fromkeys(names))
    bundles = {name: id_bundle(exact_key(name), uniis.get(name, ()), index, dictionary,
                               is_biosimilar(name))[0] for name in names}
    found = graph.by_db_id(sorted({k for b in bundles.values() for k in keys(b, dictionary)}))
    named = graph.named({exact_key(n) for n in names}, (DRUG, CHEMICAL))
    out: dict[str, DrugIdentity] = {}
    for name in names:
        bundle = bundles[name]
        node, route = pick(bundle, dictionary, found, named.get(exact_key(name), []))
        out[name] = DrugIdentity(
            source_name=name, uniis=SEPARATOR.join(bundle.uniis), mesh_id=bundle.mesh_id,
            mesh_name=bundle.mesh_name, nodes=node.biokdeid if node else "",
            node_type=node.type if node else "",
            basis=(BASIS_REGISTRY if bundle.mesh_id else BASIS_NO_MESH if bundle.uniis
                   else BASIS_NONE),
            route=route)
    rows = out.values()
    routes = Counter(r.route for r in rows if r.route)
    report = IdentityReport(
        names=len(out), with_unii=sum(1 for r in rows if r.uniis),
        with_mesh=sum(1 for r in rows if r.mesh_id),
        with_drug_node=sum(1 for r in rows if r.node_type == DRUG),
        with_chemical_node=sum(1 for r in rows if r.node_type == CHEMICAL),
        without_node=sum(1 for r in rows if not r.nodes),
        routes=tuple((route, routes[route]) for route in ROUTES if routes[route]))
    _log.info("drug identity: %d names, %d MeSH, %d with an iKraph2 node",
              report.names, report.with_mesh, report.names - report.without_node)
    return out, report


def split(ids: str) -> list[str]:
    """A ";"-joined id column as a list.

    >>> split("2700000295;6308113"), split("")
    (['2700000295', '6308113'], [])
    """
    return [i for i in ids.split(SEPARATOR) if i]


def render(report: IdentityReport) -> str:
    """The pass report, as ``--mode normalize`` prints it."""
    return "\n".join([
        "FDA ingredient identity (own ids, then exact name, D45)",
        f"  ingredient names                       {report.names:>9,}",
        f"    with a UNII                          {report.with_unii:>9,}",
        f"    with a MeSH id                       {report.with_mesh:>9,}",
        f"    iKraph2 node: TherapyPreventions     {report.with_drug_node:>9,}",
        f"    iKraph2 node: Chemical (fallback)    {report.with_chemical_node:>9,}",
        f"    no iKraph2 node                      {report.without_node:>9,}",
        "  entity found by",
        *(f"    {route:<36} {count:>9,}" for route, count in report.routes),
    ])


__all__ = ["BASIS_NONE", "BASIS_NO_MESH", "BASIS_REGISTRY", "Bundle", "CANONICAL", "DrugIdentity",
           "Graph", "IdentityReport", "NAME_ROUTES", "ROUTES", "ROUTE_CHEMICAL_MESH",
           "ROUTE_CHEMICAL_NAME", "ROUTE_CHEMICAL_PUBCHEM", "ROUTE_TP_MESH", "ROUTE_TP_NAME",
           "ROUTE_TP_RXCUI", "ROUTE_TP_UNII", "SEPARATOR", "UNII", "build", "chemical_by_name",
           "from_mesh", "from_uniis", "id_bundle", "keys", "pick", "pubchems", "render",
           "rxcuis", "split", "tp_by_name", "uniis_of"]
