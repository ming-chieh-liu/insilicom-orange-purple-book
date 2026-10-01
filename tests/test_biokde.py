"""Tests for the iKraph1 connection: since D42 only the abstracts store is read."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import BiokdeConnection
from dao import biokde


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
