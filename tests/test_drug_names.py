"""P1's drug names table: brand, generic, ingredient, moiety, UNII."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from service import drug_names
from source import moiety
from source.moiety import Moiety


class _Data:
    """Just the three tables :func:`drug_names.build` reads."""

    def __init__(self, applications: Path, products: Path, unii: Path) -> None:
        self.applications = applications
        self.products = products
        self.unii_names = unii


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_build_keeps_every_name_in_ingredient_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = {
        "applications": [{"ApplNo": "208215", "ApplType": "NDA"},
                         {"ApplNo": "000001", "ApplType": "ANDA"}],
        "products": [
            {"ApplNo": "208215", "DrugName": "DESCOVY",
             "ActiveIngredient": "EMTRICITABINE; TENOFOVIR ALAFENAMIDE FUMARATE"},
            {"ApplNo": "208215", "DrugName": "DESCOVY", "ActiveIngredient": "EMTRICITABINE"},
            {"ApplNo": "000001", "DrugName": "X", "ActiveIngredient": "Y"},
        ],
        "unii": [{"NAME": "EMTRICITABINE", "UNII": "G70B4ETF4S", "DISPLAY_NAME": ""},
                 {"NAME": "AMBIGUOUS", "UNII": "A", "DISPLAY_NAME": ""},
                 {"NAME": "AMBIGUOUS", "UNII": "B", "DISPLAY_NAME": ""}],
    }
    data = _Data(Path("applications"), Path("products"), Path("unii"))
    monkeypatch.setattr(drug_names, "read_rows", lambda table: iter(rows[str(table)]))
    uniis = drug_names.unii_index(data)  # type: ignore[arg-type]
    assert uniis == {"EMTRICITABINE": "G70B4ETF4S"}
    fold = {"TENOFOVIR ALAFENAMIDE FUMARATE": Moiety(
        "TENOFOVIR ALAFENAMIDE FUMARATE", "TENOFOVIR ANHYDROUS", "FWF6Q91TZO", "W4HFE001U5")}
    built = drug_names.build(
        data, frozenset({"NDA208215"}), fold,  # type: ignore[arg-type]
        {"NDA208215": "EMTRICITABINE AND TENOFOVIR ALAFENAMIDE"}, uniis,
    )
    assert built == [drug_names.DrugNames(
        "NDA208215", "DESCOVY", "EMTRICITABINE AND TENOFOVIR ALAFENAMIDE",
        "EMTRICITABINE; TENOFOVIR ALAFENAMIDE FUMARATE",
        "EMTRICITABINE; TENOFOVIR ANHYDROUS",
        "G70B4ETF4S; FWF6Q91TZO", "G70B4ETF4S; W4HFE001U5",
    )]
    assert built[0].display == "DESCOVY (emtricitabine and tenofovir alafenamide)"


def test_generic_names_are_read_per_application_by_majority(tmp_path: Path) -> None:
    path = tmp_path / "extract.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("generic_name,application_numbers\n"
                     "Zanubrutinib,NDA218785\nzanubrutinib,NDA218785; NDA213217\n"
                     "Other,NDA218785\n,NDA9\n")
    assert moiety.read_generic_names(path) == {
        "NDA218785": "ZANUBRUTINIB", "NDA213217": "ZANUBRUTINIB"}
    assert moiety.read_generic_names(tmp_path / "missing.gz") == {}
