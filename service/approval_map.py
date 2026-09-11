"""Build the trial → approval mapping: the project's actual deliverable.

Answers one question at scale: **which FDA approval was supported by which
clinical trial?** The FDA states it itself, in label section 14 — and that
sentence is the edge:

    Phesgo, BLA 761170, section 14: "…is supported by evidence from adequate
    and well-controlled studies conducted with intravenous pertuzumab and
    intravenous trastuzumab… in adults with HER2-overexpressing early breast
    cancer (NCT00545688, NCT00976989, NCT02132949, NCT01358877)."

Two tasks, one pass over the 1.8 GB label download because both need the same
records:

**T5 — link a label to an application.** Two paths are needed, not one.
``openfda.application_number`` is present on only a third of labels, because
the ``openfda`` block is empty on 66.7% of them. For the rest, the label's
``set_id`` is looked up in the NDC file, which carries both ``spl_set_id`` and
``application_number`` on 96,752 of its 137,804 records. A single-path join
silently loses two thirds of the corpus.

**T8 — extract the trials.** ``NCT\\d{8}`` over the ``clinical_studies`` text.
Every NCT id in the corpus is exactly 8 digits, so the pattern needs no
tolerance. Each id is then checked against the local ClinicalTrials.gov
archive's manifest: an id that matches the shape but names no real study is a
false positive (a typo, or a coincidental digit run), and is counted separately
rather than emitted.

**What the confidence column means.** ``fda_stated`` is the only value this
module produces, and it is the strongest evidence available: the FDA, in a
legally binding document, naming the studies it relied on. Weaker sources
(Open Targets asserts 49,724 trial–drug pairs but cannot say the FDA relied on
any of them) would enter as ``candidate`` and are not part of this task.

Decided 2026-09-10; built 2026-09-11.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData, TrialArchive
from service import identity_census
from source.openfda_json import first, openfda_of, read_results, read_shards
from source.raw_table import read_rows
from utility import application_number as appno
from utility import fda_date
from utility.logging import get_logger

_log = get_logger(__name__)

#: Every NCT id in the corpus is NCT plus exactly 8 digits — verified over the
#: label text, where 319 of 319 matches in one shard had that shape. A looser
#: pattern would admit digit runs that are not registry ids.
NCT_PATTERN = re.compile(r"NCT\d{8}")

#: The evidence strength this module emits. The FDA naming its own evidence in
#: a label is the strongest available claim; nothing here is inferred.
FDA_STATED: str = "fda_stated"

#: Which join path found a label's application, for the coverage report.
VIA_OPENFDA: str = "openfda"
VIA_NDC_SET_ID: str = "ndc_set_id"
VIA_NONE: str = "unlinked"


class DrugRow(NamedTuple):
    """One substance. Keyed by UNII, per the drug-key decision."""

    unii: str
    name: str


class ApprovalRow(NamedTuple):
    """One application, with its original approval."""

    application: str
    app_type: str
    sponsor: str
    first_approval: str
    approved_pre_1982: str


class LinkRow(NamedTuple):
    """One trial → approval claim, and where the claim came from."""

    nct_id: str
    application: str
    evidence: str
    confidence: str
    has_results: str


class MapRow(NamedTuple):
    """The denormalized deliverable: one row per trial → approval claim."""

    nct_id: str
    application: str
    app_type: str
    drug: str
    unii: str
    first_approval: str
    sponsor: str
    has_results: str
    confidence: str


class MapReport(NamedTuple):
    """The tables, plus what the run measured."""

    drugs: tuple[DrugRow, ...]
    approvals: tuple[ApprovalRow, ...]
    links: tuple[LinkRow, ...]
    mapping: tuple[MapRow, ...]
    label_paths: tuple[tuple[str, int], ...]
    section14_labels: int
    labels_seen: int
    nct_mentions: int
    nct_distinct: int
    nct_not_in_archive: tuple[str, ...]
    nct_with_results: int
    applications_linked: int
    generic_labels_skipped: int
    generic_applications_skipped: int


# --- the approval side ------------------------------------------------------


def _approvals(data: RawData) -> tuple[dict[str, ApprovalRow], dict[str, str]]:
    """Every application, and the digits→canonical index everything else joins on.

    The approval date is the ``ORIG`` submission's, not the application's:
    Drugs@FDA records a date per submission, and 86% of the 193,687 submissions
    are supplements carrying later changes rather than the first approval.

    Returns the rows keyed by canonical application, plus ``digits →
    canonical`` — because every other file in the corpus states the six digits
    without a type, and that index is the only honest way back to a typed key.
    """
    sponsors: dict[str, str] = {}
    types: dict[str, str] = {}
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row["ApplType"], row["ApplNo"])
        if not parsed.is_application:
            continue
        sponsors[parsed.canonical] = row["SponsorName"].strip()
        types[parsed.canonical] = parsed.app_type
        canonical_of[parsed.digits] = parsed.canonical

    originals: dict[str, fda_date.FdaDate] = {}
    for row in read_rows(data.submissions):
        if row["SubmissionType"].strip() != "ORIG" or row["SubmissionStatus"].strip() != "AP":
            continue
        application = canonical_of.get(appno.pad(row["ApplNo"]))
        if application:
            originals[application] = fda_date.parse(
                row["SubmissionStatusDate"], direction=fda_date.PAST_ONLY
            )

    out: dict[str, ApprovalRow] = {}
    for application, app_type in types.items():
        parsed_date = originals.get(application)
        out[application] = ApprovalRow(
            application=application,
            app_type=app_type,
            sponsor=sponsors.get(application, ""),
            first_approval=str(parsed_date.value) if parsed_date and parsed_date.value else "",
            approved_pre_1982="true" if parsed_date and parsed_date.approved_pre_1982 else "false",
        )
    return out, canonical_of


def _drugs_by_application(
    data: RawData, canonical_of: dict[str, str]
) -> tuple[dict[str, tuple[str, str]], tuple[DrugRow, ...]]:
    """Map each application to one representative (name, UNII) for the map table.

    An application can hold several ingredients; the map carries the first that
    resolves to a UNII so a row is readable, while ``drugs.tsv`` keeps every
    substance. The mapping table is a convenience view, not the authority.

    Keyed by **canonical application**, so the map joins on the same string
    ``approvals.tsv`` and ``links.tsv`` use.
    """
    _known, branded = identity_census.typed_applnos(data)
    per_application: dict[str, set[str]] = {}
    for row in read_rows(data.products):
        digits = appno.pad(row["ApplNo"])
        if digits not in branded:
            continue
        # branded is a subset of the typed applications and canonical_of holds
        # every one of them, so this lookup cannot miss.
        application = canonical_of[digits]
        for part in row["ActiveIngredient"].split(identity_census.INGREDIENT_SEPARATOR):
            cleaned = part.strip().upper()
            if cleaned:
                per_application.setdefault(application, set()).add(cleaned)

    wanted = {name for names in per_application.values() for name in names}
    hits, _ = identity_census.resolve_uniis(data, wanted)

    drug_rows = tuple(
        sorted(
            {DrugRow(unii, name) for name, uniis in hits.items() for unii in uniis},
            key=lambda r: (r.unii, r.name),
        )
    )

    representative: dict[str, tuple[str, str]] = {}
    for application, names in per_application.items():
        for name in sorted(names):
            uniis = hits.get(name)
            if uniis:
                representative[application] = (name, sorted(uniis)[0])
                break
        else:
            representative[application] = (sorted(names)[0] if names else "", "")
    return representative, drug_rows


# --- the label side ---------------------------------------------------------


def _set_id_to_application(data: RawData) -> dict[str, str]:
    """The NDC bridge: SPL set id → canonical application number.

    Needed because the label's own ``openfda`` block is empty on 66.7% of
    labels. 96,752 of 137,804 NDC records carry both keys.
    """
    bridge: dict[str, str] = {}
    for record in read_results(data.ndc_json):
        parsed = appno.normalize(record.get("application_number"))
        if not parsed.is_application:
            continue
        for set_id in openfda_of(record).get("spl_set_id") or []:
            bridge.setdefault(str(set_id), parsed.canonical)
    return bridge


def _archive_results(archive: TrialArchive) -> dict[str, bool]:
    """Every NCT id in the local archive, and whether results were posted.

    Only the manifest is read — one line per study — so validating ids never
    touches the 2 GB of study documents.
    """
    out: dict[str, bool] = {}
    if not archive.exists:
        _log.warning("%s absent: NCT ids cannot be validated", archive.manifest)
        return out
    with gzip.open(archive.manifest, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                _log.warning("malformed manifest line, skipped")
                continue
            nct = str(row.get("nct", "")).strip()
            if nct:
                out[nct] = bool(row.get("has_results"))
    return out


def build(data: RawData, archive: TrialArchive) -> MapReport:
    """Read everything once and return the four tables."""
    approvals, canonical_of = _approvals(data)
    representative, drug_rows = _drugs_by_application(data, canonical_of)
    bridge = _set_id_to_application(data)
    archive_results = _archive_results(archive)

    paths: Counter[str] = Counter()
    links: dict[tuple[str, str], LinkRow] = {}
    mentions = 0
    section14 = 0
    labels = 0
    unknown: set[str] = set()
    with_results: set[str] = set()
    generic_rows = 0
    generic_applications: set[str] = set()

    for record in read_shards(data.label_shards):
        labels += 1
        sections = record.get("clinical_studies") or []
        if not sections:
            continue
        section14 += 1

        block = openfda_of(record)
        parsed = appno.normalize(first(block.get("application_number")))
        if parsed.is_application:
            application = parsed.canonical
            paths[VIA_OPENFDA] += 1
        else:
            application = bridge.get(str(record.get("set_id") or ""), "")
            paths[VIA_NDC_SET_ID if application else VIA_NONE] += 1
        if not application:
            continue

        # A generic's label reproduces the originator's section 14 verbatim, so
        # it cites the brand's trials — but an ANDA is approved on
        # bioequivalence, not on those trials, and emitting the row would
        # assert something false. Decided 2026-09-11: branded only. An
        # application we cannot type is dropped for the same reason — we
        # cannot confirm it is branded.
        row = approvals.get(application)
        if row is None or row.app_type not in identity_census.BRANDED_TYPES:
            generic_rows += 1
            generic_applications.add(application)
            continue

        for text in sections:
            for nct in NCT_PATTERN.findall(str(text)):
                mentions += 1
                if nct not in archive_results:
                    unknown.add(nct)
                    continue
                if archive_results[nct]:
                    with_results.add(nct)
                links[(nct, application)] = LinkRow(
                    nct_id=nct,
                    application=application,
                    evidence="label_section_14",
                    confidence=FDA_STATED,
                    has_results="true" if archive_results[nct] else "false",
                )

    link_rows = tuple(sorted(links.values()))
    mapping = tuple(
        MapRow(
            nct_id=link.nct_id,
            application=link.application,
            app_type=approvals[link.application].app_type if link.application in approvals else "",
            drug=representative.get(link.application, ("", ""))[0],
            unii=representative.get(link.application, ("", ""))[1],
            first_approval=approvals[link.application].first_approval
            if link.application in approvals
            else "",
            sponsor=approvals[link.application].sponsor if link.application in approvals else "",
            has_results=link.has_results,
            confidence=link.confidence,
        )
        for link in link_rows
    )

    return MapReport(
        drugs=drug_rows,
        approvals=tuple(sorted(approvals.values())),
        links=link_rows,
        mapping=mapping,
        label_paths=tuple(sorted(paths.items())),
        section14_labels=section14,
        labels_seen=labels,
        nct_mentions=mentions,
        nct_distinct=len({link.nct_id for link in link_rows}),
        nct_not_in_archive=tuple(sorted(unknown)),
        nct_with_results=len(with_results),
        applications_linked=len({link.application for link in link_rows}),
        generic_labels_skipped=generic_rows,
        generic_applications_skipped=len(generic_applications),
    )


def render(report: MapReport) -> str:
    """The run's numbers as text."""
    lines = [
        "T5  LABEL -> APPLICATION",
        f"  labels read                              {report.labels_seen:>9,}",
        f"  with section 14 (clinical_studies)       {report.section14_labels:>9,}",
    ]
    for path, count in report.label_paths:
        lines.append(f"    via {path:<34} {count:>9,}")
    lines.append(
        f"  generic/untypable, not emitted           "
        f"{report.generic_labels_skipped:>9,}"
        f"   ({report.generic_applications_skipped:,} applications)"
    )
    lines += [
        "",
        "T8  SECTION 14 -> TRIALS",
        f"  NCT mentions found                       {report.nct_mentions:>9,}",
        f"  distinct NCT ids kept                    {report.nct_distinct:>9,}",
        f"    of those, with posted results          {report.nct_with_results:>9,}",
        f"  matched the shape but no such study      {len(report.nct_not_in_archive):>9,}",
        f"  applications with at least one trial     {report.applications_linked:>9,}",
        "",
        "TABLES",
        f"  drugs.tsv                                {len(report.drugs):>9,}",
        f"  approvals.tsv                            {len(report.approvals):>9,}",
        f"  links.tsv                                {len(report.links):>9,}",
        f"  trial_approval_map.tsv                   {len(report.mapping):>9,}",
    ]
    return "\n".join(lines)


__all__ = [
    "FDA_STATED",
    "NCT_PATTERN",
    "VIA_NDC_SET_ID",
    "VIA_NONE",
    "VIA_OPENFDA",
    "ApprovalRow",
    "DrugRow",
    "LinkRow",
    "MapReport",
    "MapRow",
    "build",
    "render",
]
