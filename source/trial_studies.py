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
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

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


class StudyFacts(NamedTuple):
    """The facts a section-14 trial description is matched against.

    ``enrollment`` is the load-bearing one. Section 14 states a patient count
    almost always — "conducted in 616 patients" — and within a candidate set
    already narrowed by drug, condition, phase and date, that count is close to
    unique: across five drug+condition sets spanning oncology and
    non-oncology, every candidate had a distinct enrollment, with no
    collisions. It is the field that turns a search into a comparison.
    """

    nct_id: str
    brief_title: str
    #: The sponsor's own protocol id, e.g. ``KEYNOTE-189``. Section 14 names
    #: trials this way far more often than it gives an NCT id.
    org_study_id: str
    acronym: str
    start_date: str
    phases: tuple[str, ...]
    enrollment: str
    interventions: tuple[str, ...]
    conditions: tuple[str, ...]
    #: MeSH ids ClinicalTrials.gov itself assigned, from
    #: ``derivedSection.interventionBrowseModule.meshes`` — NLM's curation
    #: rather than a name match, and present on 46% of studies. Prefixed here
    #: (``MESH:D004837``) to match the id space the FDA side uses.
    #:
    #: **``ancestors`` is deliberately not read.** It walks the MeSH hierarchy
    #: to the root — an epinephrine trial carries ``Hydrocarbons`` and a
    #: polycystic-ovary trial carries ``Neoplasms`` — so matching on it joins a
    #: drug to everything sharing a benzene ring. See D22.
    derived_drug_mesh: tuple[str, ...] = ()
    derived_condition_mesh: tuple[str, ...] = ()
    #: Every other id the study is registered under — a cooperative group's
    #: number (``GOG-0240``), NCI's CTRP id, the sponsor's own when another
    #: organisation registered it. FDA reviews cite trials by these (D26).
    secondary_ids: tuple[str, ...] = ()
    #: The registering sponsor's name, for the review route's sponsor guard.
    lead_sponsor: str = ""
    #: ``primaryCompletionDateStruct``, as written (may be year-month). A trial
    #: whose primary completion postdates an approval cannot have been its
    #: evidence; the drug-only graph fallback uses it as a hard filter (D31).
    primary_completion: str = ""
    #: ``statusModule.overallStatus`` (``COMPLETED``, ``TERMINATED``, …) as of
    #: the archive, not as of any approval — so it is shown to a judge, and
    #: only :data:`NEVER_RAN` is filtered on.
    overall_status: str = ""
    #: ``statusModule.whyStopped``: why a terminated or suspended trial stopped.
    why_stopped: str = ""

    @property
    def never_ran(self) -> bool:
        """Whether the registry says the trial never enrolled anyone.

        >>> StudyFacts("NCT1", "", "", "", "", (), "", (), (), overall_status="WITHDRAWN").never_ran
        True
        >>> StudyFacts("NCT1", "", "", "", "", (), "", (), (), overall_status="TERMINATED").never_ran
        False
        """
        return self.overall_status in NEVER_RAN


#: Statuses of a trial that never ran, and so can support no approval: a
#: withdrawn study stopped before enrolling its first participant, and a
#: not-yet-recruiting one has not started. Excluded from every candidate route
#: (B+, B-, C+, C-, D). Terminated, active and recruiting trials are kept: 14%
#: of the trials FDA labels name were not COMPLETED in the registry (D37).
NEVER_RAN: frozenset[str] = frozenset({"WITHDRAWN", "NOT_YET_RECRUITING"})


def _facts_of(document: dict[str, object]) -> StudyFacts | None:
    """One study's matchable facts, or ``None`` when it has no id."""
    protocol = document.get("protocolSection")
    if not isinstance(protocol, dict):
        return None

    def section(name: str) -> dict[str, object]:
        value = protocol.get(name)
        return value if isinstance(value, dict) else {}

    identification = section("identificationModule")
    nct = str(identification.get("nctId") or "")
    if not nct:
        return None
    org = identification.get("orgStudyIdInfo")
    design = section("designModule")
    enrollment_info = design.get("enrollmentInfo")
    count = ""
    if isinstance(enrollment_info, dict) and enrollment_info.get("count") is not None:
        count = str(enrollment_info["count"])
    arms = section("armsInterventionsModule")
    raw_interventions = arms.get("interventions")
    interventions = tuple(
        str(item.get("name") or "")
        for item in (raw_interventions if isinstance(raw_interventions, list) else [])
        if isinstance(item, dict) and item.get("name")
    )
    raw_conditions = section("conditionsModule").get("conditions")
    conditions = tuple(
        str(item)
        for item in (raw_conditions if isinstance(raw_conditions, list) else [])
        if item
    )
    phases = design.get("phases")
    status = section("statusModule").get("startDateStruct")
    derived = document.get("derivedSection")
    derived = derived if isinstance(derived, dict) else {}
    secondary = identification.get("secondaryIdInfos")
    lead = section("sponsorCollaboratorsModule").get("leadSponsor")
    completion = section("statusModule").get("primaryCompletionDateStruct")
    return StudyFacts(
        nct_id=nct,
        brief_title=str(identification.get("briefTitle") or ""),
        org_study_id=str(org.get("id") or "") if isinstance(org, dict) else "",
        acronym=str(identification.get("acronym") or ""),
        start_date=str(status.get("date") or "") if isinstance(status, dict) else "",
        phases=tuple(str(p) for p in (phases if isinstance(phases, list) else [])),
        enrollment=count,
        interventions=interventions,
        conditions=conditions,
        derived_drug_mesh=_browse_meshes(derived, "interventionBrowseModule"),
        derived_condition_mesh=_browse_meshes(derived, "conditionBrowseModule"),
        secondary_ids=tuple(
            str(item.get("id") or "")
            for item in (secondary if isinstance(secondary, list) else [])
            if isinstance(item, dict) and item.get("id")
        ),
        lead_sponsor=str(lead.get("name") or "") if isinstance(lead, dict) else "",
        primary_completion=str(completion.get("date") or "")
        if isinstance(completion, dict) else "",
        overall_status=str(section("statusModule").get("overallStatus") or ""),
        why_stopped=str(section("statusModule").get("whyStopped") or ""),
    )


def _browse_meshes(derived: dict[str, object], module: str) -> tuple[str, ...]:
    """The ``meshes`` of one browse module, prefixed. ``ancestors`` is ignored.

    >>> _browse_meshes({"m": {"meshes": [{"id": "D004837", "term": "x"}],
    ...                       "ancestors": [{"id": "D006838", "term": "y"}]}}, "m")
    ('MESH:D004837',)
    >>> _browse_meshes({}, "m")
    ()
    """
    block = derived.get(module)
    if not isinstance(block, dict):
        return ()
    meshes = block.get("meshes")
    if not isinstance(meshes, list):
        return ()
    return tuple(
        f"MESH:{identifier}"
        for item in meshes
        if isinstance(item, dict) and (identifier := str(item.get("id") or ""))
    )


def read_study_facts(archive: TrialArchive) -> Iterator[StudyFacts]:
    """Stream every study's matchable facts. A missing archive is silent.

    Streamed rather than returned whole: 600,762 studies is more than any
    caller needs resident, and every caller so far either indexes a projection
    or filters on the way past.
    """
    shards = archive.study_shards
    if not shards:
        _log.warning("%s: no study shards (%s)", archive.root, archive.study_glob)
        return
    seen = 0
    for shard in shards:
        with gzip.open(shard, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(document, dict):
                    continue
                facts = _facts_of(document)
                if facts is not None:
                    seen += 1
                    yield facts
        _log.debug("%s: %d studies so far", shard.name, seen)
    _log.info("%d studies from %d shards", seen, len(shards))


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


__all__ = [
    "NEVER_RAN","StudyFacts", "read_start_dates", "read_study_facts"]
