# Runbook — P4 per-candidate matching (D33)

**For:** the main agent · **Prompt:** `docs/prompts/trial-candidate-match.md` (v1)
**Code:** `service/candidate_match.py` · **Written:** 2026-09-28

Replaces `docs/runbooks/identify-trial.md` (pick one of the whole shortlist, two judges plus a
third) for P4. That runbook, its prompt and `identify-prepare/combine/record` stay in the repo for
reuse; nothing about them changed.

## What a unit is

A **tier**: one section-14 description plus up to 10 of its candidates, taken in P3's rank order
(tier 1 = ranks 1–10, tier 2 = 11–20, …, through the whole shortlist; there is no cap, so
literature-injected candidates beyond 100 are reached too). `tier_id = <unit_id>#T<k>`.

The descriptions are the same set the pick-one route judged (`identification.read_units`:
earliest submission only, no label-stated NCT, no borrowed biosimilar section 14, no D24 guard).

## The stop rule

Per description, tiers are judged in order. The first tier holding any **Yes** decides, and the
answer is its **highest-ranked Yes**. A tier is read only once every candidate in it has a final
answer. Every tier No means **NONE**. Measured on the 11,792 known answers: median rank 2, 74% of
shortlisted answers in tier 1.

## Judges

- **Pilot:** two independent judges (A, B) per tier. A candidate's answer is final when they
  agree; a disagreement goes to a fresh third judge (C) whose answer is final. The pilot's job is
  to measure A/B agreement per candidate.
- **Full run:** **one judge (A)**. This is a recorded exception to the project's two-judge
  convention (D33), taken for cost and resting on the pilot's agreement rate. Re-enable two
  judges with `--judges 2`; the code path is the pilot's.

## Steps

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
$PY main.py --mode match-prepare [--limit 50]     # pilot: a hash-ordered sample of 50
#   -> adjudication/candidate_match_tiers.jsonl (every tier issued)
#   -> adjudication/candidate_match_batches/round-RR/batch-NNN.ids (10 tiers each)
#   judges write adjudication/candidate_match_raw/round-RR/batch-NNN-{A,B}.txt
$PY main.py --mode match-combine --judges 2       # pilot (1 for the full run)
#   -> appends complete tiers to adjudication/candidate_match_answers.jsonl
#   -> candidate_match_tiebreak_needed.txt: "<tier_id> | <nct>" lines for judge C, whose
#      answers go in candidate_match_raw/round-RR/tiebreak-NNN-C.txt
$PY main.py --mode match-prepare [--limit 50]     # next round: the next tier of every
                                                  # description still undecided
# ... repeat prepare -> judge -> combine until prepare writes 0 batch files ...
$PY main.py --mode match-record [--limit 50]      # decided descriptions -> identify_verdicts.jsonl
```

Use the same `--limit` on every prepare and record of one run: the sample is deterministic, so
the pilot's descriptions are a prefix of the full run's and are skipped there as already decided.

## The subagent message

The orchestrator passes **ids only** (the batch file path). Each judge reads its own tiers:

```
Read the tier ids in <batch file> (one per line; a tier id may contain spaces and "=",
copy it exactly). For each, pull only that tier from
adjudication/candidate_match_tiers.jsonl, e.g.
  jq -c --arg id '<tier_id>' 'select(.tier_id==$id)' .../candidate_match_tiers.jsonl
and judge it with the prompt below, using ONLY its "approval", "excerpt", "trial_name",
"enrollment" and "candidates" fields. Read every field in full; never truncate.
Write one line per candidate, in the order shown, to <raw file>:
  <tier_id> | <NCT id> = Yes|No
<the prompt from docs/prompts/trial-candidate-match.md, verbatim>
Do not look anything up, search the web, or open any other file.
```

A tiebreak judge gets the same message plus the `"<tier_id> | <nct>"` lines it must answer, and
answers only those candidates.

## Rules carried over

A missing or unparseable line leaves its tier incomplete, never guessed. An answer is recorded
against the candidate ids the tier was written with, and a decided description's NCT must be on
its own shortlist (`record_decisions` refuses anything else). Answers are append-only and stamped
`prompt_version`; decisions go to `identify_verdicts.jsonl` stamped `prompt_version: 2,
method: candidate_match`, so they supersede the pick-one answer for the same unit (last row wins).

## What the report must state

Descriptions in scope, decided (identified / NONE) and still judging · identified answers by the
tier they were found in · tiers judged per description · per-candidate A/B agreement (pilot) and
tiebreaks · answers refused · for the pilot, how its answers compare with the pick-one verdicts
on the same descriptions.
