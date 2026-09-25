"""Tests for locating approval letters (D25)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from config.settings import RawData
from source import approval_letter

_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType\tSubmissionNo"
    "\tApplicationDocsTitle\tApplicationDocsURL\tApplicationDocsDate",
    "1\t1\t021574\tORIG      \t1\t0\thttp://x/appletter/2004/21574ltr.pdf\t2004-10-01 00:00:00",
    # A label, not a letter: skipped.
    "2\t2\t021574\tORIG      \t1\t0\thttp://x/label/2004/21574lbl.pdf\t2004-10-01 00:00:00",
    # A letter row with no address: skipped.
    "3\t1\t021574\tSUPPL     \t2\t0\t\t2005-01-01 00:00:00",
]


def _data(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("ApplicationDocs.txt", "\r\n".join(_DOCS) + "\r\n")
    return RawData(root=root)


def test_only_letters_with_an_address_are_read(tmp_path: Path) -> None:
    docs = list(approval_letter.read_letter_documents(_data(tmp_path)))
    assert len(docs) == 1
    assert docs[0].url.endswith("21574ltr.pdf")
    assert (docs[0].submission_type, docs[0].submission_no) == ("ORIG", "1")
    assert docs[0].document_date == "2004-10-01"


def test_a_pdf_is_recognised_by_its_magic(tmp_path: Path) -> None:
    pdf, html = tmp_path / "a.pdf", tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.4 ...")
    html.write_bytes(b"<!DOCTYPE html>")
    assert approval_letter.is_pdf(pdf)
    assert not approval_letter.is_pdf(html)
    assert not approval_letter.is_pdf(tmp_path / "absent.pdf")
