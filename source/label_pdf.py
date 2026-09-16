"""The label as approved at one submission: fetch it, cache it, read its text.

``ApplicationDocs.txt`` type 2 is the label filed *with* a submission —
``.../drugsatfda_docs/label/2009/017881s010lbl.pdf``, where ``s010`` is the
submission number. That is the point-in-time document this project needs and
DailyMed is not required for: 29,788 of them exist, 4,169 distinct PDFs cover
the 4,416 trial-relevant submissions that have one.

**One PDF can serve several submissions.** ``206843s001s002s003lbl.pdf`` is the
label for three, approved together. The cache is therefore keyed on the URL,
not on the submission, and 4,416 submissions cost 4,169 downloads.

**The stored URLs are ``http://`` and answer nothing.** They must be requested
over ``https://`` with redirects followed, or every file comes back empty —
measured, thirteen for thirteen.

**Some labels are scanned images.** Pre-2006 ones especially: ``_prntlbl`` in
the filename, 49k-161k bytes per page, and no extractable text at all. There is
no OCR here by decision — those labels are also pre-PLR, so they carry no
numbered ``14.N`` section to read even if the pixels were transcribed. They are
detected, counted and skipped.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from source.raw_table import read_rows
from utility.logging import get_logger

_log = get_logger(__name__)

#: ``ApplicationDocsTypeID`` for the label filed with a submission. Type 1 is
#: the approval letter, which states the indication but names no trials.
LABEL_DOC_TYPE: str = "2"

#: Below this many extracted characters a PDF is taken to be a scan. A real
#: label section runs to thousands; the scanned ones measured yielded 0, 0
#: and 98.
MIN_TEXT_CHARS: int = 500

#: Requested on every fetch. The FDA asks that bulk callers identify themselves.
USER_AGENT: str = "insilicom-orange-purple-book (research; mliu@insilicom.com)"

#: Seconds before a download is abandoned. Some labels are 8 MB.
TIMEOUT: int = 90


class LabelDocument(NamedTuple):
    """One submission's archived label, before it is fetched."""

    application: str
    submission_type: str
    submission_no: str
    url: str
    document_date: str

    @property
    def key(self) -> str:
        """The submission this label belongs to."""
        return f"{self.application}|{self.submission_type}|{self.submission_no}"


class FetchedLabel(NamedTuple):
    """A cached PDF and what reading it produced."""

    url: str
    path: Path
    pages: int
    bytes_per_page: int
    text: str

    @property
    def is_scanned(self) -> bool:
        """Whether the PDF carries no extractable text.

        >>> FetchedLabel("u", Path("p"), 20, 49_000, "").is_scanned
        True
        >>> FetchedLabel("u", Path("p"), 20, 10_000, "x" * 5_000).is_scanned
        False
        """
        return len(self.text) < MIN_TEXT_CHARS


def as_https(url: str) -> str:
    """The stored URL, over a scheme that answers and with spaces encoded.

    Two defects in the stored value, both real. The scheme is ``http://``,
    which returns nothing — measured, thirteen for thirteen came back empty.
    And some rows carry a literal space, because one PDF covers two
    applications and the archive names it after both:
    ``020786Orig1s033, 021704Orig1s017lbl.pdf``. An unencoded space raises
    ``http.client.InvalidURL``, which is not an ``OSError`` and so escaped the
    handler and killed a whole run.

    >>> as_https("http://www.accessdata.fda.gov/x.pdf")
    'https://www.accessdata.fda.gov/x.pdf'
    >>> as_https("https://www.accessdata.fda.gov/x.pdf")
    'https://www.accessdata.fda.gov/x.pdf'
    >>> as_https("http://x/a, b.pdf")
    'https://x/a,%20b.pdf'
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return url.replace(" ", "%20")


def read_label_documents(data: RawData) -> Iterator[LabelDocument]:
    """Every type-2 label document recorded against a submission."""
    for row in read_rows(data.application_docs):
        if (row.get("ApplicationDocsTypeID") or "").strip() != LABEL_DOC_TYPE:
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


def cache_path(url: str, root: Path) -> Path:
    """Where a URL's PDF is cached.

    The basename alone collides — several years hold a ``NNNNNNlbl.pdf`` — so
    the name carries a hash of the whole URL. Sharded into 256 directories
    because a single directory of 4,169 files is slow to stat on some
    filesystems and unpleasant to inspect.
    """
    digest = hashlib.sha256(url.encode()).hexdigest()
    return root / digest[:2] / f"{digest}.pdf"


def fetch(url: str, root: Path) -> Path | None:
    """The cached PDF for ``url``, downloading it once. ``None`` if it fails.

    Never refetches: a present, non-empty file is taken as good. The archive is
    static — a label approved in 2009 does not change — so revalidation would
    cost 4,169 round trips for nothing.
    """
    destination = cache_path(url, root)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        as_https(url), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read()
    except Exception as error:
        # Deliberately broad. A single malformed row must not end a 4,169-file
        # run, and the archive produces more than URLError and OSError:
        # InvalidURL for an unencoded space, and decoding errors on a few
        # redirect chains. Every failure is counted and reported.
        _log.warning("%s: %s", url, error)
        return None
    if not payload:
        _log.warning("%s: empty response", url)
        return None
    # Written via a temporary name so an interrupted run cannot leave a
    # half-file that the next run would trust.
    partial = destination.with_suffix(".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    return destination


def extract_text(path: Path) -> tuple[str, int, int] | None:
    """``(text, pages, size)`` for one PDF, or ``None`` if it cannot be opened.

    Split out from :func:`read_pdf` so it can run in a process pool: it takes a
    path and returns plain data, both of which pickle. Parsing is where the
    time goes — measured at ~3.7 s per label against a download that is already
    threaded — and pdfplumber releases no GIL worth sharing, so processes are
    the only thing that helps.
    """
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as error:  # pdfplumber raises a wide variety
        _log.warning("%s: unreadable (%s)", path.name, error)
        return None
    return text, pages, path.stat().st_size


def read_pdf(path: Path, url: str = "") -> FetchedLabel | None:
    """Extract a cached PDF's text. ``None`` when it cannot be opened."""
    got = extract_text(path)
    if got is None:
        return None
    text, pages, size = got
    return FetchedLabel(
        url=url,
        path=path,
        pages=pages,
        bytes_per_page=size // pages if pages else size,
        text=text,
    )


__all__ = [
    "LABEL_DOC_TYPE",
    "MIN_TEXT_CHARS",
    "TIMEOUT",
    "USER_AGENT",
    "FetchedLabel",
    "LabelDocument",
    "as_https",
    "cache_path",
    "extract_text",
    "fetch",
    "read_label_documents",
    "read_pdf",
]
