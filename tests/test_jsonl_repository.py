"""Tests for gzipped JSON-lines tables (D46: section 14 kept whole)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dao.jsonl_repository import JsonlRepository


def test_records_round_trip_with_their_line_breaks(tmp_path: Path) -> None:
    repository = JsonlRepository(tmp_path / "out")
    records = [{"key": "BLA1|ORIG|1", "text": "14 CLINICAL STUDIES\n14.1 Melanoma\nA trial…"}]
    assert repository.save("label_section14", records) == 1
    assert repository.path_for("label_section14").name == "label_section14.jsonl.gz"
    assert repository.load("label_section14") == records


def test_an_absent_table_is_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert JsonlRepository(tmp_path).load("nothing") == []
    assert "no such table" in caplog.text
