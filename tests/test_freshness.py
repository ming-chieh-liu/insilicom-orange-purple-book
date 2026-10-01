"""The stale-input guard and the Makefile's generated code dependencies."""

from __future__ import annotations

from pathlib import Path

import pytest

import main
from utility import code_deps, freshness


# ---- utility.code_deps -------------------------------------------------------

def _tree(tmp_path: Path) -> Path:
    for package in ("service", "source"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "service" / "a.py").write_text(
        "import json\nfrom service import b\nfrom source.c import thing\n")
    (tmp_path / "service" / "b.py").write_text("import source.d\n")
    (tmp_path / "source" / "c.py").write_text("thing = 1\n")
    (tmp_path / "source" / "d.py").write_text("from . import c\n")
    (tmp_path / "service" / "lone.py").write_text("")
    return tmp_path


def test_closure_follows_imports_transitively(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    names = {str(p.relative_to(root)) for p in code_deps.closure(root / "service/a.py", root)}
    assert names == {"service/a.py", "service/b.py", "service/__init__.py",
                     "source/c.py", "source/d.py"}


def test_relative_and_third_party_imports_are_ignored(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert code_deps.direct_imports(root / "source/d.py", root) == set()


def test_render_unions_every_entry_of_a_step(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    text = code_deps.render({"x": [Path("service/lone.py"), Path("source/c.py")]}, root)
    assert text == "CODE_x := service/lone.py source/c.py\n"


def test_parse_steps_rejects_a_malformed_argument() -> None:
    for bad in ("nopath", "=service/a.py", "name="):
        with pytest.raises(ValueError):
            code_deps.parse_steps([bad])


def test_main_prints_the_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path)
    monkeypatch.chdir(root)
    assert code_deps.main(["x=service/lone.py"]) == 0
    assert capsys.readouterr().out == "CODE_x := service/lone.py\n"
    assert code_deps.main(["broken"]) == 2


def test_the_real_makefile_steps_resolve() -> None:
    """Every entry module the Makefile names exists, so no step silently has no code."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    block = makefile.split("STEPS := \\", 1)[1].split("\n\n", 1)[0]
    arguments = [line.strip().rstrip("\\").strip() for line in block.splitlines() if "=" in line]
    for entries in code_deps.parse_steps(arguments).values():
        for entry in entries:
            assert entry.is_file(), entry


# ---- utility.freshness ------------------------------------------------------------

def test_check_reports_only_other_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAKELEVEL", raising=False)
    run = "x --mode indication-normalize\nx --mode candidates\n"
    assert freshness.check("candidates", "candidates", lambda _t: run) == ["indication-normalize"]
    assert freshness.check("candidates", "candidates", lambda _t: "") == []
    assert freshness.check("candidates", None, lambda _t: None) == []


def test_check_trusts_make_when_run_by_make(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKELEVEL", "1")
    assert freshness.check("candidates", None, lambda _t: "x --mode labels\n") == []


def test_make_dry_run_without_a_makefile(tmp_path: Path) -> None:
    assert freshness.make_dry_run("all", tmp_path) is None


def test_make_dry_run_ignores_a_parent_makes_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("out.txt: in.txt\n\techo --mode stepx\n")
    (tmp_path / "in.txt").write_text("")
    monkeypatch.setenv("MAKEFLAGS", "q")
    assert "--mode stepx" in (freshness.make_dry_run("out.txt", tmp_path) or "")


def test_make_dry_run_reads_make(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("out.txt: in.txt\n\techo --mode stepx\n")
    (tmp_path / "in.txt").write_text("")
    assert "--mode stepx" in (freshness.make_dry_run("out.txt", tmp_path) or "")
    assert freshness.make_dry_run("no-such-target", tmp_path) is None


# ---- the guard in main.py ------------------------------------------------------------

def test_main_refuses_a_stale_mode(monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(freshness, "check", lambda target, own: ["indication-normalize"])
    assert main.main(["--mode", "candidates"]) == 1
    err = capsys.readouterr().err
    assert "--mode candidates refused" in err and "make candidates" in err


def test_allow_stale_skips_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def recording(target: str, own: str | None) -> list[str]:
        called.append(target)
        return []

    monkeypatch.setattr(freshness, "check", recording)
    monkeypatch.setattr(main.candidates, "build", _boom)
    with pytest.raises(RuntimeError):
        main.main(["--mode", "candidates", "--allow-stale"])
    assert called == []


def test_a_non_default_tree_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def recording(target: str, own: str | None) -> list[str]:
        called.append(target)
        return []

    monkeypatch.setattr(freshness, "check", recording)
    main.main(["--mode", "match-prepare", "--adjudication", str(tmp_path),
               "--out", str(tmp_path)])
    assert called == []


def test_every_guarded_mode_is_a_real_mode() -> None:
    assert set(main.FRESHNESS) <= set(main.MODES)


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("reached the step")
