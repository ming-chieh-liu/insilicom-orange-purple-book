"""Role judging for trials FDA reviews name (D26): prepare batches, combine answers.

``--mode reviews`` writes one unit per ``(submission, trial)`` to
``adjudication/review_role_units.jsonl``. Judges answer with one of six roles
(``docs/prompts/review-trial-role.md``); this module batches the undecided
units and pairs the answers, per ``docs/runbooks/review-trial-role.md``:
two judges, a fresh third on disagreement, nothing recorded that was not
returned.

**Versioned by folder.** Answers under prompt v2 live in
``review_role_raw/v2/`` and batches in ``review_role_batches/v2/``, so they never
mix with the pilot's v1 answers; a unit is decided only by a verdict carrying
the current :data:`PROMPT_VERSION`, so units judged under v1 are judged again.
"""

from __future__ import annotations

from pathlib import Path

from service import role_judging
from service.role_judging import LINKING, ROLES, RoleReport, pair

PROMPT_VERSION: int = 2

#: Units whose role is known from the approval history, plus the pilot's two
#: possible misses; batched first so every run re-checks them.
CALIBRATION: tuple[str, ...] = (
    "NDA202811|ORIG|1|NCT00938717",    # Linzess MCP-103-302 → PIVOTAL
    "NDA214787|SUPPL|10|NCT04292899",  # Veklury SIMPLE, earlier approval → NOT_FOR_THIS_APPROVAL
    "BLA125409|SUPPL|51|NCT01358877",  # Perjeta APHINITY, ongoing → NOT_FOR_THIS_APPROVAL
    "NDA202880|ORIG|1|NCT02126917",    # Zohydro alcohol interaction → PK_CLINPHARM
    "NDA214324|ORIG|1|NCT03950739",    # Tyvaso DPI BREEZE — v1 said OTHER; possible miss
    "NDA209500|SUPPL|6|NCT02600494",   # Caplyta ITI-007-401 — v1 said OTHER; possible miss
)

STEP = "role"


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


def render(report: RoleReport) -> str:
    """The prepare/combine report, with the next action spelled out."""
    return role_judging.render(
        report, "Review-trial roles (docs/runbooks/review-trial-role.md)", PROMPT_VERSION)


__all__ = [
    "CALIBRATION",
    "LINKING",
    "PROMPT_VERSION",
    "ROLES",
    "RoleReport",
    "combine",
    "pair",
    "prepare",
    "render",
]
