"""Tests for the approval-letter indication source (D25)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from config.settings import RawData
from service import letters

_APPLICATIONS = ["ApplNo\tApplType\tApplPublicNotes\tSponsorName",
                 "000001\tNDA\t\tACME", "000002\tNDA\t\tOLDCO"]
_SUBMISSIONS = [
    "ApplNo\tSubmissionClassCodeID\tSubmissionType\tSubmissionNo\tSubmissionStatus"
    "\tSubmissionStatusDate\tSubmissionsPublicNotes\tReviewPriority",
    "000001\t7\tORIG\t1\tAP\t2010-07-16 00:00:00\t\t",   # indexed letter, decimal
    "000001\t2\tSUPPL\t2\tAP\t2012-01-01 00:00:00\t\t",  # indexed letter, "proposes"
    "000001\t2\tSUPPL\t3\tAP\t2015-01-01 00:00:00\t\t",  # no row: guessed address
    "000001\t2\tSUPPL\t4\tAP\t2016-01-01 00:00:00\t\t",  # scanned letter
    "000001\t2\tSUPPL\t5\tAP\t2017-01-01 00:00:00\t\t",  # letter without a sentence
    "000001\t2\tSUPPL\t6\tAP\t2018-01-01 00:00:00\t\t",  # fetch fails
    "000001\t2\tSUPPL\t7\tAP\t2019-01-01 00:00:00\t\t",  # unreadable file
    "000002\t7\tORIG\t1\tAP\t1990-01-01 00:00:00\t\t",   # before letters existed
]
_CLASS = ["SubmissionClassCodeID\tSubmissionClassCode\tSubmissionClassCodeDescription",
          "2\tEFFICACY\tEfficacy", "7\tTYPE 1\tType 1 - New Molecular Entity"]
_ACTIONS = ["ActionTypes_LookupID\tActionTypes_LookupDescription"
            "\tSupplCategoryLevel1Code\tSupplCategoryLevel2Code",
            "8\tEfficacy-New Indication\tEFFICACY\tINDICATION"]
_JOIN = ["SubmissionType\tj_submissionActionTypeID\tApplNo\tSubmissionNo\tActionTypes_LookupID"] + [
    f"SUPPL     \t{n}\t000001\t{n}\t8" for n in range(2, 8)
]
_PROPERTY = ["ApplNo\tSubmissionType\tSubmissionNo\tSubmissionPropertyTypeCode"
             "\tSubmissionPropertyTypeID"]
_BASE = "http://x/appletter"
_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType\tSubmissionNo"
    "\tApplicationDocsTitle\tApplicationDocsURL\tApplicationDocsDate",
    f"1\t1\t000001\tORIG      \t1\t0\t{_BASE}/o1.pdf\t2010-07-16 00:00:00",
    f"2\t1\t000001\tSUPPL     \t2\t0\t{_BASE}/s2.pdf\t2012-01-01 00:00:00",
    f"3\t1\t000001\tSUPPL     \t4\t0\t{_BASE}/s4.pdf\t2016-01-01 00:00:00",
    f"4\t1\t000001\tSUPPL     \t5\t0\t{_BASE}/s5.pdf\t2017-01-01 00:00:00",
    f"5\t1\t000001\tSUPPL     \t6\t0\t{_BASE}/s6.pdf\t2018-01-01 00:00:00",
    f"6\t1\t000001\tSUPPL     \t7\t0\t{_BASE}/s7.pdf\t2019-01-01 00:00:00",
]

_PAD = " Please refer to your application. " * 20  # above the scan threshold
_TEXTS = {
    f"{_BASE}/o1.pdf": _PAD + "This NDA provides for the use of Veltin Gel, 1.2%/0.025% "
    "for the treatment of acne vulgaris in patients 12 years and older. We have completed our review.",
    f"{_BASE}/s2.pdf": _PAD + "This supplement proposes to add the indication of: gout "
    "APPROVAL & LABELING We have completed our review",
    "https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2015/000001s003ltr.pdf":
        _PAD + "This sNDA provides for the treatment of covid-19 in adults. Next.",
    f"{_BASE}/s4.pdf": "x",
    f"{_BASE}/s5.pdf": _PAD + "We acknowledge receipt of your amendments.",
}


@pytest.fixture
def data(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    tables = {
        "Applications.txt": _APPLICATIONS, "Submissions.txt": _SUBMISSIONS,
        "SubmissionClass_Lookup.txt": _CLASS, "ActionTypes_Lookup.txt": _ACTIONS,
        "Join_Submission_ActionTypes_Lookup.txt": _JOIN,
        "SubmissionPropertyType.txt": _PROPERTY, "ApplicationDocs.txt": _DOCS,
    }
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        for name, lines in tables.items():
            zf.writestr(name, "\r\n".join(lines) + "\r\n")
    return RawData(root=root)


@pytest.fixture
def stubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Serve _TEXTS; s6 answers 404; s7 cannot be parsed; one guessed address
    answers with HTML and must not be kept. Requests are counted, sleeps too."""
    cache = tmp_path / "cache"
    cache.mkdir()
    html_guess = "https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2015/000001Orig1s003ltr.pdf"
    calls: list[str] = []

    def fetch_status(url: str, root: Path) -> tuple[Path | None, str]:
        calls.append(url)
        path = letters.label_pdf.cache_path(url, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if url in _TEXTS:
            path.write_bytes(b"%PDF" + _TEXTS[url].encode())
            return path, ""
        if url == f"{_BASE}/s7.pdf":
            path.write_bytes(b"%PDF broken")
            return path, ""
        if url == html_guess:
            path.write_bytes(b"<html>not found</html>")
            return path, ""
        return None, letters.label_pdf.NOT_FOUND

    def extract(path: Path) -> tuple[str, int, int] | None:
        raw = path.read_bytes()
        if raw == b"%PDF broken":
            return None
        return raw[4:].decode(), 1, len(raw)

    monkeypatch.setattr(letters.label_pdf, "fetch_status", fetch_status)
    monkeypatch.setattr(letters.label_pdf, "extract_text", extract)
    monkeypatch.setattr(letters, "_sleep", lambda seconds: calls.append(f"sleep {seconds}"))
    monkeypatch.setattr(letters, "_CALLS", calls, raising=False)
    return cache


def test_every_status_is_reached(data: RawData, stubbed: Path) -> None:
    report = letters.build(data, stubbed, parsers=1)
    by_key = {(r.application, r.submission_no): r for r in report.rows}
    assert by_key[("NDA000001", "1")].status == "ok"
    assert by_key[("NDA000001", "2")].status == "ok"
    assert by_key[("NDA000001", "3")].status == "ok"
    assert by_key[("NDA000001", "4")].status == "scanned"
    assert by_key[("NDA000001", "5")].status == "no_sentence"
    assert by_key[("NDA000001", "6")].status == "fetch_failed"
    assert by_key[("NDA000001", "7")].status == "unreadable"
    assert by_key[("NDA000002", "1")].status == "no_letter"


def test_a_decimal_does_not_end_the_indication(data: RawData, stubbed: Path) -> None:
    row = next(r for r in letters.build(data, stubbed, parsers=1).rows
               if r.submission_no == "1" and r.application == "NDA000001")
    assert row.sentence.endswith("in patients 12 years and older")
    assert row.indication_bearing == "true"


def test_a_guessed_letter_is_marked_and_html_is_discarded(
    data: RawData, stubbed: Path
) -> None:
    report = letters.build(data, stubbed, parsers=1)
    row = next(r for r in report.rows if r.submission_no == "3")
    assert row.url_guessed == "true"
    assert row.letter_url.endswith("000001s003ltr.pdf")
    assert "covid-19" in row.sentence
    assert report.guessed_found == 1
    assert not any(
        p.read_bytes().startswith(b"<html>") for p in stubbed.rglob("*") if p.is_file()
    )


def test_a_bare_for_disease_sentence_is_not_trusted_as_an_indication() -> None:
    """Precision over recall, by instruction: "the use of X for acne vulgaris"
    is an indication, but without the keyword or a purpose phrase the rule
    cannot tell it from "the use of X for subcutaneous injection", so it falls
    back to the label rather than risk overriding it."""
    assert not letters.is_indication(
        "provides for the use of Veltin Gel for acne vulgaris in patients 12 and older"
    )


def test_the_report_renders(data: RawData, stubbed: Path) -> None:
    rendered = letters.render(letters.build(data, stubbed, parsers=1))
    assert "indication-bearing sentences" in rendered and "by era and status" in rendered


def test_requests_are_spaced_out(data: RawData, stubbed: Path) -> None:
    """The unthrottled first run was banned after ~1,500 letters."""
    letters.build(data, stubbed, parsers=1, interval=0.5)
    calls = letters._CALLS  # type: ignore[attr-defined]
    fetches = [c for c in calls if not c.startswith("sleep")]
    assert calls.count("sleep 0.5") == len(fetches) - 1


def test_a_second_run_reads_the_cache_and_asks_nothing(
    data: RawData, stubbed: Path
) -> None:
    letters.build(data, stubbed, parsers=1)
    calls = letters._CALLS  # type: ignore[attr-defined]
    calls.clear()
    report = letters.build(data, stubbed, parsers=1, offline=True)
    assert calls == []
    by_key = {(r.application, r.submission_no): r.status for r in report.rows}
    assert by_key[("NDA000001", "1")] == "ok"
    assert by_key[("NDA000001", "6")] == "not_fetched"


def test_repeated_refusals_stop_the_run_and_mark_the_rest_blocked(
    data: RawData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After BREAKER refusals nothing more is requested, and nothing is called
    missing: a refusal is not evidence that a letter does not exist."""
    asked: list[str] = []

    def refuse(url: str, root: Path) -> tuple[Path | None, str]:
        asked.append(url)
        return None, letters.label_pdf.FORBIDDEN

    monkeypatch.setattr(letters.label_pdf, "fetch_status", refuse)
    monkeypatch.setattr(letters, "_sleep", lambda seconds: None)
    report = letters.build(data, tmp_path / "c", parsers=1)
    assert len(asked) == letters.BREAKER
    statuses = {r.status for r in report.rows}
    assert "fetch_failed" not in statuses
    assert "blocked" in statuses


def test_the_parallel_parser_branch_runs_with_nothing_to_parse(
    data: RawData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``parsers > 1`` takes the process-pool branch. A pool needs picklable
    callables, which rules out the stubbed extractor, so nothing is cached here
    and the branch runs empty: what it proves is that such a run still reports
    every submission rather than crashing."""
    report = letters.build(
        data, tmp_path / "empty", parsers=2, guess_missing=False, offline=True
    )
    assert len(report.rows) == 8
    assert {r.status for r in report.rows} <= {"not_fetched", "no_letter"}
