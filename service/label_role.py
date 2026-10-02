"""Role of every trial a label names by its NCT id, at submission grain (S6, D46).

D46 (2026-10-02): a role check belongs to every trial an FDA document names by
an exact id — a review's protocol id (:mod:`service.review_role`) and a label's
stated NCT id (this module) — and is done in S6, before any search; trials S9
identifies (routes B, C, D) get none. Both tasks render one prompt template
(``docs/prompts/fda-document-trial-role.md``).

Identifying a trial is not the same as knowing the label presents it as
evidence for this approval. A label
discusses failed trials ("Lack of Efficacy in Cardiovascular Outcomes
Trials"), safety follow-ups, PK studies and trials behind earlier approvals in
the same section, and each of those, identified correctly, is still a false
approval edge.

The old polarity task answered this once, on the *current* openFDA label, per
*application*, and was never re-run on the rebuilt units (1,055 of 3,361 links
had no verdict at all). This task judges each ``(submission, trial)`` link on
**that submission's own label**, with the same six roles as the review route
(D26), so the two routes' answers are comparable and ``role_disagrees`` means
something.

Units are built from S4's ``label_trials.tsv`` (:func:`targets_from_label`): one
per ``(application, stated NCT)`` at the **earliest** submission whose label
states it (D16), as the review route does. The judge sees the passages of that
submission's section 14 that mention the trial, each under its subsection
heading, and the approval being judged — never the registry record.

Prompt: the label rendering of ``docs/prompts/fda-document-trial-role.md``; runbook:
``docs/runbooks/label-trial-role.md``.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from service import role_judging, section14
from service.role_judging import RoleReport

PROMPT_VERSION: int = 2

STEP = "lrole"

#: Units whose role is known from the approval history, batched first.
CALIBRATION: tuple[str, ...] = (
    "BLA125514|SUPPL|35|NCT02578680",  # Keytruda KEYNOTE-189, NSCLC 2018 → PIVOTAL
)

#: Characters either side of a mention.
WINDOW: int = 700

#: Passages kept per unit.
MAX_PASSAGES: int = 8

#: A subsection shown whole when the trial is unnamed is cut here.
MAX_SUBSECTION: int = 3_000

#: Unit-building outcomes.
BUILT = "built"
NO_LABEL_TEXT = "no_label_text"
NOT_IN_SECTION_14 = "trial_not_found_in_section_14"


class LinkTarget(NamedTuple):
    """One label link to judge, as P5 would write it."""

    key: str
    nct_id: str
    trial_name: str
    heading: str
    enrollment: str


class UnitsReport(NamedTuple):
    """What unit building produced."""

    units: tuple[dict[str, str], ...]
    outcomes: Counter[str]


def _submission_rank(submission_type: str, submission_no: str) -> tuple[int, int]:
    """The original first, then supplements by number (numbers are strings in
    Drugs@FDA, so ``"10"`` must not sort before ``"9"``).

    >>> sorted([("SUPPL", "10"), ("SUPPL", "9"), ("ORIG", "1")], key=lambda s: _submission_rank(*s))
    [('ORIG', '1'), ('SUPPL', '9'), ('SUPPL', '10')]
    """
    number = int(submission_no) if submission_no.strip().isdigit() else 0
    return (0 if submission_type.strip() == "ORIG" else 1, number)


def targets_from_label(rows: Iterable[dict[str, str]],
                       approval_dates: dict[str, str]) -> list[LinkTarget]:
    """One target per ``(application, label-stated NCT)``, at the earliest
    submission whose label states it (D16): by approval date, then the original
    before supplements, then supplement number.

    >>> rows = [{"application": "BLA1", "submission_type": "SUPPL", "submission_no": "9",
    ...          "nct_id": "NCT1", "acronym": "KN-1", "heading": "Melanoma", "enrollment": "50"},
    ...         {"application": "BLA1", "submission_type": "ORIG", "submission_no": "1",
    ...          "nct_id": "NCT1", "acronym": "KN-1", "heading": "Melanoma", "enrollment": "50"},
    ...         {"application": "BLA1", "submission_type": "ORIG", "submission_no": "1",
    ...          "nct_id": "", "acronym": "X", "heading": "", "enrollment": ""}]
    >>> [t.key for t in targets_from_label(rows, {"BLA1|ORIG|1": "2014-09-04",
    ...                                           "BLA1|SUPPL|9": "2015-12-18"})]
    ['BLA1|ORIG|1']
    """
    best: dict[tuple[str, str], tuple[tuple[str, int, int], LinkTarget]] = {}
    for row in rows:
        nct = (row.get("nct_id") or "").strip()
        if not nct:
            continue
        key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
        rank = (approval_dates.get(key) or "9999",
                *_submission_rank(row["submission_type"], row["submission_no"]))
        pair = (row["application"], nct)
        if pair not in best or rank < best[pair][0]:
            best[pair] = (rank, LinkTarget(
                key, nct, row.get("acronym") or "",
                row.get("heading_full") or row.get("heading") or "", row.get("enrollment") or ""))
    return [target for _, target in sorted(best.values(), key=lambda b: (b[1].key, b[1].nct_id))]


def _nct_pattern(nct_id: str) -> re.Pattern[str]:
    """The id as S4 reads it: "NCT 01624662", "NCT#", "NCT #", "NCT:" (section14.NCT)."""
    return re.compile(r"NCT[\s:#-]{0,3}" + re.escape(nct_id[3:]))


def _name_pattern(name: str) -> str:
    """A trial name with its separators loosened: KEYNOTE-189 / KEYNOTE 189."""
    return r"[\s\-]*".join(re.escape(p) for p in re.split(r"[\s\-]+", name) if p)


def name_beside_id(section_14: str, target: LinkTarget) -> str:
    """S4's name for the trial, kept only where the text writes it right before
    the id — the guard against a name S4 paired wrongly (2026-10-02: a figure
    caption gave NCT00087711, Study JMDB, the name KEYNOTE-189). Same rule as
    S4's (:func:`section14.gap_allows_name`): at most
    ``section14.NAME_BEFORE_ID`` characters between, none of them a sentence end
    or a digit, a renaming aside ("referred to as …") set aside.

    >>> t = LinkTarget("k", "NCT00087711", "KEYNOTE-189", "", "")
    >>> name_beside_id("in KEYNOTE-189 *Based on it. Study JMDB (NCT00087711)", t)
    ''
    >>> name_beside_id("Study KEYNOTE 189 (NCT00087711)", t)
    'KEYNOTE-189'
    >>> name_beside_id("KEYNOTE-189 (referred to as Study 189) (NCT00087711)", t)
    'KEYNOTE-189'
    """
    name = target.trial_name.strip()
    if not name or name == target.heading or name.startswith("n=") or len(name) < 3:
        return ""
    pattern = re.compile(_name_pattern(name) + r"(.{0,120}?)" + _nct_pattern(target.nct_id).pattern,
                         re.IGNORECASE | re.DOTALL)
    return name if any(section14.gap_allows_name(m.group(1))
                       for m in pattern.finditer(section_14)) else ""


def _search_terms(section_14: str, target: LinkTarget) -> list[re.Pattern[str]]:
    """What marks a mention of this trial in the section text.

    The id always; the name only as :func:`build_units` passes it (verified
    beside the id); the patient count only where it is written in a sentence
    that also writes the id.

    >>> body = "KEYNOTE-189 (NCT02578680) enrolled 616 patients. Another had 616 sites."
    >>> [p.pattern for p in _search_terms(body, LinkTarget("k", "NCT02578680", "KEYNOTE-189", "", "616"))]
    ['NCT[\\\\s:#-]{0,3}02578680', 'KEYNOTE[\\\\s\\\\-]*189', '(?<![\\\\d,.])616(?![\\\\d,])']
    >>> [p.pattern for p in _search_terms("NCT1 was run. It had 1,485 patients.", LinkTarget("k", "NCT1", "", "", "1485"))]
    ['NCT[\\\\s:#-]{0,3}1']
    """
    nct = _nct_pattern(target.nct_id)
    terms = [nct]
    name = target.trial_name.strip()
    if name and name != target.heading and not name.startswith("n=") and len(name) >= 3:
        terms.append(re.compile(_name_pattern(name), re.IGNORECASE))
    count = target.enrollment.replace(",", "")
    if count.isdigit() and len(count) >= 2:
        digits = f"{count[:-3]},?{count[-3:]}" if len(count) > 3 else count
        count_term = re.compile(rf"(?<![\d,.]){digits}(?![\d,])")
        sentences = re.split(r"(?<=[.;])\s+", section_14)
        if any(nct.search(s) and count_term.search(s) for s in sentences):
            terms.append(count_term)
    return terms


def passages(section_14: str, target: LinkTarget) -> str:
    """The section-14 text a judge reads for this trial, or ``""``.

    Every mention (registry id, and the name and count as :func:`_search_terms`
    allows) with :data:`WINDOW` characters either side; windows that overlap in
    one subsection are merged, so no stretch of text is shown twice. Passages
    holding the id come first when :data:`MAX_PASSAGES` cuts, then everything is
    shown in reading order, each labelled with its subsection heading. A trial
    the label never mentions falls back to the subsection whose heading the
    unit carries.

    >>> body = "14 CLINICAL STUDIES\\n14.1 Lung Cancer\\nKEYNOTE-189 (NCT02578680) enrolled 616 patients."
    >>> passages(body, LinkTarget("k", "NCT02578680", "KEYNOTE-189", "Lung Cancer", "616"))
    '[14.1 Lung Cancer] … 14 CLINICAL STUDIES 14.1 Lung Cancer KEYNOTE-189 (NCT02578680) enrolled 616 patients. …'
    >>> passages(body, LinkTarget("k", "NCT9", "OTHER-1", "Breast", ""))
    ''
    """
    blocks = section14.split_subsections(section_14)
    offset = 0
    spans: list[tuple[int, int, str]] = []
    for number, heading, block in blocks:
        start = section_14.find(block, offset)
        start = start if start >= 0 else offset
        spans.append((start, start + len(block), f"{number} {heading}".strip()))
        offset = start + len(block)
    terms = _search_terms(section_14, target)
    windows: list[tuple[int, int, str, bool]] = []
    for index, pattern in enumerate(terms):
        for match in pattern.finditer(section_14):
            label = next((lab for lo, hi, lab in spans if lo <= match.start() < hi), "14")
            windows.append((max(0, match.start() - WINDOW), match.end() + WINDOW, label,
                            index == 0))
    merged: list[list[object]] = []
    for lo, hi, label, has_id in sorted(windows):
        if merged and merged[-1][2] == label and lo <= int(str(merged[-1][1])):
            merged[-1][1] = max(int(str(merged[-1][1])), hi)
            merged[-1][3] = bool(merged[-1][3]) or has_id
        else:
            merged.append([lo, hi, label, has_id])
    if merged:
        kept = sorted(merged, key=lambda m: (not m[3], int(str(m[0]))))[:MAX_PASSAGES]
        kept.sort(key=lambda m: int(str(m[0])))
        return "\n\n".join(
            f"[{m[2]}] … {' '.join(section_14[int(str(m[0])):int(str(m[1]))].split())} …"
            for m in kept)
    heading = target.heading.strip().casefold()
    for number, block_heading, block in blocks:
        if heading and block_heading.strip().casefold() == heading:
            text = " ".join(block.split())[:MAX_SUBSECTION]
            return f"[{number} {block_heading.strip()}] {text}"
    return ""


def sections_by_submission(records: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Each submission's section 14 as S4 stored it, keyed like a unit.

    A submission can carry two label PDFs, and S4 stores one record per PDF;
    every distinct text is kept, joined, so a mention in either is found.

    >>> sections_by_submission([
    ...     {"application": "NDA1", "submission_type": "ORIG", "submission_no": "1", "text": "a"},
    ...     {"application": "NDA1", "submission_type": "ORIG", "submission_no": "1", "text": "b"},
    ...     {"application": "NDA1", "submission_type": "ORIG", "submission_no": "1", "text": "a"}])
    {'NDA1|ORIG|1': 'a\\n\\nb'}
    """
    texts: dict[str, list[str]] = {}
    for record in records:
        key = f"{record['application']}|{record['submission_type']}|{record['submission_no']}"
        text = str(record.get("text") or "")
        if text and text not in texts.setdefault(key, []):
            texts[key].append(text)
    return {key: "\n\n".join(parts) for key, parts in texts.items() if parts}


def read_sentences(out_dir: Path) -> dict[str, str]:
    """Each submission's approval-letter indication sentence (D25), if any."""
    out: dict[str, str] = {}
    path = out_dir / "letter_indications.tsv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("sentence"):
                out[f"{row['application']}|{row['submission_type']}|{row['submission_no']}"] = \
                    row["sentence"]
    return out


def approval_line(key: str, approval_date: str, brands: Iterable[str],
                  sentence: str) -> str:
    """The APPROVAL BEING JUDGED line, built exactly as the review route builds it.

    >>> approval_line("BLA125514|SUPPL|35", "2018-08-20", {"KEYTRUDA"}, "")
    'KEYTRUDA · BLA125514 SUPPL 35 · approved 2018-08-20 · approval letter not available'
    """
    application, submission_type, submission_no = key.split("|")
    names = " / ".join(sorted(b for b in brands if b))
    return (f"{names} · {application} {submission_type} {submission_no} · "
            f"approved {approval_date} · {sentence or 'approval letter not available'}")


def target_label(target: LinkTarget) -> str:
    """How the prompt names the study to judge: its registry id, as the label
    prints it, and the name only where :func:`name_beside_id` kept one.

    >>> target_label(LinkTarget("k", "NCT1", "KEYNOTE-189", "Lung", "616"))
    'KEYNOTE-189 (registry id NCT1)'
    >>> target_label(LinkTarget("k", "NCT1", "", "Lung", ""))
    'the study registered as NCT1'
    """
    name = target.trial_name.strip()
    return f"{name} (registry id {target.nct_id})" if name \
        else f"the study registered as {target.nct_id}"


def build_units(targets: Iterable[LinkTarget], section_14_of: dict[str, str],
                approvals: dict[str, str]) -> UnitsReport:
    """One unit per target whose submission's label mentions the trial."""
    units: list[dict[str, str]] = []
    outcomes: Counter[str] = Counter()
    for target in targets:
        body = section_14_of.get(target.key, "")
        if not body:
            outcomes[NO_LABEL_TEXT] += 1
            continue
        # The guard: S4's name is shown and searched only where the text
        # writes it right before the id.
        target = target._replace(trial_name=name_beside_id(body, target))
        text = passages(body, target)
        if not text:
            outcomes[NOT_IN_SECTION_14] += 1
            continue
        outcomes[BUILT] += 1
        units.append({
            "unit_id": f"{target.key}|{target.nct_id}",
            "target": target_label(target),
            "approval": approvals.get(target.key, target.key),
            "text": text,
        })
    return UnitsReport(tuple(units), outcomes)


def prepare(units_path: Path, batch_dir: Path, verdicts: Path,
            limit: int | None = None) -> RoleReport:
    """Batch every unit without a current-version verdict, calibration first."""
    return role_judging.prepare(units_path, batch_dir, verdicts, version=PROMPT_VERSION,
                                calibration=CALIBRATION, step=STEP, limit=limit)


def combine(units_path: Path, raw_dir: Path, batch_dir: Path, verdicts: Path,
            decided_on: str) -> RoleReport:
    """Pair every answer on disk; batch tiebreaks; record new decisions."""
    return role_judging.combine(units_path, raw_dir, batch_dir, verdicts, decided_on,
                                version=PROMPT_VERSION, step=STEP)


def read_roles(verdicts: Path) -> dict[tuple[str, str], str]:
    """``(submission key, nct)`` → final role, current prompt version only."""
    out: dict[tuple[str, str], str] = {}
    for unit_id, row in role_judging.decided(verdicts, PROMPT_VERSION).items():
        if unit_id.count("|") == 3:
            key, nct = unit_id.rsplit("|", 1)
            out[(key, nct)] = str(row["final"])
    return out


def render_units(report: UnitsReport, path: Path) -> str:
    """The unit-building report."""
    lines = ["Label-link roles — units (S6, D46, docs/runbooks/label-trial-role.md)", ""]
    for name in (BUILT, NO_LABEL_TEXT, NOT_IN_SECTION_14):
        lines.append(f"  {name:<40} {report.outcomes.get(name, 0):>9,}")
    lines += ["", f"  written to {path}"]
    return "\n".join(lines)


def render(report: RoleReport) -> str:
    """The prepare/combine report."""
    return role_judging.render(
        report, "Label-link roles (docs/runbooks/label-trial-role.md)", PROMPT_VERSION)


__all__ = [
    "CALIBRATION",
    "LinkTarget",
    "PROMPT_VERSION",
    "UnitsReport",
    "approval_line",
    "build_units",
    "target_label",
    "read_sentences",
    "combine",
    "name_beside_id",
    "passages",
    "prepare",
    "read_roles",
    "sections_by_submission",
    "render",
    "render_units",
    "targets_from_label",
]
