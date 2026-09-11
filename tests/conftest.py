"""Shared fixtures: the real data tree's availability.

Two kinds of test live in this suite. Most are **pure** — they build a tiny
table in ``tmp_path`` and assert on it, so they run in milliseconds and pass on
a machine that has never downloaded anything. A few are marked ``realdata`` and
read ``fda_raw/`` (2.9 GB, gitignored) to assert the corpus-wide numbers that
``docs/drug-identity.md`` quotes.

The pure tests carry the coverage thresholds by themselves. The ``realdata``
tests are the proof, not the safety net, so their absence is a skip and never a
failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The repository root, found from this file rather than the cwd so the suite
#: runs the same from any directory.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: The bulk data tree. Gitignored and rebuildable, so it may simply be absent.
FDA_RAW = REPO_ROOT / "fda_raw"


@pytest.fixture(scope="module")
def fda_raw_root() -> Path:
    """The real data tree, or skip. Module-scoped so a census is read once."""
    if not FDA_RAW.is_dir():
        pytest.skip(f"{FDA_RAW} is absent; realdata tests need the downloaded corpus")
    return FDA_RAW
