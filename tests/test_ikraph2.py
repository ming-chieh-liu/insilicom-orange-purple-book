"""Tests for the iKraph2 DAOs and the exact drug identity (D42); no network, no Mongo."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from config.ikraph2 import Ikraph2Connection
from dao.graph_evidence import Abstract, Mention
from dao.ikraph2_api import Ikraph2Api, Ikraph2Error, Ikraph2Literature
from dao.ikraph2_entities import Ikraph2Entities, Node, connection_uri
from service import drug_identity, normalize
from source.moiety import Moiety

REL = "v1_2"


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Opener:
    """Answers each request from a handler; records the bodies."""

    def __init__(self, handler: Callable[[str, dict[str, Any]], object]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _Response:
        endpoint = request.full_url.rsplit("/", 1)[1]
        assert isinstance(request.data, bytes)
        body = json.loads(request.data)
        self.calls.append((endpoint, body))
        reply = self.handler(endpoint, body)
        if isinstance(reply, Exception):
            raise reply
        return _Response(json.dumps(reply).encode())


def _api(handler: Callable[[str, dict[str, Any]], object]) -> tuple[Ikraph2Api, _Opener]:
    opener = _Opener(handler)
    return Ikraph2Api(Ikraph2Connection(), opener=opener, sleep=lambda s: None), opener


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, None)  # type: ignore[arg-type]


def _relation(edge: str, disease: int, relation: str = "treats", score: float = 1.0,
              subject: bool = True) -> dict[str, Any]:
    return {"edge_id": edge, "relation": relation, "other_id": disease,
            "other_name": f"disease {disease}", "score": score, "document_count": 3,
            "anchor_is_subject": subject}


def _graph(endpoint: str, body: dict[str, Any]) -> object:
    """Drug 27: two pages of disease relations; evidence pages of two PMIDs."""
    if endpoint == "search_direct_relations":
        if body.get("cursor") is None:
            return {"kg_release": REL, "next_cursor": "page2",
                    "relations": [_relation("27.1.9.0", 1, score=5.0),
                                  _relation("27.1.8.0", 1, "causes", 2.0)]}
        return {"kg_release": REL, "next_cursor": None,
                "relations": [_relation("27.2.9.0", 2, score=9.0),
                              _relation("27.3.9.0", 3, score=1.0, subject=False)]}
    edge = body["edge_id"]
    cursor = body.get("cursor")
    start = 0 if cursor is None else int(cursor)
    pmids = {"27.1.9.0": ["11", "12", "13"], "27.1.8.0": ["12", "14"],
             "27.2.9.0": ["21", "22", "23", "24"]}[edge]
    page = pmids[start:start + 2]
    nxt = None if start + 2 >= len(pmids) else str(start + 2)
    return {"kg_release": REL, "paging": {"next_cursor": nxt},
            "documents": [{"pmid": int(p)} for p in page] + [{"pmid": None}]}


def test_relations_are_paged_through_and_sorted_by_score() -> None:
    api, opener = _api(_graph)
    edges = api.disease_edges("27")
    assert [e.edge_id for e in edges] == ["27.2.9.0", "27.1.9.0", "27.1.8.0", "27.3.9.0"]
    assert opener.calls[0][1] == {"biokdeid": 27, "other_types": ["Disease"], "page_size": 3000}
    assert opener.calls[1][1]["cursor"] == "page2"
    api.disease_edges("27")
    assert len(opener.calls) == 2  # cached


def test_evidence_stops_at_the_limit_and_is_cached() -> None:
    api, opener = _api(_graph)
    assert api.edge_pmids("27.2.9.0", 2) == ["21", "22"]
    assert api.edge_pmids("27.2.9.0", 1) == ["21"]
    assert len(opener.calls) == 1
    assert api.edge_pmids("27.2.9.0", 10) == ["21", "22", "23", "24"]
    calls = len(opener.calls)
    assert api.edge_pmids("27.2.9.0", 50) == ["21", "22", "23", "24"]  # exhausted: cached
    assert len(opener.calls) == calls
    assert api.edge_pmids("27.2.9.0", 0) == []


def test_pmids_for_a_pair_take_every_relation_type_up_to_the_budget() -> None:
    api, _ = _api(_graph)
    literature = Ikraph2Literature(api, _Docs(), budget=4)
    assert literature.pmids_for("27", "1") == ["11", "12", "13", "14"]
    assert Ikraph2Literature(api, _Docs(), budget=2).pmids_for("27", "1") == ["11", "12"]
    assert literature.pmids_for("27", "99") == []


def test_treatment_edges_keep_drug_to_disease_treats_and_spend_the_budget_in_score_order(
        ) -> None:
    api, _ = _api(_graph)
    edges = Ikraph2Literature(api, _Docs(), budget=4).treatment_edges("27")
    assert [(e.disease_biokdeid, e.pmids) for e in edges] == [
        ("2", ("21", "22", "23", "24")), ("1", ())]


class _Docs:
    def mentions_in(self, pmids: Iterable[str],
                    resolve: Callable[[str], list[tuple[str, str]]] | None = None
                    ) -> list[Mention]:
        return [Mention("NCT00000001", p, "t", ()) for p in pmids]

    def abstracts(self, pmids: Iterable[str]) -> list[Abstract]:
        return [Abstract(p, "t", "a", ()) for p in pmids]


def test_abstracts_and_mentions_come_from_the_documents_store() -> None:
    api, opener = _api(_graph)
    literature = Ikraph2Literature(api, _Docs())
    assert [m.pmid for m in literature.mentions_in(["5"])] == ["5"]
    assert [a.pmid for a in literature.abstracts(["6"])] == ["6"]
    assert opener.calls == []


def test_a_reply_from_another_release_is_refused() -> None:
    api, _ = _api(lambda e, b: {"kg_release": "v1_1", "relations": []})
    with pytest.raises(Ikraph2Error, match="release"):
        api.disease_edges("27")
    api, _ = _api(lambda e, b: ["not", "an", "object"])
    with pytest.raises(Ikraph2Error, match="unexpected"):
        api.disease_edges("27")


def test_transient_errors_are_retried_and_client_errors_are_not() -> None:
    replies: list[object] = [_http(503), urllib.error.URLError("down"),
                             {"kg_release": REL, "relations": [], "next_cursor": None}]
    api, opener = _api(lambda e, b: replies.pop(0))
    assert api.disease_edges("27") == []
    assert len(opener.calls) == 3
    api, opener = _api(lambda e, b: _http(400))
    with pytest.raises(Ikraph2Error, match="HTTP 400"):
        api.disease_edges("28")
    assert len(opener.calls) == 1
    api, _ = _api(lambda e, b: _http(429))
    with pytest.raises(Ikraph2Error, match="after 4 attempts"):
        api.disease_edges("29")


class _Cursor(list[dict[str, Any]]):
    pass


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any], projection: dict[str, int]) -> _Cursor:
        self.queries.append(query)
        ids = set(query["db_id"]["$in"])
        types = set(query["type"]["$in"])
        return _Cursor(r for r in self.rows if r["db_id"] in ids and r["type"] in types)


def _client(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, _Collection]], _Collection]:
    collection = _Collection(rows)
    return {"biokde": {"ikraph2_entities_v1_2": collection}}, collection


_ROWS = [
    {"biokdeid": 2700000295, "type": "TherapyPreventions", "official_name": "PEMBROLIZUMAB",
     "db_id": "UNII:DPT0O3T46P", "mention_count": 31466},
    {"biokdeid": 6308113, "type": "Chemical", "official_name": "pembrolizumab",
     "db_id": "MESH:C582435", "mention_count": 4},
    {"biokdeid": 202330, "type": "Chemical", "official_name": "heparin",
     "db_id": "MESH:D006493", "mention_count": 9},
    {"biokdeid": 202331, "type": "Chemical", "official_name": "heparin sodium",
     "db_id": "PubChem:CID772", "mention_count": 1},
    {"biokdeid": 9, "type": "Disease", "official_name": "x", "db_id": "MESH:D006493",
     "mention_count": 1},
]


def test_entities_are_read_by_exact_id_and_only_drug_or_chemical_types() -> None:
    client, collection = _client(_ROWS)
    found = Ikraph2Entities(client, Ikraph2Connection()).by_db_id(
        ["UNII:DPT0O3T46P", "MESH:D006493", "", "MESH:NONE"])
    assert set(found) == {"UNII:DPT0O3T46P", "MESH:D006493"}
    assert found["MESH:D006493"] == [Node("202330", "Chemical", "heparin", "MESH:D006493", 9)]
    assert collection.queries[0]["type"] == {"$in": ["TherapyPreventions", "Chemical"]}


def test_the_mongo_uri_quotes_the_credentials(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"username": "m l", "password": "p@ss"}), encoding="utf-8")
    connection = Ikraph2Connection(credentials=creds)
    assert connection.has_credentials
    assert connection.entities == "ikraph2_entities_v1_2"
    assert connection_uri(connection) == (
        "mongodb://m+l:p%40ss@jupiter2:27017/?authSource=admin")


def test_identity_prefers_the_drug_node_and_falls_back_to_chemical() -> None:
    client, _ = _client(_ROWS)
    lookup = Ikraph2Entities(client, Ikraph2Connection()).by_db_id
    uniis = drug_identity.uniis_of(
        ["PEMBROLIZUMAB", "HEPARIN SODIUM", "ODDITY", "NOTHING"],
        {"PEMBROLIZUMAB": "DPT0O3T46P", "HEPARIN SODIUM": "ZZFG82DD2Y", "ODDITY": "ODDUNII000"},
        {"HEPARIN SODIUM": Moiety("HEPARIN SODIUM", "HEPARIN", "ZZFG82DD2Y", "T2410KM04A")})
    assert uniis["HEPARIN SODIUM"] == ("ZZFG82DD2Y", "T2410KM04A")
    identities, report = drug_identity.build(
        ["PEMBROLIZUMAB", "HEPARIN SODIUM", "ODDITY", "NOTHING"], uniis,
        {"DPT0O3T46P": ("MESH:C582435", "pembrolizumab"),
         "T2410KM04A": ("MESH:D006493", "Heparin")},
        {"ZZFG82DD2Y": "PubChem:CID772"}, lookup)
    pembro = identities["PEMBROLIZUMAB"]
    # A drug node exists, so the Chemical node holding the same MeSH id is not used.
    assert (pembro.mesh_id, pembro.nodes, pembro.node_type) == (
        "MESH:C582435", "2700000295", "TherapyPreventions")
    heparin = identities["HEPARIN SODIUM"]
    assert (heparin.mesh_id, heparin.nodes, heparin.node_type) == (
        "MESH:D006493", "202330;202331", "Chemical")
    assert identities["ODDITY"].basis == drug_identity.BASIS_NO_MESH
    assert identities["NOTHING"].basis == drug_identity.BASIS_NONE
    assert report == drug_identity.IdentityReport(4, 3, 2, 1, 1, 2)
    assert "Chemical (fallback)" in drug_identity.render(report)


def test_drug_rows_take_the_registry_mesh_and_ikraph2_nodes() -> None:
    old = {
        "A": normalize.Mapping("A", "MESH:D1", "11", "a", "Chemical", "exact"),
        "B": normalize.Mapping("B", "PubChem:CID1", "12", "b", "Chemical", "moiety"),
        "C": normalize.Mapping("C", "MESH:D3", "13", "c", "Chemical", "stem"),
    }
    ids = {
        "A": drug_identity.DrugIdentity("A", "UA", "MESH:D9", "Nine", "", "", "unii_registry"),
        "B": drug_identity.DrugIdentity("B", "UB", "", "", "28", "Chemical", "unii_no_mesh"),
        "D": drug_identity.DrugIdentity("D", "", "", "", "", "", "no_unii"),
    }
    out = normalize.with_identities(old, ids)
    assert out["A"] == normalize.Mapping("A", "MESH:D9", "", "Nine", "Chemical",
                                         "unii_registry")
    assert (out["B"].mesh_id, out["B"].biokdeid, out["B"].basis) == (
        "PubChem:CID1", "28", "moiety")
    # Not asked about: keeps iKraph1's id, but no iKraph1 node id survives.
    assert (out["C"].mesh_id, out["C"].biokdeid) == ("MESH:D3", "")
    assert "D" not in out


def test_prefetch_warms_pairs_and_treats_walks_so_the_serial_pass_is_free() -> None:
    api, opener = _api(_graph)
    literature = Ikraph2Literature(api, _Docs(), budget=4, workers=2)
    literature.prefetch([("27", "1"), ("27", "2"), ("27", "1")])
    literature.prefetch_treatments(["27"])
    calls = len(opener.calls)
    assert literature.pmids_for("27", "2") == ["21", "22", "23", "24"]
    assert literature.treatment_edges("27")[0].pmids == ("21", "22", "23", "24")
    assert len(opener.calls) == calls


def test_a_failed_prefetch_is_logged_and_left_cold(caplog: pytest.LogCaptureFixture) -> None:
    api, _ = _api(lambda e, b: _http(400))
    Ikraph2Literature(api, _Docs()).prefetch([("27", "1")])
    assert "prefetch, drug 27" in caplog.text
