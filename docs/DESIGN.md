# Linking FDA drug approvals to the trials that supported them

## Context

The question: **which drug was approved by FDA on the evidence of which trial?**

The repo already answers a weaker version of it. `output/trial_approval_map.tsv` holds 2,679
adjudicated trial→approval links, but at **application** grain. That is the wrong grain, and the
existing `service/attribution.py` shows why: of its 2,679 rows, only **52 (2%)** resolve to a
specific submission, 477 are `ambiguous`, and 2,134 default to the original approval. It
discriminates on dates alone, which cannot separate the forty supplements on a single application.

Keytruda is the case that makes this concrete. BLA125514 was approved for melanoma on 2014-09-04
(accelerated, on KEYNOTE-001) and again on 2015-12-18 (converted to regular, on KEYNOTE-002/006).
Same drug, same indication, two approval events, two different trial sets — the second existing
precisely because FDA demanded confirmatory evidence. At application grain these collapse into one
node and the confirmatory-evidence relationship disappears.

This plan rebuilds the mapping at **submission** grain, and it is based on measurements taken
against the real corpus rather than assumptions. Several assumptions were tested and discarded
along the way; §8 records them so they are not retried.

---

## 1. Goal and output

Output is **flat TSV**, not a knowledge graph — one edge type repeated a few thousand times is a
table.

The deliverable is `trial_submission_map.tsv`, one row per `(nct_id, submission)`, with an evidence
tier on every row and a companion `unlinked_submissions.tsv` that gives a reason for every approval
event that produced no link. Coverage must be auditable, not implied.

---

## 2. Domain model

FDA data is four levels, and the word "drug" names none of them:

| Level | Key | Note |
|---|---|---|
| Application | `ApplNo` | A marketing licence, **not** an approval. A folder. |
| **Submission** | `ApplNo` + `SubmissionType` + `SubmissionNo` | **The approval event.** `ORIG-1`, then `SUPPL-1..n` for the product's commercial life |
| Product | `ApplNo` + `ProductNo` | dosage form + strength |
| Substance | UNII / RxNorm | active moiety |

Pathways: **NDA** (§505(b), small molecule, Orange Book) · **ANDA** (§505(j), generic, Orange Book) ·
**BLA 351(a)** (biologic, Purple Book) · **BLA 351(k)** (biosimilar, Purple Book).

"New Drug Application" does not mean new substance — novelty is the ORIG submission class
(Type 1 = New Molecular Entity, Type 3 = New Dosage Form, Type 4 = New Combination).

---

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Output is flat TSV, not a KG | One edge type |
| D2 | **The edge is trial → submission**, keyed `(ApplNo, SubmissionType, SubmissionNo)` | Accelerated→confirmatory conversions collapse at application grain |
| D3 | Scope NDA + BLA, **all years** (87,055 approved events). Era is a column, not a filter | The spine is cheap; linkability should be measured, not assumed away |
| D4 | Trial-relevant set = **5,879 ORIG + 2,042 core SUPPL = 7,921** | Core SUPPL = action types New Indication (1,894), Accelerated (120), Confirmatory (28) |
| D5 | **Which submissions are trial-driven is published data** | `ActionTypes_Lookup` + `Join_Submission_ActionTypes_Lookup`. Accelerated status and confirmatory conversion are structured fields — no PDF parsing |
| D6 | **Point-in-time labels come from `ApplicationDocs` type 2** | 29,788 per-submission label PDFs, e.g. `…/2009/017881s010lbl.pdf` |
| D7 | **Trials are identified from §14 prose, not NCT ids** | Only 1/6 originals carry an NCT; 0.9% corpus-wide. This is the core problem the project exists to solve |
| D8 | **Enrollment count is the disambiguating key** | Across 5 drug+condition candidate sets, every candidate had a distinct enrollment — zero collisions |
| D9 | **MeSH descriptor is the shared id space**; biokde is the synonym dictionary | Both sides must normalise to the same space for the join to fire |
| D10 | Every row carries an **evidence tier** (A/B/C) | Coverage is uneven; tier C is not the same claim as tier A |
| D11 | Substances normalise to **active moiety**; biosimilar 4-letter suffixes kept **distinct** | Trials name the moiety; a biosimilar's 351(k) rests on its own trials |
| D12 | **Any agent-delegated task needs a versioned prompt + runbook** before it runs | See §9 |
| D13 | **No OCR** | Scanned labels are all pre-2006, which is also pre-PLR with no `14.N` structure to use |
| D14 | The **Insilicom KG is a cross-check and recall backstop**, not a required path | 126 candidates vs ~8 real for pembrolizumab/NSCLC |
| D15 | Combination regimens yield **one row per (trial, submission)** | KEYNOTE-189 approved pembrolizumab *with* chemo owned by other applications |

### Hard constraints

- **Trial registration did not exist before ~2005.** CT.gov opened 2000, ICMJE required it 2005,
  FDAAA 2007. Pre-2005 approvals have no NCT to link to. Low match rates there are reality.
- **Approval letters begin in 1998** — 37,283 documents, only 2 before that.
- **openFDA labels are a current-state snapshot** — 262,757 records, 262,757 distinct `set_id`s,
  one version each. §14 is cumulative *and pruned*: withdrawn indications are deleted.
- **Insulin and hGH moved NDA → BLA in March 2020** by statute. Not a data error.

---

## 4. Pipeline — six phases

```
P0 spine        Submissions + Applications + ActionTypes -> 87,055 approved NDA|BLA events
                trial-relevant = 5,879 ORIG + 2,042 core SUPPL = 7,921

P1 normalize    biokde pubmedEntitiesFlat as synonym dictionary -> MeSH descriptor
                  FDA drug -> MeSH          FDA indication -> MeSH
                  trial intervention -> MeSH   trial condition -> MeSH

P2 labels       ApplicationDocs type 2 -> per-submission label PDF (~4,305 available)
                anchor ^14 CLINICAL STUDIES; split on 14.N; per description extract
                acronym, NCT if present, ENROLLMENT COUNT, phase, design, population

P3 candidates   CT.gov archive (600,762) filtered by
                drug-MeSH + condition-MeSH + phase + start < approval_date
                -> 10-40 candidates   (measured: 16 / 11 / 33 / 14 / 10)

P4 agents       4a identify: which candidate is this description? or NONE   [AGENT]
                4b polarity: supporting vs failed / safety / PK   [ALREADY BUILT]

P5 assemble     tier A/B/C -> trial_submission_map.tsv
                + unlinked_submissions.tsv, a reason for every unlinked event

P6 validate     against the 2,209 label-cited NCT links; KG cross-check for conflicts
```

**Why this works.** §14 rarely names an NCT id but always describes the trial — design, population,
and the patient count. Structured filters cut 600,762 studies to 10–40 candidates, and enrollment
count then separates them. The agent compares a description against ~15 fully-specified candidates
rather than searching a registry.

### Phase detail and gates

**P0 — polish the spine.** *Mostly existing code.* Restrict `service/approval_events.py` to
NDA+BLA; add `action_type`, `orphan`, `review_priority`, `era`. New reader for the two ActionTypes
tables, same shape as `source/raw_table.py`.
→ `approval_events.tsv`. **Gate:** 87,055 events in scope; 11,426 indication-changing;
**7,921 trial-relevant = 5,879 ORIG + 2,042 core SUPPL**. *(Met 2026-09-15.)*

**P1 — normalization layer.** *New: `service/normalize.py`, `dao/biokde.py`.* The Mongo client is
pooled, constructed once in `main.py`, injected. Build four mappings, each with a coverage report.
Resolve three measured traps: `M` concept ids must roll up to `D` descriptors; drugs with no MeSH
descriptor need a fallback key (semaglutide → PubChem only); code names are separate concepts
(`MK-3475` ≠ pembrolizumab's id). Exclude `placebo`.
→ `drug_map.tsv` (2,270), `indication_map.tsv` (1,259), `trial_drug_map.tsv` (7,585),
`trial_condition_map.tsv` (13,824). **Gate:** ≥80% of trial-relevant applications resolve a drug —
**met 2026-09-16 at 92.3%** (5,370 of 5,821).

**P2 — label acquisition and §14 extraction.** *New: `source/label_pdf.py`,
`service/section14.py`.* Download and cache type-2 PDFs; never refetch. Detect scans by `prntlbl`
naming plus ≥49k bytes/page and skip them. Anchor on `^14 CLINICAL STUDIES` to the next top-level
section — a naive regex over flat text matches table data and TOC entries and must not be used.
→ `label_trials.tsv`. **Gate:** ≥90% of non-scanned labels yield a §14 region.

**P3 — candidate generation.** *New: `service/candidates.py`.* Index the CT.gov archive once
(600,762 rows; the prototype took ~2 minutes across 25 shards).
→ `candidates.jsonl`, one unit per §14 trial description. **Gate:** median candidate count ≤40;
report the tail.

**P4 — agent judgement.** Task 4a is new and needs `docs/prompts/trial-identification.md` plus
`docs/runbooks/identify-trial.md`. Two judges, disagreement to a third. **A wrong match fabricates
an edge**, which is worse than a wrong polarity label, so `NONE` must be an easy, blameless answer.
Task 4b is already built — `docs/runbooks/adjudicate-section-14.md`, 2,679 links judged — and is
reused unchanged.
→ `adjudication/verdicts.jsonl`, append-only and git-tracked. **Gate:** 200-unit pilot,
disagreement <25%.

**P5 — assembly.** Extend `service/approval_map.py` and `service/attribution.py`.
→ `trial_submission_map.tsv` plus supporting tables. Every unlinked event gets a `reason`.

**P6 — validation.** Score against the 2,209 label-cited NCT links.
**Gate:** tier-A precision ≥90%.

---

## 5. Input schema

| Source | Location | Fields used |
|---|---|---|
| **Drugs@FDA** | `fda_raw/drugsfda/drugsatfda.zip` | `Applications.txt` · `Submissions.txt` · `ApplicationDocs.txt` · **`ActionTypes_Lookup.txt`** · **`Join_Submission_ActionTypes_Lookup.txt`** · `SubmissionPropertyType.txt` · `Products.txt` |
| **Label PDFs** | `ApplicationDocsURL`, type 2 | §14 prose (use `https://`, follow redirects) |
| **openFDA labels** | `fda_raw/label/drug-label-*.json.zip` | `clinical_studies`, `indications_and_usage`, `openfda.application_number` |
| **CT.gov archive** | `/data/mliu/insilicom-clinical_trials/final/output_raw` | `manifest.jsonl.gz` + 25 `studies-*.jsonl.gz`; nctId, acronym, orgStudyId, startDate, phases, **enrollment**, interventions, conditions |
| **Purple Book** | `fda_raw/purplebook/purplebook-2026-08.csv` | BLA no, Proper Name, BLA Type, Ref Product |
| **Orange Book** | `fda_raw/orangebook/EOBZIP.zip` | products, patent, exclusivity |
| **UNII** | `fda_raw/unii/UNIIs.zip` | UNII, preferred name, active moiety |
| **MeSH** | `/data/mliu/ontology_normalization_tp/external_data/MeSH/desc2026.xml` | descriptor ids/terms |
| **biokde** | `mongodb://galaxy3:27017`, db `biokde` | `pubmedEntitiesFlat` (nodes + synonyms), `pubmedRelations.locations`, `pubmedDocs` |

**biokde gotcha:** evidence PMIDs are in `locations`, formatted `"<pmid>.<node_two>.<node_one>"` —
not in `context` (co-occurring entities) or `contList`. And `biokdeIDTest` is a **different id
space**; always resolve through `pubmedEntitiesFlat`.

---

## 6. Output schema

**`trial_submission_map.tsv`** — grain: one row per `(nct_id, submission)`.

| Column | Values |
|---|---|
| `nct_id` | `NCT\d{8}`, validated against the manifest |
| `appl_no` | 6-digit zero-padded |
| `app_type` | `NDA` \| `BLA` |
| `submission_type` | `ORIG` \| `SUPPL` |
| `submission_no` | integer |
| `action_type` | `New Indication` \| `Accelerated Approval` \| `Confirmatory Study` \| `` (ORIG) |
| `approval_date` | `YYYY-MM-DD` |
| `indication_mesh` / `indication_term` | `D######` / string |
| `substance` / `unii` | active moiety, normalised |
| `partner_drugs` | `;`-separated moieties, `` for monotherapy |
| `evidence_tier` | `A` \| `B` \| `C` |
| `evidence_source` | `label_nct` \| `label_description` \| `kg_abstract` |
| `trial_role` | `supporting` \| `failed` \| `safety` \| `pk` \| `unclear` |
| `match_basis` | `nct_stated` \| `enrollment+design` \| `acronym` \| `manual` |
| `trial_start` | `YYYY-MM-DD` or partial |
| `date_consistent` | `1` \| `0` \| `` |
| `conflict` | `` \| `kg_disagrees` \| `polarity_split` \| `date_inconsistent` |

Supporting tables: `approval_events.tsv` (spine) · `label_trials.tsv` · the four P1 mapping tables ·
**`unlinked_submissions.tsv`** with `reason` ∈ `no_label` \| `scanned` \| `no_section_14` \|
`no_drug_mapping` \| `no_candidates` \| `agent_none` \| `pre_registration_era` \| `not_trial_driven`.

---

## 7. Difficulty register

| # | Difficulty | Status | Evidence |
|---|---|---|---|
| Δ1 | Attribute an approval to a submission | **SOLVED** | Action types published |
| Δ2 | Know which submissions are trial-driven | **SOLVED** | Same source; originals = all 5,879 |
| Δ3 | Accelerated + confirmatory status | **SOLVED** | Structured field |
| Δ4 | Get a point-in-time label | **SOLVED** | 29,788 per-submission PDFs |
| Δ5 | §14 polarity | **SOLVED, BUILT** | 2,679 links judged |
| Δ6 | §14 text extraction | **MOSTLY SOLVED** | 10/13 clean; scans detectable |
| Δ7 | Diff labels to isolate what a supplement added | **FAILED — abandoned** | 0/10 clean diffs |
| Δ8 | §14 names trials by prose, not NCT | **OPEN — core problem** | 1/6 originals; 0.9% corpus-wide |
| Δ9 | Narrow 600,762 trials to a judgeable set | **SOLVED** | 10–33 candidates |
| Δ10 | Disambiguate within that set | **LIKELY SOLVED** | Enrollment distinct 5/5, zero collisions |
| Δ11 | §14 states the enrollment count | **MEASURED (P2)** | 9,697 of 22,666 descriptions state one |
| Δ20 | FDA names the salt, trials name the moiety | **SOLVED (P1)** | `source/moiety.py`; 580 drugs now resolve via the moiety |
| Δ21 | The term sweep inverts negated diseases | **SOLVED (P3)** | `indications.NEGATORS`; SCLC rows 155 → 21 |
| Δ12 | Drug → MeSH across brand/INN/code | **SOLVED (P1)** | Synonym expansion folds them: `Keytruda`, `MK3475`, `PEMBROLIZUMAB` all → `MESH:C582435` |
| Δ13 | Condition → MeSH | **SOLVED (P1)** | `M`→`D` rollup from `desc2026.xml`; `Gastric Cancer` and `Stomach Neoplasms` both → `MESH:D013274` |
| Δ14 | Drugs with no MeSH descriptor | **HANDLED (P1)** | Kept under their own vocabulary, prefix and all — 1,341 of 2,270. Both sides use the same id, so the join still fires |
| Δ19 | `NA` is a placeholder id, not an identifier | **SOLVED (P1)** | Excluded by `PLACEHOLDER_IDS`; it had inflated trial matches 7,585 → 44,874 |
| Δ15 | Enrollment semantics: analyzed vs enrolled | **OPEN** | Not yet measured |
| Δ16 | A wrong identification fabricates an edge | **OPEN — design constraint** | Two judges + blameless `NONE` |
| Δ17 | Pre-2005 trials unregistered | **WON'T FIX** | Hard ceiling |
| Δ18 | Combination regimens many-to-many | **DECIDED** | One row per (trial, submission) |
| Δ22 | §14 is cumulative, so a trial recurs on every later label | **SOLVED (P4)** | KEYNOTE-087 on 37 Keytruda submissions; earliest-wins (D16) cuts 4,963 units to 3,007 |

---

## 8. Rejected approaches

Tested and discarded. Do not retry without new evidence.

- **Diffing consecutive submission labels** (Δ7). 0/10 clean diffs. `207981|SUPPL|12`, a New
  Indication supplement, grew §14 from 5,267 → 9,625 chars with **identical** subsection headings
  and **identical** NCT ids — the new trial prose went inside existing subsections. Keytruda
  (19→21 subsections, 29→32 NCTs) is a best case and misled an earlier version of this plan.
- **NCT-first from labels.** Only 0.9% of 265,514 labels cite an NCT — 2,209 links across 869
  NDA/BLA applications. Too thin to be primary, but it is the **gold standard** for P6.
- **Downloading historical DailyMed labels.** FDA already publishes per-submission labels (D6), and
  pre-PLR labels have no §14 at all — going back in time *lowers* coverage.
- **OCR for scanned labels** (D13). Would serve at most ~215 pre-2006 submissions, which are also
  pre-PLR with no `14.N` structure.
- **Approval letters as the primary indication source.** Still useful as a fallback, but the
  per-submission label is the stronger signal because it carries the trials too.
- **Naive regex over flat PDF text.** Matches table data and TOC cross-references. Section
  anchoring is required.

---

## 9. Convention — agent-delegated work requires a runbook

**General rule, belongs in `CLAUDE.md` for all projects.** Reference implementation:
`docs/runbooks/adjudicate-section-14.md`.

Before any task is fanned out to subagents there must exist:

1. A **versioned prompt** in `docs/prompts/<task>.md`, copied into each subagent message
   **verbatim, never paraphrased**. Changing wording bumps the version; the version is stamped on
   every stored result.
2. A **runbook** in `docs/runbooks/<task>.md` naming: what a work unit is, how units are built, how
   many judges, how answers combine, how disagreement resolves, where results are written, and what
   the final report must state.
3. **Two independent judges per unit** minimum; agreement required for the positive verdict.
4. **Disagreement escalates to a fresh third judge** whose answer is final. Do not average, do not
   re-run the original judges, do not decide it in the orchestrator.
5. **Minimal context per judge** — no hints, no expected answer. If the judge has file or web
   access, forbid lookups explicitly.
6. **Never record a result that was not returned.** A guessed verdict is worse than a missing one.
7. **The orchestrator must not read the payload** — subagents read their own inputs from disk.
8. **Results append-only and git-tracked**, outside any directory the pipeline truncates.
9. **Resumable by construction** — decided units are skipped.
10. **Pilot before the full run.**

---

## 10. Files to write

1. **`CLAUDE.md`** — goal, domain model, decisions, pipeline, data locations, gotchas, and §9.
2. **`docs/DESIGN.md`** — this document. It currently lives outside the repo and must be committed.
3. **`docs/prompts/trial-identification.md`** + **`docs/runbooks/identify-trial.md`** — before P4a
   runs.
4. Code per §4, respecting the existing layout: entry wiring in `main.py` only, readers in
   `source/`, logic in `service/`, parsers in `utility/`, DB access in `dao/`.

---

## 11. Verification

- **Per phase:** the gates in §4.
- **End to end:** `python main.py --mode <phase>` for each phase, then score
  `trial_submission_map.tsv` against the 2,209 label-cited NCT links — precision, recall, and tier
  disagreement.
- **Spot check:** KEYNOTE-189 (`NCT02578680`) must land on BLA125514's 2018-08-20 NSCLC supplement,
  not on ORIG-1. Keytruda's melanoma pair must produce two rows — accelerated 2014 and confirmatory
  2015 — with different trials.
- **Gates per CLAUDE.md:** `mypy` zero errors; `pytest` configured in `pyproject.toml`; coverage
  overall ≥85%, core ≥90%, utility ≥80%. Conda env `clinical-trials-claude`.
