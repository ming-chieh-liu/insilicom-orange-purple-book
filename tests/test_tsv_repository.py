"""Tests for writing the mapping tables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

import pytest

from dao.tsv_repository import DELIMITER, TsvRepository


class Row(NamedTuple):
    a: str
    b: str


def test_the_header_is_written_then_the_rows(tmp_path: Path) -> None:
    repo = TsvRepository(tmp_path)
    count = repo.save("t", ("a", "b"), [Row("1", "2"), Row("3", "4")])
    assert count == 2
    assert repo.path_for("t").read_text().splitlines() == ["a\tb", "1\t2", "3\t4"]


def test_the_separator_is_a_tab(tmp_path: Path) -> None:
    """FDA values contain commas freely; they contain no tabs.

    'AEROSOL, FOAM;RECTAL' and 'CELLTRION, Inc.' are real values, so a CSV
    would need quoting on a large fraction of rows.
    """
    repo = TsvRepository(tmp_path)
    repo.save("t", ("a", "b"), [Row("AEROSOL, FOAM;RECTAL", "CELLTRION, Inc.")])
    line = repo.path_for("t").read_text().splitlines()[1]
    assert DELIMITER == "\t"
    assert line == "AEROSOL, FOAM;RECTAL\tCELLTRION, Inc."
    assert line.split(DELIMITER) == ["AEROSOL, FOAM;RECTAL", "CELLTRION, Inc."]


def test_the_output_directory_is_created(tmp_path: Path) -> None:
    repo = TsvRepository(tmp_path / "deep" / "nested")
    repo.save("t", ("a", "b"), [Row("1", "2")])
    assert repo.path_for("t").is_file()


def test_an_empty_table_still_writes_its_header(tmp_path: Path) -> None:
    """A reader must be able to tell 'no rows' from 'file never written'."""
    repo = TsvRepository(tmp_path)
    assert repo.save("t", ("a", "b"), []) == 0
    assert repo.path_for("t").read_text() == "a\tb\n"


def test_a_rerun_truncates_rather_than_appends(tmp_path: Path) -> None:
    """The tables are wholly derived from fda_raw/, so a re-run reproduces them."""
    repo = TsvRepository(tmp_path)
    repo.save("t", ("a", "b"), [Row("1", "2"), Row("3", "4")])
    repo.save("t", ("a", "b"), [Row("5", "6")])
    assert repo.path_for("t").read_text().splitlines() == ["a\tb", "5\t6"]


@pytest.mark.parametrize("char", ["\t", "\n", "\r"])
def test_a_value_containing_the_separator_is_neutralised_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, char: str
) -> None:
    """The format has no quoting, so an embedded tab would shift every later
    column of that row — the exact defect the Purple Book demonstrates."""
    repo = TsvRepository(tmp_path)
    with caplog.at_level(logging.WARNING):
        repo.save("t", ("a", "b"), [Row(f"x{char}y", "z")])
    lines = repo.path_for("t").read_text().splitlines()
    assert lines == ["a\tb", "x y\tz"]
    assert len(caplog.records) == 1


def test_a_none_value_becomes_an_empty_field(tmp_path: Path) -> None:
    """An absent value is empty, not the string 'None'."""
    repo = TsvRepository(tmp_path)
    repo.save("t", ("a", "b"), [Row(None, "z")])  # type: ignore[arg-type]
    assert repo.path_for("t").read_text().splitlines()[1] == "\tz"


def test_the_output_directory_is_injected_not_computed(tmp_path: Path) -> None:
    """main is the only place that decides where output goes."""
    repo = TsvRepository(tmp_path / "chosen")
    assert repo.out_dir == tmp_path / "chosen"
    assert repo.path_for("drugs") == tmp_path / "chosen" / "drugs.tsv"


def test_it_accepts_a_string_path(tmp_path: Path) -> None:
    """argparse hands main a string, not a Path."""
    repo = TsvRepository(str(tmp_path))
    repo.save("t", ("a", "b"), [Row("1", "2")])
    assert (tmp_path / "t.tsv").is_file()


def test_no_database_connection_is_opened_anywhere_in_this_module() -> None:
    """The DB rules bind the first task that stores anything; this is files.

    When the target becomes Mongo, a sibling repository implements the same
    save against a pooled client injected once per process, and no service
    above changes.
    """
    import dao.tsv_repository as module

    source = Path(module.__file__).read_text()
    for forbidden in ("pymongo", "MongoClient", "psycopg", "sqlite3", "connect("):
        assert forbidden not in source


def test_a_saved_table_loads_back_by_column(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repository = TsvRepository(tmp_path)
    repository.save("t", ("a", "b"), [Row("1", "x \"quoted\"")])
    assert repository.load("t") == [{"a": "1", "b": 'x "quoted"'}]
    with caplog.at_level(logging.WARNING):
        assert repository.load("absent") == []
    assert "no such table" in caplog.text
