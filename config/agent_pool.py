"""How to reach the Insilicom agent pool. No client is built here.

Kept out of :mod:`config.settings` on purpose: every pipeline stage imports
that module, so a change there marks the whole pipeline stale (D38 Makefile),
while only judging reads this one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass(frozen=True)
class AgentPoolConnection:
    """The pool's address, the judge settings, and where the key lives.

    The key is read from the environment, else from ``env_file`` (the user's
    ``~/.claude/pp.env``, never committed). Each job is a fresh CLI session
    with no tools, so a judge can answer only from the prompt it is sent.
    """

    base_url: str = "http://galaxy4:8093"
    key_env: str = "AGENT_POOL_API_KEY"
    env_file: Path = field(default_factory=lambda: Path.home() / ".claude" / "pp.env")
    model: str = "opus"
    #: Seconds the pool lets one job run; a 40k-character tier fits easily.
    timeout_s: int = 600
    #: The user's cap (2026-09-30): never more than 32 of our jobs in flight.
    max_in_flight: int = 32
    #: Seconds between status polls of one job.
    poll_s: float = 5.0
    #: Seconds one HTTP call may take.
    http_timeout_s: float = 30.0

    def api_key(self) -> str:
        """The key, or "" when neither the environment nor the file has it."""
        value = os.environ.get(self.key_env, "")
        if value:
            return value
        if not self.env_file.is_file():
            return ""
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            match = _ASSIGN.match(line)
            if match and match.group(1) == self.key_env:
                return match.group(2).strip().strip("'\"")
        return ""


__all__ = ["AgentPoolConnection"]
