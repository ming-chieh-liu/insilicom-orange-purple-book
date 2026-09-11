"""Tests for reading the openFDA JSON downloads."""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import pytest

from source import openfda_json


def _zip(path: Path, document: Any, member: str = "doc.json") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, json.dumps(document))
    return path


def test_the_records_come_out_of_the_results_array(tmp_path: Path) -> None:
    """Every openFDA file is {"meta": ..., "results": [...]}."""
    path = _zip(tmp_path / "a.zip", {"meta": {"results": {"total": 2}},
                                     "results": [{"id": "1"}, {"id": "2"}]})
    assert [r["id"] for r in openfda_json.read_results(path)] == ["1", "2"]


def test_a_missing_file_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """The downloads are gitignored, so absence is a normal state."""
    with caplog.at_level(logging.WARNING):
        assert list(openfda_json.read_results(Path("/nonexistent/x.zip"))) == []
    assert caplog.records == []


def test_a_file_with_no_results_array_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A download that changed shape is a real problem, unlike a missing one."""
    path = _zip(tmp_path / "b.zip", {"meta": {}})
    with caplog.at_level(logging.WARNING):
        assert list(openfda_json.read_results(path)) == []
    assert len(caplog.records) == 1


def test_an_empty_archive_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w"):
        pass
    with caplog.at_level(logging.WARNING):
        assert list(openfda_json.read_results(path)) == []
    assert len(caplog.records) == 1


def test_a_non_dict_record_is_skipped(tmp_path: Path) -> None:
    """Defensive: the array is read from disk, so its contents are untrusted."""
    path = _zip(tmp_path / "c.zip", {"results": [{"id": "1"}, "junk", None, {"id": "2"}]})
    assert [r["id"] for r in openfda_json.read_results(path)] == ["1", "2"]


def test_shards_read_in_order_and_one_at_a_time(tmp_path: Path) -> None:
    """The label download is 14 zips; memory is bounded by the largest, not the sum."""
    first = _zip(tmp_path / "s1.zip", {"results": [{"id": "a"}]})
    second = _zip(tmp_path / "s2.zip", {"results": [{"id": "b"}, {"id": "c"}]})
    assert [r["id"] for r in openfda_json.read_shards([first, second])] == ["a", "b", "c"]


def test_shards_tolerate_a_missing_member(tmp_path: Path) -> None:
    present = _zip(tmp_path / "s1.zip", {"results": [{"id": "a"}]})
    absent = tmp_path / "gone.zip"
    assert [r["id"] for r in openfda_json.read_shards([present, absent])] == ["a"]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"openfda": {"unii": ["X"]}}, {"unii": ["X"]}),
        ({"openfda": {}}, {}),
        ({}, {}),
        ({"openfda": None}, {}),
        ({"openfda": "not a dict"}, {}),
    ],
)
def test_openfda_of_normalises_absent_and_empty(
    record: dict[str, Any], expected: dict[str, Any]
) -> None:
    """The key is present on nearly every record and empty on two thirds.

    So `"openfda" in record` is true and means nothing; the test has to be on
    the contents, and it is done once here rather than at each call site.
    """
    assert openfda_json.openfda_of(record) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["NDA019019"], "NDA019019"),
        (["NDA019019", "NDA019020"], "NDA019019"),
        ([], ""),
        (None, ""),
        ("a string, not a list", ""),
        ([123], "123"),
    ],
)
def test_first_takes_one_value_out_of_an_openfda_array(
    values: Any, expected: str
) -> None:
    """Every openFDA value is an array, including single-valued ones."""
    assert openfda_json.first(values) == expected
