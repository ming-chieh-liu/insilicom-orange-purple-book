"""Send rendered prompts to the agent pool as one judge, and record the answers.

The input is a ``<batch>.requests.jsonl`` from ``--mode render-prompts``: one
complete prompt per tier or role unit. This module sends each as its own job
(at most :attr:`AgentPoolConnection.max_in_flight` at once), checks every reply
strictly (:func:`service.prompt_render.tier_reply_lines` /
:func:`~service.prompt_render.role_reply_line`), and appends only valid answers
to the judge's raw file — the file the existing ``*-combine`` modes read.

Rules carried from the runbooks:

* **Never record what was not returned.** A failed job, an overdue job or a
  malformed reply writes no answer; the request stays undecided and a re-run
  sends it again.
* **Resumable.** A request whose answer is already in the raw file is skipped.
* **Independent judges.** Judge A, B and C are separate runs into separate raw
  files; each job is a fresh session with no tools. A role job may hold
  several units (``role-case-batch`` wrapper, 2026-10-02) — each case the
  unit's own prompt verbatim; the reply must answer every case or none is
  recorded.
* **The orchestrator never reads the payload.** The text goes from the
  requests file to the pool; only ids, counts and statuses are reported.

Every job — answered or not — is logged to ``<raw file>.jobs.jsonl`` (request
id, judge, job id, status, whether the reply was valid, cost), never to the
raw file itself.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Protocol

from dao.agent_pool import PoolReply
from service import candidate_match, prompt_render
from service.role_judging import ROLES

JUDGES = ("A", "B", "C")
_ROLE_LINE = re.compile(r"^(?P<unit>.+?)\s*=\s*(?P<role>[A-Z_]+)\s*$")


class Pool(Protocol):
    """What this module needs from :class:`dao.agent_pool.AgentPool`."""

    def run(self, prompt: str) -> PoolReply: ...


class PoolReport(NamedTuple):
    """One judge's pass over one requests file."""

    requests: int
    already_answered: int
    sent: int
    answered: int
    rejected: int        # a reply came back but did not pass the strict check
    failed: int          # no reply: job failed, overdue or pool unreachable
    cost_usd: float
    statuses: tuple[tuple[str, int], ...]
    resent: int = 0      # jobs sent again after a failed strict check or no reply


def is_role(prompt: str) -> bool:
    """Whether a prompt is a role task (one option) rather than per-candidate matching."""
    return prompt not in candidate_match.PROMPT_VERSIONS


def answered_ids(raw_file: Path, requests: Iterable[prompt_render.Rendered]) -> set[str]:
    """The request ids whose answer is already complete in ``raw_file``."""
    if not raw_file.is_file():
        return set()
    lines = raw_file.read_text(encoding="utf-8").splitlines()
    tiers: dict[str, set[str]] = {}
    roles: set[str] = set()
    for line in lines:
        match = candidate_match.ANSWER_LINE.match(line.strip())
        if match:
            tiers.setdefault(match["tier"].strip(), set()).add(match["nct"])
            continue
        role = _ROLE_LINE.match(line.strip())
        if role and role["role"] in ROLES:
            roles.add(role["unit"])
    done: set[str] = set()
    for request in requests:
        if request.cases:
            if all(set(ncts) <= tiers.get(tier_id, set()) for tier_id, ncts in request.cases):
                done.add(request.request_id)
        elif is_role(request.prompt):
            if set(request.expects or (request.request_id,)) <= roles:
                done.add(request.request_id)
        elif set(request.expects) <= tiers.get(request.request_id, set()):
            done.add(request.request_id)
    return done


#: How many more times a request whose reply failed the strict check (or never
#: came) is sent again in the same run, each time as a fresh job (2026-10-02).
RETRIES: int = 2


def judge(requests: list[prompt_render.Rendered], pool: Pool, raw_file: Path,
          judge_letter: str, max_in_flight: int, limit: int | None = None,
          retries: int = RETRIES) -> PoolReport:
    """Send every unanswered request; append valid answers as they arrive.

    A reply that fails the strict check (malformed, incomplete, an option not
    offered) or a job that returns nothing records nothing, and the request is
    sent again as a fresh job, up to ``retries`` more times. What is still
    undecided after that is reported, never guessed; a later run sends it again.
    """
    if judge_letter not in JUDGES:
        raise ValueError(f"judge must be one of {JUDGES}, not {judge_letter!r}")
    if not raw_file.stem.endswith(f"-{judge_letter}"):
        raise ValueError(f"{raw_file.name}: a judge {judge_letter} raw file must end "
                         f"in -{judge_letter}.txt, or combine would read it as another judge")
    done = answered_ids(raw_file, requests)
    todo = [r for r in requests if r.request_id not in done]
    if limit is not None:
        todo = todo[:limit]
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_log = raw_file.with_name(raw_file.name + ".jobs.jsonl")
    lock = threading.Lock()
    tally: Counter[str] = Counter()
    cost = [0.0]

    def one(request: prompt_render.Rendered, attempt: int) -> str:
        reply = pool.run(request.text)
        lines: list[str] | None = None
        if reply.text:
            if request.cases:
                lines = prompt_render.tier_batch_reply_lines(request, reply.text)
            elif is_role(request.prompt) and request.expects:
                lines = prompt_render.role_batch_reply_lines(request, reply.text, ROLES)
            elif is_role(request.prompt):
                line = prompt_render.role_reply_line(request, reply.text, ROLES)
                lines = [line] if line else None
            else:
                lines = prompt_render.tier_reply_lines(request, reply.text)
        outcome = "answered" if lines else "rejected" if reply.text else "failed"
        with lock:
            if lines:
                with raw_file.open("a", encoding="utf-8") as handle:
                    handle.write("".join(line + "\n" for line in lines))
            with jobs_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "request_id": request.request_id, "judge": judge_letter,
                    "prompt": request.prompt, "version": request.version,
                    "job_id": reply.job_id, "status": reply.status, "outcome": outcome,
                    "error": reply.error, "cost_usd": reply.cost_usd, "attempt": attempt,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }) + "\n")
            tally[outcome] += 1
            tally["status:" + reply.status] += 1
            cost[0] += reply.cost_usd
        return outcome

    pending = todo
    resent = 0
    final: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_in_flight)) as executor:
        for attempt in range(1, max(0, retries) + 2):
            if not pending:
                break
            if attempt > 1:
                resent += len(pending)
            outcomes = list(executor.map(one, pending, [attempt] * len(pending)))
            final.update((r.request_id, o) for r, o in zip(pending, outcomes))
            pending = [r for r, o in zip(pending, outcomes) if o != "answered"]
    still = Counter(final[r.request_id] for r in pending)
    return PoolReport(
        requests=len(requests), already_answered=len(done), sent=len(todo),
        answered=tally["answered"],
        rejected=still["rejected"], failed=still["failed"],
        cost_usd=cost[0],
        statuses=tuple(sorted((k[7:], v) for k, v in tally.items() if k.startswith("status:"))),
        resent=resent,
    )


def render(report: PoolReport, judge_letter: str, raw_file: Path) -> str:
    """The pass report, as ``--mode pool-judge`` prints it."""
    return "\n".join([
        f"Agent pool — judge {judge_letter} -> {raw_file}",
        "",
        f"  requests in file                       {report.requests:>9,}",
        f"    already answered (skipped)           {report.already_answered:>9,}",
        f"    sent this run                        {report.sent:>9,}",
        f"      answered (valid reply recorded)    {report.answered:>9,}",
        f"      sent again (bad or no reply)       {report.resent:>9,}",
        f"      still rejected (malformed reply)   {report.rejected:>9,}",
        f"      still failed (no reply)            {report.failed:>9,}",
        f"  cost this run (USD)                    {report.cost_usd:>9.2f}",
        "  job statuses: " + (", ".join(f"{s or '?'} {n}" for s, n in report.statuses) or "none"),
        "",
        "  Requests still rejected or failed stay undecided; re-run the same command to retry them.",
    ])


__all__ = ["JUDGES", "RETRIES", "Pool", "PoolReport", "answered_ids", "is_role", "judge", "render"]
