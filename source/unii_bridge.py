"""The exact PubChem <-> MeSH bridge (D39), as S8 reads it.

The FDA side sometimes holds a drug only by a PubChem id (``PubChem:CID135565884``,
zanubrutinib) while NLM codes the trials of that drug in MeSH
(``MESH:C000629551``). Both vocabularies record the substance's **UNII**, so
S2 writes each MeSH record's moiety UNII with its PubChem CID to
``id_bridge.tsv`` (:func:`source.substance_ids.bridges`, D45); S8 widens a drug
id with its counterpart. Every pair is one-to-one — the user's rule is
precision over recall, so an ambiguous bridge is no bridge.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
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


__all__ = ["Bridge", "UNII_SHAPE", "equivalents", "read_bridges", "read_unii_records"]
