"""The approval-letter sentence table, shared by S4 reviews and S6 label units."""

from __future__ import annotations

from pathlib import Path

from service import letter_sentences


def test_letter_sentences_are_read(tmp_path: Path) -> None:
    assert letter_sentences.read_sentences(tmp_path) == {}
    (tmp_path / "letter_indications.tsv").write_text(
        "application\tsubmission_type\tsubmission_no\tsentence\n"
        "A\tORIG\t1\tprovides for X\nB\tORIG\t1\t\n", encoding="utf-8")
    assert letter_sentences.read_sentences(tmp_path) == {"A|ORIG|1": "provides for X"}
