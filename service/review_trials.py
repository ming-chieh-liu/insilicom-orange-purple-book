"""The review route: trials FDA reviews name by sponsor protocol id (D26, D27).

FDA reviews cite the studies behind an approval by the sponsor's protocol
number — ``Study DR-103-301``, ``Trial GS-US-342-1202`` — which ClinicalTrials.gov
stores as ``orgStudyId`` or as a secondary id (``GOG-0240``). An **exact** match
therefore identifies the trial with no judgement at all; measured 70/70 on the
pilot. What the review does *not* say without reading is why it names the
trial — pivotal, supporting, pharmacology, an earlier approval's evidence, a
confirmatory study still running — and that is left to the role judges
(``docs/prompts/review-trial-role.md``).

Deterministic steps here, per ``docs/runbooks/review-trial-role.md``:

1. collect each in-scope submission's review texts (fetched politely; a PDF
   with no text layer is skipped, per file, never by year);
2. capture the token after ``Study|Trial|Protocol`` that has letters **and**
   digits — abbreviations (``KN555``, ``104a``) are not expanded;
3. keep it only if it names **exactly one** registry study;
4. guards: started on or before the approval; the lead sponsor is the
   applicant **or** the trial names the application's drug; the submission's
   reviews name the drug (catches ``ApplicationDocs`` mislinks);
5. one unit per ``(submission, trial)``; within an application only the
   earliest submission naming a trial is judged (D16 inside the route; P5
   applies D16 across the label and review routes).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from source import label_pdf, polite_fetch, review_docs
from source.trial_studies import StudyFacts
from utility.logging import get_logger

_log = get_logger(__name__)

#: The token after a study word. A line break inside an id ("GS-US-281- 0102")
#: is repaired by :func:`repair`.
MENTION = re.compile(
    r"\b(?:Stud(?:y|ies)|Trials?|Protocols?)\s+(?:No\.?\s*|Number\s*|ID\s*)?\(?"
    r"([A-Za-z0-9][A-Za-z0-9\-_/\.]*(?:-\s\d+)?[A-Za-z0-9]?)"
)

#: A section heading line in extracted review text: numbered ("7 Clinical/
#: Statistical- Efficacy") or upper case.
HEADING = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Z][^\n]{3,90}|[A-Z][A-Z /,&\-]{6,80})\s*$", re.M
)

#: Below this many characters a review PDF has no usable text layer (a scan).
MIN_TEXT: int = 2_000

#: Characters of context kept either side of a mention.
WINDOW: int = 700

#: Passages kept per unit.
MAX_PASSAGES: int = 8

#: Company-name words that say nothing about which company it is.
SPONSOR_STOP: frozenset[str] = frozenset({
    "INC", "LLC", "LTD", "CORP", "CO", "PHARMACEUTICALS", "PHARMACEUTICAL", "PHARMA",
    "THERAPEUTICS", "THE", "AND", "LABORATORIES", "LABS", "COMPANY", "GMBH", "AG", "SA",
    "USA", "US", "BIOTECH", "BIOSCIENCES", "HEALTHCARE", "INTERNATIONAL", "GROUP",
    "HOLDINGS", "SCIENCES", "DIVISION", "OF", "NORTH", "AMERICA",
})

#: Mention outcomes.
ACCEPTED = "accepted"
NOT_IN_REGISTRY = "not_in_registry"
SEVERAL_STUDIES = "several_studies"
START_AFTER_APPROVAL = "start_after_approval"
SPONSOR_AND_DRUG_DIFFER = "sponsor_and_drug_differ"
DOCUMENT_NOT_ABOUT_DRUG = "document_not_about_drug"


class ProtocolStudy(NamedTuple):
    """A registry study as the protocol-id index holds it."""

    nct_id: str
    sponsor: str
    start_date: str
    title: str
    interventions: str
    #: The registry's acronym, so P3 can match a label's trial name to it (D29).
    acronym: str = ""


class ReviewText(NamedTuple):
    """One readable review document's text."""

    label: str
    text: str


class ReviewMention(NamedTuple):
    """One protocol-id mention and what became of it."""

    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    document: str
    heading: str
    protocol_id: str
    nct_id: str
    outcome: str
    #: Registry acronym of the matched study; P3 matches label names to it.
    acronym: str = ""


class ReviewReport(NamedTuple):
    """The review route's deterministic pass."""

    mentions: tuple[ReviewMention, ...]
    units: tuple[dict[str, str], ...]
    submissions_with_reviews: int
    documents: Counter[str]
    outcomes: Counter[str]
    later_in_route: int


def normalise_id(token: str) -> str:
    """The form protocol ids are compared in.

    >>> normalise_id("GS-US-342-1202"), normalise_id("gog 0240")
    ('GSUS3421202', 'GOG0240')
    """
    return re.sub(r"[^A-Z0-9]", "", token.upper())


def repair(token: str) -> str:
    """Rejoin an id a line break split, and drop trailing punctuation.

    >>> repair("GS-US-281- 0102."), repair("DR-103-301")
    ('GS-US-281-0102', 'DR-103-301')
    """
    return re.sub(r"-\s(\d)", r"-\1", token).strip(".")


def candidate_tokens(text: str) -> list[tuple[str, int]]:
    """``(token, position)`` for every id-shaped mention: letters **and** digits.

    Bare numbers ("Study 301") and words ("Study design") are dropped.

    >>> [t for t, _ in candidate_tokens("Study DR-103-301 and Study 301 and Study design, Trial GOG-0240")]
    ['DR-103-301', 'GOG-0240']
    """
    out: list[tuple[str, int]] = []
    for match in MENTION.finditer(text):
        token = repair(match.group(1))
        key = normalise_id(token)
        if len(key) >= 4 and re.search("[A-Z]", key) and re.search(r"\d", key):
            out.append((token, match.start()))
    return out


def build_index(facts: Iterable[StudyFacts]) -> dict[str, list[ProtocolStudy]]:
    """Normalised ``orgStudyId`` and secondary ids → the studies registered under them."""
    index: dict[str, list[ProtocolStudy]] = defaultdict(list)
    for study in facts:
        record = ProtocolStudy(study.nct_id, study.lead_sponsor, study.start_date,
                               study.brief_title, " | ".join(study.interventions),
                               study.acronym)
        for identifier in {study.org_study_id, *study.secondary_ids}:
            key = normalise_id(identifier)
            if len(key) >= 4:
                index[key].append(record)
    return dict(index)


def protocol_trials(
    text: str, index: dict[str, list[ProtocolStudy]]
) -> list[tuple[str, str]]:
    """``(nct_id, protocol_id)`` for every full protocol id in ``text`` that
    names **exactly one** registry study (D35).

    The literature counterpart of the review route's matching: the same token
    rule as :func:`candidate_tokens` (after Study/Trial/Protocol, letters and
    digits, at least four characters, no abbreviation expansion) and the same
    one-study rule. Bare numbers and ids several studies share are dropped.
    Whether the trial supported an approval is left to the judge.

    >>> ix = {"DR103301": [ProtocolStudy("NCT1", "Teva", "2009-10", "t", "i")],
    ...       "AB1234": [ProtocolStudy("NCT2", "", "", "", ""),
    ...                  ProtocolStudy("NCT3", "", "", "", "")]}
    >>> protocol_trials("In Study DR-103-301 and Study AB-1234 and Study 301", ix)
    [('NCT1', 'DR-103-301')]
    """
    out: dict[str, str] = {}
    for token, _ in candidate_tokens(text):
        hits = {study.nct_id for study in index.get(normalise_id(token), [])}
        if len(hits) == 1:
            out.setdefault(next(iter(hits)), token)
    return list(out.items())


def sponsor_words(name: str) -> set[str]:
    """Distinctive words of a company name.

    >>> sorted(sponsor_words("Gilead Sciences, Inc.")), sorted(sponsor_words("GILEAD SCIENCES INC"))
    (['GILEAD'], ['GILEAD'])
    """
    return {w for w in re.findall(r"[A-Z]{3,}", name.upper()) if w not in SPONSOR_STOP}


def heading_before(text: str, position: int) -> str:
    """The nearest section heading above ``position``, or ``(no heading)``."""
    found = list(HEADING.finditer(text, max(0, position - 8_000), position))
    return " ".join(found[-1].group(0).split())[:90] if found else "(no heading)"


def judge_mention(
    token: str,
    index: dict[str, list[ProtocolStudy]],
    approval_date: str,
    applicant: str,
    drug_names: set[str],
    package_names_drug: bool,
) -> tuple[ProtocolStudy | None, str]:
    """The study a mention names and its outcome (see the module docstring).

    >>> ix = {"DR103301": [ProtocolStudy("NCT1", "Teva", "2009-10", "DR-103 study", "DR-103")]}
    >>> judge_mention("DR-103-301", ix, "2013-03-28", "TEVA BRANDED", {"QUARTETTE"}, True)[1]
    'accepted'
    >>> judge_mention("DR-103-301", ix, "2008-01-01", "TEVA BRANDED", set(), True)[1]
    'start_after_approval'
    >>> judge_mention("DR-103-301", ix, "2013-03-28", "OTHERCO", {"QUARTETTE"}, True)[1]
    'sponsor_and_drug_differ'
    >>> judge_mention("XX-1", ix, "2013-03-28", "TEVA", set(), True)[1]
    'not_in_registry'
    """
    hits = index.get(normalise_id(token), [])
    ncts = {h.nct_id for h in hits}
    if not ncts:
        return None, NOT_IN_REGISTRY
    if len(ncts) > 1:
        return None, SEVERAL_STUDIES
    study = hits[0]
    if not package_names_drug:
        return study, DOCUMENT_NOT_ABOUT_DRUG
    if not study.start_date or study.start_date[:10] > approval_date:
        return study, START_AFTER_APPROVAL
    about = f"{study.title} {study.interventions}".upper()
    if not (sponsor_words(study.sponsor) & sponsor_words(applicant)
            or any(name in about for name in drug_names)):
        return study, SPONSOR_AND_DRUG_DIFFER
    return study, ACCEPTED


def _cached_text(path: Path) -> tuple[Path, str | None]:
    """A PDF's text, via a ``.txt`` beside it so a review is parsed only once.

    Module level so a process pool can call it.
    """
    sidecar = path.with_suffix(".txt")
    if sidecar.is_file():
        return path, sidecar.read_text(encoding="utf-8")
    got = label_pdf.extract_text(path)
    if got is None:
        return path, None
    sidecar.write_text(got[0], encoding="utf-8")
    return path, got[0]


def collect_texts(
    documents: Iterable[review_docs.ReviewDocument],
    cache_root: Path,
    *,
    offline: bool = False,
    parsers: int = 1,
    fetch: Callable[..., polite_fetch.FetchResult] = polite_fetch.fetch_all,
) -> tuple[dict[str, list[ReviewText]], Counter[str]]:
    """Each submission's readable review texts, keyed ``digits|type|number``.

    Two passes of politely-throttled fetching: the review documents themselves
    (PDFs, or table-of-contents pages), then the component PDFs the pages list.
    """
    docs = list(documents)
    stats: Counter[str] = Counter()
    first = fetch([d.url for d in docs], cache_root, offline=offline,
                  require_pdf=lambda url: not review_docs.is_toc(url))
    parts: list[tuple[review_docs.ReviewDocument, str, str]] = []
    for doc in docs:
        path = first.cached.get(doc.url)
        if path is None:
            stats["document_not_fetched"] += 1
            continue
        if review_docs.is_toc(doc.url):
            components = review_docs.toc_components(path.read_text(encoding="latin-1"))
            stats["toc_pages"] += 1
            if not components:
                stats["toc_without_components"] += 1
            parts.extend((doc, f"{doc.label} / {c}", review_docs.component_url(doc.url, f))
                         for c, f in components)
        else:
            parts.append((doc, doc.label, doc.url))
    second = fetch([u for _, _, u in parts if u not in first.cached], cache_root, offline=offline)
    cached = {**first.cached, **second.cached}
    paths = {cached[u] for _, _, u in parts if u in cached}
    texts: dict[Path, str | None] = {}
    if parsers <= 1:
        texts = dict(_cached_text(p) for p in paths)
    else:
        with ProcessPoolExecutor(max_workers=parsers) as pool:
            for job in as_completed([pool.submit(_cached_text, p) for p in paths]):
                path, text = job.result()
                texts[path] = text
    out: dict[str, list[ReviewText]] = defaultdict(list)
    for doc, label, url in parts:
        path = cached.get(url)
        if path is None:
            stats["component_not_fetched"] += 1
            continue
        text = texts.get(path)
        if not text or len(text.strip()) < MIN_TEXT:
            stats["no_text_layer"] += 1
            continue
        stats["readable"] += 1
        key = f"{doc.application.zfill(6)}|{doc.submission_type}|{doc.submission_no}"
        out[key].append(ReviewText(label, text))
    stats["refused"] = len(first.refused | second.refused)
    return dict(out), stats


def build(
    submissions: Iterable[tuple[str, str, str, str]],
    texts: dict[str, list[ReviewText]],
    index: dict[str, list[ProtocolStudy]],
    *,
    applicants: dict[str, str],
    drug_names: dict[str, set[str]],
    brands: dict[str, set[str]],
    approval_sentences: dict[str, str],
) -> ReviewReport:
    """Mentions and role units for every in-scope submission with reviews.

    ``submissions`` are ``(canonical application, type, number, approval date)``;
    the other maps are keyed by the application's six digits, except
    ``approval_sentences`` (``application|type|number``, the letter's sentence).
    """
    mentions: list[ReviewMention] = []
    accepted: dict[tuple[str, str], list[tuple[str, str, str, str]]] = defaultdict(list)
    approved_on: dict[str, str] = {}
    documents: Counter[str] = Counter()
    served = 0
    for application, submission_type, submission_no, approval_date in submissions:
        digits = application[-6:]
        key = f"{application}|{submission_type}|{submission_no}"
        package = texts.get(f"{digits}|{submission_type}|{submission_no}", [])
        if not package:
            continue
        served += 1
        approved_on[key] = approval_date
        names = drug_names.get(digits, set())
        package_names_drug = any(n in r.text.upper() for r in package for n in names)
        for review in package:
            documents[review.label] += 1
            for token, position in candidate_tokens(review.text):
                study, outcome = judge_mention(token, index, approval_date,
                                               applicants.get(digits, ""), names,
                                               package_names_drug)
                heading = heading_before(review.text, position)
                mentions.append(ReviewMention(application, submission_type, submission_no,
                                              approval_date, review.label, heading, token,
                                              study.nct_id if study else "", outcome,
                                              study.acronym if study else ""))
                if outcome == ACCEPTED and study is not None:
                    snippet = " ".join(review.text[max(0, position - WINDOW):position + WINDOW].split())
                    accepted[(key, study.nct_id)].append((token, review.label, heading, snippet))

    first_by_trial: dict[tuple[str, str], str] = {}
    for key, nct in accepted:
        application = key.split("|", 1)[0]
        held = first_by_trial.get((application, nct))
        if held is None or approved_on[key] < approved_on[held]:
            first_by_trial[(application, nct)] = key
    units: list[dict[str, str]] = []
    later = 0
    for (key, nct), found in sorted(accepted.items()):
        if first_by_trial[(key.split("|", 1)[0], nct)] != key:
            later += 1
            continue
        application, submission_type, submission_no = key.split("|")
        passages, seen = [], set()
        for _token, label, heading, snippet in found:
            if snippet[:200] in seen:
                continue
            seen.add(snippet[:200])
            passages.append(f"[{label} · {heading}] … {snippet} …")
        brand_names = " / ".join(sorted(brands.get(application[-6:], set())))
        sentence = approval_sentences.get(key) or "approval letter not available"
        units.append({
            "unit_id": f"{key}|{nct}",
            "protocol_id": found[0][0],
            "approval": f"{brand_names} · {application} {submission_type} {submission_no} · "
                        f"approved {approved_on[key]} · {sentence}",
            "text": "\n\n".join(passages[:MAX_PASSAGES]),
        })
    outcomes = Counter(m.outcome for m in mentions)
    _log.info("review route: %d submissions with reviews, %d mentions, %d units",
              served, len(mentions), len(units))
    return ReviewReport(tuple(mentions), tuple(units), served, documents, outcomes, later)


def render(report: ReviewReport, fetch_stats: Counter[str]) -> str:
    """The pass report, as ``--mode reviews`` prints it."""
    lines = [
        "Review route — trials named by sponsor protocol id (D26)",
        "",
        "  documents:",
    ]
    for name, count in sorted(fetch_stats.items()):
        lines.append(f"    {name:<38}{count:>9,}")
    lines += [
        "",
        f"  submissions with readable reviews        {report.submissions_with_reviews:>9,}",
        "  readable documents by type:",
    ]
    for name, count in report.documents.most_common():
        lines.append(f"    {name:<38}{count:>9,}")
    lines += ["", "  id-shaped mentions by outcome:"]
    for name, count in report.outcomes.most_common():
        lines.append(f"    {name:<38}{count:>9,}")
    lines += [
        "",
        f"  role units (earliest submission per trial) {len(report.units):>7,}",
        f"  later mentions in the same application     {report.later_in_route:>7,}",
    ]
    return "\n".join(lines)


__all__ = [
    "ACCEPTED",
    "DOCUMENT_NOT_ABOUT_DRUG",
    "MIN_TEXT",
    "NOT_IN_REGISTRY",
    "SEVERAL_STUDIES",
    "SPONSOR_AND_DRUG_DIFFER",
    "START_AFTER_APPROVAL",
    "ProtocolStudy",
    "ReviewMention",
    "ReviewReport",
    "ReviewText",
    "build",
    "build_index",
    "candidate_tokens",
    "collect_texts",
    "heading_before",
    "judge_mention",
    "normalise_id",
    "protocol_trials",
    "render",
    "repair",
    "sponsor_words",
]
