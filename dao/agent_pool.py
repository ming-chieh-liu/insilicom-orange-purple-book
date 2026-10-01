"""The agent pool's job API, behind one injected client.

``POST /api/v1/jobs`` queues one prompt as a fresh CLI session and returns its
id; ``GET /api/v1/jobs/{id}`` reports ``status`` and, when finished, ``result``
(the CLI's JSON: ``result.result`` is the reply text, ``result.is_error`` its
failure flag). Auth is the ``X-API-Key`` header.

**One client per process, injected.** ``main`` builds one :class:`AgentPool`
and hands it to the judging service; nothing else opens a connection. The
HTTP opener is injectable too, which is what the tests pass.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, NamedTuple

from config.agent_pool import AgentPoolConnection
from utility.logging import get_logger

_log = get_logger(__name__)

#: Statuses of a job that has not finished yet.
ACTIVE = frozenset({"queued", "pending", "running", "dispatched", "starting"})


class PoolError(RuntimeError):
    """The pool could not be reached or refused the request."""


class PoolReply(NamedTuple):
    """What one job ended with. ``text`` is "" unless it finished cleanly."""

    job_id: int
    status: str
    text: str
    error: str
    cost_usd: float


Opener = Callable[..., Any]


class AgentPool:
    """Submit prompts and collect replies, through one injected opener."""

    def __init__(self, connection: AgentPoolConnection, api_key: str,
                 opener: Opener = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not api_key:
            raise PoolError(f"no API key: set {connection.key_env} or add it to "
                            f"{connection.env_file}")
        self._connection = connection
        self._key = api_key
        self._open = opener
        self._sleep = sleep
        self._clock = clock

    def _call(self, method: str, path: str, body: dict[str, object] | None = None,
              attempts: int = 3) -> dict[str, Any]:
        """One JSON call, retried on network errors and 5xx; 4xx is final."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last = ""
        for attempt in range(attempts):
            request = urllib.request.Request(
                self._connection.base_url + path, data=data, method=method,
                headers={"X-API-Key": self._key, "Content-Type": "application/json"})
            try:
                with self._open(request, timeout=self._connection.http_timeout_s) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise PoolError(f"{method} {path}: unexpected reply {parsed!r:.200}")
                    return parsed
            except urllib.error.HTTPError as error:
                last = f"HTTP {error.code}"
                if error.code < 500:
                    raise PoolError(f"{method} {path}: {last}") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = str(error)
            self._sleep(2.0 * (attempt + 1))
        raise PoolError(f"{method} {path}: {last} after {attempts} attempts")

    def capacity(self) -> dict[str, Any]:
        """``GET /api/v1/capacity``: what the pool can take from us right now."""
        return self._call("GET", "/api/v1/capacity")

    def submit(self, prompt: str) -> int:
        """Queue one prompt; the job id."""
        job = self._call("POST", "/api/v1/jobs", {
            "prompt": prompt, "model": self._connection.model, "no_tools": True,
            "timeout_s": self._connection.timeout_s,
        })
        return int(job["id"])

    def wait(self, job_id: int) -> PoolReply:
        """Poll a job until it finishes or overruns its own timeout plus a margin."""
        deadline = self._clock() + self._connection.timeout_s + 600
        while True:
            job = self._call("GET", f"/api/v1/jobs/{job_id}")
            status = str(job.get("status") or "")
            if status not in ACTIVE or job.get("finished_at"):
                return _reply(job_id, status, job)
            if self._clock() > deadline:
                return PoolReply(job_id, "overdue", "", "no result before the deadline", 0.0)
            self._sleep(self._connection.poll_s)

    def run(self, prompt: str) -> PoolReply:
        """Submit and wait. A pool that cannot be reached is a failed reply, not a crash."""
        try:
            return self.wait(self.submit(prompt))
        except PoolError as error:
            _log.warning("pool job failed: %s", error)
            return PoolReply(0, "unreachable", "", str(error), 0.0)


def _reply(job_id: int, status: str, job: dict[str, Any]) -> PoolReply:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    assert isinstance(result, dict)
    cost = float(result.get("total_cost_usd") or 0.0)
    text = result.get("result")
    if status == "done" and not result.get("is_error") and isinstance(text, str):
        return PoolReply(job_id, status, text, "", cost)
    error = str(job.get("error_message") or result.get("subtype") or "no reply text")
    return PoolReply(job_id, status or "unknown", "", error, cost)


__all__ = ["ACTIVE", "AgentPool", "PoolError", "PoolReply"]
