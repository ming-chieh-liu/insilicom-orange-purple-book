"""Tests for reading a label's Clinical Studies section."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from config.settings import RawData
from service import section14

# A section 14 as a modern label writes it: numbered, partitioned by
# indication, trials named in prose with a patient count.
_LABEL = """
FULL PRESCRIBING INFORMATION: CONTENTS
1 INDICATIONS AND USAGE
14 CLINICAL STUDIES
14.1 Melanoma
14.2 Non-Small Cell Lung Cancer

FULL PRESCRIBING INFORMATION

1 INDICATIONS AND USAGE
DRUGX is indicated for the treatment of unresectable melanoma.

2 DOSAGE AND ADMINISTRATION
Administer 200 mg every 3 weeks.

14 CLINICAL STUDIES
14.1 Melanoma
The efficacy of DRUGX was investigated in KEYNOTE-006 (NCT01866319), a
randomized, open-label, multicenter trial conducted in 834 patients with
unresectable melanoma.
Among the 91 patients randomized to DRUGX 10 mg/kg every 3 weeks with a
response, durations ranged from 1.4+ to 8.1+ months.
14.2 Non-Small Cell Lung Cancer
Two hundred twenty-nine (229) patients were randomized to receive DRUGX
7.4 GBq every 8 weeks.

16 HOW SUPPLIED/STORAGE AND HANDLING
Store at 25 degrees.
"""

# A pre-PLR label: the section exists but carries no number.
_OLD_LABEL = """
DESCRIPTION
DRUGY is a white powder.

CLINICAL STUDIES
In a double-blind trial, 412 patients were randomized to DRUGY or placebo.

HOW SUPPLIED
Bottles of 30.
"""


def test_section_14_is_found() -> None:
    body = section14.section(_LABEL, 14)
    assert body.startswith("14 CLINICAL STUDIES")


def test_the_contents_list_is_not_mistaken_for_the_section() -> None:
    """A label states '14 CLINICAL STUDIES' twice — once in the contents at the
    top, once as the section. Taking the first match yields the contents."""
    body = section14.section(_LABEL, 14)
    assert "KEYNOTE-006" in body


def test_the_section_stops_at_the_next_top_level_heading() -> None:
    assert "Store at 25 degrees" not in section14.section(_LABEL, 14)


def test_section_1_is_found_and_bounded() -> None:
    body = section14.section(_LABEL, 1)
    assert "unresectable melanoma" in body
    assert "Administer 200 mg" not in body


def test_an_unnumbered_section_is_found() -> None:
    """Pre-PLR labels head it without a number. They still name trials and
    patient counts, so reading them as having no section is simply wrong."""
    body = section14.section(_OLD_LABEL, 14)
    assert "412 patients" in body


def test_an_unnumbered_section_stops_at_the_next_heading() -> None:
    assert "Bottles of 30" not in section14.section(_OLD_LABEL, 14)


def test_a_label_with_no_clinical_studies_yields_nothing() -> None:
    assert section14.section("1 INDICATIONS AND USAGE\nFor pain.", 14) == ""


# ---------------------------------------------------------------------------
# Subsections
# ---------------------------------------------------------------------------


def test_subsections_split_on_numbered_headings() -> None:
    parts = section14.split_subsections(section14.section(_LABEL, 14))
    assert [number for number, _, _ in parts] == ["14.1", "14.2"]
    assert parts[0][1] == "Melanoma"


def test_a_section_with_no_subsections_is_one_block() -> None:
    """The trials still belong to the submission even when the label does not
    partition them."""
    parts = section14.split_subsections(section14.section(_OLD_LABEL, 14))
    assert len(parts) == 1
    assert parts[0][0] == "14"


def test_an_empty_section_yields_no_blocks() -> None:
    assert section14.split_subsections("   ") == ()


def test_a_number_in_running_text_is_not_a_subsection() -> None:
    """A bare 14.2 pattern over flat text matches table values, dosages and
    confidence bounds. Only a line-anchored heading counts."""
    body = "14 CLINICAL STUDIES\nThe ratio was 14.2 at week 8 in Table 14.3.\n"
    assert [n for n, _, _ in section14.split_subsections(body)] == ["14"]


# ---------------------------------------------------------------------------
# Trial descriptions
# ---------------------------------------------------------------------------


def _describe(text: str) -> tuple[section14.TrialDescription, ...]:
    return section14.describe_trials("NDA1", "SUPPL", "1", "14.1 Head\n" + text)


def test_an_nct_id_and_acronym_and_count_are_all_extracted() -> None:
    got = _describe(
        "The efficacy of DRUGX was investigated in KEYNOTE-006 (NCT01866319), a "
        "randomized, open-label trial conducted in 834 patients."
    )
    assert got[0].nct_id == "NCT01866319"
    assert got[0].acronym == "KEYNOTE-006"
    assert got[0].enrollment == "834"


def test_a_count_written_before_the_verb_is_found() -> None:
    """'Two hundred twenty-nine (229) patients were randomized' puts the number
    before the verb, which the other pattern misses."""
    got = _describe("Two hundred twenty-nine (229) patients were randomized to DRUGX.")
    assert got[0].enrollment == "229"


def test_a_comma_grouped_count_loses_its_comma() -> None:
    got = _describe("Efficacy was studied in 1,739 patients with NSCLC.")
    assert got[0].enrollment == "1739"


def test_a_dose_is_not_read_as_a_trial_name() -> None:
    """'DRUGX 10 mg/kg' is a brand name and a dose. Reading it as trial
    'DRUGX 10' was the largest source of false acronyms on a real label."""
    got = _describe("Patients received DRUGX 10 mg/kg every 3 weeks for 12 months.")
    assert all(d.acronym == "" for d in got)


def test_a_decimal_dose_is_not_read_as_a_trial_name() -> None:
    """The unit follows a decimal point, so a lookahead for the unit alone is
    not enough: 'LUTATHERA 7.4 GBq' read as trial 'LUTATHERA 7'."""
    got = _describe("Two hundred (200) patients were given DRUGY 7.4 GBq weekly.")
    assert all(d.acronym == "" for d in got)


def test_a_spaced_trial_name_is_still_a_trial_name() -> None:
    """Labels write both KEYNOTE-189 and KEYNOTE 189."""
    got = _describe("Efficacy was studied in CHECKMATE 227, enrolling 1739 patients.")
    assert got[0].acronym == "CHECKMATE 227"


def test_a_sentence_naming_nothing_is_not_a_description() -> None:
    got = _describe("The primary endpoint was overall survival.")
    assert got == ()


def test_the_design_words_are_kept() -> None:
    got = _describe(
        "DRUGX was studied in a randomized, double-blind, multicenter trial of "
        "500 patients."
    )
    assert "randomized" in got[0].design
    assert "double-blind" in got[0].design


def test_the_excerpt_carries_the_sentence_for_a_judge() -> None:
    got = _describe("Efficacy was studied in TRIALX 12, enrolling 300 patients.")
    assert "TRIALX 12" in got[0].excerpt


def test_the_heading_rides_along_so_the_indication_is_known() -> None:
    body = section14.section(_LABEL, 14)
    got = section14.describe_trials("NDA1", "SUPPL", "1", body)
    assert {d.heading for d in got} >= {"Melanoma"}


def test_the_submission_is_stamped_on_every_description() -> None:
    got = _describe("Efficacy was studied in TRIALX 12, enrolling 300 patients.")
    assert (got[0].application, got[0].submission_type, got[0].submission_no) == (
        "NDA1",
        "SUPPL",
        "1",
    )


# ---------------------------------------------------------------------------
# Reading a whole label
# ---------------------------------------------------------------------------


def test_reading_a_label_yields_both_sections() -> None:
    reading = section14.read_label("k", "NDA1", "SUPPL", "1", _LABEL)
    assert reading.has_section_14
    assert "unresectable melanoma" in reading.section_1
    assert reading.subsections == 2


def test_a_label_without_the_section_reports_so() -> None:
    reading = section14.read_label("k", "NDA1", "SUPPL", "1", "nothing here")
    assert not reading.has_section_14
    assert reading.descriptions == ()


def test_an_unnumbered_label_reports_no_numbered_subsections() -> None:
    reading = section14.read_label("k", "NDA1", "SUPPL", "1", _OLD_LABEL)
    assert reading.has_section_14
    assert reading.subsections == 0


@pytest.mark.parametrize(
    ("with_14", "readable", "expected"),
    [(7, 8, 0.875), (0, 0, 0.0), (9, 9, 1.0)],
)
def test_the_section_14_rate(with_14: int, readable: int, expected: float) -> None:
    report = section14.LabelsReport(
        (), (), 0, readable, 0, 0, 0, with_14, 0, 0, 0, 0
    )
    assert report.section_14_rate == expected


def test_the_report_renders_its_numbers() -> None:
    report = section14.LabelsReport((), (), 10, 9, 1, 2, 0, 6, 6, 3, 4, 12)
    rendered = section14.render(report)
    assert "section 14" in rendered
    assert "scanned" in rendered


# ---------------------------------------------------------------------------
# The whole pass, over a fake corpus with fetching stubbed out
# ---------------------------------------------------------------------------

_APPLICATIONS = [
    "ApplNo\tApplType\tApplPublicNotes\tSponsorName",
    "125514\tBLA\t\tMERCK",
]
_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    "125514\t7\tORIG\t1\tAP\t2014-09-04 00:00:00\t\tPRIORITY",
    # A labeling supplement: not trial-relevant, so never fetched.
    "125514\t3\tSUPPL\t2\tAP\t2015-01-01 00:00:00\t\t",
]
_CLASS = [
    "SubmissionClassCodeID\tSubmissionClassCode\tSubmissionClassCodeDescription",
    "3\tLABELING\tLabeling",
    "7\tTYPE 1\tType 1 - New Molecular Entity",
]
_ACTIONS = [
    "ActionTypes_LookupID\tActionTypes_LookupDescription"
    "\tSupplCategoryLevel1Code\tSupplCategoryLevel2Code",
    "13\tLabeling\tLABELING\tNOT APPLICABLE",
    "46\t\t\tNOT APPLICABLE",
]
_JOIN = [
    "SubmissionType\tj_submissionActionTypeID\tApplNo\tSubmissionNo"
    "\tActionTypes_LookupID",
    "ORIG      \t1\t125514\t1\t46",
    "SUPPL     \t2\t125514\t2\t13",
]
_PROPERTY = [
    "ApplNo\tSubmissionType\tSubmissionNo\tSubmissionPropertyTypeCode"
    "\tSubmissionPropertyTypeID",
    "125514\tORIG      \t1\tNull\t0",
]
_PRODUCTS = [
    "ApplNo\tProductNo\tForm\tStrength\tReferenceDrug\tDrugName"
    "\tActiveIngredient\tReferenceStandard",
    "125514\t001\tINJECTION\t100MG/4ML\t0\tKEYTRUDA\tPEMBROLIZUMAB\t0",
]

_APP_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType"
    "\tSubmissionNo\tApplicationDocsTitle\tApplicationDocsURL\tApplicationDocsDate",
    "1\t2\t125514\tORIG      \t1\t0\thttp://x/125514s000lbl.pdf\t2014-09-04 00:00:00",
    "2\t2\t125514\tSUPPL     \t2\t0\thttp://x/125514s002lbl.pdf\t2015-01-01 00:00:00",
]


@pytest.fixture
def corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    members = {
        "Applications.txt": _APPLICATIONS,
        "Products.txt": _PRODUCTS,
        "Submissions.txt": _SUBMISSIONS,
        "SubmissionClass_Lookup.txt": _CLASS,
        "ActionTypes_Lookup.txt": _ACTIONS,
        "Join_Submission_ActionTypes_Lookup.txt": _JOIN,
        "SubmissionPropertyType.txt": _PROPERTY,
        "ApplicationDocs.txt": _APP_DOCS,
    }
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        for name, lines in members.items():
            zf.writestr(name, "\r\n".join(lines) + "\r\n")
    return RawData(root=root)


@pytest.fixture
def stub_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Fetch returns a path; extract returns whatever text the test registered."""
    texts: dict[str, str] = {}

    def _fetch(url: str, root: Path) -> Path:
        path = root / f"{abs(hash(url))}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
        texts[str(path)] = texts.get(url, _LABEL)
        return path

    def _extract(path: Path) -> tuple[str, int, int] | None:
        body = texts.get(str(path))
        return None if body is None else (body, 10, 1_000)

    monkeypatch.setattr(section14.label_pdf, "fetch", _fetch)
    # P2 now fetches through polite_fetch, which asks label_pdf.fetch_status;
    # route it to the same stub, and skip the one-second pause between requests.
    monkeypatch.setattr(section14.label_pdf, "fetch_status",
                        lambda url, root: (lambda p: (p, "") if p else (None, "not_found"))(
                            _fetch(url, root)))
    real_fetch_all = section14.polite_fetch.fetch_all
    monkeypatch.setattr(section14.polite_fetch, "fetch_all",
                        lambda urls, root, **kw: real_fetch_all(
                            urls, root, **{**kw, "sleep": lambda _s: None}))
    monkeypatch.setattr(section14.label_pdf, "extract_text", _extract)
    return texts


def test_only_trial_relevant_submissions_are_fetched(
    corpus: RawData, tmp_path: Path, stub_network: dict[str, str]
) -> None:
    """The labeling supplement has a label document too, and no reason to read
    it — labeling changes the document, not what the drug is approved to treat."""
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    assert report.submissions == 1
    assert {d.submission_type for d in report.descriptions} == {"ORIG"}


def test_the_pass_extracts_descriptions_and_indications(
    corpus: RawData, tmp_path: Path, stub_network: dict[str, str]
) -> None:
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    assert report.with_section_14 == 1
    assert report.section_14_rate == 1.0
    assert any(d.nct_id == "NCT01866319" for d in report.descriptions)
    assert "unresectable melanoma" in report.indications[0].text


def test_a_scanned_label_is_counted_and_skipped(
    corpus: RawData, tmp_path: Path, stub_network: dict[str, str]
) -> None:
    stub_network["http://x/125514s000lbl.pdf"] = "tiny"
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    assert report.scanned == 1
    assert report.descriptions == ()


def test_an_unreadable_pdf_is_counted_not_fatal(
    corpus: RawData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _cached(url: str, root: Path) -> tuple[Path, str]:
        path = root / "label.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")          # a PDF, but one with no readable text
        return path, ""

    monkeypatch.setattr(section14.label_pdf, "fetch_status", _cached)
    monkeypatch.setattr(section14.label_pdf, "extract_text", lambda path: None)
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    assert report.unreadable == 1
    assert report.descriptions == ()


def test_a_failed_download_is_counted(
    corpus: RawData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unreachable label must not cost the rest of the run."""
    monkeypatch.setattr(section14.label_pdf, "fetch_status",
                        lambda url, root: (None, "not_found"))
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    assert report.failed == 1


def test_offline_sends_nothing_and_counts_what_it_could_not_read(
    corpus: RawData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild re-extracts from the cache; nothing may reach FDA."""
    def _refuse(url: str, root: Path) -> tuple[None, str]:
        raise AssertionError("offline must not fetch")

    monkeypatch.setattr(section14.label_pdf, "fetch_status", _refuse)
    report = section14.build(corpus, tmp_path / "cache", parsers=1, offline=True)
    assert report.failed == 1 and report.descriptions == ()
    assert report.fetched == 0


def test_the_limit_takes_the_newest_not_the_first(
    corpus: RawData, tmp_path: Path, stub_network: dict[str, str]
) -> None:
    """ApplicationDocs is ordered by application number, which is effectively
    chronological, so head() samples only the oldest, pre-PLR, often-scanned
    labels — a pilot that way reported 16.7% against a 90% gate."""
    report = section14.build(corpus, tmp_path / "cache", limit=1, parsers=1)
    assert report.submissions == 1


# ---------------------------------------------------------------------------
# The drug's own name is not a trial name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acronym", "own", "expected"),
    [
        ("ABILIFY 15", {"ABILIFY"}, True),
        ("ALOXI 189", {"ALOXI"}, True),
        ("KEYTRUDA 200", {"KEYTRUDA"}, True),
        # A real trial name survives even when the drug is in the set.
        ("KEYNOTE-189", {"KEYTRUDA"}, False),
        ("CHECKMATE 227", {"OPDIVO"}, False),
        # Nothing to compare against.
        ("ABILIFY 15", set(), False),
    ],
)
def test_an_acronym_starting_with_the_drugs_own_name_is_rejected(
    acronym: str, own: set[str], expected: bool
) -> None:
    """Section 14 is full of dose-arm tables, and PDF extraction runs the brand
    name into the numbers beside it. 51% of extracted trial names were this
    shape before the guard."""
    assert section14.is_own_drug_name(acronym, frozenset(own)) is expected


def test_describe_trials_drops_the_drugs_own_name() -> None:
    got = section14.describe_trials(
        "NDA1",
        "ORIG",
        "1",
        "14.1 Schizophrenia\nPatients on ABILIFY 15 and ABILIFY 30 were "
        "compared in 400 patients.",
        frozenset({"ABILIFY"}),
    )
    assert all(d.acronym == "" for d in got)


def test_describe_trials_keeps_a_real_name_alongside(
    ) -> None:
    got = section14.describe_trials(
        "NDA1",
        "ORIG",
        "1",
        "14.1 Melanoma\nDRUGX was studied in KEYNOTE-006, enrolling 834 patients.",
        frozenset({"DRUGX"}),
    )
    assert got[0].acronym == "KEYNOTE-006"


def test_read_label_passes_the_own_names_through() -> None:
    reading = section14.read_label(
        "k",
        "NDA1",
        "ORIG",
        "1",
        "14 CLINICAL STUDIES\n14.1 Schizophrenia\nABILIFY 15 was given to "
        "400 patients.\n16 HOW SUPPLIED\nx",
        frozenset({"ABILIFY"}),
    )
    assert all(d.acronym == "" for d in reading.descriptions)


def test_the_pass_collects_each_applications_own_names(
    corpus: RawData, tmp_path: Path, stub_network: dict[str, str]
) -> None:
    """Read from Products.txt, both the brand name and the ingredient, so a
    label writing either one in a table column is covered.

    The filler is not decoration: a text shorter than
    ``label_pdf.MIN_TEXT_CHARS`` is classified as a scan and never parsed, so
    a terse fixture silently tests nothing.
    """
    stub_network["http://x/125514s000lbl.pdf"] = (
        "1 INDICATIONS AND USAGE\nKEYTRUDA is indicated for melanoma.\n"
        "2 DOSAGE AND ADMINISTRATION\n" + "Filler prose. " * 40 + "\n"
        "14 CLINICAL STUDIES\n14.1 Melanoma\n"
        "KEYTRUDA 200 was compared with KEYNOTE-006 in 834 patients.\n"
        "16 HOW SUPPLIED\nx\n"
    )
    report = section14.build(corpus, tmp_path / "cache", parsers=1)
    names = {d.acronym for d in report.descriptions}
    assert "KEYNOTE-006" in names
    assert "KEYTRUDA 200" not in names


@pytest.mark.parametrize(
    "written",
    ["NCT01474512", "NCT 01474512", "NCT#01474512", "NCT:01474512", "NCT-01474512"],
)
def test_a_registry_id_is_found_however_the_label_spaces_it(written: str) -> None:
    """Labels write 'NCT 01474512' as often as 'NCT01474512'. Requiring no
    separator missed 40 rows that already stated their own answer, sending them
    to a judge to identify a trial the label had named."""
    got = section14.describe_trials(
        "NDA1", "ORIG", "1",
        f"14.1 Psoriasis\nThree trials ({written}) enrolled 3866 subjects.",
    )
    assert got[0].nct_id == "NCT01474512"


# ---------------------------------------------------------------------------
# A sentence naming several trials must not lose any of them
# ---------------------------------------------------------------------------


_TWO = (
    "14 CLINICAL STUDIES\n"
    "The efficacy of VUITY for the treatment of presbyopia was demonstrated in "
    "two 30-Day Phase 3, randomized, double-masked, vehicle-controlled studies, "
    "namely GEMINI 1 (NCT03804268) and GEMINI 2 (NCT03857542).\n"
)


def test_both_ids_in_one_sentence_are_kept() -> None:
    """Labels routinely introduce a pair together. Keeping only the first
    dropped the second silently — 26 of a 40-case sample were ids sitting
    inside a section 14 already captured correctly."""
    got = section14.describe_trials("NDA214028", "ORIG", "1", _TWO)
    assert {d.nct_id for d in got} == {"NCT03804268", "NCT03857542"}


def test_acronyms_pair_positionally_with_their_ids() -> None:
    """GEMINI 1 belongs to the first id, GEMINI 2 to the second. Mis-pairing is
    what made two different trials collide in candidates.collapse."""
    got = {d.nct_id: d.acronym for d in section14.describe_trials(
        "NDA214028", "ORIG", "1", _TWO
    )}
    assert got == {"NCT03804268": "GEMINI 1", "NCT03857542": "GEMINI 2"}


def test_an_unpairable_acronym_is_not_guessed_onto_the_second_id() -> None:
    """A wrong name is worse than none."""
    body = (
        "14 CLINICAL STUDIES\n"
        "The MONARCH trial compared the two arms (NCT00000001) and (NCT00000002).\n"
    )
    got = {d.nct_id: d.acronym for d in section14.describe_trials(
        "NDA1", "ORIG", "1", body
    )}
    assert got["NCT00000002"] == ""


def test_the_count_stays_with_the_first_id_only() -> None:
    """A sentence naming two trials and one count does not say which it belongs
    to. Copying it to both manufactures the count agreement P5 reads as
    corroboration (D17)."""
    body = (
        "14 CLINICAL STUDIES\n"
        "Efficacy was shown in ALPHA (NCT00000001) and BETA (NCT00000002), "
        "which enrolled 616 patients.\n"
    )
    got = {d.nct_id: d.enrollment for d in section14.describe_trials(
        "NDA1", "ORIG", "1", body
    )}
    assert got["NCT00000002"] == ""


def test_the_single_trial_case_is_unchanged() -> None:
    body = (
        "14 CLINICAL STUDIES\n"
        "KEYNOTE-189 (NCT02578680) was conducted in 616 patients.\n"
    )
    got = section14.describe_trials("BLA125514", "SUPPL", "35", body)
    assert len(got) == 1
    assert (got[0].nct_id, got[0].acronym, got[0].enrollment) == (
        "NCT02578680", "KEYNOTE-189", "616",
    )


# --- wrapped headings (D34) -------------------------------------------------


def test_a_wrapped_heading_is_joined_back() -> None:
    block = ("14.1 Deleterious or Suspected Deleterious Germline BRCA-mutated "
             "HER2-negative Locally Advanced\nor Metastatic Breast Cancer\n"
             "EMBRACA (NCT01945775) was an open label study.")
    first = ("Deleterious or Suspected Deleterious Germline BRCA-mutated "
             "HER2-negative Locally Advanced")
    assert section14.continued_heading(first, block).endswith(
        "Locally Advanced or Metastatic Breast Cancer"
    )


def test_a_long_heading_takes_a_title_cased_line_but_never_prose() -> None:
    first = "Clinical Trial Results in Adults with Chronic GT4 HCV Infection with Compensated"
    assert section14.continued_heading(first, f"14.3 {first}\nCirrhosis\nPEARL-I was a trial") == (
        first + " Cirrhosis"
    )
    prose = f"14.3 {first}\nThe efficacy of the drug was evaluated in a trial"
    assert section14.continued_heading(first, prose) == first


def test_a_short_heading_is_not_extended_and_ids_counts_and_numbers_stop_it() -> None:
    assert section14.continued_heading("Melanoma", "14.1 Melanoma\nAdjuvant Treatment") == "Melanoma"
    long = "A Heading Long Enough To Have Wrapped Onto The Next Line Of Text"
    for stop in ("KEYNOTE-054 (NCT02362594)", "Patients (N = 120)", "14.2 Next Section",
                 "A sentence that ends.", ""):
        assert section14.continued_heading(long, f"14.1 {long}\n{stop}") == long
    assert section14.continued_heading(long + ".", f"14.1 {long}.\nMore Words") == long + "."


def test_descriptions_carry_the_full_heading() -> None:
    body = ("14 CLINICAL STUDIES\n14.1 Deleterious or Suspected Deleterious Germline "
            "BRCA-mutated HER2-negative Locally Advanced\nor Metastatic Breast Cancer\n"
            "EMBRACA (NCT01945775) was an open label study in 431 patients.\n")
    got = section14.describe_trials("NDA211651", "ORIG", "1", body)
    assert got and got[0].heading.endswith("Locally Advanced")
    assert got[0].heading_full.endswith("Metastatic Breast Cancer")


def test_an_unheaded_block_has_no_heading_to_continue() -> None:
    assert section14.continued_heading("", "14 CLINICAL STUDIES\nPreviously Untreated NSCLC") == ""
