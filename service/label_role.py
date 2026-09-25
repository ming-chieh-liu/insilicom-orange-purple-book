"""Role of every label link, at submission grain (D28).

P4 answers *which* registry trial a section-14 description is; it never asks
whether the label presents that trial as evidence for this approval. A label
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

Units are built from the links P5 would make with no role filter applied
(:func:`service.assemble.build` with ``apply_label_roles=False``), after D16:
a trial is judged only where it is attributed. The judge sees the passages of
the submission's section 14 that mention the trial, each under its subsection
heading, and the approval being judged — never the registry record.

Prompt: ``docs/prompts/label-trial-role.md``; runbook:
``docs/runbooks/label-trial-role.md``.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NamedTuple

from config.settings import RawData
from service import role_judging, section14
from service.role_judging import RoleReport
from source import label_pdf
from utility import application_number as appno

PROMPT_VERSION: int = 1

STEP = "lrole"

#: Evidence sources that come from the label, and so need this judgement.
LABEL_SOURCES: frozenset[str] = frozenset({"label_nct", "label_description",
                                           "review_name_match"})

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


def targets_of(links: Iterable[object]) -> list[LinkTarget]:
    """The label-sourced links among P5's rows, one per ``(submission, trial)``.

    ``links`` are :class:`service.assemble.Link` rows; typed loosely so this
    module does not import P5.
    """
    out: dict[tuple[str, str], LinkTarget] = {}
    for link in links:
        sources = set(str(getattr(link, "sources", "") or
                          getattr(link, "evidence_source", "")).split(";"))
        if not sources & LABEL_SOURCES:
            continue
        key = (f"{getattr(link, 'application')}|{getattr(link, 'submission_type')}"
               f"|{getattr(link, 'submission_no')}")
        nct = str(getattr(link, "nct_id"))
        out.setdefault((key, nct), LinkTarget(
            key, nct, str(getattr(link, "trial_name", "") or ""),
            str(getattr(link, "indication_term", "") or ""),
            str(getattr(link, "label_enrollment", "") or ""),
        ))
    return list(out.values())


def _search_terms(target: LinkTarget) -> list[re.Pattern[str]]:
    """What marks a mention of this trial in the section text.

    >>> [p.pattern for p in _search_terms(LinkTarget("k", "NCT02578680", "KEYNOTE-189", "", "616"))]
    ['NCT\\\\s?02578680', 'KEYNOTE[\\\\s\\\\-]*189', '(?<![\\\\d,.])616(?![\\\\d,])']
    >>> [p.pattern for p in _search_terms(LinkTarget("k", "NCT1", "n=89", "", "1485"))][1:]
    ['(?<![\\\\d,.])1,?485(?![\\\\d,])']
    """
    # Labels also write "NCT 01624662", with a space.
    terms = [re.compile(r"NCT\s?" + re.escape(target.nct_id[3:]))]
    name = target.trial_name.strip()
    if name and name != target.heading and not name.startswith("n=") and len(name) >= 3:
        parts = [re.escape(p) for p in re.split(r"[\s\-]+", name) if p]
        terms.append(re.compile(r"[\s\-]*".join(parts), re.IGNORECASE))
    count = target.enrollment.replace(",", "")
    if count.isdigit() and len(count) >= 2:
        digits = f"{count[:-3]},?{count[-3:]}" if len(count) > 3 else count
        terms.append(re.compile(rf"(?<![\d,.]){digits}(?![\d,])"))
    return terms


def passages(section_14: str, target: LinkTarget) -> str:
    """The section-14 text a judge reads for this trial, or ``""``.

    Every mention (registry id, trial name, stated patient count) with
    :data:`WINDOW` characters either side, labelled with its subsection
    heading. A trial the label never names — a unit keyed on its count or its
    heading — falls back to the subsection whose heading the unit carries.

    >>> body = "14 CLINICAL STUDIES\\n14.1 Lung Cancer\\nKEYNOTE-189 enrolled 616 patients."
    >>> passages(body, LinkTarget("k", "NCT02578680", "KEYNOTE-189", "Lung Cancer", "616"))
    '[14.1 Lung Cancer] … 14 CLINICAL STUDIES 14.1 Lung Cancer KEYNOTE-189 enrolled 616 patients. …'
    >>> passages(body, LinkTarget("k", "NCT9", "OTHER-1", "Breast", ""))
    ''
    """
    blocks = section14.split_subsections(section_14)
    found: list[str] = []
    seen: set[str] = set()
    offset = 0
    spans: list[tuple[int, int, str]] = []
    for number, heading, block in blocks:
        start = section_14.find(block, offset)
        start = start if start >= 0 else offset
        spans.append((start, start + len(block), f"{number} {heading}".strip()))
        offset = start + len(block)
    for pattern in _search_terms(target):
        for match in pattern.finditer(section_14):
            label = next((lab for lo, hi, lab in spans if lo <= match.start() < hi), "14")
            snippet = " ".join(section_14[max(0, match.start() - WINDOW):
                                          match.end() + WINDOW].split())
            if snippet[:200] in seen:
                continue
            seen.add(snippet[:200])
            found.append(f"[{label}] … {snippet} …")
            if len(found) >= MAX_PASSAGES:
                return "\n\n".join(found)
    if found:
        return "\n\n".join(found)
    heading = target.heading.strip().casefold()
    for number, block_heading, block in blocks:
        if heading and block_heading.strip().casefold() == heading:
            text = " ".join(block.split())[:MAX_SUBSECTION]
            return f"[{number} {block_heading.strip()}] {text}"
    return ""


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
    """How the prompt names the study to judge.

    P3 keys an unnamed trial on its patient count or its subsection heading, and
    neither reads as a name, so they are spelled out.

    >>> target_label(LinkTarget("k", "NCT1", "KEYNOTE-189", "Lung", "616"))
    'KEYNOTE-189 (registry id NCT1; the label may not print it)'
    >>> target_label(LinkTarget("k", "NCT1", "n=89", "", "89"))
    'the study of 89 patients (registry id NCT1; the label may not print it)'
    >>> target_label(LinkTarget("k", "NCT1", "Lung", "Lung", ""))
    'the study described under "Lung" (registry id NCT1; the label may not print it)'
    """
    name = target.trial_name.strip()
    if name.startswith("n="):
        shown = f"the study of {name[2:]} patients"
    elif not name or name == target.heading:
        shown = f'the study described under "{target.heading}"' if target.heading \
            else "the study"
    else:
        shown = name
    return f"{shown} (registry id {target.nct_id}; the label may not print it)"


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


def label_urls(data: RawData, keys: set[str]) -> dict[str, str]:
    """``application|type|number`` → its type-2 label URL, for ``keys`` only.

    Keyed as P2 keys them: ApplicationDocs carries bare digits, so each is
    tried as an NDA and a BLA against the wanted set.
    """
    out: dict[str, str] = {}
    for document in label_pdf.read_label_documents(data):
        digits = appno.from_parts(None, document.application).digits
        for prefix in ("NDA", "BLA"):
            key = f"{prefix}{digits}|{document.submission_type}|{document.submission_no}"
            if key in keys and key not in out:
                out[key] = document.url
    return out


def _section_14(path: Path) -> str:
    got = label_pdf.extract_text(path)
    return section14.section(got[0], 14) if got else ""


def read_sections(urls: dict[str, str], cache_root: Path, *, parsers: int = 16,
                  extract: Callable[[Path], str] = _section_14) -> dict[str, str]:
    """Section 14 of each submission's **cached** label; never fetches.

    P2 does the downloading at its polite rate. A label not in the cache yields
    no unit here (``no_label_text``) rather than a request to FDA.
    """
    paths = {url: label_pdf.cache_path(url, cache_root) for url in set(urls.values())
             if label_pdf.is_cached(url, cache_root)}
    if parsers <= 1:
        by_url = {url: extract(path) for url, path in paths.items()}
    else:  # pragma: no cover - process pool, exercised by the real run
        with ProcessPoolExecutor(max_workers=parsers) as pool:
            by_url = dict(zip(paths, pool.map(extract, paths.values())))
    return {key: by_url[url] for key, url in urls.items() if by_url.get(url)}


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
    lines = ["Label-link roles — units (D28, docs/runbooks/label-trial-role.md)", ""]
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
    "LABEL_SOURCES",
    "LinkTarget",
    "PROMPT_VERSION",
    "UnitsReport",
    "approval_line",
    "build_units",
    "target_label",
    "read_sentences",
    "combine",
    "label_urls",
    "passages",
    "prepare",
    "read_roles",
    "read_sections",
    "render",
    "render_units",
    "targets_of",
]
