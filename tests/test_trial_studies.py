"""Tests for reading study start dates out of the trial archive's shards."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import pytest

from config.settings import TrialArchive
from source.trial_studies import read_start_dates


def _study(nct: str, start: str | None, *, full: bool = True) -> dict[str, object]:
    protocol: dict[str, object] = {"identificationModule": {"nctId": nct}}
    if start is not None:
        protocol["statusModule"] = {"startDateStruct": {"date": start, "type": "ACTUAL"}}
    elif full:
        protocol["statusModule"] = {"overallStatus": "COMPLETED"}
    return {"protocolSection": protocol}


@pytest.fixture
def archive(tmp_path: Path) -> TrialArchive:
    root = tmp_path / "archive"
    root.mkdir()
    (root / "manifest.jsonl.gz").write_bytes(b"")
    shard_one = [
        _study("NCT00000001", "2011-06-01"),
        _study("NCT00000002", "2016-03-01"),
        # A study with no start date at all.
        _study("NCT00000003", None),
    ]
    shard_two = [
        # A partial date, as the registry writes them.
        _study("NCT00000004", "2019-01"),
        # No protocolSection: nothing to read.
        {"derivedSection": {}},
        # No nctId.
        {"protocolSection": {"statusModule": {"startDateStruct": {"date": "2020-01-01"}}}},
        # A protocolSection with no statusModule at all.
        {"protocolSection": {"identificationModule": {"nctId": "NCT00000005"}}},
        # statusModule present but startDateStruct is not a dict.
        {"protocolSection": {"identificationModule": {"nctId": "NCT00000006"},
                             "statusModule": {"startDateStruct": "2020"}}},
    ]
    for name, rows in (("studies-000.jsonl.gz", shard_one),
                       ("studies-001.jsonl.gz", shard_two)):
        with gzip.open(root / name, "wt", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
            handle.write("\n")            # a blank line the reader must tolerate
            handle.write("{not json\n")   # one bad row must not cost the shard
            handle.write("[1, 2, 3]\n")   # valid JSON, wrong shape
    return TrialArchive(root=root)


def test_start_dates_are_read_across_every_shard(archive: TrialArchive) -> None:
    dates = read_start_dates(archive)
    assert dates["NCT00000001"] == "2011-06-01"
    assert dates["NCT00000004"] == "2019-01"


def test_a_partial_date_is_kept_as_written(archive: TrialArchive) -> None:
    """'2019-01' still orders correctly against 'YYYY-MM-DD' for the year-level
    comparison attribution needs. Inventing a day would be a fact we do not have."""
    assert read_start_dates(archive)["NCT00000004"] == "2019-01"


def test_a_study_with_no_start_date_is_absent(archive: TrialArchive) -> None:
    """Absent, not empty-stringed: the caller distinguishes 'no date' from
    'date we could not use', and a blank in the map would read as the latter."""
    assert "NCT00000003" not in read_start_dates(archive)


def test_a_malformed_line_does_not_cost_the_shard(archive: TrialArchive) -> None:
    """Shard 1 holds a bad line after its good rows; those must still arrive."""
    assert "NCT00000004" in read_start_dates(archive)


def test_a_record_without_an_id_is_skipped(archive: TrialArchive) -> None:
    assert len(read_start_dates(archive)) == 3


def test_a_study_with_no_status_module_is_absent(archive: TrialArchive) -> None:
    assert "NCT00000005" not in read_start_dates(archive)


def test_a_malformed_start_date_struct_is_absent(archive: TrialArchive) -> None:
    """The registry's shape is a dict; a bare string is not one, and coercing
    it would invent a date."""
    assert "NCT00000006" not in read_start_dates(archive)


def test_every_shard_is_found_in_name_order(archive: TrialArchive) -> None:
    assert [p.name for p in archive.study_shards] == [
        "studies-000.jsonl.gz",
        "studies-001.jsonl.gz",
    ]


def test_an_archive_without_shards_is_a_warning_and_no_dates(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The archive is another project's output tree, so absence is reported
    rather than raised — the caller decides whether it is fatal."""
    empty = TrialArchive(root=tmp_path / "nothing")
    with caplog.at_level(logging.WARNING):
        assert read_start_dates(empty) == {}
    assert "no study shards" in caplog.text
