"""Tests for the approval-event spine: one row per approval, not per application."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from config.settings import RawData
from service import approval_events

_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "000001\tNDA\t\tACME",
    "125514\tBLA\t\tMERCK SHARP DOHME",
    "000009\tANDA\t\tGENERICO",
    # A type no FDA file states: skipped, not guessed — so its submissions
    # cannot be keyed either.
    "000004\tXX\t\tBADTYPE",
]

_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    # The original approval — indication-changing whatever its class.
    "000001\t7\tORIG\t1\tAP\t1996-12-17 00:00:00\t\tUNKNOWN",
    # An efficacy supplement: a new indication, years later.
    "000001\t2\tSUPPL\t14\tAP\t2014-09-04 00:00:00\t\t",
    # A labeling supplement: changes the document, not what the drug treats.
    "000001\t3\tSUPPL\t15\tAP\t2016-01-05 00:00:00\t\t",
    # A manufacturing supplement: likewise.
    "000001\t4\tSUPPL\t16\tAP\t2017-03-02 00:00:00\t\t",
    # Not approved — a tentative approval is not an event.
    "000001\t2\tSUPPL\t17\tTA\t2018-04-01 00:00:00\t\t",
    "125514\t7\tORIG\t1\tAP\t2014-09-04 00:00:00\t\tPRIORITY",
    "125514\t2\tSUPPL\t4\tAP\t2015-10-02 00:00:00\t\t",
    # A submission whose application has no Applications.txt row: undroppable
    # to a canonical key, so it is not an event.
    "999999\t2\tSUPPL\t1\tAP\t2020-01-01 00:00:00\t\t",
]

_CLASS_LOOKUP = [
    "SubmissionClassCodeID\tSubmissionClassCode\tSubmissionClassCodeDescription",
    "2\tEFFICACY\tEfficacy",
    "3\tLABELING\tLabeling",
    "4\tMANUF (CMC)\tManufacturing (CMC)",
    "7\tTYPE 1\tType 1 - New Molecular Entity",
    # A blank id, which the real lookup carries: indexing it would map every
    # class-less submission onto whatever description sat beside the blank.
    "\t\tUnclassified",
]

_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient\tReferenceStandard",
    "000001\t001\tTABLET;ORAL\t10MG\t0\tBRANDA\tASPIRIN\t0",
]
_OB_PRODUCTS = [
    "Ingredient~DF;Route~Trade_Name~Applicant~Strength~Appl_Type~Appl_No~Product_No"
    "~TE_Code~Approval_Date~RLD~RS~Type~Applicant_Full_Name",
    "ASPIRIN~TABLET;ORAL~BRANDA~ACME~10MG~N~000001~001~AB~Dec 17, 1996~Yes~No~RX~ACME INC",
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
_UNII_NAMES = ["NAME\tTYPE\tUNII\tDISPLAY_NAME", "Aspirin\tof\tR16CO5Y76E\tASPIRIN"]


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\r\n".join(lines).encode() + b"\r\n")


@pytest.fixture
def corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("Applications.txt", "\r\n".join(_APPLICATIONS) + "\r\n")
        zf.writestr("Products.txt", "\r\n".join(_PRODUCTS) + "\r\n")
        zf.writestr("Submissions.txt", "\r\n".join(_SUBMISSIONS) + "\r\n")
        zf.writestr("SubmissionClass_Lookup.txt", "\r\n".join(_CLASS_LOOKUP) + "\r\n")
    _write(root / "orangebook" / "x" / "products.txt", _OB_PRODUCTS)
    _write(root / "orangebook" / "x" / "patent.txt", _OB_PATENT)
    _write(root / "orangebook" / "x" / "exclusivity.txt", _OB_EXCLUSIVITY)
    (root / "unii").mkdir(parents=True)
    with zipfile.ZipFile(root / "unii" / "UNIIs.zip", "w") as zf:
        zf.writestr("UNII_Names_4Aug2026.txt", "\n".join(_UNII_NAMES) + "\n")
    return RawData(root=root)


def test_only_approved_submissions_become_events(corpus: RawData) -> None:
    """A tentative approval is not an approval."""
    report = approval_events.build(corpus)
    assert report.submissions_seen == 8
    assert report.approved == 7
    assert all(row.approval_date for row in report.rows)


def test_a_submission_on_an_untypable_application_is_dropped(corpus: RawData) -> None:
    """999999 has no Applications.txt row, so it cannot be keyed."""
    report = approval_events.build(corpus)
    assert len(report.rows) == 6
    assert "999999" not in {row.application for row in report.rows}


def test_an_efficacy_supplement_is_indication_changing(corpus: RawData) -> None:
    report = approval_events.build(corpus)
    by_key = {(r.application, r.submission_no): r for r in report.rows}
    assert by_key[("NDA000001", "14")].submission_class == "Efficacy"
    assert by_key[("NDA000001", "14")].indication_changing == "true"


def test_labeling_and_manufacturing_supplements_are_not(corpus: RawData) -> None:
    """They change the document, the plant or the risk programme — not what the
    drug is approved to treat. Over the real corpus they are 152,233 of 192,477
    approved submissions, so counting them would swamp the signal."""
    report = approval_events.build(corpus)
    by_key = {(r.application, r.submission_no): r for r in report.rows}
    assert by_key[("NDA000001", "15")].indication_changing == "false"
    assert by_key[("NDA000001", "16")].indication_changing == "false"


def test_an_original_is_indication_changing_whatever_its_class(
    corpus: RawData,
) -> None:
    """It is the approval that created the indication in the first place."""
    report = approval_events.build(corpus)
    orig = next(r for r in report.rows if r.application == "NDA000001"
                and r.submission_type == "ORIG")
    assert orig.submission_class == "Type 1 - New Molecular Entity"
    assert orig.indication_changing == "true"


def test_the_application_type_rides_along(corpus: RawData) -> None:
    report = approval_events.build(corpus)
    assert {r.app_type for r in report.rows if r.application == "BLA125514"} == {"BLA"}


def test_indication_changing_dates_are_sorted_per_application(
    corpus: RawData,
) -> None:
    dates = approval_events.indication_changing_dates(approval_events.build(corpus))
    assert dates["NDA000001"] == ["1996-12-17", "2014-09-04"]
    assert dates["BLA125514"] == ["2014-09-04", "2015-10-02"]


def test_dates_exclude_the_non_indication_changing_events(corpus: RawData) -> None:
    """2016-01-05 and 2017-03-02 are Labeling and CMC; they must not appear."""
    dates = approval_events.indication_changing_dates(approval_events.build(corpus))
    assert "2016-01-05" not in dates["NDA000001"]
    assert "2017-03-02" not in dates["NDA000001"]


@pytest.mark.parametrize(
    ("submission_type", "submission_class", "expected"),
    [
        ("ORIG", "Type 1 - New Molecular Entity", True),
        ("ORIG", "", True),
        ("SUPPL", "Efficacy", True),
        ("SUPPL", "Labeling", False),
        ("SUPPL", "Manufacturing (CMC)", False),
        ("SUPPL", "REMS", False),
        ("SUPPL", "", False),
    ],
)
def test_the_indication_changing_rule(
    submission_type: str, submission_class: str, expected: bool
) -> None:
    assert (
        approval_events.is_indication_changing(submission_type, submission_class)
        is expected
    )


def test_an_untypable_application_contributes_no_events(corpus: RawData) -> None:
    """000004 is typed XX, which no FDA file states. Its rows drop out rather
    than being defaulted to NDA."""
    report = approval_events.build(corpus)
    assert all(not r.application.endswith("000004") for r in report.rows)


def test_a_blank_class_id_is_not_indexed(corpus: RawData) -> None:
    """The lookup carries a blank id; indexing it would attach 'Unclassified'
    to every submission whose class code is empty."""
    report = approval_events.build(corpus)
    # the TA row is excluded, so no event should carry that description
    assert all(r.submission_class != "Unclassified" for r in report.rows)


def test_the_report_renders_its_numbers(corpus: RawData) -> None:
    rendered = approval_events.render(approval_events.build(corpus))
    assert "Approval events" in rendered
    assert "Efficacy" in rendered
