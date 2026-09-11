"""Tests for the trial → approval mapping: T5's join and T8's extraction."""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from config.settings import RawData, TrialArchive
from service import approval_map

# --- a complete miniature corpus -------------------------------------------

_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "000001\tNDA\t\tACME",
    "000002\tBLA\t\tBIOCO",
    "000003\tANDA\t\tGENERICO",
    # A type no FDA file states: skipped, not guessed.
    "000004\tXX\t\tBADTYPE",
]

_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName\tActiveIngredient\tReferenceStandard",
    "000001\t001\tTABLET;ORAL\t10MG\t0\tBRANDA\tASPIRIN\t0",
    "000002\t001\tINJECTION\t60MG\t0\tBRANDB\tDENOSUMAB\t0",
    "000003\t001\tTABLET;ORAL\t10MG\t0\tGENA\tASPIRIN\t0",
]

_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    # The ORIG is the first approval; the SUPPL must not overwrite it.
    "000001\t19\tORIG\t1\tAP\t1996-12-17 00:00:00\t\tUNKNOWN",
    "000001\t3\tSUPPL\t10\tAP\t2014-09-04 00:00:00\t\t",
    "000002\t1\tORIG\t1\tAP\t2020-06-29 00:00:00\t\tPRIORITY",
    "000003\t1\tORIG\t1\tAP\t2010-01-05 00:00:00\t\t",
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
_UNII_NAMES = [
    "NAME\tTYPE\tUNII\tDISPLAY_NAME",
    "Aspirin\tof\tR16CO5Y76E\tASPIRIN",
    "Denosumab\tof\t4EQZ6YO2HI\tDENOSUMAB",
]

#: NCT00000001 is real; NCT09999999 matches the shape but no study exists.
_LABELS = [
    # Path A: the openfda block names the application.
    {
        "set_id": "set-brand-a",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["14 CLINICAL STUDIES … (NCT00000001, NCT00000002)."],
    },
    # A biologic, cited twice in one block — the pair must dedupe.
    {
        "set_id": "set-brand-b",
        "openfda": {"application_number": ["BLA000002"]},
        "clinical_studies": ["Study NCT00000003 …", "… again NCT00000003 and NCT00000004"],
    },
    # A generic reproducing the brand's section 14 verbatim: must NOT be emitted.
    {
        "set_id": "set-generic",
        "openfda": {"application_number": ["ANDA000003"]},
        "clinical_studies": ["… (NCT00000001)."],
    },
    # An NCT-shaped string naming no real study: must be rejected.
    {
        "set_id": "set-brand-a2",
        "openfda": {"application_number": ["NDA000001"]},
        "clinical_studies": ["… (NCT09999999)."],
    },
    # openfda empty, but the NDC file knows this set_id: path B.
    {
        "set_id": "set-bridged",
        "openfda": {},
        "clinical_studies": ["… (NCT00000005)."],
    },
    # openfda empty and nothing knows it: unlinked.
    {
        "set_id": "set-orphan",
        "openfda": {},
        "clinical_studies": ["… (NCT00000006)."],
    },
    # No section 14 at all: not counted as a section-14 label.
    {"set_id": "set-nosection", "openfda": {"application_number": ["NDA000001"]}},
]

_NDC = [
    {"application_number": "BLA000002", "openfda": {"spl_set_id": ["set-bridged"]}},
    {"application_number": "M020", "openfda": {"spl_set_id": ["set-monograph"]}},
]

_MANIFEST = [
    {"nct": "NCT00000001", "has_results": True},
    {"nct": "NCT00000002", "has_results": False},
    {"nct": "NCT00000003", "has_results": True},
    {"nct": "NCT00000004", "has_results": False},
    {"nct": "NCT00000005", "has_results": True},
    {"nct": "NCT00000006", "has_results": False},
]


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\r\n".join(lines).encode() + b"\r\n")


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[RawData, TrialArchive]:
    """A miniature fda_raw/ and trial archive with the real layout."""
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
        zf.writestr("UNII_Names_4Aug2026.txt", "\n".join(_UNII_NAMES) + "\n")

    (root / "label").mkdir(parents=True)
    # Everything goes in shard 1; the other 13 are legitimately absent.
    with zipfile.ZipFile(root / "label" / "drug-label-0001-of-0014.json.zip", "w") as zf:
        zf.writestr("d.json", json.dumps({"results": _LABELS}))
    (root / "ndc").mkdir(parents=True)
    with zipfile.ZipFile(root / "ndc" / "drug-ndc-0001-of-0001.json.zip", "w") as zf:
        zf.writestr("d.json", json.dumps({"results": _NDC}))

    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    with gzip.open(archive_root / "manifest.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in _MANIFEST:
            handle.write(json.dumps(row) + "\n")
        handle.write("\n")  # a blank line, which the reader must tolerate
        handle.write("{not valid json\n")  # one bad row must not cost the file
    return RawData(root=root), TrialArchive(root=archive_root)


# --- T5: the join -----------------------------------------------------------


def test_a_label_naming_its_application_links_directly(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """Path A: openfda.application_number, present on a third of real labels."""
    report = approval_map.build(*corpus)
    paths = dict(report.label_paths)
    assert paths[approval_map.VIA_OPENFDA] == 4


def test_an_empty_openfda_block_falls_back_to_the_ndc_bridge(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """Path B: set_id -> ndc.spl_set_id -> application.

    On the real corpus this recovers nothing, because a label's openfda block
    derives from the same SPL/NDC linkage — but the path is correct and works
    wherever the NDC file does know the set_id, as here.
    """
    report = approval_map.build(*corpus)
    paths = dict(report.label_paths)
    assert paths[approval_map.VIA_NDC_SET_ID] == 1
    assert ("NCT00000005", "BLA000002") in {(r.nct_id, r.application) for r in report.links}


def test_a_label_nothing_can_identify_is_counted_as_unlinked(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    assert dict(report.label_paths)[approval_map.VIA_NONE] == 1
    assert "NCT00000006" not in {r.nct_id for r in report.links}


def test_a_label_with_no_section_14_is_not_counted(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """46,810 of 262,757 real labels have section 14; the rest are read and skipped."""
    report = approval_map.build(*corpus)
    assert report.labels_seen == len(_LABELS)
    assert report.section14_labels == len(_LABELS) - 1


# --- the branded-only rule --------------------------------------------------


def test_a_generic_citing_the_brands_trials_is_not_emitted(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """An ANDA is approved on bioequivalence, not on the brand's trials.

    Its label reproduces section 14 verbatim, so emitting the row would assert
    something false. Decided 2026-09-11.
    """
    report = approval_map.build(*corpus)
    applications = {row.application for row in report.links}
    assert "ANDA000003" not in applications
    assert all(not a.startswith("ANDA") for a in applications)
    assert report.generic_labels_skipped == 1
    assert report.generic_applications_skipped == 1


def test_the_brands_own_link_survives_the_generic_being_dropped(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """NCT00000001 is cited by both; dropping the generic must not lose it."""
    report = approval_map.build(*corpus)
    assert ("NCT00000001", "NDA000001") in {(r.nct_id, r.application) for r in report.links}


# --- T8: the extraction -----------------------------------------------------


def test_every_nct_in_section_14_becomes_a_link(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    assert {(r.nct_id, r.application) for r in report.links} == {
        ("NCT00000001", "NDA000001"),
        ("NCT00000002", "NDA000001"),
        ("NCT00000003", "BLA000002"),
        ("NCT00000004", "BLA000002"),
        ("NCT00000005", "BLA000002"),
    }


def test_the_same_trial_cited_twice_yields_one_link(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """NCT00000003 appears in two blocks of one label."""
    report = approval_map.build(*corpus)
    pairs = [(r.nct_id, r.application) for r in report.links]
    assert pairs.count(("NCT00000003", "BLA000002")) == 1
    assert report.nct_mentions > len(report.links)


def test_an_nct_shaped_string_naming_no_study_is_rejected(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """Validation against the archive is what separates an id from a digit run."""
    report = approval_map.build(*corpus)
    assert report.nct_not_in_archive == ("NCT09999999",)
    assert "NCT09999999" not in {r.nct_id for r in report.links}


def test_posted_results_are_carried_from_the_archive(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    by_nct = {r.nct_id: r.has_results for r in report.links}
    assert by_nct["NCT00000001"] == "true"
    assert by_nct["NCT00000002"] == "false"
    assert report.nct_with_results == 3


def test_every_link_is_marked_as_the_fda_saying_so(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """This module emits only fda_stated; weaker sources are a later task."""
    report = approval_map.build(*corpus)
    assert {r.confidence for r in report.links} == {approval_map.FDA_STATED}
    assert {r.evidence for r in report.links} == {"label_section_14"}


def test_a_malformed_manifest_line_is_skipped_not_fatal(
    corpus: tuple[RawData, TrialArchive], caplog: pytest.LogCaptureFixture
) -> None:
    """One bad row must not cost a 600,762-row file."""
    import logging

    with caplog.at_level(logging.WARNING):
        report = approval_map.build(*corpus)
    assert "malformed manifest line" in caplog.text
    assert len(report.links) == 5  # every good row still read


def test_an_application_type_the_files_never_state_is_skipped(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """000004 carries type XX and must not become an approval row."""
    report = approval_map.build(*corpus)
    assert all("000004" not in a.application for a in report.approvals)


def test_a_missing_archive_warns_and_emits_nothing(
    corpus: tuple[RawData, TrialArchive], caplog: pytest.LogCaptureFixture
) -> None:
    """Without validation every NCT would be unverified, so none is emitted."""
    import logging

    data, _ = corpus
    with caplog.at_level(logging.WARNING):
        report = approval_map.build(data, TrialArchive(root=Path("/nonexistent")))
    assert report.links == ()
    assert "cannot be validated" in caplog.text


# --- the approval side ------------------------------------------------------


def test_the_approval_date_is_the_original_not_the_latest_supplement(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """86% of real submissions are supplements carrying later changes."""
    report = approval_map.build(*corpus)
    by_app = {a.application: a for a in report.approvals}
    assert by_app["NDA000001"].first_approval == "1996-12-17"
    assert by_app["BLA000002"].first_approval == "2020-06-29"


def test_every_application_gets_a_row_even_without_a_trial(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    """approvals.tsv is the approval facts; links.tsv is the evidence."""
    report = approval_map.build(*corpus)
    assert {a.application for a in report.approvals} == {
        "NDA000001", "BLA000002", "ANDA000003",
    }


def test_the_map_carries_the_drug_and_the_date_for_a_readable_row(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    row = next(r for r in report.mapping if r.nct_id == "NCT00000001")
    assert row.application == "NDA000001"
    assert row.app_type == "NDA"
    assert row.drug == "ASPIRIN"
    assert row.unii == "R16CO5Y76E"
    assert row.first_approval == "1996-12-17"
    assert row.sponsor == "ACME"


def test_drugs_lists_every_substance_not_just_the_representative(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    assert {d.unii for d in report.drugs} == {"R16CO5Y76E", "4EQZ6YO2HI"}


def test_render_reports_every_number_it_holds(
    corpus: tuple[RawData, TrialArchive],
) -> None:
    report = approval_map.build(*corpus)
    text = approval_map.render(report)
    for section in ("T5  LABEL -> APPLICATION", "T8  SECTION 14 -> TRIALS", "TABLES"):
        assert section in text
    assert f"{len(report.links):,}" in text


def test_a_missing_corpus_yields_empty_tables_not_a_crash(tmp_path: Path) -> None:
    report = approval_map.build(
        RawData(root=tmp_path / "absent"), TrialArchive(root=tmp_path / "absent")
    )
    assert report.links == ()
    assert report.mapping == ()
    assert approval_map.render(report)


# --- the real corpus --------------------------------------------------------


@pytest.fixture(scope="module")
def real_report(fda_raw_root: Path) -> approval_map.MapReport:
    """One build over the real corpus, shared by the realdata tests (~52s)."""
    from config.settings import DEFAULT_TRIAL_ARCHIVE

    archive = TrialArchive(root=DEFAULT_TRIAL_ARCHIVE)
    if not archive.exists:
        pytest.skip(f"{archive.manifest} is absent")
    return approval_map.build(RawData(root=fda_raw_root), archive)


@pytest.mark.realdata
def test_the_mapping_reaches_the_predicted_scale(
    real_report: approval_map.MapReport,
) -> None:
    """Measured 2026-09-11, branded only.

    The scope artifact predicted ~2,124 trials and 1,838 with posted results
    from section 14; this reaches 2,060 and 1,803 after excluding generics.
    """
    assert real_report.nct_distinct == 2060
    assert real_report.nct_with_results == 1803
    assert real_report.applications_linked == 773
    assert len(real_report.links) == 2678


@pytest.mark.realdata
def test_no_generic_application_appears_in_the_mapping(
    real_report: approval_map.MapReport,
) -> None:
    assert all(not r.application.startswith("ANDA") for r in real_report.links)
    assert real_report.generic_labels_skipped == 17_504


@pytest.mark.realdata
def test_section_14_is_on_the_measured_number_of_labels(
    real_report: approval_map.MapReport,
) -> None:
    """17.8% of 262,757 — the figure README.md had wrong as 17.2%."""
    assert real_report.labels_seen == 262_757
    assert real_report.section14_labels == 46_810


@pytest.mark.realdata
def test_a_handful_of_nct_shaped_strings_name_no_real_study(
    real_report: approval_map.MapReport,
) -> None:
    """Which is why every id is validated against the archive before emission."""
    assert len(real_report.nct_not_in_archive) == 6


@pytest.mark.realdata
def test_phesgo_links_to_its_own_trials(
    real_report: approval_map.MapReport,
) -> None:
    """The example the scope artifact leads with, end to end.

    Its section 14 excerpt names 4 trials; the whole section cites 7.
    """
    phesgo = {r.nct_id for r in real_report.links if r.application == "BLA761170"}
    assert {"NCT00545688", "NCT00976989", "NCT02132949", "NCT01358877"} <= phesgo
    assert len(phesgo) == 7
