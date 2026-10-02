"""Tests for assembling the deliverable and tiering its evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import assemble, label_role


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
        # D32: a judged link is tier B whatever the counts say.
        ("label_description", "counts_agree", "B"),
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
    """The units, and (D46) a PIVOTAL S6 label role for every label-stated trial
    — what links it; a test about roles writes its own over these."""
    (out / "candidates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in units) + "\n", encoding="utf-8"
    )
    roles = [f"{u['application']}|{u['submission_type']}|{u['submission_no']}|{u['stated_nct']}"
             for u in units if u.get("stated_nct")]
    (out.parent / "adjudication" / "label_role_verdicts.jsonl").write_text("".join(
        json.dumps({"unit_id": r, "final": "PIVOTAL", "prompt_version": label_role.PROMPT_VERSION}) + "\n"
        for r in roles), encoding="utf-8")


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
                "prompt_version": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    link = assemble.build(out, adj).links[0]
    assert link.evidence_source == "label_description"
    # D32: agreeing counts are recorded, not promoted.
    assert (link.evidence_tier, link.count_check) == ("B", "counts_agree")


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
                "prompt_version": 3,
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
                "prompt_version": 3,
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


def test_agreeing_counts_no_longer_promote_a_judged_link() -> None:
    """D32: the judge chose on the count, so the count cannot also be the proof."""
    assert assemble.tier_for("label_description", "counts_agree", "") == "B"


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


# ---------------------------------------------------------------------------
# The review route joins the same D16 (D26, D27, D29)
# ---------------------------------------------------------------------------


def _reviews(adj: Path, verdicts: list[tuple[str, str, int]]) -> None:
    (adj / "review_role_units.jsonl").write_text(
        "\n".join(json.dumps({"unit_id": u, "protocol_id": f"P-{u[-3:]}"}) for u, _, _ in verdicts)
        + "\n", encoding="utf-8")
    (adj / "review_role_verdicts.jsonl").write_text(
        "\n".join(json.dumps({"unit_id": u, "final": role, "prompt_version": v})
                  for u, role, v in verdicts) + "\n", encoding="utf-8")


def test_a_review_only_link_is_tier_a(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|45|NCT09999999", "PIVOTAL", 2)])
    link = next(l for l in assemble.build(out, adj).links if l.nct_id == "NCT09999999")
    assert (link.evidence_tier, link.evidence_source, link.sources) == ("A", "review_protocol", "review_protocol")
    assert link.trial_name == "P-999" and link.submission_no == "45"


def test_a_review_only_link_takes_the_submissions_chosen_indication(
    workspace: tuple[Path, Path],
) -> None:
    """A review names no disease heading, so the link carries the approval's
    chosen indication from submission_indications.tsv (D30)."""
    out, adj = workspace
    _write_units(out, [_unit()])
    (out / "submission_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext\t"
        "mesh_id\tbiokdeid\tterm\tchosen\n"
        "BLA125514\tSUPPL\t45\tletter\tk\tt\tMESH:D008545\t11\tMelanoma\ttrue\n"
        "BLA125514\tSUPPL\t45\tletter\tk\tt\tMESH:D008545\t11\tMelanoma\ttrue\n"
        "BLA125514\tSUPPL\t45\tletter\tk\tt\tMESH:D002289\t12\tLung\ttrue\n"
        "BLA125514\tSUPPL\t45\topenfda_current\tk\tt\tMESH:D000001\t13\tX\tfalse\n"
        "BLA125514\tSUPPL\t45\tletter\tk\tt\t\t\t\tfalse\n",
        encoding="utf-8")
    _reviews(adj, [("BLA125514|SUPPL|45|NCT09999999", "PIVOTAL", 2)])
    link = next(l for l in assemble.build(out, adj).links if l.nct_id == "NCT09999999")
    assert link.indication_mesh == "MESH:D008545;MESH:D002289"
    assert link.indication_term == "Melanoma;Lung"


def test_without_the_indication_table_a_review_link_has_a_blank_indication(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|45|NCT09999999", "PIVOTAL", 2)])
    link = next(l for l in assemble.build(out, adj).links if l.nct_id == "NCT09999999")
    assert (link.indication_mesh, link.indication_term) == ("", "")


def test_both_routes_on_one_submission_merge_into_one_row(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "SUPPORTING", 2)])
    links = [l for l in assemble.build(out, adj).links if l.nct_id == "NCT02578680"]
    assert len(links) == 1
    assert links[0].sources == "label_nct;review_protocol" and links[0].conflict == ""


def test_an_earlier_review_moves_the_link_and_says_so(workspace: tuple[Path, Path]) -> None:
    """The label first names the trial on SUPPL 45; the review of SUPPL 35 --
    four months earlier -- already names it as pivotal. D16 across both routes
    gives the link to SUPPL 35 and the row records that the routes disagreed."""
    out, adj = workspace
    _write_units(out, [_unit(unit_id="BLA125514|SUPPL|45|KEYNOTE-189", submission_no="45",
                             approval_date="2018-12-19")])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "PIVOTAL", 2)])
    report = assemble.build(out, adj)
    links = [l for l in report.links if l.nct_id == "NCT02578680"]
    assert [(l.submission_no, l.evidence_source, l.conflict) for l in links] == [
        ("35", "review_protocol", "attribution_moved")
    ]
    assert report.attribution_moved == 1
    assert ("45", "later_appearance_of_the_same_trial") in {
        (u.submission_no, u.reason) for u in report.unlinked
    }
    assert "attribution moved" in assemble.render(report)


def test_non_linking_roles_are_context_and_v1_is_ignored(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT00000777", "PK_CLINPHARM", 2),
                   ("BLA125514|SUPPL|35|NCT00000888", "PIVOTAL", 1),
                   ("BLA125514|SUPPL|99|NCT00000999", "PIVOTAL", 2)])  # not trial-relevant
    report = assemble.build(out, adj)
    assert [(c.nct_id, c.role) for c in report.review_context] == [("NCT00000777", "PK_CLINPHARM")]
    assert {l.nct_id for l in report.links} == {"NCT02578680"}


# ---------------------------------------------------------------------------
# D29 — direct identification and the conflict column
# ---------------------------------------------------------------------------


def _identify(adj: Path, unit_id: str, final: str) -> None:
    (adj / "identify_verdicts.jsonl").write_text(json.dumps({
        "unit_id": unit_id, "judge_a": final, "judge_b": final, "tiebreak": None,
        "final": final, "prompt_version": 3,
    }) + "\n", encoding="utf-8")


def test_a_review_identified_description_is_tier_a_whatever_the_counts(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", review_nct="NCT02578680", enrollment="9")])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "PIVOTAL", 2)])  # D46: its S6 role
    [link] = assemble.build(out, adj).links
    assert (link.evidence_source, link.evidence_tier) == ("review_name_match", "A")
    assert link.sources == "label_description;review_protocol" and link.conflict == ""


def test_a_judge_contradicting_the_review_is_overruled_and_recorded(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", review_nct="NCT02578680")])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "PIVOTAL", 2)])  # D46: its S6 role
    _identify(adj, "BLA125514|SUPPL|35|KEYNOTE-189", "NCT01111111")
    report = assemble.build(out, adj)
    [link] = report.links
    assert link.nct_id == "NCT02578680"
    assert link.conflict == "judged_contradicts_direct"
    assert ("judged_contradicts_direct", 1) in report.by_conflict


def test_a_judge_agreeing_with_the_review_is_no_conflict(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="", review_nct="NCT02578680")])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "PIVOTAL", 2)])  # D46: its S6 role
    _identify(adj, "BLA125514|SUPPL|35|KEYNOTE-189", "NCT02578680")
    assert assemble.build(out, adj).links[0].conflict == ""


def test_a_review_calling_a_label_link_not_evidence_keeps_it_at_tier_a_flagged(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "FAILED", 2)])
    report = assemble.build(out, adj)
    [link] = report.links
    # D46: the label's linking S6 role links it; the review's disagreement is flagged.
    assert (link.conflict, link.evidence_tier) == ("role_disagrees", "A")
    assert [c.role for c in report.review_context] == ["FAILED"]


_INDICATIONS_HEAD = ("application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext\t"
                     "mesh_id\tbiokdeid\tterm\tchosen\n")


def _indications(out: Path, rows: list[tuple[str, str]]) -> None:
    (out / "submission_indications.tsv").write_text(_INDICATIONS_HEAD + "".join(
        f"BLA125514\tSUPPL\t35\t{source}\tk\tt\t{mesh}\t1\tx\tfalse\n" for source, mesh in rows
    ), encoding="utf-8")


def test_a_letter_indication_outside_label_section_1_is_flagged(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _indications(out, [("letter", "MESH:D008545"), ("label_section_1", "MESH:D002289")])
    [link] = assemble.build(out, adj).links
    assert (link.conflict, link.evidence_tier) == ("indication_mismatch", "A")


def test_a_letter_indication_inside_a_cumulative_section_1_is_fine(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _indications(out, [("letter_located", "MESH:D002289"), ("", ""),
                       ("label_section_1", "MESH:D002289"), ("label_section_1", "MESH:D008545")])
    assert assemble.build(out, adj).links[0].conflict == ""


def test_without_a_label_section_1_the_letter_is_not_tested(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _indications(out, [("letter", "MESH:D008545")])
    assert assemble.build(out, adj).links[0].conflict == ""


def test_the_report_lists_conflicts(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "FAILED", 2)])
    text = assemble.render(assemble.build(out, adj))
    assert "conflicts (D29" in text and "role_disagrees" in text


def test_a_review_identified_unit_does_not_take_part_in_guard_grouping() -> None:
    units: list[dict[str, object]] = [
        {"unit_id": "A|SUPPL|1|X", "application": "A", "submission_type": "SUPPL",
         "submission_no": "1", "approval_date": "2020", "review_nct": "NCT1"},
        {"unit_id": "A|SUPPL|2|X", "application": "A", "submission_type": "SUPPL",
         "submission_no": "2", "approval_date": "2021"},
    ]
    assert assemble._guard_led_units(units, {"A|SUPPL|1"}) == set()


# ---------------------------------------------------------------------------
# D46 — label-stated trials link on their S6 role; described trials need none
# ---------------------------------------------------------------------------


def _label_roles(adj: Path, rows: list[tuple[str, str]],
                 version: int = label_role.PROMPT_VERSION) -> None:
    (adj / "label_role_verdicts.jsonl").write_text("\n".join(
        json.dumps({"unit_id": u, "final": role, "prompt_version": version}) for u, role in rows
    ) + "\n", encoding="utf-8")


def test_a_label_role_that_does_not_link_drops_the_link_with_its_role(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _label_roles(adj, [("BLA125514|SUPPL|35|NCT02578680", "FAILED")])
    report = assemble.build(out, adj)
    assert report.links == ()
    assert "label_role_failed" in {u.reason for u in report.unlinked}


def test_a_role_under_an_old_prompt_version_is_ignored(workspace: tuple[Path, Path]) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _label_roles(adj, [("BLA125514|SUPPL|35|NCT02578680", "FAILED")], version=0)
    report = assemble.build(out, adj)
    # D46: a label-stated trial waits for its S6 role; it does not link unjudged.
    assert report.links == () and report.label_role_missing == 1
    assert assemble.LABEL_ROLE_PENDING in {u.reason for u in report.unlinked}
    assert "label-stated trials awaiting S6 role" in assemble.render(report)


def test_a_described_trial_needs_no_role(workspace: tuple[Path, Path]) -> None:
    """D46: a §14 description S9 identified links with no S6 role at all."""
    out, adj = workspace
    _write_units(out, [_unit(stated_nct="")])
    _identify(adj, "BLA125514|SUPPL|35|KEYNOTE-189", "NCT02578680")
    (adj / "label_role_verdicts.jsonl").write_text("", encoding="utf-8")
    [link] = assemble.build(out, adj).links
    assert link.evidence_source == "label_description"


def test_a_label_role_rejecting_what_the_review_links_is_role_disagrees(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    _label_roles(adj, [("BLA125514|SUPPL|35|NCT02578680", "NOT_FOR_THIS_APPROVAL")])
    _reviews(adj, [("BLA125514|SUPPL|35|NCT02578680", "SUPPORTING", 2)])
    [link] = assemble.build(out, adj).links
    # D46: either FDA document's linking verdict links it, at its own tier, flagged.
    assert (link.sources, link.conflict, link.evidence_tier) == (
        "review_protocol", "role_disagrees", "A")


# ---------------------------------------------------------------------------
# Tiers C and D join P5 (D35) — never above, never moving, a stronger link
# ---------------------------------------------------------------------------


def _case_unit(key: str, case: str, ncts: list[str]) -> dict[str, object]:
    application, submission_type, submission_no = key.split("|")
    return {
        "unit_id": f"{key}|{'indication' if case == 'C' else 'drug_only'}",
        "application": application, "submission_type": submission_type,
        "submission_no": submission_no, "approval_date": "2019-01-01",
        "heading": "", "indication_mesh": "", "drug_mesh": "MESH:C582435",
        "stated_nct": "", "acronym": "", "enrollment": "", "excerpt": "",
        "candidates": [{"nct_id": n, "enrollment": "500"} for n in ncts], "case": case,
    }


def _picks(adj: Path, rows: list[tuple[str, str]]) -> None:
    with (adj / "identify_verdicts.jsonl").open("a", encoding="utf-8") as handle:
        for unit_id, nct in rows:
            handle.write(json.dumps({"unit_id": unit_id, "judge_a": nct, "judge_b": "",
                                     "final": nct, "prompt_version": 3}) + "\n")


def test_a_case_c_pick_is_a_tier_c_link_and_a_case_d_pick_tier_d(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(), _case_unit("BLA125514|SUPPL|45", "C", ["NCT07777777"]),
                       _case_unit("NDA021029|SUPPL|38", "D", ["NCT07777778"])])
    _picks(adj, [("BLA125514|SUPPL|45|indication", "NCT07777777"),
                 ("NDA021029|SUPPL|38|drug_only", "NCT07777778")])
    got = {l.nct_id: l for l in assemble.build(out, adj).links}
    assert (got["NCT07777777"].evidence_tier, got["NCT07777777"].evidence_source) == (
        "C", "indication_candidate")
    assert (got["NCT07777778"].evidence_tier, got["NCT07777778"].sources) == ("D", "kg_abstract")
    assert got["NCT07777777"].registry_enrollment == "500"


def test_a_case_pick_agreeing_with_a_label_link_only_adds_a_source(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(), _case_unit("BLA125514|SUPPL|35", "D", ["NCT02578680"])])
    _picks(adj, [("BLA125514|SUPPL|35|drug_only", "NCT02578680")])
    [link] = assemble.build(out, adj).links
    assert link.evidence_tier == "A" and link.sources == "label_nct;kg_abstract"


def test_a_case_pick_never_moves_a_trial_another_route_attributes(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit(), _case_unit("BLA125514|SUPPL|45", "C", ["NCT02578680"])])
    _picks(adj, [("BLA125514|SUPPL|45|indication", "NCT02578680")])
    links = assemble.build(out, adj).links
    assert [(l.submission_no, l.evidence_tier) for l in links] == [("35", "A")]


def test_among_case_picks_the_earliest_submission_keeps_the_trial(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    d_early = _case_unit("BLA125514|SUPPL|35", "D", ["NCT07777777"])
    c_late = _case_unit("BLA125514|SUPPL|45", "C", ["NCT07777777"])
    outside = _case_unit("BLA999999|ORIG|1", "C", ["NCT07777778"])     # not in the spine
    _write_units(out, [d_early, c_late, outside])
    _picks(adj, [(str(d_early["unit_id"]), "NCT07777777"), (str(c_late["unit_id"]), "NCT07777777"),
                 (str(outside["unit_id"]), "NCT07777778")])
    links = [l for l in assemble.build(out, adj).links if l.evidence_tier in "CD"]
    # D37: tiers C and D are not ordered; D16 earliest-wins decides.
    assert [(l.nct_id, l.submission_no, l.evidence_tier) for l in links] == [
        ("NCT07777777", "35", "D")]


def test_undecided_and_none_case_units_are_unlinked_with_a_reason(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    none = _case_unit("BLA125514|SUPPL|45", "C", ["NCT07777777"])
    pending = _case_unit("NDA021029|SUPPL|36", "D", ["NCT07777778"])
    empty = {**_case_unit("BLA125514|SUPPL|35", "C", []), "drug_mesh": ""}
    _write_units(out, [none, pending, empty])
    _picks(adj, [(str(none["unit_id"]), "NONE")])
    reasons = {(u.submission_no, u.reason) for u in assemble.build(out, adj).unlinked}
    assert {("45", "judged_none"), ("36", "not_yet_judged"),
            ("35", "no_drug_resolved")} <= reasons


def test_the_graph_cross_check_corroborates_without_a_judge(
    workspace: tuple[Path, Path],
) -> None:
    """D19: the literature for this application names the linked trial."""
    out, adj = workspace
    _write_units(out, [_unit()])
    (out / "graph_trials.tsv").write_text(
        "application\tnct_id\tmentions\nBLA125514\tNCT02578680\t3\nBLA125514\tNCT0000009\t1\n",
        encoding="utf-8")
    [link] = assemble.build(out, adj).links
    assert link.sources == "label_nct;kg_literature" and link.evidence_tier == "A"


def test_the_cross_check_is_silent_when_the_literature_does_not_name_it(
    workspace: tuple[Path, Path],
) -> None:
    out, adj = workspace
    _write_units(out, [_unit()])
    (out / "graph_trials.tsv").write_text(
        "application\tnct_id\tmentions\nBLA999999\tNCT02578680\t3\n", encoding="utf-8")
    assert assemble.build(out, adj).links[0].sources == "label_nct"


def test_an_identification_made_before_d35_is_not_linked(workspace: tuple[Path, Path]) -> None:
    """Pick-one (v1) and first-pilot (v2) answers were made on the pre-D35
    shortlists; until re-decided the description is not_yet_judged."""
    out, adj = workspace
    unit = _unit(stated_nct="", unit_id="BLA125514|SUPPL|35|KEYNOTE-189")
    _write_units(out, [unit])
    (adj / "identify_verdicts.jsonl").write_text(json.dumps(
        {"unit_id": unit["unit_id"], "judge_a": "NCT02578680", "judge_b": "NCT02578680",
         "final": "NCT02578680", "prompt_version": 2}) + "\n", encoding="utf-8")
    report = assemble.build(out, adj)
    assert report.links == ()
    assert [u.reason for u in report.unlinked if u.submission_no == "35"] == ["not_yet_judged"]


def test_d37_routes_become_tiers_and_an_earlier_case_pick_moves_the_trial(
    workspace: tuple[Path, Path],
) -> None:
    """D37: the decision's route is the tier; D16 runs over every route."""
    out, adj = workspace
    with (out / "approval_events.tsv").open("a", encoding="utf-8") as handle:
        handle.write("BLA125514\tBLA\tSUPPL\t30\tEfficacy\tEfficacy-New Indication"
                     "\t2017-01-01\t2008+\tP\tfalse\ttrue\ttrue\n")
    early = _case_unit("BLA125514|SUPPL|30", "C", ["NCT02578680"])
    _write_units(out, [_unit(), early])
    _picks(adj, [(str(early["unit_id"]), "NCT02578680")])
    with (adj / "identify_verdicts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"unit_id": early["unit_id"], "judge_a": "NCT02578680",
                                 "final": "NCT02578680", "prompt_version": 3,
                                 "route": "C+"}) + "\n")
    report = assemble.build(out, adj)
    [link] = report.links
    assert (link.submission_no, link.evidence_tier, link.sources) == (
        "30", "C+", "indication_candidate;literature")
    assert "attribution_moved" in link.conflict
    assert ("35", "later_appearance_of_the_same_trial") in {
        (u.submission_no, u.reason) for u in report.unlinked}


def test_d16_runs_across_applications_of_the_same_moiety(
    workspace: tuple[Path, Path],
) -> None:
    """D37: a new formulation repeating the original's trial is a later appearance."""
    out, adj = workspace
    (out / "drug_names.tsv").write_text(
        "application\tbrand_names\tgeneric_name\tactive_ingredients\tmoieties"
        "\tingredient_uniis\tmoiety_uniis\n"
        "BLA125514\tKEYTRUDA\t\tPEMBROLIZUMAB\tPEMBROLIZUMAB\t\t\n"
        "NDA021029\tNEWFORM\t\tPEMBROLIZUMAB\tPEMBROLIZUMAB\t\t\n", encoding="utf-8")
    later = _unit(unit_id="NDA021029|SUPPL|36|KEYNOTE-189", application="NDA021029",
                  submission_no="36", approval_date="2023-09-14")
    _write_units(out, [_unit(), later])
    report = assemble.build(out, adj)
    assert [(l.application, l.submission_no) for l in report.links] == [("BLA125514", "35")]
    assert ("36", "later_appearance_of_the_same_trial") in {
        (u.submission_no, u.reason) for u in report.unlinked}
    # Without the shared moiety each application keeps its own link.
    (out / "drug_names.tsv").unlink()
    assert len(assemble.build(out, adj).links) == 2


def test_a_unit_emptied_by_d41_says_why() -> None:
    from service import assemble as asm
    assert asm._why({"drug_mesh": "MESH:D1", "already_linked": ["NCT1"]}) == "candidates_already_linked"
    assert asm._why({"drug_mesh": "MESH:D1"}) == "no_candidates"
    assert asm._why({}) == "no_drug_resolved"
