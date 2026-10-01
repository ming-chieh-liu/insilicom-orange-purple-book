"""Render a judge's prompt as one self-contained text (file-independent judging).

Until now a judge was a local subagent: its message carried the versioned
template with its ``{{placeholders}}`` and told it to pull the unit's field
values from a JSONL file on disk, so **the judge did the substitution**. A job on
the agent pool runs with ``no_tools: true`` and can read nothing, so the text it
receives must already be complete. This module does the substitution in code:

* the template is read **verbatim** from ``docs/prompts/<name>.md`` (the
  ``text`` block under ``## The prompt``), and its ``**Version:**`` must equal the
  version the code stamps on answers — a mismatch refuses to render rather than
  judging under one version and recording another;
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
_FIELD = re.compile(r"\{\{(\w+)\}\}")
_REPLY_LINE = re.compile(r"^(NCT\d{8})\s*=\s*(Yes|No)$")


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
    #: Matching: the NCT ids the reply must answer, in order. Roles: empty.
    expects: tuple[str, ...] = ()


def load(name: str, prompts_dir: Path = PROMPTS_DIR) -> Template:
    """The template ``name`` exactly as written in its prompt file.

    Raises :class:`RenderError` when the file, its version line or its
    ``text`` block under ``## The prompt`` is missing.
    """
    path = prompts_dir / f"{name}.md"
    if not path.is_file():
        raise RenderError(f"{path}: no such prompt")
    doc = path.read_text(encoding="utf-8")
    version = _VERSION.search(doc)
    if version is None:
        raise RenderError(f"{path}: no **Version:** line")
    _, _, after = doc.partition("## The prompt")
    _, fence, body = after.partition("```text\n")
    if not fence:
        raise RenderError(f"{path}: no ```text block under '## The prompt'")
    text, closed, _ = body.partition("\n```")
    if not closed:
        raise RenderError(f"{path}: the prompt's ```text block is not closed")
    return Template(name, int(version.group(1)), text)


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


def _ids(batch_file: Path) -> list[str]:
    return [line.strip() for line in batch_file.read_text(encoding="utf-8").splitlines()
            if line.strip()]


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
            handle.write(json.dumps({**item._asdict(), "expects": list(item.expects)}) + "\n")
            count += 1
    return count


def read_requests(path: Path) -> list[Rendered]:
    """The requests :func:`write_requests` wrote."""
    out: list[Rendered] = []
    for row in identification.iter_json_lines(path):
        expects = row.get("expects")
        out.append(Rendered(
            str(row["request_id"]), str(row["prompt"]), int(str(row["version"])),
            str(row["text"]),
            tuple(str(x) for x in expects) if isinstance(expects, list) else ()))
    return out


__all__ = [
    "PROMPTS_DIR",
    "RenderError",
    "Rendered",
    "Template",
    "fill",
    "load",
    "read_requests",
    "render_tier",
    "render_tier_batch",
    "render_unit",
    "render_unit_batch",
    "role_reply_line",
    "tier_reply_lines",
    "write_requests",
]
