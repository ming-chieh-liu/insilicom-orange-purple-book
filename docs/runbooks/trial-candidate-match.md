# Runbook — P4 per-candidate matching (D33)

**For:** the main agent · **Prompts:** `docs/prompts/trial-candidate-match.md` (v1, case B) and
`docs/prompts/trial-candidate-match-nolabel.md` (v1, cases C and D)
**Code:** `service/candidate_match.py` · **Written:** 2026-09-28 · **Updated:** 2026-09-29 (D35)

> **D35 (2026-09-29).** Three kinds of unit, one tier mechanism:
> - **Case B** — a §14 description with no identifier (`--case B`, the default). Its shortlist is
>   the drug pool **filtered on the indication** (MeSH tree, keeping id-less trials), top 100 by
>   rank = **at most 10 tiers**, plus the literature's candidates beyond the 100, unfiltered.
> - **Case C** — a submission with an indication but no §14 description of its own; **case D** — a
>   submission with no indication (`--case CD`, or `C` / `D`). One unit per submission, judged with
>   the **no-label prompt**; results are tiers C and D (`docs/TIERS.md`).
>
> `match-prepare` writes one prompt per batch file: `batch-NNN.ids` (excerpt prompt) and
> `nolabel-NNN.ids` (no-label prompt); judges write `batch-NNN-{A,B}.txt` / `nolabel-NNN-{A,B}.txt`.
> Answers are stamped with the prompt's name and version. Pilots: two judges + third, 50 units per
> case family; full runs: two judges + third as well (D40, 2026-10-01).

Replaces `docs/runbooks/identify-trial.md` (pick one of the whole shortlist, two judges plus a
third) for P4. That runbook, its prompt and `identify-prepare/combine/record` stay in the repo for
reuse; nothing about them changed.

## What a unit is

A **tier**: one unit (a section-14 description, or a case C/D submission) plus up to 10 of its
candidates. **Since D37 a unit has two candidate sets, judged in this order:**

1. the **literature set** (`literature` in `candidates.jsonl`; routes B+, C+, D) — trials the
   literature names, drug- and indication-checked, each shown with up to 3 abstracts — judged
   with `docs/prompts/trial-literature-match.md`; batch files `literature-NNN.ids`;
2. the **registry shortlist** (`candidates`; routes B−, C−) — judged with
   `trial-candidate-match.md` (B) or `-nolabel.md` (C), batch files `batch-NNN.ids` /
   `nolabel-NNN.ids`.

Tier 1 is the literature set's first ten; registry tiers follow. `tier_id =
<unit_id>#T<k>@<digest>`. A decision records its **route** (`B+`, `B-`, `C+`, `C-`, `D`), which S10
uses as the tier (`docs/TIERS.md`).

The descriptions are the same set the pick-one route judged (`identification.read_units`:
earliest submission only, no label-stated NCT, no borrowed biosimilar section 14, no D24 guard).

## The stop rule

Per unit, tiers are judged in order — literature first, then registry ("+" before "−", D37).
The first tier holding any **Yes** decides, and the answer is its **highest-ranked Yes**. A tier is read only once every candidate in it has a final
answer. Every tier No means **NONE**. Measured on the 11,792 known answers: median rank 2, 74% of
shortlisted answers in tier 1.

## Judges

- **Pilot:** two independent judges (A, B) per tier. A candidate's answer is final when they
  agree; a disagreement goes to a fresh third judge (C) whose answer is final. The pilot's job is
  to measure A/B agreement per candidate.
- **Full run:** the same — **two judges plus a third** (D40, 2026-10-01). D33's one-judge
  exception is withdrawn. `match-combine` defaults to `--judges 2`; `--judges 1` remains only
  for re-reading old one-judge rounds.

## Steps

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
$PY main.py --mode match-prepare --case B|C|D|CD [--limit 50]   # or: make judge-prepare TASK=identify CASE=B
#   -> adjudication/candidate_match_tiers.jsonl (every tier issued)
#   -> adjudication/candidate_match_batches/round-RR/batch-NNN.ids (10 tiers each)
#   judges write adjudication/candidate_match_raw/round-RR/batch-NNN-{A,B}.txt
$PY main.py --mode match-combine                  # two judges + third (D40)
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

**Which fields per batch file** (the message names them; copy the prompt of that file):

| Batch file | Prompt | Fields the judge uses |
|---|---|---|
| `batch-NNN.ids` | `trial-candidate-match.md` v3 | `approval`, `excerpt`, `trial_name`, `enrollment`, `candidates` |
| `nolabel-NNN.ids` | `trial-candidate-match-nolabel.md` v4 | `drug`, `indication`, `approval_date`, `candidates` |
| `literature-NNN.ids` | `trial-literature-match.md` v2 | `approval`, `label_block`, `candidates` (abstracts included) |

### Delivery without files (agent pool, 2026-10-01)

On the agent pool (`http://galaxy4:8093`, `no_tools: true`) the judge cannot read files, so the
prompt is rendered **in code** and sent whole (`service/prompt_render.py`):

```bash
$PY main.py --mode render-prompts --batch adjudication/candidate_match_batches/round-RR/batch-NNN.ids
#   -> batch-NNN.requests.jsonl: one request per tier {request_id, prompt, version, text, expects}
```

- The template is read verbatim from `docs/prompts/<name>.md`; its `**Version:**` must equal the
  version the code stamps on answers, or nothing renders.
- Every `{{field}}` is filled in one pass from the stored tier; an empty field refuses to render.
- **One tier per request.** The judge sees one tier and nothing else — no batch context.
- The reply must answer every expected NCT id exactly once, `Yes` or `No`, and nothing else
  (`prompt_render.tier_reply_lines`); the code then appends `<tier_id> | <NCT> = Yes|No` to
  `candidate_match_raw/round-RR/batch-NNN-{A,B,C}.txt`, the files `match-combine` already reads.
  An incomplete or malformed reply writes nothing; the tier stays undecided for a re-run.
- Judges A and B are separate requests; C is sent only for the `tiebreak_needed` candidates'
  tiers. The orchestrator passes batch paths only and never reads the rendered text.

A tiebreak judge gets the same message plus the `"<tier_id> | <nct>"` lines it must answer, and
answers only those candidates.

## Answers are bound to what the judge saw (2026-09-30)

A tier id carries a digest of every field its prompt shows the judge, and the prompt version:
`<unit_id>#T<n>@<12 hex>` (`candidate_match.input_digest`). When P3 or P2i changes a unit's
candidates, their order, its excerpt or its indication text, the tier gets a new id: the old answers
stay on file under the old id but no longer count, and `match-prepare` batches the new version.
An unchanged tier keeps its id and its answers. Tiers written before digests existed are re-keyed
once from their own stored content (`migrate_legacy_ids`; the carried answers are appended with
`migrated_from`, never rewritten). A decision is recorded again when the tiers it was read from
change (`basis` on each `identify_verdicts.jsonl` row; last row wins).

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
