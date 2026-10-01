# Runbook — role of every label link, at submission grain (D28)

**For:** the main agent · **Prompt:** `docs/prompts/label-trial-role.md` (v1)
**Written:** 2026-09-25 · **Status:** wired (`label-role-units`, `label-role-prepare`,
`label-role-combine`); not yet piloted.

## How to run it

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
# after P3 (candidates), P4 (identify) and the review-role run are current:
$PY main.py --mode label-role-units        # reads cached label PDFs only, never fetches
#   → adjudication/label_role_units.jsonl (one unit per label link, after D16)
$PY main.py --mode label-role-prepare [--limit N]
#   → adjudication/label_role_batches/v1/lrole-batch-NNN.ids, calibration first
#   run judges A and B per batch; save answers as
#   adjudication/label_role_raw/v1/lrole-batch-NNN-{A,B}.txt
$PY main.py --mode label-role-combine
#   → records agreed units in adjudication/label_role_verdicts.jsonl (prompt_version 1),
#     writes lrole-tiebreak-batch-NNN.ids; save the third judge's answers as
#     lrole-tiebreak-batch-NNN-C.txt and re-run combine until "nothing pending"
$PY main.py --mode assemble                # applies the roles
```

## What this does and why

P4 says *which* registry trial a §14 description is. It never asks whether the label presents it
as evidence for this approval — and labels describe failed trials, safety follow-ups, PK studies
and trials behind other indications in the same section. The old polarity task
(`adjudication/verdicts.jsonl`) asked, but on the **current** openFDA label, per **application**,
and 31% of links had no verdict. This task asks on the submission's **own** label.

## Step 1 — units (deterministic)

1. **Which links.** `assemble.build(..., apply_label_roles=False)` — every link P5 would make with
   no support filter, after D16 across label and review routes. A link is judged if any of its
   sources is a label source: `label_nct`, `label_description`, `review_name_match`. Review-only
   links are judged by the review route and are not units here.
2. **Which text.** That submission's type-2 label PDF from the **cache** (P2 downloads at its polite
   rate; this step makes no request to FDA), section 14 anchored as P2 anchors it. Passages: every
   mention of the registry id, the trial name (not when the name is the heading or `n=…`) or the
   stated patient count, ±700 characters, ≤8, prefixed with the `14.N` subsection heading. An
   unnamed trial falls back to the subsection whose heading the unit carries.
3. **Dropped, with the reason reported:** `no_label_text` (PDF not cached or no §14 text),
   `trial_not_found_in_section_14` (nothing to show the judge — never guessed).

Unit shape (`adjudication/label_role_units.jsonl`, rebuilt, gitignored):

```json
{"unit_id": "BLA125514|SUPPL|35|NCT02578680",
 "target": "KEYNOTE-189 (registry id NCT02578680; the label may not print it)",
 "approval": "KEYTRUDA · BLA125514 SUPPL 35 · approved 2018-08-20 · provides for …",
 "text": "[14.2 Non-Small Cell Lung Cancer] … KEYNOTE-189 … 616 patients …"}
```

## Step 2 — judge

Same rules as `docs/runbooks/adjudicate-section-14.md`:

1. **Two independent judges per unit**, launched in the same message; batches of ≤ 25 unit ids.
2. **The orchestrator never reads `text`, `target` or `approval`.** It passes unit ids; each judge
   reads its own lines from the units file.
3. The subagent message holds the prompt from `docs/prompts/label-trial-role.md` **verbatim**, the
   file path, the id list, an explicit ban on any lookup, file read or search beyond the units
   file, and the output format `<unit_id> = <OPTION>`, one line per unit, nothing else.
4. Raw answers: `adjudication/label_role_raw/v1/lrole-batch-NNN-{A,B}.txt`.

### Delivery without files (agent pool, 2026-10-01)

A pool job (`no_tools: true`) cannot read the units file, so the prompt is rendered in code:
`main.py --mode render-prompts --task label-role --batch <lrole-…-NNN.ids>` writes
`<batch>.requests.jsonl`, one complete prompt per unit (`service/prompt_render.py`: template read
verbatim from `docs/prompts/label-trial-role.md`, version checked against the code, every field filled, an
empty field refuses). A reply counts only if it is exactly one of the six roles on one line
(`prompt_render.role_reply_line`); the code then writes `<unit_id> = <ROLE>` to the judge's raw
file. Anything else writes nothing and the unit stays undecided.

## Step 3 — combine and tiebreak

| judge A | judge B | final |
|---|---|---|
| equal | equal | that answer |
| different | different | a **fresh third judge** on that unit alone, told nothing of the disagreement; its answer is final |

Missing or unparseable answers leave the unit undecided — never guessed. If more than a quarter of
a batch disagrees, stop and report: the prompt or the passage extraction is wrong.

## Step 4 — record and apply

Appended to `adjudication/label_role_verdicts.jsonl` (append-only, tracked in git):

```json
{"unit_id": "…", "judge_a": "PIVOTAL", "judge_b": "PIVOTAL", "tiebreak": null,
 "final": "PIVOTAL", "prompt_version": 1, "decided": "2026-09-25"}
```

P5 (`service/assemble.py`): where a current-version role exists it **replaces** the old polarity
verdict. `PIVOTAL`/`SUPPORTING` link; any other role leaves the link out with reason
`label_role_<role>` (e.g. `label_role_failed`). Where no role exists yet the old polarity verdict
still filters, and the assemble report counts those links (`label links without a D28 role yet`)
— that number must reach 0 before D28 is called complete. A label role that does not link while
the same submission's review links the trial, or the reverse, is `conflict = role_disagrees`,
tier B (D29).

## What the report must state

Units built and dropped (per reason) · agreement rate A/B · tiebreaks · final role distribution ·
the calibration units against their expected roles · links newly dropped versus the old polarity
filter, and links newly kept that it had dropped · `role_disagrees` count against the review route.

## Pilot

Before the full run: 200 units, calibration first, sampled across eras (pre-2008 labels are
pre-PLR and read differently). The user hand-checks a sample of non-linking answers before the
full run is cleared.
