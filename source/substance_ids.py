"""One substance dictionary: UNII ↔ MeSH ↔ PubChem ↔ RxCUI (D45).

iKraph2 gives each drug entity **one** external id — a UNII, a MeSH id, an
RxCUI, a PubChem CID — so a drug is reached only through the id its entity
happens to carry. Both sides of the pipeline (the FDA ingredient, S2; the
registry intervention, S3) therefore first expand what they hold into every
id of the same substance, through this one table, and only then look the ids
up. Before D45 the two sides each had their own rules (``unii_mesh.tsv`` for
the FDA, raw MeSH registry numbers for the trials, ``id_bridge.tsv`` used by
neither), and the same drug could land on different entities.

Every pairing is exact and unique:

* **UNII → MeSH.** The record listing the UNII as a concept's
  ``RegistryNumber`` — exactly one such record, and the two share a name
  (one of the UNII's names equals one of the record's entry terms, case and
  punctuation folded), which drops NLM's few curation slips. A UNII no record
  claims that way may sit in exactly one record's ``RelatedRegistryNumber``
  list — a salt or ester filed under its moiety — and is mapped there when the
  record's name appears whole inside the UNII's name (``esomeprazole
  magnesium`` ⊃ ``esomeprazole``).
* **MeSH → its own UNII** (``moiety_of``). The single UNII on the record's
  preferred concept, when that UNII maps back to the record: the substance the
  record is about.

The roles say how a UNII sits in its record. Resolution (:mod:`service.drug_identity`)
uses a record only for a UNII on its **preferred** concept: a salt filed under
its moiety's record (``form``, ``related``) or another substance on another
concept (``primary``) never takes the record's id, so nothing is folded. The
other roles are kept for audit, and for a biosimilar's retrieval key (``stem``).
* **UNII ↔ PubChem CID, UNII ↔ RxCUI** — from the FDA's UNII Records, kept only
  one-to-one both ways.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import NamedTuple

from source.unii_bridge import UNII_SHAPE, Bridge
from source.unii_mesh import MeshIndex, fold
from utility.logging import get_logger

_log = get_logger(__name__)

ROLE_PREFERRED = "preferred"
#: Another concept of the record whose name contains the record's own name as
#: whole words — a salt, ester or hydrate MeSH files as a concept
#: (``afatinib dimaleate`` under Afatinib).
ROLE_FORM = "form"
#: Another concept that is a different substance (brexanolone under
#: Pregnanolone).
ROLE_PRIMARY = "primary"
ROLE_RELATED = "related"

PUBCHEM = "PubChem:CID"
RXCUI = "RXCUI:"


class Substance(NamedTuple):
    """One UNII and every exact id of its substance (``substance_ids.tsv``)."""

    unii: str
    name: str            # the FDA's display name
    mesh_id: str         # "" where no record maps
    mesh_name: str
    mesh_role: str       # preferred | form | primary | related | ""
    moiety_unii: str     # the MeSH record's own UNII (may be this one), or ""
    pubchem: str         # ``PubChem:CID…`` or ""
    rxcui: str           # ``RXCUI:…`` or ""


class Dictionary(NamedTuple):
    """The table, keyed both ways."""

    by_unii: Mapping[str, Substance]
    #: ``MESH:<ui>`` → its moiety UNII.
    moiety_of: Mapping[str, str]

    def mesh_of(self, unii: str) -> str:
        """The UNII's MeSH id, or ""."""
        row = self.by_unii.get(unii)
        return row.mesh_id if row else ""


class DictionaryReport(NamedTuple):
    uniis: int
    preferred: int
    form: int
    primary: int
    related: int
    several_records: int
    no_shared_name: int
    pubchem: int
    rxcui: int
    moieties: int


def one_to_one(rows: Iterable[Mapping[str, str]], column: str) -> dict[str, str]:
    """UNII → the value of ``column``, kept only where the pairing is one-to-one.

    >>> one_to_one([{"UNII": "AG9MHG098Z", "PUBCHEM": "135565884"},
    ...             {"UNII": "AAAAAAAAAA", "PUBCHEM": "1"},
    ...             {"UNII": "BBBBBBBBBB", "PUBCHEM": "1"},
    ...             {"UNII": "CCCCCCCCCC", "PUBCHEM": "5|6"},
    ...             {"UNII": "DDDDDDDDDD", "PUBCHEM": ""}], "PUBCHEM")
    {'AG9MHG098Z': '135565884'}
    """
    values_of: dict[str, set[str]] = defaultdict(set)
    uniis_of: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        unii = (row.get("UNII") or "").strip()
        for value in re.split(r"[|;, ]+", (row.get(column) or "").strip()):
            if unii and value.isdigit():
                values_of[unii].add(value)
                uniis_of[value].add(unii)
    return {unii: next(iter(values)) for unii, values in values_of.items()
            if len(values) == 1 and len(uniis_of[next(iter(values))]) == 1}


def _contains(names: Iterable[str], terms: Iterable[str]) -> bool:
    """Some term appears whole, word for word, inside some name.

    >>> _contains(["esomeprazole magnesium"], ["esomeprazole"])
    True
    >>> _contains(["omeprazole magnesium"], ["esomeprazole"])
    False
    """
    padded = [f" {n} " for n in names]
    return any(f" {t} " in name for t in terms if t for name in padded)


def build(index: MeshIndex, records: Iterable[Mapping[str, str]],
          names: Mapping[str, set[str]],
          extra_names: Mapping[str, Iterable[str]] | None = None
          ) -> tuple[Dictionary, DictionaryReport]:
    """The dictionary over every UNII the UNII Records or MeSH know.

    ``names`` is UNII → folded names (:func:`source.unii_mesh.unii_names`);
    ``extra_names`` adds names the FDA uses for a UNII (its ingredient names).
    """
    rows = list(records)
    pubchem = one_to_one(rows, "PUBCHEM")
    rxcui = one_to_one(rows, "RXCUI")
    display = {(r.get("UNII") or "").strip(): (r.get("DISPLAY_NAME") or "").strip()
               for r in rows if UNII_SHAPE.match((r.get("UNII") or "").strip())}
    primary_of: dict[str, set[str]] = defaultdict(set)
    for mesh, found in index.uniis.items():
        for unii in found:
            primary_of[unii].add(mesh)
    related_of: dict[str, set[str]] = defaultdict(set)
    for mesh, found in index.related.items():
        for unii in found:
            related_of[unii].add(mesh)
    wanted = set(index.uniis) | set(index.related)
    terms: dict[str, set[str]] = defaultdict(set)
    for term, meshes in index.by_term.items():
        for mesh in meshes & wanted:
            terms[mesh].add(fold(term))
    extra = extra_names or {}

    assigned: dict[str, tuple[str, str]] = {}
    tally: Counter[str] = Counter()
    for unii in sorted(set(display) | set(primary_of) | set(related_of)):
        known = set(names.get(unii, set())) | {fold(display.get(unii, ""))} | {
            fold(n) for n in extra.get(unii, ())}
        known.discard("")
        claims = primary_of.get(unii) or related_of.get(unii)
        if not claims:
            continue
        if len(claims) > 1:
            tally["several"] += 1
            continue
        mesh = next(iter(claims))
        if unii in primary_of:
            if not known & terms[mesh]:
                tally["no_name"] += 1
                continue
            if unii in index.preferred.get(mesh, ()):
                role = ROLE_PREFERRED
            elif _contains(known, [fold(index.names.get(mesh, ""))]):
                role = ROLE_FORM
            else:
                role = ROLE_PRIMARY
        elif _contains(known, terms[mesh]):
            role = ROLE_RELATED
        else:
            tally["no_name"] += 1
            continue
        assigned[unii] = (mesh, role)
        tally[role] += 1

    moiety_of = {mesh: next(iter(own)) for mesh, own in index.preferred.items()
                 if len(own) == 1 and assigned.get(next(iter(own)), ("",))[0] == mesh}
    by_unii: dict[str, Substance] = {}
    for unii in sorted(set(assigned) | set(pubchem) | set(rxcui)):
        mesh, role = assigned.get(unii, ("", ""))
        by_unii[unii] = Substance(
            unii=unii, name=display.get(unii, ""), mesh_id=mesh,
            mesh_name=index.names.get(mesh, "") if mesh else "", mesh_role=role,
            moiety_unii=moiety_of.get(mesh, ""),
            pubchem=PUBCHEM + pubchem[unii] if unii in pubchem else "",
            rxcui=RXCUI + rxcui[unii] if unii in rxcui else "")
    report = DictionaryReport(
        uniis=len(by_unii), preferred=tally[ROLE_PREFERRED], primary=tally[ROLE_PRIMARY],
        related=tally[ROLE_RELATED], form=tally[ROLE_FORM], several_records=tally["several"],
        no_shared_name=tally["no_name"], pubchem=len(pubchem), rxcui=len(rxcui),
        moieties=len(moiety_of))
    _log.info("substance dictionary: %d UNIIs, %d with MeSH", report.uniis, len(assigned))
    return Dictionary(by_unii, moiety_of), report


def bridges(dictionary: Dictionary) -> list[Bridge]:
    """PubChem ↔ MeSH pairs (``id_bridge.tsv``, D39): each record's moiety UNII
    with its CID. One-to-one by construction — a moiety UNII belongs to one
    record, and a CID to one UNII."""
    return sorted(Bridge(unii, dictionary.by_unii[unii].pubchem, mesh)
                  for mesh, unii in dictionary.moiety_of.items()
                  if dictionary.by_unii[unii].pubchem)


def render(report: DictionaryReport) -> str:
    """The pass report, as ``--mode normalize`` prints it."""
    return "\n".join([
        "Substance dictionary (UNII <-> MeSH <-> PubChem <-> RxCUI, D45)",
        f"  UNIIs with any exact id                {report.uniis:>9,}",
        f"    MeSH: preferred concept              {report.preferred:>9,}",
        f"    MeSH: salt/ester concept             {report.form:>9,}",
        f"    MeSH: other substance concept        {report.primary:>9,}",
        f"    MeSH: related form (salt, ester)     {report.related:>9,}",
        f"    claimed by several records (out)     {report.several_records:>9,}",
        f"    no shared name (out)                 {report.no_shared_name:>9,}",
        f"    PubChem CID, one-to-one              {report.pubchem:>9,}",
        f"    RxCUI, one-to-one                    {report.rxcui:>9,}",
        f"  MeSH records with a moiety UNII        {report.moieties:>9,}",
    ])


__all__ = ["Dictionary", "DictionaryReport", "PUBCHEM", "ROLE_FORM", "ROLE_PREFERRED", "ROLE_PRIMARY",
           "ROLE_RELATED", "RXCUI", "Substance", "bridges", "build", "one_to_one",
           "render"]
