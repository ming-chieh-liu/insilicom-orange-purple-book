"""Which BLAs are biosimilars, from the Purple Book (D49).

A biosimilar is a licence the Purple Book types ``351(k) Biosimilar`` or
``351(k) Interchangeable``. The four-letter suffix is **not** a biosimilar
marker: since 2017 the FDA gives one to every new biologic (ravulizumab-cwvz,
aducanumab-avwa), and keying on the stripped name's MeSH id also caught the
reference products that share it (Humira, Remicade, Herceptin). Measured
2026-10-04: of 230 applications the suffix rule flagged, 142 were 351(a).

The file is read with a real CSV reader, positionally: two title rows precede
the data, the type is written ``351(k) Biosimilar`` (never bare ``351(k)``),
and nulls are single spaces (see the Purple Book parsing traps).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

#: Column positions in the Purple Book data rows.
BLA_NUMBER, BLA_TYPE = 2, 5


class _HasRoot(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def purplebook_snapshot(self) -> str: ...


def snapshot_path(data: _HasRoot) -> Path:
    """Where the Purple Book snapshot sits in the ``fda_raw/`` tree.

    >>> class D: root, purplebook_snapshot = Path("fda_raw"), "pb.csv"
    >>> snapshot_path(D()).as_posix()
    'fda_raw/purplebook/pb.csv'
    """
    return data.root / "purplebook" / data.purplebook_snapshot


def biosimilar_applications(path: Path) -> frozenset[str]:
    """``BLA`` + six digits for every licence typed 351(k); an absent file is empty.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp(); p = Path(d) / "pb.csv"
    >>> _ = p.write_text("Report title,,,,,\\n,,,,,\\n"
    ...     'U,"CELLTRION, Inc.",761399,Omlyclo,omalizumab-igec,351(k) Interchangeable\\n'
    ...     "N,Genentech,103705,Rituxan,rituximab,351(a)\\n", encoding="utf-8")
    >>> sorted(biosimilar_applications(p))
    ['BLA761399']
    >>> biosimilar_applications(Path(d) / "absent.csv")
    frozenset()
    """
    if not path.is_file():
        return frozenset()
    out: set[str] = set()
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) <= BLA_TYPE:
                continue
            number, kind = row[BLA_NUMBER].strip(), row[BLA_TYPE].strip()
            if number.isdigit() and kind.startswith("351(k)"):
                out.add("BLA" + number.zfill(6))
    return frozenset(out)


__all__ = ["BLA_NUMBER", "BLA_TYPE", "biosimilar_applications", "snapshot_path"]
