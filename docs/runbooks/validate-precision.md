# Runbook — P6 judge precision on withheld answers (D35)

**For:** the main agent · **Prompt:** `docs/prompts/trial-candidate-match.md` (v1), copied
verbatim — the same prompt the production case-B run uses · **Code:** `service/validate.py`
(`withhold`, `score`), `service/candidate_match.py` · **Written:** 2026-09-29

Replaces the pick-one precision experiment (`precision-prepare/-combine`, deprecated) and the old
`validate --limit` path that wrote `adjudication/validate_units.jsonl` and scored against the
production `identify_verdicts.jsonl`.

## What it measures

**P4's precision in the configuration the full run uses**: one judge, per-candidate Yes/No in
tiers of 10, first Yes wins (D33, D35). P6's other number, shortlist recall, costs no agent call
(`--mode validate` without `--limit`); this one needs judges.

## What a unit is

A §14 description **whose label states its NCT id**, with that id withheld:

- **earliest appearance only** — the unit P4 would judge (D16); a later label repeating the trial
  carries another submission's indication and is not a real P4 unit;
- **the answer is in its own shortlist** — otherwise recall, not precision, is being measured;
- **the excerpt does not print any NCT id** — else the judge reads the answer off the text;
- chosen in a deterministic hash order, `--limit` of them.

The answer is removed from the unit (`stated_nct = ""`) and kept only in `truth.json`, which the
judges never open.

## Where everything lives

Everything is under **`adjudication/precision_d35/`** — `units.jsonl`, `truth.json`, and the
matcher's own tiers, answers, batches, raw files and `identify_verdicts.jsonl`. These unit ids
are real production ids, so **nothing here may be written to `adjudication/identify_verdicts.jsonl`**:
a verdict reached with the answer withheld must never become a link.

## Steps

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
ADJ=adjudication/precision_d35
$PY main.py --mode validate --limit 200            # writes $ADJ/units.jsonl, truth.json (once)
$PY main.py --mode match-prepare --units $ADJ/units.jsonl --adjudication $ADJ
#   judges write $ADJ/candidate_match_raw/round-RR/batch-NNN-A.txt (message: trial-candidate-match runbook)
$PY main.py --mode match-combine --judges 1 --adjudication $ADJ
$PY main.py --mode match-prepare --units $ADJ/units.jsonl --adjudication $ADJ   # next round
# ... repeat prepare -> judge -> combine until prepare writes 0 batch files ...
$PY main.py --mode match-record --units $ADJ/units.jsonl --adjudication $ADJ
$PY main.py --mode validate --limit 200            # re-reads truth.json and scores
```

`truth.json` is written once; re-running `validate --limit` scores against the same sample.
Delete the folder only to draw a new sample.

## Judges

**One judge**, because the measurement is of the production configuration (D33's recorded
exception). The judge message is the one in `docs/runbooks/trial-candidate-match.md`, with the
prompt copied verbatim; no hint that the answer is known, no batch context, lookups forbidden.
Missing or unparseable lines leave the tier undecided for a re-run — never guessed.

## What the report must state

Units written and the eligible pool size · units decided · **correct / wrong / abstained (NONE)**
and precision = correct ÷ (correct + wrong) · tiers per unit · the wrong answers listed with the
withheld one beside them. **Gate:** precision ≥ 90% before the full case-B run's links are called
tier B-quality in any report.

## Limits

Cases C and D have no label-stated answers, so this does not measure them; their pilots (two
judges plus a third) are the only check on them.
