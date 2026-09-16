"""Tests for assembling the deliverable and tiering its evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import assemble


# ---------------------------------------------------------------------------
# The tier rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "registry", "expected"),
    [
        ("970", "970", "counts_agree"),
        ("5407", "5407", "counts_agree"),
        # A label reports the analysed population where the registry reports
        # everyone enrolled, so a small gap still corroborates.
        ("844", "900", "counts_agree"),
        ("374", "895", "counts_diverge"),
        # The case the tier exists for: 3 is a table number the section-14
        # extractor read as an enrollment.
        ("3", "41", "counts_diverge"),
        ("", "895", "no_count"),
        ("970", "", "no_count"),
        ("abc", "970", "no_count"),
        ("0", "0", "no_count"),
    ],
)
def test_whether_two_counts_corroborate(
    label: str, registry: str, expected: str
) -> None:
    assert assemble.counts_agree(label, registry) == expected


@pytest.mark.parametrize(
    ("source", "check", "expected"),
    [
        # The FDA stated the id itself; there is nothing to corroborate.
        ("label_nct", "no_count", "A"),
        ("label_nct", "counts_diverge", "A"),
        # A judged link reaches A only with a matching count.
        ("label_description", "counts_agree", "A"),
        ("label_description", "counts_diverge", "B"),
        ("label_description", "no_count", "B"),
    ],
)
def test_the_tier_a_link_earns(source: str, check: str, expected: str) -> None:
    assert assemble.tier_for(source, check) == expected


# ---------------------------------------------------------------------------
# The whole pass
# ---------------------------------------------------------------------------


def _unit(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "unit_id": "BLA125514|SUPPL|35|KEYNOTE-189",
        "application": "BLA125514",
        "submission_type": "SUPPL",
        "submission_no": "35",
        "approval_date": "2018-08-20",
        "heading": "Non-Small Cell Lung Cancer",
        "indication_mesh": "MESH:D002289",
        "drug_mesh": "MESH:C582435",
        "stated_nct": "NCT02578680",
        "acronym": "KEYNOTE-189",
        "enrollment": "616",
        "excerpt": "…616 patients…",
        "candidates": [
            {"nct_id": "NCT02578680", "enrollment": "616"},
            {"nct_id": "NCT00000001", "enrollment": "40"},
        ],
    }
    base.update(kw)
    return base


_SPINE = (
    "application\tapp_type\tsubmission_type\tsubmission_no\tsubmission_class"
    "\taction_type\tapproval_date\tera\treview_priority\torphan"
    "\tindication_changing\ttrial_relevant\n"
    "BLA125514\tBLA\tSUPPL\t35\tEfficacy\tEfficacy-New Indication\t2018-08-20"
    "\t2008+\tP\tfalse\ttrue\ttrue\n"
    "BLA125514\tBLA\tSUPPL\t45\tEfficacy\tEfficacy-New Indication\t2018-12-19"
    "\t2008+\tP\tfalse\ttrue\ttrue\n"
    "BLA125514\tBLA\tSUPPL\t99\tLabeling\t\t2019-01-01\t2008+\t\tfalse"
    "\tfalse\tfalse\n"
)


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "output"
    adj = tmp_path / "adjudication"
    out.mkdir()
    adj.mkdir()
    (out / "approval_events.tsv").write_text(_SPINE, encoding="utf-8")
    return out, adj


def _write_units(out: Path, units: list[dict[str, object]]) -> None:
    (out / "candidates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in units) + "\n", encoding="utf-8"
    )


def test_a_label_stated_id_becomes_a_tier_a_link(
    workspace: tuple[Path, Path]
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    report = assemble.build(out, adj)
    assert len(report.links) == 1
    assert report.links[0].evidence_tier == "A"
    assert report.links[0].evidence_source == "label_nct"


def test_the_counts_ride_along_for_a_reader(workspace: tuple[Path, Path]) -> None:
    """Their agreement is the tier, so a reader must be able to check it."""
    out, adj = workspace
    _write_units(out, [_unit()])
    link = assemble.build(out, adj).links[0]
    assert (link.label_enrollment, link.registry_enrollment) == ("616", "616")
    assert link.count_check == "counts_agree"


def test_a_later_appearance_of_the_same_trial_is_not_a_link(
    workspace: tuple[Path, Path]
) -> None:
    """Section 14 is cumulative: KEYNOTE-189 is named in 37 Keytruda labels and
    was the evidence for the earliest. Linking all 37 asserts one trial
    supported 37 approvals. See D16."""
    out, adj = workspace
    _write_units(
        out,
        [
            _unit(),
            _unit(
                unit_id="BLA125514|SUPPL|45|KEYNOTE-189",
                submission_no="45",
                approval_date="2018-12-19",
            ),
        ],
    )
    report = assemble.build(out, adj)
    assert len(report.links) == 1
    assert report.links[0].submission_no == "35"
    assert "later_appearance_of_the_same_trial" in {u.reason for u in report.unlinked}


def test_a_judged_link_with_agreeing_counts_reaches_tier_a(
    workspace: tuple[Path, Path]
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", unit_id="u1")])
    (adj / "identify_verdicts.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "judge_a": "NCT02578680",
                "judge_b": "NCT02578680",
                "tiebreak": None,
                "final": "NCT02578680",
                "prompt_version": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    link = assemble.build(out, adj).links[0]
    assert link.evidence_source == "label_description"
    assert link.evidence_tier == "A"


def test_a_judged_link_whose_counts_diverge_is_demoted(
    workspace: tuple[Path, Path]
) -> None:
    """The TABLE 3 case: a label 'count' of 3 against a registry 41. Without
    this the row is indistinguishable from CHECKMATE-648 at 970 against 970."""
    out, adj = workspace
    _write_units(
        out,
        [
            _unit(
                stated_nct="",
                unit_id="u1",
                enrollment="3",
                candidates=[{"nct_id": "NCT01733316", "enrollment": "41"}],
            )
        ],
    )
    (adj / "identify_verdicts.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "judge_a": "NCT01733316",
                "judge_b": "NCT01733316",
                "tiebreak": None,
                "final": "NCT01733316",
                "prompt_version": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    link = assemble.build(out, adj).links[0]
    assert link.evidence_tier == "B"
    assert link.count_check == "counts_diverge"


def test_a_judged_none_is_recorded_as_unlinked(
    workspace: tuple[Path, Path]
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", unit_id="u1")])
    (adj / "identify_verdicts.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "judge_a": "NONE",
                "judge_b": "NONE",
                "tiebreak": None,
                "final": "NONE",
                "prompt_version": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = assemble.build(out, adj)
    assert report.links == ()
    assert dict(report.by_reason)["judged_none"] == 1


def test_an_unjudged_unit_says_so_rather_than_vanishing(
    workspace: tuple[Path, Path]
) -> None:
    """Recall stays auditable: every row that produced no link carries why."""
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", unit_id="u1")])
    report = assemble.build(out, adj)
    assert "not_yet_judged" in {u.reason for u in report.unlinked}


def test_a_unit_with_no_drug_names_that_reason(
    workspace: tuple[Path, Path]
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", unit_id="u1", drug_mesh="", candidates=[])])
    report = assemble.build(out, adj)
    assert "no_drug_resolved" in {u.reason for u in report.unlinked}


def test_a_submission_no_description_reached_is_still_accounted_for(
    workspace: tuple[Path, Path]
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    report = assemble.build(out, adj)
    assert "no_description_extracted" in {u.reason for u in report.unlinked}


def test_only_trial_relevant_submissions_are_counted(
    workspace: tuple[Path, Path]
) -> None:
    """The labeling supplement in the fixture is not trial-driven."""
    out, adj = workspace
    _write_units(out, [_unit()])
    report = assemble.build(out, adj)
    assert report.submissions_total == 2


def test_the_report_renders_its_tiers(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    rendered = assemble.render(assemble.build(out, adj))
    assert "by evidence tier" in rendered
    assert "unlinked rows" in rendered


def test_coverage_is_zero_with_no_submissions() -> None:
    assert assemble.AssembleReport((), (), (), (), (), 0, 0).coverage == 0.0
