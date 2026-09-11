"""Tests for the drug-identity census.

Two halves. The synthetic half builds a miniature ``fda_raw/`` in ``tmp_path``
and asserts the whole chain on numbers small enough to verify by hand — it
carries the coverage and runs in milliseconds with no corpus present. The
``realdata`` half asserts the corpus-wide figures that ``docs/drug-identity.md``
quotes, and is the actual proof.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from config.settings import RawData
from service import identity_census
from utility import fda_date

# --- the synthetic tree -----------------------------------------------------

_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "000001\tNDA\t\tACME",
    "000002\tBLA\t\tBIOCO",
    "000003\tANDA\t\tGENERICO",
    # A trailing space, as Applications.txt really has on two rows.
    "00005 \tNDA\t\tSPACEY",
    # The all-zero sentinel, as Join_Submission_ActionTypes_Lookup.txt has.
    "00000 \tNDA\t\tZEROCO",
    # A type no FDA file states: refused rather than guessed.
    "000006\tXX\t\tBADTYPE",
]

_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient\tReferenceStandard",
    "000001\t001\tTABLET;ORAL\t10MG\t0\tBRANDA\tASPIRIN\t0",
    # A ';'-separated combination, and an unmatchable name.
    "000002\t001\tINJECTION\t60MG\t0\tBRANDB\tDENOSUMAB; NOTAREALDRUG\t0",
    # An ANDA: typed, and excluded by design rather than as a defect.
    "000003\t001\tTABLET;ORAL\t200MG\t0\tGENB\tIBUPROFEN\t0",
    # The trailing-space application, with two ingredients sharing one UNII.
    "000005\t001\tTABLET;ORAL\t10MG\t0\tBRANDC\tATORVASTATIN CALCIUM; EZETIMIBE\t0",
    # No Applications.txt row at all: untypable.
    "000099\t001\tTABLET;ORAL\t5MG\t0\tORPHAN\tMYSTERY\t0",
    # An empty ingredient, as one real Products.txt row has.
    "000001\t002\tSOLUTION\t1%\t0\tBRANDA\t\t0",
]

_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    "000001\t19\tORIG\t1\tAP\t1969-07-16 00:00:00\t\tUNKNOWN",
    "000002\t3\tSUPPL\t10\tAP\t2014-09-04 00:00:00\t\t",
    # A blank date, as 9 real rows have.
    "000003\t1\tORIG\t1\tAP\t\t\t",
]

# 14 fields, tilde-delimited, exactly as orangebook/x/products.txt.
_OB_PRODUCTS = [
    "Ingredient~DF;Route~Trade_Name~Applicant~Strength~Appl_Type~Appl_No~Product_No"
    "~TE_Code~Approval_Date~RLD~RS~Type~Applicant_Full_Name",
    "ASPIRIN~TABLET;ORAL~BRANDA~ACME~10MG~N~000001~001~AB~Dec 17, 1996~Yes~No~RX~ACME INC",
    # The pre-1982 literal, which must yield no date and set the flag.
    "QUININE~TABLET;ORAL~OLD~ACME~5MG~N~000007~001~~Approved Prior to Jan 1, 1982~No~No~DISCN~ACME INC",
]

_OB_PATENT = [
    "Appl_Type~Appl_No~Product_No~Patent_No~Patent_Expire_Date_Text"
    "~Drug_Substance_Flag~Drug_Product_Flag~Patent_Use_Code~Delist_Flag~Submission_Date",
    "N~000001~001~7625884~Aug 24, 2026~~~U-141~~",
]

_OB_EXCLUSIVITY = [
    "Appl_Type~Appl_No~Product_No~Exclusivity_Code~Exclusivity_Date",
    "N~000001~001~NCE~Jul 13, 2026",
]

_UNII_NAMES = [
    "NAME\tTYPE\tUNII\tDISPLAY_NAME",
    # Mixed case in the registry, upper case in Products: matching must fold.
    "Aspirin\tof\tR16CO5Y76E\tASPIRIN",
    "Denosumab\tof\t4EQZ6YO2HI\tDENOSUMAB",
    # A suffixed biosimilar sharing the reference UNII. Not one of our names,
    # so it must not inflate the fan-in.
    "Denosumab-adet\tcn\t4EQZ6YO2HI\tDENOSUMAB",
    # Two of our names on one UNII: the fan-in this fixture exists to measure.
    "Atorvastatin calcium\tcn\tSHARED0001\tCOMBO",
    "Ezetimibe\tof\tSHARED0001\tCOMBO",
    # A name we never ask about.
    "Ibuprofen\tof\tWK2XYI10QM\tIBUPROFEN",
]


def _write(path: Path, lines: list[str], newline: str = "\r\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(newline.join(lines).encode() + newline.encode())


@pytest.fixture
def synthetic(tmp_path: Path) -> RawData:
    """A miniature fda_raw/ with the real layout, delimiters and line endings."""
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("Applications.txt", "\r\n".join(_APPLICATIONS) + "\r\n")
        zf.writestr("Products.txt", "\r\n".join(_PRODUCTS) + "\r\n")
        zf.writestr("Submissions.txt", "\r\n".join(_SUBMISSIONS) + "\r\n")

    _write(root / "orangebook" / "x" / "products.txt", _OB_PRODUCTS)
    _write(root / "orangebook" / "x" / "patent.txt", _OB_PATENT)
    _write(root / "orangebook" / "x" / "exclusivity.txt", _OB_EXCLUSIVITY)

    (root / "unii").mkdir(parents=True)
    with zipfile.ZipFile(root / "unii" / "UNIIs.zip", "w") as zf:
        # LF-only, as the real UNII list is.
        zf.writestr("UNII_Names_4Aug2026.txt", "\n".join(_UNII_NAMES) + "\n")

    return RawData(root=root)


# --- the chain --------------------------------------------------------------


def test_the_chain_is_computed_from_the_tables_it_is_given(
    synthetic: RawData,
) -> None:
    """Five names, one unmatched, one fan-in pair merging one, three UNII.

    ASPIRIN, DENOSUMAB, NOTAREALDRUG, ATORVASTATIN CALCIUM and EZETIMIBE are
    the NDA/BLA ingredient components. NOTAREALDRUG resolves to nothing; the
    last two share SHARED0001. So 5 - 1 - 1 = 3.
    """
    chain = identity_census.run(synthetic).identity
    assert chain.names == 5
    assert chain.unmatched == 1
    assert chain.resolved == 4
    assert chain.fan_in_groups == 1
    assert chain.fan_in_names == 2
    assert chain.merged == 1
    assert chain.uniis == 3
    assert chain.reconciles is True


def test_an_anda_ingredient_never_enters_the_count(synthetic: RawData) -> None:
    """22,954 of 29,323 real applications are ANDAs — generic copies.

    IBUPROFEN is in the synthetic Products.txt under an ANDA, and it has a UNII
    in the registry, so it would resolve if it were ever counted.
    """
    chain = identity_census.run(synthetic).identity
    assert chain.names == 5  # not 6
    assert "WK2XYI10QM" not in {unii for unii, _, _ in chain.largest_groups}


def test_an_untypable_applno_drops_out_rather_than_defaulting_to_nda(
    synthetic: RawData,
) -> None:
    """The real file has exactly 8 such rows; MYSTERY is this fixture's one.

    Defaulting an untypable application to NDA would silently add a drug.
    """
    chain = identity_census.run(synthetic).identity
    assert chain.untypable_applnos == ("000099",)
    assert "MYSTERY" not in _names_of(synthetic)


def test_an_anda_is_not_reported_as_untypable(synthetic: RawData) -> None:
    """The two exclusions mean different things and must not be one number."""
    chain = identity_census.run(synthetic).identity
    assert "000003" not in chain.untypable_applnos


def _names_of(data: RawData) -> set[str]:
    names, _ = identity_census.ingredient_names(data)
    return names


def test_a_trailing_space_applno_still_joins(synthetic: RawData) -> None:
    """'00005 ' in Applications.txt must match '000005' in Products.txt.

    If it did not, ATORVASTATIN CALCIUM and EZETIMIBE would vanish from the
    count — which is the real-world failure the canonical form prevents.
    """
    assert {"ATORVASTATIN CALCIUM", "EZETIMIBE"} <= _names_of(synthetic)


def test_matching_is_case_insensitive_in_both_directions(
    synthetic: RawData,
) -> None:
    """The registry stores 'Aspirin'; Products.txt states 'ASPIRIN'."""
    chain = identity_census.run(synthetic).identity
    assert "ASPIRIN" in _names_of(synthetic)
    assert chain.resolved == 4


def test_an_empty_ingredient_is_skipped_not_counted(synthetic: RawData) -> None:
    """One real Products.txt row has an empty ActiveIngredient."""
    assert "" not in _names_of(synthetic)


def test_a_biosimilar_suffix_we_never_asked_about_does_not_inflate_the_fan_in(
    synthetic: RawData,
) -> None:
    """Denosumab-adet shares 4EQZ6YO2HI but is not one of our names.

    The fan-in counts names we actually resolved, not every alias the registry
    happens to hold, or every biologic would appear to collapse.
    """
    chain = identity_census.run(synthetic).identity
    assert chain.fan_in_groups == 1
    assert chain.fan_in_names == 2


def test_an_ambiguous_name_is_counted_and_warned(
    tmp_path: Path, synthetic: RawData, caplog: pytest.LogCaptureFixture
) -> None:
    """0 real names map to >1 UNII — the assumption the drug key rests on.

    So the detector needs its own test rather than relying on the corpus.
    """
    import logging

    with zipfile.ZipFile(synthetic.root / "unii" / "UNIIs.zip", "w") as zf:
        zf.writestr(
            "UNII_Names_4Aug2026.txt",
            "\n".join([_UNII_NAMES[0], "Aspirin\tof\tAAA1111111\tASPIRIN",
                       "Aspirin\tof\tBBB2222222\tASPIRIN"]) + "\n",
        )
    with caplog.at_level(logging.WARNING):
        chain = identity_census.run(synthetic).identity
    assert chain.ambiguous_names == 1
    assert "maps to 2 UNIIs" in caplog.text


# --- the other three sections ----------------------------------------------


def test_the_date_census_counts_every_form_it_meets(synthetic: RawData) -> None:
    """Including the pre-1982 literal and a genuinely blank date."""
    forms = {(label, form): n for label, form, n in identity_census.run(synthetic).date_forms}
    assert forms[("orangebook/products.txt:Approval_Date", "mon_d_yyyy")] == 1
    assert forms[("orangebook/products.txt:Approval_Date", "pre_1982")] == 1
    assert forms[("drugsatfda/Submissions.txt:SubmissionStatusDate", "sql_midnight")] == 2
    assert forms[("drugsatfda/Submissions.txt:SubmissionStatusDate", "absent")] == 1
    assert not any(form == "unparsed" for (_, form) in forms)


def test_the_conformance_census_names_the_value_needing_work(
    synthetic: RawData,
) -> None:
    """A non-conforming ApplNo is quoted, so it can be found in the file."""
    kinds = dict(identity_census.run(synthetic).appno_kinds)
    assert kinds["needs_normalising: '00005 '"] == 1
    assert kinds["already_six_digits"] == 13
    assert kinds["zero_sentinel"] == 1


def test_the_row_counts_flag_a_mismatch_against_the_declared_shape(
    synthetic: RawData,
) -> None:
    """The synthetic tables are tiny, so every declared count must disagree.

    That is the point: a real download that had been truncated would show the
    same way rather than quietly producing smaller numbers.
    """
    counts = identity_census.run(synthetic).row_counts
    assert counts
    for label, actual, declared in counts:
        assert actual != declared, label


def test_render_prints_every_number_it_holds(synthetic: RawData) -> None:
    """So a figure cannot be computed and then silently not reported."""
    report = identity_census.run(synthetic)
    text = identity_census.render(report)
    c = report.identity
    for value in (c.names, c.unmatched, c.resolved, c.fan_in_groups,
                  c.fan_in_names, c.merged, c.uniis, c.ambiguous_names):
        assert f"{value:,}" in text
    assert "OK" in text
    assert "SHARED0001" in text
    for section in ("A. DRUG IDENTITY", "B. DATE SHAPES",
                    "C. APPLICATION-NUMBER CONFORMANCE", "D. ROW COUNTS"):
        assert section in text


def test_a_missing_tree_yields_an_empty_chain_not_a_crash(tmp_path: Path) -> None:
    """fda_raw/ is gitignored, so a fresh clone has none of it."""
    report = identity_census.run(RawData(root=tmp_path / "absent"))
    assert report.identity.names == 0
    assert report.identity.uniis == 0
    assert identity_census.render(report)


# --- the real corpus --------------------------------------------------------


@pytest.fixture(scope="module")
def real_report(fda_raw_root: Path) -> identity_census.CensusReport:
    """One census over the real tree, shared by every realdata test (~3s)."""
    return identity_census.run(RawData(root=fda_raw_root))


@pytest.mark.realdata
def test_the_chain_reproduces_2790_names_and_2597_uniis(
    real_report: identity_census.CensusReport,
) -> None:
    """The figures docs/drug-identity.md quotes, measured 2026-09-11.

    Any drift here is a finding about the data, not a test to relax.
    """
    c = real_report.identity
    assert (c.names, c.unmatched, c.resolved) == (2790, 67, 2723)
    assert (c.fan_in_groups, c.fan_in_names, c.merged) == (49, 175, 126)
    assert c.uniis == 2597
    assert c.reconciles is True


@pytest.mark.realdata
def test_no_matched_name_maps_to_more_than_one_unii(
    real_report: identity_census.CensusReport,
) -> None:
    """The assumption the drug key rests on: names collapse, they do not split."""
    assert real_report.identity.ambiguous_names == 0


@pytest.mark.realdata
def test_the_eight_untypable_applnos_are_exactly_these_eight(
    real_report: identity_census.CensusReport,
) -> None:
    """Products.txt rows with no Applications.txt row, so no type available."""
    assert real_report.identity.untypable_applnos == (
        "040850", "040853", "071403", "077089",
        "200171", "204086", "205476", "205581",
    )


@pytest.mark.realdata
def test_the_fan_in_leaders_are_the_biosimilar_families(
    real_report: identity_census.CensusReport,
) -> None:
    """Biosimilars share their reference product's UNII, so they collapse."""
    assert real_report.identity.largest_groups == (
        ("4EQZ6YO2HI", "DENOSUMAB", 11),
        ("FYS6T7F842", "ADALIMUMAB", 10),
        ("3A58010674", "PEGFILGRASTIM", 9),
        ("FU77B4U5Z0", "USTEKINUMAB", 9),
        ("15C2VL427D", "AFLIBERCEPT", 8),
        ("2S9ZZM9Q9V", "BEVACIZUMAB", 8),
    )


@pytest.mark.realdata
def test_all_5873_pre_1982_rows_are_flagged_and_none_get_a_date(
    real_report: identity_census.CensusReport,
) -> None:
    """5,873 + 42,791 = 48,664, the whole Orange Book.

    The FDA agrees with the reading: its orangebook JSON omits approval_date
    on exactly those 5,873 rows.
    """
    forms = {
        form: n for label, form, n in real_report.date_forms
        if label == "orangebook/products.txt:Approval_Date"
    }
    assert forms["pre_1982"] == 5873
    assert forms["mon_d_yyyy"] == 42791
    assert forms["pre_1982"] + forms["mon_d_yyyy"] == 48664
    assert fda_date.parse(fda_date.PRE_1982_TEXT, direction=fda_date.PAST_ONLY).value is None


@pytest.mark.realdata
def test_no_date_field_in_the_corpus_is_unparsed(
    real_report: identity_census.CensusReport,
) -> None:
    """Zero unparsed values today, which is why one would be worth a warning."""
    assert [row for row in real_report.date_forms if row[1] == "unparsed"] == []


@pytest.mark.realdata
def test_every_applno_conforms_except_the_two_trailing_space_rows(
    real_report: identity_census.CensusReport,
) -> None:
    """274,752 values across three tables; exactly 2 are written differently."""
    kinds = dict(real_report.appno_kinds)
    assert kinds["already_six_digits"] == 274_750
    assert kinds["needs_normalising: '19019 '"] == 1
    assert kinds["needs_normalising: '21939 '"] == 1
    assert sum(kinds.values()) == 274_752


@pytest.mark.realdata
def test_every_file_has_the_row_count_the_config_declares(
    real_report: identity_census.CensusReport,
) -> None:
    """A truncated or re-formatted download shows up here, not downstream."""
    for label, actual, declared in real_report.row_counts:
        assert actual == declared, f"{label}: read {actual}, declared {declared}"
