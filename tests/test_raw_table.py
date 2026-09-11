"""Tests for the delimited-table reader: newlines, encodings, ragged rows."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

from config.settings import TAB, TILDE, DelimitedFile
from source import raw_table


def _spec(path: Path, delimiter: str = TILDE, fields: int = 2) -> DelimitedFile:
    return DelimitedFile(path, delimiter, fields, rows=0)


def test_a_crlf_table_leaves_no_carriage_return_on_the_last_column(
    tmp_path: Path,
) -> None:
    """Every Orange Book and Drugs@FDA .txt is CRLF.

    The failure this exists for is a last column of 'y\\r', which then travels
    into every join that uses SponsorName or Applicant_Full_Name.
    """
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"A~B\r\nx~y\r\np~q\r\n")
    rows = list(raw_table.read_rows(_spec(path)))
    assert rows == [{"A": "x", "B": "y"}, {"A": "p", "B": "q"}]


def test_a_table_with_no_trailing_newline_keeps_its_last_row(tmp_path: Path) -> None:
    """orangebook/x/products.txt ends mid-row; 48,664 data rows is correct."""
    path = tmp_path / "notrail.txt"
    path.write_bytes(b"A~B\r\nx~y\r\np~q")
    rows = list(raw_table.read_rows(_spec(path)))
    assert len(rows) == 2
    assert rows[-1] == {"A": "p", "B": "q"}


def test_an_lf_only_table_reads_the_same_way(tmp_path: Path) -> None:
    """UNII_Names is LF-only while every FDA file is CRLF: one reader, no flag."""
    path = tmp_path / "lf.txt"
    path.write_bytes(b"A\tB\nx\ty\n")
    assert list(raw_table.read_rows(_spec(path, TAB))) == [{"A": "x", "B": "y"}]


def test_cp1252_bytes_are_recovered_after_utf8_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Submissions.txt holds 15 en-dashes and 3 apostrophes as cp1252 bytes.

    A strict UTF-8 open crashes on 2 of the 12 drugsatfda tables.
    """
    path = tmp_path / "cp1252.txt"
    path.write_bytes(b"A\tB\r\nx\t\x96y\r\n")
    with caplog.at_level(logging.WARNING):
        rows = list(raw_table.read_rows(_spec(path, TAB)))
    assert rows == [{"A": "x", "B": "–y"}]


def test_valid_utf8_is_not_mangled_by_the_fallback(tmp_path: Path) -> None:
    """UNII_Names is 130,797 non-ASCII bytes of real UTF-8.

    Trying cp1252 first would silently corrupt every one of them, which is why
    the order in _ENCODINGS is load-bearing rather than arbitrary.
    """
    path = tmp_path / "utf8.txt"
    path.write_bytes("A\tB\nx\tβ-carotene\n".encode())
    assert list(raw_table.read_rows(_spec(path, TAB))) == [
        {"A": "x", "B": "β-carotene"}
    ]


def test_a_ragged_row_is_skipped_with_a_warning_and_the_rest_survive(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every row of every file has its declared field count, so ragged = corrupt.

    Padding or truncating would put one column's value under another's name.
    """
    path = tmp_path / "ragged.txt"
    path.write_bytes(b"A~B\r\nx~y\r\nbad\r\np~q~extra\r\nm~n\r\n")
    with caplog.at_level(logging.WARNING):
        rows = list(raw_table.read_rows(_spec(path)))
    assert rows == [{"A": "x", "B": "y"}, {"A": "m", "B": "n"}]
    assert len(caplog.records) == 2
    # The line number is named so the offending row can be found.
    assert "line 3" in caplog.text
    assert "line 4" in caplog.text


def test_a_header_with_the_wrong_width_stops_the_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A re-formatted download is a whole-file problem, not a per-row one."""
    path = tmp_path / "badhdr.txt"
    path.write_bytes(b"A~B~C\r\nx~y~z\r\n")
    with caplog.at_level(logging.WARNING):
        rows = list(raw_table.read_rows(_spec(path, TILDE, fields=2)))
    assert rows == []
    assert len(caplog.records) == 1


def test_a_missing_file_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """fda_raw/ is gitignored and rebuildable, so absence is legitimate."""
    with caplog.at_level(logging.WARNING):
        rows = list(raw_table.read_rows(_spec(Path("/nonexistent/nope.txt"))))
    assert rows == []
    assert caplog.records == []


def test_an_empty_file_yields_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    assert list(raw_table.read_rows(_spec(path))) == []


def test_a_header_only_file_yields_nothing(tmp_path: Path) -> None:
    path = tmp_path / "hdronly.txt"
    path.write_bytes(b"A~B\r\n")
    assert list(raw_table.read_rows(_spec(path))) == []


def test_a_zip_member_reads_like_a_plain_file(tmp_path: Path) -> None:
    """drugsatfda.zip and UNIIs.zip are read from without extracting."""
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Inner.txt", "A\tB\r\nx\ty\r\n")
    spec = DelimitedFile(archive, TAB, 2, rows=0, member="Inner.txt")
    assert list(raw_table.read_rows(spec)) == [{"A": "x", "B": "y"}]


def test_a_missing_zip_member_warns_and_yields_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A member named wrong is a real error, unlike a missing tree."""
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Inner.txt", "A\tB\r\nx\ty\r\n")
    spec = DelimitedFile(archive, TAB, 2, rows=0, member="Absent.txt")
    with caplog.at_level(logging.WARNING):
        assert list(raw_table.read_rows(spec)) == []
    assert len(caplog.records) == 1


def test_a_missing_zip_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    spec = DelimitedFile(Path("/nonexistent/x.zip"), TAB, 2, rows=0, member="I.txt")
    with caplog.at_level(logging.WARNING):
        assert list(raw_table.read_rows(spec)) == []
    assert caplog.records == []


def test_trailing_spaces_in_values_are_preserved(tmp_path: Path) -> None:
    """'19019 ' is a genuine ApplNo; stripping would hide the defect.

    The normalisers exist to handle it, so the reader must not pre-empt them.
    """
    path = tmp_path / "spacey.txt"
    path.write_bytes(b"ApplNo\tApplType\r\n19019 \tNDA\r\n")
    rows = list(raw_table.read_rows(_spec(path, TAB)))
    assert rows == [{"ApplNo": "19019 ", "ApplType": "NDA"}]


def test_the_delimiter_is_a_parameter_not_a_guess(tmp_path: Path) -> None:
    """The same bytes are two different tables under ~ and under TAB."""
    path = tmp_path / "both.txt"
    path.write_bytes(b"A~B\r\nx~y\r\n")
    assert list(raw_table.read_rows(_spec(path, TILDE))) == [{"A": "x", "B": "y"}]
    # Under TAB the header is one field wide, so the declared width fails.
    assert list(raw_table.read_rows(_spec(path, TAB, fields=1))) == [{"A~B": "x~y"}]


def test_count_rows_counts_what_a_caller_would_receive(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """So it can be compared against DelimitedFile.rows to verify a download."""
    path = tmp_path / "count.txt"
    path.write_bytes(b"A~B\r\nx~y\r\nbad\r\np~q")
    with caplog.at_level(logging.WARNING):
        assert raw_table.count_rows(_spec(path)) == 2
