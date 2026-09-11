"""Count the drug-identity chain, and prove the parsers on the way through.

This is a **census**, not an ingest: it reads ``fda_raw/``, counts, and returns
numbers. It stores nothing, emits no tables and builds no crosswalk. Its purpose
is that every figure in ``docs/drug-identity.md`` be reproducible by one command
rather than asserted from notes — and that, because every application number it
touches goes through :mod:`utility.application_number` and every date through
:mod:`utility.fda_date`, the parsers are exercised against all ~700,000 real
values rather than only against unit-test tables.

**The chain it measures**, verified to the unit on 2026-09-11::

    2,790 NDA/BLA ingredient components     split on ';' only
      - 67 that match no UNII name
    = 2,723 resolved names
      - 126 merged by name fan-in           175 names in 49 UNII groups
    = 2,597 distinct UNII                   the drug key

The recipe, exactly: take ``Products.ActiveIngredient``, split on ``';'``,
strip, upper-case, drop empties, keep only products whose application is typed
NDA or BLA in ``Applications.txt``, then match case-insensitively into
``UNII_Names.NAME``.

**Two subtractions, reported separately because they mean different things.**
The 67 are *lost* — a name we could not resolve, so a drug missing from the key
space. The 126 are *merged* — several names for one substance, which is the
crosswalk working. Quoting "2,597" without both numbers hides a coverage gap
behind a deduplication win.

**Why the type filter comes first.** 22,954 of 29,323 applications are ANDAs —
generic copies of drugs already counted. Including them would inflate the
ingredient count with duplicates of the branded originals. 8 ``Products.txt``
application numbers have no ``Applications.txt`` row at all, so they cannot be
typed and drop out; they are counted and named rather than defaulted to NDA.

Decided 2026-09-10; measured 2026-09-11.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

from config.settings import RawData
from source.raw_table import read_rows
from utility import application_number as appno
from utility import fda_date
from utility.logging import get_logger

_log = get_logger(__name__)

#: The application types whose ingredients count as a distinct drug. ANDAs are
#: generic copies of a drug already counted under its NDA.
BRANDED_TYPES: frozenset[str] = frozenset({"NDA", "BLA"})

#: The only separator used to split a combination product's ingredients. ','
#: (433 rows), ' AND ' (43) and '/' (159) also occur and are a recorded known
#: gap: splitting on ',' would break 'CARBON DIOXIDE, USP' into two substances
#: and would re-derive both published totals.
INGREDIENT_SEPARATOR: str = ";"


class IdentityChain(NamedTuple):
    """The drug-key chain, with both subtractions kept separate."""

    names: int
    unmatched: int
    resolved: int
    fan_in_groups: int
    fan_in_names: int
    merged: int
    uniis: int
    untypable_applnos: tuple[str, ...]
    ambiguous_names: int
    largest_groups: tuple[tuple[str, str, int], ...]

    @property
    def reconciles(self) -> bool:
        """``names - unmatched - merged == uniis``, the arithmetic that must hold."""
        return self.names - self.unmatched - self.merged == self.uniis


class CensusReport(NamedTuple):
    """Everything one census run measured."""

    identity: IdentityChain
    date_forms: tuple[tuple[str, str, int], ...]
    appno_kinds: tuple[tuple[str, int], ...]
    row_counts: tuple[tuple[str, int, int], ...]


def typed_applnos(data: RawData) -> tuple[frozenset[str], frozenset[str]]:
    """Every typed application number, and the NDA/BLA subset of them.

    Both are needed, and for different questions: the NDA/BLA set says which
    products to count, while the full set is what makes an application number
    in ``Products.txt`` *untypable* rather than merely generic.
    """
    known: set[str] = set()
    branded: set[str] = set()
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row["ApplType"], row["ApplNo"])
        if not parsed.is_application:
            continue
        known.add(parsed.digits)
        if parsed.app_type in BRANDED_TYPES:
            branded.add(parsed.digits)
    return frozenset(known), frozenset(branded)


def ingredient_names(data: RawData) -> tuple[set[str], tuple[str, ...]]:
    """The NDA/BLA ingredient components, and the ApplNos that cannot be typed.

    Untypable means *no ``Applications.txt`` row at all* — 8 of them. An ANDA
    is a different thing entirely: it is typed, and excluded deliberately
    because it is a generic copy of a drug already counted under its NDA. The
    two must not be reported as one number.
    """
    known, branded = typed_applnos(data)
    names: set[str] = set()
    untypable: set[str] = set()
    for row in read_rows(data.products):
        digits = appno.pad(row["ApplNo"])
        if digits not in known:
            untypable.add(digits)
            continue
        if digits not in branded:
            continue  # a typed ANDA: excluded by design, not a defect
        for part in row["ActiveIngredient"].split(INGREDIENT_SEPARATOR):
            cleaned = part.strip().upper()
            if cleaned:
                names.add(cleaned)
    return names, tuple(sorted(untypable))


def resolve_uniis(
    data: RawData, names: set[str]
) -> tuple[dict[str, set[str]], int]:
    """Map each wanted name to the UNIIs claiming it, streaming the registry.

    The registry is 1,050,022 rows; only hits are kept, so memory stays at the
    ~2,800 names we asked about rather than the whole file.
    """
    hits: dict[str, set[str]] = {}
    ambiguous = 0
    for row in read_rows(data.unii_names):
        key = row["NAME"].strip().upper()
        if key in names:
            hits.setdefault(key, set()).add(row["UNII"].strip())
    for key, uniis in hits.items():
        if len(uniis) > 1:
            ambiguous += 1
            _log.warning("name maps to %d UNIIs: %r", len(uniis), key)
    return hits, ambiguous


def _identity(data: RawData) -> IdentityChain:
    """Walk the chain from ingredient strings to distinct UNII."""
    names, untypable = ingredient_names(data)
    hits, ambiguous = resolve_uniis(data, names)

    resolved = len(hits)
    unmatched = len(names) - resolved

    # One UNII claimed by several of our names is the fan-in. Its size is how
    # many name strings collapse; the merge count is that minus one per group.
    per_unii: dict[str, set[str]] = {}
    for name, uniis in hits.items():
        for unii in uniis:
            per_unii.setdefault(unii, set()).add(name)
    groups = {u: ns for u, ns in per_unii.items() if len(ns) > 1}
    fan_in_names = sum(len(ns) for ns in groups.values())
    merged = fan_in_names - len(groups)

    largest = tuple(
        (unii, sorted(ns)[0], len(ns))
        for unii, ns in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:6]
    )

    return IdentityChain(
        names=len(names),
        unmatched=unmatched,
        resolved=resolved,
        fan_in_groups=len(groups),
        fan_in_names=fan_in_names,
        merged=merged,
        uniis=len(per_unii),
        untypable_applnos=untypable,
        ambiguous_names=ambiguous,
        largest_groups=largest,
    )


#: Each date column the census reads, with the direction its years run in.
#: PAST_ONLY for things that have happened, FUTURE_OK for expiries.
_DATE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("ob_products", "Approval_Date", fda_date.PAST_ONLY, "orangebook/products.txt"),
    ("ob_patent", "Patent_Expire_Date_Text", fda_date.FUTURE_OK, "orangebook/patent.txt"),
    ("ob_exclusivity", "Exclusivity_Date", fda_date.FUTURE_OK, "orangebook/exclusivity.txt"),
    ("submissions", "SubmissionStatusDate", fda_date.PAST_ONLY, "drugsatfda/Submissions.txt"),
)


def _date_census(data: RawData) -> tuple[tuple[str, str, int], ...]:
    """Count the shape of every value in each date column."""
    out: list[tuple[str, str, int]] = []
    for attr, column, direction, label in _DATE_FIELDS:
        spec = getattr(data, attr)
        counts: Counter[str] = Counter()
        for row in read_rows(spec):
            parsed = fda_date.parse(row[column], direction=direction)  # type: ignore[arg-type]
            counts[parsed.form] += 1
        for form in fda_date.DATE_FORMS:
            if counts[form]:
                out.append((f"{label}:{column}", form, counts[form]))
    return tuple(out)


def _appno_census(data: RawData) -> tuple[tuple[str, int], ...]:
    """How many ApplNo values across the raw tables are written cleanly.

    The raw tables hold bare six-digit numbers, so classifying them by
    :func:`~utility.application_number.normalize` would only ever answer
    "untyped" and tell us nothing. The useful question is **conformance**: does
    every value already sit at six digits with no surrounding whitespace, and
    which ones do not? Expected non-conforming: ``'19019 '`` and ``'21939 '``
    (trailing space, the two the openFDA JSON writes as 5 digits) and
    ``'00000 '`` (the all-zero sentinel).
    """
    counts: Counter[str] = Counter()
    for spec, column in (
        (data.applications, "ApplNo"),
        (data.products, "ApplNo"),
        (data.submissions, "ApplNo"),
    ):
        for row in read_rows(spec):
            raw = row[column]
            padded = appno.pad(raw)
            if not padded.strip("0"):
                counts["zero_sentinel"] += 1
            elif raw == padded:
                counts["already_six_digits"] += 1
            else:
                counts[f"needs_normalising: {raw!r}"] += 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _row_counts(data: RawData) -> tuple[tuple[str, int, int], ...]:
    """Actual versus declared row count, so a bad download is visible."""
    out: list[tuple[str, int, int]] = []
    for attr, label in (
        ("applications", "Applications.txt"),
        ("products", "Products.txt"),
        ("ob_products", "orangebook/products.txt"),
        ("ob_patent", "orangebook/patent.txt"),
        ("ob_exclusivity", "orangebook/exclusivity.txt"),
    ):
        spec = getattr(data, attr)
        out.append((label, sum(1 for _ in read_rows(spec)), spec.rows))
    return tuple(out)


def run(data: RawData) -> CensusReport:
    """Measure everything, reading each file once."""
    return CensusReport(
        identity=_identity(data),
        date_forms=_date_census(data),
        appno_kinds=_appno_census(data),
        row_counts=_row_counts(data),
    )


def render(report: CensusReport) -> str:
    """The report as text. Every number it holds appears here."""
    c = report.identity
    lines = [
        "A. DRUG IDENTITY",
        f"  NDA/BLA ingredient names (split on {INGREDIENT_SEPARATOR!r})  {c.names:>9,}",
        f"    matched into UNII_Names                    {c.resolved:>9,}",
        f"    unmatched                                  {c.unmatched:>9,}",
        f"  fan-in groups                                {c.fan_in_groups:>9,}"
        f"   over {c.fan_in_names:,} names, merging {c.merged:,}",
        f"  distinct UNII (the drug key)                 {c.uniis:>9,}",
        f"    {c.names:,} - {c.unmatched:,} - {c.merged:,} = {c.uniis:,}"
        f"   {'OK' if c.reconciles else 'MISMATCH'}",
        f"  names mapping to >1 UNII                     {c.ambiguous_names:>9,}",
        f"  untypable ApplNos in Products.txt            {len(c.untypable_applnos):>9,}",
    ]
    if c.largest_groups:
        lines.append("  largest fan-in groups:")
        for unii, name, size in c.largest_groups:
            lines.append(f"    {unii}  {name:<28} {size:>3} names")

    lines.append("")
    lines.append("B. DATE SHAPES")
    for label, form, count in report.date_forms:
        lines.append(f"  {label:<44} {form:<14} {count:>9,}")

    lines.append("")
    lines.append("C. APPLICATION-NUMBER CONFORMANCE (raw-table ApplNo values)")
    for kind, count in report.appno_kinds:
        lines.append(f"  {kind:<34} {count:>9,}")

    lines.append("")
    lines.append("D. ROW COUNTS (actual vs declared)")
    for label, actual, declared in report.row_counts:
        flag = "OK" if actual == declared else "MISMATCH"
        lines.append(f"  {label:<36} {actual:>9,}  {declared:>9,}  {flag}")

    return "\n".join(lines)


__all__ = [
    "BRANDED_TYPES",
    "INGREDIENT_SEPARATOR",
    "CensusReport",
    "IdentityChain",
    "ingredient_names",
    "render",
    "resolve_uniis",
    "run",
    "typed_applnos",
]
