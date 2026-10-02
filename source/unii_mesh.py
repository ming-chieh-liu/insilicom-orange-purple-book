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


def exact_key(text: str) -> str:
    """A name as exact matching compares it: case and runs of spaces folded,
    nothing else (D44). iKraph2 keys its names the same way.

    >>> exact_key("  Non-Small  Cell Lung CANCER ")
    'non-small cell lung cancer'
    """
    return " ".join(text.casefold().split())


class MeshIndex(NamedTuple):
    """Every MeSH descriptor and supplementary record, read once (D44)."""

    #: ``MESH:<ui>`` → its UNII-shaped registry numbers (only records with any).
    uniis: dict[str, frozenset[str]]
    #: :func:`exact_key` of each entry term → the records listing it.
    by_term: dict[str, frozenset[str]]
    #: ``MESH:<ui>`` → its preferred name.
    names: dict[str, str]
    #: ``MESH:C…`` supplementary record → the descriptors it is mapped to.
    headings: dict[str, frozenset[str]]


def mesh_index(paths: Iterable[Path]) -> MeshIndex:
    """Registry numbers, entry terms, names and heading mappings, in one pass."""
    uniis: dict[str, frozenset[str]] = {}
    by_term: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    headings: dict[str, frozenset[str]] = {}
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
                mapped = frozenset(
                    "MESH:" + ref.text.strip().lstrip("*")
                    for ref in element.findall(
                        "HeadingMappedToList/HeadingMappedTo/DescriptorReferredTo/DescriptorUI")
                    if ref.text)
                if ui and mapped:
                    headings[f"MESH:{ui}"] = mapped
            else:
                continue
            if ui:
                mesh = f"MESH:{ui}"
                names[mesh] = name
                found = frozenset(
                    number.text.strip() for number in element.iter("RegistryNumber")
                    if number.text and UNII_SHAPE.match(number.text.strip()))
                if found:
                    uniis[mesh] = found
                for term in [name, *(t.text or "" for t in element.findall(
                        "ConceptList/Concept/TermList/Term/String"))]:
                    if term.strip():
                        by_term[exact_key(term)].add(mesh)
            element.clear()
    _log.info("MeSH: %d records, %d with a UNII, %d entry terms", len(names), len(uniis),
              len(by_term))
    return MeshIndex(uniis, {k: frozenset(v) for k, v in by_term.items()}, names, headings)


def records_of(index: MeshIndex) -> list[MeshRecord]:
    """The records holding a UNII, as :func:`build` reads them."""
    terms: dict[str, set[str]] = defaultdict(set)
    for term, meshes in index.by_term.items():
        for mesh in meshes:
            if mesh in index.uniis:
                terms[mesh].add(fold(term))
    return [MeshRecord(mesh, index.names.get(mesh, ""), frozenset(terms[mesh]), found)
            for mesh, found in index.uniis.items()]


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


__all__ = ["BASIS_REGISTRY", "MapReport", "MeshIndex", "MeshRecord", "UniiMesh", "build", "exact_key", "fold", "mesh_index", "records_of", "read", "render", "unii_names"]
