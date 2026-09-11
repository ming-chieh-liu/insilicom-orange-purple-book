"""Canonicalise an FDA application number, or say what else it turned out to be.

The FDA writes application numbers four ways across its own files:

==========================================  ==================================
``NDA000004`` / ``BLA125514``               openFDA JSON — prefixed, normally
                                            zero-padded to 6, but **exactly
                                            two records are 5 digits**:
                                            ``NDA19019`` and ``NDA21939``.
``000004``                                  the Drugs@FDA raw tables, in all
                                            12 of them — unprefixed, padded to
                                            6. **Two rows carry a trailing
                                            space** (``'19019 '``,
                                            ``'21939 '``) and are the same two
                                            applications the JSON writes short.
``Appl_Type=N`` + ``Appl_No=020702``        the Orange Book text files — the
                                            type and the number are separate
                                            columns. ``A`` 37,795 · ``N``
                                            10,869; there is no third letter.
``761399``                                  the Purple Book — bare, type
                                            implied BLA.
==========================================  ==================================

So the canonical form is **the type, then six digits**: ``NDA019019``. It is one
string, it is self-describing, and it makes the trailing-space row and the
5-digit JSON record resolve to the same key — which is the whole reason for
having a canonical form at all.

**The field does not always hold an application.** Measured over all 137,804
``ndc`` records on 2026-09-11, case-insensitively:

=================  =======  ============================================
``absent``          38,074  the field is missing entirely (27.6%)
``application``     63,184  an NDA, BLA or ANDA (45.8%)
``otc_monograph``   32,393  ``M020``, ``M020.11``, ``m021``, bare ``M``
``section_505g``     3,499  ``505G(a)(3)`` — a statute, not an application
``monograph_part``     451  ``part341``, ``part333A``
``other_prefixed``      95  ``BN820915``, ``BA110057``, ``EUA000122``, ``C200263``
``unrecognized``        54  23 distinct source typos (see below)
``untyped``             54  bare digits: ``352``, ``343``, ``341``, ``032``
=================  =======  ============================================

which sums to 137,804 exactly. An ``^(NDA|BLA|ANDA)\\d{6}$`` assumption would
therefore mis-handle **54% of that file**, and reporting it as a parse failure
would libel a field the FDA simply uses for two different purposes.

**Bare digits are ``untyped``, not a guess.** The 54 bare values are mostly
recognisable 21 CFR OTC monograph parts (``352`` sunscreen, ``341``
cough/cold, ``343`` analgesics), so calling them ``monograph_part`` is
tempting. We do not: a bare number states no type, and inferring "monograph"
invents a type exactly as much as inferring "NDA" would. ``digits`` is still
populated, so a caller with outside knowledge can act; ``canonical`` is empty,
because no canonical key can be minted without a type.

**Typos are reported, never repaired.** The 54 unrecognized rows are 23
distinct strings — ``M20``, ``M505G(a)(3)``, ``505(a)(3)``, ``MO12`` (letter O
for zero), ``OKM020``, ``505G(a)(30``, ``505G(a)(3)(A)``. Each is a defect in
the source worth a human's attention, and at 0.04% of the file there are few
enough that warning on each produces a readable log. Guessing what they meant
would put invented application numbers into a join.

**All-zero digits are a sentinel, not application zero.**
``Join_Submission_ActionTypes_Lookup.txt`` has 3 rows of ``'00000 '``. Run
naively through strip → lstrip → zfill that becomes ``000000`` and then
``NDA000000``, an application that does not exist.

Error policy: a defect in the **data** is a value, named in the result and
counted by the caller. A defect in the **call** is an exception.

Decided 2026-09-10; measured 2026-09-11.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: The three application types Drugs@FDA states, by frequency in
#: ``Applications.txt`` (29,323 rows): ANDA 22,954 · NDA 5,887 · BLA 482.
CANONICAL_TYPES: tuple[str, ...] = ("ANDA", "NDA", "BLA")

#: The Orange Book's one-letter ``Appl_Type`` mapped to the word Drugs@FDA
#: uses. ``products.txt``: ``A`` 37,795 · ``N`` 10,869 over 48,664 rows.
#: There is deliberately no ``B`` — the Orange Book lists no biologics, so no
#: file states one and this table invents nothing.
TYPE_LETTERS: dict[str, str] = {"A": "ANDA", "N": "NDA"}

APPLICATION: Final = "application"
ABSENT: Final = "absent"
OTC_MONOGRAPH: Final = "otc_monograph"
SECTION_505G: Final = "section_505g"
MONOGRAPH_PART: Final = "monograph_part"
OTHER_PREFIXED: Final = "other_prefixed"
UNTYPED: Final = "untyped"
ZERO_SENTINEL: Final = "zero_sentinel"
UNRECOGNIZED: Final = "unrecognized"

#: Every value :attr:`ApplicationNumber.kind` can take, most frequent first.
KINDS: tuple[str, ...] = (
    APPLICATION,
    ABSENT,
    OTC_MONOGRAPH,
    SECTION_505G,
    MONOGRAPH_PART,
    OTHER_PREFIXED,
    UNTYPED,
    UNRECOGNIZED,
    ZERO_SENTINEL,
)

#: How many digits an application number has once padded.
_WIDTH: Final = 6

_APPLICATION = re.compile(r"(NDA|BLA|ANDA)(\d{1,6})", re.IGNORECASE)
_OTC_MONOGRAPH = re.compile(r"M\d{3}(?:\.\d+)?|M", re.IGNORECASE)
_MONOGRAPH_PART = re.compile(r"part\d{3}[A-Z]?", re.IGNORECASE)
_SECTION_505G = re.compile(r"505G\(a\)\(\d\)", re.IGNORECASE)
_OTHER_PREFIXED = re.compile(r"(?:BN|BA|EUA|C)\d{3,6}", re.IGNORECASE)
_BARE_DIGITS = re.compile(r"\d{1,6}")


class ApplicationNumber(NamedTuple):
    """What a source's application-number field turned out to be."""

    kind: str
    app_type: str
    digits: str

    @property
    def canonical(self) -> str:
        """``"NDA019019"``, or ``""`` when this is not a typed application.

        Derived rather than stored, so the one fact cannot be recorded twice
        and then disagree with itself. **An empty string is not a parse
        failure** — check :attr:`kind`: an untyped number and an OTC monograph
        both yield ``""`` and mean entirely different things.

        >>> normalize("NDA19019").canonical
        'NDA019019'
        >>> normalize("019019").canonical
        ''
        """
        if self.kind != APPLICATION:
            return ""
        return f"{self.app_type}{self.digits}"

    @property
    def is_application(self) -> bool:
        """Whether this names an NDA/BLA/ANDA that can be keyed on.

        >>> normalize("BLA125514").is_application
        True
        >>> normalize("M020").is_application
        False
        """
        return self.kind == APPLICATION


def pad(number: str | None) -> str:
    """Six zero-padded digits from however a source wrote them.

    ``strip`` → ``lstrip("0")`` → ``zfill(6)``, which is the one normalisation
    every source needs and the step that silently differs between call sites.
    An all-zero input pads to ``"000000"``; callers detect that separately
    rather than minting an application from it.

    >>> pad("19019 ")
    '019019'
    >>> pad("000004")
    '000004'
    >>> pad("4")
    '000004'
    >>> pad(None)
    ''
    """
    if number is None:
        return ""
    text = number.strip()
    if not text:
        return ""
    return text.lstrip("0").zfill(_WIDTH)


def _typed(app_type: str, digits: str) -> ApplicationNumber:
    """An application, unless its digits are the all-zero sentinel."""
    padded = pad(digits)
    if not padded.strip("0"):
        return ApplicationNumber(ZERO_SENTINEL, "", "")
    return ApplicationNumber(APPLICATION, app_type, padded)


def normalize(raw: str | None) -> ApplicationNumber:
    """Classify a self-describing application-number string.

    For the sources that write the type into the value — the openFDA
    ``drugsfda``, ``ndc`` and ``label`` JSON. Two-column sources use
    :func:`from_parts`.

    >>> normalize("NDA019019")
    ApplicationNumber(kind='application', app_type='NDA', digits='019019')
    >>> normalize("anda077089").canonical
    'ANDA077089'
    >>> normalize("M020").kind
    'otc_monograph'
    >>> normalize("505G(a)(3)").kind
    'section_505g'
    >>> normalize(None).kind
    'absent'
    """
    if raw is None:
        return ApplicationNumber(ABSENT, "", "")
    text = raw.strip()
    if not text:
        return ApplicationNumber(ABSENT, "", "")

    match = _APPLICATION.fullmatch(text)
    if match:
        return _typed(match.group(1).upper(), match.group(2))

    if _OTC_MONOGRAPH.fullmatch(text):
        return ApplicationNumber(OTC_MONOGRAPH, "", "")
    if _MONOGRAPH_PART.fullmatch(text):
        return ApplicationNumber(MONOGRAPH_PART, "", "")
    if _SECTION_505G.fullmatch(text):
        return ApplicationNumber(SECTION_505G, "", "")
    if _OTHER_PREFIXED.fullmatch(text):
        return ApplicationNumber(OTHER_PREFIXED, "", "")

    if _BARE_DIGITS.fullmatch(text):
        padded = pad(text)
        if not padded.strip("0"):
            return ApplicationNumber(ZERO_SENTINEL, "", "")
        # A bare number states no type, and we do not invent one.
        return ApplicationNumber(UNTYPED, "", padded)

    _log.warning("not a recognised application number, skipped: %r", raw)
    return ApplicationNumber(UNRECOGNIZED, "", "")


def from_parts(app_type: str | None, number: str | None) -> ApplicationNumber:
    """Classify a type and a number held in separate columns.

    For the Orange Book (``Appl_Type`` + ``Appl_No``), the Drugs@FDA raw
    tables (``ApplType`` + ``ApplNo``), and the Purple Book — where the caller
    supplies ``"BLA"``, since the file states no type. That is safe rather than
    a guess: of the Purple Book's 860 BLA numbers, Drugs@FDA types 460 as BLA,
    has no row for 400 (CBER-only), and types **none** as anything else.

    >>> from_parts("N", "019019").canonical
    'NDA019019'
    >>> from_parts("A", "077089").canonical
    'ANDA077089'
    >>> from_parts("NDA", "19019 ").canonical
    'NDA019019'
    >>> from_parts("BLA", "101062").canonical
    'BLA101062'
    >>> from_parts(None, "040850").kind
    'untyped'
    """
    padded = pad(number)
    if not padded:
        return ApplicationNumber(ABSENT, "", "")
    if not padded.strip("0"):
        return ApplicationNumber(ZERO_SENTINEL, "", "")

    if app_type is None or not app_type.strip():
        # The 8 Products.txt rows whose ApplNo has no Applications.txt row land
        # here: a real number, no type available, so no canonical key.
        return ApplicationNumber(UNTYPED, "", padded)

    token = app_type.strip().upper()
    if token in TYPE_LETTERS:
        return _typed(TYPE_LETTERS[token], padded)
    if token in CANONICAL_TYPES:
        return _typed(token, padded)

    _log.warning("not a recognised application type, skipped: %r", app_type)
    return ApplicationNumber(UNRECOGNIZED, "", "")


def is_application(raw: str | None) -> bool:
    """Whether a self-describing string names an NDA, BLA or ANDA.

    >>> is_application("BLA125514")
    True
    >>> is_application("505G(a)(3)")
    False
    """
    return normalize(raw).is_application


__all__ = [
    "ABSENT",
    "APPLICATION",
    "ApplicationNumber",
    "CANONICAL_TYPES",
    "KINDS",
    "MONOGRAPH_PART",
    "OTC_MONOGRAPH",
    "OTHER_PREFIXED",
    "SECTION_505G",
    "TYPE_LETTERS",
    "UNRECOGNIZED",
    "UNTYPED",
    "ZERO_SENTINEL",
    "from_parts",
    "is_application",
    "normalize",
    "pad",
]
