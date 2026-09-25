"""Tests for the review route: protocol ids FDA reviews cite (D26)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from service import review_trials as rt
from source import label_pdf, polite_fetch, review_docs
from source.trial_studies import StudyFacts

_PAD = "Clinical review of QUARTETTE (levonorgestrel). " * 60  # past the scan threshold


def _facts(nct: str, org: str, sponsor: str, start: str, title: str,
           secondary: tuple[str, ...] = ()) -> StudyFacts:
    return StudyFacts(nct, title, org, "", start, (), "", ("DR-103",), (),
                      secondary_ids=secondary, lead_sponsor=sponsor)


_INDEX = rt.build_index([
    _facts("NCT00996580", "DR-103-301", "Teva Women's Health", "2009-10", "DR-103 study"),
    _facts("NCT00803062", "NCI-2009-01084", "National Cancer Institute", "2009-04",
           "Paclitaxel with or without Bevacizumab", ("GOG-0240",)),
    _facts("NCT1", "SHARED-1", "A Co", "2001", "x"),
    _facts("NCT2", "SHARED-1", "B Co", "2001", "y"),
])


def test_secondary_ids_are_indexed_and_shared_ids_are_ambiguous() -> None:
    assert [s.nct_id for s in _INDEX["GOG0240"]] == ["NCT00803062"]
    ok = rt.judge_mention("GOG-0240", _INDEX, "2014-08-14", "GENENTECH", {"BEVACIZUMAB"}, True)
    assert ok[1] == "accepted"  # sponsor differs, but the trial names the drug
    assert rt.judge_mention("SHARED-1", _INDEX, "2014-01-01", "A CO", set(), True)[1] == "several_studies"
    assert rt.judge_mention("DR-103-301", _INDEX, "2013-01-01", "TEVA", set(), False)[1] == \
        "document_not_about_drug"


def _texts(extra: str = "") -> dict[str, list[rt.ReviewText]]:
    body = (_PAD + "\n7 Clinical/Statistical-Efficacy\nThe pivotal Study DR-103-301 enrolled "
            "3,597 women. Study 301 and Study KN555 were also cited." + extra)
    return {"204061|ORIG|1": [rt.ReviewText("Summary Review", body)],
            "204061|SUPPL|3": [rt.ReviewText("Summary Review", body)]}


def _build(texts: dict[str, list[rt.ReviewText]]) -> rt.ReviewReport:
    return rt.build(
        [("NDA204061", "ORIG", "1", "2013-03-28"), ("NDA204061", "SUPPL", "3", "2015-01-01"),
         ("NDA999999", "ORIG", "1", "2015-01-01")],
        texts, _INDEX,
        applicants={"204061": "TEVA BRANDED PHARM"},
        drug_names={"204061": {"QUARTETTE", "LEVONORGESTREL"}},
        brands={"204061": {"QUARTETTE"}},
        approval_sentences={"NDA204061|ORIG|1": "provides for the prevention of pregnancy"},
    )


def test_a_trial_is_judged_once_at_its_earliest_submission() -> None:
    report = _build(_texts())
    assert report.submissions_with_reviews == 2
    assert [u["unit_id"] for u in report.units] == ["NDA204061|ORIG|1|NCT00996580"]
    assert report.later_in_route == 1
    unit = report.units[0]
    assert unit["protocol_id"] == "DR-103-301"
    assert unit["approval"] == ("QUARTETTE · NDA204061 ORIG 1 · approved 2013-03-28 · "
                                "provides for the prevention of pregnancy")
    assert "[Summary Review · 7 Clinical/Statistical-Efficacy]" in unit["text"]
    # Bare numbers and abbreviations never become mentions.
    assert {m.protocol_id for m in report.mentions} == {"DR-103-301", "KN555"}
    assert report.outcomes["not_in_registry"] == 2  # KN555 on both submissions
    assert "role units" in rt.render(report, Counter(readable=2))


def test_heading_and_text_helpers() -> None:
    assert rt.heading_before("no headings at all", 5) == "(no heading)"



def test_collect_texts_resolves_tocs_skips_scans_and_caches_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toc = "http://x/nda/2013/204061Orig1s000TOC.html"
    bodies = {
        "http://x/sum.pdf": b"%PDF sum",
        toc: b'pdfBaseName = "204061Orig1s000"; var pdfFiles = {medR: 1, statR: 1};',
        "http://x/nda/2013/204061Orig1s000MedR.pdf": b"%PDF med",
        "http://x/scan.pdf": b"%PDF scan",
        "http://x/empty.html": b"<html>nothing</html>",
    }

    def fetch(urls: list[str], root: Path, *, offline: bool = False,
              require_pdf: object = None) -> polite_fetch.FetchResult:
        cached = {}
        for url in urls:
            if url in bodies:
                path = label_pdf.cache_path(url, root)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bodies[url])
                cached[url] = path
        missing = frozenset(u for u in urls if u not in bodies)
        return polite_fetch.FetchResult(cached, {}, missing, False)

    def extract(path: Path) -> tuple[str, int, int] | None:
        raw = path.read_bytes()
        return (_PAD if raw in (b"%PDF sum", b"%PDF med") else "x", 1, len(raw))

    monkeypatch.setattr(label_pdf, "extract_text", extract)
    docs = [
        review_docs.ReviewDocument("204061", "ORIG", "1", "21", "http://x/sum.pdf", ""),
        review_docs.ReviewDocument("204061", "ORIG", "1", "3", toc, ""),
        review_docs.ReviewDocument("204061", "ORIG", "1", "20", "http://x/scan.pdf", ""),
        review_docs.ReviewDocument("204061", "ORIG", "1", "3", "http://x/empty.html", ""),
        review_docs.ReviewDocument("204061", "ORIG", "1", "21", "http://x/gone.pdf", ""),
    ]
    texts, stats = rt.collect_texts(docs, tmp_path, fetch=fetch)
    assert [t.label for t in texts["204061|ORIG|1"]] == ["Summary Review", "Review / MedR"]
    assert stats["no_text_layer"] == 1 and stats["toc_pages"] == 2
    assert stats["toc_without_components"] == 1 and stats["component_not_fetched"] == 1
    assert stats["document_not_fetched"] == 1
    # The text is cached beside the PDF, so a second pass parses nothing.
    monkeypatch.setattr(label_pdf, "extract_text", lambda path: None)
    again, _ = rt.collect_texts(docs, tmp_path, fetch=fetch)
    assert len(again["204061|ORIG|1"]) == 2
