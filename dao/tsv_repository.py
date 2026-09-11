"""Write the mapping tables as TSV. The only module that touches the output tree.

**TSV, not CSV, and that is a data decision.** FDA values contain commas
freely — ``AEROSOL, FOAM;RECTAL``, ``CARBON DIOXIDE, USP``, ``CELLTRION,
Inc.`` — so a comma-separated file would need quoting on a large fraction of
rows, and the Purple Book already demonstrates what happens when a consumer
splits such a file naively. Tabs appear in none of the values we emit, so a
tab-separated file needs no quoting at all and stays greppable and diffable.

**Files now, a database later, and the seam is here.** Nothing above this
module knows an output format; the service returns rows and this writes them.
When these tables need to live in Mongo, a sibling repository implements the
same ``save`` against a pooled client injected once per process, and no service
changes. No connection is opened in this module or anywhere above it.

The output directory is injected rather than computed, so tests write to
``tmp_path`` and nothing in the package can be surprised by where its data
went.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: The separator. See the module docstring for why it is not a comma.
DELIMITER: str = "\t"

#: What a value may not contain, because the format has no quoting. A value
#: carrying one is written with it replaced by a space and a warning raised —
#: silently emitting it would shift every later column for that row.
FORBIDDEN: tuple[str, ...] = ("\t", "\n", "\r")


class TsvRepository:
    """Write named tables into one directory."""

    def __init__(self, out_dir: str | Path) -> None:
        self._out_dir = Path(out_dir)

    @property
    def out_dir(self) -> Path:
        return self._out_dir

    def path_for(self, name: str) -> Path:
        """Where a table called ``name`` is written."""
        return self._out_dir / f"{name}.tsv"

    def _clean(self, value: object, name: str) -> str:
        text = "" if value is None else str(value)
        for char in FORBIDDEN:
            if char in text:
                _log.warning("%s: value contains %r, replaced with a space", name, char)
                text = text.replace(char, " ")
        return text

    def save(self, name: str, header: Sequence[str], rows: Iterable[NamedTuple]) -> int:
        """Write one table and return how many data rows it holds.

        The file is truncated: these tables are wholly derived from
        ``fda_raw/``, so a re-run reproduces them and appending would only
        duplicate.
        """
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(name)
        count = 0
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(DELIMITER.join(header) + "\n")
            for row in rows:
                handle.write(
                    DELIMITER.join(self._clean(v, name) for v in tuple(row)) + "\n"
                )
                count += 1
        _log.info("wrote %s (%d rows)", path, count)
        return count


__all__ = ["DELIMITER", "FORBIDDEN", "TsvRepository"]
