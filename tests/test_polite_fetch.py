"""Tests for the throttled, circuit-broken bulk fetcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from source import label_pdf, polite_fetch


def _stub(monkeypatch: pytest.MonkeyPatch, answers: dict[str, tuple[bytes | None, str]]) -> list[str]:
    asked: list[str] = []

    def fetch_status(url: str, root: Path) -> tuple[Path | None, str]:
        asked.append(url)
        body, reason = answers.get(url, (None, label_pdf.NOT_FOUND))
        if body is None:
            return None, reason
        path = label_pdf.cache_path(url, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path, ""

    monkeypatch.setattr(label_pdf, "fetch_status", fetch_status)
    return asked


def test_requests_are_spaced_and_the_cache_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _stub(monkeypatch, {"a": (b"%PDF a", ""), "b": (b"%PDF b", "")})
    sleeps: list[float] = []
    first = polite_fetch.fetch_all(["a", "b", "a"], tmp_path, interval=0.5, sleep=sleeps.append)
    assert set(first.cached) == {"a", "b"} and sleeps == [0.5] and asked == ["a", "b"]
    asked.clear()
    again = polite_fetch.fetch_all(["a", "b"], tmp_path, sleep=sleeps.append)
    assert asked == [] and set(again.cached) == {"a", "b"}


def test_a_non_pdf_answer_is_discarded_unless_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"page.html": (b"<html>", ""), "bad.pdf": (b"<html>404", "")})
    result = polite_fetch.fetch_all(["page.html", "bad.pdf", "gone.pdf"], tmp_path,
                                    require_pdf=lambda url: url.endswith(".pdf"), sleep=lambda s: None)
    assert set(result.cached) == {"page.html"}
    assert result.failed == {"bad.pdf": "not_pdf", "gone.pdf": "not_found"}
    assert not label_pdf.is_cached("bad.pdf", tmp_path)


def test_repeated_refusals_trip_the_breaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = [f"u{i}" for i in range(10)]
    asked = _stub(monkeypatch, {u: (None, label_pdf.FORBIDDEN) for u in urls})
    result = polite_fetch.fetch_all(urls, tmp_path, breaker=3, sleep=lambda s: None)
    assert asked == urls[:3]
    assert result.tripped and result.refused == frozenset(urls) and result.failed == {}


def test_offline_asks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _stub(monkeypatch, {"a": (b"%PDF", "")})
    result = polite_fetch.fetch_all(["a"], tmp_path, offline=True)
    assert asked == [] and result.refused == frozenset({"a"})
    assert not polite_fetch.is_pdf(tmp_path / "absent")
