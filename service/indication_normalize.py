"""One indication table, coded once, normalised to MeSH and to biokde (D30).

Every source of "what is this approval for" is read elsewhere and arrives here
as raw text; this module is the only place any of it is coded.

========================  ==========================================  ==========
source                    raw text read by                            grain
========================  ==========================================  ==========
``letter``                P2b ``letter_indications.tsv`` (only        submission
                          sentences flagged indication-bearing)
``letter_located``        agent-located letter spans, copied by code  submission
                          (``adjudication/letter_indication_verdicts``)
``label_section_1``       P2a ``submission_indication_text.tsv``      submission
``label_14_heading``      P2a ``label_trials.tsv`` subsection         one trial
                          headings, keyed by the heading              description
``openfda_current``       ``indications.tsv`` (today's label,         application
                          already coded) — fallback only
========================  ==========================================  ==========

**One coder** (:func:`code_text`): an exact match of the whole text against the
registry condition names P1 already mapped, then the MeSH entry-term sweep of
:mod:`service.indications` (possessive and negation guards). It is P3's former
heading coder, which was the better of the three paths, now applied to every
source and saved.

**One id space.** Every coded disease is written as its MeSH descriptor
(``MESH:D…``) **and** the graph's ``biokdeid``, so the label route and the
graph route read the same ids.

**The submission-level choice** (``chosen``): the first of letter → that
submission's label §1 → the current openFDA label that codes at least one
disease. §14 headings are never ``chosen`` — they say which disease one trial
was for, not what the approval covers.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

from service import indications
from utility.logging import get_logger

_log = get_logger(__name__)

SOURCE_LETTER = "letter"
#: A letter span located by the agents and copied by code (service.letter_locate).
SOURCE_LETTER_LOCATED = "letter_located"
SOURCE_LABEL_1 = "label_section_1"
SOURCE_HEADING = "label_14_heading"
SOURCE_OPENFDA = "openfda_current"

#: Submission-level sources, in precedence order.
PRECEDENCE: tuple[str, ...] = (
    SOURCE_LETTER, SOURCE_LETTER_LOCATED, SOURCE_LABEL_1, SOURCE_OPENFDA
)

#: Characters of source text kept per row.
TEXT_KEEP: int = 600

MESH_PREFIX = "MESH:"


class IndicationRow(NamedTuple):
    """One coded disease from one source of one submission."""

    application: str
    submission_type: str
    submission_no: str
    source: str
    source_key: str
    text: str
    mesh_id: str
    biokdeid: str
    term: str
    chosen: str


class IndicationReport(NamedTuple):
    """The table, plus what it measured."""

    rows: tuple[IndicationRow, ...]
    submissions: int
    chosen_by_source: tuple[tuple[str, int], ...]
    rows_by_source: tuple[tuple[str, int], ...]
    coded_ids: int
    with_biokdeid: int


def _prefixed(ui: str) -> str:
    """``D002289`` → ``MESH:D002289``; an already prefixed id is kept.

    >>> _prefixed("D002289"), _prefixed("MESH:D002289")
    ('MESH:D002289', 'MESH:D002289')
    """
    return ui if ui.startswith(MESH_PREFIX) else f"{MESH_PREFIX}{ui}"


def code_text(
    text: str, exact: dict[str, str], index: indications.TermIndex
) -> list[str]:
    """Every disease ``text`` names, as ``MESH:`` ids — the one coder (D30).

    An exact whole-text match against a registry condition name wins, because
    it names the disease directly; otherwise every descriptor the entry-term
    sweep finds.

    >>> ix = indications.TermIndex({"melanoma": "D008545", "lung cancer": "D008175"},
    ...                            {"D008545": "Melanoma", "D008175": "Lung Neoplasms"}, 2, 0)
    >>> code_text("Non-Small Cell Lung Cancer", {"non-small cell lung cancer": "MESH:D002289"}, ix)
    ['MESH:D002289']
    >>> code_text("Advanced Melanoma", {}, ix)
    ['MESH:D008545']
    >>> code_text("Melanoma and Lung Cancer", {}, ix)
    ['MESH:D008175', 'MESH:D008545']
    >>> code_text("ARISTOTLE", {}, ix)
    []
    """
    direct = exact.get(text.strip().casefold())
    if direct:
        return [_prefixed(direct)]
    return sorted(_prefixed(ui) for ui in indications.code(text, index))


def build(
    submissions: Iterable[tuple[str, str, str]],
    *,
    letters: dict[str, str],
    label_section_1: dict[str, str],
    letters_located: dict[str, str] | None = None,
    headings: dict[str, list[str]],
    openfda: dict[str, set[str]],
    exact: dict[str, str],
    known_biokdeids: dict[str, str],
    index: indications.TermIndex,
    internal_ids: Callable[[Iterable[str]], dict[str, str]],
) -> IndicationReport:
    """Code every source of every in-scope submission into one table.

    Keys are ``application|type|number``; ``openfda`` is keyed by application.
    ``letters`` holds only indication-bearing sentences. ``internal_ids`` is
    :meth:`dao.biokde.BiokdeRepository.internal_ids`, injected so this module
    touches no database; ``known_biokdeids`` are the ids P1 already resolved.
    """
    names = {_prefixed(ui): name for ui, name in index.names.items()}
    pending: list[tuple[tuple[str, str, str], str, str, str, list[str]]] = []
    for application, submission_type, submission_no in submissions:
        key = f"{application}|{submission_type}|{submission_no}"
        sub = (application, submission_type, submission_no)
        coded_submission = False
        located = letters_located or {}
        for source, text in ((SOURCE_LETTER, letters.get(key, "")),
                             (SOURCE_LETTER_LOCATED, located.get(key, "")),
                             (SOURCE_LABEL_1, label_section_1.get(key, ""))):
            if not text:
                continue
            ids = code_text(text, exact, index)
            pending.append((sub, source, "", text, ids))
            coded_submission = coded_submission or bool(ids)
        if not coded_submission and openfda.get(application):
            ids = sorted(_prefixed(ui) for ui in openfda[application])
            pending.append((sub, SOURCE_OPENFDA, "", "", ids))
        for heading in headings.get(key, []):
            pending.append((sub, SOURCE_HEADING, heading, heading,
                            code_text(heading, exact, index)))

    all_ids = {i for *_, ids in pending for i in ids}
    biokde = dict(known_biokdeids)
    missing = [i for i in sorted(all_ids) if i not in biokde]
    if missing:
        biokde.update(internal_ids(missing))

    chosen_source: dict[tuple[str, str, str], str] = {}
    for sub, source, _key, _text, ids in pending:
        if source == SOURCE_HEADING or not ids:
            continue
        held = chosen_source.get(sub)
        if held is None or PRECEDENCE.index(source) < PRECEDENCE.index(held):
            chosen_source[sub] = source

    rows: list[IndicationRow] = []
    for sub, source, source_key, text, ids in pending:
        chosen = "true" if chosen_source.get(sub) == source else "false"
        for mesh_id in ids or [""]:
            rows.append(
                IndicationRow(
                    application=sub[0],
                    submission_type=sub[1],
                    submission_no=sub[2],
                    source=source,
                    source_key=source_key,
                    text=" ".join(text.split())[:TEXT_KEEP],
                    mesh_id=mesh_id,
                    biokdeid=biokde.get(mesh_id, "") if mesh_id else "",
                    term=names.get(mesh_id, "") if mesh_id else "",
                    chosen=chosen if mesh_id else "false",
                )
            )
    coded = {r.mesh_id for r in rows if r.mesh_id}
    _log.info("%d indication rows over %d submissions", len(rows), len({r[:3] for r in rows}))
    return IndicationReport(
        rows=tuple(rows),
        submissions=len({r[:3] for r in rows}),
        chosen_by_source=tuple(Counter(chosen_source.values()).most_common()),
        rows_by_source=tuple(Counter(r.source for r in rows).most_common()),
        coded_ids=len(coded),
        with_biokdeid=sum(1 for i in coded if biokde.get(i)),
    )


def read_chosen(path: Path) -> dict[str, set[tuple[str, str]]]:
    """Each submission's chosen diseases as ``{(biokdeid, term)}``, keyed
    ``application|type|number`` — what the graph route walks (D30).

    Only rows with a ``biokdeid`` are kept: the graph is keyed on it. An absent
    table yields ``{}``, and callers fall back to the application-level
    ``indications.tsv`` so an older output directory still works.
    """
    out: dict[str, set[tuple[str, str]]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row.get("chosen") == "true" and row.get("biokdeid"):
                key = f"{row['application']}|{row['submission_type']}|{row['submission_no']}"
                out.setdefault(key, set()).add((row["biokdeid"], row.get("term") or ""))
    return out


def render(report: IndicationReport, in_scope: int) -> str:
    """The pass report, as ``--mode indication-normalize`` prints it."""
    chosen = dict(report.chosen_by_source)
    lines = [
        "Submission indications — one table, one coder, MeSH + biokde (D30)",
        "",
        f"  in-scope submissions                     {in_scope:>9,}",
        f"    with a chosen indication               {sum(chosen.values()):>9,}",
    ]
    for source in PRECEDENCE:
        lines.append(f"      from {source:<32}{chosen.get(source, 0):>9,}")
    lines += ["", "  rows by source:"]
    for source, count in report.rows_by_source:
        lines.append(f"    {source:<38}{count:>9,}")
    lines += [
        "",
        f"  distinct diseases coded                  {report.coded_ids:>9,}",
        f"    with a biokdeid                        {report.with_biokdeid:>9,}",
    ]
    return "\n".join(lines)


__all__ = [
    "PRECEDENCE",
    "SOURCE_HEADING",
    "SOURCE_LABEL_1",
    "SOURCE_LETTER",
    "SOURCE_LETTER_LOCATED",
    "SOURCE_OPENFDA",
    "IndicationReport",
    "IndicationRow",
    "build",
    "code_text",
    "read_chosen",
    "render",
]
