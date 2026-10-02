"""Write, and read back, named record tables as gzipped JSON lines.

For tables whose values keep their line breaks — a label's section 14, whose
subsection headings are found line by line — which TSV cannot hold (see
:mod:`dao.tsv_repository`). The output directory is injected, as there; no
connection is opened here or above.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from utility.logging import get_logger

_log = get_logger(__name__)


class JsonlRepository:
    """Named ``<name>.jsonl.gz`` tables in one directory."""

    def __init__(self, out_dir: str | Path) -> None:
        self._out_dir = Path(out_dir)

    def path_for(self, name: str) -> Path:
        """Where a table called ``name`` is written."""
        return self._out_dir / f"{name}.jsonl.gz"

    def save(self, name: str, records: Iterable[Mapping[str, object]]) -> int:
        """Write one table (truncating it) and return how many records it holds."""
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(name)
        count = 0
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
                count += 1
        _log.info("wrote %s (%d records)", path, count)
        return count

    def load(self, name: str) -> list[dict[str, object]]:
        """One table's records; an absent table is empty."""
        path = self.path_for(name)
        if not path.is_file():
            _log.warning("%s: no such table", path)
            return []
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


__all__ = ["JsonlRepository"]
