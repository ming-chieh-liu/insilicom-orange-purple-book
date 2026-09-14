"""Read study start dates from the ClinicalTrials.gov archive's study shards.

The manifest answers "does this NCT id exist, and were results posted" in one
600,762-row scan, which is all the trial mapping ever needed. Attribution needs
one more fact — **when the trial started** — and that is only in the study
documents, 2 GB across 24 gzipped JSONL shards.

**Why start date and not completion date.** A trial cannot have supported an
approval granted before the trial began; that is a hard, checkable fact. A
completion date would be the sharper bound, but it is missing or estimated on a
large share of studies, and an estimate cannot refute an approval date. Start
is the conservative choice: it over-accepts rather than wrongly rejecting.

**Why a separate module from the manifest reader.** Cost. This is minutes
where the manifest is seconds, so the caller has to opt into it explicitly
rather than pay it inside a mode that does not need dates.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from config.settings import TrialArchive
from utility.logging import get_logger

_log = get_logger(__name__)


def _start_of(document: dict[str, object]) -> tuple[str, str]:
    """``(nct_id, start_date)`` from one study document; blanks when absent.

    ``startDateStruct`` is sometimes a bare year-month (``"2019-01"``) rather
    than a full date. It is returned as written: a partial date still orders
    correctly against ``YYYY-MM-DD`` for the year-level comparisons this
    supports, and inventing a day would be a fact we do not have.
    """
    protocol = document.get("protocolSection")
    if not isinstance(protocol, dict):
        return "", ""
    identification = protocol.get("identificationModule")
    nct = ""
    if isinstance(identification, dict):
        nct = str(identification.get("nctId") or "")
    status = protocol.get("statusModule")
    start = ""
    if isinstance(status, dict):
        struct = status.get("startDateStruct")
        if isinstance(struct, dict):
            start = str(struct.get("date") or "")
    return nct, start


def read_start_dates(archive: TrialArchive) -> dict[str, str]:
    """Every study's start date, keyed by NCT id. A missing archive is silent.

    Silent rather than raising, to match the other readers: an absent external
    archive is reported by the caller, which knows whether it is fatal.
    """
    shards = archive.study_shards
    if not shards:
        _log.warning("%s: no study shards (%s)", archive.root, archive.study_glob)
        return {}
    dates: dict[str, str] = {}
    for shard in shards:
        with gzip.open(shard, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    # One malformed line must not cost the shard; the manifest
                    # reader takes the same view.
                    continue
                if not isinstance(document, dict):
                    continue
                nct, start = _start_of(document)
                if nct and start:
                    dates[nct] = start
        _log.debug("%s: %d start dates so far", shard.name, len(dates))
    _log.info("%d study start dates from %d shards", len(dates), len(shards))
    return dates


__all__ = ["read_start_dates"]
