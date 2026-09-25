# Runbook — role of trials named in FDA review documents

**For:** the main agent · **Prompt:** `docs/prompts/review-trial-role.md` (v2)
**Written:** 2026-09-24 · **Status:** wired 2026-09-25 (`reviews`, `review-role-prepare`,
`review-role-combine`); v1 pilot passed (70 units); v2 not yet run.

## How to run it

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
$PY main.py --mode reviews                 # fetch (1 req/s, breaker), extract, match, guard
#   → output/review_trial_mentions.tsv (every id-shaped mention + outcome)
#   → adjudication/review_role_units.jsonl (one unit per submission × trial, earliest only)
$PY main.py --mode review-role-prepare [--limit N]
#   → adjudication/review_role_batches/v2/role-batch-NNN.ids, calibration units first
#   run judges A and B per batch; save answers as
#   adjudication/review_role_raw/v2/role-batch-NNN-{A,B}.txt
$PY main.py --mode review-role-combine
#   → records agreed units in adjudication/review_role_verdicts.jsonl (prompt_version 2),
#     writes role-tiebreak-batch-NNN.ids; save the third judge's answers as
#     role-tiebreak-batch-NNN-C.txt and re-run combine until "nothing pending"
```

A unit counts as decided only with a verdict under the **current** prompt version, so the v1 pilot's
units are judged again under v2; v1 and v2 answers live in separate folders.

## What this does and why

FDA reviews (document types 21 Summary Review, 3 Review and the pediatric reviews) name the studies
behind an approval by the sponsor's **protocol id**, which ClinicalTrials.gov stores as
`orgStudyId`. An exact id match finds the trial with no judgement at all — measured 30/30 correct
on identity across 80 summary reviews. It does **not** say why the review names the trial: pivotal
evidence, supportive evidence, a PK study, a recap of an earlier approval, an ongoing confirmatory
trial, or a failure. That is what the judges decide.

Approval letters are **not** an input to this pipeline — they name almost no evidence studies.
They contribute only the one `provides for …` sentence that tells the judge which approval it is
judging.

## Steps 1–5 — deterministic, no agents

1. **Fetch** the review documents of **approved** in-scope (D23) submissions from `ApplicationDocs`.
   Type 3 table-of-contents pages are resolved to their component PDFs (`MedR`, `StatR`,
   `MultidisciplineR`, `IntegratedR`, `ClinPharmR`, `SumR`, `CrossR`) by parsing the page's embedded
   `var pdfFilenames = {…}` map, or, on newer pages, `pdfBaseName` plus the `pdfFiles` existence
   flags; missing components (404) are skipped.
   **Readability is decided per file, never by year.** Every PDF is opened and tested for a text
   layer: no embedded fonts and under 2,000 extractable characters means it is a scanned image
   (producers seen: *Acrobat Scan Plug-in*, *eCopy*, *CPY Document Producer*). Such a file is skipped
   with reason `no_text_layer` (D13, no OCR); every other file goes through steps 2–5 whatever its
   date. Measured on the pilot: 13 of 15 sampled 1998–2007 type-3 reviews had no text layer, but a
   2004 review (`021267` S9) was born-digital and was processed normally.
   **Excluded document types:** Pediatric Written Request and its Amendments/Reissues (requests for
   future studies), letters, labels, Medication Guides, REMS.
2. **Extract** every token following `Study|Studies|Trial|Protocol [No.|Number|ID]` that contains
   both letters and digits, after repairing line-break splits (`GS-US-281- 0102` → `GS-US-281-0102`).
   Bare numbers and plain words are discarded. **No abbreviation expansion** (`KN555`, `104a` are
   not rebuilt).
3. **Match** the normalised token against every registry study's `orgStudyId` and secondary ids.
   Keep it only if it resolves to **exactly one** NCT.
4. **Guard** — all must hold, else the match is dropped with its reason recorded:
   start date ≤ approval date · lead sponsor matches the applicant **or** the trial's title or
   interventions name the application's drug · the document itself names the application's drug.
5. **D16** — a trial belongs to the earliest submission whose label **or** review names it; later
   mentions are context and are not judged.

## Step 6 — build units

One unit per `(submission, nct_id)`, whatever the number of documents or mentions:

```json
{"unit_id": "NDA202811|ORIG|1|NCT00938717", "protocol_id": "MCP-103-302",
 "approval": "LINZESS · NDA202811 ORIG 1 · approved 2012-08-30 · provides for …",
 "text": "[Summary Review · 7 Clinical/Statistical-Efficacy] … Study MCP-103-302 …"}
```

Written to `adjudication/review_role_units.jsonl` (rebuilt, gitignored).

## Step 7 — judge

Same rules as `docs/runbooks/adjudicate-section-14.md`:

1. **Two independent judges per unit**, launched in the same message. Batches of ≤ 25 unit ids.
2. **The orchestrator never reads `text` or `approval`.** It passes unit ids; each judge reads its
   own lines from the units file.
3. The subagent message contains the prompt from `docs/prompts/review-trial-role.md`
   **verbatim**, plus the file path, the id list, an explicit ban on any lookup, file read or search
   beyond the units file, and the output format `<unit_id> = <OPTION>`, one line per unit, nothing
   else.
4. Raw answers are saved as `adjudication/review_role_raw/batch-NNN-{A,B,C}.txt`.

## Step 8 — combine and tiebreak

| judge A | judge B | final |
|---|---|---|
| equal | equal | that answer |
| different | different | a **fresh third judge** on that unit alone, told nothing of the disagreement; its answer is final |

Missing or unparseable answers leave the unit undecided for re-run — never guessed. If more than a
quarter of a batch disagrees, stop and report: the prompt or the snippet extraction is wrong.

## Step 9 — record

Append to `adjudication/review_role_verdicts.jsonl` (append-only, tracked in git):

```json
{"unit_id": "…", "judge_a": "PIVOTAL", "judge_b": "PIVOTAL", "tiebreak": null,
 "final": "PIVOTAL", "prompt_version": 1}
```

Only `PIVOTAL` and `SUPPORTING` produce a trial → submission link (`evidence_source =
review_protocol`); every other answer is kept as a context row with its role.

## What the report must state

Units built and why others were dropped (per guard) · agreement rate between judges A and B ·
tiebreaks · final answer distribution · the calibration units' answers against their expected
roles · for the pilot, the per-document-type identity precision and coverage.

## Pilot

Before any full run: the summary-review units already matched (80-review sample) plus a 40-submission
type-3 sample. A document type is enabled for the full run only after its own pilot measurement.
