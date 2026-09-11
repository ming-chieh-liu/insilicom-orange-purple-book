"""Tests for the declared shape of the fda_raw/ tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import TAB, TILDE, DelimitedFile, RawData


@pytest.fixture
def raw() -> RawData:
    """A RawData pointed at a path that need not exist; it opens nothing."""
    return RawData(root=Path("/somewhere/fda_raw"))


def test_building_it_touches_no_disk() -> None:
    """Construction is pure, so main can build it before checking the tree."""
    data = RawData(root=Path("/definitely/not/here"))
    assert data.exists is False


def test_exists_reports_the_tree(tmp_path: Path) -> None:
    assert RawData(root=tmp_path).exists is True
    assert RawData(root=tmp_path / "missing").exists is False


@pytest.mark.parametrize(
    ("attr", "member", "fields", "rows"),
    [
        ("applications", "Applications.txt", 4, 29_323),
        ("products", "Products.txt", 8, 51_742),
        ("submissions", "Submissions.txt", 8, 193_687),
    ],
)
def test_the_drugsfda_tables_are_tab_delimited_zip_members(
    raw: RawData, attr: str, member: str, fields: int, rows: int
) -> None:
    """All 12 live inside drugsatfda.zip and are read without extracting."""
    spec: DelimitedFile = getattr(raw, attr)
    assert spec.member == member
    assert spec.delimiter == TAB
    assert spec.fields == fields
    assert spec.rows == rows
    assert spec.path == raw.drugsfda_zip
    assert spec.path.name == "drugsatfda.zip"


@pytest.mark.parametrize(
    ("attr", "name", "fields", "rows"),
    [
        ("ob_products", "products.txt", 14, 48_664),
        ("ob_patent", "patent.txt", 10, 22_205),
        ("ob_exclusivity", "exclusivity.txt", 5, 2_370),
    ],
)
def test_the_orange_book_files_are_tilde_delimited_plain_files(
    raw: RawData, attr: str, name: str, fields: int, rows: int
) -> None:
    """Already unzipped under orangebook/x/, so no member is set."""
    spec: DelimitedFile = getattr(raw, attr)
    assert spec.member is None
    assert spec.delimiter == TILDE
    assert spec.fields == fields
    assert spec.rows == rows
    assert spec.path.name == name
    assert spec.path.parent.name == "x"


def test_the_unii_list_is_a_tab_delimited_zip_member(raw: RawData) -> None:
    """1,050,022 data rows — README's 1,050,023 counts the header line."""
    spec = raw.unii_names
    assert spec.member == raw.unii_snapshot == "UNII_Names_4Aug2026.txt"
    assert spec.delimiter == TAB
    assert spec.fields == 4
    assert spec.rows == 1_050_022
    assert spec.path == raw.unii_zip


def test_the_snapshot_year_matches_the_snapshot_names(raw: RawData) -> None:
    """Two-digit years resolve against the data on disk, not the clock.

    If a newer download lands, these three move together or the century rule
    starts disagreeing with the file it is reading.
    """
    from utility import fda_date

    assert raw.snapshot_year == fda_date.SNAPSHOT_YEAR == 2026
    assert "2026" in raw.purplebook_snapshot
    assert "2026" in raw.unii_snapshot


def test_the_specs_are_frozen(raw: RawData) -> None:
    """Configuration is injected, never mutated by whatever received it."""
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        raw.snapshot_year = 2027  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        raw.applications.rows = 1  # type: ignore[misc]


def test_every_declared_file_sits_under_the_given_root(raw: RawData) -> None:
    """Nothing reaches outside the tree it was handed."""
    specs = [
        raw.applications, raw.products, raw.submissions,
        raw.ob_products, raw.ob_patent, raw.ob_exclusivity, raw.unii_names,
    ]
    for spec in specs:
        assert raw.root in spec.path.parents
