"""FDA approval letters: which letter belongs to which submission, and where.

``ApplicationDocs.txt`` type 1 is the approval letter FDA sent for a
submission (D25). Its "provides for …" sentence states what *that* approval
covers, which makes it the per-submission indication source the label's
cumulative section 1 cannot be. Letters name almost no evidence trials — their
study mentions are postmarketing requirements — so they are an indication
source only.

**Coverage is era-bound.** Letters exist for 95.2% of in-scope submissions
approved since 1998 and for almost none before: of 37,283 letter rows, 2
predate 1998.

**Some letters exist but are not indexed.** About half of a sampled six
in-scope submissions without an ``ApplicationDocs`` letter row had one at the
standard address. :func:`guessed_urls` builds those addresses; a guessed letter
is marked as such wherever it is used.

Fetching and caching are :mod:`source.label_pdf`'s, which are URL-agnostic;
letters keep their own cache root so the two archives never mix.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from config.settings import RawData
from source.label_pdf import LabelDocument
from source.raw_table import read_rows

#: ``ApplicationDocsTypeID`` for the approval letter.
LETTER_DOC_TYPE: str = "1"

#: Where letters are published; guessed addresses are built under it.
LETTER_ROOT: str = "https://www.accessdata.fda.gov/drugsatfda_docs/appletter"

#: The first year FDA published letters in bulk. Guessing before it is waste.
FIRST_LETTER_YEAR: int = 1998

#: A PDF file starts with these bytes; anything else cached under a letter URL
#: is an HTML error page or a stray document.
PDF_MAGIC: bytes = b"%PDF"


def read_letter_documents(data: RawData) -> Iterator[LabelDocument]:
    """Every type-1 letter document recorded against a submission.

    Yields :class:`~source.label_pdf.LabelDocument`, whose fields — application,
    submission, URL, date — describe any per-submission document.
    """
    for row in read_rows(data.application_docs):
        if (row.get("ApplicationDocsTypeID") or "").strip() != LETTER_DOC_TYPE:
            continue
        url = (row.get("ApplicationDocsURL") or "").strip()
        if not url:
            continue
        yield LabelDocument(
            application=(row.get("ApplNo") or "").strip(),
            submission_type=(row.get("SubmissionType") or "").strip(),
            submission_no=(row.get("SubmissionNo") or "").strip(),
            url=url,
            document_date=(row.get("ApplicationDocsDate") or "").strip()[:10],
        )


def guessed_urls(
    digits: str, submission_type: str, submission_no: str, approval_date: str
) -> list[str]:
    """Standard addresses a missing letter may sit at, most likely first.

    An original is filed as supplement 000. Both the current naming and the
    one used before about 2011 are tried.

    >>> guessed_urls("022141", "ORIG", "1", "2018-02-28")[0]
    'https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2018/022141Orig1s000ltr.pdf'
    >>> guessed_urls("761034", "SUPPL", "58", "2025-10-02")[1]
    'https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2025/761034s058ltr.pdf'
    >>> guessed_urls("020001", "ORIG", "1", "1995-01-01")
    []
    >>> guessed_urls("020001", "SUPPL", "x", "2010-01-01")
    []
    """
    year = approval_date[:4]
    if not (year.isdigit() and int(year) >= FIRST_LETTER_YEAR):
        return []
    if submission_type.strip() == "ORIG":
        number = "000"
    elif submission_no.strip().isdigit():
        number = f"{int(submission_no):03d}"
    else:
        return []
    return [
        f"{LETTER_ROOT}/{year}/{digits}Orig1s{number}ltr.pdf",
        f"{LETTER_ROOT}/{year}/{digits}s{number}ltr.pdf",
    ]


def is_pdf(path: Path) -> bool:
    """Whether a cached file is a PDF at all.

    A guessed address can answer with an HTML page, which the cache would
    otherwise keep and every later run would trust.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


__all__ = [
    "FIRST_LETTER_YEAR",
    "LETTER_DOC_TYPE",
    "LETTER_ROOT",
    "PDF_MAGIC",
    "guessed_urls",
    "is_pdf",
    "read_letter_documents",
]
