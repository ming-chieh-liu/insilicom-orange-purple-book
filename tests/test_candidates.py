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


@pytest.fixture
def term_index() -> indications.TermIndex:
    return indications.TermIndex(
        by_term={
            "small cell lung cancer": "D055752",
            "lung cancer": "D008175",
            "melanoma": "D008545",
        },
        names={
            "D055752": "Small Cell Lung Carcinoma",
            "D008175": "Lung Neoplasms",
            "D008545": "Melanoma",
        },
        max_words=4,
        collisions=0,
    )


def test_an_exact_heading_wins(term_index: indications.TermIndex) -> None:
    """The graph's synonyms are precise where the sweep returns a parent."""
    exact = {"non-small cell lung cancer": "MESH:D002289"}
    assert (
        candidates.code_heading("Non-Small Cell Lung Cancer", exact, term_index)
        == "MESH:D002289"
    )


def test_a_qualified_heading_falls_back_to_the_sweep(
    term_index: indications.TermIndex,
) -> None:
    """Most headings carry qualifiers, so the exact lookup answers only 19%."""
    assert candidates.code_heading("Advanced Melanoma", {}, term_index) == "D008545"


def test_an_ambiguous_heading_codes_to_nothing(
    term_index: indications.TermIndex,
) -> None:
    """Two diseases in one heading is not evidence for either."""
    got = candidates.code_heading("Melanoma and Lung Cancer", {}, term_index)
    assert got == ""


def test_a_heading_naming_no_disease_codes_to_nothing(
    term_index: indications.TermIndex,
) -> None:
    """Some '14.N' lines are trial names, not indications — ARISTOTLE, ATHENA."""
    assert candidates.code_heading("ARISTOTLE", {}, term_index) == ""


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


def test_the_condition_narrows_a_list_too_long_to_judge() -> None:
    """Only when there is something to narrow."""
    studies = {
        f"NCT{n}": _candidate(f"NCT{n}") for n in range(candidates.CANDIDATE_CAP + 10)
    }
    by_drug = {"MESH:C582435": set(studies)}
    by_condition = {"MESH:D002289": {"NCT1", "NCT2"}}
    got = candidates.shortlist(
        {"MESH:C582435"}, "MESH:D002289", "", studies, by_drug, by_condition
    )
    assert {c.nct_id for c in got} == {"NCT1", "NCT2"}


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
    got = candidates.shortlist(
        {"MESH:C582435"}, "", "", studies, by_drug, by_condition
    )
    assert [c.nct_id for c in got] == sorted(c.nct_id for c in got)


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
    out = tmp_path / "output"
    out.mkdir()
    (out / "label_trials.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsection\theading\tnct_id"
        "\tacronym\tenrollment\tdesign\texcerpt\n"
        "BLA125514\tSUPPL\t35\t14.2\tNSCLC\t\tKEYNOTE-189\t616\trandomized\ttext\n"
        "BLA125514\tSUPPL\t36\t14.2\tNSCLC\tNCT99999999\tKEYNOTE-407\t500\t\ttext\n",
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
        lambda archive: iter([_facts("NCT02578680")]),
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
    monkeypatch.setattr(
        candidates.indications,
        "build_index",
        lambda vocabulary: indications.TermIndex({}, {}, 1, 0),
    )

    report = candidates.build(
        object(), object(), object(), out  # type: ignore[arg-type]
    )
    assert report.descriptions == 2
    assert report.collapsed == 2
    assert report.already_identified == 1
    assert report.to_judge == 1
    judged = next(u for u in report.units if u.needs_judgement)
    assert judged.acronym == "KEYNOTE-189"
    assert judged.enrollment == "616"
    assert judged.drug_mesh == "MESH:C582435"
    assert [c.nct_id for c in judged.candidates] == ["NCT02578680"]


def test_a_missing_input_file_yields_no_rows(tmp_path: Path) -> None:
    assert list(candidates._read_tsv(tmp_path / "absent.tsv")) == []
