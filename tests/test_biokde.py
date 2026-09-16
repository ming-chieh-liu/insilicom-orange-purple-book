"""Tests for the graph DAO: name → entity, and entity → synonyms."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config.settings import BiokdeConnection
from dao import biokde

_ROWS: list[dict[str, str]] = [
    {"name": "Keytruda", "id": "MESH:C582435", "official name": "Pembrolizumab",
     "type": "Chemical", "subtype": "Drug"},
    {"name": "MK3475", "id": "MESH:C582435", "official name": "Pembrolizumab",
     "type": "Chemical", "subtype": "Drug"},
    {"name": "Keytruda", "id": "PubChem:SID187051801", "official name": "GTPL7499",
     "type": "Chemical", "subtype": "Drug"},
    {"name": "Carboplatin", "id": "MESH:D016190", "biokdeid": "9999",
     "official name": "Carboplatin", "type": "Chemical", "subtype": "Drug"},
    # An id-less row, which the graph writes as the literal string "NA".
    {"name": "Acoltremon", "id": "NA", "official name": "Acoltremon",
     "type": "Chemical", "subtype": "NA"},
    # A row with no id at all: must not be yielded as an entity.
    {"name": "Broken", "id": "", "official name": "", "type": "Chemical",
     "subtype": "NA"},
]


class _Cursor:
    """The slice of pymongo's cursor this DAO uses."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows
        self.collation_used: dict[str, object] | None = None

    def collation(self, spec: dict[str, object]) -> "_Cursor":
        self.collation_used = spec
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)


class _Collection:
    """A stand-in for ``pubmedEntitiesFlat`` with case-insensitive matching."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, Any]] = []
        self.last_cursor: _Cursor | None = None

    def find(self, query: dict[str, Any], _projection: dict[str, int]) -> _Cursor:
        self.queries.append(query)
        if "name" in query:
            wanted = {value.casefold() for value in query["name"]["$in"]}
            rows = [r for r in self.rows if r["name"].casefold() in wanted]
        else:
            wanted = set(query["id"]["$in"])
            rows = [r for r in self.rows if r["id"] in wanted]
        self.last_cursor = _Cursor(rows)
        return self.last_cursor


class _Client:
    """``client[database][collection]``, and nothing else."""

    def __init__(self, collection: _Collection) -> None:
        self._collection = collection

    def __getitem__(self, _name: str) -> Any:
        return self

    def __getattr__(self, _name: str) -> Any:  # pragma: no cover - not used
        raise AttributeError(_name)


class _Database:
    def __init__(self, collection: _Collection) -> None:
        self._collection = collection

    def __getitem__(self, _name: str) -> _Collection:
        return self._collection


class _Root:
    def __init__(self, collection: _Collection) -> None:
        self._database = _Database(collection)

    def __getitem__(self, _name: str) -> _Database:
        return self._database


@pytest.fixture
def collection() -> _Collection:
    return _Collection(list(_ROWS))


@pytest.fixture
def repository(collection: _Collection) -> biokde.BiokdeRepository:
    return biokde.BiokdeRepository(_Root(collection), BiokdeConnection())


def test_a_name_resolves_to_every_entity_that_claims_it(
    repository: biokde.BiokdeRepository,
) -> None:
    got = repository.resolve(["Keytruda"])
    assert {e.id for e in got["Keytruda"]} == {
        "MESH:C582435",
        "PubChem:SID187051801",
    }


def test_lookup_is_case_insensitive(repository: biokde.BiokdeRepository) -> None:
    """A plain equality query on ``name`` is case-sensitive and loses
    ``keytruda`` silently. Every query must carry the collation."""
    got = repository.resolve(["keytruda"])
    assert "keytruda" in got
    assert "MESH:C582435" in {e.id for e in got["keytruda"]}


def test_the_collation_is_passed(
    repository: biokde.BiokdeRepository, collection: _Collection
) -> None:
    """Without it the ``name_1_type_1_ci`` index is not used either."""
    repository.resolve(["Keytruda"])
    assert collection.last_cursor is not None
    assert collection.last_cursor.collation_used == {"locale": "en", "strength": 2}


def test_the_result_is_keyed_by_the_name_asked_for(
    repository: biokde.BiokdeRepository,
) -> None:
    """The match is case-insensitive, so the stored spelling may differ. A
    caller has to be able to look its own string back up."""
    got = repository.resolve(["KEYTRUDA"])
    assert "KEYTRUDA" in got
    assert "Keytruda" not in got


def test_an_unknown_name_is_absent_rather_than_empty(
    repository: biokde.BiokdeRepository,
) -> None:
    assert "nosuchdrug" not in repository.resolve(["nosuchdrug", "Keytruda"])


def test_a_row_with_no_id_is_not_an_entity(
    repository: biokde.BiokdeRepository,
) -> None:
    assert "Broken" not in repository.resolve(["Broken"])


def test_blank_names_are_not_asked_for(
    repository: biokde.BiokdeRepository, collection: _Collection
) -> None:
    repository.resolve(["", "Keytruda"])
    assert collection.queries[0]["name"]["$in"] == ["Keytruda"]


def test_synonyms_expand_an_id_to_every_spelling(
    repository: biokde.BiokdeRepository,
) -> None:
    assert repository.synonyms(["MESH:C582435"])["MESH:C582435"] == frozenset(
        {"Keytruda", "MK3475"}
    )


def test_an_id_with_no_rows_is_absent(
    repository: biokde.BiokdeRepository,
) -> None:
    assert "MESH:D999999" not in repository.synonyms(["MESH:D999999"])


def test_names_are_batched(collection: _Collection) -> None:
    """A 16 MB query document is a real limit; 2,700 drug names must not go in
    one ``$in``."""
    repository = biokde.BiokdeRepository(_Root(collection), BiokdeConnection())
    names = [f"drug-{n}" for n in range(biokde.BATCH + 5)]
    repository.resolve(names)
    assert len(collection.queries) == 2
    assert len(collection.queries[0]["name"]["$in"]) == biokde.BATCH


def test_the_uri_takes_the_host_from_config_not_the_file(tmp_path: Path) -> None:
    """The credentials file in this repo names ``localhost`` but authenticates
    against galaxy3. Trusting its host would connect to the wrong machine."""
    creds = tmp_path / "creds.json"
    creds.write_text(
        json.dumps(
            {"host": "localhost", "port": 27017, "username": "u", "password": "p@ss"}
        )
    )
    uri = biokde.connection_uri(BiokdeConnection(credentials=creds))
    assert uri == "mongodb://u:p%40ss@galaxy3:27017/?authSource=admin"
    assert "localhost" not in uri


def test_a_password_needing_escapes_is_quoted(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"username": "a/b", "password": "c:d@e"}))
    assert "a%2Fb:c%3Ad%40e@" in biokde.connection_uri(
        BiokdeConnection(credentials=creds)
    )


def test_has_credentials_reports_the_file(tmp_path: Path) -> None:
    missing = BiokdeConnection(credentials=tmp_path / "nope.json")
    assert not missing.has_credentials
    present = tmp_path / "creds.json"
    present.write_text("{}")
    assert BiokdeConnection(credentials=present).has_credentials


def test_the_internal_node_id_is_retained(
    repository: biokde.BiokdeRepository,
) -> None:
    """``pubmedRelations`` keys its edges on ``biokdeid``, not on the prefixed
    vocabulary id. Discarding it means every later query that wants to walk
    from a drug to its supporting literature must resolve the entity again."""
    got = repository.resolve(["Carboplatin"])
    assert got["Carboplatin"][0].biokdeid == "9999"
