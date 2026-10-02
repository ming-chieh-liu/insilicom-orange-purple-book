"""An exact PubChem <-> MeSH bridge through the FDA's UNII (D39).

The FDA side often resolves a drug to a PubChem id (``PubChem:CID135565884``,
zanubrutinib) while NLM codes the trials of that drug in MeSH
(``MESH:C000629551``). biokde gives an entity one external id, never both, so
the two never meet and the registry route finds none of the drug's trials.

Both vocabularies record the substance's **UNII**, the FDA's own substance id:
the FDA's UNII Records list each UNII's PubChem CID, and MeSH lists a
substance's UNII among its ``RegistryNumber`` values (descriptors in
``desc2026.xml``, supplementary concept records in ``supp2026.xml``). A
PubChem CID and a MeSH id are joined **only** when each side maps one-to-one:

* the UNII has exactly one PubChem CID, and that CID exactly one UNII;
* the MeSH record has exactly one UNII-shaped registry number, and that UNII
  exactly one MeSH record.

Anything else is left out — the user's rule is precision over recall, so an
ambiguous bridge is no bridge.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: A UNII: ten upper-case letters and digits. MeSH ``RegistryNumber`` also
#: holds CAS numbers (``50-78-2``), EC numbers (``EC 3.4.21.-``) and ``0``.
UNII_SHAPE = re.compile(r"^[0-9A-Z]{10}$")


class Bridge(NamedTuple):
    """One exact substance identity across the two vocabularies."""

    unii: str
    pubchem: str   # ``PubChem:CID…``
    mesh: str      # ``MESH:D…`` or ``MESH:C…``


def unii_to_pubchem(rows: Iterable[dict[str, str]]) -> dict[str, str]:
    """UNII → ``PubChem:CID…``, kept only where the pairing is one-to-one.

    >>> unii_to_pubchem([{"UNII": "AG9MHG098Z", "PUBCHEM": "135565884"},
    ...                  {"UNII": "AAAAAAAAAA", "PUBCHEM": "1"},
    ...                  {"UNII": "BBBBBBBBBB", "PUBCHEM": "1"},
    ...                  {"UNII": "CCCCCCCCCC", "PUBCHEM": ""}])
    {'AG9MHG098Z': 'PubChem:CID135565884'}
    """
    cids_of: dict[str, set[str]] = defaultdict(set)
    uniis_of: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        unii = (row.get("UNII") or "").strip()
        for cid in re.split(r"[|;, ]+", (row.get("PUBCHEM") or "").strip()):
            if unii and cid.isdigit():
                cids_of[unii].add(cid)
                uniis_of[cid].add(unii)
    return {
        unii: f"PubChem:CID{next(iter(cids))}"
        for unii, cids in cids_of.items()
        if len(cids) == 1 and len(uniis_of[next(iter(cids))]) == 1
    }


def read_unii_records(path: Path) -> Iterator[dict[str, str]]:
    """Rows of ``UNII_Records_*.txt`` inside the FDA's ``UNII_Data.zip``."""
    if not path.is_file():
        _log.warning("%s: no UNII records", path)
        return
    with zipfile.ZipFile(path) as archive:
        name = next((n for n in archive.namelist() if n.startswith("UNII_Records")), "")
        if not name:
            return
        with archive.open(name) as handle:
            yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"),
                                      delimiter="\t")


def mesh_to_unii(records: Iterable[tuple[str, list[str]]]) -> dict[str, str]:
    """``MESH:<ui>`` → UNII, kept only where the pairing is one-to-one.

    >>> mesh_to_unii([("MESH:C1", ["AG9MHG098Z", "1691249-45-2"]),
    ...               ("MESH:C2", ["AAAAAAAAAA", "BBBBBBBBBB"]),
    ...               ("MESH:C3", ["CCCCCCCCCC"]), ("MESH:C4", ["CCCCCCCCCC"]),
    ...               ("MESH:D5", ["0"])])
    {'MESH:C1': 'AG9MHG098Z'}
    """
    uniis_of: dict[str, set[str]] = {}
    meshes_of: dict[str, set[str]] = defaultdict(set)
    for mesh, numbers in records:
        uniis = {n for n in numbers if UNII_SHAPE.match(n)}
        uniis_of[mesh] = uniis
        for unii in uniis:
            meshes_of[unii].add(mesh)
    return {
        mesh: next(iter(uniis))
        for mesh, uniis in uniis_of.items()
        if len(uniis) == 1 and len(meshes_of[next(iter(uniis))]) == 1
    }


def build(unii_pubchem: dict[str, str], mesh_unii: dict[str, str]) -> list[Bridge]:
    """The bridges: a UNII with exactly one PubChem CID and exactly one MeSH record.

    >>> build({"AG9MHG098Z": "PubChem:CID135565884", "X000000000": "PubChem:CID9"},
    ...       {"MESH:C000629551": "AG9MHG098Z"})
    [Bridge(unii='AG9MHG098Z', pubchem='PubChem:CID135565884', mesh='MESH:C000629551')]
    """
    return sorted(
        Bridge(unii, unii_pubchem[unii], mesh)
        for mesh, unii in mesh_unii.items()
        if unii in unii_pubchem
    )


def equivalents(bridges: Iterable[Bridge]) -> dict[str, str]:
    """Each bridged id → its counterpart, both directions.

    >>> equivalents([Bridge("U", "PubChem:CID1", "MESH:C1")])
    {'PubChem:CID1': 'MESH:C1', 'MESH:C1': 'PubChem:CID1'}
    """
    out: dict[str, str] = {}
    for bridge in bridges:
        out[bridge.pubchem] = bridge.mesh
        out[bridge.mesh] = bridge.pubchem
    return out


def read_bridges(path: Path) -> list[Bridge]:
    """``id_bridge.tsv`` as written by S2; absent file → []."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [Bridge(r["unii"], r["pubchem"], r["mesh"])
                for r in csv.DictReader(handle, delimiter="\t")]


__all__ = [
    "Bridge",
    "UNII_SHAPE",
    "build",
    "equivalents",
    "mesh_to_unii",
    "read_bridges",
    "read_unii_records",
    "unii_to_pubchem",
]
