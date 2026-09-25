"""What each approval is *for*: label section 1, coded to MeSH disease ids.

Section 14 says which trials an approval rests on. **Section 1 says what the
drug is approved to treat**, and it is on 253,456 of the 262,757 labels (96.5%)
against section 14's 46,810 — so this layer reaches an order of magnitude more
applications than the trial links do.

Two tables come out of one pass:

``indications.tsv``
    One row per (application, disease). The drug→disease edge, coded to a MeSH
    descriptor so it joins to ClinicalTrials.gov, whose conditions are already
    MeSH-coded on 80.5% of drug-arm studies.

``indication_text.tsv``
    One row per application, holding the section-1 prose itself. Coding loses
    what a judge needs — "indicated for X **in patients who have failed** Y" is
    one MeSH term and one crucial qualifier — so the text is kept beside the
    codes rather than thrown away.

**Coding is a dictionary sweep, and its limits are measured, not assumed.**
Every 1..n-word window of the indications text is looked up in the MeSH entry
terms. That is deliberately shallow: it cannot tell an indication from a
contraindication, so a label reading "not indicated for pancreatitis" codes
pancreatitis just the same.

**And measured against the answer key we already own.** On the 774 adjudicated
applications that carry a coded indication, adding an indication↔condition
match to a drug-name trial-candidate filter moves F1 from 0.207 to 0.225 —
precision 14.9% → 18.4%, recall 34.3% → 29.1%. It trades recall for precision
rather than adding information, because indication is largely *collinear* with
the drug name: a trial of drug X is usually already in X's disease. So these
codes are published as a drug→disease deliverable in their own right and as
context for a judge, and are **not** used to filter trial candidates, where
they do not earn their cost.

**Why the join is section 1's, not a new one.** A label reaches an application
through ``openfda.application_number``, exactly as in :mod:`service.approval_map`.
The NDC ``set_id`` bridge is tried second and, as there, recovers nothing: a
label's ``openfda`` block derives from the same SPL/NDC linkage, so both routes
fail on the same records.

Measured 2026-09-14.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from config.settings import MeshVocabulary, RawData
from service import approval_map, identity_census
from source.mesh_descriptors import diseases
from source.openfda_json import openfda_of, read_shards
from utility import application_number as appno
from utility.logging import get_logger

_log = get_logger(__name__)

#: What this layer records as the evidence for a drug→disease edge.
FROM_LABEL: str = "label_section_1"

#: Words, for the n-gram sweep. Digits are kept because MeSH names carry them
#: ("hepatitis b"), and everything else is a separator.
WORD = re.compile(r"[a-z0-9]+")

#: A possessive ``'s`` (straight or curly apostrophe). MeSH writes "Alzheimer
#: Disease" where a label or letter writes "Alzheimer's disease"; split on
#: punctuation, the ``s`` survives as a word of its own and the match fails, so
#: the text coded only the generic "Disease". Dropped from both sides.
POSSESSIVE = re.compile(r"['\u2019]s\b")


def normalise(text: str) -> str:
    """The form both entry terms and text are matched in.

    >>> normalise("Alzheimer's Disease")
    'alzheimer disease'
    >>> normalise("Alzheimer\u2019s disease")
    'alzheimer disease'
    >>> normalise("Carcinoma, Non-Small-Cell Lung")
    'carcinoma non small cell lung'
    """
    return " ".join(WORD.findall(POSSESSIVE.sub("", text.lower())))


#: Words that invert the disease named after them. ``[a-z0-9]+`` splits
#: ``Non-Small Cell Lung Cancer`` into ``non small cell lung cancer``, so a
#: sweep that matches the four-word span ``small cell lung cancer`` returns
#: **Small Cell Lung Carcinoma** — the opposite disease, and one treated with
#: different drugs. MeSH does carry ``non small cell lung carcinoma``, but the
#: label writes *cancer* where MeSH writes *carcinoma*, so the five-word window
#: misses and the wrong four-word one wins. Measured in ``indications.tsv``
#: before this guard: 155 rows coded Small Cell against 3 Non-Small-Cell, where
#: the truth is the reverse.
#:
#: A span preceded by one of these, and not itself starting with it, is
#: dropped. Returning nothing is right where returning the negation is wrong.
NEGATORS: frozenset[str] = frozenset({"non", "not", "nonsmall", "un"})

#: Collapses the runs of whitespace the label text is full of. TSV has no
#: quoting, so the text column is normalised here rather than being repaired
#: by the repository one warning at a time.
WHITESPACE = re.compile(r"\s+")

#: How much section-1 prose is kept per application. The median is 760
#: characters and the 90th percentile 3,026; the cap exists so one pathological
#: label cannot bloat the table, not because anything real approaches it.
TEXT_LIMIT: int = 8_000


class IndicationRow(NamedTuple):
    """One drug→disease edge, as ``indications.tsv`` holds it."""

    application: str
    app_type: str
    drug: str
    unii: str
    mesh_id: str
    mesh_term: str
    evidence: str


class IndicationTextRow(NamedTuple):
    """One application's section-1 prose, as ``indication_text.tsv`` holds it."""

    application: str
    effective_time: str
    n_diseases: str
    text: str


class IndicationReport(NamedTuple):
    """What the pass measured, for the coverage report."""

    rows: tuple[IndicationRow, ...]
    texts: tuple[IndicationTextRow, ...]
    descriptors: int
    terms: int
    labels: int
    labels_with_section_1: int
    applications: int
    branded_applications: int
    applications_coded: int
    applications_uncoded: int
    paths: Counter[str]
    top_diseases: tuple[tuple[str, int], ...]


class TermIndex(NamedTuple):
    """MeSH entry terms, ready to sweep text against."""

    #: Entry term → descriptor ui. One term maps to one descriptor; where a
    #: term appears on two, the first descriptor in file order wins and the
    #: collision is counted rather than silently resolved.
    by_term: dict[str, str]
    #: Descriptor ui → preferred name, for the emitted rows.
    names: dict[str, str]
    #: Longest entry term in words, which bounds the sweep.
    max_words: int
    collisions: int


def build_index(vocabulary: MeshVocabulary) -> TermIndex:
    """Index every disease descriptor's entry terms for matching."""
    by_term: dict[str, str] = {}
    names: dict[str, str] = {}
    max_words = 1
    collisions = 0
    for descriptor in diseases(vocabulary):
        names[descriptor.ui] = descriptor.name
        for raw in descriptor.terms:
            # Normalised exactly as the text is, or a term carrying punctuation
            # ("alzheimer's diseases", "alzheimer-type dementia (atd)") could
            # never be matched by the word sweep at all.
            term = normalise(raw)
            if not term:
                continue
            if by_term.get(term, descriptor.ui) != descriptor.ui:
                collisions += 1
                continue
            by_term[term] = descriptor.ui
            max_words = max(max_words, len(term.split()))
    _log.info(
        "%d disease descriptors, %d entry terms, %d collisions",
        len(names),
        len(by_term),
        collisions,
    )
    return TermIndex(by_term=by_term, names=names, max_words=max_words,
                     collisions=collisions)


def code(text: str, index: TermIndex) -> set[str]:
    """Every disease descriptor named in ``text``, most specific match winning.

    A sweep of every 1..``max_words``-word window. Windows rather than a regex
    alternation because MeSH entry terms are multi-word far more often than
    not, and an alternation over 59,000 of them is slower than the sweep.

    **A match inside a longer match is dropped.** MeSH has a descriptor for the
    bare word ``Disease``, and one for ``Alzheimer Disease``; without this rule
    every label naming any named disease also coded the generic one, which made
    ``Disease`` the most-indicated concept in the corpus at 3,207 applications.
    The specific term's span covers the generic one's, so the generic is
    suppressed. The parent concept is not lost — it is reachable through the
    descriptor's tree numbers, which is what a hierarchy is for.

    >>> index = TermIndex({"asthma": "D001249", "severe asthma": "D000081029",
    ...                    "disease": "D004194", "graves disease": "D006111"},
    ...                   {"D001249": "Asthma", "D000081029": "Asthma, Severe",
    ...                    "D004194": "Disease", "D006111": "Graves Disease"},
    ...                   2, 0)
    >>> sorted(code("indicated for severe asthma in adults", index))
    ['D000081029']
    >>> sorted(code("treatment of graves disease", index))
    ['D006111']
    >>> sorted(code("in disease of any kind", index))
    ['D004194']
    >>> code("", index)
    set()

    **A possessive does not break a match** — see :data:`POSSESSIVE`.

    >>> ad = TermIndex({"alzheimer disease": "D000544", "disease": "D004194"},
    ...                {"D000544": "Alzheimer Disease", "D004194": "Disease"}, 2, 0)
    >>> sorted(code("for the treatment of Alzheimer's disease", ad))
    ['D000544']

    **A span negated by the word before it is dropped.** See :data:`NEGATORS`.

    >>> negated = TermIndex({"small cell lung cancer": "D055752"},
    ...                     {"D055752": "Small Cell Lung Carcinoma"}, 4, 0)
    >>> sorted(code("small cell lung cancer", negated))
    ['D055752']
    >>> code("non-small cell lung cancer", negated)
    set()
    """
    words = normalise(text).split()
    spans: list[tuple[int, int, str]] = []
    for start in range(len(words)):
        if start and words[start - 1] in NEGATORS:
            # The term after a negator names the disease the text says it is
            # *not*. Coding nothing is right where coding the opposite is wrong.
            continue
        for length in range(1, min(index.max_words, len(words) - start) + 1):
            ui = index.by_term.get(" ".join(words[start:start + length]))
            if ui is not None:
                spans.append((start, start + length, ui))
    return {
        ui
        for start, end, ui in spans
        if not any(
            other_start <= start and end <= other_end
            and (other_end - other_start) > (end - start)
            for other_start, other_end, _ in spans
        )
    }


def _section_1(record: dict[str, object]) -> str:
    """One label's indications prose, whitespace-normalised and capped."""
    value = record.get("indications_and_usage")
    if not value:
        return ""
    body = " ".join(str(part) for part in value) if isinstance(value, list) else str(value)
    return WHITESPACE.sub(" ", body).strip()[:TEXT_LIMIT]


def _applications_of(record: dict[str, object], bridge: dict[str, str]) -> list[str]:
    """Which applications a label names, by the same two paths T5 uses."""
    block = openfda_of(record)
    applications = [
        parsed.canonical
        for parsed in (
            appno.normalize(value)
            for value in (block.get("application_number") or [])
        )
        if parsed.is_application
    ]
    if applications:
        return applications
    bridged = bridge.get(str(record.get("set_id") or ""), "")
    return [bridged] if bridged else []


def build(data: RawData, vocabulary: MeshVocabulary) -> IndicationReport:
    """Read the label corpus once and return both indication tables.

    Unlike the trial mapping, **generic applications are kept**. An ANDA label
    reproducing the originator's section 1 is not making a false claim: the
    generic really is indicated for that disease, which is what bioequivalence
    means. The row carries its own application type so a consumer can filter.
    """
    index = build_index(vocabulary)
    approvals, canonical_of = approval_map.approvals_by_application(data)
    representative, _drug_rows = approval_map.drugs_by_application(
        data, canonical_of, branded_only=False
    )
    bridge = approval_map.set_id_to_application(data)

    # application -> (effective_time, text). The most recent label version wins:
    # an application accumulates label versions over years and the current one
    # is what it is approved for now.
    latest: dict[str, tuple[str, str]] = {}
    paths: Counter[str] = Counter()
    labels = 0
    with_section_1 = 0
    for record in read_shards(data.label_shards):
        labels += 1
        text = _section_1(record)
        if not text:
            continue
        with_section_1 += 1
        applications = _applications_of(record, bridge)
        if not applications:
            paths[approval_map.VIA_NONE] += 1
            continue
        paths[approval_map.VIA_OPENFDA if openfda_of(record).get("application_number")
              else approval_map.VIA_NDC_SET_ID] += 1
        when = str(record.get("effective_time") or "")
        for application in applications:
            prior = latest.get(application)
            if prior is None or when > prior[0]:
                latest[application] = (when, text)

    rows: list[IndicationRow] = []
    texts: list[IndicationTextRow] = []
    coded = 0
    disease_counts: Counter[str] = Counter()
    for application, (when, text) in sorted(latest.items()):
        approval = approvals.get(application)
        drug, unii = representative.get(application, ("", ""))
        found = code(text, index)
        if found:
            coded += 1
        for ui in sorted(found):
            name = index.names.get(ui, "")
            disease_counts[name] += 1
            rows.append(
                IndicationRow(
                    application=application,
                    app_type=approval.app_type if approval else "",
                    drug=drug,
                    unii=unii,
                    mesh_id=ui,
                    mesh_term=name,
                    evidence=FROM_LABEL,
                )
            )
        texts.append(
            IndicationTextRow(
                application=application,
                effective_time=when,
                n_diseases=str(len(found)),
                text=text,
            )
        )

    branded = sum(
        1
        for application in latest
        if (row := approvals.get(application)) is not None
        and row.app_type in identity_census.BRANDED_TYPES
    )
    return IndicationReport(
        rows=tuple(rows),
        texts=tuple(texts),
        descriptors=len(index.names),
        terms=len(index.by_term),
        labels=labels,
        labels_with_section_1=with_section_1,
        applications=len(latest),
        branded_applications=branded,
        applications_coded=coded,
        applications_uncoded=len(latest) - coded,
        paths=paths,
        top_diseases=tuple(disease_counts.most_common(10)),
    )


def render(report: IndicationReport) -> str:
    """The coverage report, as ``--mode indications`` prints it."""
    lines = [
        "Indications — label section 1, coded to MeSH",
        "",
        f"  MeSH disease descriptors                 {report.descriptors:>9,}",
        f"  entry terms indexed                      {report.terms:>9,}",
        "",
        f"  labels read                              {report.labels:>9,}",
        f"    with an indications section            {report.labels_with_section_1:>9,}",
    ]
    for path in (approval_map.VIA_OPENFDA, approval_map.VIA_NDC_SET_ID,
                 approval_map.VIA_NONE):
        lines.append(f"      via {path:<32}{report.paths.get(path, 0):>9,}")
    lines += [
        "",
        f"  applications with an indication          {report.applications:>9,}",
        f"    branded (NDA/BLA)                      {report.branded_applications:>9,}",
        f"    at least one disease coded             {report.applications_coded:>9,}",
        f"    text present but nothing coded         {report.applications_uncoded:>9,}",
        "",
        f"  indications.tsv      drug->disease rows  {len(report.rows):>9,}",
        f"  indication_text.tsv  applications        {len(report.texts):>9,}",
        "",
        "  most-indicated diseases:",
    ]
    for name, count in report.top_diseases:
        lines.append(f"    {name:<40}{count:>7,}")
    return "\n".join(lines)


__all__ = [
    "POSSESSIVE",
    "normalise",
    "NEGATORS",
    "FROM_LABEL",
    "TEXT_LIMIT",
    "IndicationReport",
    "IndicationRow",
    "IndicationTextRow",
    "TermIndex",
    "build",
    "build_index",
    "code",
    "render",
]
