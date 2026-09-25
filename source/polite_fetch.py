"""Fetch many FDA documents without getting this address banned.

The first unthrottled letter run fetched ~12 documents a second and FDA's
server refused this address (HTTP 403) after 1,494 of them, for about four
hours. Every route that downloads in bulk goes through :func:`fetch_all`:

* **one request at a time, ``interval`` seconds apart;**
* **a circuit breaker** — after ``breaker`` refusals in a row the run stops
  asking, because once the server bans an address every further request is
  wasted and can lengthen the ban; what was not fetched is reported
  ``refused``, not missing, so a later run resumes from the cache;
* **the cache first** — :mod:`source.label_pdf`'s URL-keyed cache is never
  refetched, and ``offline`` reads only the cache and sends nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

from source import label_pdf
from utility.logging import get_logger

_log = get_logger(__name__)

#: Seconds between requests.
REQUEST_INTERVAL: float = 1.0

#: Refusals in a row that stop the run.
BREAKER: int = 5

#: A PDF starts with these bytes.
PDF_MAGIC: bytes = b"%PDF"


class FetchResult(NamedTuple):
    """What a bulk fetch produced."""

    #: URL → cached file, for every URL now in the cache.
    cached: dict[str, Path]
    #: URL → why it has no file: ``not_found``, ``error``, ``empty``, or
    #: ``not_pdf`` when a PDF was required and something else came back.
    failed: dict[str, str]
    #: URLs never fetched because the server refused or the breaker tripped.
    refused: frozenset[str]
    #: Whether the circuit breaker stopped the run.
    tripped: bool


def is_pdf(path: Path) -> bool:
    """Whether a cached file is a PDF at all."""
    try:
        with path.open("rb") as handle:
            return handle.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


def fetch_all(
    urls: Iterable[str],
    root: Path,
    *,
    offline: bool = False,
    interval: float = REQUEST_INTERVAL,
    breaker: int = BREAKER,
    require_pdf: Callable[[str], bool] = lambda url: True,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Fetch every URL once, politely; see the module docstring.

    ``require_pdf`` says which URLs must come back as PDFs; any other answer
    under such a URL (an HTML "not found" page) is deleted from the cache and
    reported ``not_pdf``. A table-of-contents page is fetched with it false.
    """
    cached: dict[str, Path] = {}
    failed: dict[str, str] = {}
    refused: set[str] = set()
    in_a_row = 0
    tripped = asked = False
    for url in dict.fromkeys(urls):
        if label_pdf.is_cached(url, root):
            path: Path | None = label_pdf.cache_path(url, root)
        elif offline or tripped:
            refused.add(url)
            continue
        else:
            if asked:
                sleep(interval)
            asked = True
            path, reason = label_pdf.fetch_status(url, root)
            if reason == label_pdf.FORBIDDEN:
                refused.add(url)
                in_a_row += 1
                if in_a_row >= breaker:
                    tripped = True
                    _log.warning(
                        "%d refusals in a row: the server is refusing this address; "
                        "stopping, the rest stays refused for a later run", in_a_row
                    )
                continue
            in_a_row = 0
            if path is None:
                failed[url] = reason
                continue
        if path is None:  # pragma: no cover - fetch_status pairs None with a reason
            continue
        if require_pdf(url) and not is_pdf(path):
            path.unlink(missing_ok=True)
            failed[url] = "not_pdf"
            continue
        cached[url] = path
    return FetchResult(cached, failed, frozenset(refused), tripped)


__all__ = ["BREAKER", "PDF_MAGIC", "REQUEST_INTERVAL", "FetchResult", "fetch_all", "is_pdf"]
