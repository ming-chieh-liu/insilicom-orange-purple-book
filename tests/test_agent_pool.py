"""Tests for the agent-pool client and the judging runner (no network)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from config.agent_pool import AgentPoolConnection
from dao.agent_pool import AgentPool, PoolError, PoolReply
from service import candidate_match as cm
from service import pool_judging as pj
from service import prompt_render as pr


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Opener:
    """Replays scripted replies; records every request."""

    def __init__(self, replies: list[object]) -> None:
        self.replies = replies
        self.seen: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _Response:
        self.seen.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(json.dumps(reply).encode())


def _pool(replies: list[object], **kw: Any) -> tuple[AgentPool, _Opener]:
    opener = _Opener(replies)
    conn = AgentPoolConnection(poll_s=0.0, **kw)
    return AgentPool(conn, "k", opener=opener, sleep=lambda s: None), opener


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, None)  # type: ignore[arg-type]


def test_a_job_is_submitted_without_tools_and_its_text_returned() -> None:
    pool, opener = _pool([
        {"id": 7, "status": "queued", "created_at": "t"},
        {"id": 7, "status": "running", "created_at": "t"},
        {"id": 7, "status": "done", "created_at": "t", "finished_at": "t",
         "result": {"result": "NCT00000001 = Yes", "is_error": False, "total_cost_usd": 0.02}},
    ])
    assert pool.run("prompt") == PoolReply(7, "done", "NCT00000001 = Yes", "", 0.02)
    data = opener.seen[0].data
    assert isinstance(data, bytes)
    body = json.loads(data)
    assert body == {"prompt": "prompt", "model": "opus", "no_tools": True, "timeout_s": 600}
    assert opener.seen[0].get_header("X-api-key") == "k"


def test_a_failed_or_errored_job_carries_no_text() -> None:
    pool, _ = _pool([{"id": 1, "status": "queued", "created_at": "t"},
                     {"id": 1, "status": "failed", "created_at": "t", "error_message": "boom"}])
    assert pool.run("p") == PoolReply(1, "failed", "", "boom", 0.0)
    pool, _ = _pool([{"id": 2, "status": "queued", "created_at": "t"},
                     {"id": 2, "status": "done", "created_at": "t", "finished_at": "t",
                      "result": {"result": "x", "is_error": True, "subtype": "error_max_turns"}}])
    assert pool.run("p").error == "error_max_turns"


def test_server_errors_are_retried_and_client_errors_are_not() -> None:
    pool, opener = _pool([_http(503), urllib.error.URLError("down"),
                          {"id": 3, "status": "queued", "created_at": "t"},
                          {"id": 3, "status": "done", "created_at": "t", "finished_at": "t",
                           "result": {"result": "ok"}}])
    assert pool.run("p").text == "ok" and len(opener.seen) == 4
    pool, _ = _pool([_http(401)])
    reply = pool.run("p")
    assert reply.status == "unreachable" and "HTTP 401" in reply.error
    pool, _ = _pool([_http(500), _http(500), _http(500)])
    assert "after 3 attempts" in pool.run("p").error
    pool, _ = _pool([["not", "a", "dict"]])
    assert "unexpected reply" in pool.run("p").error


def test_a_job_that_never_finishes_is_overdue() -> None:
    ticks = iter([0.0, 0.0, 10_000.0])
    opener = _Opener([{"id": 4, "status": "queued", "created_at": "t"}] * 3)
    pool = AgentPool(AgentPoolConnection(poll_s=0.0), "k", opener=opener,
                     sleep=lambda s: None, clock=lambda: next(ticks))
    assert pool.wait(4).status == "overdue"


def test_capacity_and_a_missing_key() -> None:
    pool, _ = _pool([{"tier": "batch", "available": True}])
    assert pool.capacity()["available"] is True
    with pytest.raises(PoolError, match="no API key"):
        AgentPool(AgentPoolConnection(), "")


def test_the_key_comes_from_the_environment_or_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "pp.env"
    env.write_text("# x\nexport OTHER=1\nexport AGENT_POOL_API_KEY='abc'\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_POOL_API_KEY", raising=False)
    assert AgentPoolConnection(env_file=env).api_key() == "abc"
    assert AgentPoolConnection(env_file=tmp_path / "none").api_key() == ""
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert AgentPoolConnection(env_file=env).api_key() == ""
    monkeypatch.setenv("AGENT_POOL_API_KEY", "fromenv")
    assert AgentPoolConnection(env_file=env).api_key() == "fromenv"


# ---------------------------------------------------------------------------
# The judging runner
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.sent: list[str] = []

    def run(self, prompt: str) -> PoolReply:
        self.sent.append(prompt)
        text = self.answers.get(prompt, "")
        return PoolReply(len(self.sent), "done" if text else "failed", text,
                         "" if text else "boom", 0.01)


def _requests() -> list[pr.Rendered]:
    return [
        pr.Rendered("U1#T1@a", cm.EXCERPT_PROMPT, 3, "p1", ("NCT00000001", "NCT00000002")),
        pr.Rendered("U2#T1@b", cm.EXCERPT_PROMPT, 3, "p2", ("NCT00000003",)),
        pr.Rendered("U3#T1@c", cm.LITERATURE_PROMPT, 2, "p3", ("NCT00000004",)),
        pr.Rendered("BLA1|ORIG|1|NCT1", "review-trial-role", 2, "p4"),
        pr.Rendered("BLA1|ORIG|1|NCT2", "review-trial-role", 2, "p5"),
    ]


def test_valid_answers_are_recorded_and_the_rest_left_undecided(tmp_path: Path) -> None:
    pool = _FakePool({"p1": "NCT00000001 = Yes\nNCT00000002 = No",
                      "p2": "Probably NCT00000003",        # malformed -> rejected
                      "p4": "PIVOTAL", "p5": "MAYBE"})     # p3 fails, p5 rejected
    raw = tmp_path / "round-01" / "batch-001-A.txt"
    report = pj.judge(_requests(), pool, raw, "A", 4)
    assert (report.sent, report.answered, report.rejected, report.failed) == (5, 2, 2, 1)
    lines = sorted(raw.read_text().splitlines())
    assert lines == ["BLA1|ORIG|1|NCT1 = PIVOTAL", "U1#T1@a | NCT00000001 = Yes",
                     "U1#T1@a | NCT00000002 = No"]
    log = [json.loads(x) for x in raw.with_name(raw.name + ".jobs.jsonl").read_text().splitlines()]
    assert {x["outcome"] for x in log} == {"answered", "rejected", "failed"}
    assert "p1" not in raw.with_name(raw.name + ".jobs.jsonl").read_text()  # no payload logged
    assert "answered (valid reply recorded)            2" in pj.render(report, "A", raw)


def test_a_rerun_sends_only_what_is_still_undecided(tmp_path: Path) -> None:
    raw = tmp_path / "batch-001-B.txt"
    raw.write_text("U1#T1@a | NCT00000001 = Yes\nU1#T1@a | NCT00000002 = No\n"
                   "BLA1|ORIG|1|NCT1 = OTHER\nU2#T1@b | NCT00000009 = No\n", encoding="utf-8")
    pool = _FakePool({"p2": "NCT00000003 = No"})
    report = pj.judge(_requests(), pool, raw, "B", 2, limit=1)
    assert report.already_answered == 2 and pool.sent == ["p2"] and report.answered == 1


def test_the_raw_file_must_name_its_judge(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end"):
        pj.judge(_requests(), _FakePool({}), tmp_path / "batch-001-A.txt", "B", 1)
    with pytest.raises(ValueError, match="one of"):
        pj.judge(_requests(), _FakePool({}), tmp_path / "batch-001-D.txt", "D", 1)


def test_a_several_case_role_job_records_every_case_or_none(tmp_path: Path) -> None:
    jobs = [pr.Rendered("U1+2", "label-trial-role", 2, "j1", ("U1", "U2")),
            pr.Rendered("U3+2", "label-trial-role", 2, "j2", ("U3", "U4"))]
    pool = _FakePool({"j1": "CASE 1 = PIVOTAL\nCASE 2 = FAILED",
                      "j2": "CASE 1 = OTHER"})                     # incomplete -> rejected
    raw = tmp_path / "lrole-batch-001-A.txt"
    report = pj.judge(jobs, pool, raw, "A", 2)
    assert (report.answered, report.rejected) == (1, 1)
    assert raw.read_text().splitlines() == ["U1 = PIVOTAL", "U2 = FAILED"]
    # A re-run skips the complete job and sends the other again.
    again = _FakePool({"j2": "CASE 1 = OTHER\nCASE 2 = SUPPORTING"})
    assert pj.judge(jobs, again, raw, "A", 2).already_answered == 1 and again.sent == ["j2"]
    assert "U4 = SUPPORTING" in raw.read_text()


class _SequencePool:
    """Answers each prompt from a queue: a bad reply first, then a good one."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.sent: list[str] = []

    def run(self, prompt: str) -> PoolReply:
        self.sent.append(prompt)
        queue = self.answers.get(prompt, [])
        text = queue.pop(0) if queue else ""
        return PoolReply(len(self.sent), "done" if text else "failed", text, "", 0.01)


def test_a_bad_reply_is_sent_again_until_valid_or_out_of_retries(tmp_path: Path) -> None:
    jobs = [pr.Rendered("U1+2", "label-trial-role", 2, "j1", ("U1", "U2")),
            pr.Rendered("U3+2", "label-trial-role", 2, "j2", ("U3", "U4")),
            pr.Rendered("U5+1", "label-trial-role", 2, "j3", ("U5",))]
    pool = _SequencePool({"j1": ["CASE 1 = PIVOTAL", "CASE 1 = PIVOTAL\nCASE 2 = OTHER"],
                          "j2": ["", "Sure!", "CASE 1 = OTHER"],     # never valid
                          "j3": ["CASE 1 = OTHER"]})
    raw = tmp_path / "x-A.txt"
    report = pj.judge(jobs, pool, raw, "A", 2, retries=2)
    assert (report.answered, report.resent, report.rejected, report.failed) == (2, 3, 1, 0)
    assert pool.sent.count("j2") == 3 and pool.sent.count("j1") == 2
    assert sorted(raw.read_text().splitlines()) == ["U1 = PIVOTAL", "U2 = OTHER", "U5 = OTHER"]
    attempts = [json.loads(x)["attempt"]
                for x in raw.with_name(raw.name + ".jobs.jsonl").read_text().splitlines()]
    assert max(attempts) == 3
    assert "sent again (bad or no reply)               3" in pj.render(report, "A", raw)
    none = pj.judge(jobs, _SequencePool({}), tmp_path / "y-A.txt", "A", 1, retries=0)
    assert (none.resent, none.failed) == (0, 3)


def test_a_several_tier_job_records_every_candidate_or_none(tmp_path: Path) -> None:
    cases = (("U1#T1@a", ("NCT00000001", "NCT00000002")), ("U2#T1@b", ("NCT00000003",)))
    jobs = [pr.Rendered("U1#T1@a+2", pr.TIER_BATCH, 1, "j1", (), cases)]
    raw = tmp_path / "round-01" / "batch-001-B.txt"
    pool = _SequencePool({"j1": ["CASE 1 | NCT00000001 = Yes\nCASE 2 | NCT00000003 = No",
                                 "CASE 1 | NCT00000001 = Yes\nCASE 1 | NCT00000002 = No\n"
                                 "CASE 2 | NCT00000003 = No"]})
    report = pj.judge(jobs, pool, raw, "B", 1)
    assert (report.answered, report.resent) == (1, 1)
    assert raw.read_text().splitlines() == [
        "U1#T1@a | NCT00000001 = Yes", "U1#T1@a | NCT00000002 = No", "U2#T1@b | NCT00000003 = No"]
    assert pj.answered_ids(raw, jobs) == {"U1#T1@a+2"}


def test_the_tiebreak_list_renders_each_tier_once(tmp_path: Path) -> None:
    tiers = tmp_path / "tiers.jsonl"
    tier = cm.TierUnit("U#T1@abc", "U", 1, "approval", "excerpt", "name", "616",
                       ("NCT00000001", "NCT00000002"), "1. NCT00000001")
    cm.write_tiers([tier], tiers)
    needed = tmp_path / "candidate_match_tiebreak_needed.txt"
    needed.write_text("U#T1@abc | NCT00000001\nU#T1@abc | NCT00000002\n", encoding="utf-8")
    real = Path(__file__).resolve().parents[1] / "docs" / "prompts"
    assert [r.request_id for r in pr.render_tier_batch(needed, tiers, real)] == ["U#T1@abc"]


def test_the_pool_judge_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    import main

    requests = tmp_path / "batch-001.requests.jsonl"
    pr.write_requests(_requests()[:1], requests)

    class _Stub:
        def __init__(self, connection: AgentPoolConnection, key: str) -> None:
            if not key:
                raise PoolError("no API key")

        def run(self, prompt: str) -> PoolReply:
            return PoolReply(1, "done", "NCT00000001 = Yes\nNCT00000002 = No", "", 0.0)

        def capacity(self) -> dict[str, object]:
            return {"available": slots[0] > 0, "job_slots": slots[0], "eligible_accounts": 1}

    slots = [3]

    monkeypatch.setattr(main, "AgentPool", _Stub)
    monkeypatch.setenv("AGENT_POOL_API_KEY", "k")
    raw = tmp_path / "batch-001-A.txt"
    argv = ["--mode", "pool-judge", "--requests", str(requests), "--judge", "A",
            "--raw-file", str(raw)]
    assert main.main(argv) == 0 and raw.read_text().count("U1#T1@a") == 2
    assert main.main([*argv[:-1], str(tmp_path / "x-B.txt")]) == 1
    assert main.main(["--mode", "pool-judge"]) == 1
    slots[0] = 0
    assert main.main(argv) == 1
    assert "no free seat" in capsys.readouterr().out
    slots[0] = 3
    monkeypatch.setenv("AGENT_POOL_API_KEY", "")
    monkeypatch.setattr(main.AgentPoolConnection, "api_key", lambda self: "")
    assert main.main(argv) == 1
    assert "not sent" in capsys.readouterr().out
