# Runbook — identify the trial a section-14 description names

**For:** the main agent · **Prompt:** `docs/prompts/trial-identification.md` (v1)
**Written:** 2026-09-16

## What this does and why

`output/candidates.jsonl` holds 11,574 `(submission, trial)` units. 5,561 of
them state their own NCT id in the label and are already identified. The other
6,013 describe a trial only in prose, beside a median of 25 registered studies
that survived the drug, condition and approval-date filters:

> The efficacy of KEYTRUDA in combination with pemetrexed and platinum
> chemotherapy was investigated in KEYNOTE-189, a randomized, multicenter,
> double-blind, active-controlled trial conducted in **616** patients…

No rule picks the right one. The count is the strongest signal — across five
drug and condition sets every candidate had a distinct enrollment — but a label
reports the analysed population where the registry reports everyone enrolled,
so it is a signal, not a key. A judge reads the paragraph against the list.

## The rules that make this trustworthy

1. **Two judges per unit, independently.** A unit is decided only when both
   give the same answer. One sampled answer is not a measurement.
2. **Disagreement goes to a third judge, whose answer is final.** Do not
   average, do not re-run the first two, do not decide it yourself.
3. **`NONE` is a correct answer and must stay cheap to give.** This is the rule
   that separates this task from the polarity one. A wrong polarity mislabels a
   link that exists; **a wrong identification fabricates an edge** — it asserts
   a drug was approved on evidence that had nothing to do with it, and nothing
   downstream can tell that from a real link. Two judges agreeing on `NONE` is
   a decision, not a gap. Do not treat a high `NONE` rate as failure.
4. **An answer must be one of that unit's own candidates.** `record_verdicts`
   refuses anything else. An id that was not on the list was misread or
   invented.
5. **A judge sees only its excerpt and its candidate list.** No drug name beyond
   what the text contains, no hint at the expected answer, no batch context.
   The subagent has file and shell access because it reads its own units, so
   the message must forbid lookups explicitly.
6. **Never write a verdict you did not receive.** A subagent that returns
   nothing, an unparseable answer, or fewer answers than units leaves those
   units undecided for a re-run. A guessed verdict is worse than a missing one.

## Step 1 — build the work units

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
cd /data/mliu/insilicom-orange-purple-book

$PY main.py --mode identify-prepare --limit 200     # pilot
$PY main.py --mode identify-prepare                 # everything
```

Writes `adjudication/identify_units.jsonl`, one object per unit:

```json
{"unit_id": "BLA125514|SUPPL|35|KEYNOTE-189",
 "excerpt": "The efficacy of KEYTRUDA …",
 "acronym": "KEYNOTE-189",
 "enrollment": "616",
 "candidate_ids": ["NCT02578680", "…"],
 "candidates": "1. NCT02578680 | enrolled: 616 | phase: PHASE3 | …"}
```

Units already in `adjudication/identify_verdicts.jsonl` are skipped, so this is
resumable and a re-run costs nothing. `--limit N` takes the N **undecided**
units with the *shortest* shortlists — a pilot built from 200-candidate units
would measure the shortlist rather than the prompt.

## Step 2 — fan out to subagents

**The main agent must not read the excerpts or the candidate blocks.** 6,013
units carrying a paragraph and 25 candidate lines each is several million
characters; pulling them into the orchestrator to paste onward would exhaust
its context. The subagent reads the file itself and is told which unit ids to
judge.

Take the `unit_id` list via `jq -r .unit_id` — the ids only, never `excerpt`
or `candidates`. Chunk into batches of **20**, and for each batch launch **two
subagents concurrently** with the identical message.

- Use a general-purpose subagent; it needs Read to pull its own lines.
- Send **both judges of a batch in the same message** so they run in parallel.
- Keep roughly 6 subagents in flight.

The message to each subagent:

```
Read /data/mliu/insilicom-orange-purple-book/adjudication/identify_units.jsonl
and find the objects whose "unit_id" is in this list:

  <unit_id>
  ... (20 of them)

For each one, judge it using ONLY that object's "excerpt", "acronym",
"enrollment" and "candidates" fields.

<the full prompt from docs/prompts/trial-identification.md, with {{excerpt}},
 {{acronym}}, {{enrollment}} and {{candidates}} meaning that object's fields>

Do not look up any drug, trial or disease. Do not search the web. Do not open
any other file.

Output one line per case, nothing else:

  <unit_id> = <ANSWER>
```

**Do not paraphrase the prompt.** Copy it from
`docs/prompts/trial-identification.md` verbatim. Stored verdicts record
`prompt_version: 1`; changing the wording means bumping the version.

## Step 3 — combine, and tiebreak

```bash
$PY main.py --mode identify-combine
```

Pairs `adjudication/identify_raw/batch-*-A.txt` with its `-B.txt`:

| judge A | judge B | action |
|---|---|---|
| same answer, including `NONE` | same | final = that answer |
| different | different | **launch a third judge on that unit alone**; its answer is final |

A third judge sees the same prompt, with no knowledge that the first two
disagreed and no sight of their answers. Units needing one are written to
`adjudication/identify_tiebreak_needed.txt`.

Expect disagreement on a minority. **If it exceeds a quarter of a batch, stop
and report** — that means the prompt or the shortlist is wrong, not that the
cases are hard.

## Step 4 — record

```bash
$PY main.py --mode identify-record --from adjudication/identify_judged.jsonl
```

Validates every answer against that unit's own candidate list, refuses anything
else, stamps `prompt_version`, and appends to
`adjudication/identify_verdicts.jsonl`.

That file is **curated, append-only and tracked in git**. It is not under
`output/`, which the pipeline truncates on every run. The last row for a
`unit_id` wins, so a re-judgement corrects an earlier one without editing
history.

## Step 5 — report

Report the run as: units judged, split by identified versus `NONE`, how many
needed a tiebreak, and how many answers were refused as not on the candidate
list. A refusal count above zero is worth investigating before the next batch —
it means a judge named something it was not shown.

## Scale

6,013 units at two judges each is ~12,000 calls for a full run, plus tiebreaks.
At 20 ids per batch that is ~300 batches × 2. The pilot exists for a reason: a
mis-worded prompt costs 400 calls, not 12,000.
