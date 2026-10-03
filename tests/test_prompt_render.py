"""Tests for rendering judge prompts as complete, file-independent texts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service import candidate_match as cm
from service import prompt_render as pr
from service import review_role
from service.role_judging import ROLES

_REAL = Path(__file__).resolve().parents[1] / "docs" / "prompts"


def _tier(**kw: object) -> cm.TierUnit:
    base: dict[str, object] = dict(
        tier_id="U#T1@abc", unit_id="U", tier=1,
        approval="KEYTRUDA (pembrolizumab) · BLA125514 SUPPL 35 · approved 2018-08-20",
        excerpt="KEYNOTE-189 randomized 616 patients", trial_name="KEYNOTE-189",
        enrollment="616", candidate_ids=("NCT02578680",),
        candidates="1. NCT02578680\n   Title: {{not a field}}",
    )
    base.update(kw)
    return cm.TierUnit(**base)  # type: ignore[arg-type]


def test_every_judged_prompt_file_agrees_with_the_code_version() -> None:
    """A version drift between docs/prompts and the code refuses to render."""
    for name, version in cm.PROMPT_VERSIONS.items():
        assert pr.load(name, _REAL).version == version
    assert pr.load("review-trial-role", _REAL).version == review_role.PROMPT_VERSION


def test_a_tier_renders_with_no_placeholder_left() -> None:
    rendered = pr.render_tier(_tier(), _REAL)
    assert rendered.request_id == "U#T1@abc" and rendered.version == cm.PROMPT_VERSION
    assert "KEYNOTE-189 randomized 616 patients" in rendered.text
    assert "{{approval}}" not in rendered.text and "{{candidates}}" not in rendered.text
    # a value is inserted as-is, never expanded
    assert "Title: {{not a field}}" in rendered.text
    assert rendered.expects == ("NCT02578680",)


def test_each_route_renders_with_its_own_prompt() -> None:
    nolabel = pr.render_tier(_tier(prompt=cm.NOLABEL_PROMPT, drug="KEYTRUDA",
                                   indication="NSCLC", approval_date="2018-08-20",
                                   product=cm.NOT_A_COPY), _REAL)
    literature = pr.render_tier(_tier(prompt=cm.LITERATURE_PROMPT,
                                      label_block="Section 14 says …"), _REAL)
    assert "NSCLC" in nolabel.text and "Section 14 says …" in literature.text
    assert "THIS PRODUCT: not a new form" in nolabel.text
    assert (nolabel.version, literature.version) == (cm.NOLABEL_PROMPT_VERSION,
                                                     cm.LITERATURE_PROMPT_VERSION)


def test_a_missing_field_refuses_to_render() -> None:
    with pytest.raises(pr.RenderError, match="excerpt"):
        pr.render_tier(_tier(excerpt=""), _REAL)


def _prompt_file(tmp_path: Path, version: int, body: str = "Hi {{approval}}") -> Path:
    (tmp_path / "trial-candidate-match.md").write_text(
        f"# x\n\n**Version:** {version}\n\n## The prompt\n\n```text\n{body}\n```\n",
        encoding="utf-8")
    return tmp_path


def test_a_version_mismatch_refuses_to_render(tmp_path: Path) -> None:
    with pytest.raises(pr.RenderError, match="bump one"):
        pr.render_tier(_tier(), _prompt_file(tmp_path, cm.PROMPT_VERSION + 1))


@pytest.mark.parametrize("doc, message", [
    (None, "no such prompt"),
    ("# x\n## The prompt\n```text\nA\n```\n", "no \\*\\*Version"),
    ("**Version:** 3\n## The prompt\nno fence\n", "no ```text block"),
    ("**Version:** 3\n## The prompt\n```text\nA {{b}}\n", "not closed"),
])
def test_a_malformed_prompt_file_refuses(tmp_path: Path, doc: str | None, message: str) -> None:
    if doc is not None:
        (tmp_path / "p.md").write_text(doc, encoding="utf-8")
    with pytest.raises(pr.RenderError, match=message):
        pr.load("p", tmp_path)


def test_the_template_is_taken_verbatim(tmp_path: Path) -> None:
    template = pr.load("trial-candidate-match", _prompt_file(tmp_path, 3, "Line 1\n\n  {{approval}}"))
    assert template.text == "Line 1\n\n  {{approval}}" and template.fields == ("approval",)


def test_replies_become_raw_lines_only_when_complete() -> None:
    rendered = pr.render_tier(_tier(candidate_ids=("NCT00000001", "NCT00000002")), _REAL)
    assert pr.tier_reply_lines(rendered, "\nNCT00000002 = No\nNCT00000001 = Yes\n") == [
        "U#T1@abc | NCT00000001 = Yes", "U#T1@abc | NCT00000002 = No"]
    for bad in ("NCT00000001 = Yes", "NCT00000001 = Yes\nNCT00000001 = No\nNCT00000002 = No",
                "NCT00000001 = Yes\nNCT00000002 = Maybe",
                "NCT00000001 = Yes\nNCT00000002 = No\nNCT00000003 = No"):
        assert pr.tier_reply_lines(rendered, bad) is None
    # the raw lines are exactly what match-combine reads
    lines = pr.tier_reply_lines(rendered, "NCT00000001 = Yes\nNCT00000002 = No")
    assert lines and all(cm.ANSWER_LINE.match(line) for line in lines)


def test_a_role_unit_renders_and_its_reply_is_checked() -> None:
    unit = {"unit_id": "BLA1|ORIG|1|NCT00000001", "approval": "X · approved 2009",
            "protocol_id": "S245.4.007", "text": "Study S245.4.007 was pivotal"}
    rendered = pr.render_unit(unit, "review-trial-role", review_role.PROMPT_VERSION, _REAL)
    assert "S245.4.007" in rendered.text and "{{" not in rendered.text
    assert pr.role_reply_line(rendered, "PIVOTAL", ROLES) == "BLA1|ORIG|1|NCT00000001 = PIVOTAL"
    assert pr.role_reply_line(rendered, "MAYBE", ROLES) is None


def test_batches_render_and_round_trip(tmp_path: Path) -> None:
    tiers = tmp_path / "tiers.jsonl"
    cm.write_tiers([_tier()], tiers)
    batch = tmp_path / "batch-001.ids"
    batch.write_text("U#T1@abc\n\n", encoding="utf-8")
    rendered = pr.render_tier_batch(batch, tiers, _REAL)
    out = tmp_path / "batch-001.requests.jsonl"
    assert pr.write_requests(rendered, out) == 1
    assert pr.read_requests(out) == rendered

    units = tmp_path / "units.jsonl"
    units.write_text(json.dumps({"unit_id": "u1", "approval": "a", "protocol_id": "p",
                                 "text": "t"}) + "\n", encoding="utf-8")
    role_batch = tmp_path / "role-batch-001.ids"
    role_batch.write_text("u1\n", encoding="utf-8")
    [role] = pr.render_unit_batch(role_batch, units, "review-trial-role",
                                  review_role.PROMPT_VERSION, _REAL)
    assert role.request_id == "u1"


def test_a_stale_batch_refuses_to_render(tmp_path: Path) -> None:
    tiers = tmp_path / "tiers.jsonl"
    cm.write_tiers([_tier()], tiers)
    batch = tmp_path / "batch-001.ids"
    batch.write_text("U#T9@gone\n", encoding="utf-8")
    with pytest.raises(pr.RenderError, match="not in"):
        pr.render_tier_batch(batch, tiers, _REAL)
    units = tmp_path / "units.jsonl"
    units.write_text("", encoding="utf-8")
    with pytest.raises(pr.RenderError, match="not in"):
        pr.render_unit_batch(batch, units, "review-trial-role", review_role.PROMPT_VERSION, _REAL)


def test_the_render_prompts_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.chdir(_REAL.parents[1])
    cm.write_tiers([_tier()], tmp_path / cm.TIERS_FILE)
    batch = tmp_path / "batch-001.ids"
    batch.write_text("U#T1@abc\n", encoding="utf-8")
    assert main.main(["--mode", "render-prompts", "--batch", str(batch),
                      "--adjudication", str(tmp_path)]) == 0
    assert len(pr.read_requests(batch.with_suffix(".requests.jsonl"))) == 1

    (tmp_path / "review_role_units.jsonl").write_text(json.dumps(
        {"unit_id": "u1", "approval": "a", "protocol_id": "p", "text": "t"}) + "\n",
        encoding="utf-8")
    (tmp_path / "label_role_units.jsonl").write_text(json.dumps(
        {"unit_id": "u1", "approval": "a", "target": "NCT1", "text": "t"}) + "\n",
        encoding="utf-8")
    roles = tmp_path / "role-batch-001.ids"
    roles.write_text("u1\n", encoding="utf-8")
    for task in ("review-role", "label-role"):
        assert main.main(["--mode", "render-prompts", "--task", task, "--batch", str(roles),
                          "--adjudication", str(tmp_path)]) == 0

    stale = tmp_path / "batch-002.ids"
    stale.write_text("U#T9@gone\n", encoding="utf-8")
    assert main.main(["--mode", "render-prompts", "--batch", str(stale),
                      "--adjudication", str(tmp_path)]) == 1
    assert main.main(["--mode", "render-prompts", "--adjudication", str(tmp_path)]) == 1
    assert "not rendered" in capsys.readouterr().out


def test_both_role_prompts_render_from_one_template_unchanged() -> None:
    """D46: one template; each rendering is byte-identical to the text its
    version was judged with, so no stored verdict changes meaning."""
    import hashlib
    pinned = {
        "review-trial-role": (2, "f3344ab75c0264163fa8d1f9f3024bdf1409a9e7529a73ffada2c5231657ca85"),
        "label-trial-role": (2, "a384fadb3ae6a6c0d2bea736a3a219fe0adb9361b516a5a862534b03ea875054"),
    }
    for name, (version, digest) in pinned.items():
        template = pr.load(name)
        assert (template.version, hashlib.sha256(template.text.encode()).hexdigest()) == (
            version, digest), name
        assert "[[" not in template.text
    assert pr.rendered_role("trial-candidate-match") is None


def test_a_slot_without_text_for_the_document_refuses(tmp_path: Path) -> None:
    (tmp_path / f"{pr.ROLE_TEMPLATE}.md").write_text(
        "| x-role | review | 1 |\n\n## The template\n\n```text\n[[gone]]\n```\n",
        encoding="utf-8")
    with pytest.raises(pr.RenderError, match="slot 'gone'"):
        pr.load("x-role", tmp_path)
    assert pr.rendered_role("x-role", tmp_path / "absent") is None


def test_role_requests_are_grouped_verbatim_and_the_wrapper_version_is_checked(
        tmp_path: Path) -> None:
    one = [pr.Rendered(f"U{i}", "label-trial-role", 2, f"prompt text {i}") for i in range(1, 4)]
    [job] = pr.group_role_requests(one, 10)
    assert job.expects == ("U1", "U2", "U3") and job.request_id == "U1+3"
    assert "output exactly 3 lines" in job.text
    for i in (1, 2, 3):
        assert f"=== CASE {i} ===\nprompt text {i}\n=== END OF CASE {i} ===" in job.text
    with pytest.raises(pr.RenderError, match="at least 1"):
        pr.group_role_requests(one, 0)
    with pytest.raises(pr.RenderError, match="one role prompt"):
        pr.group_role_requests([one[0], one[1]._replace(prompt="review-trial-role")], 2)
    (tmp_path / "role-case-batch.md").write_text(
        "**Version:** 9\n\n## The prompt\n\n```text\n{{count}} {{cases}}\n```\n",
        encoding="utf-8")
    with pytest.raises(pr.RenderError, match="v9"):
        pr.group_role_requests(one, 2, tmp_path)


def test_tiers_are_packed_by_size_never_two_of_one_unit_and_round_trip(tmp_path: Path) -> None:
    def tier(unit: str, n: int, size: int) -> pr.Rendered:
        return pr.Rendered(f"{unit}#T{n}@d", "trial-candidate-match", 4, "x" * size,
                           ("NCT00000001", "NCT00000002"))
    tiers = [tier("U1", 1, 100), tier("U1", 2, 100), tier("U2", 1, 900), tier("U3", 1, 5000),
             tier("U4", 1, 100)]
    jobs = pr.group_tier_requests(tiers, 5, 2_000)
    assert [[c[0] for c in j.cases] for j in jobs] == [
        ["U1#T1@d"], ["U1#T2@d", "U2#T1@d"], ["U3#T1@d"], ["U4#T1@d"]]
    assert jobs[0].prompt == pr.TIER_BATCH and "judge 1 separate cases" in jobs[0].text
    path = tmp_path / "r.jsonl"
    pr.write_requests(jobs, path)
    assert pr.read_requests(path) == jobs
    with pytest.raises(pr.RenderError, match="one-tier"):
        pr.group_tier_requests([pr.Rendered("U1", "label-trial-role", 2, "t")], 2)
    with pytest.raises(pr.RenderError, match="at least 1"):
        pr.group_tier_requests(tiers, 0)
