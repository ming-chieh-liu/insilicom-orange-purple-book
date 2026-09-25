"""Agent-located letter indications: locate → extract → verify (D25, D30).

For letters the keyword rule (:mod:`service.letters`) could not use — no
recognisable sentence, or a sentence it judged not indication-bearing — two
judges name the numbered segments that state the indication, **code** copies
those segments verbatim, and two further judges verify the copied span. An
agent never writes the indication, so it cannot invent one: a segment id that
is not in the letter is refused. Protocol: ``docs/runbooks/letter-indication.md``;
prompts ``docs/prompts/letter-indication-locate.md`` and
``…-verify.md``.

This module does the deterministic parts — segmenting, unit and batch files,
pairing answers, extraction, recording — so the orchestrator never reads a
letter.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from service.judging import (
    BATCH_SIZE,
    batched_ids,
    parse_answers,
    read_answer_files,
    read_jsonl,
    recorded,
    write_batches,
    write_jsonl,
)
from utility.logging import get_logger

_log = get_logger(__name__)

LOCATE_PROMPT_VERSION: int = 1
VERIFY_PROMPT_VERSION: int = 1


#: Where a letter is split: after a sentence end or semicolon before a capital,
#: bullet, bracket or digit, and before a bullet.
SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z•(\d])|\s+(?=•)")

#: Characters kept per segment; a runaway segment is a parse failure, not prose.
SEGMENT_MAX: int = 700

NONE = "NONE"
STATES = "STATES_INDICATION"
NOT_INDICATION = "NOT_INDICATION"
VERIFY_OPTIONS: frozenset[str] = frozenset({STATES, NOT_INDICATION})

#: Final outcomes recorded per letter.
ACCEPTED = "accepted"
REJECTED = "rejected"
NONE_LOCATED = "none_located"


class LocateOutcome(NamedTuple):
    """Where one letter stands after pairing the locator answers."""

    unit_id: str
    located: str  # "S4,S5", "NONE", or "" while undecided
    needs_tiebreak: bool
    invalid: bool


def segment(text: str) -> list[str]:
    """The letter as numbered-segment material, one entry per segment.

    >>> segment("This sNDA provides for X. APPROVAL & LABELING We have completed "
    ...         "our review; it is approved. \\u2022 One \\u2022 Two")
    ['This sNDA provides for X.', 'APPROVAL & LABELING We have completed our review; it is approved.', '\\u2022 One', '\\u2022 Two']
    """
    flat = " ".join(text.split())
    return [part.strip()[:SEGMENT_MAX] for part in SPLIT.split(flat) if part.strip()]


def numbered(segments: list[str]) -> str:
    """``[S1] …`` lines, the form the locate prompt shows.

    >>> numbered(["a", "b"])
    '[S1] a\\n[S2] b'
    """
    return "\n".join(f"[S{i}] {s}" for i, s in enumerate(segments, 1))


def segments_of(numbered_text: str) -> dict[str, str]:
    """``{"S1": "…"}`` back from the numbered form.

    >>> segments_of("[S1] a\\n[S2] b c")
    {'S1': 'a', 'S2': 'b c'}
    """
    return dict(re.findall(r"^\[(S\d+)\] (.*)$", numbered_text, re.M))


def needs_agent(status: str, indication_bearing: str) -> bool:
    """Whether a letter goes to the agents: the keyword rule found nothing usable.

    >>> needs_agent("no_sentence", "false"), needs_agent("ok", "false")
    (True, True)
    >>> needs_agent("ok", "true"), needs_agent("scanned", "false"), needs_agent("not_fetched", "false")
    (False, False, False)
    """
    return status == "no_sentence" or (status == "ok" and indication_bearing != "true")


def normalise_located(answer: str) -> str:
    """A locate answer in canonical form: ``NONE`` or sorted unique ``S`` ids.

    >>> normalise_located(" s5, S4 ,S4"), normalise_located("none"), normalise_located("maybe")
    ('S4,S5', 'NONE', '')
    """
    if answer.strip().upper() == NONE:
        return NONE
    ids = [part.strip().upper() for part in answer.split(",") if part.strip()]
    if not ids or not all(re.fullmatch(r"S\d+", i) for i in ids):
        return ""
    return ",".join(sorted(set(ids), key=lambda i: int(i[1:])))


def pair_locate(
    unit_id: str, a: str, b: str, c: str, valid: set[str]
) -> LocateOutcome:
    """Combine two locators, and a tiebreak if present.

    Equal answers decide; different answers need a third judge, whose answer is
    final. An answer naming a segment the letter does not have is invalid and
    leaves the unit undecided for re-run.

    >>> v = {"S1", "S2", "S3"}
    >>> pair_locate("u", "S2", "S2", "", v)
    LocateOutcome(unit_id='u', located='S2', needs_tiebreak=False, invalid=False)
    >>> pair_locate("u", "S2", "NONE", "", v).needs_tiebreak
    True
    >>> pair_locate("u", "S2", "NONE", "S2", v).located
    'S2'
    >>> pair_locate("u", "S9", "S9", "", v).invalid
    True
    >>> pair_locate("u", "S2", "", "", v).located
    ''
    """
    answers = [normalise_located(x) if x else "" for x in (a, b, c)]

    def ok(answer: str) -> bool:
        return answer == NONE or all(i in valid for i in answer.split(","))

    if any(x and not ok(x) for x in answers):
        return LocateOutcome(unit_id, "", False, True)
    first, second, third = answers
    if not (first and second):
        return LocateOutcome(unit_id, "", False, False)
    if first == second:
        return LocateOutcome(unit_id, first, False, False)
    if third:
        return LocateOutcome(unit_id, third, False, False)
    return LocateOutcome(unit_id, "", True, False)


def pair_verify(a: str, b: str, c: str) -> tuple[str, bool]:
    """``(final, needs_tiebreak)`` for the verifiers; ``""`` while undecided.

    >>> pair_verify("STATES_INDICATION", "STATES_INDICATION", "")
    ('STATES_INDICATION', False)
    >>> pair_verify("STATES_INDICATION", "NOT_INDICATION", "")
    ('', True)
    >>> pair_verify("STATES_INDICATION", "NOT_INDICATION", "NOT_INDICATION")
    ('NOT_INDICATION', False)
    >>> pair_verify("maybe", "STATES_INDICATION", "")
    ('', False)
    """
    first, second, third = (x if x in VERIFY_OPTIONS else "" for x in (a, b, c))
    if not (first and second):
        return "", False
    if first == second:
        return first, False
    if third:
        return third, False
    return "", True


def extract(numbered_text: str, located: str) -> str:
    """The located segments, copied verbatim — the step no agent performs.

    >>> extract("[S1] a\\n[S2] b\\n[S3] c", "S1,S3")
    'a c'
    """
    segments = segments_of(numbered_text)
    return " ".join(segments[i] for i in located.split(","))


def accepted_spans(verdicts: Path) -> dict[str, str]:
    """``application|type|number`` → accepted span text, for the indication step."""
    return {
        unit: str(row.get("text") or "")
        for unit, row in recorded(verdicts).items()
        if row.get("final") == ACCEPTED and row.get("text")
    }


class PrepareReport(NamedTuple):
    """What ``letter-locate-prepare`` wrote."""

    candidates: int
    units: int
    unreadable: int
    new_batches: tuple[Path, ...]


class CombineReport(NamedTuple):
    """Where the task stands after ``letter-indication-combine``."""

    locate_answered: int
    locate_agreed: int
    locate_tiebreaks: tuple[Path, ...]
    invalid: tuple[str, ...]
    none_located: int
    spans: int
    verify_batches: tuple[Path, ...]
    verify_tiebreaks: tuple[Path, ...]
    accepted: int
    rejected: int
    newly_recorded: int


def prepare(
    letter_rows: Iterable[dict[str, str]],
    text_of: Callable[[str], str | None],
    brands: dict[str, set[str]],
    units_path: Path,
    batch_dir: Path,
    limit: int | None = None,
) -> PrepareReport:
    """Write a locate unit for every letter the rule missed; batch the new ones.

    Every candidate letter is written to the units file (the file is rebuilt,
    so extraction can always find a unit's segments); only letters not yet in a
    batch file get new batches, so a re-run never judges a letter twice.
    """
    units: list[dict[str, object]] = []
    candidates = unreadable = 0
    for row in letter_rows:
        if not needs_agent(row.get("status", ""), row.get("indication_bearing", "")):
            continue
        candidates += 1
        text = text_of(row.get("letter_url", ""))
        if not text:
            unreadable += 1
            continue
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        names = " / ".join(sorted(brands.get(row["application"][-6:], set())))
        units.append({
            "unit_id": key,
            "approval": f"{names} · {row['application']} {row['submission_type']} "
                        f"{row['submission_no']} · approved {row.get('approval_date', '')}",
            "segments": numbered(segment(text)),
        })
    write_jsonl(units_path, units)
    done = batched_ids(batch_dir, "locate")
    pending = [str(u["unit_id"]) for u in units if u["unit_id"] not in done]
    if limit is not None:
        pending = pending[:limit]
    batches = write_batches(batch_dir, "locate", pending) if pending else []
    _log.info("%d letters need an agent; %d units; %d newly batched", candidates, len(units), len(pending))
    return PrepareReport(candidates, len(units), unreadable, tuple(batches))


def combine(
    units_path: Path,
    raw_dir: Path,
    batch_dir: Path,
    verify_units_path: Path,
    verdicts_path: Path,
    decided: str,
) -> CombineReport:
    """Pair every answer on disk, extract agreed spans, record decided letters.

    Idempotent: it reads all answer files each time, writes the verify units and
    any tiebreak batches still needed, and appends a verdict only for a letter
    whose outcome is new or changed.
    """
    units = {str(u["unit_id"]): u for u in read_jsonl(units_path)}
    la, lb, lc = read_answer_files(raw_dir, "locate")
    va, vb, vc = read_answer_files(raw_dir, "verify")
    before = recorded(verdicts_path)
    locate_tb: list[str] = []
    verify_tb: list[str] = []
    invalid: list[str] = []
    verify_units: list[dict[str, object]] = []
    new_rows: list[dict[str, object]] = []
    answered = agreed = none_located = accepted = rejected = 0
    for unit_id, unit in units.items():
        if unit_id not in la and unit_id not in lb:
            continue
        answered += 1
        numbered_text = str(unit["segments"])
        outcome = pair_locate(unit_id, la.get(unit_id, ""), lb.get(unit_id, ""),
                              lc.get(unit_id, ""), set(segments_of(numbered_text)))
        if outcome.invalid:
            invalid.append(unit_id)
            continue
        if outcome.needs_tiebreak:
            locate_tb.append(unit_id)
            continue
        if not outcome.located:
            continue
        agreed += 1
        row: dict[str, object] = {
            "unit_id": unit_id, "locate_a": la.get(unit_id), "locate_b": lb.get(unit_id),
            "locate_tiebreak": lc.get(unit_id), "located": outcome.located, "text": "",
            "verify_a": None, "verify_b": None, "verify_tiebreak": None,
            "locate_prompt_version": LOCATE_PROMPT_VERSION,
            "verify_prompt_version": VERIFY_PROMPT_VERSION, "decided": decided,
        }
        if outcome.located == NONE:
            none_located += 1
            row["final"] = NONE_LOCATED
        else:
            text = extract(numbered_text, outcome.located)
            verify_units.append({"unit_id": unit_id, "approval": unit["approval"], "text": text})
            final, needs = pair_verify(va.get(unit_id, ""), vb.get(unit_id, ""), vc.get(unit_id, ""))
            if needs:
                verify_tb.append(unit_id)
            if not final:
                continue
            row.update(text=text, verify_a=va.get(unit_id), verify_b=vb.get(unit_id),
                       verify_tiebreak=vc.get(unit_id),
                       final=ACCEPTED if final == STATES else REJECTED)
            if final == STATES:
                accepted += 1
            else:
                rejected += 1
        held = before.get(unit_id)
        if held is None or any(held.get(k) != row.get(k) for k in ("final", "located", "text")):
            new_rows.append(row)
    write_jsonl(verify_units_path, verify_units)
    verify_done = batched_ids(batch_dir, "verify")
    verify_new = write_batches(batch_dir, "verify", [
        str(u["unit_id"]) for u in verify_units if u["unit_id"] not in verify_done
    ])
    locate_tb_new = write_batches(batch_dir, "locate-tiebreak", [
        u for u in locate_tb if u not in batched_ids(batch_dir, "locate-tiebreak")
    ])
    verify_tb_new = write_batches(batch_dir, "verify-tiebreak", [
        u for u in verify_tb if u not in batched_ids(batch_dir, "verify-tiebreak")
    ])
    if new_rows:
        verdicts_path.parent.mkdir(parents=True, exist_ok=True)
        with verdicts_path.open("a", encoding="utf-8") as handle:
            for row in new_rows:
                handle.write(json.dumps(row) + "\n")
    return CombineReport(answered, agreed, tuple(locate_tb_new), tuple(invalid), none_located,
                         len(verify_units), tuple(verify_new), tuple(verify_tb_new),
                         accepted, rejected, len(new_rows))


def render(report: CombineReport) -> str:
    """The combine report, with the next action spelled out."""
    lines = [
        "Letter indications — locate → extract → verify (docs/runbooks/letter-indication.md)",
        "",
        f"  letters with locator answers             {report.locate_answered:>9,}",
        f"    located (agreed or tiebroken)          {report.locate_agreed:>9,}",
        f"      NONE                                 {report.none_located:>9,}",
        f"      spans extracted by code              {report.spans:>9,}",
        f"    invalid segment ids (re-run)           {len(report.invalid):>9,}",
        f"  verified: accepted                       {report.accepted:>9,}",
        f"            rejected                       {report.rejected:>9,}",
        f"  verdict rows newly recorded              {report.newly_recorded:>9,}",
        "",
        "  next:",
    ]
    for label, paths in (("locate tiebreak", report.locate_tiebreaks),
                         ("verify (A+B)", report.verify_batches),
                         ("verify tiebreak", report.verify_tiebreaks)):
        for path in paths:
            lines.append(f"    run {label}: {path.name}")
    if not (report.locate_tiebreaks or report.verify_batches or report.verify_tiebreaks):
        lines.append("    nothing pending for the answers on disk")
    return "\n".join(lines)


__all__ = [
    "CombineReport",
    "PrepareReport",
    "combine",
    "prepare",
    "render",
    "ACCEPTED",
    "BATCH_SIZE",
    "LOCATE_PROMPT_VERSION",
    "NONE",
    "NONE_LOCATED",
    "NOT_INDICATION",
    "REJECTED",
    "STATES",
    "VERIFY_PROMPT_VERSION",
    "LocateOutcome",
    "accepted_spans",
    "batched_ids",
    "extract",
    "needs_agent",
    "numbered",
    "pair_locate",
    "pair_verify",
    "parse_answers",
    "read_answer_files",
    "read_jsonl",
    "recorded",
    "segment",
    "segments_of",
    "write_batches",
    "write_jsonl",
]
