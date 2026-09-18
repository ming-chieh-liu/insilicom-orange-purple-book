"""Tests for the literature precompute that ranks P3's candidates."""

from __future__ import annotations

from typing import cast

from pathlib import Path

from dao.graph_evidence import GraphEvidenceRepository, Mention
from service import graph_trials


class _FakeGraph:
    """A graph with the shapes that matter, and a record of what was asked."""

    def __init__(
        self,
        pmids: dict[tuple[str, str], list[str]] | None = None,
        mentions: dict[str, list[Mention]] | None = None,
    ) -> None:
        self._pmids = pmids or {}
        self._mentions = mentions or {}
        self.pairs_asked: list[tuple[str, str]] = []
        self.pmids_asked: list[str] = []

    def pmids_for(self, drug: str, disease: str) -> list[str]:
        self.pairs_asked.append((drug, disease))
        return self._pmids.get((drug, disease), [])

    def mentions_in(self, pmids: list[str]) -> list[Mention]:
        self.pmids_asked = list(pmids)
        out: list[Mention] = []
        for pmid in pmids:
            out.extend(self._mentions.get(pmid, []))
        return out


def _workspace(tmp_path: Path, indications: str = "BLA1\tD002289\n") -> Path:
    out = tmp_path / "output"
    out.mkdir()
    (out / "drug_map.tsv").write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "PEMBROLIZUMAB\tMESH:C582435\tbk-drug\tPembrolizumab\tChemical\texact\n",
        encoding="utf-8",
    )
    (out / "indication_map.tsv").write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        "Lung Neoplasms\tMESH:D002289\tbk-dis\tLung Neoplasms\tDisease\texact\n",
        encoding="utf-8",
    )
    (out / "indications.tsv").write_text(
        "application\tmesh_id\n" + indications, encoding="utf-8"
    )
    return out


def _mention(nct: str, pmid: str) -> Mention:
    return Mention(nct_id=nct, pmid=pmid, title="t", publication_types=())


def test_a_trial_the_literature_names_is_emitted(tmp_path: Path) -> None:
    out = _workspace(tmp_path)
    graph = _FakeGraph(
        {("bk-drug", "bk-dis"): ["111"]},
        {"111": [_mention("NCT02578680", "111")]},
    )
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, graph), frozenset({"BLA1"}), {"BLA1": frozenset({"PEMBROLIZUMAB"})}
    )
    assert [(r.application, r.nct_id) for r in report.rows] == [
        ("BLA1", "NCT02578680")
    ]


def test_mentions_are_counted_across_abstracts(tmp_path: Path) -> None:
    """The tie-break value: better attested, not thereby pivotal."""
    out = _workspace(tmp_path)
    graph = _FakeGraph(
        {("bk-drug", "bk-dis"): ["111", "222"]},
        {
            "111": [_mention("NCT1", "111")],
            "222": [_mention("NCT1", "222")],
        },
    )
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, graph), frozenset({"BLA1"}), {"BLA1": frozenset({"PEMBROLIZUMAB"})}
    )
    assert report.rows[0].mentions == 2


def test_an_edge_naming_no_trial_counts_as_an_edge_not_a_yield(
    tmp_path: Path,
) -> None:
    """These are different failures and conflating them overstates the yield —
    the same wrong-denominator error made twice elsewhere in this project."""
    out = _workspace(tmp_path)
    graph = _FakeGraph({("bk-drug", "bk-dis"): ["111"]}, {"111": []})
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, graph), frozenset({"BLA1"}), {"BLA1": frozenset({"PEMBROLIZUMAB"})}
    )
    assert (report.with_edge, report.with_trials) == (1, 0)
    assert report.rows == ()


def test_an_application_with_no_drug_id_is_not_counted_as_having_one(
    tmp_path: Path,
) -> None:
    out = _workspace(tmp_path)
    graph = _FakeGraph()
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, graph), frozenset({"BLA1"}), {"BLA1": frozenset({"UNKNOWN DRUG"})}
    )
    assert (report.with_drug, report.with_disease, report.with_edge) == (0, 1, 0)
    assert graph.pairs_asked == []


def test_an_application_with_no_disease_id_is_skipped(tmp_path: Path) -> None:
    """Disease coverage is the known bottleneck on this route."""
    out = _workspace(tmp_path, indications="")
    graph = _FakeGraph()
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, graph), frozenset({"BLA1"}), {"BLA1": frozenset({"PEMBROLIZUMAB"})}
    )
    assert (report.with_drug, report.with_disease) == (1, 0)
    assert graph.pairs_asked == []


def test_the_drug_and_disease_crosses_are_capped(tmp_path: Path) -> None:
    """A cost guard: eight ingredients by twenty indications is 160 round
    trips to add ids the first few already found."""
    out = tmp_path / "output"
    out.mkdir()
    (out / "drug_map.tsv").write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        + "".join(
            f"DRUG{i}\tMESH:C{i}\tbk-d{i}\tDrug{i}\tChemical\texact\n" for i in range(8)
        ),
        encoding="utf-8",
    )
    (out / "indication_map.tsv").write_text(
        "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
        + "".join(
            f"Dis{i}\tMESH:D{i:04d}\tbk-s{i}\tDis{i}\tDisease\texact\n"
            for i in range(20)
        ),
        encoding="utf-8",
    )
    (out / "indications.tsv").write_text(
        "application\tmesh_id\n"
        + "".join(f"BLA1\tD{i:04d}\n" for i in range(20)),
        encoding="utf-8",
    )
    graph = _FakeGraph()
    graph_trials.build(
        out,
        cast(GraphEvidenceRepository, graph),
        frozenset({"BLA1"}),
        {"BLA1": frozenset({f"DRUG{i}" for i in range(8)})},
    )
    assert len(graph.pairs_asked) == graph_trials.MAX_DRUGS * graph_trials.MAX_DISEASES


def test_the_abstract_budget_caps_what_is_read(tmp_path: Path) -> None:
    out = _workspace(tmp_path)
    graph = _FakeGraph({("bk-drug", "bk-dis"): [str(i) for i in range(500)]})
    graph_trials.build(
        out,
        cast(GraphEvidenceRepository, graph),
        frozenset({"BLA1"}),
        {"BLA1": frozenset({"PEMBROLIZUMAB"})},
        budget=10,
    )
    assert len(graph.pmids_asked) == 10


def test_missing_inputs_yield_nothing_rather_than_raising(tmp_path: Path) -> None:
    """Every other reader in the project is silent on an absent file."""
    out = tmp_path / "output"
    out.mkdir()
    report = graph_trials.build(
        out, cast(GraphEvidenceRepository, _FakeGraph()), frozenset({"BLA1"}), {}
    )
    assert report.rows == ()
    assert report.applications == 1


def test_the_yield_rate_of_no_applications_is_zero() -> None:
    assert graph_trials.GraphTrialsReport((), 0, 0, 0, 0, 0).yield_rate == 0.0


def test_the_report_renders(tmp_path: Path) -> None:
    text = graph_trials.render(graph_trials.GraphTrialsReport((), 100, 95, 60, 55, 25))
    assert "YIELDING TRIALS" in text
    assert "25.0%" in text
