"""UNII → MeSH from MeSH's own registry numbers (D42).

ClinicalTrials.gov codes a trial's interventions in MeSH (NLM's
``derivedSection``, D21); the FDA names a drug by its UNII. MeSH records the
UNII of a substance among its ``RegistryNumber`` values, in descriptors
(``desc2026.xml``) and supplementary concept records (``supp2026.xml``), so
NLM's own curation joins the two without any name lookup.

A UNII is mapped to a MeSH record only when:

* the UNII sits on **exactly one** MeSH record — a UNII two records claim is
  left out, not arbitrated;
* the UNII and the record **share a name**: one of the UNII's names (the FDA
  UNII name list, or the FDA's own ingredient name) equals one of the record's
  entry terms after case and punctuation are folded. This drops the few
  records whose registry number is a curation slip (medium-chain triglycerides
  → ``SMOFlipid``).

Several UNIIs may share one record: MeSH files a drug's salts and hydrates
under the moiety (``ESOMEPRAZOLE MAGNESIUM`` and ``ESOMEPRAZOLE SODIUM`` →
``Esomeprazole``), which is the moiety fold D11 asks for.

Measured on the 3,068 UNIIs of ``drug_names.tsv`` (2026-10-01): no UNII sits on
two records; 2,563 sit on one, 2,556 of them share a name; a random 100 were
hand-checked and all were right.
"""

from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from source.unii_bridge import UNII_SHAPE
from utility.logging import get_logger

_log = get_logger(__name__)

#: ``basis`` of a mapping made here.
BASIS_REGISTRY = "unii_registry"


class MeshRecord(NamedTuple):
    """One MeSH descriptor or supplementary record, as this module reads it."""

    mesh: str                  # ``MESH:D…`` or ``MESH:C…``
    name: str                  # the record's preferred name
    terms: frozenset[str]      # folded entry terms, the preferred name included
    uniis: frozenset[str]      # UNII-shaped registry numbers


class UniiMesh(NamedTuple):
    """One UNII and the MeSH record it maps to (``unii_mesh.tsv``)."""

    unii: str
    mesh_id: str
    mesh_name: str
    #: How many UNIIs the record holds; above 1 the record is the moiety of a salt.
    record_uniis: int
    basis: str = BASIS_REGISTRY


class MapReport(NamedTuple):
    """What happened to each UNII asked about."""

    asked: int
    mapped: int
    no_record: int
    several_records: int
    no_shared_name: int


def fold(text: str) -> str:
    """Case and punctuation folded, for comparing names.

    >>> fold(".ALPHA.-Tocopherol Acetate")
    'alpha tocopherol acetate'
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def mesh_records(paths: Iterable[Path]) -> Iterator[MeshRecord]:
    """Every descriptor and supplementary record carrying a UNII. Streamed."""
    for path in paths:
        if not path.is_file():
            _log.warning("%s: no MeSH file", path)
            continue
        for _, element in ET.iterparse(path, events=("end",)):
            if element.tag == "DescriptorRecord":
                ui = element.findtext("DescriptorUI") or ""
                name = element.findtext("DescriptorName/String") or ""
            elif element.tag == "SupplementalRecord":
                ui = element.findtext("SupplementalRecordUI") or ""
                name = element.findtext("SupplementalRecordName/String") or ""
            else:
                continue
            uniis = frozenset(
                number.text.strip() for number in element.iter("RegistryNumber")
                if number.text and UNII_SHAPE.match(number.text.strip()))
            if ui and uniis:
                terms = {fold(name)} | {
                    fold(term.text)
                    for term in element.findall("ConceptList/Concept/TermList/Term/String")
                    if term.text}
                yield MeshRecord(f"MESH:{ui}", name, frozenset(t for t in terms if t), uniis)
            element.clear()


def unii_names(rows: Iterable[dict[str, str]]) -> dict[str, set[str]]:
    """UNII → its folded names, from the FDA's UNII name list.

    >>> sorted(unii_names([{"UNII": "21G72T1950", "NAME": "Jinarc",
    ...                     "DISPLAY_NAME": "TOLVAPTAN"}])["21G72T1950"])
    ['jinarc', 'tolvaptan']
    """
    out: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        unii = (row.get("UNII") or "").strip()
        if not UNII_SHAPE.match(unii):
            continue
        for key in ("NAME", "DISPLAY_NAME"):
            name = fold(row.get(key) or "")
            if name:
                out[unii].add(name)
    return dict(out)


def build(records: Iterable[MeshRecord], wanted: dict[str, set[str]],
          names: dict[str, set[str]]) -> tuple[list[UniiMesh], MapReport]:
    """The mapping for each UNII in ``wanted`` (UNII → the FDA's names for it).

    >>> rec = MeshRecord("MESH:D064098", "Esomeprazole",
    ...                  frozenset({"esomeprazole"}), frozenset({"N3PA6559FT", "R6DXU4WAY9"}))
    >>> rows, report = build([rec], {"R6DXU4WAY9": {"ESOMEPRAZOLE MAGNESIUM"}},
    ...                      {"R6DXU4WAY9": {"esomeprazole magnesium", "esomeprazole"}})
    >>> rows[0].mesh_id, rows[0].record_uniis, report.mapped
    ('MESH:D064098', 2, 1)
    """
    on: dict[str, list[MeshRecord]] = defaultdict(list)
    for record in records:
        for unii in record.uniis:
            if unii in wanted:
                on[unii].append(record)
    rows: list[UniiMesh] = []
    tally: Counter[str] = Counter()
    for unii in sorted(wanted):
        found = on.get(unii, [])
        if not found:
            tally["no_record"] += 1
            continue
        if len(found) > 1:
            tally["several"] += 1
            continue
        record = found[0]
        known = names.get(unii, set()) | {fold(n) for n in wanted[unii]}
        if not known & record.terms:
            tally["no_name"] += 1
            continue
        rows.append(UniiMesh(unii, record.mesh, record.name, len(record.uniis)))
    report = MapReport(len(wanted), len(rows), tally["no_record"], tally["several"],
                       tally["no_name"])
    _log.info("UNII -> MeSH: %d of %d mapped", report.mapped, report.asked)
    return rows, report


def read(path: Path) -> dict[str, str]:
    """``unii_mesh.tsv`` as UNII → MeSH id; absent file → {}."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {r["unii"]: r["mesh_id"] for r in csv.DictReader(handle, delimiter="\t")}


def render(report: MapReport) -> str:
    """The pass report, as ``--mode normalize`` prints it."""
    return "\n".join([
        "UNII -> MeSH (MeSH RegistryNumber, D42)",
        f"  FDA UNIIs asked                        {report.asked:>9,}",
        f"    mapped                               {report.mapped:>9,}",
        f"    no MeSH record holds the UNII        {report.no_record:>9,}",
        f"    held by several records (left out)   {report.several_records:>9,}",
        f"    no shared name (left out)            {report.no_shared_name:>9,}",
    ])


__all__ = ["BASIS_REGISTRY", "MapReport", "MeshRecord", "UniiMesh", "build", "fold",
           "mesh_records", "read", "render", "unii_names"]
