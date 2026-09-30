"""Refuse to run a step on stale inputs, using the Makefile's dependencies.

The Makefile is the one declaration of what each step reads and which code
writes it. Before a mode runs, ``make -n <its target>`` lists every command
make would need to bring that target up to date. If any of those commands is
a *different* mode, an input is stale — its code or its own inputs changed
after it was written — and running now would build on it silently, which is
how P3 once ran on a ``submission_indications.tsv`` older than its code.

Consumers of agent work (``match-prepare`` reads ``candidates.jsonl``) pass no
own mode: their input must need no command at all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

_MODE = re.compile(r"--mode ([a-z0-9-]+)")

#: ``make -n`` output for a target, or None when make cannot be asked.
DryRun = Callable[[str], "str | None"]


def make_dry_run(target: str, root: Path = Path(".")) -> str | None:
    """What ``make -n target`` would run, or None if there is no make or Makefile."""
    if not (root / "Makefile").is_file() or shutil.which("make") is None:
        return None
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def stale_modes(dry_run_output: str, own_mode: str | None) -> list[str]:
    """The modes, other than ``own_mode``, that ``make -n`` output would run.

    >>> out = "python main.py --mode indication-normalize\\npython main.py --mode candidates\\n"
    >>> stale_modes(out, "candidates")
    ['indication-normalize']
    >>> stale_modes("python main.py --mode candidates\\n", "candidates")
    []
    >>> stale_modes("python main.py --mode candidates\\n", None)
    ['candidates']
    """
    found: list[str] = []
    for mode in _MODE.findall(dry_run_output):
        if mode != own_mode and mode not in found:
            found.append(mode)
    return found


def check(target: str, own_mode: str | None, dry_run: DryRun = make_dry_run) -> list[str]:
    """Stale upstream modes for ``target``; empty when current or unknowable.

    Returns nothing when run by make itself (``MAKELEVEL`` is set), since make
    has already ordered the steps, and when make cannot be asked.
    """
    if os.environ.get("MAKELEVEL"):
        return []
    output = dry_run(target)
    if output is None:
        return []
    return stale_modes(output, own_mode)


def message(mode: str, stale: list[str], make_target: str) -> str:
    """The refusal a user sees, naming what to run.

    >>> print(message("candidates", ["indication-normalize"], "candidates"))
    --mode candidates refused: its inputs are stale — make would first run --mode indication-normalize.
    Run `make candidates` to rebuild in order, or pass --allow-stale to run anyway.
    """
    steps = ", ".join(f"--mode {m}" for m in stale)
    return (f"--mode {mode} refused: its inputs are stale — make would first run {steps}.\n"
            f"Run `make {make_target}` to rebuild in order, or pass --allow-stale to run anyway.")
