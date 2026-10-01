"""Each FDA ingredient's MeSH id and iKraph2 node, by exact ids only (D42).

For one ingredient name (``PEMBROLIZUMAB``, ``ESOMEPRAZOLE MAGNESIUM``):

1. **UNIIs** — the ingredient's own UNII (the FDA UNII name list, a name two
   UNIIs share is dropped) and, where the moiety extract has one, its moiety's
   UNII. Ingredient first.
2. **MeSH** — the first of those UNIIs that ``unii_mesh.tsv`` maps
   (:mod:`source.unii_mesh`: NLM's ``RegistryNumber``, one record, shared name).
3. **iKraph2 node** — every ``TherapyPreventions`` entity whose ``db_id`` is
   exactly one of the UNIIs; only where there is none, every ``Chemical``
   entity whose ``db_id`` is the mapped MeSH id or the UNII's PubChem CID
   (the FDA UNII records, one-to-one only). The user's rule: drugs are
   ``TherapyPreventions`` by preference, ``Chemical`` as the fallback.

Nothing is matched by name, so nothing is guessed: an ingredient with no exact
id has no MeSH or no node, and says so in ``basis``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple

from dao.ikraph2_entities import CHEMICAL, DRUG, Node
from source.moiety import Moiety
from utility.logging import get_logger

_log = get_logger(__name__)

BASIS_NONE = "no_unii"
BASIS_NO_MESH = "unii_no_mesh"
BASIS_REGISTRY = "unii_registry"

#: Ids per graph-node lookup; a list of them joins with ";" in the TSVs.
SEPARATOR = ";"


class DrugIdentity(NamedTuple):
    """One ingredient's exact ids (``drug_identity.tsv``)."""

    source_name: str
    uniis: str            # ";"-joined, ingredient UNII first
    mesh_id: str          # "" where no UNII maps
    mesh_name: str
    nodes: str            # ";"-joined iKraph2 biokdeids
    node_type: str        # TherapyPreventions | Chemical | ""
    basis: str


class IdentityReport(NamedTuple):
    names: int
    with_unii: int
    with_mesh: int
    with_drug_node: int
    with_chemical_node: int
    without_node: int


def uniis_of(names: Iterable[str], unii_of_name: dict[str, str],
             moieties: dict[str, Moiety]) -> dict[str, tuple[str, ...]]:
    """Ingredient name → its UNIIs, the ingredient's own first.

    >>> m = {"ESOMEPRAZOLE MAGNESIUM": Moiety("ESOMEPRAZOLE MAGNESIUM", "ESOMEPRAZOLE",
    ...      "R6DXU4WAY9", "N3PA6559FT")}
    >>> uniis_of(["Esomeprazole Magnesium", "NOTHING"], {}, m)
    {'Esomeprazole Magnesium': ('R6DXU4WAY9', 'N3PA6559FT')}
    """
    out: dict[str, tuple[str, ...]] = {}
    for name in dict.fromkeys(names):
        key = name.strip().upper()
        found: dict[str, None] = {}
        for unii in (unii_of_name.get(key, ""),
                     (moieties.get(key) or Moiety("", "", "", "")).salt_unii,
                     (moieties.get(key) or Moiety("", "", "", "")).moiety_unii):
            if unii:
                found.setdefault(unii, None)
        if found:
            out[name] = tuple(found)
    return out


def build(names: Iterable[str], uniis: dict[str, tuple[str, ...]],
          mesh_of: dict[str, tuple[str, str]], pubchem_of: dict[str, str],
          lookup: Callable[[Iterable[str]], dict[str, list[Node]]]
          ) -> tuple[dict[str, DrugIdentity], IdentityReport]:
    """Every name's identity. ``mesh_of`` is UNII → (MeSH id, MeSH name).

    ``lookup`` is :meth:`dao.ikraph2_entities.Ikraph2Entities.by_db_id`.
    """
    names = list(dict.fromkeys(names))
    mesh: dict[str, tuple[str, str]] = {}
    for name in names:
        for unii in uniis.get(name, ()):
            if unii in mesh_of:
                mesh[name] = mesh_of[unii]
                break
    keys: set[str] = set()
    for name in names:
        keys.update(f"UNII:{u}" for u in uniis.get(name, ()))
        keys.update(pubchem_of[u] for u in uniis.get(name, ()) if u in pubchem_of)
        if name in mesh:
            keys.add(mesh[name][0])
    found = lookup(sorted(keys))
    out: dict[str, DrugIdentity] = {}
    for name in names:
        own = uniis.get(name, ())
        drug_nodes = [n for u in own for n in found.get(f"UNII:{u}", []) if n.type == DRUG]
        nodes, node_type = drug_nodes, DRUG if drug_nodes else ""
        if not drug_nodes:
            chemical_keys = [pubchem_of[u] for u in own if u in pubchem_of]
            if name in mesh:
                chemical_keys.insert(0, mesh[name][0])
            nodes = [n for k in chemical_keys for n in found.get(k, []) if n.type == CHEMICAL]
            node_type = CHEMICAL if nodes else ""
        ids = list(dict.fromkeys(n.biokdeid for n in nodes if n.biokdeid))
        mesh_id, mesh_name = mesh.get(name, ("", ""))
        out[name] = DrugIdentity(
            source_name=name, uniis=SEPARATOR.join(own), mesh_id=mesh_id,
            mesh_name=mesh_name, nodes=SEPARATOR.join(ids), node_type=node_type,
            basis=BASIS_REGISTRY if mesh_id else BASIS_NO_MESH if own else BASIS_NONE)
    rows = out.values()
    report = IdentityReport(
        names=len(out), with_unii=sum(1 for r in rows if r.uniis),
        with_mesh=sum(1 for r in rows if r.mesh_id),
        with_drug_node=sum(1 for r in rows if r.node_type == DRUG),
        with_chemical_node=sum(1 for r in rows if r.node_type == CHEMICAL),
        without_node=sum(1 for r in rows if not r.nodes))
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
        "FDA ingredient identity (exact ids, D42)",
        f"  ingredient names                       {report.names:>9,}",
        f"    with a UNII                          {report.with_unii:>9,}",
        f"    with a MeSH id (UNII registry)       {report.with_mesh:>9,}",
        f"    iKraph2 node: TherapyPreventions     {report.with_drug_node:>9,}",
        f"    iKraph2 node: Chemical (fallback)    {report.with_chemical_node:>9,}",
        f"    no iKraph2 node                      {report.without_node:>9,}",
    ])


__all__ = ["BASIS_NONE", "BASIS_NO_MESH", "BASIS_REGISTRY", "DrugIdentity",
           "IdentityReport", "SEPARATOR", "build", "render", "split", "uniis_of"]
