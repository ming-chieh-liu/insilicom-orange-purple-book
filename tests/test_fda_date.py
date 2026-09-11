"""Tests for every date shape the FDA corpus uses, and the four non-dates."""

from __future__ import annotations

import logging
from datetime import date

import pytest

from utility import fda_date
from utility.fda_date import FUTURE_OK, PAST_ONLY


@pytest.mark.parametrize(
    ("raw", "form", "expected"),
    [
        # yyyymmdd — openFDA JSON throughout, ~580,000 values.
        ("19940613", "yyyymmdd", date(1994, 6, 13)),
        ("20140904", "yyyymmdd", date(2014, 9, 4)),
        ("19390101", "yyyymmdd", date(1939, 1, 1)),
        # sql_midnight — the Drugs@FDA raw tables, 274,716 values.
        ("1969-07-16 00:00:00", "sql_midnight", date(1969, 7, 16)),
        ("2003-07-28 00:00:00", "sql_midnight", date(2003, 7, 28)),
        # mon_d_yyyy — the Orange Book text files, 67,366 values.
        ("Dec 17, 1996", "mon_d_yyyy", date(1996, 12, 17)),
        ("Apr 12, 2023", "mon_d_yyyy", date(2023, 4, 12)),
        ("Oct 7, 2014", "mon_d_yyyy", date(2014, 10, 7)),  # day not zero-padded
        ("Feb 1, 2027", "mon_d_yyyy", date(2027, 2, 1)),
        # d_mon_yy — Purple Book, and ndc packaging[] in upper case.
        ("7-Mar-25", "d_mon_yy", date(2025, 3, 7)),
        ("23-Jul-86", "d_mon_yy", date(1986, 7, 23)),
        ("10-JUL-87", "d_mon_yy", date(1987, 7, 10)),
        ("26-MAY-15", "d_mon_yy", date(2015, 5, 26)),
    ],
)
def test_each_of_the_four_shapes_in_the_corpus_parses(
    raw: str, form: str, expected: date
) -> None:
    """The four shapes, measured over ~700,000 values in fda_raw/."""
    result = fda_date.parse(raw, direction=PAST_ONLY)
    assert result.form == form
    assert result.value == expected


def test_the_pre_1982_text_yields_no_date_and_says_so() -> None:
    """5,873 of 48,664 Orange Book rows (12.07%) state this instead of a date.

    The FDA reads it the same way: its own orangebook JSON omits approval_date
    on exactly those 5,873 rows rather than inventing one.
    """
    result = fda_date.parse(fda_date.PRE_1982_TEXT, direction=PAST_ONLY)
    assert result.value is None
    assert result.form == "pre_1982"
    assert result.approved_pre_1982 is True


@pytest.mark.parametrize(
    "raw",
    ["19940613", "1969-07-16 00:00:00", "Dec 17, 1996", "7-Mar-25", "", None, "Date TBD", "junk"],
)
def test_nothing_else_sets_the_pre_1982_flag(raw: str | None) -> None:
    """The flag means one specific sentence, not "no date"."""
    assert fda_date.parse(raw, direction=PAST_ONLY).approved_pre_1982 is False


@pytest.mark.parametrize(
    ("yy", "expected"),
    [(36, 1936), (26, 2026), (27, 1927), (99, 1999), (0, 2000), (30, 1930), (68, 1968)],
)
def test_a_past_only_two_digit_year_never_lands_in_the_future(
    yy: int, expected: int
) -> None:
    """13-Feb-36 is 1936 — BLA 101062, Antivenin, one of the oldest licences."""
    assert (
        fda_date.resolve_two_digit_year(yy, direction=PAST_ONLY, reference_year=2026)
        == expected
    )


@pytest.mark.parametrize(
    ("yy", "expected"),
    [(36, 2036), (33, 2033), (68, 2068), (69, 1969), (99, 1999), (0, 2000), (26, 2026)],
)
def test_a_future_ok_two_digit_year_pivots_at_sixty_nine(yy: int, expected: int) -> None:
    """Expiry columns hold real future years; 69 is what %y and POSIX mean."""
    assert (
        fda_date.resolve_two_digit_year(yy, direction=FUTURE_OK, reference_year=2026)
        == expected
    )


def test_the_same_two_digits_are_two_different_years_by_direction() -> None:
    """The whole reason `direction` has no default, in one assertion.

    Purple Book approval date `13-Feb-36` is 1936; its reference-product
    exclusivity column holds `-36` meaning 2036. Python's %y answers 2036 to
    both, which is the bug this design exists to prevent.
    """
    past = fda_date.parse("13-Feb-36", direction=PAST_ONLY)
    future = fda_date.parse("13-Feb-36", direction=FUTURE_OK)
    assert past.value is not None and future.value is not None
    assert past.value.year == 1936
    assert future.value.year == 2036


def test_the_pivot_does_not_move_when_the_clock_does() -> None:
    """Pinned to SNAPSHOT_YEAR, so no stored date changes meaning on 1 January."""
    assert fda_date.resolve_two_digit_year(27, direction=PAST_ONLY, reference_year=2026) == 1927
    assert fda_date.resolve_two_digit_year(27, direction=PAST_ONLY, reference_year=2027) == 2027
    assert fda_date.resolve_two_digit_year(28, direction=PAST_ONLY, reference_year=2027) == 1928
    # The default is the snapshot, not today.
    assert fda_date.SNAPSHOT_YEAR == 2026


@pytest.mark.parametrize("raw", [None, "", " ", "  ", "\t", "\n"])
def test_absence_is_absence_and_not_a_date(
    raw: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """The Purple Book writes ' ' for null — 968 rows of one column alone.

    9 Submissions.txt rows and 15 Purple Book approval dates are simply blank.
    A blank column is a documented state, so it must not warn.
    """
    with caplog.at_level(logging.WARNING):
        result = fda_date.parse(raw, direction=PAST_ONLY)
    assert result == fda_date.FdaDate(None, "absent")
    assert caplog.records == []


def test_date_tbd_is_a_declared_non_date() -> None:
    """3 rows of the Purple Book's first-interchangeable exclusivity column."""
    result = fda_date.parse(fda_date.TBD_TEXT, direction=FUTURE_OK)
    assert result == fda_date.FdaDate(None, "tbd")


def test_the_1900_sentinel_is_returned_as_written_and_flagged() -> None:
    """1,842 ApplicationDocs rows, 1 Submissions row and 2 ndc rows write it.

    The next-earliest ApplicationDocs date is 1955 — a 55-year gap — so it is a
    null written as a date. Only the caller knows whether it wants the literal,
    so the value is preserved and the flag carries the judgement.
    """
    result = fda_date.parse("1900-01-01 00:00:00", direction=PAST_ONLY)
    assert result.value == fda_date.EPOCH_SENTINEL
    assert result.form == "sql_midnight"
    assert result.is_epoch_sentinel is True
    assert fda_date.parse("19000101", direction=PAST_ONLY).is_epoch_sentinel is True
    assert fda_date.parse("1955-03-01 00:00:00", direction=PAST_ONLY).is_epoch_sentinel is False


@pytest.mark.parametrize(
    "raw",
    ["20261301", "20260231", "Feb 30, 1996", "32-Jan-25", "20260000", "Dec 32, 1996"],
)
def test_an_impossible_date_is_refused_not_rounded(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A shape that matches but names no real day is a defect, not a date."""
    with caplog.at_level(logging.WARNING):
        result = fda_date.parse(raw, direction=PAST_ONLY)
    assert result == fda_date.FdaDate(None, "unparsed")
    assert len(caplog.records) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "1969-07-16 13:45:00",  # a real timestamp is not a date column
        "1969-07-16",  # ISO with no time is not a shape the corpus uses
        "16/07/1969",
        "Xyz 1, 2000",  # matches mon_d_yyyy's shape, unknown month
        "1-Xyz-25",  # matches d_mon_yy's shape, unknown month
        "Jan 1 2000",  # no comma
        "7 Mar 25",  # spaces, not hyphens
        "2014-09",
        "NDA019019",
    ],
)
def test_a_shape_the_corpus_does_not_use_is_refused(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Anchored patterns, so a longer string is rejected, not partially read."""
    with caplog.at_level(logging.WARNING):
        result = fda_date.parse(raw, direction=PAST_ONLY)
    assert result == fda_date.FdaDate(None, "unparsed")
    assert len(caplog.records) == 1


@pytest.mark.parametrize(
    ("raw", "recognized"),
    [
        ("Dec 17, 1996", True),
        ("19940613", True),
        ("Approved Prior to Jan 1, 1982", True),
        ("Date TBD", True),
        ("", True),
        (None, True),
        ("16/07/1969", False),
        ("Xyz 1, 2000", False),
    ],
)
def test_is_recognized_asks_about_shape_not_about_direction(
    raw: str | None, recognized: bool
) -> None:
    """Shape is a direction-free question, so the helper takes no direction."""
    assert fda_date.is_recognized(raw) is recognized


def test_a_direction_the_module_does_not_define_is_a_call_bug() -> None:
    """mypy rejects this statically; the raise covers a dynamic call site.

    A defect in the data is a value. A defect in the call is an exception.
    """
    with pytest.raises(ValueError, match="direction must be"):
        fda_date.parse("7-Mar-25", direction="tomorrow")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="direction must be"):
        fda_date.resolve_two_digit_year(25, direction="past-only")  # type: ignore[arg-type]


def test_every_form_the_parser_can_return_is_declared() -> None:
    """DATE_FORMS is the closed set a consumer may switch on."""
    produced = {
        fda_date.parse(raw, direction=PAST_ONLY).form
        for raw in [
            "19940613",
            "1969-07-16 00:00:00",
            "Dec 17, 1996",
            "7-Mar-25",
            fda_date.PRE_1982_TEXT,
            fda_date.TBD_TEXT,
            "",
            "16/07/1969",
        ]
    }
    assert produced == set(fda_date.DATE_FORMS)
    assert len(fda_date.DATE_FORMS) == len(set(fda_date.DATE_FORMS))
