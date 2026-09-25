"""Approval letters as the per-submission indication source (D25).

Each in-scope approval's letter is fetched and its "provides for …" sentence
extracted and flagged as indication-bearing or not. The sentence states what
*that* approval covers — "the use of Veklury … for the treatment of COVID-19 in
… patients who are not hospitalized" — where the label's section 1 is
cumulative and says everything the drug is approved for by then.

**The sentence has three measured shapes**, all handled by :func:`indication_sentence`:

* ``This … application provides for the use of X for …`` — the usual one.
* ``… proposes to add the indication of: persistent, recurrent, or metastatic
  carcinoma of the cervix`` — Avastin's cervical cancer supplement, with no
  full stop before the next heading.
* A full stop inside a number — ``Veltin … Gel, 1.2%/0.025%`` — which is not
  the end of the sentence. The pilot's extractor stopped there.

**This module reads; it does not code (D30).** The sentence goes out as raw
text; :mod:`service.indication_normalize` codes it, with every other indication
source, into one table.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from service import approval_events
from source import approval_letter, label_pdf
from utility import application_number as appno
from utility.logging import get_logger

_log = get_logger(__name__)

#: Where the indication clause starts. Each alternative is a wording measured in
#: real letters: "provides for" (with or without a colon), "provides updates",
#: "proposes to add …" and "proposes a new indication", "is indicated
#: for/to/as/in", and the general form "the application / supplement / sNDA /
#: sBLA + an action verb" ("adds the following new indication", "proposes the
#: use of", "provides efficacy and safety data for", "provides an update"), and
#: the pre-2010 form "Your request to supplement your biologics license
#: application for X to expand the indication to include … has been approved".
START = re.compile(
    r"\b(?:provides?|provided)\s+(?:for\b[:\s]*|updates?\b|revisions?\b)"
    r"|\bpropos(?:es|ed)\s+(?:to\s+(?:add|expand|extend|revise|update|include|change)\b"
    r"|an?\s+(?:new\s+)?indications?\b)"
    r"|\b(?:application|supplement|sNDA|sBLA|NDA|BLA)\s+"
    r"(?:provides|proposes|adds|updates|revises|expands|extends|includes|requests|seeks)\b"
    r"|\b(?:is|are)\s+(?:now\s+)?indicated\s+(?:for|as|in|to)\s+"
    r"|\byour\s+request\s+to\s+supplement\b",
    re.IGNORECASE,
)

#: The submission noun the general form starts on; dropped so every sentence
#: starts at its verb.
SUBJECT = re.compile(r"^(?:application|supplement|sNDA|sBLA|NDA|BLA)\s+", re.IGNORECASE)

#: What follows the clause when it is not a full stop: the letter's next heading
#: or its next stock paragraph.
NEXT_SECTION = re.compile(
    r"\s(?:APPROVAL\s*(?:&|AND)\s*LABELING|CONTENT OF LABELING|"
    r"We have completed (?:our|the) review|REQUIRED PEDIATRIC|PEDIATRIC RESEARCH|"
    r"POSTMARKETING|LABELING\s|LICENSING|WAIVER\s)"
)

#: Words after which a full stop does not end the sentence.
ABBREVIATIONS: frozenset[str] = frozenset(
    {"e.g", "i.e", "no", "inc", "vs", "approx", "u.s", "dr", "st", "fig", "ltd", "co"}
)

#: A letter sentence states an indication, rather than a labeling, dosing-form
#: or manufacturing change, when it uses the word itself or a purpose phrase.
#: Only such sentences may become a submission's indication source: a false one
#: would override a correct label §1, while a missed one only falls back to it,
#: so the rule errs towards rejecting. Measured on 1,404 extracted sentences:
#: the keyword alone keeps 722 and loses true indications written as
#: "provides for the use of X for the treatment of …" (263 more) or "for the
#: relief of …".
INDICATION_BEARING = re.compile(
    r"\bindicat(?:ion|ions|ed)\b"
    r"|\bfor\s+the\s+(?:treatment|prevention|prophylaxis|relief|management|diagnosis)\s+of\b"
    r"|\bfor\s+(?:the\s+)?induction\s+of\b"
    r"|\bto\s+(?:treat|prevent|improve|reduce\s+the\s+risk\s+of)\b"
    r"|\bas\s+an\s+adjunct\s+to\b"
    r"|\bfor\s+use\s+in\s+(?:adult|pediatric|patients)",
    re.IGNORECASE,
)

#: A clause longer than this is a failed parse, not an indication.
MAX_SENTENCE: int = 1_500


WHITESPACE = re.compile(r"\s+")

#: The pause between requests; a seam for tests.
_sleep = time.sleep

#: Status of one submission's letter.
OK = "ok"
NO_SENTENCE = "no_sentence"
SCANNED = "scanned"
UNREADABLE = "unreadable"
FETCH_FAILED = "fetch_failed"
NO_LETTER = "no_letter"
#: The server refused us (HTTP 403) or the circuit breaker stopped the run: not
#: evidence that the letter is missing; a later run fetches it.
BLOCKED = "blocked"
#: A cache-only run found no cached copy.
NOT_FETCHED = "not_fetched"

#: Seconds between requests to FDA. The first unthrottled run was banned after
#: about 1,500 letters at ~12 requests a second.
REQUEST_INTERVAL: float = 1.0

#: Refusals (HTTP 403) in a row that stop the run. Once the server bans an
#: address, every further request is wasted and can lengthen the ban.
BREAKER: int = 5



class LetterIndication(NamedTuple):
    """One in-scope submission's letter and what it says the approval covers."""

    application: str
    submission_type: str
    submission_no: str
    approval_date: str
    letter_url: str
    url_guessed: str
    shared_letter: str
    status: str
    sentence: str
    #: Whether the sentence states an indication (:data:`INDICATION_BEARING`).
    indication_bearing: str = "false"


class LetterReport(NamedTuple):
    """The letters pass, plus what it measured."""

    rows: tuple[LetterIndication, ...]
    by_status: tuple[tuple[str, int], ...]
    by_era_status: tuple[tuple[str, int], ...]
    guessed_found: int
    distinct_letters: int


def indication_sentence(text: str) -> str:
    """The letter's statement of what the approval covers, or ``""``.

    >>> indication_sentence("This Prior Approval sNDA provides for the use of "
    ...     "Veklury (remdesivir) for the treatment of COVID-19 in adults. "
    ...     "APPROVAL & LABELING We have completed our review")
    'provides for the use of Veklury (remdesivir) for the treatment of COVID-19 in adults'
    >>> indication_sentence("This supplement proposes to add the indication of: "
    ...     "carcinoma of the cervix APPROVAL & LABELING We have completed")
    'proposes to add the indication of: carcinoma of the cervix'
    >>> indication_sentence("This NDA provides for the use of Veltin Gel, "
    ...     "1.2%/0.025% for acne vulgaris. We have completed our review")
    'provides for the use of Veltin Gel, 1.2%/0.025% for acne vulgaris'
    >>> indication_sentence("It provides for use in pts, e.g. adults. Next.")
    'provides for use in pts, e.g. adults'
    >>> indication_sentence("This supplement provides for: \u2022 The addition of "
    ...     "autoimmune hepatitis to Warnings APPROVAL & LABELING We have")
    'provides for: \u2022 The addition of autoimmune hepatitis to Warnings'
    >>> indication_sentence("This application provides updates to labeling to change "
    ...     "the dose for adjuvant melanoma. APPROVAL & LABELING")
    'provides updates to labeling to change the dose for adjuvant melanoma'
    >>> indication_sentence("This application proposes a new indication for nivolumab "
    ...     "in advanced renal cell carcinoma. APPROVAL & LABELING")
    'proposes a new indication for nivolumab in advanced renal cell carcinoma'
    >>> indication_sentence("This application updates the prescribing information "
    ...     "with data from study R2810 in Section 8.4: Pediatric Use. APPROVAL")
    'updates the prescribing information with data from study R2810 in Section 8.4: Pediatric Use'
    >>> indication_sentence("Trutakna is indicated to reduce proteinuria in adults "
    ...     "with IgA nephropathy. The license")
    'is indicated to reduce proteinuria in adults with IgA nephropathy'
    >>> indication_sentence("This Prior Approval sBLA adds the following new "
    ...     "indication: IMFINZI for bladder cancer (NMIBC) APPROVAL & LABELING We")
    'adds the following new indication: IMFINZI for bladder cancer (NMIBC)'
    >>> indication_sentence("This supplemental biologics application proposes the "
    ...     "use of Stelara for the indication of Psoriatic Arthritis. APPROVAL")
    'proposes the use of Stelara for the indication of Psoriatic Arthritis'
    >>> indication_sentence("This supplemental biologics license application provides "
    ...     "efficacy and safety data for tildrakizumab in plaque psoriasis. APPROVAL")
    'provides efficacy and safety data for tildrakizumab in plaque psoriasis'
    >>> indication_sentence("Opdivo is indicated for the following: 1. Non-small cell "
    ...     "lung cancer. 2. Melanoma. APPROVAL & LABELING")
    'is indicated for the following: 1. Non-small cell lung cancer. 2. Melanoma'
    >>> indication_sentence("Dear Dr. Steck: Your request to supplement your biologics "
    ...     "license application for adalimumab to expand the indication to include "
    ...     "psoriatic arthritis has been approved. Sincerely")
    'Your request to supplement your biologics license application for adalimumab to expand the indication to include psoriatic arthritis has been approved'
    >>> indication_sentence("Please refer to your application.")
    ''
    """
    flat = WHITESPACE.sub(" ", text)
    start = START.search(flat)
    if start is None:
        return ""
    tail = flat[start.start():start.start() + MAX_SENTENCE]
    end = len(tail)
    heading = NEXT_SECTION.search(tail)
    if heading is not None:
        end = heading.start()
    for position, char in enumerate(tail[:end]):
        if char != ".":
            continue
        following = tail[position + 1:position + 2]
        if following.isdigit():
            continue  # a decimal: "1.2%"
        word = re.findall(r"[A-Za-z.]+$", tail[:position])
        if word and word[-1].lower().rstrip(".") in ABBREVIATIONS:
            continue
        if re.search(r"(?:^|[\s:(])\d{1,2}$", tail[:position]):
            continue  # a list marker: "indicated for the following: 1. Non-Small …"
        rest = tail[position + 1:].lstrip()
        if re.match(r"\d{1,2}\.\s", rest):
            continue  # the next item of a numbered list, not a new sentence
        if not rest or rest[0].isupper() or rest[0].isdigit():
            end = position
            break
    return SUBJECT.sub("", tail[:end].strip(" .;:"))


def is_indication(sentence: str) -> bool:
    """Whether a letter sentence states an indication (D25).

    >>> is_indication("adds a new indication for the treatment of NSCLC")
    True
    >>> is_indication("provides for the use of Botox for the treatment of spasticity")
    True
    >>> is_indication("provides for the use of Flexeril for the relief of muscle spasm")
    True
    >>> is_indication("provides for changes to align the USPI with the reference USPI")
    False
    >>> is_indication("provides for a new 250 mg/1.25 mL prefilled autoinjector")
    False
    """
    return bool(INDICATION_BEARING.search(sentence))


def _digits(application: str) -> str:
    """The six-digit number of a canonical application id.

    >>> _digits("BLA125514")
    '125514'
    >>> _digits("NDA021574")
    '021574'
    """
    return re.sub(r"\D", "", application).zfill(6)


def _shared(url: str, users: int) -> bool:
    """Whether one letter covers several submissions.

    >>> _shared("https://x/125514Orig1s059,s060ltr.pdf", 1)
    True
    >>> _shared("https://x/125514Orig1s059ltr.pdf", 2)
    True
    >>> _shared("https://x/125514Orig1s059ltr.pdf", 1)
    False
    """
    name = url.rsplit("/", 1)[-1]
    return users > 1 or len(re.findall(r"s\d{3}", name)) > 1


def build(
    data: RawData,
    cache_root: Path,
    *,
    parsers: int = 1,
    guess_missing: bool = True,
    offline: bool = False,
    interval: float = REQUEST_INTERVAL,
) -> LetterReport:
    """Fetch, read and code the letter of every in-scope approval.

    Requests go out **one at a time, ``interval`` seconds apart**, and the run
    stops asking after :data:`BREAKER` refusals in a row; everything not yet
    fetched is then ``blocked`` rather than failed, and a later run resumes from
    the cache. ``offline`` reads only what is already cached and sends nothing.
    """
    spine = [row for row in approval_events.build(data).rows if row.trial_relevant == "true"]
    indexed: dict[str, list[label_pdf.LabelDocument]] = defaultdict(list)
    for document in approval_letter.read_letter_documents(data):
        key = f"{appno.pad(document.application)}|{document.submission_type}|{document.submission_no}"
        indexed[key].append(document)

    plan: dict[str, list[tuple[str, bool]]] = {}
    for row in spine:
        digits = _digits(row.application)
        key = f"{digits}|{row.submission_type}|{row.submission_no}"
        urls = [(d.url, False) for d in sorted(indexed.get(key, []), key=lambda d: d.document_date)]
        if not urls and guess_missing:
            urls = [
                (u, True)
                for u in approval_letter.guessed_urls(
                    digits, row.submission_type, row.submission_no, row.approval_date
                )
            ]
        plan[f"{row.application}|{row.submission_type}|{row.submission_no}"] = urls

    distinct = list(dict.fromkeys(url for urls in plan.values() for url, _ in urls))
    users: Counter[str] = Counter(
        url for urls in plan.values() for url, guessed in urls if not guessed
    )
    _log.info("%d in-scope submissions, %d distinct letter addresses", len(spine), len(distinct))
    cached: dict[str, Path] = {}
    failure: dict[str, str] = {}
    refused = 0
    tripped = False
    asked = False
    for url in distinct:
        if label_pdf.is_cached(url, cache_root):
            path: Path | None = label_pdf.cache_path(url, cache_root)
        elif offline or tripped:
            continue  # left unfetched: blocked or not_fetched below
        else:
            if asked:
                _sleep(interval)
            asked = True
            path, reason = label_pdf.fetch_status(url, cache_root)
            if reason == label_pdf.FORBIDDEN:
                refused += 1
                if refused >= BREAKER:
                    tripped = True
                    _log.warning(
                        "%d refusals in a row: the server is refusing this address; "
                        "stopping, the rest stays blocked for a later run", refused
                    )
                continue
            refused = 0
            if path is None:
                failure[url] = reason
                continue
        if path is not None and approval_letter.is_pdf(path):
            cached[url] = path
        elif path is not None:
            path.unlink(missing_ok=True)  # an HTML answer is not a letter
            failure[url] = label_pdf.NOT_FOUND
    texts: dict[str, str] = {}
    unreadable: set[str] = set()
    if parsers <= 1:
        for url, path in cached.items():
            got = label_pdf.extract_text(path)
            if got is None:
                unreadable.add(url)
            else:
                texts[url] = got[0]
    else:
        with ProcessPoolExecutor(max_workers=parsers) as parsing:
            jobs = {parsing.submit(label_pdf.extract_text, path): url for url, path in cached.items()}
            for finished_parse in as_completed(jobs):
                got = finished_parse.result()
                if got is None:
                    unreadable.add(jobs[finished_parse])
                else:
                    texts[jobs[finished_parse]] = got[0]

    rows: list[LetterIndication] = []
    for row in spine:
        key = f"{row.application}|{row.submission_type}|{row.submission_no}"
        chosen: tuple[str, bool, str, str] | None = None
        for url, guessed in plan[key]:
            if url in texts:
                text = texts[url]
                if len(text) < label_pdf.MIN_TEXT_CHARS:
                    status, sentence = SCANNED, ""
                else:
                    sentence = indication_sentence(text)
                    status = OK if sentence else NO_SENTENCE
            elif url in unreadable:
                status, sentence = UNREADABLE, ""
            elif url not in failure:
                status, sentence = (NOT_FETCHED if offline else BLOCKED), ""
            elif guessed:
                continue  # a guess that answered "not here" is not a failure
            else:
                status, sentence = FETCH_FAILED, ""
            if chosen is None or status == OK:
                chosen = (url, guessed, status, sentence)
            if status == OK:
                break
        if chosen is None:
            chosen = ("", False, NO_LETTER, "")
        url, guessed, status, sentence = chosen
        rows.append(
            LetterIndication(
                application=row.application,
                submission_type=row.submission_type,
                submission_no=row.submission_no,
                approval_date=row.approval_date,
                letter_url=url,
                url_guessed="true" if url and guessed else "false",
                shared_letter="true" if url and _shared(url, users.get(url, 1)) else "false",
                status=status,
                sentence=sentence,
                indication_bearing="true" if is_indication(sentence) else "false",
            )
        )
    by_status = Counter(r.status for r in rows)
    by_era = Counter(f"{approval_events.era_of(r.approval_date) or '(undated)'} {r.status}" for r in rows)
    guessed_found = sum(1 for r in rows if r.url_guessed == "true" and r.status == OK)
    _log.info("letters: %s", dict(by_status))
    return LetterReport(
        rows=tuple(rows),
        by_status=tuple(by_status.most_common()),
        by_era_status=tuple(sorted(by_era.items())),
        guessed_found=guessed_found,
        distinct_letters=len(cached),
    )


def render(report: LetterReport) -> str:
    """The pass report, as ``--mode letters`` prints it."""
    total = len(report.rows)
    bearing = sum(1 for r in report.rows if r.indication_bearing == "true")
    lines = [
        "Approval letters — the per-submission indication text (D25, D30)",
        "",
        f"  in-scope submissions                     {total:>9,}",
        f"  distinct letters cached                  {report.distinct_letters:>9,}",
        f"  letters found at a guessed address       {report.guessed_found:>9,}",
        f"  indication-bearing sentences             {bearing:>9,}",
        "",
        "  by status:",
    ]
    for status, count in report.by_status:
        lines.append(f"    {status:<38}{count:>9,}")
    lines += ["", "  by era and status:"]
    for name, count in report.by_era_status:
        lines.append(f"    {name:<38}{count:>9,}")
    return "\n".join(lines)


__all__ = [
    "ABBREVIATIONS",
    "INDICATION_BEARING",
    "BLOCKED",
    "BREAKER",
    "FETCH_FAILED",
    "MAX_SENTENCE",
    "NO_LETTER",
    "NO_SENTENCE",
    "NOT_FETCHED",
    "OK",
    "REQUEST_INTERVAL",
    "SCANNED",
    "UNREADABLE",
    "LetterIndication",
    "LetterReport",
    "build",
    "indication_sentence",
    "is_indication",
    "render",
]
