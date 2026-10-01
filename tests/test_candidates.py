"""Tests for narrowing the registry to a judgeable shortlist."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from service import candidates, indications
from source import trial_studies


def _candidate(nct: str, start: str = "2015-01-01", n: str = "100") -> candidates.Candidate:
    return candidates.Candidate(
        nct_id=nct, brief_title=f"Study {nct}", acronym="", org_study_id="",
        phases="PHASE3", enrollment=n, start_date=start, conditions="",
        interventions="",
    )


# ---------------------------------------------------------------------------
# Collapsing descriptions into judging units
# ---------------------------------------------------------------------------


def _row(**kw: str) -> dict[str, str]:
    base = {
        "application": "BLA125514", "submission_type": "SUPPL",
        "submission_no": "35", "heading": "Non-Small Cell Lung Cancer",
        "nct_id": "", "acronym": "", "enrollment": "", "excerpt": "",
    }
    base.update(kw)
    if base["nct_id"] and base["acronym"] and not base["excerpt"]:
        # A pairing the text itself shows (see candidates.split_pairings).
        base["excerpt"] = f"{base['acronym']} ({base['nct_id']})"
    return base


def test_sentences_about_one_trial_become_one_unit() -> None:
    """A label describes a trial over several sentences — the Keytruda spot
    check yields three rows all naming KEYNOTE-189."""
    rows = [
        _row(acronym="KEYNOTE-189", excerpt="first"),
        _row(acronym="KEYNOTE-189", excerpt="second sentence, longer"),
        _row(acronym="KEYNOTE-189", enrollment="616", excerpt="third"),
    ]
    assert len(candidates.collapse(rows)) == 1


def test_collapsing_keeps_the_stated_enrollment() -> None:
    """Only some sentences state the count, and it is the field that decides."""
    rows = [
        _row(acronym="KEYNOTE-189", excerpt="no count here"),
        _row(acronym="KEYNOTE-189", enrollment="616", excerpt="has the count"),
    ]
    assert candidates.collapse(rows)[0]["enrollment"] == "616"


def test_collapsing_keeps_a_stated_nct_from_any_sentence() -> None:
    rows = [
        _row(acronym="KEYNOTE-189", enrollment="616"),
        _row(acronym="KEYNOTE-189", nct_id="NCT02578680"),
    ]
    assert candidates.collapse(rows)[0]["nct_id"] == "NCT02578680"


def test_two_trials_in_one_subsection_stay_two_units() -> None:
    rows = [_row(acronym="KEYNOTE-189"), _row(acronym="KEYNOTE-407")]
    assert len(candidates.collapse(rows)) == 2


def test_the_same_trial_on_two_submissions_stays_two_units() -> None:
    """A confirmatory study supports a different approval than the accelerated
    one it confirms, even when both cite the same trial."""
    rows = [
        _row(acronym="KEYNOTE-006", submission_no="4"),
        _row(acronym="KEYNOTE-006", submission_no="6"),
    ]
    assert len(candidates.collapse(rows)) == 2


def test_collapsing_nothing_yields_nothing() -> None:
    assert candidates.collapse([]) == []


# ---------------------------------------------------------------------------
# Coding a heading
# ---------------------------------------------------------------------------


def test_the_heading_wins_when_it_names_one_disease() -> None:
    assert candidates.pick_indication(["MESH:D002289"], ["MESH:D008545"]) == "MESH:D002289"


def test_an_ambiguous_heading_codes_to_nothing() -> None:
    """Two diseases in one heading is not evidence for either."""
    assert candidates.pick_indication(["MESH:D008545", "MESH:D008175"], []) == ""


def test_a_heading_naming_no_disease_falls_back_to_the_submission() -> None:
    """Some '14.N' lines are trial names, not indications — ARISTOTLE, ATHENA.
    The submission's chosen indication (letter or label §1) stands in (D30)."""
    assert candidates.pick_indication([], ["MESH:D002289"]) == "MESH:D002289"
    assert candidates.pick_indication([], []) == ""


def test_pick_indications_keeps_every_disease_instead_of_refusing() -> None:
    """D34: a two-disease heading ranks on both; no heading falls back to all
    of the submission's own documents."""
    assert candidates.pick_indications(["MESH:D008545", "MESH:D008175"], ["MESH:D1"]) == (
        "MESH:D008175", "MESH:D008545")
    assert candidates.pick_indications([], ["MESH:D1", "MESH:D2", "MESH:D1"]) == (
        "MESH:D1", "MESH:D2")
    assert candidates.pick_indications([], []) == ()


def test_read_submission_ids_takes_own_documents_not_openfda(tmp_path: Path) -> None:
    (tmp_path / "submission_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext"
        "\tmesh_id\tbiokdeid\tterm\tchosen\n"
        "NDA211651\tSUPPL\t10\tletter\t\tx\tMESH:D011471\tb\tt\ttrue\n"
        "NDA211651\tSUPPL\t10\tlabel_section_1\t\tx\tMESH:D001943\tb\tt\tfalse\n"
        "NDA211651\tSUPPL\t10\tlabel_section_1\t\tx\tMESH:D001943\tb\tt\tfalse\n"
        "NDA211651\tSUPPL\t10\tlabel_14_heading\tH\tH\tMESH:D5\tb\tt\tfalse\n"
        "NDA211651\tSUPPL\t10\topenfda_current\t\t\tMESH:D6\tb\tt\tfalse\n"
        "NDA211651\tSUPPL\t10\tletter\t\tx\t\t\t\tfalse\n",
        encoding="utf-8",
    )
    assert candidates.read_submission_ids(tmp_path) == {
        "NDA211651|SUPPL|10": ["MESH:D011471", "MESH:D001943"]
    }


def test_read_indications_splits_headings_from_chosen(tmp_path: Path) -> None:
    (tmp_path / "submission_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext"
        "\tmesh_id\tbiokdeid\tterm\tchosen\n"
        "BLA125514\tSUPPL\t35\tlabel_14_heading\tNSCLC\tNSCLC\tMESH:D002289\tb\tt\tfalse\n"
        "BLA125514\tSUPPL\t35\tletter\t\tx\tMESH:D002289\tb\tt\ttrue\n"
        "BLA125514\tSUPPL\t35\tlabel_section_1\t\tx\tMESH:D008545\tb\tt\tfalse\n",
        encoding="utf-8",
    )
    headings, chosen = candidates.read_indications(tmp_path)
    assert headings == {("BLA125514|SUPPL|35", "NSCLC"): ["MESH:D002289"]}
    assert chosen == {"BLA125514|SUPPL|35": ["MESH:D002289"]}


# ---------------------------------------------------------------------------
# The shortlist
# ---------------------------------------------------------------------------


class _Registry(NamedTuple):
    """The three structures :func:`shortlist` reads."""

    studies: dict[str, candidates.Candidate]
    by_drug: dict[str, set[str]]
    by_condition: dict[str, set[str]]


@pytest.fixture
def registry() -> _Registry:
    studies = {
        "NCT1": _candidate("NCT1", "2014-01-01"),
        "NCT2": _candidate("NCT2", "2016-01-01"),
        "NCT3": _candidate("NCT3", "2013-01-01"),
        "NCT4": _candidate("NCT4", ""),
    }
    by_drug = {"MESH:C582435": {"NCT1", "NCT2", "NCT3", "NCT4"}}
    by_condition = {"MESH:D002289": {"NCT1", "NCT2"}}
    return _Registry(studies, by_drug, by_condition)


def test_a_trial_cannot_support_an_earlier_approval(registry: _Registry) -> None:
    """The one hard fact in the whole filter chain."""
    studies, by_drug, by_condition = registry
    got = candidates.shortlist(
        {"MESH:C582435"}, "", "2015-06-01", studies, by_drug, by_condition
    )
    assert {c.nct_id for c in got} == {"NCT1", "NCT3", "NCT4"}


def test_a_study_with_no_start_date_is_kept(registry: _Registry) -> None:
    """Dropping it would hide a real answer for a missing field."""
    studies, by_drug, by_condition = registry
    got = candidates.shortlist(
        {"MESH:C582435"}, "", "2015-06-01", studies, by_drug, by_condition
    )
    assert "NCT4" in {c.nct_id for c in got}


def test_the_condition_does_not_narrow_a_judgeable_list(
    registry: _Registry,
) -> None:
    """CHECKMATE-648 is in the registry with the exact enrollment the label
    states, and the condition filter dropped it: the trial registers
    esophageal squamous cell carcinoma where the label heading codes to
    something else. Drug and date are hard facts; the condition is an
    agreement between two vocabularies that often disagree."""
    studies, by_drug, by_condition = registry
    got = candidates.shortlist(
        {"MESH:C582435"}, "MESH:D002289", "", studies, by_drug, by_condition
    )
    assert len(got) == 4


def test_a_long_list_is_ranked_not_cut() -> None:
    """Condition matches sort first, but the list is filled to the cap rather
    than intersected down — intersecting is what lost CHECKMATE-648."""
    studies = {
        f"NCT{n}": _candidate(f"NCT{n}") for n in range(candidates.CANDIDATE_CAP + 10)
    }
    by_drug = {"MESH:C582435": set(studies)}
    by_condition = {"MESH:D002289": {"NCT1", "NCT2"}}
    got = candidates.shortlist(
        {"MESH:C582435"}, "MESH:D002289", "", studies, by_drug, by_condition
    )
    assert len(got) == candidates.CANDIDATE_CAP
    assert {"NCT1", "NCT2"} <= {c.nct_id for c in got}


def test_several_indications_rank_a_match_on_any_of_them_first() -> None:
    """D34: a description about two diseases ranks trials of either first."""
    studies = {f"NCT{n}": _candidate(f"NCT{n}") for n in range(5)}
    by_condition = {"MESH:D1": {"NCT3"}, "MESH:D2": {"NCT4"}}
    got = candidates.shortlist(
        {"MESH:X"}, ("MESH:D1", "MESH:D2"), "", studies,
        {"MESH:X": set(studies)}, by_condition,
    )
    assert [c.nct_id for c in got][:2] == ["NCT3", "NCT4"]


def test_the_stated_count_ranks_a_long_list() -> None:
    """The label says 970 and the registry says 970 for exactly one study, so
    it survives a cut that the condition filter would have thrown it out of.
    Ranking orders the list; it never decides — that is P4's job."""
    studies = {
        f"NCT{n:04d}": _candidate(f"NCT{n:04d}", n="100")
        for n in range(candidates.CANDIDATE_CAP + 50)
    }
    studies["NCT9999"] = _candidate("NCT9999", n="970")
    by_drug = {"MESH:D1": set(studies)}
    got = candidates.shortlist(
        {"MESH:D1"}, "", "", studies, by_drug, {}, "970"
    )
    assert "NCT9999" in {c.nct_id for c in got}


def test_a_list_inside_the_cap_is_never_ranked_away() -> None:
    studies = {f"NCT{n}": _candidate(f"NCT{n}") for n in range(5)}
    got = candidates.shortlist(
        {"MESH:D1"}, "MESH:D002289", "", studies, {"MESH:D1": set(studies)}, {}
    )
    assert len(got) == 5


def test_a_condition_matching_nothing_does_not_empty_the_pool(
    registry: _Registry,
) -> None:
    """A heading coded to a parent concept would otherwise wipe out a shortlist
    the drug filter had got right."""
    studies, by_drug, by_condition = registry
    got = candidates.shortlist(
        {"MESH:C582435"}, "MESH:D999999", "", studies, by_drug, by_condition
    )
    assert len(got) == 4


def test_an_unknown_drug_yields_no_candidates(registry: _Registry) -> None:
    studies, by_drug, by_condition = registry
    assert candidates.shortlist(
        {"MESH:D000000"}, "", "", studies, by_drug, by_condition
    ) == ()


def test_the_shortlist_is_ordered_deterministically(registry: _Registry) -> None:
    studies, by_drug, by_condition = registry
    args = ({"MESH:C582435"}, "", "", studies, by_drug, by_condition)
    assert [c.nct_id for c in candidates.shortlist(*args)] == [
        c.nct_id for c in candidates.shortlist(*args)
    ]


def test_the_shortlist_is_returned_in_rank_order_below_the_cap() -> None:
    """Pools under the cap are ranked too, so tiers of 10 mean something."""
    studies = {
        "NCT1": _candidate("NCT1")._replace(phases="PHASE2"),
        "NCT2": _candidate("NCT2")._replace(phases="PHASE3"),
    }
    by_drug = {"MESH:D1": {"NCT1", "NCT2"}}
    got = candidates.shortlist({"MESH:D1"}, "", "", studies, by_drug, {})
    assert [c.nct_id for c in got] == ["NCT2", "NCT1"]


def test_literature_trials_are_kept_out_of_the_shortlist() -> None:
    """D37: a trial the literature names is judged on the "+" route only."""
    studies = {
        "NCT1": _candidate("NCT1")._replace(phases="PHASE3"),
        "NCT9": _candidate("NCT9")._replace(phases="PHASE2"),
    }
    by_drug = {"MESH:D1": {"NCT1", "NCT9"}}
    got = candidates.shortlist(
        {"MESH:D1"}, "", "", studies, by_drug, {}, from_graph=frozenset({"NCT9"})
    )
    assert [c.nct_id for c in got] == ["NCT1"]


# ---------------------------------------------------------------------------
# Units and the report
# ---------------------------------------------------------------------------


def test_a_unit_stating_its_own_nct_needs_no_judge() -> None:
    """Sending it would spend two agent calls confirming what the label said."""
    unit = candidates.Unit(
        "u", "BLA1", "SUPPL", "1", "2018-01-01", "NSCLC", "MESH:D002289",
        "MESH:C582435", "NCT02578680", "KEYNOTE-189", "616", "x", (),
    )
    assert not unit.needs_judgement


def test_a_unit_without_one_does() -> None:
    unit = candidates.Unit(
        "u", "BLA1", "SUPPL", "1", "2018-01-01", "NSCLC", "MESH:D002289",
        "MESH:C582435", "", "KEYNOTE-189", "616", "x", (_candidate("NCT1"),),
    )
    assert unit.needs_judgement


def test_the_report_counts_only_what_needs_judging() -> None:
    judged = candidates.Unit(
        "a", "B", "S", "1", "", "", "", "", "", "ACR", "", "", ()
    )
    known = candidates.Unit(
        "b", "B", "S", "2", "", "", "", "", "NCT1", "", "", "", ()
    )
    report = candidates.CandidatesReport(
        (judged, known), 10, 2, 1, 0, 0, 0, 30
    )
    assert report.to_judge == 1
    assert "MEDIAN CANDIDATES" in candidates.render(report)


# ---------------------------------------------------------------------------
# Indexing the registry
# ---------------------------------------------------------------------------


def _facts(
    nct: str,
    interventions: tuple[str, ...] = ("Keytruda",),
    conditions: tuple[str, ...] = ("NSCLC",),
    phases: tuple[str, ...] = ("PHASE3",),
) -> trial_studies.StudyFacts:
    return trial_studies.StudyFacts(
        nct_id=nct, brief_title=f"Study {nct}", org_study_id="", acronym="",
        start_date="2015-01-01", phases=phases, enrollment="616",
        interventions=interventions, conditions=conditions,
    )


class _Archive:
    """Stands in for the 25-shard archive."""

    def __init__(self, facts: list[trial_studies.StudyFacts]) -> None:
        self.facts = facts


@pytest.fixture
def patched_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    def _read(archive: object) -> object:
        return iter(archive.facts)  # type: ignore[attr-defined]

    monkeypatch.setattr(candidates.trial_studies, "read_study_facts", _read)


def test_studies_are_indexed_by_both_keys(patched_archive: None) -> None:
    """A dict of postings lists, because 22,666 descriptions against 600,762
    studies is 13 billion comparisons done naively."""
    archive = _Archive([_facts("NCT1"), _facts("NCT2", conditions=("Melanoma",))])
    studies, by_drug, by_condition = candidates.index_studies(
        archive,  # type: ignore[arg-type]
        {"keytruda": "MESH:C582435"},
        {"nsclc": "MESH:D002289", "melanoma": "MESH:D008545"},
    )
    assert len(studies) == 2
    assert by_drug["MESH:C582435"] == {"NCT1", "NCT2"}
    assert by_condition["MESH:D002289"] == {"NCT1"}


def test_an_intervention_the_map_does_not_know_is_skipped(
    patched_archive: None,
) -> None:
    archive = _Archive([_facts("NCT1", interventions=("Unknownium",))])
    _, by_drug, _ = candidates.index_studies(
        archive, {"keytruda": "MESH:C582435"}, {}  # type: ignore[arg-type]
    )
    assert by_drug == {}


def test_the_matching_facts_ride_along_for_the_judge(
    patched_archive: None,
) -> None:
    """Enrollment above all: it is what separates candidates, and a filter that
    dropped it would leave the judge nothing to compare."""
    archive = _Archive([_facts("NCT1")])
    studies, _, _ = candidates.index_studies(
        archive, {"keytruda": "MESH:C582435"}, {}  # type: ignore[arg-type]
    )
    assert studies["NCT1"].enrollment == "616"
    assert studies["NCT1"].phases == "PHASE3"


def test_an_unusable_phase_is_excluded(patched_archive: None) -> None:
    archive = _Archive([_facts("NCT1", phases=("EARLY_PHASE1",))])
    studies, _, _ = candidates.index_studies(
        archive, {"keytruda": "MESH:C582435"}, {}  # type: ignore[arg-type]
    )
    assert studies == {}


# ---------------------------------------------------------------------------
# The whole pass
# ---------------------------------------------------------------------------


def test_the_pass_shortlists_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _run_pass(tmp_path, monkeypatch)
    assert report.descriptions == 2
    assert report.collapsed == 2
    assert report.already_identified == 1
    assert report.to_judge == 1
    judged = next(u for u in report.units if u.needs_judgement)
    assert judged.acronym == "KEYNOTE-189"
    assert judged.enrollment == "616"
    assert judged.drug_mesh == "MESH:C582435"
    assert [c.nct_id for c in judged.candidates] == ["NCT02578680"]


def test_a_name_the_submissions_review_resolved_skips_the_shortlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D29: the review named KEYNOTE-189's trial by protocol id and its registry
    acronym is the label's name, so no judge is asked."""
    report = _run_pass(tmp_path, monkeypatch, mentions=(
        "application\tsubmission_type\tsubmission_no\tapproval_date\tdocument\theading"
        "\tprotocol_id\tnct_id\toutcome\tacronym\n"
        "BLA125514\tSUPPL\t35\t2018-08-20\tSumR\th\tMK-3475-189\tNCT02578680"
        "\taccepted\tKEYNOTE-189\n"
    ))
    assert report.to_judge == 0 and report.review_identified == 1
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert unit.review_nct == "NCT02578680" and not unit.needs_judgement
    # still shortlisted: P6 scores shortlist recall on identified units (D18)
    assert [c.nct_id for c in unit.candidates] == ["NCT02578680"]
    assert "identified by the submission's review" in candidates.render(report)


def _run_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mentions: str = "",
    extra_rows: tuple["candidates.approval_events.EventRow", ...] = (),
    files: dict[str, str] | None = None,
    ancestors: dict[str, frozenset[str]] | None = None,
    facts: tuple[trial_studies.StudyFacts, ...] = (),
    review_trials: tuple[tuple[str, str], ...] = (),
) -> "candidates.CandidatesReport":
    out = tmp_path / "output"
    out.mkdir()
    (out / "label_trials.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsection\theading\tnct_id"
        "\tacronym\tenrollment\tdesign\texcerpt\n"
        "BLA125514\tSUPPL\t35\t14.2\tNSCLC\t\tKEYNOTE-189\t616\trandomized\ttext\n"
        "BLA125514\tSUPPL\t36\t14.2\tNSCLC\tNCT99999999\tKEYNOTE-407\t500\t\t"
        "KEYNOTE-407 (NCT99999999)\n",
        encoding="utf-8",
    )
    for name in ("trial_drug_map", "trial_condition_map"):
        (out / f"{name}.tsv").write_text(
            "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
            "Keytruda\tMESH:C582435\t6308113\t\t\tsynonym\n"
            "NSCLC\tMESH:D002289\t55633\t\t\tsynonym\n",
            encoding="utf-8",
        )
    (out / "drug_map.tsv").write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "PEMBROLIZUMAB\tMESH:C582435\t6308113\tPembrolizumab\tChemical\texact\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        candidates.trial_studies,
        "read_study_facts",
        lambda archive: iter([_facts("NCT02578680"), *facts]),
    )
    monkeypatch.setattr(
        candidates.approval_events,
        "build",
        lambda data: candidates.approval_events.EventsReport(
            rows=(
                candidates.approval_events.EventRow(
                    "BLA125514", "BLA", "SUPPL", "35", "Efficacy",
                    "Efficacy-New Indication", "2018-08-20", "2008+", "P",
                    "false", "true", "true",
                ),
                *extra_rows,
            ),
            submissions_seen=1, approved=1, applications=1,
            indication_changing=1, trial_relevant=1, originals=0,
            core_supplements=1, by_class=(), by_action=(), by_era=(),
            most_supplemented=(),
        ),
    )
    monkeypatch.setattr(
        candidates.normalize,
        "ingredients_of",
        lambda data, apps: {"BLA125514": frozenset({"PEMBROLIZUMAB"})},
    )
    if mentions:
        (out / "review_trial_mentions.tsv").write_text(mentions, encoding="utf-8")
    for name, text in (files or {}).items():
        (out / name).write_text(text, encoding="utf-8")
    return candidates.build(
        object(), object(), out, ancestors=ancestors,  # type: ignore[arg-type]
        review_trials=review_trials,
    )


def _event(number: str, date: str, scope: str = "full") -> "candidates.approval_events.EventRow":
    return candidates.approval_events.EventRow(
        "BLA125514", "BLA", "SUPPL", number, "Efficacy", "Efficacy-New Indication",
        date, "2008+", "P", "false", "true", "true", "EFFICACY", scope)


_INDICATIONS_HEAD = ("application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext"
                     "\tmesh_id\tbiokdeid\tterm\tchosen\n")


def test_submissions_without_their_own_description_become_cases_c_and_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D35: SUPPL 50 has an indication and no label → C, its candidates the
    drug pool; SUPPL 51 has none → D, its candidates the drug-only literature;
    a guard submission is never a case."""
    report = _run_pass(
        tmp_path, monkeypatch,
        extra_rows=(_event("50", "2019-01-01"), _event("51", "2020-01-01"),
                    _event("52", "2020-01-01", "guard")),
        files={
            "submission_indications.tsv": _INDICATIONS_HEAD
            + "BLA125514\tSUPPL\t50\tletter\t\tprovides for NSCLC\tMESH:D002289\tb\tt\ttrue\n",
            "drug_only_trials.tsv": "application\tsubmission_type\tsubmission_no\tnct_id"
            "\tmentions\tdiseases\tprotocol_id\n"
            "BLA125514\tSUPPL\t51\tNCT02578680\t2\tMelanoma\t\n"
            "BLA125514\tSUPPL\t51\tNCT00000404\t1\tMelanoma\t\n",
        },
    )
    routes = dict(report.routes)
    assert (routes["BLA125514|SUPPL|35"], routes["BLA125514|SUPPL|50"],
            routes["BLA125514|SUPPL|51"], routes["BLA125514|SUPPL|52"]) == (
        "label", "C", "D", "guard")
    c = next(u for u in report.units if u.case == "C")
    d = next(u for u in report.units if u.case == "D")
    assert c.unit_id == "BLA125514|SUPPL|50|indication" and c.indication_text == "provides for NSCLC"
    assert [x.nct_id for x in c.candidates] == ["NCT02578680"] and c.drug == "PEMBROLIZUMAB"
    # D37: case D's candidates are the drug-only literature, on the "+" side.
    assert d.candidates == ()
    assert [(x.nct_id, x.found_under) for x in d.literature] == [("NCT02578680", "Melanoma")]
    assert (report.case_c_with_candidates, report.case_d_with_candidates) == (1, 1)
    assert "case D units with candidates                     1" in candidates.render(report)


def test_the_indication_filter_runs_before_the_cap_but_spares_the_literature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trial registered for an unrelated disease leaves the pool; one with no
    condition id stays; the literature's own candidate is never filtered."""
    melanoma = trial_studies.StudyFacts(
        nct_id="NCT00000002", brief_title="t", org_study_id="", acronym="",
        start_date="2015-01-01", phases=("PHASE3",), enrollment="100",
        interventions=("Keytruda",), conditions=("Melanoma",),
        derived_condition_mesh=("MESH:D008545",))
    report = _run_pass(
        tmp_path, monkeypatch,
        facts=(melanoma,),
        ancestors={"MESH:D002289": frozenset(), "MESH:D008545": frozenset()},
        files={"submission_indications.tsv": _INDICATIONS_HEAD
               + "BLA125514\tSUPPL\t35\tlabel_14_heading\tNSCLC\tNSCLC\tMESH:D002289\tb\tt\tfalse\n"},
    )
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert [c.nct_id for c in unit.candidates] == ["NCT02578680"]


def test_a_literature_trial_for_another_disease_reaches_neither_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D37: the literature set takes the indication check too (id-less kept)."""
    melanoma = trial_studies.StudyFacts(
        nct_id="NCT00000002", brief_title="t", org_study_id="", acronym="",
        start_date="2015-01-01", phases=("PHASE3",), enrollment="100",
        interventions=("Other drug",), conditions=("Melanoma",),
        derived_condition_mesh=("MESH:D008545",))
    report = _run_pass(
        tmp_path, monkeypatch, facts=(melanoma,),
        ancestors={"MESH:D002289": frozenset(), "MESH:D008545": frozenset()},
        files={"submission_indications.tsv": _INDICATIONS_HEAD
               + "BLA125514\tSUPPL\t35\tlabel_14_heading\tNSCLC\tNSCLC\tMESH:D002289\tb\tt\tfalse\n",
               "graph_trials.tsv": "application\tnct_id\tmentions\tvia\tpmids\n"
               "BLA125514\tNCT00000002\t1\tprotocol\t7\n"},
    )
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert unit.literature == ()
    assert "NCT00000002" not in {c.nct_id for c in unit.candidates}


def test_a_review_with_a_linking_trial_routes_to_review_not_c_or_d() -> None:
    """D35, 2026-09-30: the FDA review already names the evidence."""
    got = candidates.route([("R|ORIG|1", "full"), ("S|ORIG|1", "full")], set(),
                           {"R|ORIG|1": ["MESH:D1"], "S|ORIG|1": ["MESH:D1"]}, {}, set(),
                           frozenset({"R|ORIG|1"}))
    assert got == [("R|ORIG|1", "review"), ("S|ORIG|1", "C")]
    # owning a §14 description still wins: B is judged regardless of the review
    assert candidates.route([("R|ORIG|1", "full")], {"R|ORIG|1"}, {}, {}, set(),
                            frozenset({"R|ORIG|1"})) == [("R|ORIG|1", "label")]


def test_route_and_ownership_edge_cases() -> None:
    assert candidates.route([("X|ORIG|1", "full")], set(), {}, {}, set()) == [("X|ORIG|1", "D")]
    assert candidates.read_drug_only(Path("/nonexistent")) == {}
    assert candidates.read_chosen_text(Path("/nonexistent")) == {}


_MENTIONS_HEAD = ("application\tsubmission_type\tsubmission_no\tprotocol_id\tnct_id"
                  "\toutcome\tacronym\n")


def test_review_names_keep_only_unambiguous_accepted_names(tmp_path: Path) -> None:
    (tmp_path / "review_trial_mentions.tsv").write_text(_MENTIONS_HEAD + "".join([
        "A\tORIG\t1\tMCP-103-302\tNCT1\taccepted\tTRIUMPH\n",
        "A\tORIG\t1\tXYZ-001-A\tNCT2\taccepted\t\n",
        "A\tORIG\t1\tXYZ-001-A\tNCT3\taccepted\t\n",       # one name, two trials
        "A\tORIG\t1\tABC-9999\tNCT4\tstart_after_approval\t\n",
        "A\tORIG\t1\tSTUDY 12\tNCT5\taccepted\t\n",        # a numbering
        "A\tORIG\t1\tAB1\tNCT6\taccepted\t\n",             # too short
    ]), encoding="utf-8")
    assert candidates.review_names(tmp_path) == {
        "A|ORIG|1": {"mcp103302": "NCT1", "triumph": "NCT1"}}
    assert candidates.review_names(tmp_path / "absent") == {}


def test_a_missing_input_file_yields_no_rows(tmp_path: Path) -> None:
    assert list(candidates._read_tsv(tmp_path / "absent.tsv")) == []


# ---------------------------------------------------------------------------
# Two sources for the trial side, in order
# ---------------------------------------------------------------------------


def _facts_with_derived(
    nct: str,
    interventions: tuple[str, ...] = (),
    conditions: tuple[str, ...] = (),
    drug_mesh: tuple[str, ...] = (),
    condition_mesh: tuple[str, ...] = (),
) -> trial_studies.StudyFacts:
    return trial_studies.StudyFacts(
        nct_id=nct, brief_title="t", org_study_id="", acronym="",
        start_date="2015-01-01", phases=("PHASE3",), enrollment="100",
        interventions=interventions, conditions=conditions,
        derived_drug_mesh=drug_mesh, derived_condition_mesh=condition_mesh,
    )


def test_ctgov_own_mesh_is_used_when_present(patched_archive: None) -> None:
    """NLM curated it; no name matching needed."""
    archive = _Archive(
        [_facts_with_derived("NCT1", drug_mesh=("MESH:D004837",))]
    )
    _, by_drug, _ = candidates.index_studies(archive, {}, {})  # type: ignore[arg-type]
    assert by_drug["MESH:D004837"] == {"NCT1"}


def test_the_synonym_index_covers_studies_with_no_derived_mesh(
    patched_archive: None,
) -> None:
    """46% of studies have derived intervention MeSH, so the fallback carries
    the majority."""
    archive = _Archive([_facts_with_derived("NCT1", interventions=("Keytruda",))])
    _, by_drug, _ = candidates.index_studies(
        archive, {"keytruda": "MESH:C582435"}, {}  # type: ignore[arg-type]
    )
    assert by_drug["MESH:C582435"] == {"NCT1"}


def test_both_sources_apply_to_the_same_study(patched_archive: None) -> None:
    """Neither subsumes the other. CT.gov assigns MeSH; half the FDA side is
    keyed on PubChem and can only be reached through the synonym index. Making
    derived MeSH *replace* the fallback cut absolute recall from 2,604 to
    2,253 while the rate appeared to rise, because the denominator shrank."""
    archive = _Archive(
        [
            _facts_with_derived(
                "NCT1",
                interventions=("Somedrug",),
                drug_mesh=("MESH:D004837",),
            )
        ]
    )
    _, by_drug, _ = candidates.index_studies(
        archive, {"somedrug": "PubChem:CID1"}, {}  # type: ignore[arg-type]
    )
    assert by_drug["MESH:D004837"] == {"NCT1"}
    assert by_drug["PubChem:CID1"] == {"NCT1"}


def test_both_sources_feed_the_same_postings(patched_archive: None) -> None:
    archive = _Archive(
        [
            _facts_with_derived("NCT1", drug_mesh=("MESH:C582435",)),
            _facts_with_derived("NCT2", interventions=("Keytruda",)),
        ]
    )
    _, by_drug, _ = candidates.index_studies(
        archive, {"keytruda": "MESH:C582435"}, {}  # type: ignore[arg-type]
    )
    assert by_drug["MESH:C582435"] == {"NCT1", "NCT2"}


def test_conditions_follow_the_same_rule(patched_archive: None) -> None:
    archive = _Archive(
        [
            _facts_with_derived("NCT1", condition_mesh=("MESH:D002386",)),
            _facts_with_derived("NCT2", conditions=("NSCLC",)),
        ]
    )
    _, _, by_condition = candidates.index_studies(
        archive, {}, {"nsclc": "MESH:D002289"}  # type: ignore[arg-type]
    )
    assert by_condition["MESH:D002386"] == {"NCT1"}
    assert by_condition["MESH:D002289"] == {"NCT2"}


# ---------------------------------------------------------------------------
# Biosimilars: the reference product's pool, ordered so the answer survives
# ---------------------------------------------------------------------------


def test_a_suffixed_intervention_marks_a_study_as_biosimilar() -> None:
    assert candidates.looks_biosimilar("A Study of ABP 501", ["Adalimumab-atto"])


def test_the_word_biosimilar_in_a_title_is_enough() -> None:
    """86 of the 302 biosimilar studies carry no derived MeSH at all, so the
    title has to be readable on its own."""
    assert candidates.looks_biosimilar("Biosimilar Trastuzumab in HER2+", [])


def test_an_ordinary_trial_is_not_flagged() -> None:
    assert not candidates.looks_biosimilar(
        "KEYNOTE-189", ["Pembrolizumab", "Carboplatin"]
    )


def _bio(nct: str, *, biosimilar: bool, n: str = "100") -> candidates.Candidate:
    return candidates.Candidate(
        nct_id=nct, brief_title=f"Study {nct}", acronym="", org_study_id="",
        phases="PHASE3", enrollment=n, start_date="2015-01-01", conditions="",
        interventions="", biosimilar=biosimilar,
    )


def _big_pool(biosimilar_at: set[str]) -> dict[str, candidates.Candidate]:
    """More candidates than the cap, so the ranking actually runs."""
    return {
        f"NCT{i:08d}": _bio(f"NCT{i:08d}", biosimilar=f"NCT{i:08d}" in biosimilar_at)
        for i in range(candidates.CANDIDATE_CAP * 3)
    }


def test_biosimilar_studies_are_ranked_first_for_a_stem_sourced_drug() -> None:
    """The measured shape: a biosimilar approval retrieves a median of 318
    candidates on its reference product's id, of which ~6 are its own. Without
    this key those 6 fall outside a 40-slot shortlist and no judge can find
    them."""
    needle = "NCT00000119"
    studies = _big_pool({needle})
    got = candidates.shortlist(
        {"MESH:D000068879"},
        "",
        "2020-01-01",
        studies,
        {"MESH:D000068879": set(studies)},
        {},
        prefer_biosimilar=True,
    )
    assert needle in {c.nct_id for c in got}


def test_without_a_stem_sourced_drug_the_flag_does_not_reorder() -> None:
    """The reference product's own approvals must keep the old ranking — its
    pivotal trials are not biosimilar studies."""
    needle = "NCT00000119"
    studies = _big_pool({needle})
    got = candidates.shortlist(
        {"MESH:D000068879"},
        "",
        "2020-01-01",
        studies,
        {"MESH:D000068879": set(studies)},
        {},
    )
    assert needle not in {c.nct_id for c in got}


def test_the_biosimilar_key_only_ranks_and_never_filters() -> None:
    """Ranking cannot lose an answer the way filtering can — a pool under the
    cap comes back whole however it is flagged."""
    studies = {
        "NCT00000001": _bio("NCT00000001", biosimilar=False),
        "NCT00000002": _bio("NCT00000002", biosimilar=True),
    }
    got = candidates.shortlist(
        {"MESH:D000068879"},
        "",
        "2020-01-01",
        studies,
        {"MESH:D000068879": set(studies)},
        {},
        prefer_biosimilar=True,
    )
    assert len(got) == 2


# ---------------------------------------------------------------------------
# A stated registry id is ground truth and is never merged away
# ---------------------------------------------------------------------------


def _desc(acronym: str, nct: str = "", enrollment: str = "", excerpt: str = ""
          ) -> dict[str, str]:
    excerpt = excerpt or (f"{acronym} ({nct})" if acronym and nct else "x")
    return {
        "application": "BLA125514", "submission_type": "SUPPL",
        "submission_no": "16", "section": "14.1", "heading": "Melanoma",
        "nct_id": nct, "acronym": acronym, "enrollment": enrollment,
        "design": "", "excerpt": excerpt,
    }


def test_two_trials_under_one_acronym_stay_two_units() -> None:
    """The melanoma subsection names KEYNOTE-006 beside both NCT01866319
    (KEYNOTE-006) and NCT01704287 (KEYNOTE-002); the extractor tags each id
    with the nearest acronym. Grouping on the acronym discarded one of them —
    382 label-stated ids across 381 groups, silently, because the surviving
    unit looked served."""
    out = candidates.collapse([
        _desc("KEYNOTE-006", "NCT01866319"),
        _desc("KEYNOTE-006", "NCT01704287"),
    ])
    assert {row["nct_id"] for row in out} == {"NCT01866319", "NCT01704287"}


def test_one_trial_over_several_sentences_is_still_one_unit() -> None:
    """The reason the acronym was the key: a label names a trial in every
    sentence but gives the id in at most one. Splitting on the id would send a
    judge to identify a trial the label had already identified."""
    out = candidates.collapse([
        _desc("KEYNOTE-189", "NCT02578680"),
        _desc("KEYNOTE-189", ""),
        _desc("KEYNOTE-189", "", enrollment="616"),
    ])
    assert len(out) == 1
    assert out[0]["nct_id"] == "NCT02578680"
    assert out[0]["enrollment"] == "616"


def test_an_id_less_sentence_beside_two_ids_becomes_its_own_unit() -> None:
    """It cannot be assigned to either trial, so a judge decides. P5's
    (trial, submission) grain guard absorbs the answer if the judge picks one
    that is already linked."""
    out = candidates.collapse([
        _desc("KEYNOTE-006", "NCT01866319"),
        _desc("KEYNOTE-006", "NCT01704287"),
        _desc("KEYNOTE-006", ""),
    ])
    assert len(out) == 3
    assert sum(1 for row in out if not row["nct_id"]) == 1


def test_split_units_do_not_share_a_trial_key() -> None:
    """unit_id is built from the trial key, and two units sharing one would
    collide in the verdict file — a bug this project has already had."""
    out = candidates.collapse([
        _desc("KEYNOTE-006", "NCT01866319"),
        _desc("KEYNOTE-006", "NCT01704287"),
    ])
    keys = [row[candidates.TRIAL_KEY] for row in out]
    assert len(set(keys)) == len(keys)


def test_no_stated_id_is_ever_dropped() -> None:
    """The property, stated directly: every id P2 extracted survives collapse."""
    rows = [
        _desc("KEYNOTE-006", "NCT01866319"),
        _desc("KEYNOTE-006", "NCT01704287"),
        _desc("KEYNOTE-024", "NCT02039674"),
        _desc("KEYNOTE-024", "NCT02142738"),
        _desc("KEYNOTE-189", "NCT02578680"),
    ]
    out = candidates.collapse(rows)
    assert {row["nct_id"] for row in rows if row["nct_id"]} <= {
        row["nct_id"] for row in out
    }


# ---------------------------------------------------------------------------
# Ranking: the label's trial name is the strongest signal, and it was unused
# ---------------------------------------------------------------------------


def _named(nct: str, acronym: str = "", org: str = "", title: str = "",
           phases: str = "PHASE3", n: str = "100",
           start: str = "2015-01-01") -> candidates.Candidate:
    return candidates.Candidate(
        nct_id=nct, brief_title=title, acronym=acronym, org_study_id=org,
        phases=phases, enrollment=n, start_date=start, conditions="",
        interventions="",
    )


def test_a_label_name_matches_the_registry_acronym() -> None:
    assert candidates.names_match("KEYNOTE-189", _named("NCT1", acronym="KEYNOTE-189"))


def test_punctuation_and_case_do_not_block_the_match() -> None:
    """A label writes KEYNOTE-189 where the registry writes 'Keynote 189'."""
    assert candidates.names_match("KEYNOTE-189", _named("NCT1", acronym="Keynote 189"))


def test_the_sponsor_protocol_id_also_counts() -> None:
    """Registry records carry the name in whichever field the sponsor used."""
    assert candidates.names_match("3475-189", _named("NCT1", org="3475-189"))


def test_a_degenerate_name_cannot_match() -> None:
    """The floor only guards the pathological case. Sweeping it from 4 to 8
    against the 6,368 known answers moved recall by at most one unit (70.2% at
    every value), so it is not load-bearing: a false acronym like ``IIIC 1``
    does match, and that is tolerable because the name key only *promotes* one
    study into a 40-slot list — it cannot evict the right answer on its own."""
    assert not candidates.names_match("A2", _named("NCT1", org="A2"))


def test_a_false_acronym_may_match_and_that_is_survivable() -> None:
    """Documented rather than prevented, for the reason above."""
    assert candidates.names_match("IIIC 1", _named("NCT1", org="IIIC 1 study"))


def test_an_unrelated_name_does_not_match() -> None:
    assert not candidates.names_match("KEYNOTE-189", _named("NCT1", acronym="CHECKMATE-648"))


def _pool() -> dict[str, candidates.Candidate]:
    """A pool comfortably over the cap, sized from the constant rather than a
    literal so the test still exercises ranking when the cap moves."""
    studies = {
        f"NCT{i:08d}": _named(f"NCT{i:08d}", n="100")
        for i in range(candidates.CANDIDATE_CAP + 20)
    }
    studies["NCT09999999"] = _named("NCT09999999", acronym="KEYNOTE-189", n="616")
    return studies


def test_the_named_trial_is_ranked_into_the_shortlist() -> None:
    """The measured failure: over 1,489 units whose answer was in the pool but
    below the cap, the label's name identified it uniquely 40% of the time and
    nothing compared them."""
    studies = _pool()
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies,
        {"MESH:D1": set(studies)}, {}, "616", label_name="KEYNOTE-189",
    )
    assert "NCT09999999" in {c.nct_id for c in got}


def test_without_the_name_the_same_pool_loses_it() -> None:
    """Guards the claim: it is the name key doing the work, not the count."""
    studies = _pool()
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies,
        {"MESH:D1": set(studies)}, {}, "", label_name="",
    )
    assert "NCT09999999" not in {c.nct_id for c in got}


def test_phase_three_outranks_an_earlier_phase() -> None:
    """70% of true answers are phase 3."""
    studies = {
        f"NCT{i:08d}": _named(f"NCT{i:08d}", phases="PHASE1")
        for i in range(candidates.CANDIDATE_CAP + 20)
    }
    studies["NCT09999999"] = _named("NCT09999999", phases="PHASE3")
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies, {"MESH:D1": set(studies)}, {},
    )
    assert "NCT09999999" in {c.nct_id for c in got}


def test_a_trial_nearer_the_approval_outranks_an_older_one() -> None:
    studies = {
        f"NCT{i:08d}": _named(f"NCT{i:08d}", start="1998-01-01")
        for i in range(candidates.CANDIDATE_CAP + 20)
    }
    studies["NCT09999999"] = _named("NCT09999999", start="2019-01-01")
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies, {"MESH:D1": set(studies)}, {},
    )
    assert "NCT09999999" in {c.nct_id for c in got}


def test_years_before_handles_a_missing_date() -> None:
    assert candidates._years_before("", "2018-08-20") == 999


# ---------------------------------------------------------------------------
# The literature as a ranking key
# ---------------------------------------------------------------------------


def test_a_literature_trial_carries_its_pmids_and_drug_check() -> None:
    studies = {"NCT00000001": _named("NCT00000001")}
    got = candidates.literature_set(
        [("NCT00000001", "5;6"), ("NCT00000001", "9"), ("NCT00000404", "1")],
        "2020-01-01", studies, lambda c: "unresolved")
    assert [(c.nct_id, c.pmids, c.drug_check) for c in got] == [
        ("NCT00000001", "5;6", "unresolved")]


def test_without_the_graph_the_same_pool_loses_it() -> None:
    """Guards the claim that it is the graph key doing the work."""
    studies = {
        f"NCT{i:08d}": _named(f"NCT{i:08d}")
        for i in range(candidates.CANDIDATE_CAP + 20)
    }
    studies["NCT09999999"] = _named("NCT09999999")
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies, {"MESH:D1": set(studies)}, {},
    )
    assert "NCT09999999" not in {c.nct_id for c in got}


def test_the_label_name_still_outranks_the_graph() -> None:
    """Order matters: the label naming a trial is direct evidence, while the
    graph only says the literature associates them."""
    studies = {
        f"NCT{i:08d}": _named(f"NCT{i:08d}")
        for i in range(candidates.CANDIDATE_CAP + 20)
    }
    studies["NCT00000001"] = _named("NCT00000001", acronym="KEYNOTE-189")
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", studies, {"MESH:D1": set(studies)}, {},
        label_name="KEYNOTE-189", from_graph=frozenset({"NCT00000002"}),
    )
    assert got and "NCT00000001" in {c.nct_id for c in got}


def test_graph_supported_reads_the_precompute(tmp_path: Path) -> None:
    (tmp_path / "graph_trials.tsv").write_text(
        "application\tnct_id\tmentions\n"
        "BLA125514\tNCT02578680\t4\n"
        "BLA125514\tNCT01866319\t2\n"
        "NDA021029\tNCT00006353\t1\n",
        encoding="utf-8",
    )
    got = candidates.graph_supported(tmp_path)
    assert got["BLA125514"] == frozenset({"NCT02578680", "NCT01866319"})


def test_a_missing_precompute_is_silent(tmp_path: Path) -> None:
    """P3 must run unchanged where the Mongo pass has not been done."""
    assert candidates.graph_supported(tmp_path) == {}


# ---------------------------------------------------------------------------
# Injection: the only route to a drug the trial index cannot be joined to
# ---------------------------------------------------------------------------


def test_the_literature_reaches_a_drug_the_registry_cannot_join() -> None:
    """53% of the drug map is keyed on PubChem while the trial index is keyed
    on MeSH, so those drugs retrieve nothing from the registry: the literature
    set is their only route (337 of 339 such units, measured)."""
    studies = {"NCT00000001": _named("NCT00000001")}
    assert candidates.shortlist({"PubChem:CID1"}, "", "2020-01-01", studies, {}, {}) == ()
    got = candidates.literature_set([("NCT00000001", "")], "2020-01-01", studies,
                                    lambda c: "unresolved")
    assert [c.nct_id for c in got] == ["NCT00000001"]


def test_another_drugs_trial_is_dropped_from_the_literature() -> None:
    studies = {"NCT00000001": _named("NCT00000001")}
    assert candidates.literature_set([("NCT00000001", "")], "2020-01-01", studies,
                                     lambda c: "other") == ()


def test_a_literature_trial_started_after_the_approval_is_dropped() -> None:
    studies = {"NCT00000001": _named("NCT00000001", start="2024-01-01")}
    assert candidates.literature_set([("NCT00000001", "")], "2020-01-01", studies,
                                     lambda c: "same") == ()


def test_the_graph_cannot_inject_a_trial_the_archive_lacks() -> None:
    """An id the registry does not carry cannot be shown to a judge."""
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2020-01-01", {}, {}, {},
        from_graph=frozenset({"NCT09999999"}),
    )
    assert got == ()


def test_an_injected_trial_still_respects_the_date_bound() -> None:
    """A trial cannot have supported an approval granted before it began — the
    one hard fact injection must not bypass."""
    studies = {"NCT00000001": _named("NCT00000001", start="2024-01-01")}
    got = candidates.shortlist(
        {"PubChem:CID1"}, "", "2020-01-01", studies, {}, {},
        from_graph=frozenset({"NCT00000001"}),
    )
    assert got == ()


def test_an_id_written_beside_another_trial_is_not_given_the_first_name() -> None:
    """The heading run into the sentence: KEYNOTE-204 came first, but the id
    belongs to KEYNOTE-087, written right before it. The id keeps the name
    beside it; KEYNOTE-204 keeps its own unit, without an id, for a judge."""
    text = ("Progression-Free Survival in KEYNOTE-204 KEYNOTE-087 The efficacy of "
            "KEYTRUDA was investigated in KEYNOTE-087 (NCT02453594), a multicenter trial")
    out = candidates.collapse([_desc("KEYNOTE-204", "NCT02453594", excerpt=text)])
    by_name = {row["acronym"]: row["nct_id"] for row in out}
    assert by_name == {"KEYNOTE-087": "NCT02453594", "KEYNOTE-204": ""}


def test_an_id_the_excerpt_does_not_show_is_not_paired_with_its_name() -> None:
    """Unverifiable (the sentence was cut at 600 characters): the id still
    stands, keyed on itself; the name goes to a judge."""
    out = candidates.collapse([_desc("KEYNOTE-045", "NCT03924895", excerpt="Table 78 ...")])
    assert {(row["acronym"], row["nct_id"]) for row in out} == {
        ("", "NCT03924895"), ("KEYNOTE-045", "")}


def test_the_shortlist_carries_sponsor_and_primary_completion() -> None:
    """The per-candidate judge (D33) is shown both."""
    study = _candidate("NCT1")._replace(
        lead_sponsor="Merck Sharp & Dohme LLC", primary_completion="2017-11-08"
    )
    got = candidates.shortlist(
        {"MESH:D1"}, "", "", {"NCT1": study}, {"MESH:D1": {"NCT1"}}, {}
    )
    assert got[0].lead_sponsor == "Merck Sharp & Dohme LLC"
    assert got[0].primary_completion == "2017-11-08"


def test_todays_label_text_is_marked_for_the_judge(tmp_path: Path) -> None:
    """2026-09-30: a case-C judge must know the text is not this submission's own."""
    (tmp_path / "submission_indications.tsv").write_text(
        _INDICATIONS_HEAD
        + "NDA1\tORIG\t1\topenfda_current\t\tFor asthma.\tMESH:D001249\tb\tAsthma\ttrue\n"
        + "NDA2\tORIG\t1\tletter\t\tprovides for asthma\tMESH:D001249\tb\tAsthma\ttrue\n",
        encoding="utf-8",
    )
    got = candidates.read_chosen_text(tmp_path)
    assert got["NDA1|ORIG|1"] == candidates.CURRENT_LABEL_NOTE + "For asthma."
    assert got["NDA2|ORIG|1"] == "provides for asthma"


def test_a_literature_trial_of_the_drug_is_on_the_plus_route_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D37: KEYNOTE-189 named by the literature is judged on B+, not again on B−."""
    report = _run_pass(
        tmp_path, monkeypatch,
        files={"graph_trials.tsv": "application\tnct_id\tmentions\tvia\tpmids\n"
               "BLA125514\tNCT02578680\t3\tnct\t41;42\n"},
    )
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert [(c.nct_id, c.pmids, c.drug_check) for c in unit.literature] == [
        ("NCT02578680", "41;42", "same")]
    assert "NCT02578680" not in {c.nct_id for c in unit.candidates}


def test_name_words_and_generic_arms() -> None:
    assert candidates.name_words(["DESCOVY", "TENOFOVIR ALAFENAMIDE FUMARATE"]) == frozenset(
        {"descovy", "tenofovir", "alafenamide"})
    c = candidates.Candidate("NCT1", "AMPLIFY", "", "", "", "", "", "",
                             "Acalabrutinib;Chemoimmunotherapy;Placebo")
    got = candidates.drug_check(c, frozenset({"MESH:A"}), frozenset({"MESH:Z"}),
                                frozenset({"zanubrutinib"}), frozenset({"MESH:A", "MESH:Z"}),
                                {"acalabrutinib": "MESH:A"})
    assert got == "other"   # the generic arm no longer leaves it unresolved


# ---------------------------------------------------------------------------
# D39: phase rule, sponsor rank, UNII bridge, normalised codes
# ---------------------------------------------------------------------------


def test_phase_one_is_kept_only_when_large() -> None:
    """Option (c): phase 1 alone needs >=100 enrolled; NA and blank never pass."""
    assert candidates.phase_kept(["PHASE1"], "100")
    assert not candidates.phase_kept(["PHASE1"], "99")
    assert not candidates.phase_kept(["PHASE1"], "")
    assert candidates.phase_kept(["PHASE1", "PHASE2"], "12")
    assert candidates.phase_kept(["PHASE4"], "")
    assert not candidates.phase_kept(["NA"], "5000")
    assert not candidates.phase_kept([], "5000")


def test_the_applicants_own_trial_ranks_after_the_start_date() -> None:
    """Sponsor is a key after the start date and before the count, never a filter."""
    def own(nct: str, start: str, sponsor: str) -> candidates.Candidate:
        return _candidate(nct, start=start)._replace(lead_sponsor=sponsor)

    studies = {
        "NCT1": own("NCT1", "2016-01-01", "Other Co"),
        "NCT2": own("NCT2", "2016-01-01", "Hoffmann-La Roche"),
        "NCT3": own("NCT3", "2012-01-01", "Genentech, Inc."),
    }
    got = candidates.shortlist(
        {"MESH:D1"}, "", "2017-06-01", studies, {"MESH:D1": set(studies)}, {},
        applicant="GENENTECH INC",
    )
    assert [c.nct_id for c in got] == ["NCT2", "NCT1", "NCT3"]


def test_index_carries_normalised_codes_beside_the_raw_text(patched_archive: None) -> None:
    archive = _Archive([_facts("NCT1")])
    studies, _, _ = candidates.index_studies(
        archive,  # type: ignore[arg-type]
        {"keytruda": "MESH:C582435"}, {"nsclc": "MESH:D002289"},
        {"MESH:C582435": "pembrolizumab", "MESH:D002289": "Carcinoma, Non-Small-Cell Lung"},
    )
    assert studies["NCT1"].intervention_codes == "pembrolizumab [MeSH C582435]"
    assert studies["NCT1"].condition_codes == "Carcinoma, Non-Small-Cell Lung [MeSH D002289]"


def test_a_pubchem_keyed_drug_reaches_mesh_coded_trials_through_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The FDA side resolved to PubChem, the trial to MeSH; the exact UNII bridge joins them."""
    report = _run_pass(tmp_path, monkeypatch, files={
        "drug_map.tsv": "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "PEMBROLIZUMAB\tPubChem:CID1\t1\tPembrolizumab\tChemical\texact\n",
        "id_bridge.tsv": "unii\tpubchem\tmesh\nDPT0O3T46P\tPubChem:CID1\tMESH:C582435\n",
    })
    judged = next(u for u in report.units if u.needs_judgement)
    assert [c.nct_id for c in judged.candidates] == ["NCT02578680"]
    assert judged.drug_codes == "[MeSH C582435]; PubChem:CID1"


def test_without_the_bridge_the_pubchem_drug_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _run_pass(tmp_path, monkeypatch, files={
        "drug_map.tsv": "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "PEMBROLIZUMAB\tPubChem:CID1\t1\tPembrolizumab\tChemical\texact\n",
    })
    judged = next(u for u in report.units if u.needs_judgement)
    assert judged.candidates == ()


# ---------------------------------------------------------------------------
# D41: candidates an FDA document already names are not judged again
# ---------------------------------------------------------------------------


def test_a_trial_the_review_already_names_is_not_judged_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review names KEYNOTE-189 PIVOTAL on SUPPL 35, so its §14 unit has
    nothing left to judge and is recorded as such."""
    report = _run_pass(tmp_path, monkeypatch,
                       review_trials=(("BLA125514|SUPPL|35", "NCT02578680"),))
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert unit.candidates == () and unit.already_linked == ("NCT02578680",)
    assert (report.already_linked_removed, report.emptied_by_already_linked) == (1, 1)
    assert "units left with no candidate" in candidates.render(report)


def test_a_naming_on_a_later_submission_keeps_the_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Yes on the earlier submission could move the attribution; keep it."""
    report = _run_pass(tmp_path, monkeypatch,
                       review_trials=(("BLA125514|SUPPL|99", "NCT02578680"),))
    unit = next(u for u in report.units if u.acronym == "KEYNOTE-189")
    assert [c.nct_id for c in unit.candidates] == ["NCT02578680"]
    assert report.already_linked_removed == 0


def test_only_the_named_trial_leaves_a_unit() -> None:
    def unit(*ncts: str) -> candidates.Unit:
        return candidates.Unit("A|SUPPL|5|X", "A", "SUPPL", "5", "2015-01-01", "", "", "",
                               "", "", "", "", tuple(_candidate(n) for n in ncts),
                               literature=(_candidate("NCT9"),))
    named = {"NCT1": [candidates.Identified("A", "2015-01-01", "A|SUPPL|5")],
             "NCT9": [candidates.Identified("A", "2012-01-01", "A|ORIG|1")]}
    [kept], removed, emptied = candidates.drop_already_linked([unit("NCT1", "NCT2")], named, {})
    assert [c.nct_id for c in kept.candidates] == ["NCT2"] and kept.literature == ()
    assert kept.already_linked == ("NCT9", "NCT1") and (removed, emptied) == (2, 0)
    stated = unit("NCT1")._replace(stated_nct="NCT1")
    assert candidates.drop_already_linked([stated], named, {})[0] == [stated]
    same_moiety = {"A": "m", "B": "m"}
    other = {"NCT2": [candidates.Identified("m", "2010-01-01", "B|ORIG|1")]}
    [moved], removed, emptied = candidates.drop_already_linked([unit("NCT2")], other, same_moiety)
    assert moved.candidates == () and (removed, emptied) == (1, 0)  # NCT9 literature stays
