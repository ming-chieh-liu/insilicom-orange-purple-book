"""Render a judge's prompt as one self-contained text (file-independent judging).

Until now a judge was a local subagent: its message carried the versioned
template with its ``{{placeholders}}`` and told it to pull the unit's field
values from a JSONL file on disk, so **the judge did the substitution**. A job on
the agent pool runs with ``no_tools: true`` and can read nothing, so the text it
receives must already be complete. This module does the substitution in code:

* the template is read **verbatim** from ``docs/prompts/<name>.md`` (the
  ``text`` block under ``## The prompt``), and its ``**Version:**`` must equal the
  version the code stamps on answers — a mismatch refuses to render rather than
  judging under one version and recording another. The two role prompts
  (``review-trial-role``, ``label-trial-role``) are renderings of one template,
  ``fda-document-trial-role.md`` (D46): its ``[[slot]]`` lines are filled for the
  document, and each rendering keeps the name and version in its table;
* every ``{{field}}`` is filled in a single pass from the stored unit (a value
  that itself contains ``{{…}}`` is never expanded), and a placeholder with no
  value refuses to render — a blank field would be judged as "not stated";
* the judge's reply is checked strictly and turned into the raw-file lines the
  existing ``*-combine`` modes read. An incomplete or malformed reply yields
  nothing, so the unit stays undecided (the runbook rule: never record a result
  that was not returned).

The orchestrator never reads the rendered text; it passes request ids, and the
submitting code reads and sends the text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from service import candidate_match, identification

#: Where the versioned prompts live.
PROMPTS_DIR = Path("docs/prompts")

_VERSION = re.compile(r"\*\*Version:\*\*\s*(\d+)")

#: The one template both role prompts render from (D46).
ROLE_TEMPLATE = "fda-document-trial-role"
_RENDERING = re.compile(r"^\|\s*([\w-]+)\s*\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*$", re.M)
_SLOT = re.compile(
    r"^### (\w+)\n\n#### review\n\n```text\n(.*?)\n```\n\n#### label\n\n```text\n(.*?)\n```",
    re.M | re.S)
_SLOT_LINE = re.compile(r"^\[\[(\w+)\]\]$", re.M)
_FIELD = re.compile(r"\{\{(\w+)\}\}")
_REPLY_LINE = re.compile(r"^(NCT\d{8})\s*=\s*(Yes|No)$")

#: The wrapper that puts several role units in one pool job, and its version.
CASE_BATCH = "role-case-batch"
CASE_BATCH_VERSION: int = 1
_CASE_LINE = re.compile(r"^CASE\s+(\d+)\s*=\s*([A-Z_]+)$")

#: The wrapper that puts several S9 tiers in one pool job, and its version.
TIER_BATCH = "tier-case-batch"
TIER_BATCH_VERSION: int = 1
#: Tiers per job, and the size a job stops growing at (characters).
TIERS_PER_JOB: int = 5
JOB_CHARS: int = 100_000
_TIER_CASE_LINE = re.compile(r"^CASE\s+(\d+)\s*\|\s*(NCT\d{8})\s*=\s*(Yes|No)$")


class RenderError(ValueError):
    """A prompt that cannot be rendered faithfully; nothing is sent."""


class Template(NamedTuple):
    """One versioned prompt as written in ``docs/prompts``."""

    name: str
    version: int
    text: str

    @property
    def fields(self) -> tuple[str, ...]:
        """The placeholders, in first-seen order."""
        return tuple(dict.fromkeys(_FIELD.findall(self.text)))


class Rendered(NamedTuple):
    """One complete prompt, ready to send as a single request."""

    request_id: str          # tier id (matching) or unit id (roles)
    prompt: str
    version: int
    text: str
    #: Matching: the NCT ids the reply must answer, in order. Roles: empty for
    #: one unit; for several (:func:`group_role_requests`) the unit ids, in
    #: case order — case n answers ``expects[n-1]``.
    expects: tuple[str, ...] = ()
    #: Several S9 tiers in one job (:func:`group_tier_requests`): per case,
    #: ``(tier id, the NCT ids it must answer)``, in case order.
    cases: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _text_block(doc: str, path: Path, heading: str) -> str:
    _, _, after = doc.partition(heading)
    _, fence, body = after.partition("```text\n")
    if not fence:
        raise RenderError(f"{path}: no ```text block under '{heading}'")
    text, closed, _ = body.partition("\n```")
    if not closed:
        raise RenderError(f"{path}: the ```text block under '{heading}' is not closed")
    return text


def rendered_role(name: str, prompts_dir: Path = PROMPTS_DIR) -> Template | None:
    """``name`` rendered from the role template, or None when the template does
    not list it (D46). Raises :class:`RenderError` on a slot the template does
    not define."""
    path = prompts_dir / f"{ROLE_TEMPLATE}.md"
    if not path.is_file():
        return None
    doc = path.read_text(encoding="utf-8")
    renderings = {m.group(1): (m.group(2), int(m.group(3))) for m in _RENDERING.finditer(doc)}
    if name not in renderings:
        return None
    document, version = renderings[name]
    slots = {m.group(1): {"review": m.group(2), "label": m.group(3)} for m in _SLOT.finditer(doc)}
    template = _text_block(doc, path, "## The template")
    missing = [m for m in _SLOT_LINE.findall(template) if document not in slots.get(m, {})]
    if missing:
        raise RenderError(f"{path}: slot {missing[0]!r} has no {document} text")
    return Template(name, version,
                    _SLOT_LINE.sub(lambda m: slots[m.group(1)][document], template))


def load(name: str, prompts_dir: Path = PROMPTS_DIR) -> Template:
    """The template ``name`` exactly as written in its prompt file, or as the
    role template renders it (:func:`rendered_role`).

    Raises :class:`RenderError` when the file, its version line or its
    ``text`` block under ``## The prompt`` is missing.
    """
    role = rendered_role(name, prompts_dir)
    if role is not None:
        return role
    path = prompts_dir / f"{name}.md"
    if not path.is_file():
        raise RenderError(f"{path}: no such prompt")
    doc = path.read_text(encoding="utf-8")
    version = _VERSION.search(doc)
    if version is None:
        raise RenderError(f"{path}: no **Version:** line")
    return Template(name, int(version.group(1)), _text_block(doc, path, "## The prompt"))


def fill(template: Template, values: Mapping[str, object]) -> str:
    """Every placeholder replaced by its value, in one pass.

    >>> t = Template("t", 1, "A: {{a}}\\nB: {{b}}")
    >>> print(fill(t, {"a": "x {{b}}", "b": "y"}))
    A: x {{b}}
    B: y
    >>> fill(t, {"a": "x", "b": ""})
    Traceback (most recent call last):
    ...
    service.prompt_render.RenderError: t v1: no value for {{b}}
    """
    missing = [f for f in template.fields if not str(values.get(f) or "")]
    if missing:
        raise RenderError(
            f"{template.name} v{template.version}: no value for "
            + ", ".join("{{" + f + "}}" for f in missing))
    return _FIELD.sub(lambda m: str(values[m.group(1)]), template.text)


def _checked(name: str, expected: int, prompts_dir: Path) -> Template:
    template = load(name, prompts_dir)
    if template.version != expected:
        raise RenderError(
            f"{name}: the prompt file is v{template.version} but the code stamps "
            f"v{expected}; bump one so they agree before judging")
    return template


def render_tier(tier: candidate_match.TierUnit,
                prompts_dir: Path = PROMPTS_DIR) -> Rendered:
    """One S9 matching tier (B±, C±, D) as a complete prompt."""
    expected = candidate_match.PROMPT_VERSIONS[tier.prompt]
    template = _checked(tier.prompt, expected, prompts_dir)
    return Rendered(tier.tier_id, tier.prompt, expected,
                    fill(template, tier._asdict()), tier.candidate_ids)


def render_unit(unit: Mapping[str, object], prompt: str, version: int,
                prompts_dir: Path = PROMPTS_DIR) -> Rendered:
    """One role unit (S6 review role, D28 label role) as a complete prompt."""
    template = _checked(prompt, version, prompts_dir)
    return Rendered(str(unit.get("unit_id") or ""), prompt, version, fill(template, unit))


def tier_reply_lines(rendered: Rendered, reply: str) -> list[str] | None:
    """The raw-file lines for a matching reply, or ``None`` if it is incomplete.

    Every expected candidate must be answered exactly once, Yes or No, and no
    other NCT id may appear; blank lines are ignored, anything else rejects it.

    >>> r = Rendered("U#T1@abc", "trial-candidate-match", 3, "", ("NCT00000001", "NCT00000002"))
    >>> tier_reply_lines(r, "NCT00000001 = Yes\\nNCT00000002 = No\\n")
    ['U#T1@abc | NCT00000001 = Yes', 'U#T1@abc | NCT00000002 = No']
    >>> tier_reply_lines(r, "NCT00000001 = Yes") is None
    True
    >>> tier_reply_lines(r, "Sure! NCT00000001 = Yes\\nNCT00000002 = No") is None
    True
    """
    answers: dict[str, str] = {}
    for raw in reply.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _REPLY_LINE.match(line)
        if match is None or match.group(1) in answers:
            return None
        answers[match.group(1)] = match.group(2)
    if set(answers) != set(rendered.expects):
        return None
    return [f"{rendered.request_id} | {nct} = {answers[nct]}" for nct in rendered.expects]


def role_reply_line(rendered: Rendered, reply: str,
                    options: Iterable[str]) -> str | None:
    """The raw-file line for a role reply, or ``None`` unless it is exactly one option.

    >>> r = Rendered("BLA1|ORIG|1|NCT00000001", "review-trial-role", 2, "")
    >>> role_reply_line(r, "  PIVOTAL\\n", {"PIVOTAL", "OTHER"})
    'BLA1|ORIG|1|NCT00000001 = PIVOTAL'
    >>> role_reply_line(r, "PIVOTAL because ...", {"PIVOTAL"}) is None
    True
    """
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if len(lines) != 1 or lines[0] not in set(options):
        return None
    return f"{rendered.request_id} = {lines[0]}"


def group_role_requests(rendered: list[Rendered], per_job: int,
                        prompts_dir: Path = PROMPTS_DIR) -> list[Rendered]:
    """Role requests joined ``per_job`` at a time into one job each, every
    case the unit's own prompt verbatim inside the :data:`CASE_BATCH` wrapper.

    The job's id is its first unit's id plus ``+n`` (the units it holds); the
    unit ids go to ``expects`` in case order.

    >>> one = [Rendered(f"U{i}", "label-trial-role", 2, f"prompt {i}") for i in (1, 2, 3)]
    >>> jobs = group_role_requests(one, 2)
    >>> [(j.request_id, j.expects) for j in jobs]
    [('U1+2', ('U1', 'U2')), ('U3+1', ('U3',))]
    >>> "=== CASE 2 ===\\nprompt 2\\n=== END OF CASE 2 ===" in jobs[0].text
    True
    """
    if per_job < 1:
        raise RenderError(f"per_job must be at least 1, not {per_job}")
    wrapper = _checked(CASE_BATCH, CASE_BATCH_VERSION, prompts_dir)
    jobs: list[Rendered] = []
    for start in range(0, len(rendered), per_job):
        group = rendered[start:start + per_job]
        if {r.prompt for r in group} != {group[0].prompt} or any(r.expects for r in group):
            raise RenderError("only one-unit requests of one role prompt can be grouped")
        cases = "\n\n".join(f"=== CASE {n} ===\n{r.text}\n=== END OF CASE {n} ==="
                             for n, r in enumerate(group, 1))
        jobs.append(Rendered(f"{group[0].request_id}+{len(group)}", group[0].prompt,
                             group[0].version,
                             fill(wrapper, {"count": len(group), "cases": cases}),
                             tuple(r.request_id for r in group)))
    return jobs


def role_batch_reply_lines(rendered: Rendered, reply: str,
                           options: Iterable[str]) -> list[str] | None:
    """The raw-file lines for a several-case role reply, or ``None`` unless
    every case is answered exactly once with a valid option and nothing else
    is written (blank lines aside).

    >>> r = Rendered("U1+2", "label-trial-role", 2, "", ("U1", "U2"))
    >>> role_batch_reply_lines(r, "CASE 1 = PIVOTAL\\n\\nCASE 2 = OTHER\\n", {"PIVOTAL", "OTHER"})
    ['U1 = PIVOTAL', 'U2 = OTHER']
    >>> role_batch_reply_lines(r, "CASE 1 = PIVOTAL", {"PIVOTAL"}) is None
    True
    >>> role_batch_reply_lines(r, "CASE 1 = PIVOTAL\\nCASE 1 = OTHER\\nCASE 2 = OTHER",
    ...                        {"PIVOTAL", "OTHER"}) is None
    True
    """
    allowed = set(options)
    answers: dict[int, str] = {}
    for raw in reply.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _CASE_LINE.match(line)
        if match is None or match.group(2) not in allowed or int(match.group(1)) in answers:
            return None
        answers[int(match.group(1))] = match.group(2)
    if set(answers) != set(range(1, len(rendered.expects) + 1)):
        return None
    return [f"{unit} = {answers[n]}" for n, unit in enumerate(rendered.expects, 1)]


def _unit_of(tier_id: str) -> str:
    """The unit a tier id belongs to (:func:`candidate_match.tier_id_for`).

    >>> _unit_of("NDA1|ORIG|1|KEYNOTE-189#T2@ab12")
    'NDA1|ORIG|1|KEYNOTE-189'
    """
    return tier_id.rsplit("#T", 1)[0]


def group_tier_requests(rendered: list[Rendered], per_job: int = TIERS_PER_JOB,
                        max_chars: int = JOB_CHARS,
                        prompts_dir: Path = PROMPTS_DIR) -> list[Rendered]:
    """S9 tiers packed into pool jobs inside the :data:`TIER_BATCH` wrapper,
    each case one tier's prompt verbatim.

    A job takes tiers in order until it holds ``per_job`` of them or the next
    would push it past ``max_chars``; a tier longer than that goes alone. A job
    never holds two tiers of one unit — a unit's tiers depend on each other
    (the stop rule), different units' never do.

    >>> t = [Rendered(f"U{i}#T1@x", "trial-candidate-match", 4, "p" * n, ("NCT00000001",))
    ...      for i, n in ((1, 40), (2, 40), (3, 40))]
    >>> [(j.request_id, len(j.cases)) for j in group_tier_requests(t, 5, 2_000)]
    [('U1#T1@x+3', 3)]
    >>> [len(j.cases) for j in group_tier_requests(t, 2, 2_000)]
    [2, 1]
    """
    if per_job < 1:
        raise RenderError(f"per_job must be at least 1, not {per_job}")
    wrapper = _checked(TIER_BATCH, TIER_BATCH_VERSION, prompts_dir)
    if any(r.prompt not in candidate_match.PROMPT_VERSIONS or not r.expects for r in rendered):
        raise RenderError("only one-tier matching requests can be grouped")
    jobs: list[Rendered] = []
    group: list[Rendered] = []

    def flush() -> None:
        if not group:
            return
        cases = "\n\n".join(f"=== CASE {n} ===\n{r.text}\n=== END OF CASE {n} ==="
                             for n, r in enumerate(group, 1))
        jobs.append(Rendered(f"{group[0].request_id}+{len(group)}", TIER_BATCH,
                             TIER_BATCH_VERSION,
                             fill(wrapper, {"count": len(group), "cases": cases}),
                             (), tuple((r.request_id, r.expects) for r in group)))
        group.clear()

    for request in rendered:
        size = sum(len(r.text) for r in group) + len(request.text)
        if group and (len(group) >= per_job or size > max_chars
                      or _unit_of(request.request_id) in {_unit_of(r.request_id) for r in group}):
            flush()
        group.append(request)
    flush()
    return jobs


def tier_batch_reply_lines(rendered: Rendered, reply: str) -> list[str] | None:
    """The raw-file lines for a several-tier reply, or ``None`` unless every
    candidate of every case is answered exactly once, Yes or No, and nothing
    else is written (blank lines aside).

    >>> r = Rendered("T1+2", "tier-case-batch", 1, "", (),
    ...              (("U1#T1@a", ("NCT00000001", "NCT00000002")), ("U2#T1@b", ("NCT00000003",))))
    >>> tier_batch_reply_lines(r, "CASE 1 | NCT00000001 = Yes\\nCASE 1 | NCT00000002 = No\\n"
    ...                           "CASE 2 | NCT00000003 = No")
    ['U1#T1@a | NCT00000001 = Yes', 'U1#T1@a | NCT00000002 = No', 'U2#T1@b | NCT00000003 = No']
    >>> tier_batch_reply_lines(r, "CASE 1 | NCT00000001 = Yes\\nCASE 2 | NCT00000003 = No") is None
    True
    """
    answers: dict[tuple[int, str], str] = {}
    for raw in reply.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _TIER_CASE_LINE.match(line)
        if match is None:
            return None
        key = (int(match.group(1)), match.group(2))
        if key in answers:
            return None
        answers[key] = match.group(3)
    wanted = {(n, nct) for n, (_, ncts) in enumerate(rendered.cases, 1) for nct in ncts}
    if set(answers) != wanted:
        return None
    return [f"{tier_id} | {nct} = {answers[(n, nct)]}"
            for n, (tier_id, ncts) in enumerate(rendered.cases, 1) for nct in ncts]


def _ids(batch_file: Path) -> list[str]:
    """The ids a batch file names, in order, once each.

    Takes ``.ids`` files (one id per line) and the matching tiebreak list
    (``<tier_id> | <NCT>`` per line), whose tier ids are kept once each: the
    third judge is sent the whole tier, and combine reads its answer only for
    the disagreed candidates.
    """
    ids = (line.split(" | ", 1)[0].strip()
           for line in batch_file.read_text(encoding="utf-8").splitlines())
    return list(dict.fromkeys(i for i in ids if i))


def render_tier_batch(batch_file: Path, tiers_path: Path,
                      prompts_dir: Path = PROMPTS_DIR) -> list[Rendered]:
    """Every tier named in a ``match-prepare`` batch file, rendered.

    A tier id absent from the tiers file refuses the whole batch: the batch and
    the tiers it names were written together, so a gap means a stale batch.
    """
    tiers = candidate_match.read_tiers(tiers_path)
    missing = [t for t in _ids(batch_file) if t not in tiers]
    if missing:
        raise RenderError(f"{batch_file}: {len(missing)} tier id(s) not in {tiers_path}, "
                          f"first {missing[0]!r}")
    return [render_tier(tiers[t], prompts_dir) for t in _ids(batch_file)]


def render_unit_batch(batch_file: Path, units_path: Path, prompt: str, version: int,
                      prompts_dir: Path = PROMPTS_DIR) -> list[Rendered]:
    """Every role unit named in a batch file, rendered."""
    units = {str(u.get("unit_id") or ""): u
             for u in identification.iter_json_lines(units_path)}
    missing = [u for u in _ids(batch_file) if u not in units]
    if missing:
        raise RenderError(f"{batch_file}: {len(missing)} unit id(s) not in {units_path}, "
                          f"first {missing[0]!r}")
    return [render_unit(units[u], prompt, version, prompts_dir) for u in _ids(batch_file)]


def write_requests(rendered: Iterable[Rendered], path: Path) -> int:
    """One JSON line per request: what the pool client sends. Returns the count."""
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for item in rendered:
            handle.write(json.dumps({**item._asdict(), "expects": list(item.expects),
                                     "cases": [[t, list(n)] for t, n in item.cases]}) + "\n")
            count += 1
    return count


def read_requests(path: Path) -> list[Rendered]:
    """The requests :func:`write_requests` wrote."""
    out: list[Rendered] = []
    for row in identification.iter_json_lines(path):
        expects = row.get("expects")
        cases = row.get("cases")
        out.append(Rendered(
            str(row["request_id"]), str(row["prompt"]), int(str(row["version"])),
            str(row["text"]),
            tuple(str(x) for x in expects) if isinstance(expects, list) else (),
            tuple((str(c[0]), tuple(str(n) for n in c[1])) for c in cases)
            if isinstance(cases, list) else ()))
    return out


__all__ = [
    "JOB_CHARS",
    "TIERS_PER_JOB",
    "TIER_BATCH",
    "TIER_BATCH_VERSION",
    "CASE_BATCH",
    "CASE_BATCH_VERSION",
    "PROMPTS_DIR",
    "ROLE_TEMPLATE",
    "RenderError",
    "Rendered",
    "Template",
    "fill",
    "group_role_requests",
    "group_tier_requests",
    "load",
    "rendered_role",
    "read_requests",
    "render_tier",
    "render_tier_batch",
    "render_unit",
    "render_unit_batch",
    "role_batch_reply_lines",
    "role_reply_line",
    "tier_batch_reply_lines",
    "tier_reply_lines",
    "write_requests",
]
