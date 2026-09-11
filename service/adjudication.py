"""Judge *why* a trial appears in section 14, not merely that it does.

``links.tsv`` is built by a regex that proves an NCT id was printed in a label's
section 14. It cannot say why it was printed. Section 14 carries pivotal trials,
supporting trials, safety and pharmacokinetic references, and trials that
**failed** — the fenofibrate label cites ACCORD Lipid (NCT00000620) beneath a
heading reading *"14.4 Lack of Efficacy in Cardiovascular Outcomes Trials"*.

A lexical-cue classifier was tried first and abandoned: inspected by hand, its
top hits were pivotal trials misread because a negative phrase about some other
endpoint fell inside the window. So the judgement is delegated to agents reading
the text, two per unit, under `docs/prompts/section14-polarity.md`. This module
is the deterministic half — it prepares what they read and applies what they
decide, and contains no model calls itself.

**A judgement unit is one (trial, application, segment).** Section 14 is one flat
string, but 55% of labels number their subsections (``14.1``, ``14.2`` …), and
the heading governs the trials beneath it. Only the segments that actually
mention the target trial are sent: if ``14.2`` never names it, judging ``14.2``
would spend tokens to learn nothing. A trial named in several segments produces
several units, and **any one SUPPORTING makes the pair supporting** — a label may
describe one trial under both an efficacy heading and a safety heading.

Measured 2026-09-11: 2,678 links become **2,776 units** — 2,228 from the 2,118
links in labels with subsections, and 548 whole-section units from labels
without. Median segment 5,519 characters against a median whole section of
19,195.

**Verdicts are curated data, not output.** They cost real agent calls and are not
reproducible, so they live in ``adjudication/verdicts.jsonl``, tracked in git and
outside the ``output/`` tree the pipeline truncates on every run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from source.openfda_json import openfda_of, read_shards
from utility import application_number as appno
from utility.logging import get_logger

_log = get_logger(__name__)

#: The two answers a judge may give under prompt v2. Anything else is refused
#: rather than coerced: a verdict we cannot read is a verdict we do not have.
SUPPORTING: str = "SUPPORTING"
NOT_SUPPORTING: str = "NOT_SUPPORTING"

#: Prompt v1 asked for four answers. Both extra values mean "not supporting",
#: so v1 verdicts stay readable rather than being thrown away or re-bought.
NEGATIVE: str = "NEGATIVE"
UNCLEAR: str = "UNCLEAR"
LEGACY_ANSWERS: tuple[str, ...] = (NEGATIVE, UNCLEAR)

#: Everything a stored verdict may contain, current and legacy.
ANSWERS: tuple[str, ...] = (SUPPORTING, NOT_SUPPORTING, NEGATIVE, UNCLEAR)

#: What a judge may answer today.
CURRENT_ANSWERS: tuple[str, ...] = (SUPPORTING, NOT_SUPPORTING)

#: What a link's ``polarity`` column can say. ``unjudged`` is not a refutation —
#: it means no agent has looked yet, and such links are kept, not dropped.
SUPPORTING_POLARITY: str = "supporting"
NOT_SUPPORTING_POLARITY: str = "not_supporting"
UNJUDGED: str = "unjudged"

#: Maps a judge's answer onto the column a consumer reads.
_POLARITY_OF: dict[str, str] = {
    SUPPORTING: SUPPORTING_POLARITY,
    NOT_SUPPORTING: NOT_SUPPORTING_POLARITY,
    # v1 values. Both mean the trial was not presented as supporting.
    NEGATIVE: NOT_SUPPORTING_POLARITY,
    UNCLEAR: NOT_SUPPORTING_POLARITY,
}

#: Which prompt wording produced a verdict. Bump when the prompt changes, or
#: verdicts from two different questions become indistinguishable.
PROMPT_VERSION: int = 2

NCT_PATTERN = re.compile(r"NCT\d{8}")

#: A section-14 subsection heading: ``14.1 Something``. The lookbehind stops it
#: matching inside a number like ``2.14.1``, and the lookahead requires a
#: capitalised word so a bare cross-reference such as "see 14.2" is not a
#: heading.
SUBSECTION = re.compile(r"(?<![\d.])14\.\d{1,2}\s+(?=[A-Z])")

#: The calibration cases from the prompt doc. A pilot run puts these first so it
#: always exercises one known negative and two known positives.
CALIBRATION: tuple[tuple[str, str], ...] = (
    ("NCT00000620", "NDA021612"),
    ("NCT00545688", "BLA761170"),
    ("NCT01866319", "BLA125514"),
)


class JudgementUnit(NamedTuple):
    """One thing to judge: a trial, an application, and the text to judge from."""

    unit_id: str
    nct_id: str
    application: str
    segment: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "nct_id": self.nct_id,
            "application": self.application,
            "segment": self.segment,
            "text": self.text,
        }


class Verdict(NamedTuple):
    """What the judges decided about one unit."""

    unit_id: str
    nct_id: str
    application: str
    judge_a: str
    judge_b: str
    tiebreak: str | None
    final: str

    @property
    def needed_tiebreak(self) -> bool:
        return self.tiebreak is not None


def _segments(text: str) -> list[str]:
    """Section 14 split at its numbered subsection headings.

    The text before the first heading is kept as its own segment: 12 links have
    their trial named in that preamble, and dropping it would lose them.
    """
    starts = [m.start() for m in SUBSECTION.finditer(text)]
    if not starts:
        return [text]
    bounds = [0] + starts
    return [text[lo:hi] for lo, hi in zip(bounds, starts + [len(text)]) if text[lo:hi].strip()]


def unit_id_for(nct_id: str, application: str, segment: int) -> str:
    """The stable key a verdict is filed under."""
    return f"{nct_id}|{application}|{segment}"


def extract_units(
    data: RawData, wanted: Iterable[tuple[str, str]]
) -> tuple[JudgementUnit, ...]:
    """Build one unit per (trial, application, segment that names the trial).

    ``wanted`` is the set of ``(nct_id, application)`` pairs in ``links.tsv`` —
    passed in rather than re-derived, so this judges exactly what was emitted.
    """
    targets = set(wanted)
    by_application: dict[str, set[str]] = {}
    for nct_id, application in targets:
        by_application.setdefault(application, set()).add(nct_id)

    units: dict[str, JudgementUnit] = {}
    for record in read_shards(data.label_shards):
        sections = record.get("clinical_studies") or []
        if not sections:
            continue
        # openfda.application_number is an ARRAY; 664 labels name more than one.
        # Every named application is judged, or a label covering two
        # formulations would have only one of them adjudicated.
        applications = [
            parsed.canonical
            for parsed in (
                appno.normalize(value)
                for value in (openfda_of(record).get("application_number") or [])
            )
            if parsed.is_application and parsed.canonical in by_application
        ]
        if not applications:
            continue
        text = " ".join(str(s) for s in sections)
        # 522 labels repeat their whole section in a second array element, so
        # joining then splitting yields the same subsection twice. Judging one
        # text twice buys nothing, so the first occurrence wins.
        seen_text: dict[tuple[str, str, str], str] = {}
        segments = _segments(text)
        for application in applications:
            for index, segment in enumerate(segments):
                body = segment.strip()
                for nct_id in NCT_PATTERN.findall(segment):
                    if nct_id not in by_application[application]:
                        continue
                    if seen_text.get((application, nct_id, body)) is not None:
                        continue
                    seen_text[(application, nct_id, body)] = ""
                    key = unit_id_for(nct_id, application, index)
                    units.setdefault(
                        key, JudgementUnit(key, nct_id, application, index, body)
                    )
    return tuple(sorted(units.values()))


def prioritise(units: Iterable[JudgementUnit], limit: int | None) -> tuple[JudgementUnit, ...]:
    """The first ``limit`` units, calibration cases first.

    A pilot that never sees a known negative proves nothing, so the three cases
    named in the prompt doc are pulled to the front.
    """
    ordered = sorted(
        units,
        key=lambda u: (
            (u.nct_id, u.application) not in CALIBRATION,
            u.unit_id,
        ),
    )
    return tuple(ordered if limit is None else ordered[:limit])


def write_units(units: Iterable[JudgementUnit], path: Path) -> int:
    """Write the work file the runbook tells the main agent to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps(unit.as_dict(), ensure_ascii=False) + "\n")
            count += 1
    _log.info("wrote %s (%d units)", path, count)
    return count


def read_verdicts(path: Path) -> dict[str, Verdict]:
    """Every recorded verdict, last row for a unit winning.

    Append-only: a re-judgement corrects an earlier one without rewriting
    history, so the file is also its own audit trail.
    """
    out: dict[str, Verdict] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                _log.warning("%s: line %d is not JSON, skipped", path, number)
                continue
            unit_id = str(row.get("unit_id", ""))
            final = str(row.get("final", ""))
            if not unit_id or final not in ANSWERS:
                _log.warning("%s: line %d has no usable verdict, skipped", path, number)
                continue
            nct_id, application, _, _ = _split_unit_id(unit_id)
            out[unit_id] = Verdict(
                unit_id=unit_id,
                nct_id=nct_id,
                application=application,
                judge_a=str(row.get("judge_a", "")),
                judge_b=str(row.get("judge_b", "")),
                tiebreak=row.get("tiebreak") or None,
                final=final,
            )
    return out


def _split_unit_id(unit_id: str) -> tuple[str, str, int, bool]:
    """``nct|application|segment`` back into its parts."""
    parts = unit_id.split("|")
    if len(parts) != 3 or not parts[2].isdigit():
        return "", "", -1, False
    return parts[0], parts[1], int(parts[2]), True


def record_verdicts(rows: Iterable[dict[str, object]], path: Path) -> tuple[int, int]:
    """Append judged rows, refusing anything malformed. Returns (written, refused).

    Validation is strict on purpose: an answer we cannot read is an answer we do
    not have, and writing a guess would put a fabricated judgement into a file
    that is treated as ground truth from then on.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = refused = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            unit_id = str(row.get("unit_id", ""))
            nct_id, application, _, ok = _split_unit_id(unit_id)
            judge_a = str(row.get("judge_a", "")).strip().upper()
            judge_b = str(row.get("judge_b", "")).strip().upper()
            raw_tie = row.get("tiebreak")
            tiebreak = str(raw_tie).strip().upper() if raw_tie else None
            if not ok:
                _log.warning("refused: unit_id %r is not nct|application|segment", unit_id)
                refused += 1
                continue
            if judge_a not in ANSWERS or judge_b not in ANSWERS:
                _log.warning("refused %s: judge answers %r/%r", unit_id, judge_a, judge_b)
                refused += 1
                continue
            if tiebreak is not None and tiebreak not in ANSWERS:
                _log.warning("refused %s: tiebreak %r", unit_id, tiebreak)
                refused += 1
                continue
            if judge_a == judge_b:
                if tiebreak is not None:
                    _log.warning("refused %s: judges agreed but a tiebreak was given", unit_id)
                    refused += 1
                    continue
                final = judge_a
            else:
                if tiebreak is None:
                    _log.warning("refused %s: judges disagreed with no tiebreak", unit_id)
                    refused += 1
                    continue
                final = tiebreak
            handle.write(
                json.dumps(
                    {
                        "unit_id": unit_id,
                        "nct_id": nct_id,
                        "application": application,
                        "judge_a": judge_a,
                        "judge_b": judge_b,
                        "tiebreak": tiebreak,
                        "final": final,
                        "prompt_version": PROMPT_VERSION,
                        "decided": date.today().isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1
    _log.info("recorded %d verdicts to %s (%d refused)", written, path, refused)
    return written, refused


def polarity_of(
    nct_id: str, application: str, verdicts: dict[str, Verdict]
) -> str:
    """One pair's polarity, taking the best answer across its segments.

    **Any segment judged SUPPORTING makes the pair supporting.** A label may
    describe one trial twice — under an efficacy heading and again under safety —
    and the efficacy mention is the one that answers our question. Failing that,
    an explicit NEGATIVE outranks NOT_SUPPORTING, because "this trial failed" is
    a stronger statement than "this text does not say".
    """
    finals = [
        v.final
        for v in verdicts.values()
        if v.nct_id == nct_id and v.application == application
    ]
    if not finals:
        return UNJUDGED
    for answer in (SUPPORTING, NOT_SUPPORTING, NEGATIVE, UNCLEAR):
        if answer in finals:
            return _POLARITY_OF[answer]
    # Unreachable: read_verdicts refuses any final outside ANSWERS, and the
    # loop above covers all four. Present so mypy sees a return on every path.
    return UNJUDGED  # pragma: no cover


def summarise(verdicts: dict[str, Verdict]) -> tuple[tuple[str, int], ...]:
    """Counts by final answer, plus how many needed a third judge."""
    counts: dict[str, int] = {answer: 0 for answer in ANSWERS}
    tiebreaks = 0
    for verdict in verdicts.values():
        counts[verdict.final] = counts.get(verdict.final, 0) + 1
        if verdict.needed_tiebreak:
            tiebreaks += 1
    rows = [(answer, counts[answer]) for answer in ANSWERS if counts[answer]]
    rows.append(("needed a tiebreak", tiebreaks))
    return tuple(rows)


#: A judge's answer line: ``unit_id = ANSWER``.
ANSWER_LINE = re.compile(r"^\s*(\S+)\s*=\s*([A-Z_]+)\s*$")


def read_judge_file(path: Path) -> dict[str, str]:
    """One judge's answers, keyed by unit id.

    Lines that do not match ``unit_id = ANSWER`` are skipped with a warning
    rather than guessed at — a judge that wrapped its output in prose must be
    noticed, not silently half-read.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        _log.warning("%s does not exist", path)
        return out
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        match = ANSWER_LINE.match(line)
        if not match:
            _log.warning("%s: line %d is not 'unit_id = ANSWER', skipped", path, number)
            continue
        unit_id, answer = match.group(1), match.group(2).upper()
        if answer not in ANSWERS:
            _log.warning("%s: line %d has answer %r, skipped", path, number, answer)
            continue
        out[unit_id] = answer
    return out


def combine(
    judge_a: dict[str, str], judge_b: dict[str, str], tiebreaks: dict[str, str]
) -> tuple[list[dict[str, object]], list[str], list[str]]:
    """Pair two judges' answers. Returns (agreed rows, needing a tiebreak, missing).

    A unit only becomes a verdict when both judges answered it. One judge's
    answer alone is a sample, not a measurement, so it waits rather than being
    recorded.
    """
    rows: list[dict[str, object]] = []
    needs_tiebreak: list[str] = []
    missing: list[str] = []
    for unit_id in sorted(set(judge_a) | set(judge_b)):
        a, b = judge_a.get(unit_id), judge_b.get(unit_id)
        if a is None or b is None:
            missing.append(unit_id)
            continue
        if a == b:
            rows.append({"unit_id": unit_id, "judge_a": a, "judge_b": b, "tiebreak": None})
            continue
        third = tiebreaks.get(unit_id)
        if third is None:
            needs_tiebreak.append(unit_id)
            continue
        rows.append({"unit_id": unit_id, "judge_a": a, "judge_b": b, "tiebreak": third})
    return rows, needs_tiebreak, missing


def iter_json_lines(path: Path) -> Iterator[dict[str, object]]:
    """Read a JSONL file the main agent wrote. A bad line warns, never aborts."""
    if not path.is_file():
        _log.warning("%s does not exist", path)
        return
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                _log.warning("%s: line %d is not JSON, skipped", path, number)
                continue
            if isinstance(row, dict):
                yield row


__all__ = [
    "ANSWERS",
    "CALIBRATION",
    "CURRENT_ANSWERS",
    "LEGACY_ANSWERS",
    "NEGATIVE",
    "NOT_SUPPORTING",
    "NOT_SUPPORTING_POLARITY",
    "PROMPT_VERSION",
    "SUPPORTING",
    "SUPPORTING_POLARITY",
    "UNCLEAR",
    "UNJUDGED",
    "JudgementUnit",
    "Verdict",
    "extract_units",
    "combine",
    "iter_json_lines",
    "read_judge_file",
    "polarity_of",
    "prioritise",
    "read_verdicts",
    "record_verdicts",
    "summarise",
    "unit_id_for",
    "write_units",
]
