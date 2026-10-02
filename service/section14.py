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
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from service import approval_events
from source import label_pdf, polite_fetch
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


#: Characters of a §14 subsection kept per description (D37).
SUBSECTION_KEEP: int = 3000


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
    #: The heading as printed, continuation lines joined (D34). A long heading
    #: wraps in the PDF — "14.1 … HER2-negative Locally Advanced" / "or
    #: Metastatic Breast Cancer" — and :attr:`heading`, one line, loses the
    #: disease. ``heading`` is kept as it was because unit ids are built from
    #: it; this is what the indication coder reads.
    heading_full: str = ""
    #: D37: the whole §14 subsection this description came from, up to
    #: :data:`SUBSECTION_KEEP` characters — what a B-route judge reads, since
    #: one sentence rarely states the design, population and comparator.
    subsection: str = ""


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


#: A line that marks a page break in an FDA label PDF, compared with its
#: whitespace removed and lower-cased — PDF extraction splits words at random
#: ("please visi t", "information , please"): the FDA watermark ("Reference
#: ID: 4093616", its two notice lines) or a short line holding "Page 26 of 37"
#: ("…clean copy 27March02) Page 12 of 39", "- Page 39 of 51 -") or only "Page 7".
_MARK_SQUEEZED = re.compile(
    r"^(?:referenceid:\d+.*"
    r"|thislabelmaynotbethelatestapprovedbyfda\.?"
    r"|forcurrentlabelinginformation,?pleasevisit.*)$"
)
_PAGE_FOOTER = re.compile(r"\bpage\s*\|?\s*\d{1,4}\s+of\s+\d{1,4}\b|^\s*page\s*\|?\s*\d{1,4}\s*$",
                          re.IGNORECASE)
#: Longest line a page footer may be.
FOOTER_CHARS: int = 120


def is_page_mark(line: str) -> bool:
    """Whether a line marks a PDF page break.

    >>> [is_page_mark(x) for x in ("Reference ID: 4093616 24", "For current labeling information, "
    ...  "please visi t https://www.fda.gov/drugsatfda", "Page 7 of 18", "Page 3 of 9 Confidential",
    ...  "ApprovletS037.doc (clean copy 27March02) Page 12 of 39", "Reference ID: 3 Page 2",
    ...  "In 616 patients the page count", "- Page 39 of 51 -", "Page 7")]
    [True, True, True, True, True, True, False, True, True]
    """
    squeezed = re.sub(r"\s+", "", line).lower()
    if not squeezed:
        return False
    if _MARK_SQUEEZED.match(squeezed):
        return True
    return len(line.strip()) <= FOOTER_CHARS and bool(_PAGE_FOOTER.search(line.strip()))


#: Furniture that sits beside a page mark: a bare page number, an
#: "NDA 22369/S-008" header, an "Approved Labeling Text – …" line.
PAGE_FURNITURE = re.compile(
    r"^\s*(?:\d{1,4}|(?:NDA|BLA|sNDA|sBLA)\s*[\d\-]+(?:\s*/\s*S-?\d+)?.*"
    r"|Approved Labeling Text\b.*)\s*$",
    re.IGNORECASE,
)

#: How many lines either side of a page mark may hold its furniture.
PAGE_REACH: int = 3


def without_page_breaks(text: str) -> str:
    r"""The text with PDF page-break furniture removed and words rejoined that a
    line break hyphenated (2026-10-02).

    Removed: every :func:`is_page_mark` line (adjacent marks are one break); a
    :data:`PAGE_FURNITURE` line right next to a mark; and a line whose text
    recurs beside two or more breaks, within :data:`PAGE_REACH` lines (a running
    header such as "Vimpat (lacosamide) Tablets and Injection"). A bare number anywhere else is kept — it is a table cell. A
    line ending "word-" whose next line starts lower-case is joined to it,
    hyphen kept ("placebo-" + "controlled", "HER2-" + "negative"; also
    U+2010/U+2011), and so is a capitalised name broken at its hyphen whose next
    line opens on an all-capitals or digit token ("KEYNOTE-" + "B61 (NCT…"),
    unless that line is only a number. Joining runs after the furniture is removed, so a
    footer or page number is never joined to anything. A soft hyphen
    (U+00AD, an invisible hyphenation hint) joins without one and is removed
    everywhere ("semi\u00ad" + "synthetic"). Other line breaks stay: the
    subsection headings are found line by line.

    >>> mark = ("Reference ID: 4093616 24\nThis label may not be the latest approved by FDA.\n"
    ...         "For current labeling information, please visit https://www.fda.gov/drugsatfda\n"
    ...         "Vimpat (lacosamide) Tablets\nPage 7 of 18\n")
    >>> without_page_breaks("In 616 patients with placebo-\n" + mark + "controlled design.\n"
    ...                     "Table 2\n616\nThe trial ran\n" + mark + "for one year.")
    'In 616 patients with placebo-controlled design.\nTable 2\n616\nThe trial ran\nfor one year.'
    >>> page = "".join(f"prose line {n}\n" for n in "abcde")
    >>> without_page_breaks("in KEYNOTE-\n" + mark + "581 (NCT02811861), a trial.\n" + page
    ...                     + mark + "Table KEYNOTE-\n24\nend")
    'in KEYNOTE-581 (NCT02811861), a trial.\nprose line a\nprose line b\nprose line c\nprose line d\nprose line e\nTable KEYNOTE-\n24\nend'
    >>> without_page_breaks("in KEYNOTE-\nVimpat (lacosamide) Tablets\n581 (NCT1)")
    'in KEYNOTE-\nVimpat (lacosamide) Tablets\n581 (NCT1)'
    >>> without_page_breaks("a semi\u00ad\nsynthetic peni\u00adcillin")
    'a semisynthetic penicillin'
    """
    lines = text.split("\n")
    marks = [i for i, line in enumerate(lines) if is_page_mark(line)]
    # One page break leaves several marks a line or two apart: one cluster.
    cluster_of: dict[int, int] = {}
    for n, mark in enumerate(marks):
        cluster_of[mark] = (cluster_of[marks[n - 1]] if n and mark - marks[n - 1] <= PAGE_REACH
                            else mark)
    # Each line beside a break belongs to its nearest one.
    nearest: dict[int, tuple[int, int]] = {}
    for mark in marks:
        for i in range(max(0, mark - PAGE_REACH), min(len(lines), mark + PAGE_REACH + 1)):
            if i not in cluster_of and (i not in nearest or abs(i - mark) < nearest[i][1]):
                nearest[i] = (cluster_of[mark], abs(i - mark))
    breaks_beside: dict[str, set[int]] = {}
    for i, (cluster, _) in nearest.items():
        if lines[i].strip():
            breaks_beside.setdefault(lines[i].strip(), set()).add(cluster)
    drop = set(marks) | {
        i for i, (_, distance) in nearest.items()
        if (distance == 1 and PAGE_FURNITURE.match(lines[i]))
        or len(breaks_beside.get(lines[i].strip(), ())) >= 2
    }
    kept = [line for i, line in enumerate(lines) if i not in drop]
    joined: list[str] = []
    for line in kept:
        if joined and re.search(r"[A-Za-z]\u00ad\s*$", joined[-1]) and re.match(r"\s*[a-z]", line):
            # A soft hyphen is an invisible hyphenation hint: the word continues.
            joined[-1] = joined[-1].rstrip()[:-1] + line.lstrip()
        elif (joined and re.search(r"[A-Za-z0-9][-\u2010\u2011]$", joined[-1])
              and re.match(r"\s*[a-z]", line)):
            joined[-1] = joined[-1] + line.lstrip()
        elif (joined and re.search(r"\b[A-Z][A-Z0-9]+[-\u2010\u2011]$", joined[-1])
              and re.match(r"\s*[A-Z0-9]+\b", line) and not re.match(r"\s*\d+\s*$", line)):
            # A capitalised name broken at its hyphen: "KEYNOTE-" + "581 (NCT…".
            # Page furniture is already gone, so a footer never joins; nor does a
            # line that is only a number (a table cell or a missed page number) or
            # one opening on a mixed-case word (a running header seen only once).
            joined[-1] = joined[-1] + line.lstrip()
        else:
            joined.append(line)
    return "\n".join(joined).replace("\u00ad", "")


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


#: Words that may sit lower-case inside a title-cased heading.
_TITLE_SMALL: frozenset[str] = frozenset({
    "a", "an", "and", "or", "of", "the", "with", "in", "for", "to", "on", "at",
    "by", "after", "from", "without", "who", "not", "as", "into", "versus", "vs",
    "than", "due", "under", "over", "per", "via", "plus", "previously", "prior",
    "receiving", "treated", "including", "following", "either", "both", "least",
    "one", "two", "have", "has", "had", "are", "is", "whose", "that",
})

#: A continuation line may open with one of these even when the heading line
#: before it is short — "or Metastatic Breast Cancer".
_CONTINUING: frozenset[str] = frozenset({
    "or", "and", "with", "of", "in", "for", "to", "after", "who", "without",
    "including", "previously", "receiving", "following", "not", "due", "at",
    "on", "by", "from", "versus", "plus", "treated",
})

#: A heading line this long has probably wrapped.
_WRAP_LENGTH: int = 55


def _title_like(line: str) -> bool:
    """Whether a line reads as heading words rather than prose.

    >>> _title_like("or Metastatic Breast Cancer")
    True
    >>> _title_like("EMBRACA (NCT01945775) was an open label study in which")
    False
    """
    words = line.split()
    if not words or len(words) > 14:
        return False
    capitalised = 0
    for word in words:
        bare = word.strip("()[],:;-–—/'\"")
        if not bare:
            continue
        if bare[0].isupper() or bare[0].isdigit():
            capitalised += 1
        elif bare.lower() not in _TITLE_SMALL and not re.match(r"^[a-z]+[A-Z]", bare):
            return False
    return capitalised >= 1


def continued_heading(heading: str, block: str, max_lines: int = 2) -> str:
    """``heading`` with the lines the PDF wrapped it onto joined back (D34).

    ``block`` is the subsection as :func:`split_subsections` returns it, so its
    first line is the heading line. A following line is taken when it is short,
    reads as heading words (:func:`_title_like`), holds no registry id and is
    not a sentence — and either opens with a joining word or follows a line
    long enough to have wrapped.

    >>> block = ("14.1 Deleterious or Suspected Deleterious Germline BRCA-mutated "
    ...          "HER2-negative Locally Advanced\\nor Metastatic Breast Cancer\\n"
    ...          "EMBRACA (NCT01945775) was an open label study in which patients")
    >>> continued_heading("Deleterious or Suspected Deleterious Germline BRCA-mutated "
    ...                   "HER2-negative Locally Advanced", block)[-36:]
    'Advanced or Metastatic Breast Cancer'
    >>> continued_heading("HRR Gene-mutated mCRPC",
    ...                   "14.2 HRR Gene-mutated mCRPC\\nThe efficacy of TALZENNA was")
    'HRR Gene-mutated mCRPC'
    """
    if not heading:
        # An unheaded "14" block starts at "14 CLINICAL STUDIES"; there is no
        # subsection heading to continue.
        return ""
    full = heading
    for raw in block.split("\n")[1 : 1 + max_lines]:
        line = raw.strip()
        if (not line or len(line) > 90 or full.rstrip().endswith((".", ":", ";"))
                or "NCT" in line or re.search(r"\(N\s*=", line)
                or line.endswith(".") or re.match(r"^\d+(\.\d+)*\s", line)):
            break
        words = line.split()
        opens = words[0].lower() in _CONTINUING and (
            len(words) == 1 or _title_like(" ".join(words[1:]))
        )
        if not (opens or (len(full) >= _WRAP_LENGTH and _title_like(line))):
            break
        full = f"{full} {line}"
    return full


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


#: How far before an id its name may sit: "KEYNOTE-180 (NCT02559687)",
#: "GEMINI 2 (Study ID NCT03857542)".
NAME_BEFORE_ID: int = 25

#: The name written right before a registry id. Unlike :data:`ACRONYM`, which
#: must find trials in prose with no id beside them and so demands a number, a
#: name here needs none — the id fixes which trial it is, and many sponsors'
#: names have no digit: ``Study JMDB (NCT00087711)``, ``PARAMOUNT (NCT00789373)``,
#: ``E4599 (NCT00021060)``. A capitalised word of three or more characters, with
#: hyphenated parts in any case (``LUX-Lung 6``, ``TROPION-Breast01``,
#: ``DRIVE-AHEAD``) and an optional trial number that is not a dose.
BESIDE_NAME = re.compile(
    r"\b([A-Z][A-Z0-9]{2,}(?:-[A-Za-z0-9]+)*"
    r"(?:[ ]\d{1,4}[A-Z]?(?!\.\d)(?!\s*" + _UNIT + r"\b))?)\b"
)

#: Capitalised words that sit before an id but never name a trial: section
#: words, roman numerals and the disease and regimen abbreviations measured in
#: that position on the 2026-10-02 labels (``with XLH. Study 1 (NCT…``).
NOT_A_NAME: frozenset[str] = frozenset({
    "II", "III", "IV", "VI", "AND", "THE", "FOR", "WITH", "STUDY", "STUDIES", "TRIAL",
    "TRIALS", "CLINICAL", "TABLE", "FIGURE", "PHASE", "USA", "FDA", "OLE", "ITT",
    "NSCLC", "SCLC", "COPD", "HIV", "HCV", "HBV", "CKD", "DVT", "ICS", "LABA", "PNH",
    "OAB", "ISS", "NDO", "XLH", "CSII", "SLE", "ADHD", "GERD", "IBS", "MDD", "RBV",
    "PEG", "PSA", "CML", "CLL", "AML", "ALL", "MCL", "DLBCL", "RCC", "HCC", "CRC",
    "IHC", "VTE", "MSI", "PCR", "NGS", "LDL", "HDL", "BSA", "EASI", "PASI", "ACR", "MTX",
    "ECOG", "BMI", "HBA1C", "FEV1", "AUC", "ORR", "PFS", "DOR", "CR", "ESMO", "RECIST",
    "WHO", "NYHA", "BRCA", "HER2", "EGFR", "ALK", "TPS", "CPS", "ICU", "NA",
})


def _name_before(position: int, named: list[re.Match[str]], sentence: str) -> str:
    """The trial name ending at most :data:`NAME_BEFORE_ID` characters before an id.

    Nothing between the name and the id may end a sentence (``.``/``;``) or hold
    a number — ``VIREAD (60) 48 (NCT…`` is a table row, not a name — except
    inside an aside that only renames the study (:func:`gap_allows_name`).

    >>> s = "Figure 2: KEYNOTE-181 curves. In KEYNOTE-180 (NCT02559687), a trial"
    >>> named = list(ACRONYM.finditer(s))
    >>> _name_before(s.index("NCT"), named, s)
    'KEYNOTE-180'
    >>> _name_before(s.index("curves"), named, s)
    'KEYNOTE-181'
    >>> _name_before(0, named, s)
    ''
    >>> s = "VIREAD (60) 48 (NCT01651403)"
    >>> _name_before(s.index("NCT"), list(BESIDE_NAME.finditer(s)), s)
    ''
    """
    before = [m for m in named if m.end() <= position
              and gap_allows_name(sentence[m.end():position])]
    return before[-1].group(0) if before else ""


#: A parenthetical that only renames the study, skipped when measuring the gap:
#: "Study PCYC-1118E (referred to as Study 1118) (NCT01614821)".
_RENAMING_ASIDE = re.compile(
    r"\((?:referred\s+to\s+as|hereafter|hereinafter|also\s+(?:known|called|referred\s+to)\s+as)"
    r"[^()]*\)", re.IGNORECASE)


def gap_allows_name(gap: str) -> bool:
    """Whether the text between a name and an id keeps them paired: at most
    :data:`NAME_BEFORE_ID` characters, none a sentence end or a digit, once a
    renaming aside is set aside.

    >>> gap_allows_name(" (referred to as Study 1118) (")
    True
    >>> gap_allows_name(" (referred\\nto as Study 1118) (")
    True
    >>> gap_allows_name(" (60) 48 (")
    False
    """
    gap = _RENAMING_ASIDE.sub("", gap)
    return len(gap) <= NAME_BEFORE_ID and not re.search(r"[.;0-9]", gap)


def beside_names(sentence: str, own_names: frozenset[str]) -> list[re.Match[str]]:
    """Every :data:`BESIDE_NAME` in a sentence that could be a trial's name.

    >>> s = "pemetrexed was evaluated in Study JMDB (NCT00087711) with NSCLC (NCT1)"
    >>> [m.group(0) for m in beside_names(s, frozenset({"PEMETREXED"}))]
    ['JMDB']
    """
    out = []
    for match in BESIDE_NAME.finditer(sentence):
        head = re.split(r"[- ]", match.group(0), maxsplit=1)[0].upper()
        if head.startswith("NCT") or head in NOT_A_NAME or head in own_names:
            continue
        out.append(match)
    return out


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

    **An id takes only the name written right before it** (:func:`_name_before`,
    :data:`BESIDE_NAME` — no digit needed), or none. It never borrows another
    name from its sentence: a figure caption carries no full stop, so "Figure 1:
    … Survival in KEYNOTE-189 … evaluated in Study JMDB (NCT00087711)" read as
    one sentence and gave JMDB's id the name KEYNOTE-189 (2026-10-02). A wrong
    name is worse than none — mis-paired names are what made two different
    trials collide in :func:`service.candidates.collapse`. A sentence with no
    id keeps :data:`ACRONYM`, which demands a number, since those rows become
    search units and every capitalised abbreviation would become a "trial".

    The **enrollment count stays with the first id only**. A sentence naming two
    trials and one count does not say which it belongs to, and copying it to
    both would manufacture agreement between a label and a registry that P5
    reads as corroboration (D17).
    """
    out: list[TrialDescription] = []
    for number, heading, block in split_subsections(body):
        for sentence in _sentences(block):
            ncts = [normalise_nct(d) for d in NCT.findall(sentence)]
            named = [
                match for match in ACRONYM.finditer(sentence)
                if not match.group(0).startswith("NCT")
                and not is_own_drug_name(match.group(0), own_names)
            ]
            acronyms = [match.group(0) for match in named]
            # The name written right before an id is that id's name
            # ("KEYNOTE-180 (NCT02559687)", "Study JMDB (NCT00087711)"), and no
            # other — a figure caption about KEYNOTE-181 can precede it.
            near = beside_names(sentence, own_names)
            beside = [_name_before(match.start(), near, sentence)
                      for match in NCT.finditer(sentence)]
            counts = [
                match.group("a") or match.group("b")
                for match in ENROLLMENT.finditer(sentence)
            ]
            if not (ncts or acronyms or counts):
                continue
            design = ";".join(sorted({d.lower() for d in DESIGN.findall(sentence)}))
            for position, nct in enumerate(ncts or [""]):
                if nct:
                    acronym = beside[position] if position < len(beside) else ""
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
                        heading_full=continued_heading(heading, block),
                        subsection=" ".join(block.split())[:SUBSECTION_KEEP],
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
    body_14 = without_page_breaks(section(text, 14))
    body_1 = without_page_breaks(section(text, 1))
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


class SubmissionSection14(NamedTuple):
    """One submission's section 14, as its label reads (D46): S6 builds its role
    units from it rather than parsing the PDF a second time."""

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
    #: Each submission's section 14 text, line breaks kept (D46).
    sections: tuple[SubmissionSection14, ...] = ()

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
    "PAGE_FURNITURE",
    "FOOTER_CHARS",
    "is_page_mark",
    "PAGE_REACH",
    "SubmissionSection14",
    "without_page_breaks",
    "SUBSECTION_KEEP",
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
    "beside_names",
    "gap_allows_name",
    "describe_trials",
    "is_own_drug_name",
    "normalise_nct",
    "read_label",
    "render",
    "section",
    "split_subsections",
    "continued_heading",
]


def build(
    data: RawData,
    cache_root: Path,
    limit: int | None = None,
    parsers: int = 16,
    offline: bool = False,
) -> LabelsReport:
    """Fetch every trial-relevant submission's label and read it.

    One PDF can serve several submissions, so downloads are deduplicated by
    URL — 4,416 submissions cost 4,169 fetches.

    **Fetching is polite, not threaded** (:mod:`source.polite_fetch`): one
    request a second, a breaker after repeated refusals, the cache first.
    It used to run six concurrent downloads with no breaker — the pattern that
    had FDA refuse this address for four hours on the letter run. ``offline``
    reads the cache only and sends nothing. Parsing is CPU-bound and pooled.
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
    fetched = polite_fetch.fetch_all(urls, cache_root, offline=offline)
    cached: dict[str, Path] = dict(fetched.cached)
    failed = len(fetched.failed) + len(fetched.refused)
    _log.info("%d cached, %d failed, %d not fetched (offline, refused or breaker)",
              len(cached), len(fetched.failed), len(fetched.refused))

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
    sections: list[SubmissionSection14] = []
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
        sections.append(SubmissionSection14(document.application, document.submission_type,
                                            document.submission_no, reading.section_14))
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
        sections=tuple(sections),
    )
