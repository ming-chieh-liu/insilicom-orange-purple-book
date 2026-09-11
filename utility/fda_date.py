"""Turn every date shape the FDA corpus uses into a ``date``, or say why not.

The FDA writes dates four ways across its own files, and writes four things in
date columns that are not dates at all. Measured over ~700,000 values in
``fda_raw/`` on 2026-09-11:

===============  ==========================================================
``yyyymmdd``     ``19940613``. openFDA JSON throughout: ``drugsfda``
                 submission dates 188,292 · ``orangebook`` approval dates
                 42,791 · ``ndc`` marketing dates 137,804 top-level and
                 217,794 in ``packaging[]`` · every ``label`` effective time.
``sql_midnight`` ``1969-07-16 00:00:00``. The Drugs@FDA raw tables:
                 ``Submissions.txt`` 193,678 · ``ApplicationDocs.txt``
                 81,038. The time is always exactly midnight; a real
                 timestamp would not be a date column.
``mon_d_yyyy``   ``Dec 17, 1996``. The Orange Book text files:
                 ``products.txt`` 42,791 · ``patent.txt`` 22,205 ·
                 ``exclusivity.txt`` 2,370. **The day is not zero-padded**
                 (``Oct 7, 2014``), so a fixed-width parse fails.
``d_mon_yy``     ``7-Mar-25``. The Purple Book's seven date columns (3,044
                 values) and ``ndc`` ``packaging[]`` in upper case
                 (``10-JUL-87``, 39,450). Same shape, different casing —
                 one case-folded month table serves both.
===============  ==========================================================

And the four non-dates, each of which means something different and none of
which may be silently turned into a date or into ``None``:

``pre_1982``
    The literal ``Approved Prior to Jan 1, 1982``, in the Orange Book's
    approval column on **5,873 of 48,664 rows (12.07%)**. It states that an
    approval exists and that its date is unknown. We return no date and set
    :attr:`FdaDate.approved_pre_1982`, inventing nothing — and the FDA agrees
    with that reading: its own ``orangebook`` JSON omits ``approval_date``
    entirely on exactly those 5,873 rows.
``tbd``
    The literal ``Date TBD``, 3 rows of the Purple Book's first-interchangeable
    exclusivity column. A date that will exist and does not yet.
``absent``
    Empty, or the Purple Book's **single-space placeholder** ``' '`` (968 rows
    of one column alone). 9 ``Submissions.txt`` rows and 15 Purple Book
    approval dates are simply blank. Absence is a normal, documented state of
    these columns, so it is **not** logged.
``unparsed``
    Anything else. **Zero values in the corpus land here today**, which is why
    one is worth a warning: it means either a new snapshot shape or a bug.

**The two-digit year is the reason this module exists.**

``13-Feb-36`` is **1936** — BLA 101062, Antivenin (Latrodectus mactans), one of
the oldest licences on file. But the Purple Book's reference-product exclusivity
column also holds ``-36`` meaning **2036**. The same two characters are two
different centuries depending on which column they sit in, so there is no single
right answer and therefore **no default**: the caller declares
:data:`PAST_ONLY` or :data:`FUTURE_OK` per field. Python's ``%y`` would answer
2036 for both, which is why :func:`parse` does not use ``strptime`` at all.

The pivot is resolved against :data:`SNAPSHOT_YEAR`, a pinned property of the
data on disk, **not** against the clock — otherwise the meaning of a stored date
would change on 1 January and a test asserting ``27 -> 1927`` would start
failing on 2027-01-01.

One further caveat, stated because it is not obvious: ``direction`` is consulted
**only** for the ``d_mon_yy`` shape. The other three carry four-digit years, so
for them the argument is accepted and provably ignored. It is still required,
because a default would be wrong for exactly the field where it matters.

Decided 2026-09-10; measured 2026-09-11.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final, Literal, NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: Which century a two-digit year belongs to. Declared by the caller per field.
YearDirection = Literal["past_only", "future_ok"]

#: Approval, licensure, submission-status and document dates: the event has
#: already happened, so the year cannot be after the snapshot.
PAST_ONLY: Final = "past_only"

#: Patent, exclusivity and interchangeability expiries: the year is routinely in
#: the future. Pivots at 69, which is what ``%y`` and POSIX already mean.
FUTURE_OK: Final = "future_ok"

#: The corpus snapshot's year: ``purplebook-2026-08``, ``UNII_Names_4Aug2026``,
#: submissions through 2026-06-26. :data:`PAST_ONLY` is resolved against this
#: rather than ``date.today()`` so a parsed date never changes meaning and no
#: test flakes on New Year. Bump it when a newer snapshot lands.
SNAPSHOT_YEAR: Final = 2026

#: The Orange Book's stand-in for an approval whose date predates the file.
PRE_1982_TEXT: Final = "Approved Prior to Jan 1, 1982"

#: The Purple Book's stand-in for an exclusivity date not yet determined.
TBD_TEXT: Final = "Date TBD"

#: 1,842 ``ApplicationDocs`` rows, 1 ``Submissions`` row and 2 ``ndc`` rows
#: write this. The next-earliest ``ApplicationDocs`` date is 1955 — a 55-year
#: gap — so it is a null written as a date. It is returned as stated and
#: flagged, never dropped: only the caller knows whether it wants the literal.
EPOCH_SENTINEL: Final = date(1900, 1, 1)

#: Month abbreviation -> number, keyed case-folded so ``Mar``, ``MAR`` and
#: ``mar`` all resolve. ``strptime``'s ``%b`` is locale-dependent; this is not.
_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

#: The four shapes, as anchored patterns. Anchored so a longer string
#: containing a date is rejected rather than partially read.
_YYYYMMDD = re.compile(r"(\d{4})(\d{2})(\d{2})")
_SQL_MIDNIGHT = re.compile(r"(\d{4})-(\d{2})-(\d{2}) 00:00:00")
_MON_D_YYYY = re.compile(r"([A-Za-z]{3}) (\d{1,2}), (\d{4})")
_D_MON_YY = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})")

#: Every value :attr:`FdaDate.form` can take. The first four resolve a date;
#: the last four do not.
DATE_FORMS: tuple[str, ...] = (
    "yyyymmdd",
    "sql_midnight",
    "mon_d_yyyy",
    "d_mon_yy",
    "pre_1982",
    "tbd",
    "absent",
    "unparsed",
)


class FdaDate(NamedTuple):
    """A parsed FDA date, and which of the corpus's shapes it came in.

    Always returned — never ``None`` — because ``None`` alone cannot tell
    *absent* from *pre-1982* from *TBD* from *unparseable*, and those four call
    for four different things downstream.
    """

    value: date | None
    form: str

    @property
    def approved_pre_1982(self) -> bool:
        """The source said :data:`PRE_1982_TEXT` and gave no date.

        >>> parse("Approved Prior to Jan 1, 1982", direction=PAST_ONLY).approved_pre_1982
        True
        >>> parse("Dec 17, 1996", direction=PAST_ONLY).approved_pre_1982
        False
        """
        return self.form == "pre_1982"

    @property
    def is_epoch_sentinel(self) -> bool:
        """The source wrote ``1900-01-01``, which this corpus uses as a null.

        >>> parse("1900-01-01 00:00:00", direction=PAST_ONLY).is_epoch_sentinel
        True
        >>> parse("1955-03-01 00:00:00", direction=PAST_ONLY).is_epoch_sentinel
        False
        """
        return self.value == EPOCH_SENTINEL


def resolve_two_digit_year(
    year: int,
    *,
    direction: YearDirection,
    reference_year: int = SNAPSHOT_YEAR,
) -> int:
    """The four-digit year two digits name, given which way time runs.

    :data:`PAST_ONLY` never returns a year after ``reference_year``;
    :data:`FUTURE_OK` pivots at 69.

    >>> resolve_two_digit_year(36, direction=PAST_ONLY)
    1936
    >>> resolve_two_digit_year(36, direction=FUTURE_OK)
    2036
    >>> resolve_two_digit_year(26, direction=PAST_ONLY)
    2026
    >>> resolve_two_digit_year(99, direction=FUTURE_OK)
    1999
    """
    if direction == FUTURE_OK:
        return 1900 + year if year >= 69 else 2000 + year
    if direction == PAST_ONLY:
        candidate = (reference_year // 100) * 100 + year
        return candidate - 100 if candidate > reference_year else candidate
    raise ValueError(f"direction must be {PAST_ONLY!r} or {FUTURE_OK!r}, not {direction!r}")


def _build(year: int, month: int, day: int, form: str, raw: str) -> FdaDate:
    """A date, or ``unparsed`` if those numbers name no real day."""
    try:
        return FdaDate(date(year, month, day), form)
    except ValueError:
        _log.warning("not a real date, skipped: %r", raw)
        return FdaDate(None, "unparsed")


def parse(
    raw: str | None,
    *,
    direction: YearDirection,
    reference_year: int = SNAPSHOT_YEAR,
) -> FdaDate:
    """The date a field states, and the shape it stated it in.

    ``direction`` is required and keyword-only: see the module docstring for
    why there is no default, and note it is consulted only for ``d_mon_yy``.

    >>> parse("19940613", direction=PAST_ONLY)
    FdaDate(value=datetime.date(1994, 6, 13), form='yyyymmdd')
    >>> parse("1969-07-16 00:00:00", direction=PAST_ONLY).value
    datetime.date(1969, 7, 16)
    >>> parse("Oct 7, 2014", direction=PAST_ONLY).value
    datetime.date(2014, 10, 7)
    >>> parse("10-JUL-87", direction=PAST_ONLY).value
    datetime.date(1987, 7, 10)
    >>> parse("13-Feb-36", direction=PAST_ONLY).value.year
    1936
    >>> parse("13-Feb-36", direction=FUTURE_OK).value.year
    2036
    >>> parse(None, direction=PAST_ONLY)
    FdaDate(value=None, form='absent')
    >>> parse("Approved Prior to Jan 1, 1982", direction=PAST_ONLY)
    FdaDate(value=None, form='pre_1982')
    """
    if direction not in (PAST_ONLY, FUTURE_OK):
        raise ValueError(f"direction must be {PAST_ONLY!r} or {FUTURE_OK!r}, not {direction!r}")

    if raw is None:
        return FdaDate(None, "absent")

    # The Purple Book writes ' ' for null, so strip before testing emptiness.
    text = raw.strip()
    if not text:
        return FdaDate(None, "absent")

    # The two declared non-dates, matched before any shape is tried.
    if text == PRE_1982_TEXT:
        return FdaDate(None, "pre_1982")
    if text == TBD_TEXT:
        return FdaDate(None, "tbd")

    match = _YYYYMMDD.fullmatch(text)
    if match:
        y, m, d = match.groups()
        return _build(int(y), int(m), int(d), "yyyymmdd", raw)

    match = _SQL_MIDNIGHT.fullmatch(text)
    if match:
        y, m, d = match.groups()
        return _build(int(y), int(m), int(d), "sql_midnight", raw)

    match = _MON_D_YYYY.fullmatch(text)
    if match:
        mon, d, y = match.groups()
        month = _MONTHS.get(mon.lower())
        if month is None:
            _log.warning("unknown month abbreviation, skipped: %r", raw)
            return FdaDate(None, "unparsed")
        return _build(int(y), month, int(d), "mon_d_yyyy", raw)

    match = _D_MON_YY.fullmatch(text)
    if match:
        d, mon, yy = match.groups()
        month = _MONTHS.get(mon.lower())
        if month is None:
            _log.warning("unknown month abbreviation, skipped: %r", raw)
            return FdaDate(None, "unparsed")
        year = resolve_two_digit_year(
            int(yy), direction=direction, reference_year=reference_year
        )
        return _build(year, month, int(d), "d_mon_yy", raw)

    _log.warning("no known date shape, skipped: %r", raw)
    return FdaDate(None, "unparsed")


def is_recognized(raw: str | None) -> bool:
    """Whether the corpus declares this shape at all.

    Shape is a direction-free question, so this needs no ``direction``. An
    absent value is *recognised* — the columns really are blank sometimes.

    >>> is_recognized("Dec 17, 1996")
    True
    >>> is_recognized("Approved Prior to Jan 1, 1982")
    True
    >>> is_recognized("")
    True
    >>> is_recognized("16/07/1969")
    False
    """
    return parse(raw, direction=PAST_ONLY).form != "unparsed"


__all__ = [
    "DATE_FORMS",
    "EPOCH_SENTINEL",
    "FUTURE_OK",
    "FdaDate",
    "PAST_ONLY",
    "PRE_1982_TEXT",
    "SNAPSHOT_YEAR",
    "TBD_TEXT",
    "YearDirection",
    "is_recognized",
    "parse",
    "resolve_two_digit_year",
]
