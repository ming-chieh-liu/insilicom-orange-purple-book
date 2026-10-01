"""Tests for the sponsor family list used as a shortlist rank key (D39)."""

from __future__ import annotations

from source.sponsor_families import family_of, same_sponsor


def test_a_family_joins_a_parent_and_its_subsidiary() -> None:
    assert same_sponsor("GENENTECH INC", "Hoffmann-La Roche")


def test_two_unrelated_mercks_stay_apart() -> None:
    assert family_of("Merck Sharp & Dohme LLC") == "merck_us"
    assert not same_sponsor("MERCK SHARP DOHME", "Merck KGaA, Darmstadt, Germany")


def test_a_family_member_never_matches_an_outsider_on_a_shared_word() -> None:
    assert not same_sponsor("PFIZER INC", "National Cancer Institute (NCI)")


def test_unlisted_companies_match_on_a_distinctive_word() -> None:
    assert same_sponsor("ACME BIOSCIENCES INC", "Acme Biosciences")
    assert not same_sponsor("ACME PHARMACEUTICALS INC", "Other Pharmaceuticals Inc")
