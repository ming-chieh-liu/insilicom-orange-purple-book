# Runbook — tier C: judge the graph's literature candidates

**For:** the main agent · **Prompt:** `docs/prompts/graph-trial-selection.md` (v2)
**Written:** 2026-09-16

## What this does and why

4,539 trial-relevant approvals carry no link at all — every approval before
2008, 57% of the whole set. Their labels are pre-PLR with no numbered Clinical
Studies section, or have one naming no trial, and many of their trials predate
registration entirely. Tiers A and B cannot serve them. The Insilicom graph is
the only source left.

`--mode graph-links` follows each approval's drug↔disease edge in
`pubmedRelations`, reads the abstracts its `locations` cite, and collects the
registry ids printed in them. That produces candidates, not evidence: the papers
behind one edge include reviews, case reports, observational registries, other
drugs' trials and post-approval studies. A judge decides whether any of them is
the trial that supported the approval, and usually the answer is no.

## The rules that make this trustworthy

1. **Two judges per unit, independently.** Agreement required.
2. **Disagreement goes to a third judge, whose answer is final.**
3. **NONE is the expected answer, not a failure.** This is the opposite of P4,
   where the shortlist was engineered to contain the answer and a high NONE
   rate meant something was broken. Here a high NONE rate means the tool is
   working. Do not tune the prompt to reduce it.
4. **A tier-C link is never promoted.** Even two judges agreeing produces tier
   C, because the question they answered — *is this plausibly the evidence* —
   is weaker than the one P4 answers. Tier is about what kind of evidence
   exists, not how confident the judges were.
5. **A judge sees only the drug, the indication, the date and the candidates.**
   No label text, because there is none — that is why the unit exists.
6. **Never write a verdict you did not receive.**

## Step 1 — build the work units

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
cd /data/mliu/insilicom-orange-purple-book

$PY main.py --mode assemble                      # tier C works on what A and B left
$PY main.py --mode graph-links --limit 300       # pilot
$PY main.py --mode graph-links                   # everything
```

Writes `adjudication/graph_units.jsonl`, one object per approval that reached at
least one candidate:

```json
{"unit_id": "BLA019640|SUPPL|58|graph",
 "approval_date": "2006-11-01",
 "indication_term": "Turner Syndrome",
 "pmids_examined": 9,
 "candidates": [{"nct_id": "NCT00960128", "mentions": 1,
                 "trial_report": false, "enrollment": "21249", ...}]}
```

**Two bases (D31, 2026-09-25).** A unit is `basis: "indication"` when the
submission has an indication — the drug↔indication edge, unchanged — or
`basis: "drug_only"` when it has none: every `relationship_type = 3`
(negative correlation) edge from the drug to a `Disease` node, direction drug →
disease or unrecorded, best-scored first up to the 400-abstract budget; each
candidate carries `diseases` (found under) and `primary_completion`, and
drug-only candidates must have completed their primary endpoint on or before
the approval date. The prompt's field table says how each basis fills
`{{indication}}` and `{{candidates}}`.

**The unserved set is read from `unlinked_submissions.tsv`, not recomputed**, so
tier C works on exactly what tiers A and B left behind and cannot quietly
disagree with them. Run `--mode assemble` first or it will work from a stale
list.

## Step 2 — fan out to subagents

**The main agent must not read the candidate blocks.** Take the `unit_id` list
via `jq -r .unit_id`, chunk into batches of **20**, and for each batch launch
**two subagents concurrently** with the identical message. Keep roughly 6 in
flight. Give each subagent its own scratch directory — a shared one gets
overwritten when several run at once, which happened on the first P4 run.

The message to each subagent:

```
Work only under your own scratch directory: mkdir -p /tmp/judge-$$ …

Read /data/mliu/insilicom-orange-purple-book/adjudication/graph_units.jsonl and
find the objects whose "unit_id" is in this list:

  <unit_id>
  ... (20 of them)

For each one, judge it using ONLY that object's fields.

<the full prompt from docs/prompts/graph-trial-selection.md, with {{drug}},
 {{indication}}, {{approval_date}} and {{candidates}} meaning that object's
 corresponding fields>

Do not look up any drug, trial or disease. Do not search the web. Do not open
any file other than the units file named above.

Output one line per case, nothing else:

  <unit_id> = <ANSWER>
```

**Do not paraphrase the prompt.** Copy it verbatim. Verdicts record
`prompt_version: 2`; changing the wording means bumping the version.

## Step 3 — combine, tiebreak, record

Identical machinery to P4 — `service/identification.py` is reused, pointed at
the tier-C directories:

```bash
$PY main.py --mode identify-combine --adjudication adjudication/graph
$PY main.py --mode identify-record --from adjudication/graph/identify_judged.jsonl \
            --adjudication adjudication/graph
```

Answers are validated against that unit's own candidate ids and refused
otherwise.

## Step 4 — report

Report as: units judged, identified versus NONE, tiebreaks, and answers refused.
**Expect NONE to dominate.** On the pilot, a Turner Syndrome unit offered two
candidates enrolling 21,249 and 22,960 — both observational registries from one
paper — and NONE is the right answer there.

If the identification rate exceeds roughly 40%, stop and look at the rows
rather than celebrating: the graph's candidates are weak by construction, and a
judge finding evidence in most of them is more likely to be guessing than
right.

## Scale and what to expect

The first pilot over 300 unserved approvals reached candidates for 132 (44%),
median 6 candidates each. That 44% is **not** the pre-2008 rate: the unserved
set is walked in key order, so 114 of those 132 were 2008+ approvals unlinked
for other reasons and only 18 were pre-2008. A random sample of 150 pre-2008
submissions measured 16% before the abstract budget was raised from 150 to 400.
Measure the target population before quoting a rate for it.

The bottleneck is indication coverage, not the graph: of that pre-2008 sample,
95% resolved a drug id and only 31% a disease id.
