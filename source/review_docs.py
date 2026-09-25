"""FDA review documents: which ones belong to a submission, and their parts.

``ApplicationDocs.txt`` lists reviews under several document types (D26). A
**Summary Review** (type 21) is one PDF. A **Review** (type 3) is, from about
2008, a table-of-contents page whose component PDFs — medical, statistical,
multidiscipline, clinical pharmacology … — are named by JavaScript, not by
links: the page embeds either a ``var pdfFilenames = {…}`` map (older pages,
multi-part files included) or ``pdfBaseName`` plus ``pdfFiles`` existence
flags (newer pages). Before 2008 a Review is usually one PDF, often scanned.

Pediatric reviews are in scope; **Pediatric Written Requests and their
amendments are not** — they request studies still to be done, so every id in
them would be a study that could not yet have supported anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from config.settings import RawData
from source.raw_table import read_rows

#: ``ApplicationDocsTypeID`` → label, for the review types read (D26).
REVIEW_DOC_TYPES: dict[str, str] = {
    "21": "Summary Review",
    "3": "Review",
    "20": "Pediatric Medical Review",
    "17": "Pediatric Statistical Review",
    "18": "Pediatric Clinical Pharmacology Review",
    "57": "Pediatric CDTL Review",
    "58": "Pediatric Medical Addendum",
    "59": "Pediatric Medical Secondary Review",
    "60": "Pediatric DD Summary Review",
    "61": "Pediatric Addendum",
    "62": "Pediatric Clinical Pharmacology Addendum",
}

#: Table-of-contents components that discuss studies, in the page's casing.
COMPONENTS: tuple[str, ...] = (
    "SumR", "MultidisciplineR", "IntegratedR", "MedR", "StatR", "ClinPharmR", "CrossR",
)

#: ``pdfFilenames`` keys (lower-cased) → component; older pages call the
#: clinical pharmacology review ``biopharmr``.
_MAP_KEYS: dict[str, str] = {
    "sumr": "SumR",
    "multidisciplinr": "MultidisciplineR",
    "multidiscipliner": "MultidisciplineR",
    "integratedr": "IntegratedR",
    "medr": "MedR",
    "statr": "StatR",
    "clinpharmr": "ClinPharmR",
    "biopharmr": "ClinPharmR",
    "crossr": "CrossR",
}

_FILENAMES = re.compile(r"var\s+pdfFilenames\s*=\s*\{(.*?)\};", re.S)
_ENTRY = re.compile(r"(\w+)\s*:\s*(\[[^\]]*\]|\"[^\"]*\")")
_BASE = re.compile(r"pdfBaseName\s*=\s*[\"']([^\"']+)[\"']")
_FLAGS = re.compile(r"var\s+pdfFiles\s*=\s*\{(.*?)\};", re.S)


class ReviewDocument(NamedTuple):
    """One review document row, before it is fetched."""

    application: str
    submission_type: str
    submission_no: str
    doc_type: str
    url: str
    document_date: str

    @property
    def label(self) -> str:
        """The document type's name.

        >>> ReviewDocument("1", "ORIG", "1", "21", "u", "").label
        'Summary Review'
        """
        return REVIEW_DOC_TYPES.get(self.doc_type, self.doc_type)


def read_review_documents(data: RawData) -> Iterator[ReviewDocument]:
    """Every in-scope review document recorded against a submission."""
    for row in read_rows(data.application_docs):
        doc_type = (row.get("ApplicationDocsTypeID") or "").strip()
        url = (row.get("ApplicationDocsURL") or "").strip()
        if doc_type not in REVIEW_DOC_TYPES or not url:
            continue
        yield ReviewDocument(
            application=(row.get("ApplNo") or "").strip(),
            submission_type=(row.get("SubmissionType") or "").strip(),
            submission_no=(row.get("SubmissionNo") or "").strip(),
            doc_type=doc_type,
            url=url,
            document_date=(row.get("ApplicationDocsDate") or "").strip()[:10],
        )


def is_toc(url: str) -> bool:
    """Whether a review URL is a table-of-contents page rather than a PDF.

    >>> is_toc("https://x/nda/2018/761116Orig1s000TOC.html"), is_toc("https://x/a.pdf")
    (True, False)
    """
    return url.lower().rsplit(".", 1)[-1] in ("html", "htm", "cfm")


def toc_components(html: str) -> list[tuple[str, str]]:
    """``(component, filename)`` for the study-bearing parts a TOC page lists.

    >>> toc_components('var pdfFilenames = {medr: ["a_medr_P1.pdf", "a_medr_P2.pdf"], '
    ...                'lbl: "a_lbl.pdf", statr: "a_statr.pdf"};')
    [('MedR', 'a_medr_P1.pdf'), ('MedR', 'a_medr_P2.pdf'), ('StatR', 'a_statr.pdf')]
    >>> toc_components('pdfBaseName = "761116Orig1s000"; var pdfFiles = {sumR: 1, medR: 0, '
    ...                'statR: 1, lbl: 1};')
    [('SumR', '761116Orig1s000SumR.pdf'), ('StatR', '761116Orig1s000StatR.pdf')]
    >>> toc_components("<html>no map here</html>")
    []
    """
    listed = _FILENAMES.search(html)
    if listed:
        out: list[tuple[str, str]] = []
        for key, value in _ENTRY.findall(listed.group(1)):
            component = _MAP_KEYS.get(key.lower())
            if component:
                out.extend((component, name) for name in re.findall(r"\"([^\"]+)\"", value))
        return out
    base, flags = _BASE.search(html), _FLAGS.search(html)
    if not (base and flags):
        return []
    present = {key.lower(): value for key, value in re.findall(r"(\w+)\s*:\s*(\d)", flags.group(1))}
    return [(c, f"{base.group(1)}{c}.pdf") for c in COMPONENTS if present.get(c.lower()) == "1"]


def component_url(toc_url: str, filename: str) -> str:
    """A component's address: the TOC page's directory plus the file name.

    >>> component_url("https://x/nda/2018/761116Orig1s000TOC.html", "761116Orig1s000MedR.pdf")
    'https://x/nda/2018/761116Orig1s000MedR.pdf'
    """
    return toc_url.rsplit("/", 1)[0] + "/" + filename


__all__ = [
    "COMPONENTS",
    "REVIEW_DOC_TYPES",
    "ReviewDocument",
    "component_url",
    "is_toc",
    "read_review_documents",
    "toc_components",
]
