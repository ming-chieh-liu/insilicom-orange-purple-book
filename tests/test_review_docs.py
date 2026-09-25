"""Tests for locating FDA review documents (D26)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from config.settings import RawData
from source import review_docs

_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType\tSubmissionNo"
    "\tApplicationDocsTitle\tApplicationDocsURL\tApplicationDocsDate",
    "1\t21\t204061\tORIG      \t1\t0\thttp://x/nda/2013/204061Orig1s000SumR.pdf\t2013-03-28 00:00:00",
    "2\t3\t204061\tORIG      \t1\t0\thttp://x/nda/2013/204061Orig1s000TOC.html\t2013-03-28 00:00:00",
    "3\t20\t204061\tSUPPL     \t5\t0\thttp://x/p.pdf\t2015-01-01 00:00:00",
    # A pediatric written request: a request for future studies, never read.
    "4\t23\t204061\tSUPPL     \t5\t0\thttp://x/wr.pdf\t2015-01-01 00:00:00",
    # A label is not a review.
    "5\t2\t204061\tORIG      \t1\t0\thttp://x/lbl.pdf\t2013-03-28 00:00:00",
    "6\t21\t204061\tORIG      \t2\t0\t\t2013-03-28 00:00:00",
]


def test_only_review_types_with_an_address_are_read(tmp_path: Path) -> None:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("ApplicationDocs.txt", "\r\n".join(_DOCS) + "\r\n")
    docs = list(review_docs.read_review_documents(RawData(root=root)))
    assert [d.label for d in docs] == ["Summary Review", "Review", "Pediatric Medical Review"]
    assert docs[0].document_date == "2013-03-28" and docs[1].submission_type == "ORIG"


def test_a_toc_page_with_multi_part_files_and_the_older_clin_pharm_key() -> None:
    html = ('var pdfFilenames = {approv: "x_approv.PDF", medr: ["x_medr_P1.pdf", "x_medr_P2.pdf"], '
            'clinpharmr: "x_biopharmr.pdf", biopharmr: "y.pdf", otherr: ["x_corres.pdf"]};')
    assert review_docs.toc_components(html) == [
        ("MedR", "x_medr_P1.pdf"), ("MedR", "x_medr_P2.pdf"),
        ("ClinPharmR", "x_biopharmr.pdf"), ("ClinPharmR", "y.pdf"),
    ]
