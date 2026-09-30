"""The project modules a pipeline step's code rests on, for the Makefile.

A step is rebuilt when any module it imports changes, directly or through
another project module. Listing those by hand is how staleness gets in —
``normalize`` imports ``approval_events`` and ``identity_census``, which no
one would think to write down — so they are read from the import statements.

Only the project's own packages count (``config``, ``dao``, ``service``,
``source``, ``utility``); the standard library and third-party packages do not
change between runs. ``main.py`` is deliberately not a dependency: every mode
lives in it, so counting it would make any edit rebuild everything.

Run as ``python -m utility.code_deps name=path/a.py[,path/b.py] ...``; it prints
one ``CODE_<name> := <files>`` line per step, which the Makefile includes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: The packages whose modules are project code.
PACKAGES: tuple[str, ...] = ("config", "dao", "service", "source", "utility")


def _module_path(name: str, root: Path) -> Path | None:
    """``service.candidates`` -> ``service/candidates.py`` if it is project code."""
    parts = name.split(".")
    if parts[0] not in PACKAGES:
        return None
    path = root.joinpath(*parts).with_suffix(".py")
    if path.is_file():
        return path
    package = root.joinpath(*parts) / "__init__.py"
    return package if package.is_file() else None


def direct_imports(path: Path, root: Path) -> set[Path]:
    """The project modules ``path`` imports itself.

    >>> import tempfile
    >>> tmp = Path(tempfile.mkdtemp())
    >>> (tmp / "service").mkdir()
    >>> _ = (tmp / "service" / "a.py").write_text("from service import b\\nimport os\\n")
    >>> _ = (tmp / "service" / "b.py").write_text("")
    >>> sorted(p.name for p in direct_imports(tmp / "service" / "a.py", tmp))
    ['b.py']
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[Path] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            # `from service import b` may name a module or an attribute.
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            module = _module_path(name, root)
            if module is not None:
                found.add(module)
    found.discard(path)
    return found


def closure(entry: Path, root: Path) -> list[Path]:
    """``entry`` and every project module it reaches through imports, sorted."""
    seen: set[Path] = set()
    todo = [entry]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        todo.extend(direct_imports(current, root) - seen)
    return sorted(seen)


def render(steps: dict[str, list[Path]], root: Path) -> str:
    """One Makefile variable per step: ``CODE_<name> := <files>``."""
    lines = []
    for name, entries in steps.items():
        reached: set[Path] = set()
        for entry in entries:
            reached.update(closure(root / entry, root))
        files = " ".join(str(p.relative_to(root)) for p in sorted(reached))
        lines.append(f"CODE_{name} := {files}")
    return "\n".join(lines) + "\n"


def parse_steps(arguments: list[str]) -> dict[str, list[Path]]:
    """``["a=x.py,y.py"]`` -> ``{"a": [Path("x.py"), Path("y.py")]}``.

    A step names every module ``main.py`` calls for that mode, comma-joined.

    >>> parse_steps(["candidates=service/candidates.py,source/mesh_descriptors.py"])
    {'candidates': [PosixPath('service/candidates.py'), PosixPath('source/mesh_descriptors.py')]}
    """
    steps: dict[str, list[Path]] = {}
    for argument in arguments:
        name, sep, paths = argument.partition("=")
        entries = [Path(p) for p in paths.split(",") if p]
        if not sep or not name or not entries:
            raise ValueError(f"expected name=path[,path...], got {argument!r}")
        steps[name] = entries
    return steps


def main(argv: list[str]) -> int:
    """Print the dependency variables for the steps named on the command line."""
    try:
        steps = parse_steps(argv)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    sys.stdout.write(render(steps, Path.cwd()))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the Makefile
    raise SystemExit(main(sys.argv[1:]))
