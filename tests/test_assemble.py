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
    # Three supplements approved on ONE day — the Temodar shape. 36 and 37
    # share a label PDF; 38 has its own.
    "NDA021029\tNDA\tSUPPL\t36\tEfficacy\tEfficacy-New Indication"
    "\t2023-09-14\t2008+\tP\tfalse\ttrue\ttrue\n"
    "NDA021029\tNDA\tSUPPL\t37\tEfficacy\tEfficacy-New Indication"
    "\t2023-09-14\t2008+\tP\tfalse\ttrue\ttrue\n"
    "NDA021029\tNDA\tSUPPL\t38\tEfficacy\tEfficacy-New Indication"
    "\t2023-09-14\t2008+\tP\tfalse\ttrue\ttrue\n"
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
    assert "no_label" in {u.reason for u in report.unlinked}


def test_only_trial_relevant_submissions_are_counted(
    workspace: tuple[Path, Path]
) -> None:
    """The labeling supplement in the fixture is not trial-driven, so it is
    excluded: the spine holds six rows and five are trial-relevant."""
    out, adj = workspace
    _write_units(out, [_unit()])
    report = assemble.build(out, adj)
    assert report.submissions_total == 5


def test_the_report_renders_its_tiers(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    rendered = assemble.render(assemble.build(out, adj))
    assert "by evidence tier" in rendered
    assert "unlinked rows" in rendered


def test_coverage_is_zero_with_no_submissions() -> None:
    assert assemble.AssembleReport((), (), (), (), (), 0, 0).coverage == 0.0


# ---------------------------------------------------------------------------
# A stem-derived drug is a retrieval key, not an identity
# ---------------------------------------------------------------------------


def test_a_stem_sourced_link_cannot_reach_tier_a_on_agreeing_counts() -> None:
    """A biosimilar retrieves on its reference product's id, so the pool is
    mostly the reference product's trials — and agreeing patient counts are
    exactly what a wrong match in that pool looks like. A 351(k) rests on its
    own trials (D11)."""
    assert assemble.tier_for("label_description", "counts_agree", "stem") == "B"


def test_a_stated_id_is_unaffected_by_a_stem_sourced_drug() -> None:
    """The FDA wrote the trial id on the label. How we resolved the drug name
    afterwards does not weaken that."""
    assert assemble.tier_for("label_nct", "no_count", "stem") == "A"


def test_an_ordinary_drug_still_reaches_tier_a_on_agreeing_counts() -> None:
    assert assemble.tier_for("label_description", "counts_agree", "") == "A"


# ---------------------------------------------------------------------------
# One submission, one explanation
# ---------------------------------------------------------------------------


def test_a_submission_is_never_both_unjudged_and_unlabelled(
    workspace: tuple[Path, Path]
) -> None:
    """Stated as a property over the whole report, which is how the bug would
    have been caught: no submission may carry the catch-all alongside a
    specific reason."""
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", unit_id="u1")])
    report = assemble.build(out, adj)
    by_submission: dict[tuple[str, str, str], set[str]] = {}
    for row in report.unlinked:
        key = (row.application, row.submission_type, row.submission_no)
        by_submission.setdefault(key, set()).add(row.reason)
    for key, reasons in by_submission.items():
        assert not ("no_label" in reasons and len(reasons) > 1), key


# ---------------------------------------------------------------------------
# D16 tie-breaking: the label decides how many claims a shared date carries
# ---------------------------------------------------------------------------


def _temodar(number: str, sha: str, heading: str = "Glioblastoma") -> dict[str, object]:
    return _unit(
        unit_id=f"NDA021029|SUPPL|{number}|{heading}",
        application="NDA021029", submission_type="SUPPL", submission_no=number,
        approval_date="2023-09-14", heading=heading, stated_nct="NCT00006353",
        acronym="", label_sha=sha,
        candidates=[{"nct_id": "NCT00006353", "enrollment": "573"}],
    )


def test_one_label_serving_three_submissions_yields_one_link(
    workspace: tuple[Path, Path]
) -> None:
    """Temodar SUPPL 36, 37 and 38 were approved the same day off a single
    021029s036_037_038lbl.pdf. Comparing dates alone let all three through."""
    out, adj = workspace
    _write_units(out, [_temodar(n, "sha-shared") for n in ("36", "37", "38")])
    links = [l for l in assemble.build(out, adj).links if l.application == "NDA021029"]
    assert [l.submission_no for l in links] == ["36"]


def test_distinct_labels_on_one_day_stay_distinct(
    workspace: tuple[Path, Path]
) -> None:
    """Half the ties are genuinely different documents sharing a date."""
    out, adj = workspace
    _write_units(out, [_temodar(n, f"sha-{n}") for n in ("36", "37", "38")])
    links = [l for l in assemble.build(out, adj).links if l.application == "NDA021029"]
    assert {l.submission_no for l in links} == {"36", "37", "38"}


def test_an_unknown_digest_is_distinct_never_shared(
    workspace: tuple[Path, Path]
) -> None:
    """The dangerous direction. With no label_sha — units built before P2
    recorded one, or a PDF that would not hash — grouping on the empty string
    would collapse every tied submission into one link and say nothing.
    Missing data must not hide a real answer."""
    out, adj = workspace
    _write_units(out, [_temodar(n, "") for n in ("36", "37", "38")])
    links = [l for l in assemble.build(out, adj).links if l.application == "NDA021029"]
    assert {l.submission_no for l in links} == {"36", "37", "38"}


def test_a_same_day_sibling_is_not_called_a_later_appearance(
    workspace: tuple[Path, Path]
) -> None:
    """It is not later, it is simultaneous. An auditor reading the unlinked
    file should not be told something untrue."""
    out, adj = workspace
    _write_units(out, [_temodar(n, "sha-shared") for n in ("36", "37", "38")])
    report = assemble.build(out, adj)
    dropped = {u.reason for u in report.unlinked if u.submission_no in {"37", "38"}}
    assert dropped == {"same_day_sibling_submission"}


def test_a_genuinely_later_submission_is_still_dropped(
    workspace: tuple[Path, Path]
) -> None:
    """The tie-break must not weaken D16 itself."""
    out, adj = workspace
    _write_units(out, [
        _unit(unit_id="a", submission_no="35", approval_date="2018-08-20", label_sha="a"),
        _unit(unit_id="b", submission_no="45", approval_date="2018-12-19", label_sha="b"),
    ])
    report = assemble.build(out, adj)
    assert [l.submission_no for l in report.links] == ["35"]
    assert "later_appearance_of_the_same_trial" in {u.reason for u in report.unlinked}


def test_ties_break_numerically_not_lexically() -> None:
    """Drugs@FDA stores submission numbers as strings, so "10" < "9"."""
    assert assemble._submission_sort("9") < assemble._submission_sort("10")


def test_one_trial_under_two_headings_is_one_row(
    workspace: tuple[Path, Path]
) -> None:
    """The declared grain is one row per (trial, submission). KEYNOTE-158 is
    cited under both "MSI-High" and "Endometrial Carcinoma" on one label."""
    out, adj = workspace
    _write_units(out, [
        _temodar("36", "sha", "Glioblastoma"),
        _temodar("36", "sha", "Anaplastic Astrocytoma"),
    ])
    links = [l for l in assemble.build(out, adj).links if l.application == "NDA021029"]
    assert len(links) == 1


# ---------------------------------------------------------------------------
# Section-14 polarity reaches the deliverable
# ---------------------------------------------------------------------------


def _polarity(adj: Path, rows: list[dict[str, object]]) -> None:
    (adj / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_a_refuted_trial_does_not_reach_the_deliverable(
    workspace: tuple[Path, Path]
) -> None:
    """The fenofibrate shape: section 14 cites ACCORD Lipid under "Lack of
    Efficacy". A regex sees an id; only a reader sees that it failed."""
    out, adj = workspace
    _write_units(out, [_unit()])
    _polarity(adj, [{"nct_id": "NCT02578680", "application": "BLA125514",
                     "final": "NOT_SUPPORTING"}])
    report = assemble.build(out, adj)
    assert report.links == ()
    assert "judged_not_supporting" in {u.reason for u in report.unlinked}


def test_supporting_anywhere_wins(workspace: tuple[Path, Path]) -> None:
    """The runbook's OR rule — Tyvaso cites one trial as both its pivotal
    study and its durability follow-up."""
    out, adj = workspace
    _write_units(out, [_unit()])
    _polarity(adj, [
        {"nct_id": "NCT02578680", "application": "BLA125514", "final": "NOT_SUPPORTING"},
        {"nct_id": "NCT02578680", "application": "BLA125514", "final": "SUPPORTING"},
    ])
    assert len(assemble.build(out, adj).links) == 1


def test_a_legacy_negative_verdict_still_counts_as_refuting() -> None:
    """v1 wrote NEGATIVE and UNCLEAR; both mean not-supporting downstream."""
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "v.jsonl").write_text(
        json.dumps({"nct_id": "N", "application": "A", "final": "NEGATIVE"}) + "\n",
        encoding="utf-8",
    )
    assert assemble.refuted_pairs(d / "v.jsonl") == {("N", "A")}


def test_an_unjudged_trial_is_not_refuted(workspace: tuple[Path, Path]) -> None:
    """Unjudged is not refuted; dropping it would make partial adjudication
    look like data loss."""
    out, adj = workspace
    _write_units(out, [_unit()])
    _polarity(adj, [{"nct_id": "NCT99999999", "application": "BLA125514",
                     "final": "NOT_SUPPORTING"}])
    assert len(assemble.build(out, adj).links) == 1


def test_polarity_is_keyed_on_the_application_too(
    workspace: tuple[Path, Path]
) -> None:
    """A trial can support one drug's approval and be a failure on another."""
    out, adj = workspace
    _write_units(out, [_unit()])
    _polarity(adj, [{"nct_id": "NCT02578680", "application": "NDA999999",
                     "final": "NOT_SUPPORTING"}])
    assert len(assemble.build(out, adj).links) == 1


def test_an_injected_candidate_cannot_reach_tier_a() -> None:
    """It entered the shortlist because the literature named it, not because it
    passed the drug filter — one of only two hard facts the pool rests on."""
    assert assemble.tier_for("label_description", "counts_agree", "", True) == "B"


def test_a_stated_id_is_unaffected_by_injection() -> None:
    """The FDA wrote the trial id on the label; how the candidate list was
    assembled afterwards does not weaken that."""
    assert assemble.tier_for("label_nct", "no_count", "", True) == "A"


# ---------------------------------------------------------------------------
# D24: guard submissions are read, not judged
# ---------------------------------------------------------------------------

_GUARD_SPINE = (
    "application\tapp_type\tsubmission_type\tsubmission_no\tsubmission_class"
    "\taction_type\tapproval_date\tera\treview_priority\torphan"
    "\tindication_changing\ttrial_relevant\tclass_code\ttrial_scope\tscope_basis\n"
    "NDA000001\tNDA\tSUPPL\t3\tEfficacy"
    "\tEfficacy-Labeling Change With Clinical Data\t2010-01-01\t2008+\t\tfalse"
    "\ttrue\ttrue\tEFFICACY\tguard\tSUPPL EFFICACY: guard\n"
    "NDA000001\tNDA\tSUPPL\t9\tEfficacy\tEfficacy-New Indication\t2015-01-01"
    "\t2008+\t\tfalse\ttrue\ttrue\tEFFICACY\tfull\tSUPPL EFFICACY: full\n"
)


def _guard_units() -> list[dict[str, object]]:
    return [
        _unit(unit_id=f"NDA000001|SUPPL|{no}|STUDY-A", application="NDA000001",
              submission_no=no, approval_date=date, stated_nct="",
              drug_mesh="MESH:D000001")
        for no, date in (("3", "2010-01-01"), ("9", "2015-01-01"))
    ]


def test_a_guard_is_recorded_as_not_judged_by_decision(tmp_path: Path) -> None:
    out, adj = tmp_path / "output", tmp_path / "adjudication"
    out.mkdir()
    adj.mkdir()
    (out / "approval_events.tsv").write_text(_GUARD_SPINE, encoding="utf-8")
    _write_units(out, _guard_units())
    reasons = {
        (u.submission_no, u.reason) for u in assemble.build(out, adj).unlinked
    }
    assert ("3", "label_change_not_judged") in reasons


def test_a_later_appearance_of_a_guard_trial_is_not_waiting_for_a_judge(
    tmp_path: Path,
) -> None:
    """P4 withheld the whole group, so SUPPL 9 will never get a verdict; calling
    it not_yet_judged would make it look like pending work forever."""
    out, adj = tmp_path / "output", tmp_path / "adjudication"
    out.mkdir()
    adj.mkdir()
    (out / "approval_events.tsv").write_text(_GUARD_SPINE, encoding="utf-8")
    _write_units(out, _guard_units())
    reasons = {
        (u.submission_no, u.reason) for u in assemble.build(out, adj).unlinked
    }
    assert ("9", "later_appearance_of_the_same_trial") in reasons
    assert all(reason != "not_yet_judged" for _, reason in reasons)
