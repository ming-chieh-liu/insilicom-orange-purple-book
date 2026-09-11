# Runbook — adjudicate section 14 trial polarity

**For:** the main agent · **Prompt:** `docs/prompts/section14-polarity.md` (v2, binary)
**Written:** 2026-09-11

## What this does and why

`links.tsv` says a trial id appeared in a label's section 14. It does **not** say
why. Section 14 carries pivotal trials, supporting trials, safety and
pharmacokinetic references, and trials that **failed** — the fenofibrate label
cites ACCORD Lipid (NCT00000620) under a heading reading *"14.4 Lack of Efficacy
in Cardiovascular Outcomes Trials"*. A regex cannot tell these apart, and a
lexical-cue attempt was tried and produced mostly false positives on inspection.

So a second step reads the text and judges. The output is a **polarity** on every
link, and `trial_approval_map.tsv` then carries only the supporting ones.

## The rules that make this trustworthy

1. **Two judges per unit, independently.** A verdict counts as SUPPORTING only if
   **both** say `SUPPORTING`. This is the point of the exercise — one sampled
   answer is not a measurement.
2. **Disagreement goes to a third judge, whose answer is final.** Do not average,
   do not re-run the first two, do not decide it yourself.
3. **A judge sees only the excerpt and the target id.** No drug name in the
   prompt beyond what the text contains, no hints, no batch context that names
   the expected answer.
4. **Never write a verdict you did not receive.** If a subagent returns nothing,
   an unparseable answer, or fewer answers than units, the missing units stay
   unjudged and get re-run. A guessed verdict is worse than a missing one.

## Step 1 — build the work units

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
cd /data/mliu/insilicom-orange-purple-book

$PY main.py --mode adjudicate-prepare --limit 100     # pilot
$PY main.py --mode adjudicate-prepare                 # everything
```

Writes `adjudication/units.jsonl`, one object per judgement unit:

```json
{"unit_id": "NCT00000620|NDA021612|3", "nct_id": "NCT00000620",
 "application": "NDA021612", "segment": 3, "text": "14.4 Lack of Efficacy …"}
```

A unit is one `(trial, application, section-14 segment)`. A trial mentioned in
several subsections yields several units — **if any one of them is judged
SUPPORTING the pair is supporting**.

Measured 2026-09-11: only **93 of 2,678 pairs** span more than one segment, and
they are not contradictions. They are one trial discussed for two purposes —
Tyvaso's `NDA214324` cites `NCT00147199` under both `14.1 Pulmonary Arterial
Hypertension (TRIUMPH I)`, the pivotal efficacy trial, and `14.2 Long-term
Treatment of PAH`, its durability follow-up. A judge may reasonably call the
second not-supporting while the first plainly supports, which is exactly what
the OR rule is for. There is no case where one section says a trial worked and
another says it failed; the FDA would not approve on that.

Units already decided in `adjudication/verdicts.jsonl` are skipped, so this is
resumable and a re-run costs nothing. `--limit N` takes the first N *undecided*
units, and deliberately includes the three calibration cases from the prompt doc
first so a pilot always exercises them.

## Step 2 — fan out to subagents

**The main agent must not read the excerpts.** 2,796 units average ~5,500
characters; pulling them into the orchestrator's own context to paste onward
would cost ~15M characters and exhaust it. Instead the subagent reads the file
itself and is told which lines to judge.

Take the `unit_id` list from `adjudication/units.jsonl` — the ids only, via
`jq -r .unit_id` or equivalent, never the `text` field. Chunk into batches of
**25 ids**, and for each batch launch **two subagents concurrently** with the
identical prompt.

- Use a general-purpose subagent (it needs Read/Bash to pull its own lines).
- Send **both judges of a batch in the same message** so they run in parallel.
- Keep roughly 6 subagents in flight. 25 ids × 2 judges means ~112 batches for a
  full run.

The message to each subagent:

```
Read /data/mliu/insilicom-orange-purple-book/adjudication/units.jsonl and find
the objects whose "unit_id" is in this list:

  <unit_id>
  <unit_id>
  ... (25 of them)

For each one, judge it using ONLY that object's "text" field and its "nct_id".

<the full prompt from docs/prompts/section14-polarity.md, with {{text}} meaning
 that object's text field and {{nct_id}} its nct_id>

Output one line per case, nothing else:

  <unit_id> = <ANSWER>
```

**Do not paraphrase the judging rules.** Copy them from
`docs/prompts/section14-polarity.md` verbatim. The stored verdicts record
`prompt_version: 2`; changing the wording means changing the version.

Because the subagent has file access, the prompt's "judge only from the text"
rule is doing real work — it must not look up the drug, open other files, or
search the web. Say so explicitly in the message.

## Step 3 — combine, and tiebreak

For each unit, compare the two answers:

| judge A | judge B | action |
|---|---|---|
| `SUPPORTING` | `SUPPORTING` | final = `SUPPORTING` |
| anything else, but equal | same | final = that answer |
| different | different | **launch a third judge on that unit alone**; its answer is final |

A third judge sees the same prompt, with no knowledge that the first two
disagreed and no sight of their answers.

Expect disagreement on a minority of units. If it exceeds roughly a quarter of a
batch, stop and report — that means the prompt or the excerpt extraction is
wrong, not that the cases are hard.

## Step 4 — write the verdicts

```bash
$PY main.py --mode adjudicate-record --from <file>
```

where `<file>` is JSONL you write with one object per unit:

```json
{"unit_id": "NCT00000620|NDA021612|3", "judge_a": "NEGATIVE",
 "judge_b": "NEGATIVE", "tiebreak": null, "final": "NEGATIVE"}
```

The command validates every answer against the allowed words, refuses
anything else, stamps `prompt_version`, `model` and `decided` on each row, and
appends to `adjudication/verdicts.jsonl`.

That file is **curated, append-only and tracked in git**. It is not under
`output/`, which the pipeline truncates on every run. The last row for a
`unit_id` wins, so a re-judgement corrects an earlier one without editing
history.

## Step 5 — apply them

```bash
$PY main.py --mode map
```

`map` reads the verdicts automatically when the file exists. Every row of
`links.tsv` gains a `polarity` column; `trial_approval_map.tsv` keeps only rows
whose polarity is `supporting`. Links with no verdict yet are `unjudged` and are
**kept** in both files — an unjudged link is not a refuted one, and dropping it
would make partial adjudication look like data loss.

Report the run as: units judged, split by final answer, how many needed a
tiebreak, and the before/after row counts of `trial_approval_map.tsv`.

## Scale

2,678 links → **2,776 units** (2,228 subsection-level, 548 whole-section where a
label has no `14.N` headings). At two judges each that is ~5,552 calls for a full
run, so the pilot exists for a reason: a mis-worded prompt costs 200 calls, not
5,552.
