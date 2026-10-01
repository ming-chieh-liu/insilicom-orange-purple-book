"""Every name an approved drug goes by, one row per application (P1).

A drug product carries four kinds of name, and each stage needs a different one:

* **brand** — the company's trade name for the product (``DESCOVY``). It names
  the approval, and §14 tables and abstracts often use it ("BRUKINSA vs
  ibrutinib"), so the judge is shown it.
* **generic name** — the nonproprietary name of the product (``EMTRICITABINE AND
  TENOFOVIR ALAFENAMIDE``).
* **active ingredient** — the exact substance in the product, salt included
  (``TENOFOVIR ALAFENAMIDE FUMARATE``), as Drugs@FDA lists it.
* **active moiety** — the molecule that does the work, salt removed
  (``TENOFOVIR``). Registries and abstracts name the moiety.

Each ingredient and moiety also carries its **UNII**, the FDA's substance id.

Sources: Drugs@FDA ``Products.txt`` (brand, ingredient — present for 5,711 of
5,716 trial-relevant applications), the label extract (generic name, and the
salt → moiety fold of :mod:`source.moiety`), and the UNII registry (name →
UNII). A combination product lists several ingredients; their moieties and
UNIIs are kept in the same order, ``;``-joined.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from service import identity_census
from source.moiety import Moiety
from source.raw_table import read_rows
from utility import application_number as appno

#: Joins several names in one TSV field.
JOIN: str = "; "


class DrugNames(NamedTuple):
    """The names of one application's drug."""

    application: str
    brand_names: str
    generic_name: str
    active_ingredients: str
    moieties: str
    ingredient_uniis: str
    moiety_uniis: str

    @property
    def display(self) -> str:
        """``BRUKINSA (zanubrutinib)`` — what a judge is shown.

        >>> DrugNames("NDA1", "BRUKINSA", "ZANUBRUTINIB", "ZANUBRUTINIB",
        ...           "ZANUBRUTINIB", "", "").display
        'BRUKINSA (zanubrutinib)'
        >>> DrugNames("NDA2", "", "", "ASPIRIN", "ASPIRIN", "", "").display
        'aspirin'
        >>> DrugNames("NDA3", "VEMLIDY", "", "TENOFOVIR ALAFENAMIDE FUMARATE",
        ...           "TENOFOVIR ANHYDROUS", "", "").display
        'VEMLIDY (tenofovir alafenamide fumarate)'

        Without a generic name the ingredients stand in, never the moieties:
        the FDA's moiety names read "tenofovir anhydrous", which no one calls
        the drug.
        """
        generic = (
            self.generic_name
            or " and ".join(self.active_ingredients.split(JOIN))
        ).lower()
        brands = [b for b in self.brand_names.split(JOIN) if b and b.lower() != generic]
        return f"{JOIN.join(brands)} ({generic})" if brands else generic


def unii_index(data: RawData) -> dict[str, str]:
    """Upper-cased substance name → UNII, from the FDA's UNII registry.

    A name the registry gives two UNIIs is dropped rather than guessed.
    """
    claims: dict[str, set[str]] = defaultdict(set)
    for row in read_rows(data.unii_names):
        unii = (row.get("UNII") or "").strip()
        for key in ("NAME", "DISPLAY_NAME"):
            name = (row.get(key) or "").strip().upper()
            if name and unii:
                claims[name].add(unii)
    return {name: next(iter(u)) for name, u in claims.items() if len(u) == 1}


def build(
    data: RawData,
    applications: frozenset[str],
    moieties: dict[str, Moiety],
    generic_names: dict[str, str],
    uniis: dict[str, str],
) -> list[DrugNames]:
    """One row per application in ``applications``, names in a stable order."""
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row.get("ApplType"), row.get("ApplNo"))
        if parsed.is_application:
            canonical_of[parsed.digits] = parsed.canonical
    brands: dict[str, dict[str, None]] = defaultdict(dict)
    ingredients: dict[str, dict[str, None]] = defaultdict(dict)
    for row in read_rows(data.products):
        application = canonical_of.get(appno.pad(row.get("ApplNo")), "")
        if application not in applications:
            continue
        brand = (row.get("DrugName") or "").strip().upper()
        if brand:
            brands[application].setdefault(brand, None)
        for part in (row.get("ActiveIngredient") or "").split(
            identity_census.INGREDIENT_SEPARATOR
        ):
            name = part.strip().upper()
            if name:
                ingredients[application].setdefault(name, None)
    out: list[DrugNames] = []
    for application in sorted(applications):
        names = list(ingredients.get(application, {}))
        folded = [moieties[n].moiety if n in moieties else n for n in names]
        out.append(
            DrugNames(
                application=application,
                brand_names=JOIN.join(brands.get(application, {})),
                generic_name=generic_names.get(application, ""),
                active_ingredients=JOIN.join(names),
                moieties=JOIN.join(folded),
                ingredient_uniis=JOIN.join(
                    moieties[n].salt_unii if n in moieties else uniis.get(n, "")
                    for n in names
                ),
                moiety_uniis=JOIN.join(
                    moieties[n].moiety_unii if n in moieties else uniis.get(m, "")
                    for n, m in zip(names, folded)
                ),
            )
        )
    return out


def moiety_key(row: DrugNames) -> str:
    """What makes two applications "the same drug" for D16 (D37).

    The sorted moieties: a new formulation or second application of
    rivaroxaban shares Xarelto's key, so a trial repeated on its label is a
    later appearance. A combination has its own key (Descovy is not Vemlidy),
    and a biosimilar keeps its suffix (D11). Without moieties, the application.

    >>> moiety_key(DrugNames("NDA2", "", "", "B; A", "TENOFOVIR; EMTRICITABINE", "", ""))
    'EMTRICITABINE|TENOFOVIR'
    >>> moiety_key(DrugNames("NDA3", "", "", "", "", "", ""))
    'NDA3'
    """
    parts = sorted({m.strip().upper() for m in row.moieties.split(";") if m.strip()})
    return "|".join(parts) if parts else row.application


def moiety_groups(path: Path) -> dict[str, str]:
    """Application → :func:`moiety_key`, from ``drug_names.tsv``; absent file → {}."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        rows = [DrugNames(**{f: r.get(f, "") or "" for f in DrugNames._fields})
                for r in csv.DictReader(handle, delimiter="\t")]
    return {row.application: moiety_key(row) for row in rows}


__all__ = ["DrugNames", "JOIN", "build", "moiety_groups", "moiety_key", "unii_index"]
