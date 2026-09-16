"""Read the MeSH descriptor release: the disease vocabulary indications code to.

One descriptor is one disease concept — an id, a preferred name, the entry
terms that are synonyms for it, and the tree numbers that place it in the
hierarchy. Only the vocabulary is built here; the matching lives in
:mod:`service.indications`.

**Why the disease branches only.** MeSH describes drugs, anatomy, techniques
and organisms alongside diseases. An indication sentence names all of them —
"ADVIL is indicated for the relief of headache" contains a drug, a route and a
symptom — so coding against the whole vocabulary would return mostly things
that are not diseases. Branch ``C`` is Diseases and ``F03`` is Mental
Disorders, which MeSH files outside C; together they are the disease space and
nothing else is.

**Why entry terms and not only the preferred name.** The FDA does not write
MeSH. A label says "heart attack" where MeSH prefers "Myocardial Infarction",
and the entry terms are exactly the list of ways the concept is said. Dropping
them costs recall for no gain.

**Why a minimum length.** Two- and three-character entry terms are acronyms
that collide with ordinary words and dosage text — MeSH lists ``CD`` for
Crohn Disease and ``PE`` for Pulmonary Embolism, and an indications section
using "pe" as part of a unit would code as an embolism. Four characters is
where the collisions stop being the common case; ``AIDS`` and ``Pain`` survive
it, which is what matters.

Parsed with :mod:`xml.etree.ElementTree` in iterparse mode: the 2026 release is
a 313 MB document and nothing here needs more than one record at a time.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from config.settings import MeshVocabulary
from utility.logging import get_logger

_log = get_logger(__name__)

#: The tree branches that are diseases. ``C`` is Diseases; ``F03`` is Mental
#: Disorders, which MeSH files under Psychiatry rather than under C.
DISEASE_BRANCHES: tuple[str, ...] = ("C", "F03")

#: Shortest entry term that may be matched. See the module docstring: below
#: this, MeSH acronyms collide with ordinary words in indications prose.
MIN_TERM_LENGTH: int = 4


class Descriptor(NamedTuple):
    """One MeSH concept, as this project needs it."""

    ui: str
    name: str
    #: Every way the concept is written, preferred name included, lower-cased.
    terms: tuple[str, ...]
    #: Positions in the hierarchy, e.g. ``("C04.588.180",)``. A descriptor may
    #: sit in several places; each is a full dotted path from a branch root.
    tree_numbers: tuple[str, ...]
    #: The ``M``-prefixed concept ids this descriptor owns. MeSH numbers a
    #: *concept* separately from the *descriptor* that heads it, and other
    #: vocabularies cite either — biokde answers "Stomach Neoplasms" with
    #: ``MESH:D013274`` but "Gastric Cancer" with ``MESH:M0333444``. Without
    #: this, the same disease arrives under two ids and the join silently
    #: fails. Last field, and defaulted, so positional construction keeps
    #: working.
    concept_uis: tuple[str, ...] = ()

    @property
    def is_disease(self) -> bool:
        """Whether any tree number places this in the disease space.

        >>> Descriptor("D001", "x", (), ("C04.588",)).is_disease
        True
        >>> Descriptor("D002", "y", (), ("D12.776",)).is_disease
        False
        >>> Descriptor("D003", "z", (), ("F03.600",)).is_disease
        True
        """
        return any(
            number.startswith(branch)
            for number in self.tree_numbers
            for branch in DISEASE_BRANCHES
        )


#: Where the entry terms live. **Not** every ``String`` in the record: a
#: descriptor also carries its allowable qualifiers, and those are
#: ``blood``, ``diagnosis``, ``complications``, ``drug therapy`` and fifty
#: more — shared by thousands of descriptors and not synonyms for anything.
#: Sweeping them into the vocabulary made the first descriptor in the file,
#: ``Abdomen, Acute``, the most-indicated disease in the corpus at 5,633
#: applications, because it claimed every qualifier before any other record
#: was read. Found by checking the output, 2026-09-14.
TERM_PATH: str = "./ConceptList/Concept/TermList/Term/String"


def _terms_of(record: ElementTree.Element) -> tuple[str, ...]:
    """Every entry term on a descriptor, lower-cased and deduplicated.

    Order is preserved so the preferred name, which MeSH lists first, stays
    first and can be used as the display name.
    """
    seen: dict[str, None] = {}
    for element in record.findall(TERM_PATH):
        text = (element.text or "").strip().lower()
        if len(text) >= MIN_TERM_LENGTH:
            seen.setdefault(text, None)
    return tuple(seen)


def read(vocabulary: MeshVocabulary) -> Iterator[Descriptor]:
    """Yield every descriptor in the release. A missing file is silent.

    Silent rather than raising, to match :func:`source.openfda_json.read_results`:
    the caller decides whether an absent external resource is fatal, and the
    census-style callers would rather report zero than stop.
    """
    if not vocabulary.exists:
        _log.warning("%s: no MeSH descriptor file", vocabulary.descriptors)
        return
    for _event, element in ElementTree.iterparse(
        str(vocabulary.descriptors), events=("end",)
    ):
        if element.tag != "DescriptorRecord":
            continue
        ui = (element.findtext("DescriptorUI") or "").strip()
        name = (element.findtext("DescriptorName/String") or "").strip()
        if ui:
            yield Descriptor(
                ui=ui,
                name=name,
                terms=_terms_of(element),
                tree_numbers=tuple(
                    (number.text or "").strip()
                    for number in element.iter("TreeNumber")
                    if (number.text or "").strip()
                ),
                concept_uis=tuple(
                    text
                    for concept in element.findall("./ConceptList/Concept")
                    if (text := (concept.findtext("ConceptUI") or "").strip())
                ),
            )
        # iterparse builds the whole tree otherwise; 313 MB does not need to be
        # resident to read one record at a time.
        element.clear()


def concept_to_descriptor(vocabulary: MeshVocabulary) -> dict[str, str]:
    """Every ``M``-prefixed concept id → the ``D`` descriptor that heads it.

    The rollup that makes two spellings of one disease join. A descriptor's own
    ``D`` id maps to itself, so a caller can pass any MeSH id through this and
    get a descriptor back or nothing.
    """
    out: dict[str, str] = {}
    for descriptor in read(vocabulary):
        out[descriptor.ui] = descriptor.ui
        for concept in descriptor.concept_uis:
            out[concept] = descriptor.ui
    _log.info("%d MeSH ids roll up to a descriptor", len(out))
    return out


def rollup_and_names(
    vocabulary: MeshVocabulary,
) -> tuple[dict[str, str], dict[str, str]]:
    """``(concept -> descriptor, descriptor -> preferred name)`` in one pass.

    Both are wanted together and the release is a 313 MB document, so reading
    it twice to get them separately is a minute of nothing.
    """
    rollup: dict[str, str] = {}
    names: dict[str, str] = {}
    for descriptor in read(vocabulary):
        rollup[descriptor.ui] = descriptor.ui
        names[descriptor.ui] = descriptor.name
        for concept in descriptor.concept_uis:
            rollup[concept] = descriptor.ui
    _log.info("%d MeSH ids roll up; %d descriptor names", len(rollup), len(names))
    return rollup, names


def diseases(vocabulary: MeshVocabulary) -> list[Descriptor]:
    """Every disease descriptor, in file order."""
    found = [descriptor for descriptor in read(vocabulary) if descriptor.is_disease]
    _log.info("%d disease descriptors from %s", len(found), vocabulary.descriptors)
    return found


__all__ = [
    "DISEASE_BRANCHES",
    "MIN_TERM_LENGTH",
    "TERM_PATH",
    "Descriptor",
    "concept_to_descriptor",
    "diseases",
    "rollup_and_names",
    "read",
]
