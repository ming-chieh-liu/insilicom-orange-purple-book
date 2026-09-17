"""Read a label's Clinical Studies section, and the trials described in it.

Section 14 is where FDA states what evidence an approval rested on. It is
partitioned by indication — ``14.2 Non-Small Cell Lung Cancer`` — and each
subsection describes the trials for that indication in prose:

    The efficacy of KEYTRUDA in combination with pemetrexed and platinum
    chemotherapy was investigated in KEYNOTE-189 (NCT02578680), a randomized,
    multicenter, double-blind, active-controlled trial conducted in 616
    patients with metastatic nonsquamous NSCLC ...

**Only rarely is there an NCT id.** One of six originals sampled carried one,
and 0.9% of labels corpus-wide. What is nearly always present is a trial name
and a patient count, and those are enough: within a candidate set already
narrowed by drug, condition, phase and approval date, the enrollment count was
distinct for every candidate across five drug+condition sets. So the fields
extracted here are what P3 filters on and P4 compares against, not an id.

**Section numbers must never be matched over flat text.** A label is full of
``14.2`` as a table value, a dosage, a confidence bound and a table-of-contents
cross-reference; a bare ``\\b14\\.\\d`` pattern picks all of them up and makes a
real finding look like a parser artefact. Every match here is anchored to the
start of a line, and the section is bounded by the *next* top-level heading
rather than run to the end of the document.

**Not every label has this section, and that is not a parser failure.** Measured
over a 600-label sample: 90.5% of labels for 2008+ approvals carry a Clinical
Studies section, against 59.0% for 2000-2007 and 42.3% for pre-2000. The ones
without it head their nearest equivalent ``CLINICAL PHARMACOLOGY`` or
``NONCLINICAL TOXICOLOGY`` — pharmacology and toxicology, not efficacy trials.
Pre-PLR labels frequently had no efficacy-studies section at all. So the
corpus-wide figure of 84.0% is a property of the archive; the P2 gate is met on
the era that can be linked to a registered trial anyway.

**The last occurrence wins.** A label states ``14 CLINICAL STUDIES`` twice: once
in the contents list at the top, once as the section itself. Taking the first
match yields the table of contents.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from service import approval_events
from source import label_pdf
from source.raw_table import read_rows
from utility import application_number as appno
from utility.logging import get_logger

_log = get_logger(__name__)

#: The section heading, at the start of a line. ``FULL PRESCRIBING
#: INFORMATION`` labels write it upper-case; the tolerance for case costs
#: nothing because the anchor is what does the work.
SECTION_14 = re.compile(r"(?mi)^[ \t]*14[ \t]+CLINICAL STUDIES")

#: Section 1, for the indication this submission was approved for.
SECTION_1 = re.compile(r"(?mi)^[ \t]*1[ \t]+INDICATIONS AND USAGE")

#: Where section 14 ends: the next top-level heading. 15 is How Supplied and
#: 16 is Storage; 17 is Patient Counseling. Any of them bounds it.
#: Three letters, not four: real labels head section 16 ``HOW SUPPLIED/STORAGE
#: AND HANDLING`` and section 8 ``USE IN SPECIFIC POPULATIONS``. Requiring four
#: let section 14 run on into 15, 16 and 17.
AFTER_14 = re.compile(r"(?m)^[ \t]*1[5-7][ \t]+[A-Z]{3,}")

#: The pre-PLR spelling. Labels approved before the numbered format was phased
#: in from 2006 head the section without a number, and 227 of the 4,424
#: trial-relevant submissions with a label are pre-2000. Without this they read
#: as having no Clinical Studies section at all, which is not true — they have
#: one, it is merely unnumbered, and it still names trials and patient counts.
SECTION_14_UNNUMBERED = re.compile(r"(?mi)^[ \t]*CLINICAL (?:STUDIES|TRIALS)[ \t]*$")

#: What bounds an unnumbered section: the next all-capitals heading line.
AFTER_UNNUMBERED = re.compile(r"(?m)^[ \t]*[A-Z][A-Z /,'&-]{6,}[ \t]*$")

#: Where section 1 ends.
AFTER_1 = re.compile(r"(?m)^[ \t]*[2-9][ \t]+[A-Z]{3,}")

#: A numbered subsection heading, at the start of a line and followed by a
#: capitalised title — ``14.2 Non-Small Cell Lung Cancer``.
SUBSECTION = re.compile(r"(?m)^[ \t]*(14\.\d{1,2})[ \t]+([A-Z][^\n]{2,90})")

#: A registry id, where the label happens to give one. The separator is
#: optional because labels write ``NCT 01474512`` as often as ``NCT01474512``,
#: and also ``NCT#``, ``NCT:`` and ``NCT-``. Requiring none missed 40 rows that
#: already stated their answer, sending them to a judge to identify a trial the
#: label had named. Matches are normalised by :func:`normalise_nct`.
NCT = re.compile(r"NCT[\s:#-]{0,3}(\d{8})")

#: Units and nouns that follow a dose or a duration, never a trial number.
#: Without this, ``KEYTRUDA 10 mg/kg`` and ``LUTATHERA 7.4 GBq`` are read as
#: trial names — the drug's own brand name plus its dose. Measured on the
#: Keytruda label, that was the single largest source of false acronyms.
_UNIT = (
    r"(?:mg|mcg|µg|g|kg|mL|L|IU|Gy|GBq|mCi|%|units?|"
    r"weeks?|days?|months?|years?|hours?|patients?|subjects?|participants?)"
)

#: A sponsor's trial name: ``KEYNOTE-189``, ``CHECKMATE-227``, ``PALOMA-3``.
#: A hyphen is the reliable separator; a space is allowed because labels also
#: write ``KEYNOTE 189`` and ``SCH 900475``, but only when what follows is not
#: a unit. Rating scales and PK parameters (``HAMD-17``, ``AUC0-24``) still get
#: through — the gazetteer match against the registry in P3 is what settles
#: those, not this pattern.
ACRONYM = re.compile(
    r"\b([A-Z][A-Z0-9]{3,}(?:"
    r"-\d{1,4}[A-Z]?"
    # A space-separated number is a trial only when it is a whole number and
    # not a dose: ``LUTATHERA 7.4 GBq`` must not read as trial "LUTATHERA 7".
    r"|[ ]\d{1,4}[A-Z]?(?!\.\d)(?!\s*" + _UNIT + r"\b)"
    r"))\b"
)

#: The patient count. Section 14 states it in a handful of shapes, all of which
#: put the number next to the word for the people counted.
#: The counted noun, however the prose qualifies it.
_COUNTED = (
    r"(?:adult\s+|pediatric\s+|male\s+|female\s+|evaluable\s+|randomized\s+)*"
    r"(?:patients|subjects|participants|women|men|children)"
)

ENROLLMENT = re.compile(
    r"(?i)(?:"
    # "investigated in KEYNOTE-189 ... conducted in 616 patients"
    r"\b(?:in|enrolled|randomi[sz]ed|assigned|included|studied)\b"
    r"(?:[^.\n]{0,40}?)\b(?P<a>\d{1,3}(?:,\d{3})*|\d{2,5})\s+" + _COUNTED +
    r"|"
    # "Two hundred twenty-nine (229) patients were randomized"
    r"\(?(?P<b>\d{1,3}(?:,\d{3})*|\d{2,5})\)?\s+" + _COUNTED +
    r"\s+(?:were|was)\s+(?:randomi[sz]ed|enrolled|assigned|treated|studied)"
    r")"
)

#: Phrases that mark a design, kept as evidence for the P4 comparison.
DESIGN = re.compile(
    r"(?i)\b(randomized|randomised|double-blind|open-label|placebo-controlled"
    r"|active-controlled|multicenter|multicentre|single-arm|crossover"
    r"|non-inferiority|dose-ranging)\b"
)


class TrialDescription(NamedTuple):
    """One trial as a label describes it, with the facts P3 and P4 use."""

    application: str
    submission_type: str
    submission_no: str
    #: ``14.2`` where the label numbers its subsections, else ``14``.
    section: str
    #: The subsection heading, which names the indication.
    heading: str
    nct_id: str
    acronym: str
    #: Patient count as written, digits only. Empty when the prose omits it.
    enrollment: str
    design: str
    #: The sentence the trial was named in, for a judge to read.
    excerpt: str
    #: SHA-256 of the label PDF this came out of. One PDF often serves several
    #: submissions approved together — ``021029s036_037_038lbl.pdf`` is the
    #: label for three — and FDA sometimes files separate but identical PDFs
    #: instead. Carrying the digest lets P5 tell "the same label, counted
    #: three times" from "three genuinely different labels that happen to
    #: share a date". See D16.
    label_sha: str = ""


class LabelReading(NamedTuple):
    """What one label yielded."""

    key: str
    section_1: str
    section_14: str
    subsections: int
    descriptions: tuple[TrialDescription, ...]

    @property
    def has_section_14(self) -> bool:
        """Whether a Clinical Studies section was found at all."""
        return bool(self.section_14)


def normalise_nct(digits: str) -> str:
    """The canonical form of a registry id.

    >>> normalise_nct("01474512")
    'NCT01474512'
    """
    return f"NCT{digits}"


def _bounded(text: str, start: re.Pattern[str], end: re.Pattern[str]) -> str:
    """The region from the LAST ``start`` heading to the next ``end`` heading.

    Last rather than first because the contents list at the top of a label
    repeats every heading; taking the first match returns the contents.
    """
    matches = list(start.finditer(text))
    if not matches:
        return ""
    region = text[matches[-1].start() :]
    stop = end.search(region, 1)
    return region[: stop.start()] if stop else region


def section(text: str, number: int) -> str:
    """Section 1 or section 14 of a label, bounded by the next top-level one.

    Section 14 falls back to the unnumbered pre-PLR heading, because a label
    from 1999 has a Clinical Studies section — it simply does not number it.
    """
    if number == 1:
        return _bounded(text, SECTION_1, AFTER_1)
    numbered = _bounded(text, SECTION_14, AFTER_14)
    if numbered:
        return numbered
    return _bounded(text, SECTION_14_UNNUMBERED, AFTER_UNNUMBERED)


def split_subsections(body: str) -> tuple[tuple[str, str, str], ...]:
    """``(number, heading, text)`` per ``14.N`` subsection.

    A label with no numbered subsections — pre-PLR, or a single-indication drug
    — yields one block numbered ``14``, because the trials in it still belong
    to the submission even when the label does not partition them.
    """
    matches = list(SUBSECTION.finditer(body))
    if not matches:
        return (("14", "", body),) if body.strip() else ()
    out: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        out.append((match.group(1), match.group(2).strip(), body[match.start() : stop]))
    return tuple(out)


def _sentences(block: str) -> list[str]:
    """The block split into sentences, crudely but adequately.

    Adequate because the only use is to give a judge the sentence a trial was
    named in; a mis-split costs a slightly wrong excerpt, not a wrong match.
    """
    return [part.strip() for part in re.split(r"(?<=[.;])\s+", block) if part.strip()]


def is_own_drug_name(acronym: str, own_names: frozenset[str]) -> bool:
    """Whether an "acronym" is really this drug's own name plus a number.

    ``ABILIFY 15``, ``ALOXI 189``, ``AEROSPHERE 320``. Section 14 is full of
    tables whose columns are dose arms, and PDF text extraction runs the brand
    name straight into the numbers beside it. The unit lookahead in
    :data:`ACRONYM` catches ``ABILIFY 15 mg``; it cannot catch ``ABILIFY 15``
    sitting in a table row with another number after it.

    Measured before this guard: **51% of extracted trial names** were of this
    shape, against 39% plausible ones. A judge answers NONE to every one of
    them, correctly, so they cost two agent calls each and yield nothing.

    >>> is_own_drug_name("ABILIFY 15", frozenset({"ABILIFY"}))
    True
    >>> is_own_drug_name("KEYNOTE-189", frozenset({"KEYTRUDA"}))
    False
    >>> is_own_drug_name("ABILIFY 15", frozenset())
    False
    """
    head = re.split(r"[- ]", acronym.strip(), maxsplit=1)[0].upper()
    return head in own_names


def describe_trials(
    application: str,
    submission_type: str,
    submission_no: str,
    body: str,
    own_names: frozenset[str] = frozenset(),
) -> tuple[TrialDescription, ...]:
    """Every trial the section names, with what the prose says about it.

    A sentence naming both an NCT id and an acronym yields one description, not
    two. A subsection naming no trial at all still yields one row when it
    states an enrollment — the trial is there, just unnamed, and P4 can still
    compare it against candidates.

    **A sentence naming several registry ids yields one description each.**
    Labels routinely introduce a pair together — *"two Phase 3 studies, namely
    GEMINI 1 (NCT03804268) and GEMINI 2 (NCT03857542)"* — and keeping only the
    first silently dropped the second. Measured against the openFDA snapshot of
    the same labels, 26 of a 40-case sample were ids sitting inside a section 14
    this module had already captured, and **none** were outside it: the region
    was right and the reading was lossy. An id the FDA wrote on the label is the
    strongest evidence this project has, so none may be discarded.

    Acronyms pair positionally when the sentence supplies as many as it does
    ids, which is the usual shape. When it does not, only the first id keeps an
    acronym — a wrong name is worse than none, and mis-paired acronyms are what
    made two different trials collide in :func:`service.candidates.collapse`.

    The **enrollment count stays with the first id only**. A sentence naming two
    trials and one count does not say which it belongs to, and copying it to
    both would manufacture agreement between a label and a registry that P5
    reads as corroboration (D17).
    """
    out: list[TrialDescription] = []
    for number, heading, block in split_subsections(body):
        for sentence in _sentences(block):
            ncts = [normalise_nct(d) for d in NCT.findall(sentence)]
            acronyms = [
                name
                for name in ACRONYM.findall(sentence)
                if not name.startswith("NCT")
                and not is_own_drug_name(name, own_names)
            ]
            counts = [
                match.group("a") or match.group("b")
                for match in ENROLLMENT.finditer(sentence)
            ]
            if not (ncts or acronyms or counts):
                continue
            design = ";".join(sorted({d.lower() for d in DESIGN.findall(sentence)}))
            paired = len(acronyms) == len(ncts)
            for position, nct in enumerate(ncts or [""]):
                if position and paired:
                    acronym = acronyms[position]
                elif position:
                    acronym = ""
                else:
                    acronym = acronyms[0] if acronyms else ""
                out.append(
                    TrialDescription(
                        application=application,
                        submission_type=submission_type,
                        submission_no=submission_no,
                        section=number,
                        heading=heading,
                        nct_id=nct,
                        acronym=acronym,
                        enrollment=(
                            counts[0].replace(",", "")
                            if counts and not position
                            else ""
                        ),
                        design=design,
                        excerpt=sentence[:600],
                    )
                )
    return tuple(out)


def read_label(
    key: str,
    application: str,
    submission_type: str,
    submission_no: str,
    text: str,
    own_names: frozenset[str] = frozenset(),
) -> LabelReading:
    """Both sections of one label, and the trials section 14 describes."""
    body_14 = section(text, 14)
    body_1 = section(text, 1)
    return LabelReading(
        key=key,
        section_1=body_1[:4000],
        section_14=body_14,
        subsections=len(
            [s for s in split_subsections(body_14) if s[0] != "14"]
        ),
        descriptions=describe_trials(
            application, submission_type, submission_no, body_14, own_names
        ),
    )


class SubmissionIndication(NamedTuple):
    """Section 1 of the label filed with one submission.

    The point of reading it here rather than from the current openFDA snapshot:
    ``indications.tsv`` is keyed on the *application*, so it holds the
    cumulative list of everything the drug has ever been approved for, undated
    and unattributed. This is the indication text as it stood **at that
    approval**, which is what makes an indication attributable to a submission
    at all.
    """

    application: str
    submission_type: str
    submission_no: str
    text: str


class LabelsReport(NamedTuple):
    """What a P2 pass fetched, read and extracted."""

    descriptions: tuple[TrialDescription, ...]
    indications: tuple[SubmissionIndication, ...]
    submissions: int
    fetched: int
    failed: int
    scanned: int
    unreadable: int
    with_section_14: int
    with_section_1: int
    with_nct: int
    with_enrollment: int
    numbered_subsections: int

    @property
    def readable(self) -> int:
        """Labels that produced text at all."""
        return self.fetched - self.scanned - self.unreadable

    @property
    def section_14_rate(self) -> float:
        """The P2 gate: non-scanned labels yielding a Clinical Studies section.

        >>> LabelsReport((), (), 0, 10, 0, 2, 0, 7, 0, 0, 0, 0).section_14_rate
        0.875
        >>> LabelsReport((), (), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0).section_14_rate
        0.0
        """
        return self.with_section_14 / self.readable if self.readable else 0.0


def render(report: LabelsReport) -> str:
    """The pass report, as ``--mode labels`` prints it."""
    return "\n".join(
        [
            "Label acquisition and section-14 extraction",
            "",
            f"  trial-relevant submissions with a label  {report.submissions:>9,}",
            f"    PDFs cached                            {report.fetched:>9,}",
            f"    download failed                        {report.failed:>9,}",
            f"    scanned (no extractable text)          {report.scanned:>9,}",
            f"    unreadable                             {report.unreadable:>9,}",
            f"    readable                               {report.readable:>9,}",
            "",
            f"  with a section 14                        {report.with_section_14:>9,}"
            f"  ({100 * report.section_14_rate:.1f}%)",
            f"  with a section 1                         {report.with_section_1:>9,}",
            f"  numbered 14.N subsections                "
            f"{report.numbered_subsections:>9,}",
            "",
            f"  trial descriptions extracted             {len(report.descriptions):>9,}",
            f"    naming an NCT id                       {report.with_nct:>9,}",
            f"    stating an enrollment count            {report.with_enrollment:>9,}",
        ]
    )


__all__ = [
    "ACRONYM",
    "DESIGN",
    "ENROLLMENT",
    "NCT",
    "SECTION_1",
    "SECTION_14",
    "SECTION_14_UNNUMBERED",
    "SUBSECTION",
    "LabelReading",
    "LabelsReport",
    "SubmissionIndication",
    "TrialDescription",
    "build",
    "describe_trials",
    "is_own_drug_name",
    "normalise_nct",
    "read_label",
    "render",
    "section",
    "split_subsections",
]


def build(
    data: RawData,
    cache_root: Path,
    limit: int | None = None,
    workers: int = 6,
    parsers: int = 16,
) -> LabelsReport:
    """Fetch every trial-relevant submission's label and read it.

    One PDF can serve several submissions, so downloads are deduplicated by
    URL — 4,416 submissions cost 4,169 fetches. Fetching is threaded because it
    is entirely network-bound; parsing stays single-threaded, where it is CPU
    bound and pdfplumber is not worth the contention.
    """
    # The drug's own names, so a table column reading "ABILIFY 15" is not
    # mistaken for a trial called ABILIFY 15.
    own_names: dict[str, set[str]] = {}
    canonical_of: dict[str, str] = {}
    for row in read_rows(data.applications):
        parsed = appno.from_parts(row.get("ApplType"), row.get("ApplNo"))
        if parsed.is_application:
            canonical_of[parsed.digits] = parsed.canonical
    for row in read_rows(data.products):
        application_key = canonical_of.get(appno.pad(row.get("ApplNo")), "")
        if not application_key:
            continue
        for field in ("DrugName", "ActiveIngredient"):
            for part in (row.get(field) or "").split(";"):
                word = part.strip().upper()
                if len(word) >= 4:
                    own_names.setdefault(application_key, set()).add(word)
                    own_names[application_key].add(word.split()[0])

    spine = approval_events.build(data)
    relevant = {
        f"{row.application}|{row.submission_type}|{row.submission_no}": row
        for row in spine.rows
        if row.trial_relevant == "true"
    }
    wanted: list[label_pdf.LabelDocument] = []
    for document in label_pdf.read_label_documents(data):
        parsed = appno.from_parts(None, document.application)
        for prefix in ("NDA", "BLA"):
            key = (
                f"{prefix}{parsed.digits}|{document.submission_type}"
                f"|{document.submission_no}"
            )
            if key in relevant:
                wanted.append(document._replace(application=key.split("|")[0]))
                break
    if limit is not None:
        # ApplicationDocs is ordered by application number, which is
        # effectively chronological, so a plain head() samples only the oldest
        # labels — the pre-PLR, frequently scanned ones. Measured: the first 60
        # were 87% pre-2008 where the whole set is 72% post-2008, which made a
        # pilot report 16.7% against a 90% gate. Newest first instead, which is
        # also the order the plan says to process in.
        wanted.sort(
            key=lambda d: relevant[d.key].approval_date if d.key in relevant else "",
            reverse=True,
        )
        wanted = wanted[:limit]

    urls = list(dict.fromkeys(document.url for document in wanted))
    _log.info("%d submissions, %d distinct PDFs", len(wanted), len(urls))
    cached: dict[str, Path] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as downloads:
        pending_fetch = {
            downloads.submit(label_pdf.fetch, url, cache_root): url for url in urls
        }
        for finished_fetch in as_completed(pending_fetch):
            path = finished_fetch.result()
            if path is None:
                failed += 1
            else:
                cached[pending_fetch[finished_fetch]] = path
    _log.info("%d cached, %d failed", len(cached), failed)

    # Parsing, not downloading, is the cost: ~3.7 s a label single-threaded,
    # which is 4.5 hours over the whole set. It is CPU-bound, so a process pool
    # is what helps; each worker takes a path and returns text.
    texts: dict[str, str] = {}
    unreadable = 0
    if parsers <= 1:
        # Serial: no pool overhead for one worker, and a pool would also
        # require every callable to pickle, which rules out a stubbed fetcher.
        for url, path in cached.items():
            got = label_pdf.extract_text(path)
            if got is None:
                unreadable += 1
            else:
                texts[url] = got[0]
    else:
        with ProcessPoolExecutor(max_workers=parsers) as parsing:
            pending_parse = {
                parsing.submit(label_pdf.extract_text, path): url
                for url, path in cached.items()
            }
            for finished_parse in as_completed(pending_parse):
                got = finished_parse.result()
                if got is None:
                    unreadable += 1
                else:
                    texts[pending_parse[finished_parse]] = got[0]
    _log.info("%d parsed, %d unreadable", len(texts), unreadable)

    # One digest per distinct PDF, computed once. The cache is keyed on URL,
    # so two submissions sharing a URL trivially share a digest; two filed
    # under different URLs are compared on their bytes.
    shas: dict[str, str] = {}
    for url, path in cached.items():
        try:
            shas[url] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:  # pragma: no cover - a cached file vanishing mid-run
            shas[url] = ""

    descriptions: list[TrialDescription] = []
    indications: list[SubmissionIndication] = []
    scanned = with_14 = with_1 = subsections = 0
    seen_url: dict[str, LabelReading] = {}
    for document in wanted:
        if document.url not in texts:
            continue
        if document.url in seen_url:
            reading = seen_url[document.url]
        else:
            text = texts[document.url]
            if len(text) < label_pdf.MIN_TEXT_CHARS:
                scanned += 1
                seen_url[document.url] = LabelReading(document.key, "", "", 0, ())
                continue
            reading = read_label(
                document.key,
                document.application,
                document.submission_type,
                document.submission_no,
                text,
                frozenset(own_names.get(document.application, set())),
            )
            seen_url[document.url] = reading
        if not reading.section_14:
            continue
        with_14 += 1
        subsections += reading.subsections
        if reading.section_1:
            with_1 += 1
            indications.append(
                SubmissionIndication(
                    application=document.application,
                    submission_type=document.submission_type,
                    submission_no=document.submission_no,
                    text=reading.section_1.replace("\n", " ").replace("\t", " "),
                )
            )
        for described in reading.descriptions:
            descriptions.append(
                described._replace(
                    application=document.application,
                    submission_type=document.submission_type,
                    submission_no=document.submission_no,
                    label_sha=shas.get(document.url, ""),
                )
            )
    return LabelsReport(
        descriptions=tuple(descriptions),
        indications=tuple(indications),
        submissions=len(wanted),
        fetched=len(cached),
        failed=failed,
        scanned=scanned,
        unreadable=unreadable,
        with_section_14=with_14,
        with_section_1=with_1,
        with_nct=sum(1 for d in descriptions if d.nct_id),
        with_enrollment=sum(1 for d in descriptions if d.enrollment),
        numbered_subsections=subsections,
    )
