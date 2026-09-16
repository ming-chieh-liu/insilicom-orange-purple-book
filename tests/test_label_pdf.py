"""Tests for fetching and caching the label archived with a submission."""

from __future__ import annotations

import io
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

from config.settings import RawData
from source import label_pdf

_DOCS = [
    "ApplicationDocsID\tApplicationDocsTypeID\tApplNo\tSubmissionType"
    "\tSubmissionNo\tApplicationDocsTitle\tApplicationDocsURL\tApplicationDocsDate",
    # Type 2 is the label filed with the submission — what this project needs.
    "1\t2\t017881\tSUPPL     \t10\t0"
    "\thttp://www.accessdata.fda.gov/drugsatfda_docs/label/2009/017881s010lbl.pdf"
    "\t2009-04-01 00:00:00",
    # Type 1 is the approval letter: states the indication, names no trials.
    "2\t1\t017881\tSUPPL     \t10\t0"
    "\thttp://www.accessdata.fda.gov/drugsatfda_docs/appletter/2009/1ltr.pdf"
    "\t2009-04-01 00:00:00",
    # One PDF serving three submissions, as the archive really does.
    "3\t2\t206843\tSUPPL     \t1\t0"
    "\thttp://www.accessdata.fda.gov/drugsatfda_docs/label/2016/206843s001s002s003lbl.pdf"
    "\t2016-02-05 00:00:00",
    # A type-2 row with no URL: nothing to fetch.
    "4\t2\t000001\tORIG      \t1\t0\t\t2001-01-01 00:00:00",
]


@pytest.fixture
def corpus(tmp_path: Path) -> RawData:
    root = tmp_path / "fda_raw"
    (root / "drugsfda").mkdir(parents=True)
    with zipfile.ZipFile(root / "drugsfda" / "drugsatfda.zip", "w") as zf:
        zf.writestr("ApplicationDocs.txt", "\r\n".join(_DOCS) + "\r\n")
    return RawData(root=root)


def test_only_label_documents_are_read(corpus: RawData) -> None:
    urls = [d.url for d in label_pdf.read_label_documents(corpus)]
    assert all("label/" in url for url in urls)
    assert not any("appletter" in url for url in urls)


def test_a_row_with_no_url_is_skipped(corpus: RawData) -> None:
    assert len(list(label_pdf.read_label_documents(corpus))) == 2


def test_the_submission_key_is_built(corpus: RawData) -> None:
    first = next(iter(label_pdf.read_label_documents(corpus)))
    assert first.key == "017881|SUPPL|10"


def test_the_document_date_is_truncated_to_a_date(corpus: RawData) -> None:
    first = next(iter(label_pdf.read_label_documents(corpus)))
    assert first.document_date == "2009-04-01"


def test_stored_urls_are_upgraded_to_https() -> None:
    """The http:// URLs in ApplicationDocs answer nothing — measured, thirteen
    for thirteen came back empty."""
    assert label_pdf.as_https("http://x/y.pdf") == "https://x/y.pdf"


def test_an_https_url_is_left_alone() -> None:
    assert label_pdf.as_https("https://x/y.pdf") == "https://x/y.pdf"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_two_urls_sharing_a_basename_do_not_collide(tmp_path: Path) -> None:
    """Several years hold a file called NNNNNNlbl.pdf."""
    one = label_pdf.cache_path("https://x/2009/017881lbl.pdf", tmp_path)
    two = label_pdf.cache_path("https://x/2011/017881lbl.pdf", tmp_path)
    assert one != two


def test_the_cache_path_is_stable(tmp_path: Path) -> None:
    url = "https://x/y.pdf"
    assert label_pdf.cache_path(url, tmp_path) == label_pdf.cache_path(url, tmp_path)


def test_a_cached_file_is_not_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive is static: a label approved in 2009 does not change, so
    revalidation would cost 4,169 round trips for nothing."""
    url = "https://x/y.pdf"
    destination = label_pdf.cache_path(url, tmp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4 cached")

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("should not have been fetched")

    monkeypatch.setattr(label_pdf.urllib.request, "urlopen", _explode)
    assert label_pdf.fetch(url, tmp_path) == destination


def _urlopen(payload: bytes) -> Any:
    class _Response:
        def read(self) -> bytes:
            return payload

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def _open(_request: Any, timeout: int = 0) -> Any:
        return _Response()

    return _open


def test_a_download_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        label_pdf.urllib.request, "urlopen", _urlopen(b"%PDF-1.4 body")
    )
    got = label_pdf.fetch("https://x/y.pdf", tmp_path)
    assert got is not None and got.read_bytes() == b"%PDF-1.4 body"


def test_no_partial_file_survives_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted run must not leave a half-file the next run would trust."""
    monkeypatch.setattr(label_pdf.urllib.request, "urlopen", _urlopen(b"%PDF-1.4"))
    label_pdf.fetch("https://x/y.pdf", tmp_path)
    assert list(tmp_path.rglob("*.part")) == []


def test_an_empty_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(label_pdf.urllib.request, "urlopen", _urlopen(b""))
    assert label_pdf.fetch("https://x/y.pdf", tmp_path) is None


def test_a_network_error_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unreachable label must not cost the other 4,168."""

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(label_pdf.urllib.request, "urlopen", _fail)
    assert label_pdf.fetch("https://x/y.pdf", tmp_path) is None


def test_the_user_agent_identifies_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def _open(request: Any, timeout: int = 0) -> Any:
        seen.update(request.headers)
        return _urlopen(b"%PDF-1.4")(request, timeout=timeout)

    monkeypatch.setattr(label_pdf.urllib.request, "urlopen", _open)
    label_pdf.fetch("https://x/y.pdf", tmp_path)
    assert "insilicom" in str(seen).lower()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_an_unopenable_pdf_is_reported_not_raised(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    assert label_pdf.extract_text(broken) is None
    assert label_pdf.read_pdf(broken) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", True), ("x" * 99, True), ("x" * 5_000, False)],
)
def test_a_pdf_with_no_text_is_a_scan(text: str, expected: bool) -> None:
    """Pre-2006 labels are images. They are also pre-PLR, so OCR would buy a
    section that is not numbered anyway."""
    label = label_pdf.FetchedLabel("u", Path("p"), 20, 49_000, text)
    assert label.is_scanned is expected


def test_bytes_per_page_survives_a_zero_page_pdf() -> None:
    assert label_pdf.FetchedLabel("u", Path("p"), 0, 100, "").bytes_per_page == 100
