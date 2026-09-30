"""Which registered trial a label's section-14 description names.

P3 leaves 6,013 units, each one paragraph of label prose beside a median of 25
registered studies that survived the drug, condition and date filters. Exactly
one of those studies is usually the trial the paragraph describes, and no rule
picks it: the paragraph says *"a randomized, double-blind trial conducted in 616
patients"* and the registry says ``enrollment: 616``, but it also says 559, 580
and 624 for the neighbours, and the words differ every time. A judge reads it.

**This task is not the polarity task, and the difference matters.** A wrong
polarity mislabels a link that exists. A wrong identification **fabricates an
edge** — it asserts a drug was approved on the evidence of a trial that had
nothing to do with it, and nothing downstream can tell that apart from a real
one. So ``NONE`` is a first-class answer here rather than a failure, and the
prompt says so in those words.

Everything else follows ``docs/runbooks/identify-trial.md`` and the convention
it implements: two independent judges, a third on disagreement, verdicts
append-only and stamped with the prompt version, and units already decided
skipped so a re-run costs nothing.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from utility.logging import get_logger

_log = get_logger(__name__)

#: Bumped whenever ``docs/prompts/trial-identification.md`` changes, and stamped
#: on every verdict: a verdict written under v1 and one under v2 are otherwise
#: indistinguishable.
PROMPT_VERSION: int = 1

#: The abstention. Recorded as an answer, not as a missing one.
NONE: str = "NONE"

#: Shortlist sizes a pilot should be built from. Below the floor the judge has
#: nothing to choose between; above the ceiling the unit tests how long the
#: shortlist is rather than whether the prompt works. The median across the
#: 6,013 units is 24, so this band is where the real work sits.
PILOT_BAND: tuple[int, int] = (5, 25)

#: A judge's answer must be one of the unit's own candidates, or NONE. An id
#: that was not on the list is a bug or a hallucination; either way it is
#: refused rather than stored.
ANSWER = re.compile(r"^(NCT\d{8}|NONE)$")

#: ``unit_id = <application>|<submission_type>|<submission_no>|<trial name>``.
#: The name is the acronym where the label gave one, else the NCT id, else the
#: subsection heading — the same key P3 collapsed on.
UNIT_ID_FIELDS: int = 4


class Verdict(NamedTuple):
    """One decided unit."""

    unit_id: str
    judge_a: str
    judge_b: str
    tiebreak: str | None
    final: str
    prompt_version: int

    @property
    def identified(self) -> bool:
        """Whether a trial was actually named.

        >>> Verdict("u", "NCT1", "NCT1", None, "NCT00000001", 1).identified
        True
        >>> Verdict("u", "NONE", "NONE", None, "NONE", 1).identified
        False
        """
        return self.final != NONE


def unit_id_for(
    application: str, submission_type: str, submission_no: str, name: str
) -> str:
    """The key one judgement is recorded against.

    >>> unit_id_for("BLA125514", "SUPPL", "35", "KEYNOTE-189")
    'BLA125514|SUPPL|35|KEYNOTE-189'
    """
    return f"{application}|{submission_type}|{submission_no}|{name}"


def read_units(
    path: Path,
    borrowed: frozenset[str] = frozenset(),
    guard: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """Every unit P3 wrote that still needs a judge, one per trial.

    A unit whose label stated its own NCT id is skipped: sending it would spend
    two judge calls confirming what the label already said.

    **And only the earliest submission carrying a trial is judged.** Section 14
    is cumulative — a subsection is added at the approval it supported and then
    never removed — so KEYNOTE-087 appears on 37 separate Keytruda submissions,
    from 2017-05-10 through 2026-07-10. It supported the first of those, the
    classical Hodgkin lymphoma approval; the other 36 carry it because the
    label kept it, not because it was evidence for them.

    Judging all 37 would spend 74 agent calls to reach one answer and then
    assert that one trial supported 37 distinct approvals, which is the
    deliverable's central claim made 36 times wrongly. Taking the earliest
    drops 4,963 units to 3,007 and is the more defensible attribution: see D16.

    **A biosimilar's section 14 is its reference product's, so it is not
    judged at all.** ``borrowed`` holds the MeSH ids that were reached only by
    stripping a four-letter biologic suffix. A 351(k) application copies the
    originator's Clinical Studies section verbatim, so the excerpt truthfully
    describes the originator's trial and a judge identifies it correctly —
    then the link asserts that the biosimilar was approved on evidence that
    legally cannot support it, since a 351(k) rests on its own PK and
    similarity data. Observed on the first wave of the full run: eight
    ustekinumab biosimilars (PYZCHIVA, OTULFI, YESINTEK, STARJEMZA, SELARSDI,
    IMULDOSA, WEZLANA) were all assigned ``NCT00267969``, **and both judges
    agreed every time** — so the two-judge protocol cannot catch it and the
    stem tiering only makes the false claim tier B rather than tier A.

    Skipping them costs nothing real and saves 1,036 calls. They are recorded
    in ``unlinked_submissions.tsv`` as ``biosimilar_copied_label`` rather than
    dropped, so recall stays auditable.

    **A D16-guard submission is grouped, then withheld (D24).** ``guard`` holds
    the ``application|type|number`` keys whose ``trial_scope`` is ``guard`` —
    labeling or manufacturing changes *with clinical data*. Their units take
    part in the earliest-wins grouping like any other, and only then is a group
    whose earliest unit is a guard dropped. Dropping them *before* grouping
    would make the next in-scope supplement carrying the same trial look like
    its first appearance, and a judge would then attribute the trial to it —
    the misattribution the guard exists to prevent.
    """
    by_trial: dict[tuple[str, str], dict[str, object]] = {}
    seen = skipped = 0
    for row in iter_json_lines(path):
        if row.get("stated_nct") or row.get("review_nct"):
            continue
        # D35: case C and D units are per submission, not per trial, and are
        # judged by their own prompt (``candidate_match.select_units``).
        if row.get("case") not in (None, "", "B"):
            continue
        candidates = row.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            continue
        if borrowed and set(str(row.get("drug_mesh") or "").split(";")) & borrowed:
            skipped += 1
            continue
        seen += 1
        # The same key P3 collapsed on, taken from the unit rather than
        # rebuilt: an unnamed trial falls back to its enrollment count or a
        # sequence number, and rederiving that here would merge distinct
        # trials back together.
        key = (
            str(row.get("application") or ""),
            str(row.get("unit_id") or "").rsplit("|", 1)[-1],
        )
        held = by_trial.get(key)
        if held is None or str(row.get("approval_date") or "") < str(
            held.get("approval_date") or ""
        ):
            by_trial[key] = row
    earliest = list(by_trial.values())
    units = [unit for unit in earliest if submission_key(unit) not in guard]
    _log.info(
        "%d units carry a trial; %d distinct (application, trial) need a judge"
        "; %d skipped as a biosimilar's borrowed section 14"
        "; %d withheld as a D16 guard",
        seen,
        len(units),
        skipped,
        len(earliest) - len(units),
    )
    return units


def submission_key(unit: dict[str, object]) -> str:
    """``application|submission_type|submission_no`` for a unit.

    >>> submission_key({"application": "NDA000001", "submission_type": "SUPPL",
    ...                 "submission_no": 7})
    'NDA000001|SUPPL|7'
    """
    return (
        f"{unit.get('application') or ''}|{unit.get('submission_type') or ''}"
        f"|{unit.get('submission_no') or ''}"
    )


def guard_submissions(approval_events: Path) -> frozenset[str]:
    """Submission keys whose ``trial_scope`` is ``guard`` (D24).

    Read from ``approval_events.tsv`` rather than recomputed, so P0, P4 and P5
    cannot disagree about which submissions are guards. A spine written before
    the column existed yields no guards.
    """
    if not approval_events.is_file():
        return frozenset()
    with approval_events.open(encoding="utf-8") as handle:
        return frozenset(
            f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("trial_scope") == "guard"
        )


def borrowed_ids(drug_map: Path) -> frozenset[str]:
    """MeSH ids reached only by stripping a four-letter biologic suffix.

    Read from ``drug_map.tsv`` rather than recomputed, so P4 and P5 cannot
    disagree about which drugs are stem-sourced.
    """
    if not drug_map.is_file():
        return frozenset()
    with drug_map.open(encoding="utf-8") as handle:
        return frozenset(
            row["mesh_id"]
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("basis") == "stem" and row.get("mesh_id")
        )


def prioritise(
    units: list[dict[str, object]], limit: int | None
) -> list[dict[str, object]]:
    """The first ``limit`` units, the ones that actually test the prompt first.

    A pilot has to be representative of the judgement, not of the extremes.
    Sorting by shortest shortlist — the obvious choice, and the first one tried
    — fills a 200-unit pilot entirely with single-candidate units, where the
    judge has nothing to choose between and the answer says nothing about the
    prompt. Sorting by longest fills it with 200-candidate tails, which
    measures the shortlist instead.

    Size alone is not enough either. Banding by size still filled a pilot with
    units whose excerpt was scraped table text and whose "acronym" was a false
    positive like ``BOTOX 95`` — a judge answers NONE to all of them, correctly,
    and the run measures the section-14 extractor rather than the prompt. What
    makes a unit decidable is the label having given **a trial name and a
    patient count**, so those come first.

    Order: decidable units inside :data:`PILOT_BAND`, then the rest of the
    band, then everything else. A full run (``limit=None``) is unaffected in
    content and only reordered.
    """
    low, high = PILOT_BAND

    def key(unit: dict[str, object]) -> tuple[int, int, int, str]:
        candidates = unit.get("candidates")
        size = len(candidates) if isinstance(candidates, list) else 0
        in_band = 0 if low <= size <= high else 1
        decidable = 0 if (unit.get("acronym") and unit.get("enrollment")) else 1
        return decidable, in_band, size, str(unit.get("unit_id") or "")

    ordered = sorted(units, key=key)
    return ordered if limit is None else ordered[:limit]


def render_candidates(candidates: Iterable[dict[str, object]]) -> str:
    """The candidate block the prompt substitutes.

    One line per study, fields in the order the prompt tells the judge to weigh
    them: the count first, because that is what discriminates.
    """
    lines = []
    for index, candidate in enumerate(candidates, 1):
        lines.append(
            f"{index}. {candidate['nct_id']}"
            f" | enrolled: {candidate.get('enrollment') or 'not stated'}"
            f" | phase: {candidate.get('phases') or 'not stated'}"
            f" | started: {candidate.get('start_date') or 'not stated'}"
            f" | name: {candidate.get('acronym') or candidate.get('org_study_id') or '-'}"
            f" | title: {str(candidate.get('brief_title') or '')[:140]}"
            f" | conditions: {str(candidate.get('conditions') or '')[:120]}"
            f" | interventions: {str(candidate.get('interventions') or '')[:120]}"
        )
    return "\n".join(lines)


def write_units(units: Iterable[dict[str, object]], path: Path) -> int:
    """Write the judging units, each carrying its rendered candidate block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            raw = unit.get("candidates")
            candidates: list[dict[str, object]] = raw if isinstance(raw, list) else []
            handle.write(
                json.dumps(
                    {
                        "unit_id": unit["unit_id"],
                        "excerpt": unit.get("excerpt", ""),
                        "acronym": unit.get("acronym") or "(the label gives no name)",
                        "enrollment": unit.get("enrollment")
                        or "(the label gives no count)",
                        "candidate_ids": [c["nct_id"] for c in candidates],
                        "candidates": render_candidates(candidates),
                    }
                )
                + "\n"
            )
            written += 1
    _log.info("wrote %d units to %s", written, path)
    return written


def candidate_ids_by_unit(path: Path) -> dict[str, set[str]]:
    """Unit id → the candidate ids that unit was shown.

    What :func:`record_verdicts` validates against: an answer naming an id that
    was never on the list was misread or invented, and storing it would be
    exactly the fabricated edge this task exists to avoid.
    """
    out: dict[str, set[str]] = {}
    for row in iter_json_lines(path):
        unit_id = str(row.get("unit_id") or "")
        raw = row.get("candidate_ids")
        if unit_id and isinstance(raw, list):
            out[unit_id] = {str(value) for value in raw}
    return out


def read_verdicts(path: Path) -> dict[str, Verdict]:
    """Every decided unit, last row winning.

    Append-only, so a re-judgement corrects an earlier one without rewriting
    history.
    """
    out: dict[str, Verdict] = {}
    for row in iter_json_lines(path):
        unit_id = str(row.get("unit_id") or "")
        if not unit_id:
            continue
        out[unit_id] = Verdict(
            unit_id=unit_id,
            judge_a=str(row.get("judge_a") or ""),
            judge_b=str(row.get("judge_b") or ""),
            tiebreak=str(row["tiebreak"]) if row.get("tiebreak") else None,
            final=str(row.get("final") or ""),
            prompt_version=int(str(row.get("prompt_version") or PROMPT_VERSION)),
        )
    return out


def is_allowed(answer: str, candidate_ids: Iterable[str]) -> bool:
    """Whether an answer is one this unit could have.

    A judge naming an id that was not on its list has either misread or
    invented one. Refusing it is the point: see the module docstring on why a
    fabricated edge is the failure this task has to avoid.

    >>> is_allowed("NONE", ["NCT00000001"])
    True
    >>> is_allowed("NCT00000001", ["NCT00000001"])
    True
    >>> is_allowed("NCT00000002", ["NCT00000001"])
    False
    >>> is_allowed("maybe the first one", ["NCT00000001"])
    False
    """
    if not ANSWER.match(answer):
        return False
    return answer == NONE or answer in set(candidate_ids)


def combine(
    judge_a: dict[str, str],
    judge_b: dict[str, str],
    tiebreaks: dict[str, str],
) -> tuple[list[dict[str, object]], list[str], list[str]]:
    """Pair two judges' answers into verdicts, agreement required.

    Returns ``(decided, needing a third judge, answered by only one)``.
    Agreement means the same answer, including agreeing on ``NONE`` — two
    judges independently declining is a decision, not a gap.
    """
    decided: list[dict[str, object]] = []
    pending: list[str] = []
    absent: list[str] = []
    for unit_id, answer_a in judge_a.items():
        answer_b = judge_b.get(unit_id)
        if answer_b is None:
            absent.append(unit_id)
            continue
        if answer_a == answer_b:
            decided.append(
                {
                    "unit_id": unit_id,
                    "judge_a": answer_a,
                    "judge_b": answer_b,
                    "tiebreak": None,
                    "final": answer_a,
                    "prompt_version": PROMPT_VERSION,
                }
            )
            continue
        third = tiebreaks.get(unit_id)
        if third is None:
            pending.append(unit_id)
            continue
        decided.append(
            {
                "unit_id": unit_id,
                "judge_a": answer_a,
                "judge_b": answer_b,
                "tiebreak": third,
                "final": third,
                "prompt_version": PROMPT_VERSION,
            }
        )
    for unit_id in judge_b:
        if unit_id not in judge_a:
            absent.append(unit_id)
    return decided, pending, absent


def read_judge_file(path: Path) -> dict[str, str]:
    """A judge's answers, as ``<unit_id> = <answer>`` lines.

    Anything that is not that shape is skipped and counted by the caller. A
    line the judge padded with prose is not a verdict.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        # Split on the LAST '=', not the first. A unit whose label gave no
        # trial name is keyed on its enrollment count -- BLA125516|ORIG|1|n=226
        # -- so partitioning on the first '=' yields unit_id "…|n" and answer
        # "226 = NCT00026312", which fails validation and drops the unit
        # silently. Thirteen of sixty went missing that way before this.
        unit_id, _, answer = line.rpartition("=")
        unit_id, answer = unit_id.strip(), answer.strip()
        if unit_id and ANSWER.match(answer):
            out[unit_id] = answer
    return out


def record_verdicts(
    rows: Iterable[dict[str, object]], path: Path, allowed: dict[str, set[str]]
) -> tuple[int, int]:
    """Validate and append. Returns ``(written, refused)``.

    ``allowed`` maps each unit to the candidate ids it was shown, so an answer
    naming something that was never on the list is refused rather than stored.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = refused = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            unit_id = str(row.get("unit_id") or "")
            final = str(row.get("final") or "")
            if not unit_id or not is_allowed(final, allowed.get(unit_id, set())):
                refused += 1
                _log.warning("refused %r = %r", unit_id, final)
                continue
            handle.write(
                json.dumps({**row, "prompt_version": PROMPT_VERSION}) + "\n"
            )
            written += 1
    return written, refused


def summarise(verdicts: dict[str, Verdict]) -> tuple[tuple[str, int], ...]:
    """Counts for the run report."""
    identified = sum(1 for v in verdicts.values() if v.identified)
    tiebroken = sum(1 for v in verdicts.values() if v.tiebreak)
    return (
        ("decided", len(verdicts)),
        ("identified a trial", identified),
        ("answered NONE", len(verdicts) - identified),
        ("needed a tiebreak", tiebroken),
    )


def iter_json_lines(path: Path) -> Iterator[dict[str, object]]:
    """Each JSON object in a JSONL file; a malformed line is skipped."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


__all__ = [
    "ANSWER",
    "NONE",
    "PILOT_BAND",
    "PROMPT_VERSION",
    "UNIT_ID_FIELDS",
    "Verdict",
    "candidate_ids_by_unit",
    "combine",
    "is_allowed",
    "iter_json_lines",
    "prioritise",
    "read_judge_file",
    "guard_submissions",
    "read_units",
    "submission_key",
    "read_verdicts",
    "record_verdicts",
    "render_candidates",
    "summarise",
    "unit_id_for",
    "write_units",
]
