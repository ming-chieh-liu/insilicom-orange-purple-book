"""Tests for tier C: literature candidates where the label serves nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dao import graph_evidence
from service import graph_links


def _mention(nct: str, pmid: str, kinds: tuple[str, ...] = ()) -> graph_evidence.Mention:
    return graph_evidence.Mention(
        nct_id=nct, pmid=pmid, title=f"Paper {pmid}", publication_types=kinds
    )


# ---------------------------------------------------------------------------
# Reading the edge
# ---------------------------------------------------------------------------


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, Any]] = []

    def find(
        self, query: dict[str, Any], _projection: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append(query)

        def matches(row: dict[str, Any], clause: dict[str, Any]) -> bool:
            return all(
                row.get(k) in v["$in"] if isinstance(v, dict) and "$in" in v
                else row.get(k) == v
                for k, v in clause.items()
            )

        plain = {k: v for k, v in query.items() if k != "$or"}
        return [
            r for r in self.rows
            if matches(r, plain)
            and ("$or" not in query or any(matches(r, c) for c in query["$or"]))
        ]

    def find_one(
        self, query: dict[str, Any], _projection: dict[str, int]
    ) -> dict[str, Any] | None:
        for row in self.rows:
            if row.get("pmid") == query.get("pmid"):
                return row
        return None


class _Database:
    def __init__(self, relations: _Collection, documents: _Collection) -> None:
        self._map = {"pubmedRelations": relations, "pubmedDocs": documents}

    def __getitem__(self, name: str) -> _Collection:
        return self._map[name]


class _Client:
    def __init__(self, database: _Database) -> None:
        self._database = database

    def __getitem__(self, _name: str) -> _Database:
        return self._database


def _repository(
    relations: list[dict[str, Any]], documents: list[dict[str, Any]]
) -> graph_evidence.GraphEvidenceRepository:
    from config.settings import BiokdeConnection

    return graph_evidence.GraphEvidenceRepository(
        _Client(_Database(_Collection(relations), _Collection(documents))),
        BiokdeConnection(),
    )


def test_pmids_come_from_locations_not_context() -> None:
    """`context` holds co-occurring entities and `contList` their ids; only
    `locations` carries the PMIDs, formatted '<pmid>.<node_two>.<node_one>'."""
    repo = _repository(
        [
            {
                "node_one_id": "6308113",
                "node_two_id": "55633",
                "context": {"2765": {"contextInfo": ["99999999"]}},
                "contList": ["2765"],
                "locations": [["40419140.55633.6308113", 0.8]],
            }
        ],
        [],
    )
    assert repo.pmids_for("6308113", "55633") == ["40419140"]


def test_both_orientations_of_the_edge_are_asked_for() -> None:
    """The graph does not guarantee which node is stored first."""
    repo = _repository(
        [
            {
                "node_one_id": "55633",
                "node_two_id": "6308113",
                "locations": [["12345678.6308113.55633", 0.9]],
            }
        ],
        [],
    )
    assert repo.pmids_for("6308113", "55633") == ["12345678"]


def test_an_edge_with_no_locations_yields_nothing() -> None:
    repo = _repository(
        [{"node_one_id": "a", "node_two_id": "b", "locations": []}], []
    )
    assert repo.pmids_for("a", "b") == []


def test_registry_ids_are_mined_from_the_abstract() -> None:
    """pubmedDocs carries no DataBankList, so the id has to come from prose."""
    repo = _repository(
        [],
        [
            {
                "pmid": "1",
                "abstract": "A randomized trial (NCT00000123) of the drug.",
                "articleTitle": "A study",
                "publicationType": ["randomized controlled trial"],
            }
        ],
    )
    found = repo.mentions_in(["1"])
    assert [m.nct_id for m in found] == ["NCT00000123"]


def test_a_missing_document_is_skipped_not_fatal() -> None:
    repo = _repository([], [])
    assert repo.mentions_in(["1", "2"]) == []


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (("randomized controlled trial",), True),
        (("Clinical Trial, Phase III",), True),
        (("review",), False),
        (("case reports",), False),
        ((), False),
    ],
)
def test_publication_type_is_matched_case_insensitively(
    kinds: tuple[str, ...], expected: bool
) -> None:
    """The graph lower-cases them, which a case-sensitive check silently
    misses — it reported zero trial reports the first time."""
    assert _mention("NCT1", "1", kinds).is_trial_report is expected


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_a_trial_report_outranks_a_review() -> None:
    ranked = graph_links.rank(
        [
            _mention("NCT2", "2", ("review",)),
            _mention("NCT1", "1", ("randomized controlled trial",)),
        ]
    )
    assert ranked[0].nct_id == "NCT1"


def test_more_mentions_break_the_tie() -> None:
    ranked = graph_links.rank(
        [_mention("NCT1", "1"), _mention("NCT2", "2"), _mention("NCT2", "3")]
    )
    assert ranked[0].nct_id == "NCT2"
    assert ranked[0].mentions == 2


def test_mentions_of_one_trial_collapse_to_one_candidate() -> None:
    ranked = graph_links.rank([_mention("NCT1", "1"), _mention("NCT1", "2")])
    assert len(ranked) == 1


def test_ranking_is_deterministic() -> None:
    forward = graph_links.rank([_mention("NCT2", "1"), _mention("NCT1", "2")])
    backward = graph_links.rank([_mention("NCT1", "2"), _mention("NCT2", "1")])
    assert [c.nct_id for c in forward] == [c.nct_id for c in backward]


# ---------------------------------------------------------------------------
# The date bound
# ---------------------------------------------------------------------------


def _candidate(nct: str, start: str) -> graph_links.GraphCandidate:
    return graph_links.GraphCandidate(
        nct_id=nct, mentions=1, trial_report=True, example_pmid="1",
        example_title="t", start_date=start, enrollment="100", phases="PHASE3",
    )


def test_a_trial_starting_after_the_approval_is_dropped() -> None:
    """A trial cannot have supported an approval granted before it began."""
    kept = graph_links.within_date(
        [_candidate("NCT1", "2001-01-01"), _candidate("NCT2", "2009-01-01")],
        "2004-05-01",
    )
    assert [c.nct_id for c in kept] == ["NCT1"]


def test_a_candidate_with_no_start_date_is_kept() -> None:
    """Missing data must not hide a real answer."""
    kept = graph_links.within_date([_candidate("NCT1", "")], "2004-05-01")
    assert len(kept) == 1


def test_no_approval_date_bounds_nothing() -> None:
    kept = graph_links.within_date([_candidate("NCT1", "2009-01-01")], "")
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_the_yield_rate() -> None:
    assert graph_links.GraphReport((), 150, 142, 47, 43, 24, ()).yield_rate == 0.16


def test_the_yield_rate_of_nothing_is_zero() -> None:
    assert graph_links.GraphReport((), 0, 0, 0, 0, 0, ()).yield_rate == 0.0


def test_the_report_shows_where_units_are_lost() -> None:
    """Drug, disease and edge separately: on the pre-2008 sample 95% had a drug
    id and only 31% a disease id, so the bottleneck is indication coverage
    rather than the graph."""
    rendered = graph_links.render(
        graph_links.GraphReport((), 300, 258, 234, 200, 132, ((1, 13), (2, 21)))
    )
    assert "with a drug id" in rendered
    assert "with a disease id" in rendered
    assert "YIELDING CANDIDATES" in rendered


# ---------------------------------------------------------------------------
# The whole pass
# ---------------------------------------------------------------------------


_SPINE = (
    "application\tapp_type\tsubmission_type\tsubmission_no\tsubmission_class"
    "\taction_type\tapproval_date\tera\treview_priority\torphan"
    "\tindication_changing\ttrial_relevant\n"
    "NDA019640\tNDA\tSUPPL\t58\tEfficacy\tEfficacy-New Indication\t2006-11-01"
    "\t2000-2007\t\tfalse\ttrue\ttrue\n"
    "NDA000002\tNDA\tORIG\t1\tEfficacy\t\t2005-01-01\t2000-2007\t\tfalse"
    "\ttrue\ttrue\n"
)
_DRUGS = (
    "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
    "SOMATROPIN\tMESH:D000068840\t6308113\tSomatropin\tChemical\texact\n"
)
_INDICATION_MAP = (
    "source_name\tmesh_id\tbiokdeid\tpreferred_name\tentity_type\tbasis\n"
    "Turner Syndrome\tMESH:D014424\t55633\tTurner Syndrome\tDisease\texact\n"
)
_INDICATIONS = (
    "application\tapp_type\tdrug\tunii\tmesh_id\tmesh_term\tevidence\n"
    "NDA019640\tNDA\tSOMATROPIN\tX\tD014424\tTurner Syndrome\tlabel_section_1\n"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    (out / "approval_events.tsv").write_text(_SPINE, encoding="utf-8")
    (out / "drug_map.tsv").write_text(_DRUGS, encoding="utf-8")
    (out / "indication_map.tsv").write_text(_INDICATION_MAP, encoding="utf-8")
    (out / "indications.tsv").write_text(_INDICATIONS, encoding="utf-8")
    return out


def _graph(
    relations: list[dict[str, Any]], documents: list[dict[str, Any]]
) -> graph_evidence.GraphEvidenceRepository:
    return _repository(relations, documents)


def test_the_pass_gathers_candidates_for_an_unserved_approval(workspace: Path) -> None:
    repo = _graph(
        [
            {
                "node_one_id": "6308113",
                "node_two_id": "55633",
                "locations": [["1.55633.6308113", 0.9]],
            }
        ],
        [
            {
                "pmid": "1",
                "abstract": "A randomized trial NCT00000123 in Turner syndrome.",
                "articleTitle": "Growth hormone trial",
                "publicationType": ["randomized controlled trial"],
            }
        ],
    )
    report = graph_links.build(
        workspace,
        repo,
        {"NCT00000123": ("2001-01-01", "540", "PHASE3")},
        {"NDA019640|SUPPL|58"},
        {"NDA019640": frozenset({"SOMATROPIN"})},
    )
    assert report.with_candidates == 1
    unit = report.units[0]
    assert unit.indication_term == "Turner Syndrome"
    assert [c.nct_id for c in unit.candidates] == ["NCT00000123"]
    assert unit.candidates[0].enrollment == "540"


def test_only_the_unserved_are_considered(workspace: Path) -> None:
    """Tier C works on exactly what tiers A and B left behind, read from
    unlinked_submissions rather than recomputed, so it cannot quietly disagree
    with them."""
    repo = _graph([], [])
    report = graph_links.build(
        workspace, repo, {}, set(), {"NDA019640": frozenset({"SOMATROPIN"})}
    )
    assert report.events_considered == 0


def test_an_approval_with_no_disease_id_is_counted_not_judged(workspace: Path) -> None:
    """The measured bottleneck: 95% of pre-2008 approvals resolve a drug and
    only 31% a disease."""
    repo = _graph([], [])
    report = graph_links.build(
        workspace, repo, {}, {"NDA000002|ORIG|1"},
        {"NDA000002": frozenset({"SOMATROPIN"})},
    )
    assert report.with_drug == 1
    assert report.with_disease == 0
    assert report.with_candidates == 0
    # D31: no indication is no longer the end — it is searched on the drug
    # alone, and here the graph has no treatment edge for it either.
    assert report.drug_only_considered == 1
    assert report.drug_only_with_candidates == 0


def test_a_candidate_starting_after_the_approval_never_reaches_a_judge(
    workspace: Path,
) -> None:
    repo = _graph(
        [
            {
                "node_one_id": "6308113",
                "node_two_id": "55633",
                "locations": [["1.55633.6308113", 0.9]],
            }
        ],
        [
            {
                "pmid": "1",
                "abstract": "Trial NCT00000999 began later.",
                "articleTitle": "t",
                "publicationType": ["randomized controlled trial"],
            }
        ],
    )
    report = graph_links.build(
        workspace,
        repo,
        {"NCT00000999": ("2012-01-01", "100", "PHASE3")},
        {"NDA019640|SUPPL|58"},
        {"NDA019640": frozenset({"SOMATROPIN"})},
    )
    assert report.with_edge == 1
    assert report.with_candidates == 0


def test_the_abstract_budget_bounds_the_read(workspace: Path) -> None:
    """The median edge cites 461 PMIDs and the largest 6,187, so a pass without
    a budget is dominated by a handful of mega-drugs."""
    repo = _graph(
        [
            {
                "node_one_id": "6308113",
                "node_two_id": "55633",
                "locations": [[f"{n}.55633.6308113", 0.9] for n in range(50)],
            }
        ],
        [],
    )
    report = graph_links.build(
        workspace, repo, {}, {"NDA019640|SUPPL|58"},
        {"NDA019640": frozenset({"SOMATROPIN"})}, budget=5,
    )
    assert report.units == ()
    assert report.with_edge == 1


# ---------------------------------------------------------------------------
# What counts as "the label route is out of options"
# ---------------------------------------------------------------------------


def _unlinked(reason: str, no: str = "1") -> dict[str, str]:
    return {
        "application": "NDA1",
        "submission_type": "ORIG",
        "submission_no": no,
        "reason": reason,
    }


@pytest.mark.parametrize(
    "reason",
    ["no_label", "judged_none", "no_candidates", "no_drug_resolved"],
)
def test_an_exhausted_reason_reaches_the_graph_route(reason: str) -> None:
    assert graph_links.exhausted_only([_unlinked(reason)]) == {"NDA1|ORIG|1"}


def test_not_yet_judged_waits_for_p4() -> None:
    """P4 has not looked, which is not the same as failing. A tier-C link is
    never promoted, so pre-empting P4 trades a possible tier A for a certain
    tier C."""
    assert graph_links.exhausted_only([_unlinked("not_yet_judged")]) == set()


def test_a_later_appearance_is_not_a_miss() -> None:
    """D16 working: the trial already has a correct link on the earlier
    submission."""
    assert (
        graph_links.exhausted_only([_unlinked("later_appearance_of_the_same_trial")])
        == set()
    )


def test_a_mixed_file_takes_only_the_exhausted() -> None:
    rows = [
        _unlinked("judged_none", "1"),
        _unlinked("not_yet_judged", "2"),
        _unlinked("no_label", "3"),
        _unlinked("later_appearance_of_the_same_trial", "4"),
    ]
    assert graph_links.exhausted_only(rows) == {"NDA1|ORIG|1", "NDA1|ORIG|3"}


def test_the_submissions_own_chosen_indication_replaces_todays_label(
    workspace: Path,
) -> None:
    """D30: when submission_indications.tsv exists, tier C walks the disease
    this approval was for, not every indication the drug holds today."""
    (workspace / "submission_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsource\tsource_key\ttext"
        "\tmesh_id\tbiokdeid\tterm\tchosen\n"
        "NDA019640\tSUPPL\t58\tletter\t\tx\tMESH:D014424\t55633\tTurner Syndrome\ttrue\n",
        encoding="utf-8",
    )
    repo = _graph(
        [{"node_one_id": "6308113", "node_two_id": "55633",
          "locations": [["1.55633.6308113", 0.9]]}],
        [{"pmid": "1", "abstract": "Trial NCT00000123 in Turner syndrome.",
          "articleTitle": "t", "publicationType": ["randomized controlled trial"]}],
    )
    report = graph_links.build(
        workspace, repo, {"NCT00000123": ("2001-01-01", "540", "PHASE3")},
        {"NDA019640|SUPPL|58", "NDA000002|ORIG|1"},
        {"NDA019640": frozenset({"SOMATROPIN"})},
    )
    assert [u.indication_term for u in report.units] == ["Turner Syndrome"]


# ---------------------------------------------------------------------------
# D31 — no indication: search the drug's negative-correlation (treats) edges
# ---------------------------------------------------------------------------


def _treats(disease: str, name: str, pmids: list[str], *, score: str = "5",
            rel: str = "3", direction: str = "12", drug_first: bool = True,
            other_type: str = "Disease") -> dict[str, Any]:
    drug, other = "6308113", disease
    row: dict[str, Any] = {
        "relationship_type": rel, "direction": direction, "score": score,
        "locations": [[f"{p}.x.y", 0.9] for p in pmids],
    }
    if drug_first:
        row.update(node_one_id=drug, node_two_id=other, node_one_type="Chemical",
                   node_two_type=other_type, node_two_name=name)
    else:
        row.update(node_one_id=other, node_two_id=drug, node_one_type=other_type,
                   node_two_type="Chemical", node_one_name=name)
    return row


def _paper(pmid: str, nct: str) -> dict[str, Any]:
    return {"pmid": pmid, "abstract": f"Trial {nct}.", "articleTitle": f"Paper {pmid}",
            "publicationType": ["randomized controlled trial"]}


def test_only_drug_to_disease_negative_correlation_edges_are_walked() -> None:
    repo = _repository([
        _treats("d1", "Dwarfism", ["1"], score="9"),
        _treats("d2", "Turner Syndrome", ["2"], drug_first=False, direction="21"),
        _treats("d3", "Obesity", ["3"], direction="NA"),
        _treats("d4", "Headache", ["4"], rel="2"),                 # positive: adverse
        _treats("d5", "Short Stature", ["5"], direction="21"),     # disease -> drug
        _treats("g1", "GH1", ["6"], other_type="Gene"),
    ], [])
    edges = repo.treatment_edges("6308113")
    assert [e.disease_name for e in edges] == ["Dwarfism", "Turner Syndrome", "Obesity"]  # ties by id
    assert edges[0].pmids == ("1",)


def test_a_bad_score_sorts_last_rather_than_failing() -> None:
    repo = _repository([_treats("d1", "A", ["1"], score="n/a"),
                        _treats("d2", "B", ["2"], score="1")], [])
    assert [e.disease_name for e in repo.treatment_edges("6308113")] == ["B", "A"]


def test_an_approval_with_no_indication_is_searched_on_the_drug_alone(
    workspace: Path,
) -> None:
    repo = _graph(
        [_treats("d1", "Growth Hormone Deficiency", ["1", "2"], score="9"),
         _treats("d2", "Turner Syndrome", ["2", "3"], score="4")],
        [_paper("1", "NCT00000101"), _paper("2", "NCT00000102"), _paper("3", "NCT00000103")],
    )
    report = graph_links.build(
        workspace, repo,
        {"NCT00000101": ("2001-01-01", "200", "PHASE3", "2003-01-01"),
         "NCT00000102": ("2002-01-01", "90", "PHASE3", "2006-05-01"),   # finished after
         "NCT00000103": ("2001-06-01", "50", "PHASE2", "")},            # unknown: kept
        {"NDA000002|ORIG|1"},
        {"NDA000002": frozenset({"SOMATROPIN"})},
    )
    [unit] = report.units
    assert unit.basis == graph_links.BASIS_DRUG_ONLY and unit.indication_term == ""
    assert unit.drug == "SOMATROPIN"
    by = {c.nct_id: c for c in unit.candidates}
    assert set(by) == {"NCT00000101", "NCT00000103"}
    assert by["NCT00000101"].diseases == "Growth Hormone Deficiency"
    assert by["NCT00000103"].diseases == "Turner Syndrome"
    assert report.drug_only_with_candidates == 1
    assert "drug-only search (D31)" in graph_links.render(report)


def test_an_approval_with_an_indication_keeps_the_indication_path(workspace: Path) -> None:
    """D31 is a fallback: where the indication is known nothing changes, and a
    treatment edge to another disease is not walked."""
    repo = _graph(
        [{"node_one_id": "6308113", "node_two_id": "55633",
          "locations": [["1.55633.6308113", 0.9]]},
         _treats("d9", "Obesity", ["9"])],
        [_paper("1", "NCT00000123"), _paper("9", "NCT00000999")],
    )
    report = graph_links.build(
        workspace, repo, {"NCT00000123": ("2001-01-01", "540", "PHASE3", "2009-01-01")},
        {"NDA019640|SUPPL|58"}, {"NDA019640": frozenset({"SOMATROPIN"})},
    )
    [unit] = report.units
    assert unit.basis == graph_links.BASIS_INDICATION and unit.drug == "SOMATROPIN"
    # the completion bound is D31's, not applied to the indication path
    assert [c.nct_id for c in unit.candidates] == ["NCT00000123"]
    assert report.drug_only_considered == 0


def test_a_drug_only_search_with_nothing_surviving_the_dates_yields_no_unit(
    workspace: Path,
) -> None:
    repo = _graph([_treats("d1", "X", ["1"])], [_paper("1", "NCT00000101")])
    report = graph_links.build(
        workspace, repo, {"NCT00000101": ("2010-01-01", "10", "PHASE3", "2012-01-01")},
        {"NDA000002|ORIG|1"}, {"NDA000002": frozenset({"SOMATROPIN"})},
    )
    assert report.units == () and report.drug_only_considered == 1


def test_the_drug_only_limit_stops_the_pass(workspace: Path) -> None:
    repo = _graph([_treats("d1", "X", ["1"])], [_paper("1", "NCT00000101")])
    report = graph_links.build(
        workspace, repo, {}, {"NDA000002|ORIG|1", "NDA019640|SUPPL|58"},
        {"NDA000002": frozenset({"SOMATROPIN"}), "NDA019640": frozenset({"SOMATROPIN"})},
        limit=1,
    )
    assert report.events_considered == 1


def test_abstracts_are_found_whether_pmid_is_stored_as_text_or_number() -> None:
    repo = _repository([], [
        {"pmid": "1", "abstract": "NCT00000001", "articleTitle": "a", "publicationType": []},
        {"pmid": 2, "abstract": "NCT00000002", "articleTitle": "b", "publicationType": []},
    ])
    assert [(m.pmid, m.nct_id) for m in repo.mentions_in(["2", "1", "2", "x9"])] == [
        ("2", "NCT00000002"), ("1", "NCT00000001")]
