"""Where the FDA bulk data sits, and how each file is shaped. No logic.

Built once in ``main`` and injected into whatever needs it, so nothing below
this module has to know a path or guess a delimiter. Every field here is a
property of the snapshot on disk, measured 2026-09-11 — if a newer download
changes a member name or a row count, this file is the one place it changes.

The row counts are carried deliberately. They are what the reader's field-count
guard and the census's assertions check against, which is how a silently
truncated or re-formatted download gets caught instead of quietly producing
smaller numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Tab, for the Drugs@FDA raw tables and the UNII names list.
TAB: str = "\t"

#: Tilde, for the three Orange Book text files. Verified never to appear
#: inside a value: every row has exactly its declared field count.
TILDE: str = "~"


@dataclass(frozen=True)
class DelimitedFile:
    """One delimited table, wherever it lives.

    ``member`` is set when the file is inside a zip and read from it directly;
    ``path`` is then the zip. ``fields`` is the declared column count, which the
    reader uses to reject a ragged row rather than mis-align it.
    """

    path: Path
    delimiter: str
    fields: int
    rows: int
    member: str | None = None


@dataclass(frozen=True)
class RawData:
    """The ``fda_raw/`` tree: what is in it, and what shape each file is."""

    root: Path

    #: Snapshot identities, so a stale download is visible rather than assumed.
    purplebook_snapshot: str = "purplebook-2026-08.csv"
    unii_snapshot: str = "UNII_Names_4Aug2026.txt"

    #: The year two-digit dates are resolved against. Matches the snapshots
    #: above and ``utility.fda_date.SNAPSHOT_YEAR``; not read from the clock.
    snapshot_year: int = 2026

    #: The Drugs@FDA raw tables, inside ``drugsatfda.zip``. Tab-delimited,
    #: CRLF, header present, no quoting. Authoritative over the openFDA JSON,
    #: which is missing application 206627.
    drugsfda_zip: Path = field(init=False)
    applications: DelimitedFile = field(init=False)
    products: DelimitedFile = field(init=False)
    submissions: DelimitedFile = field(init=False)

    #: The Orange Book text files, already unzipped under ``orangebook/x/``.
    #: Tilde-delimited, CRLF, header present. ``products.txt`` has **no
    #: trailing newline**, so 48,664 data rows is correct and ``wc -l``
    #: undercounts by one.
    ob_products: DelimitedFile = field(init=False)
    ob_patent: DelimitedFile = field(init=False)
    ob_exclusivity: DelimitedFile = field(init=False)

    #: The UNII registry, inside ``UNIIs.zip``. Tab-delimited and **LF-only**,
    #: unlike every FDA file above. 1,050,022 data rows; line 1 is the header.
    #: Names are stored mixed case, so matching must case-fold.
    unii_zip: Path = field(init=False)
    unii_names: DelimitedFile = field(init=False)

    def __post_init__(self) -> None:
        drugsfda_zip = self.root / "drugsfda" / "drugsatfda.zip"
        unii_zip = self.root / "unii" / "UNIIs.zip"
        orangebook = self.root / "orangebook" / "x"
        object.__setattr__(self, "drugsfda_zip", drugsfda_zip)
        object.__setattr__(self, "unii_zip", unii_zip)

        object.__setattr__(
            self, "applications",
            DelimitedFile(drugsfda_zip, TAB, 4, 29_323, "Applications.txt"),
        )
        object.__setattr__(
            self, "products",
            DelimitedFile(drugsfda_zip, TAB, 8, 51_742, "Products.txt"),
        )
        object.__setattr__(
            self, "submissions",
            DelimitedFile(drugsfda_zip, TAB, 8, 193_687, "Submissions.txt"),
        )
        object.__setattr__(
            self, "ob_products",
            DelimitedFile(orangebook / "products.txt", TILDE, 14, 48_664),
        )
        object.__setattr__(
            self, "ob_patent",
            DelimitedFile(orangebook / "patent.txt", TILDE, 10, 22_205),
        )
        object.__setattr__(
            self, "ob_exclusivity",
            DelimitedFile(orangebook / "exclusivity.txt", TILDE, 5, 2_370),
        )
        object.__setattr__(
            self, "unii_names",
            DelimitedFile(unii_zip, TAB, 4, 1_050_022, self.unii_snapshot),
        )

    @property
    def exists(self) -> bool:
        """Whether the bulk tree is present. It is gitignored and rebuildable."""
        return self.root.is_dir()


__all__ = ["TAB", "TILDE", "DelimitedFile", "RawData"]
