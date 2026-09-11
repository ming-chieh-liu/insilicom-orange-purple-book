"""Tests for canonicalising and classifying FDA application numbers."""

from __future__ import annotations

import logging

import pytest

from utility import application_number as appno


@pytest.mark.parametrize(
    ("raw", "app_type", "digits"),
    [
        ("NDA019019", "NDA", "019019"),
        ("BLA125514", "BLA", "125514"),
        ("ANDA077089", "ANDA", "077089"),
        ("NDA000004", "NDA", "000004"),
        ("anda077089", "ANDA", "077089"),
        ("  BLA125514  ", "BLA", "125514"),
        ("nda019019", "NDA", "019019"),
    ],
)
def test_the_canonical_form_is_the_type_then_six_digits(
    raw: str, app_type: str, digits: str
) -> None:
    """Prefix is upper-cased, value stripped, digits padded to six."""
    result = appno.normalize(raw)
    assert result.kind == appno.APPLICATION
    assert result.app_type == app_type
    assert result.digits == digits
    assert result.canonical == f"{app_type}{digits}"
    assert result.is_application is True


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [("NDA19019", "NDA019019"), ("NDA21939", "NDA021939")],
)
def test_the_two_unpadded_json_records_pad_to_six(raw: str, canonical: str) -> None:
    """Exactly 2 of the 29,322 drugsfda JSON records are written 5 digits."""
    assert appno.normalize(raw).canonical == canonical


@pytest.mark.parametrize("number", ["19019 ", "21939 "])
def test_the_trailing_space_applnos_are_the_same_two_applications(number: str) -> None:
    """The cross-source guarantee the canonical form exists for.

    Applications.txt writes these two as 5 digits plus a trailing space; the
    openFDA JSON writes them as 5 digits with a prefix. Both must resolve to
    the same key, or the two files cannot be joined on those rows.
    """
    from_table = appno.from_parts("NDA", number)
    from_json = appno.normalize(f"NDA{number.strip()}")
    assert from_table.canonical == from_json.canonical
    assert from_table == from_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19019 ", "019019"),
        ("000004", "000004"),
        ("4", "000004"),
        (" 077089", "077089"),
        ("761399", "761399"),
        ("00000 ", "000000"),
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_pad_is_strip_then_lstrip_zeros_then_zfill(raw: str | None, expected: str) -> None:
    """The one normalisation every source needs, exported so it is pinned."""
    assert appno.pad(raw) == expected


@pytest.mark.parametrize(
    ("letter", "number", "canonical"),
    [
        ("N", "019019", "NDA019019"),
        ("A", "077089", "ANDA077089"),
        ("N", "020702", "NDA020702"),
        ("a", "077089", "ANDA077089"),
    ],
)
def test_an_orange_book_letter_becomes_the_word_drugs_at_fda_uses(
    letter: str, number: str, canonical: str
) -> None:
    """products.txt holds only A (37,795) and N (10,869) over 48,664 rows."""
    assert appno.from_parts(letter, number).canonical == canonical


def test_the_purple_book_type_is_supplied_by_the_caller_not_inferred() -> None:
    """Of its 860 BLA numbers, Drugs@FDA types 460 BLA, 400 absent, 0 otherwise.

    So T5 may pass "BLA" explicitly; the module itself infers nothing, and the
    same number with no type stays untyped.
    """
    assert appno.from_parts("BLA", "101062").canonical == "BLA101062"
    assert appno.normalize("101062").kind == appno.UNTYPED
    assert appno.normalize("101062").canonical == ""


@pytest.mark.parametrize("letter", ["B", "X", "NDB", "ANDB", "1"])
def test_a_type_the_files_never_state_is_refused_not_guessed(
    letter: str, caplog: pytest.LogCaptureFixture
) -> None:
    """There is no B in the Orange Book: it lists no biologics at all."""
    with caplog.at_level(logging.WARNING):
        result = appno.from_parts(letter, "101062")
    assert result.kind == appno.UNRECOGNIZED
    assert result.canonical == ""
    assert len(caplog.records) == 1


@pytest.mark.parametrize("raw", ["019019", "040850", "352", "032", "1"])
def test_a_bare_number_states_no_type_and_mints_no_key(raw: str) -> None:
    """The 8 untypable Products.txt ApplNos, and 54 bare ndc values.

    digits is still populated so a caller with outside knowledge can act, but
    canonical is empty because no key can be minted without a type. Inferring
    "monograph" for 352 would invent a type exactly as much as inferring NDA.
    """
    result = appno.normalize(raw)
    assert result.kind == appno.UNTYPED
    assert result.digits == appno.pad(raw)
    assert result.canonical == ""
    assert result.is_application is False


def test_an_untypable_applno_from_two_columns_is_also_untyped() -> None:
    """from_parts with no type is the 8-row Products.txt case."""
    result = appno.from_parts(None, "040850")
    assert result.kind == appno.UNTYPED
    assert result.digits == "040850"
    assert result.canonical == ""
    assert appno.from_parts("", "040850").kind == appno.UNTYPED
    assert appno.from_parts("   ", "040850").kind == appno.UNTYPED


@pytest.mark.parametrize(
    "raw", ["00000 ", "000000", "NDA000000", "0", "00", "BLA000000", "anda000000"]
)
def test_all_zero_digits_are_a_sentinel_not_application_zero(raw: str) -> None:
    """Join_Submission_ActionTypes_Lookup.txt has 3 rows of '00000 '.

    Run naively through strip/lstrip/zfill that becomes NDA000000, an
    application that does not exist.
    """
    result = appno.normalize(raw)
    assert result.kind == appno.ZERO_SENTINEL
    assert result.canonical == ""
    assert result.digits == ""


def test_all_zero_digits_from_two_columns_are_also_a_sentinel() -> None:
    assert appno.from_parts("NDA", "00000 ").kind == appno.ZERO_SENTINEL
    assert appno.from_parts("N", "000000").kind == appno.ZERO_SENTINEL


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        # otc_monograph — 32,393 ndc rows including 112 bare M
        ("M020", appno.OTC_MONOGRAPH),
        ("M012", appno.OTC_MONOGRAPH),
        ("M017", appno.OTC_MONOGRAPH),
        ("m021", appno.OTC_MONOGRAPH),
        ("M020.11", appno.OTC_MONOGRAPH),
        ("M", appno.OTC_MONOGRAPH),
        # section_505g — 3,499 rows; a statute, not an application
        ("505G(a)(3)", appno.SECTION_505G),
        ("505g(A)(3)", appno.SECTION_505G),
        # monograph_part — 451 rows
        ("part341", appno.MONOGRAPH_PART),
        ("part333A", appno.MONOGRAPH_PART),
        ("PART341", appno.MONOGRAPH_PART),
        # other_prefixed — 95 rows over four prefixes
        ("BN820915", appno.OTHER_PREFIXED),
        ("BA110057", appno.OTHER_PREFIXED),
        ("EUA000122", appno.OTHER_PREFIXED),
        ("C200263", appno.OTHER_PREFIXED),
    ],
)
def test_an_ndc_value_that_is_not_an_application_is_named_not_failed(
    raw: str, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    """54% of the ndc file is not an application; that is the field, not a bug.

    Naming each kind turns an alarming "54% failed" into a reconciliation, and
    these are normal documented states, so none of them warns.
    """
    with caplog.at_level(logging.WARNING):
        result = appno.normalize(raw)
    assert result.kind == kind
    assert result.canonical == ""
    assert result.is_application is False
    assert caplog.records == []


@pytest.mark.parametrize(
    "raw",
    [
        "M20", "M505G(a)(3)", "505(a)(3)", "M016B", "M12", "333C", "333B", "MO12",
        "505G(a)(30", "M0012", "505G(a)(c)", "ok505G(a)(3)", "M017B", "505G(3)(a)",
        "M0017", "MO17", "MO19", "OKM016", "505G9a)(3)", "505G(a)(3", "505G(a)(3)(A)",
        "OKM020", "MO18",
    ],
)
def test_a_source_typo_is_reported_not_repaired(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The 23 distinct unrecognized strings, 54 rows, 0.04% of the ndc file.

    Few enough that one warning each is a readable log. Guessing what MO12
    meant would put an invented application number into a join.
    """
    with caplog.at_level(logging.WARNING):
        result = appno.normalize(raw)
    assert result.kind == appno.UNRECOGNIZED
    assert result.canonical == ""
    assert len(caplog.records) == 1


@pytest.mark.parametrize("raw", [None, "", " ", "   ", "\t"])
def test_absence_is_silent(raw: str | None, caplog: pytest.LogCaptureFixture) -> None:
    """38,074 of 137,804 ndc records have no application number at all.

    A documented normal state; 38,074 warnings is a log nobody reads.
    """
    with caplog.at_level(logging.WARNING):
        result = appno.normalize(raw)
    assert result.kind == appno.ABSENT
    assert caplog.records == []
    assert appno.from_parts("NDA", raw).kind == appno.ABSENT


@pytest.mark.parametrize("raw", ["NDA1234567", "1234567", "BLA12345678"])
def test_more_than_six_digits_is_refused(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Never observed, and must not silently mint a 10-character key."""
    with caplog.at_level(logging.WARNING):
        assert appno.normalize(raw).kind == appno.UNRECOGNIZED
    assert len(caplog.records) == 1


def test_is_application_agrees_with_the_kind() -> None:
    assert appno.is_application("BLA125514") is True
    assert appno.is_application("NDA19019") is True
    assert appno.is_application("505G(a)(3)") is False
    assert appno.is_application("019019") is False
    assert appno.is_application(None) is False


def test_every_kind_the_module_can_return_is_declared() -> None:
    """KINDS is the closed set a consumer may switch on."""
    produced = {
        appno.normalize(raw).kind
        for raw in ["NDA019019", None, "M020", "505G(a)(3)", "part341",
                    "BN820915", "019019", "MO12", "000000"]
    }
    assert produced == set(appno.KINDS)
    assert len(appno.KINDS) == len(set(appno.KINDS))


def test_the_type_tables_hold_only_what_the_files_state() -> None:
    """No B in TYPE_LETTERS; the Orange Book lists no biologics."""
    assert set(appno.TYPE_LETTERS) == {"A", "N"}
    assert set(appno.CANONICAL_TYPES) == {"NDA", "BLA", "ANDA"}
    assert set(appno.TYPE_LETTERS.values()) <= set(appno.CANONICAL_TYPES)
