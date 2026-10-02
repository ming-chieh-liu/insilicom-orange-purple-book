# Runbook — role of every label-stated trial, at submission grain (S6, D46; was D28)

**For:** the main agent · **Prompt:** `label-trial-role` v2, rendered from
`docs/prompts/fda-document-trial-role.md` (document = label) · **Written:** 2026-09-25 ·
**Revised:** 2026-10-02 (D46: S6, units from S4; v2: name guard, merged passages) · **Status:** wired;
3,325 units built 2026-10-02; not yet piloted.

**D46 (2026-10-02).** This is an **S6** task, run with the review-role task and before any search.
Units are the trials a label **states by NCT id**, one per (application, NCT) at the earliest
submission stating it (D16), built from S4's outputs. A trial S9 identifies from prose gets **no**
role check, and the old polarity verdicts are no longer read.

## How to run it

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
# after S4 labels (writes output/label_trials.tsv and output/label_section14.jsonl.gz):
$PY main.py --mode label-role-units        # reads S4's outputs; no PDF is parsed
#   → adjudication/label_role_units.jsonl
$PY main.py --mode label-role-prepare [--limit N]
#   → adjudication/label_role_batches/v2/lrole-batch-NNN.ids, calibration first
# per batch, on the agent pool (≤ 32 in flight, opus, no tools):
$PY main.py --mode render-prompts --task label-role --batch adjudication/label_role_batches/v2/lrole-batch-NNN.ids
$PY main.py --mode pool-judge --judge A --requests <batch>.requests.jsonl \
    --raw-file adjudication/label_role_raw/v2/lrole-batch-NNN-A.txt
$PY main.py --mode pool-judge --judge B --requests <batch>.requests.jsonl \
    --raw-file adjudication/label_role_raw/v2/lrole-batch-NNN-B.txt
$PY main.py --mode label-role-combine
#   → records agreed units in adjudication/label_role_verdicts.jsonl (prompt_version 2),
#     writes lrole-tiebreak-batch-NNN.ids; render them and send to the pool as judge C
#     (raw file lrole-tiebreak-batch-NNN-C.txt), then re-run combine until "nothing pending"
```

## What this does and why

A label stating a trial's NCT id identifies it exactly, but not every trial section 14 describes is
evidence for this approval: labels describe failed trials, safety follow-ups, PK studies and trials
behind other indications in the same section. This task asks, on the submission's **own** label,
what role the trial plays in **this** approval.

## Step 1 — units (deterministic)

1. **Which trials.** Every row of `output/label_trials.tsv` with an `nct_id`; one unit per
   (application, NCT) at its earliest submission — approval date, then ORIG before SUPPL, then
   supplement number (`label_role.targets_from_label`).
2. **Which text.** That submission's section 14 from `output/label_section14.jsonl.gz`, as S4 cut
   and cleaned it (page furniture removed, hyphenation rejoined); a submission with two label PDFs
   has both texts joined (`label_role.sections_by_submission`).
3. **The name guard (v2).** S4's trial name is kept only where the text writes it right before
   the id — at most 25 characters between, no sentence end or digit (`label_role.name_beside_id`).
   Otherwise the trial is named by its id alone.
4. **Passages.** Every mention of the id (any of `NCT01234567`, `NCT 0…`, `NCT#0…`, `NCT: 0…`), of the
   kept name, and of the patient count where the id's own sentence states it; ±700 characters;
   overlapping windows in one subsection merged; ≤ 8 (those holding the id first), in reading
   order, each prefixed `[<14.N> <subsection heading>]`.
5. **Dropped, with the reason reported:** `no_label_text` (no section 14 stored),
   `trial_not_found_in_section_14` (nothing to show the judge — never guessed). Both 0 on
   2026-10-02.

Unit shape (`adjudication/label_role_units.jsonl`, rebuilt, gitignored):

```json
{"unit_id": "BLA125514|SUPPL|35|NCT02578680",
 "target": "KEYNOTE-189 (registry id NCT02578680)",
 "approval": "KEYTRUDA · BLA125514 SUPPL 35 · approved 2018-08-20 · provides for …",
 "text": "[14.2 Non-Small Cell Lung Cancer] … Study KEYNOTE-189 (NCT02578680), a randomized … …"}
```

## Step 2 — judge

Same rules as `docs/runbooks/adjudicate-section-14.md`:

1. **Two independent judges per unit**, launched in the same message; batches of ≤ 25 unit ids.
2. **The orchestrator never reads `text`, `target` or `approval`.** It passes unit ids; each judge
   reads its own lines from the units file.
3. The subagent message holds the label rendering of `docs/prompts/fda-document-trial-role.md` **verbatim**, the
   file path, the id list, an explicit ban on any lookup, file read or search beyond the units
   file, and the output format `<unit_id> = <OPTION>`, one line per unit, nothing else.
4. Raw answers: `adjudication/label_role_raw/v2/lrole-batch-NNN-{A,B}.txt`.

### Delivery without files (agent pool, 2026-10-01)

A pool job (`no_tools: true`) cannot read the units file, so the prompt is rendered in code:
`main.py --mode render-prompts --task label-role --batch <lrole-…-NNN.ids>` writes
`<batch>.requests.jsonl`, one complete prompt per unit (`service/prompt_render.py`: template read
verbatim from `docs/prompts/fda-document-trial-role.md`, version checked against the code, every field filled, an
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
 "final": "PIVOTAL", "prompt_version": 2, "decided": "2026-10-02"}
```

S10 (`service/assemble.py`): a label-stated trial (`label_nct`) links only on a current-version
`PIVOTAL` or `SUPPORTING` verdict; any other role leaves it out as `label_role_<role>` (e.g.
`label_role_failed`); with no verdict yet it is held back as `label_role_pending`, and the assemble
report counts those ("label-stated trials awaiting S6 role") — S6 is complete when that is 0. A
§14 name matching a review protocol id (`review_name_match`) takes the review's role. When the
label's and the review's verdicts for the same trial differ, it links if either is PIVOTAL or
SUPPORTING, keeps tier A and carries `conflict = role_disagrees` (D46). S8: a submission whose label
states a PIVOTAL or SUPPORTING trial is route A, with no C/D unit (D46, extending D36).

## What the report must state

Units built and dropped (per reason) · agreement rate A/B · tiebreaks · final role distribution ·
the calibration units against their expected roles · `role_disagrees` count against the review
route · links held as `label_role_pending`.

## Pilot

Before the full run: 200 units, calibration first, sampled across eras (pre-2008 labels are
pre-PLR and read differently). The user hand-checks a sample of non-linking answers before the
full run is cleared.
