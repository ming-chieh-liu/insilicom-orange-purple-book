"""MeSH read once: registry numbers, entry terms, names, headings (D42, D44, D45).

ClinicalTrials.gov codes a trial's interventions in MeSH (NLM's
``derivedSection``, D21); the FDA names a drug by its UNII. MeSH records the
UNII of a substance among its registry numbers, in descriptors
(``desc2026.xml``) and supplementary concept records (``supp2026.xml``), so
NLM's own curation joins the two without any name lookup.

A record's registry numbers come in two kinds, and NLM keeps them apart:

* ``RegistryNumber`` — the UNII of a concept of the record. The one on the
  record's **preferred** concept (``PreferredConceptYN="Y"``) is the substance
  the record is about: ``Esomeprazole`` → ``N3PA6559FT``.
* ``RelatedRegistryNumber`` — UNIIs of related forms filed under the record,
  mostly salts, esters and hydrates: ``Esomeprazole`` lists
  ``R6DXU4WAY9`` (esomeprazole magnesium) here. This is the salt → moiety fold
  D11 asks for, curated by NLM.

:mod:`source.substance_ids` turns these into the one UNII ↔ MeSH ↔ PubChem ↔
RxCUI dictionary both sides resolve through (D45).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from source.unii_bridge import UNII_SHAPE
from utility.logging import get_logger

_log = get_logger(__name__)


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


_EMPTY: Mapping[str, frozenset[str]] = {}


class MeshIndex(NamedTuple):
    """Every MeSH descriptor and supplementary record, read once (D44)."""

    #: ``MESH:<ui>`` → the UNIIs among its concepts' ``RegistryNumber`` values
    #: (only records with any).
    uniis: Mapping[str, frozenset[str]]
    #: :func:`exact_key` of each entry term → the records listing it.
    by_term: Mapping[str, frozenset[str]]
    #: ``MESH:<ui>`` → its preferred name.
    names: Mapping[str, str]
    #: ``MESH:C…`` supplementary record → the descriptors it is mapped to.
    headings: Mapping[str, frozenset[str]]
    #: ``MESH:<ui>`` → the UNIIs of its preferred concept (D45).
    preferred: Mapping[str, frozenset[str]] = _EMPTY
    #: ``MESH:<ui>`` → its ``RelatedRegistryNumber`` UNIIs: salts, esters (D45).
    related: Mapping[str, frozenset[str]] = _EMPTY


def _uniis(numbers: Iterable[ET.Element]) -> frozenset[str]:
    return frozenset(n.text.strip() for n in numbers
                     if n.text and UNII_SHAPE.match(n.text.strip()))


def mesh_index(paths: Iterable[Path]) -> MeshIndex:
    """Registry numbers, entry terms, names and heading mappings, in one pass."""
    uniis: dict[str, frozenset[str]] = {}
    preferred: dict[str, frozenset[str]] = {}
    related: dict[str, frozenset[str]] = {}
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
                concepts = element.findall("ConceptList/Concept")
                found = _uniis(n for c in concepts
                               for n in c.findall("RegistryNumberList/RegistryNumber"))
                if found:
                    uniis[mesh] = found
                own = _uniis(n for c in concepts if c.get("PreferredConceptYN") == "Y"
                             for n in c.findall("RegistryNumberList/RegistryNumber"))
                if own:
                    preferred[mesh] = own
                kin = _uniis(n for c in concepts for n in c.findall(
                    "RelatedRegistryNumberList/RelatedRegistryNumber")) - found
                if kin:
                    related[mesh] = kin
                for term in [name, *(t.text or "" for t in element.findall(
                        "ConceptList/Concept/TermList/Term/String"))]:
                    if term.strip():
                        by_term[exact_key(term)].add(mesh)
            element.clear()
    _log.info("MeSH: %d records, %d with a UNII, %d with related UNIIs, %d entry terms",
              len(names), len(uniis), len(related), len(by_term))
    return MeshIndex(uniis, {k: frozenset(v) for k, v in by_term.items()}, names, headings,
                     preferred, related)


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


__all__ = ["MeshIndex", "exact_key", "fold", "mesh_index", "unii_names"]
