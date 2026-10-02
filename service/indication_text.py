"""What of an indication text the coder may read (S5, 2026-10-02).

The coder (:func:`service.indications.code`) finds every disease a text names.
A label's section 1, today's openFDA label and an approval letter name diseases
that are **not** what the drug is for, and a hand check of 40 chosen codes found
the errors were of four kinds, each handled here before coding:

* **negation and limitation** (:func:`without_negations`) — "Limitations of
  Use" paragraphs and sentences such as "has not been studied in patients with
  a history of pancreatitis" or "no substantial evidence that it increases
  weight loss";
* **context** (:func:`indication_sentences`) — in long texts, sentences that
  carry no indication cue: "Lowering blood pressure reduces the risk of …
  strokes and myocardial infarctions";
* **HIV strains** (:func:`plain_hiv`) — "HIV-1 infection" matches no MeSH entry
  term, so 509 of 561 texts naming HIV coded a generic "Infections";
* **abbreviations in §14 headings** (:func:`expand_abbreviations`) — "Metastatic
  NSCLC" names no MeSH term; the label usually defines it ("non-small cell lung
  cancer (NSCLC)").

Only what the coder reads changes; the stored text stays as the source wrote it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: Bullet characters labels use, the private-use ```` of PDF extraction included.
BULLETS = "•\uf0b7▪◦●"

#: A "Limitations of Use" passage: from the phrase to the next numbered
#: subsection ("1.2 …"), the next bullet, or the end of the text.
LIMITATIONS = re.compile(
    r"\blimitations?\s+of\s+use\b.*?(?=\s\(?\d{1,2}\.\d{1,2}\s*\)?\s+[A-Z]|[" + BULLETS + r"]|$)",
    re.IGNORECASE | re.DOTALL,
)

#: The opening of a "Limitations of Use" passage.
LIMITATION_START = re.compile(r"\blimitations?\s+of\s+use\b", re.IGNORECASE)

#: A segment that states an indication outright, which ends a limitation passage.
STATES = re.compile(r"(?<!not\s)\bis\s+indicated\b|(?<!not\s)\bare\s+indicated\b",
                    re.IGNORECASE)

#: A segment opening a numbered subsection ("1.2 Lupus Nephritis"), which
#: also ends a limitation passage.
SUBSECTION = re.compile(r"^\s*\d{1,2}\.\d{1,2}\s+[A-Z]")

#: A finite verb: a list item has none ("Chancroid caused by Haemophilus
#: ducreyi"); prose does ("Lowering blood pressure reduces the risk …").
VERB = re.compile(
    r"\b(?:is|are|was|were|has|have|had|should|may|might|can|could|will|would|must"
    r"|reduces?|increases?|occurs?|consider|appears?|showed|demonstrated)\b",
    re.IGNORECASE,
)

#: A sentence that says the drug is **not** for something.
NEGATION = re.compile(
    r"\b(?:has|have|had)\s+not\s+been\b"
    r"|\bnot\s+(?:been\s+)?(?:studied|established|demonstrated|shown|evaluated|proven"
    r"|recommended|indicated|approved|intended)\b"
    r"|\bno\s+(?:substantial\s+|adequate\s+|sufficient\s+)?evidence\b"
    r"|\bis\s+not\s+(?:for|intended|indicated|effective)\b"
    r"|\bshould\s+not\s+be\s+used\b"
    r"|\bdo(?:es)?\s+not\s+(?:treat|prevent|cure)\b"
    r"|\bunknown\s+whether\b",
    re.IGNORECASE,
)

#: What makes a sentence of a long text an indication statement.
CUE = re.compile(
    r"\bindicat(?:ed|ion|ions)\b"
    r"|\b(?:treatment|prevention|prophylaxis|relief|management|control|diagnosis"
    r"|reduction|palliation|suppression|maintenance|induction)\s+of\b"
    r"|\b(?:for|in)\s+the\s+(?:treatment|prevention|prophylaxis|relief|management)\b"
    r"|\bto\s+(?:reduce|treat|prevent|improve|lower|relieve|induce|control|slow|delay"
    r"|decrease|increase|correct|restore|maintain)\b"
    r"|\brelieves?\b|\badjunct(?:ive)?\b|\bas\s+an\s+aid\b"
    r"|^\s*\d{1,2}\.\d{1,2}\s",
    re.IGNORECASE,
)

#: A segment this short, after an indication statement that opens a list with a
#: colon, is one of its items ("Psoriatic arthritis." under "As adjunctive
#: therapy … in:"). Without the colon a short sentence is not taken: "… indicated
#: for the treatment of hypertension. Lowering blood pressure reduces the risk
#: of … myocardial infarctions" opens no list.
LIST_ITEM_WORDS: int = 15

#: Where a text is split into segments: a full stop after a word (never a list
#: number: "for: 1. Rheumatoid Arthritis 2. …" stays one segment) before a capital or
#: bracket, any bullet, an "o" sub-bullet after a full stop, and a numbered
#: subsection ("1.2 Candidemia"). Semicolons are not split: indication lists
#: use them. A label's whole section 1 once read as one sentence because its
#: bullets were the private-use ````, and one negated clause removed it all.
SENTENCE = re.compile(
    r"(?<=[A-Za-z)\]]\.)\s+(?=[A-Z(\[])|\s+(?=[" + BULLETS + r"])|(?<=\.)\s+(?=o\s)"
    r"|\s+(?=\d{1,2}\.\d{1,2}\s+[A-Z])"
)

#: A segment that continues the list or statement before it.
CONTINUES = re.compile(r"^\s*(?:[" + BULLETS + r"]|o\s|such\b|these\b|this\s+includes\b)",
                       re.IGNORECASE)

#: Texts longer than this are read sentence by sentence (:func:`indication_sentences`);
#: a letter's one sentence is not.
LONG_TEXT: int = 400

#: "HIV-1", "HIV 2", "HIV type 1" — strain numbers MeSH's disease terms do not carry.
HIV_STRAIN = re.compile(r"\bHIV(?:[-\s]?(?:type\s*)?[12])\b", re.IGNORECASE)

#: A definition "long form (ABBR)" inside a text.
DEFINITION = re.compile(r"((?:[A-Za-z][\w'\-]*\s+){1,9}?[A-Za-z][\w'\-]*)\s*\(([A-Z][A-Za-z0-9]{1,9})\)")

#: Common abbreviations a heading may use without its label defining them.
KNOWN_ABBREVIATIONS: Mapping[str, str] = {
    "NSCLC": "non-small cell lung cancer",
    "SCLC": "small cell lung cancer",
    "AML": "acute myeloid leukemia",
    "CLL": "chronic lymphocytic leukemia",
    "MDS": "myelodysplastic syndromes",
    "RCC": "renal cell carcinoma",
    "BPH": "benign prostatic hyperplasia",
    "ED": "erectile dysfunction",
    "HCC": "hepatocellular carcinoma",
    "CRC": "colorectal cancer",
}


def without_negations(text: str) -> str:
    """The text without its Limitations of Use passages and negated sentences.

    >>> without_negations("indicated for SLE. Limitations of Use: The efficacy has not "
    ...                   "been evaluated in severe active CNS lupus. 1.2 Lupus Nephritis x")
    'indicated for SLE. 1.2 Lupus Nephritis x'
    >>> without_negations("RA: X is indicated for RA. Limitations of Use: Use with biologics is "
    ...                   "not recommended. Psoriatic Arthritis X is indicated for PsA.")
    'RA: X is indicated for RA. Psoriatic Arthritis X is indicated for PsA.'
    >>> without_negations("Indicated for diabetes. It has not been studied in patients "
    ...                   "with pancreatitis. Use with diet.")
    'Indicated for diabetes. Use with diet.'
    >>> without_negations("X is indicated for chronic hepatitis C who have: \u2022 genotype 1 "
    ...                   "infection. o Benefit was not shown in genotype 2 patients with fibrosis")
    'X is indicated for chronic hepatitis C who have: • genotype 1 infection.'
    """
    kept: list[str] = []
    limiting = False
    for segment in SENTENCE.split(text):
        start = LIMITATION_START.search(segment)
        if start is not None:
            # The passage runs to the next segment that states an indication
            # ("1.4 Chronic Spontaneous Urticaria XOLAIR is indicated …",
            # "Psoriatic Arthritis XELJANZ is indicated …").
            before, after = segment[:start.start()], segment[start.start():]
            if before.strip():
                kept.append(before)
            limiting = not STATES.search(after)
            continue
        if limiting:
            if not (STATES.search(segment) or SUBSECTION.match(segment)):
                continue
            limiting = False
        negated = NEGATION.search(segment)
        if negated is None:
            kept.append(segment)
        elif CUE.search(segment[:negated.start()]):
            # An indication stated before the negation stays; what the
            # negation qualifies (the text after it) goes.
            kept.append(segment[:negated.start()])
    return " ".join(" ".join(kept).split())


def indication_sentences(text: str) -> str:
    """A long text's sentences that carry an indication cue; a short one whole.

    >>> indication_sentences("Lotensin is indicated for the treatment of hypertension. "
    ...                      "Lowering blood pressure reduces the risk of strokes and "
    ...                      "myocardial infarctions. " + "x" * 400)
    'Lotensin is indicated for the treatment of hypertension.'
    >>> indication_sentences("for the reduction of fever")
    'for the reduction of fever'
    >>> indication_sentences("X is indicated for palliation in neonates with heart defects. "
    ...                      "Such defects include coarctation of the aorta. Store cold. "
    ...                      "\u2022 not a list item of anything" + "." * 400)
    'X is indicated for palliation in neonates with heart defects. Such defects include coarctation of the aorta.'
    >>> indication_sentences("As adjunctive therapy in: Psoriatic arthritis. Rheumatoid arthritis. "
    ...                      "Lowering blood pressure reduces the risk of fatal and nonfatal "
    ...                      "cardiovascular events, primarily strokes and myocardial infarctions. "
    ...                      "1.2 Newly Diagnosed CML Adults with chronic myeloid leukemia." + "." * 300)[:130]
    'As adjunctive therapy in: Psoriatic arthritis. Rheumatoid arthritis. 1.2 Newly Diagnosed CML Adults with chronic myeloid leukemia.'
    """
    if len(text) <= LONG_TEXT:
        return text
    kept: list[str] = []
    open_statement = open_list = False
    for segment in SENTENCE.split(text):
        if CUE.search(segment):
            kept.append(segment)
            open_statement, open_list = True, ":" in segment
        elif open_statement and (CONTINUES.match(segment) or (open_list and (
                len(segment.split()) <= LIST_ITEM_WORDS or not VERB.search(segment)))):
            kept.append(segment)  # a list item or "Such defects include …"
        else:
            open_statement = open_list = False
    return " ".join(kept)


def plain_hiv(text: str) -> str:
    """HIV strain numbers dropped, so "HIV-1 infection" reads "HIV infection".

    >>> plain_hiv("treatment of HIV-1 infection and HIV type 2 and HIV 1")
    'treatment of HIV infection and HIV and HIV'
    """
    return HIV_STRAIN.sub("HIV", text)


def abbreviations(texts: Iterable[str]) -> dict[str, str]:
    """Abbreviation → long form, from "long form (ABBR)" definitions whose
    words' initials spell the abbreviation (the shortest such tail).

    >>> abbreviations(["patients with metastatic non-small cell lung cancer (NSCLC) whose",
    ...                "advanced renal cell carcinoma (RCC)", "the trial (KEYNOTE)"])
    {'NSCLC': 'non-small cell lung cancer', 'RCC': 'renal cell carcinoma'}
    """
    found: dict[str, str] = {}
    for text in texts:
        for match in DEFINITION.finditer(text):
            words, abbr = match.group(1).split(), match.group(2)
            letters = abbr.lower()
            for start in range(len(words) - 1, -1, -1):
                tail = words[start:]
                initials = "".join(part[0] for w in tail for part in re.split(r"-", w) if part)
                if initials.lower() == letters:
                    found.setdefault(abbr, " ".join(tail))
                    break
    return found


#: The last word of a long form that names a disease; only such abbreviations
#: are written out ("anaplastic lymphoma kinase" for ALK would read as the
#: lymphoma, and a gene is not an indication).
DISEASE_HEAD = re.compile(
    r"(?:cancer|carcinoma|leuk(?:a)?emia|lymphoma|myeloma|melanoma|sarcoma|tumou?rs?|neoplasms?"
    r"|disease|diseases|syndromes?|disorders?|infections?|dysfunction|hyperplasia|failure"
    r"|arthritis|colitis|asthma|hypertension|fibrosis|deficiency|acidemia|aciduria|sclerosis"
    r"|psoriasis|dermatitis|hepatitis|anemia|obstruction|edema|pain|injury)$",
    re.IGNORECASE,
)


def expand_abbreviations(heading: str, defined: Mapping[str, str]) -> str:
    """A heading with its disease abbreviations written out — the label's own
    definitions first, then :data:`KNOWN_ABBREVIATIONS`; a long form already in
    the heading, or one that names no disease (:data:`DISEASE_HEAD`), is left.

    >>> expand_abbreviations("Previously Treated Metastatic NSCLC", {})
    'Previously Treated Metastatic non-small cell lung cancer'
    >>> expand_abbreviations("Pediatric ED", {"ED": "enteric disease"})
    'Pediatric enteric disease'
    >>> expand_abbreviations("ALK-Positive IMT", {"ALK": "anaplastic lymphoma kinase"})
    'ALK-Positive IMT'
    >>> expand_abbreviations("Muscle invasive bladder cancer (MIBC)",
    ...                      {"MIBC": "Muscle invasive bladder cancer"})
    'Muscle invasive bladder cancer (MIBC)'
    """
    lowered = heading.casefold()
    table = {abbr: long for abbr, long in {**KNOWN_ABBREVIATIONS, **defined}.items()
             if DISEASE_HEAD.search(long) and long.casefold() not in lowered}
    return re.sub(r"\b[A-Z][A-Za-z0-9]{1,9}\b",
                  lambda m: table.get(m.group(0), m.group(0)), heading)


def for_coding(text: str, prose: bool, label: bool = False) -> str:
    """What the coder reads of a text: HIV strains plain; for prose, negations
    and limitations out; for a label's long text (``label``: section 1, today's
    openFDA label) only its indication sentences. A letter *is* its indication
    statement and is not cue-filtered; a §14 heading (``prose`` false) is coded
    whole.

    >>> for_coding("indicated for HIV-1 infection. Limitations of Use: not for HBV.", True)
    'indicated for HIV infection.'
    """
    plain = plain_hiv(text)
    if not prose:
        return plain
    kept = without_negations(plain)
    return indication_sentences(kept) if label else kept


__all__ = ["BULLETS", "CONTINUES", "CUE", "DISEASE_HEAD", "KNOWN_ABBREVIATIONS", "LIMITATION_START",
           "LIST_ITEM_WORDS", "STATES", "SUBSECTION", "VERB", "LIMITATIONS", "LONG_TEXT", "NEGATION", "abbreviations",
           "expand_abbreviations", "for_coding", "indication_sentences", "plain_hiv",
           "without_negations"]
