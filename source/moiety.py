"""Salt to active moiety, from the structured label extract.

The FDA names a product's ingredient as the salt it is formulated as —
``ESOMEPRAZOLE MAGNESIUM``, ``ABIRATERONE ACETATE`` — while a trial names the
moiety that does the work, ``esomeprazole``. 723 of the 2,270 drugs this
project resolves are salts of a different moiety, and where the graph happens
to give the salt and the moiety different ids, that drug matches **no** trials
at all: silently, because a drug with zero candidates looks the same as a drug
whose trials were never registered.

``drug_indication_market.csv.gz`` carries both, per label: ``active_ingredients``
beside ``moiety_names``, and their UNIIs beside them.

**Only single-ingredient rows are used.** The two lists are semicolon-joined and
do **not** correspond positionally — zipping them pairs
``.ALPHA.-LIPOIC ACID`` with ``.ALPHA.-KETOGLUTARIC ACID``, and makes 1,350
salts claim more than one moiety. Restricting to rows naming exactly one
ingredient and one moiety leaves 3,277 salts with 48 residual conflicts, which
are settled by majority across the 65,248 rows that state them. Precision
matters more than reach here: a wrong moiety sends a drug's whole shortlist to
the wrong molecule.

**Biosimilar suffixes are never folded.** The file maps ``USTEKINUMAB-AEKN`` to
``USTEKINUMAB``, which would merge a biosimilar into its reference product. A
biosimilar's 351(k) approval rests on *its own* trials, so the suffix is the
identity, not noise.
"""

from __future__ import annotations

import csv
import gzip
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: A biosimilar's four-letter suffix, which distinguishes it from its reference
#: product and must survive normalisation. See D11.
BIOSIMILAR_SUFFIX = re.compile(r"-[A-Z]{4}$")

#: The extract quotes long ingredient lists, so a field can exceed the default.
FIELD_LIMIT: int = 10_000_000


class Moiety(NamedTuple):
    """One salt and the moiety it carries."""

    salt: str
    moiety: str
    salt_unii: str
    moiety_unii: str


def is_biosimilar(name: str) -> bool:
    """Whether a name carries a biosimilar's four-letter suffix.

    >>> is_biosimilar("USTEKINUMAB-AEKN")
    True
    >>> is_biosimilar("USTEKINUMAB")
    False
    >>> is_biosimilar("ABIRATERONE ACETATE")
    False
    """
    return bool(BIOSIMILAR_SUFFIX.search(name.upper()))


def read(path: Path) -> dict[str, Moiety]:
    """Salt name (upper-cased) → its moiety. Absent file yields nothing.

    Silent on a missing file, to match the other readers: the caller decides
    whether an absent external resource is fatal, and this one is an
    enrichment rather than a dependency.
    """
    if not path.is_file():
        _log.warning("%s: no moiety extract", path)
        return {}
    csv.field_size_limit(FIELD_LIMIT)
    seen: dict[str, Counter[tuple[str, str, str]]] = defaultdict(Counter)
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ingredients = _split(row.get("active_ingredients"))
            moieties = _split(row.get("moiety_names"))
            uniis = _split(row.get("unii"))
            moiety_uniis = _split(row.get("moiety_uniis"))
            if not (
                len(ingredients) == len(moieties) == len(uniis)
                == len(moiety_uniis) == 1
            ):
                continue
            rows += 1
            seen[ingredients[0].upper()][
                (moieties[0].upper(), uniis[0], moiety_uniis[0])
            ] += 1
    out: dict[str, Moiety] = {}
    conflicts = 0
    for salt, counted in seen.items():
        if len(counted) > 1:
            conflicts += 1
        moiety, salt_unii, moiety_unii = counted.most_common(1)[0][0]
        if moiety == salt or is_biosimilar(salt):
            continue
        out[salt] = Moiety(salt, moiety, salt_unii, moiety_unii)
    _log.info(
        "%d single-ingredient rows -> %d salts with a distinct moiety "
        "(%d conflicts settled by majority)",
        rows,
        len(out),
        conflicts,
    )
    return out


def read_generic_names(path: Path) -> dict[str, str]:
    """Application (``NDA208215``) → the generic name its labels give, by majority.

    The extract names each label's product by ``generic_name`` (``EMTRICITABINE
    AND TENOFOVIR ALAFENAMIDE``) and lists the applications it was marketed
    under. Absent file yields nothing, like :func:`read`.
    """
    if not path.is_file():
        return {}
    csv.field_size_limit(FIELD_LIMIT)
    seen: dict[str, Counter[str]] = defaultdict(Counter)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            generic = (row.get("generic_name") or "").strip()
            if not generic:
                continue
            for application in _split(row.get("application_numbers")):
                seen[application.upper()][generic.upper()] += 1
    return {app: counted.most_common(1)[0][0] for app, counted in seen.items()}


def _split(value: str | None) -> list[str]:
    """A semicolon-joined field as a list, blanks dropped.

    >>> _split("A; B")
    ['A', 'B']
    >>> _split(None)
    []
    >>> _split(" ; ")
    []
    """
    return [part.strip() for part in (value or "").split(";") if part.strip()]


__all__ = ["BIOSIMILAR_SUFFIX", "FIELD_LIMIT", "Moiety", "is_biosimilar", "read"]
