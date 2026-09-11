"""Read the openFDA JSON downloads: one envelope, many shards.

Every openFDA bulk file is a single zip holding a single pretty-printed JSON
document shaped ``{"meta": {...}, "results": [ ... ]}``. The label download is
14 such zips (1.8 GB compressed, 262,757 records); ``ndc`` and ``drugsfda`` are
one each.

**Why the whole shard is parsed at once.** The largest label shard is 631 MB of
JSON, and the environment has no streaming JSON parser (``ijson`` is not
installed and adding a dependency needs asking). ``json.loads`` on one shard
peaks around 3 GB, which is comfortable here, and each shard is released before
the next is opened — so memory is bounded by the *largest* shard, not the corpus.
The caller sees a flat iterator either way.

**``openfda`` is a key on every record and an empty dict on most.** Measured
across all 14 shards: 87,416 of 262,757 labels (33.3%) have a non-empty block.
So ``if "openfda" in record`` is true almost always and means nothing; the test
has to be on the contents. :func:`openfda_of` does that once, here, rather than
at each call site.

Measured 2026-09-11.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from utility.logging import get_logger

_log = get_logger(__name__)


def read_results(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each record in one openFDA zip. A missing file is silent."""
    if not path.is_file():
        return
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:
            _log.warning("%s: empty archive", path)
            return
        with archive.open(names[0]) as handle:
            document = json.loads(handle.read().decode("utf-8"))
    results = document.get("results")
    if not isinstance(results, list):
        _log.warning("%s: no results array", path)
        return
    for record in results:
        if isinstance(record, dict):
            yield record


def read_shards(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    """Yield every record across several shards, one shard resident at a time."""
    for path in paths:
        yield from read_results(path)


def openfda_of(record: dict[str, Any]) -> dict[str, Any]:
    """The record's ``openfda`` block, or an empty dict.

    The key is present on essentially every record and empty on two thirds of
    labels, so this normalises "absent" and "present but empty" to one thing.

    >>> openfda_of({"openfda": {}})
    {}
    >>> openfda_of({})
    {}
    >>> openfda_of({"openfda": {"unii": ["X"]}})
    {'unii': ['X']}
    """
    block = record.get("openfda")
    return block if isinstance(block, dict) else {}


def first(values: Any) -> str:
    """The first element of an openFDA array field, or ``""``.

    Every openFDA value is an array, including the ones that always hold
    exactly one element.

    >>> first(["NDA019019", "NDA019020"])
    'NDA019019'
    >>> first([])
    ''
    >>> first(None)
    ''
    """
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


__all__ = ["first", "openfda_of", "read_results", "read_shards"]
