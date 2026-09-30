"""Deprecated modes warn every time they run (user rule, 2026-09-29)."""

from __future__ import annotations

from pathlib import Path

import pytest

import main


def test_a_deprecated_mode_prints_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main.main(["--mode", "graph-prepare", "--adjudication", str(tmp_path)])
    assert "WARNING: --mode graph-prepare is DEPRECATED" in capsys.readouterr().err


def test_every_deprecated_mode_is_a_real_mode() -> None:
    assert set(main.DEPRECATED) <= set(main.MODES)
    assert "match-prepare" not in main.DEPRECATED
