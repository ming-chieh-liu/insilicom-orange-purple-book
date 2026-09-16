"""Tests for the salt → active moiety table."""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from source import moiety

_HEADER = (
    "set_id,brand_name,generic_name,active_ingredients,unii,moiety_names,"
    "moiety_uniis,product_type,route,manufacturer,application_numbers,"
    "marketing_category,market_start_date,first_approval_date,"
    "ingredient_first_market_date,ingredient_first_approval_date,"
    "market_end_date,n_ndc_products,indication_text"
)


def _row(ingredients: str, uniis: str, moieties: str, moiety_uniis: str) -> str:
    return (
        f'x,Brand,Generic,"{ingredients}","{uniis}","{moieties}","{moiety_uniis}",'
        "HUMAN PRESCRIPTION DRUG,ORAL,Maker,NDA1,NDA,2019,2019,2019,2019,,1,Use"
    )


@pytest.fixture
def extract(tmp_path: Path) -> Path:
    path = tmp_path / "extract.csv.gz"
    lines = [
        _HEADER,
        _row("ESOMEPRAZOLE MAGNESIUM", "R6DXU4WAY9", "ESOMEPRAZOLE", "N3PA6559FT"),
        _row("CARBOPLATIN", "BG3F62OND5", "CARBOPLATIN", "BG3F62OND5"),
        # Multi-ingredient: the two lists do not correspond positionally, so
        # zipping them invents pairs. Must be skipped entirely.
        _row(
            ".ALPHA.-LIPOIC ACID; ASCORBIC ACID",
            "U1A4E5Z6T3; PQ6CK8PD0R",
            ".ALPHA.-KETOGLUTARIC ACID; ASCORBIC ACID",
            "Y5T4S8R2Q1; PQ6CK8PD0R",
        ),
        # A biosimilar: folding it would merge it into its reference product.
        _row("USTEKINUMAB-AEKN", "A1B2C3D4E5", "USTEKINUMAB", "F6G7H8I9J0"),
        # A salt stated twice one way and once another: majority settles it.
        _row("AMPHETAMINE ASPARTATE", "U1", "AMPHETAMINE", "M1"),
        _row("AMPHETAMINE ASPARTATE", "U1", "AMPHETAMINE", "M1"),
        _row("AMPHETAMINE ASPARTATE", "U2", "DEXTROAMPHETAMINE", "M2"),
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def test_a_salt_maps_to_its_moiety(extract: Path) -> None:
    got = moiety.read(extract)
    assert got["ESOMEPRAZOLE MAGNESIUM"].moiety == "ESOMEPRAZOLE"


def test_both_uniis_are_kept(extract: Path) -> None:
    record = moiety.read(extract)["ESOMEPRAZOLE MAGNESIUM"]
    assert record.salt_unii == "R6DXU4WAY9"
    assert record.moiety_unii == "N3PA6559FT"


def test_a_drug_that_is_its_own_moiety_is_not_listed(extract: Path) -> None:
    """Nothing to fold, so nothing to carry."""
    assert "CARBOPLATIN" not in moiety.read(extract)


def test_multi_ingredient_rows_are_skipped(extract: Path) -> None:
    """The lists are semicolon-joined and do not correspond positionally —
    zipping pairs .ALPHA.-LIPOIC ACID with .ALPHA.-KETOGLUTARIC ACID, and made
    1,350 salts claim more than one moiety."""
    assert ".ALPHA.-LIPOIC ACID" not in moiety.read(extract)


def test_a_biosimilar_suffix_is_never_folded(extract: Path) -> None:
    """A biosimilar's 351(k) rests on its own trials, so the suffix is the
    identity, not noise. See D11."""
    assert "USTEKINUMAB-AEKN" not in moiety.read(extract)


def test_a_conflict_is_settled_by_majority(extract: Path) -> None:
    assert moiety.read(extract)["AMPHETAMINE ASPARTATE"].moiety == "AMPHETAMINE"


def test_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    """An enrichment, not a dependency."""
    assert moiety.read(tmp_path / "absent.csv.gz") == {}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("USTEKINUMAB-AEKN", True),
        ("ADALIMUMAB-AACF", True),
        ("USTEKINUMAB", False),
        ("ABIRATERONE ACETATE", False),
        ("DRUG-AB", False),
    ],
)
def test_the_biosimilar_pattern(name: str, expected: bool) -> None:
    assert moiety.is_biosimilar(name) is expected
