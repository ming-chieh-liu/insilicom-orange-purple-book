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

**How this table is maintained.** Decisions are append-only and never deleted or renumbered. A new
decision takes the next free number and a date. When it changes an older one, the older row keeps
its original text and gains a status marker at the front of its Decision cell:

- **`[SUPERSEDED by Dn, YYYY-MM-DD]`** — no longer in force; read Dn instead.
- **`[PARTIALLY SUPERSEDED by Dn, YYYY-MM-DD]`** — still in force except for the part Dn names,
  which is stated in the marker.
- **`[CLARIFIED YYYY-MM-DD]`** — wording made precise; the decision itself is unchanged.

A row with no marker is in force. The same markers are applied to the matching bullet in
`CLAUDE.md`, which must never disagree with this table. A decision that is agreed but not yet
reflected in code says so (`not yet implemented`) until the code lands.

| # | Decision | Rationale |
|---|---|---|
| D1 | Output is flat TSV, not a KG | One edge type |
| D2 | **The edge is trial → submission**, keyed `(ApplNo, SubmissionType, SubmissionNo)` | Accelerated→confirmatory conversions collapse at application grain |
| D3 | Scope NDA + BLA, **all years** (87,055 approved events). Era is a column, not a filter | The spine is cheap; linkability should be measured, not assumed away |
| D4 | **[SUPERSEDED by D23, 2026-09-24]** Trial-relevant set = **5,879 ORIG + 2,042 core SUPPL = 7,921** | Core SUPPL = action types New Indication (1,894), Accelerated (120), Confirmatory (28) |
| D5 | **[PARTIALLY SUPERSEDED by D23, 2026-09-24 — the scope is now chosen by submission class, not action type; and "accelerated status and confirmatory conversion are structured fields" does not hold: originals carry no action type at all, so an accelerated ORIG is invisible, and the subtype tags are noisy — see §3a]** **Which submissions are trial-driven is published data** | `ActionTypes_Lookup` + `Join_Submission_ActionTypes_Lookup`. Accelerated status and confirmatory conversion are structured fields — no PDF parsing |
| D6 | **[CLARIFIED 2026-09-24 — "type 2" means `ApplicationDocs.ApplicationDocsTypeID = 2`, which `ApplicationsDocsType_Lookup` names `Label`; it is unrelated to submission class id 2 or action type id 2, both of which mean Efficacy]** **Point-in-time labels come from `ApplicationDocs` type 2** | 29,788 per-submission label PDFs, e.g. `…/2009/017881s010lbl.pdf` |
| D7 | **Trials are identified from §14 prose, not NCT ids** | Only 1/6 originals carry an NCT; 0.9% corpus-wide. This is the core problem the project exists to solve |
| D8 | **Enrollment count is the disambiguating key** | Across 5 drug+condition candidate sets, every candidate had a distinct enrollment — zero collisions |
| D9 | **MeSH descriptor is the shared id space**; biokde is the synonym dictionary | Both sides must normalise to the same space for the join to fire |
| D10 | **[CLARIFIED 2026-10-01 — tier labels A > B+ > {B−, C+, C−, D} since D37]** **[CLARIFIED 2026-09-29 — four tiers since D35: A/B/C/D, defined in `docs/TIERS.md`]** Every row carries an **evidence tier** (A/B/C) | Coverage is uneven; tier C is not the same claim as tier A |
| D11 | Substances normalise to **active moiety**; biosimilar 4-letter suffixes kept **distinct** | Trials name the moiety; a biosimilar's 351(k) rests on its own trials |
| D12 | **Any agent-delegated task needs a versioned prompt + runbook** before it runs | See §9 |
| D13 | **No OCR** | Scanned labels are all pre-2006, which is also pre-PLR with no `14.N` structure to use |
| D14 | **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — literature trials found for a drug + indication become P3 candidates for tier B/C; the stand-alone literature route serves only submissions with no indication, and its links are tier D, not C]** The **Insilicom KG is tier C and is not optional** — 4,539 pre-2008 approvals have no other source | Measured: 95% resolve a drug id, 31% a disease id, 16% yield a registry id |
| D16 | **[PARTIALLY SUPERSEDED by D37, 2026-10-01 — applies to every route and across applications of the same active moiety; not yet implemented]** **[PARTIALLY SUPERSEDED by D26, 2026-09-24 — "whose label carries it" becomes "whose label OR review names it"; the rule itself is unchanged]** **A trial is attributed to the EARLIEST submission whose label carries it**, label-stated ids included | §14 is cumulative: KEYNOTE-087 appears on 37 Keytruda submissions and supported only the first; missing the label-stated branch linked KEYNOTE-189 to later supplements 3,123 times. *Row added 2026-09-29: it was in `CLAUDE.md` but missing from this table* |
| D17 | **[SUPERSEDED by D32, 2026-09-25 — a judged link is tier B whatever the counts; counts are information only]** **[PARTIALLY SUPERSEDED by D27, 2026-09-24 — a second outright tier-A source is added: a review-route link judged PIVOTAL or SUPPORTING]** **The tier is the count**: a judged link is tier A only when label and registry counts agree within 15%; a label-stated NCT is tier A outright | A unit keyed `TABLE 3` carried a label "count" of 3 and was indistinguishable from CHECKMATE-648's 970 vs 970. *Row added 2026-09-29* |
| D18 | **Shortlist recall is the ceiling, measured separately from judge precision** | A shortlist failure looks exactly like a judging failure (CHECKMATE-648 hid as one for two commits). **[CLARIFIED 2026-09-29 — recall is scored on each trial's earliest appearance only (`validate.shortlist_recall`); the 54.1% figure counted later appearances]** *Row added 2026-09-29* |
| D19 | **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — the fallback is judged in the D33 format with one judge on the full run, not "2 calls per unit"; its links are tier D]** The graph route runs **parallel** to the label route, as fallback *and* as a zero-cost cross-check | Fallback needs a judge; cross-check is set membership against existing links |
| D21 | Trial side normalises from CT.gov `derivedSection.*.meshes`, not biokde | 46% / 79% coverage vs 17.8% exact-string, and no NER noise |
| D22 | Use `meshes`, **never** `ancestors` | `ancestors` walks to the root: epinephrine → Hydrocarbons, PCOS → Neoplasms |
| D20 | **[CLARIFIED 2026-09-29 — under D35 the literature tier is D and no tier (A–D) is ever promoted; see `docs/TIERS.md`]** Tier C is **never promoted** above C | The question it answers — "is this plausibly evidence" — is weaker than P4's |
| D15 | Combination regimens yield **one row per (trial, submission)** | KEYNOTE-189 approved pembrolizumab *with* chemo owned by other applications |
| D23 | **2026-09-24 · implemented 2026-09-24 in `service/approval_events.py` (P0 gate met: 11,295); `output/` not yet rebuilt.** The trial-relevant scope is chosen by **submission class code**, one rule for both submission types, with action type used only to exclude two supplement subtypes. ORIG: `EFFICACY, BIOEQUIV, BIOSIMILAR, TYPE 1, 1/4, 2, 2/3, 2/4, 3, 3/4, 4, 4/5, 5, 6, 7, 9, 9- BLA, 10, 10- BLA, UNKNOWN`, blank. SUPPL: `EFFICACY, BIOSIMILAR, BIOEQUIV`, minus supplements whose only efficacy action is 10 New Route or 12 Rx→OTC. **11,295 events** (was 7,921). Full detail, counts and rejected alternatives in §3a | Action type is empty on every original, so it cannot scope them; submission class is populated on both. D4 kept 3 of FDA's 12 efficacy subtypes and so dropped 3,506 efficacy supplements whose letters name trials, and D16 then attributed those trials to the next in-scope supplement — a false edge. Supersedes D4, partly D5 |
| D24 | **2026-09-24 · implemented 2026-09-24: `trial_scope` in P0, withheld after D16 grouping in `identification.read_units`, reasons in `assemble`; `output/` not yet rebuilt.** Supplements whose only efficacy actions are **5 Labeling Change With Clinical Data** or **6 Manufacturing Change With Clinical Data** (1,520) are in scope as a **D16 guard**: P2 reads their label and label-stated NCT ids link, but P4 does not judge their prose descriptions. Unjudged units get the unlinked reason `label_change_not_judged` | They rest on named studies but support a label claim, not a new use. Their labels must be read so that a trial first described there is not later attributed to an in-scope supplement (D16); judging them is a separate, optional P4 run |
| D25 | **[PARTIALLY SUPERSEDED by D30, 2026-09-24 — the letter reader stays; its MeSH coding and the letter → label → openFDA merge move into the single indication step] 2026-09-24 · letter reader implemented 2026-09-24 (`source/approval_letter.py`, `service/letters.py`, `--mode letters`); full download in progress.** **Approval letters (`ApplicationDocsTypeID = 1`) are an indication source**, alongside label §1. A letter's "provides for …" sentence states the approved indication of that exact submission. Letters are **not** a trial source | Letters exist for 95.2% of in-scope submissions since 1998 (7,413 of 7,786) and for almost none before (28 of 3,509); they add 454 submissions that have no label PDF. Their study mentions are mostly postmarketing requirements. Detail §3b |
| D26 | **2026-09-24 · implemented in code 2026-09-25 (`--mode reviews`, `review-role-prepare/-combine`, P5 join); as of 2026-09-29 the review download is ~99% (60 documents and 71 TOC components never fetched; the retry step never run) and the v2 role run has decided 966 of 4,825 units.** **FDA review documents are a trial source**, as a route parallel to the label route: Summary Review (21), Review (3, resolved to its component PDFs) and the pediatric reviews. A protocol id is captured by regex after `Study/Trial/Protocol`, matched **exactly** to one registry study's `orgStudyId` or secondary id, passed through deterministic guards, then given a **role** by two judges (`docs/prompts/review-trial-role.md`). Abbreviated ids are not expanded. Readability is decided **per file** (no text layer → skipped), never by year | Precision over recall, by instruction. Pilot, 2026-09-24: identity 70/70 correct; judges agreed 66/70; the 38 PIVOTAL/SUPPORTING links were checked and confirmed plausible by the user (3 read in full). Detail §3b |
| D27 | **2026-09-24 · implemented in code 2026-09-25 (`assemble`: tier A, `sources`, `attribution_moved`); `output/` not yet rebuilt.** A review-route link judged **PIVOTAL or SUPPORTING is tier A** outright, `evidence_source = review_protocol`, with no count corroboration. D16 applies across the label and review routes together | The FDA names the trial by its sponsor protocol number and the match is exact, so, as with a label-stated NCT (D17), there is nothing to corroborate. Tier C remains below it (D20) |
| D28 | **2026-09-24 · implemented in code 2026-09-25; not yet piloted.** **Every label-route link gets a role judgement at submission grain**, on the §14 text of the submission it is linked to, as a judging step before assembly (P4b) — for label-stated NCTs and shortlist-judged descriptions alike. It uses the review route's six-option design (`PIVOTAL`, `SUPPORTING`, `PK_CLINPHARM`, `NOT_FOR_THIS_APPROVAL`, `FAILED`, `OTHER`) with its own versioned prompt and runbook, and **replaces** the application-grain verdicts in `adjudication/verdicts.jsonl`. Only `PIVOTAL`/`SUPPORTING` link | A mention in §14 is not support. The existing polarity task ran once (2026-09-11/14) on *current* openFDA labels at *application* grain and was never re-run on the rebuilt pipeline's units: **1,055 of 3,361 links (31%) have no support check at all** (491 label-stated, 564 judged), and the other 69% were checked against a later, pruned label. P4 identification asks only "which trial is this", never whether it supported the approval. Supersedes in part the §4 statement that task 4b is "reused unchanged" |
| D29 | **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — "the graph route (tier C) serves what has no usable document" becomes: a submission with an indication but no judgeable §14 description is case C; one with no indication is case D]** **2026-09-24 · implemented in code 2026-09-25; `output/` not yet rebuilt.** **How the sources combine.** (1) **Direct sources first**: a label-stated NCT and a review protocol id name the trial themselves. (2) A §14 description on submission S whose trial name equals a protocol id or acronym the review of S matched, and whose count agrees where both exist, is **identified by that match and skips the shortlist**. (3) The shortlist serves only descriptions no FDA document identifies. (4) The graph route (tier C) serves submissions with no usable label or review. Every link row carries `sources` (every route that named the trial) and `conflict`. Conflict rules in §3c | Direct sources are the FDA naming the trial; spending judge calls re-identifying what a review already named costs calls and creates avoidable disagreements. Different routes naming different trials for one approval is normal (an approval rests on several trials); what must be caught is the same trial given different roles or different submissions, and a judged pick contradicting a direct source |
| D30 | **2026-09-24 · implemented in code 2026-09-24 (`service/indication_normalize.py`, `--mode indication-normalize`; P3 and the graph route read the table); `output/` not yet rebuilt.** **Indications are read in one stage, normalised in one step, and consumed from one table.** Document readers emit raw text only — label §1 and §14 subsection headings (P2a), the letter's "provides for" sentence with its indication-bearing flag (P2b), reviews later (P2c). One indication step codes every source with one coder (exact disease-name match, then the MeSH sweep with the possessive and negation guards), resolves every id to `MESH:D…` **and** `biokdeid`, and writes `submission_indications.tsv`: one row per (submission, source, coded disease), `source` ∈ `letter` · `label_section_1` · `label_14_heading` (keyed to its trial description) · `review` · `openfda_current`, plus a `chosen` flag for the submission-level indication (indication-bearing letter → that label's §1 → openFDA). P3 reads each trial's heading indication from it (falling back to the submission's chosen one), the graph route reads chosen indications instead of the application-level `indications.tsv`, and the deliverable's indication columns come from it | The same concept was coded in three places by two methods into two id formats: `indications.tsv` (application grain, current label, the only path given a `biokdeid`), P3's in-memory heading coding (unsaved, no `biokdeid`) and the letter route. `submission_indication_text.tsv` was written by P2 and read by nothing. The §14 heading is kept as a source because it answers a different question — which disease *this trial* was for — but its coder, exact-then-sweep, becomes the only coder. Supersedes D25's precedence merge in part |
| D31 | **[PARTIALLY SUPERSEDED by D37, 2026-10-01 — primary completion ≤ approval is no longer a filter (start ≤ approval stays); never-ran trials dropped]** **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — its links are tier D, judged in the D33 per-candidate format, not pick-one]** **2026-09-25 · implemented in code 2026-09-25 (`dao.graph_evidence.treatment_edges`, `graph_links` drug-only units, prompt `graph-trial-selection` v2; tier-C judging `service/graph_judging.py` and the P5 join, which did not exist before — `TIER_GRAPH` was defined and unused); not yet run.** **No indication → search the drug's negative-correlation edges.** The indication-based graph walk is unchanged and remains a P3/P4 candidate source. For a submission with a drug and no indication, tier C walks every `relationship_type = 3` edge from the drug to a `Disease` node (direction `12` drug-first / `21` drug-second / unrecorded), best-scored first, reads up to 400 abstracts, mines NCT ids, and keeps those that started **and** reached primary completion on or before the approval; the judge sees the disease each candidate was found under | The indication is what D14 measured as the bottleneck (31% resolve one). Negative correlation is the graph's "drug treats disease" relation; positive correlation (`2`) is dominated by adverse events. A drug-wide literature search is mostly post-approval studies, which the completion bound removes without a judge. Code meanings read off pembrolizumab's 2,888 disease edges; `correlation_type` is a different field with different codes. **Measured 2026-09-25** on 150 unserved submissions with a drug and no indication (of 3,401; 116 of the 150 pre-2000): 143 have a treatment edge, 101 an NCT id in the abstracts, 29 a trial started by the approval, **21 (14%) one also completed by it**, median 2 candidates — e.g. Zulresso's three postpartum-depression trials. A candidate with no registry record is dropped in this mode (a 1996 approval was offered a 2023 COVID-19 trial) |
| D32 | **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — four tiers: C is now an agent pick from drug + indication with no §14 text; literature on the drug alone is D]** **2026-09-25 · implemented in code 2026-09-25 (`assemble.tier_for`); `output/` not yet rebuilt.** **The tier says how the trial's identity was established.** A: an FDA document names it by an identifier — label-stated NCT, review protocol id (D27), label trial name matching a same-submission review id (D29). B: an agent inferred it from §14 prose against a shortlist, whatever the counts. C: literature graph (D14). Role checks (D26, D28) decide whether a link exists, not its tier. Supersedes D17 | D17 promoted a judged link to A on agreeing counts, but the P4 prompt tells the judge the count is its strongest signal — the same evidence twice — and a ±15% match was unique in only 45 of 2,363 known cases. Measured on the rebuilt scope: 366 judged links move A → B; submissions with a tier-A link 1,709 → 1,448 |
| D33 | **[PARTIALLY SUPERSEDED by D40, 2026-10-01 — full runs use two judges plus a third on disagreement; the one-judge exception is withdrawn]** **[PARTIALLY SUPERSEDED by D37, 2026-10-01 — two templates (registry v2, literature v1); candidates show status and why stopped]** **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — the drug pool is indication-filtered and capped at 10 tiers (100); literature candidates are added beyond the cap; the format also judges cases C and D]** **2026-09-28 · implemented in code 2026-09-28 (`service/candidate_match.py`, modes `match-prepare/-combine/-record`; P3 rank order commit 308887d); pilot not yet run.** **P4 judges each candidate Yes/No in ranked tiers of 10, with one judge.** P3 returns every shortlist in rank order with no cap for literature-injected candidates; P4 judges ranks 1–10, then 11–20, …, and a description stops at the first tier with a Yes, taking its highest-ranked Yes; all No is NONE. Ten candidates per call, each answered on its own (prompt `trial-candidate-match` v1). The full run uses **one judge** — a recorded exception to the two-judge convention; the pilot uses two plus a third on disagreement. Decisions are stamped `prompt_version 2` in `identify_verdicts.jsonl` and supersede the pick-one answers; the pick-one prompt, runbook and `identify-*` modes are kept | Manager's request: judging a candidate against the description, not against 60 neighbours, and stopping early. Before this the rank only chose which 100 survived the cap and the list was re-sorted by NCT id, so the order the judge saw was meaningless. Measured on 11,792 known answers with the order fixed: median rank 2; 41.6% / 74.0% / 86.6% of shortlisted answers within ranks 1 / 10 / 30. Cost on the 3,668 P4 descriptions, tiers through the whole list: ~17k tier calls per judge vs 3,668 pick-one calls, mostly from the 66% NONE descriptions that read their whole list. One judge halves it |
| D34 | **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — "condition still ranks, never filters": condition now filters, by MeSH tree match keeping trials where either side has no id]** **2026-09-29 · implemented in code 2026-09-29 (`service/section14.py` `continued_heading`, `service/indications.py` `NON_INDICATION`/`registry_aliases`/`build_index(extra=)`, `service/candidates.py` `pick_indications`/`read_submission_ids`); P2 → P2i → P3 rebuilt 2026-09-29; P4+ not yet re-run on it.** Measured on the 3,821 judged descriptions: with an indication id 64.1% → 96.7%; §14 headings coded 79.2% → 84.4%; judged answers whose trial matches the indication 47.3% → 78.9%; exact-filter loss on judged answers 52.7% → 21.1%, tree-match 44.1% → 12.7%, tree-match keeping id-less 6.5% → 7.2%; answer rank unchanged (top-10 71.0% → 71.5%). **Each §14 description gets every disease it is about, coded from the heading as printed.** (1) A heading wrapped onto the next PDF line is joined back into `label_trials.tsv` `heading_full`; `heading` stays one line because unit ids are built from it. (2) The coder drops descriptors that are never an indication (`Disease`, `Disease Progression`, `Recurrence`, `Death`, `Syndrome`, `Signs and Symptoms`, biomarkers, `Sprains and Strains`…) and lets a branch root (`Neoplasms`, `Infections`) or `Carcinoma` give way to a specific disease in the same text. (3) The sweep also knows registry condition strings NLM codes consistently to one descriptor (≥3 single-condition studies, ≥60% on one id) and the multi-word registry names P1 resolved through biokde; neither overrules a MeSH entry term. (4) P3 ranks on `indication_ids` — all of the heading's ids, else every id of the submission's own letter and label §1 — instead of refusing when there are two; `indication_mesh` stays the single id P5 reports. Condition still ranks and never filters | The user asked for FDA-side coding to be fixed before indication is re-tested as a filter. Measured on the 3,821 descriptions P4 judges: 36% had no indication id (19% no heading and 2+ chosen ids, 9% a heading with no codable disease, 3% a heading with two); an exact filter lost 51% of judged answers, a third of them to a missing id. Top coded ids were `Neoplasms` 1,338, `Disease` 1,207, `Infections` 1,158, `Disease Progression` 497. Examples: "Urothelial Carcinoma" swept to `Carcinoma` (NLM codes 117/117 single-condition studies to D002295); "HIV-1 infection" to `Infections`; Talzenna SUPPL 10's EMBRACA heading lost "or Metastatic Breast Cancer" at the line break and fell back to the letter's prostate cancer. Sample of 711 headings: 81 (11%) wrap |
| D35 | **[PARTIALLY SUPERSEDED by D40, 2026-10-01 — full runs use two judges plus a third, like pilots]** **[PARTIALLY SUPERSEDED by D37, 2026-10-01 — literature candidates are their own routes B+, C+, D, judged with abstracts, not added to the shortlist; tiers A > B+ > {B−, C+, C−, D}]** **[PARTIALLY SUPERSEDED by D36, 2026-09-30 — a submission whose FDA review names a PIVOTAL or SUPPORTING trial is case A and gets no C/D unit]** **2026-09-29 · implemented in code 2026-09-29 (`review_trials.protocol_trials`, `graph_evidence.mentions_in(resolve=)`, `graph_trials` `via`, `graph_links.no_indication_scope`/`drug_only_rows` → `drug_only_trials.tsv`, `candidates.indication_allows`/`route`/`owned_submissions` + case C/D units + `submission_routes.tsv`, `candidate_match` no-label prompt and `--case`, `assemble` tiers C/D); not yet run.** **Route by what the FDA documents give, four tiers (`docs/TIERS.md`), and an indication filter before the cap.** **Case A** — an FDA document names the trial by an identifier (label-stated NCT, review protocol id, label trial name matching the review's id): no search; a role judge (P4r for reviews, P4b/D28 for labels) keeps only pivotal or supporting → **tier A**. **Case B** — a §14 description with no identifier: drug + date pool → **indication filter** (keep a trial whose condition id equals, is an ancestor of, or is a descendant of one of the description's `indication_ids` in the MeSH tree, or where either side has no id; siblings not used) → rank → **top 100 = 10 tiers** → **plus literature candidates** for this drug × indication, unfiltered and beyond the 100 → P4 per-candidate (D33, with excerpt) → **tier B**. A B description judged NONE stays unlinked (`judged_none`). **Case C** — the submission has an indication (its chosen indication, D30) but no judgeable §14 description (no label, pre-PLR text, or a §14 naming no trial): one unit per submission, candidates built as in B, judged per candidate on drug + indication sentence + approval date (new prompt `trial-candidate-match-nolabel`) → **tier C**. **Case D** — no indication: the drug's treats edges (D31) → literature candidates, start and primary completion ≤ approval → the same no-label prompt with the indication "not recorded" → **tier D** (formerly C). **Literature candidates (new P2g, no agents)**: NCT ids in the evidence abstracts of drug × indication edges **and full protocol ids** (letters and digits) that match exactly one registry study by `orgStudyId` or secondary id; bare numbers and ambiguous ids dropped; `graph-trials` is rebuilt on the D34 indications. **Judges**: pilots of B, C and D with two plus a third; full runs with one (the D33 exception). Tiers are never promoted; where routes find the same trial the highest tier wins and the rest go to `sources` | The user's routing: identity from documents; indication → shortlist; no indication → literature. Tier C had been triggered by a missing label, when the real gap is a missing indication. Filter measured on the rebuilt P3 with the filter applied before the cap, on 2,412 label-stated answers in their shortlists: 115 dropped, 87 recovered from below the cap, **net −28 (1.2%)**; P4-judged answers −80 (7.2%, a pessimistic set: none could be below the old cap). Judge calls on the 3,821 descriptions 17,662 → 13,983 (−21%), subagents 1,775 → 1,406. Stricter rules and tier caps lose 6.8–17.1%. Siblings recover 6 answers and match NSCLC to SCLC, Type 2 to Type 1 diabetes |
| D36 | **2026-09-30 · implemented in code 2026-09-30 (`candidates.route`, `ROUTE_REVIEW`; main passes P4r's linking submissions); P3 rebuilt.** **A submission whose FDA review names a trial judged PIVOTAL or SUPPORTING is routed `review` (case A) and gets no C/D unit.** A review naming only non-linking trials does not count; a §14 description (case B) is judged regardless | The user's rule: an FDA document already identifies the evidence, so a drug + indication search adds only a weaker link. Measured on the finished P4r (4,825 units): 279 submissions routed `review`; C/D units with candidates 2,734 → 2,483 (C −249, D −2) |
| D37 | **2026-10-01 · implemented in code 2026-10-01, all parts (S2 `drug_names`; S3 never-ran exclusion and status; S4 §14 `subsection`; S7 PMIDs, found-under and abstracts files; S8 `literature_set`, `drug_check`, literature out of `shortlist`; S9 `LITERATURE_PROMPT`, literature tiers first, route on decisions; S10 route tiers, D16 across routes and `moiety_key`; S11 `first_appearances`); `output/` not rebuilt, not piloted.** **Literature trials are their own routes, judged with their abstracts; six routes, two templates; status shown, never-ran trials dropped.** Routes: **A** an FDA document names the trial · **B+** §14 description × literature trials · **B−** §14 description × registry shortlist · **C+** indication only × literature trials · **C−** indication only × registry shortlist · **D** no indication × drug-only literature. Literature trials leave the ranked shortlist and are judged with up to 3 abstracts naming them; "+" runs first and a Yes stops the unit. Before judging: drop a literature trial whose every non-placebo arm resolves to another FDA drug; for B+ and C+ apply the MeSH-tree indication check (id-less kept). Every route drops WITHDRAWN and NOT_YET_RECRUITING and keeps start ≤ approval; status, why stopped and primary completion are shown, not filtered. Judges see **brand (generic)** from `drug_names.tsv` (brand, generic, ingredients, moieties, UNIIs). Tiers **A > B+ > {B−, C+, C−, D}**; one row per trial and submission, all routes in `sources`. D16 earliest-wins across every route and same-moiety applications | The user's routing: literature candidates have exact identity and need a support check, not a rank. Measured 2026-09-30: literature trials were re-sorted into the shortlist by rank key 3 (6 of the top 8 in a Brukinsa tier, 3 of them other drugs); the drug check removes ~⅓ of literature candidates and loses no known answer; the indication check lost 4–5 of 224 literature-only answers, each a disease-coding error, and 13 of 18 drops were later appearances; registry status of 3,325 label-stated trials: 86% COMPLETED, 11% ACTIVE_NOT_RECRUITING, 1.4% TERMINATED (some for efficacy); 9% show a primary completion after approval; dose-stripped drug names 99.7% precise on 2×200 hand-checked, recall 78.2% → 79.1% |
| D38 | **2026-10-01 · docs and Makefile stage targets implemented 2026-10-01; `--mode` names unchanged.** **The pipeline is eleven stages, S1–S11, named for what they produce:** S1 SPINE · S2 DRUGS · S3 REGISTRY · S4 DOCUMENTS · S5 INDICATIONS · S6 REVIEW ROLES (agents) · S7 LITERATURE · S8 ROUTE + CANDIDATES · S9 JUDGE (agents) · S10 ASSEMBLE · S11 VALIDATE. Old → new: P0→S1 · P1 drug side→S2, trial side→S3 · P2a/b/c→S4 · P2i→S5 · P4r→S6 · P2g→S7 · P3→S8 · P4, P4b→S9 · P5→S10 · P6→S11. Older decisions keep their P-numbers | P0–P6 no longer matched the run order — review roles feed routing (D36), registry normalisation feeds the indication coder (D34) — or the steps (P1 did two unrelated jobs, P2g is not reading a document). The dependency graph is in §4 |
| D39 | **2026-10-01 · implemented in code 2026-10-01; `output/` not yet rebuilt.** **The registry pool:** (1) phase — keep PHASE2, PHASE3, PHASE2/3, PHASE1/2, PHASE4, and PHASE1 with ≥100 enrolled; drop NA, blank, small phase 1 (`candidates.phase_kept`). (2) the lead sponsor is a **rank key after the start date**, before the patient count — the applicant's own or its family's trials first (`source/sponsor_families.py`); never a filter. (3) **PubChem ↔ MeSH through the UNII, strictly one-to-one** (`source/unii_bridge.py` → S2 `id_bridge.tsv`): UNII Records give UNII → CID, MeSH descriptor + supplementary `RegistryNumber` give MeSH → UNII; a pair survives only if each link is unique in both directions; S8 adds each drug id's counterpart. (4) **Prompts v3** (excerpt 3, no-label 3, literature 2), wording unchanged: each candidate shows `Conditions (normalised)` / `Interventions (normalised)` as `Name [MeSH …]`, and the approval its drug and indication codes | Phase shares of 2,122 reachable known answers: PHASE3 78.7%, PHASE2 11.9%, 1/2 3.2%, 2/3 2.9%, PHASE1 1.9% (25.5% of the pool), PHASE4 1.2%, blank 0.1%, NA 0; rule (c) keeps 98.8%, median pool 89 → 61. Sponsor = applicant keeps only 62% of answers (Roche/Genentech, NCI, licensing), so it ranks. The user's rule for the bridge is precision over recall: 14,462 bridges, 584 bridged FDA drugs, 94% share a name word. Codes give the judge NLM's vocabulary, which it may know |
| D40 | **2026-10-01 · implemented in code 2026-10-01 (`match-combine --judges` defaults to 2).** **Every judged route uses two judges plus a third, full runs included.** Withdraws D33's one-judge exception and D35's "full runs 1". Two independent judges answer every candidate; agreement is final; a disagreement goes to a fresh third judge, whose answer is final. Applies to S9 (B±, C±, D) and the S11 precision sample; S6 review roles and D28 label roles already did this | The user's decision: return to the project's two-judge convention rather than rest a link on one sampled answer. Cost: judge calls roughly double, plus a third judge on ~1% of candidates (pilot agreement 99.2%) |
| D41 | **2026-10-01 · implemented in code 2026-10-01 (`candidates.identified_trials`, `already_linked`, `drop_already_linked`; S10 reason `candidates_already_linked`).** **A trial an FDA document already names is not judged again.** Before S9 every judged unit (B±, C±, D) loses each candidate named on the same submission, or on an earlier submission of the same moiety, by a label-stated NCT, a review-resolved §14 name (D29) or a review protocol id judged PIVOTAL/SUPPORTING. Identity, not role (D28 judges role). A later or same-day naming elsewhere does not count. A unit left empty is not judged. Units are not skipped wholesale | The user's rule: a link case A has needs no second judgement. Whole-unit skipping was measured and rejected: 919 of 2,006 submissions with a tier-A link (46%) have two or more A trials, and tier B had added 65 trials on 40 A-linked submissions (e.g. MONARCH 1 beside MONARCH 2's NCT). Before D41, 2,140 candidate slots on 1,357 of 3,699 B units were the submission's own A trial; 0 C units overlap |

### Hard constraints

- **Trial registration did not exist before ~2005.** CT.gov opened 2000, ICMJE required it 2005,
  FDAAA 2007. Pre-2005 approvals have no NCT to link to. Low match rates there are reality.
- **Approval letters begin in 1998** — 37,283 documents, only 2 before that.
- **openFDA labels are a current-state snapshot** — 262,757 records, 262,757 distinct `set_id`s,
  one version each. §14 is cumulative *and pruned*: withdrawn indications are deleted.
- **Insulin and hGH moved NDA → BLA in March 2020** by statute. Not a data error.

---

## 3a. D23 / D24 — which submissions are trial-relevant

Decided 2026-09-24 after reading every Drugs@FDA table (`examine/01_drugsfda_tables/`) and a
sample of FDA approval letters. All counts are **approved NDA/BLA submissions** in the
2026-09-07 `drugsatfda.zip`. **Implemented 2026-09-24** in `service/approval_events.py`; `output/` has not been rebuilt, so
every downstream count in §4 is still D4's.

### Three fields that are easy to confuse

| Field | Table | Per submission | What it says |
|---|---|---|---|
| **Submission class** | `Submissions.SubmissionClassCodeID` → `SubmissionClass_Lookup` (28 codes) | exactly 1 | *what kind* of submission: on ORIG the novelty (`TYPE 1` NME … `TYPE 10`), on SUPPL the area (`EFFICACY`, `LABELING`, `MANUF (CMC)`, `REMS` …) |
| **Action type** | `Join_Submission_ActionTypes_Lookup` → `ActionTypes_Lookup` (59 ids) | 0..n | *what the approval changed*: 12 `Efficacy-*` subtypes plus Labeling/CMC/REMS ones. A FDA-published junction table, not one we build |
| **Document type** | `ApplicationDocs.ApplicationDocsTypeID` → `ApplicationsDocsType_Lookup` (62) | 0..n | *what is attached*: 1 Letter, **2 Label**, 3 Review … Only 2 is used (D6) |

The id `2` means Efficacy in the first two and Label in the third. Use the **code**
(`SubmissionClassCode`, e.g. `TYPE 1`), not the id and not the description: two codes,
`BIOSIMILAR` and `TYPE 10- BLA`, have an **empty** description in the lookup, so a
description-keyed rule silently reads them as blank.

### Why class, not action type

- **Action type is empty on every original.** Of 5,879 NDA/BLA ORIGs, 5,202 join to id 46 (blank
  description), 672 have no join row, 5 carry an empty id. Nothing else in Drugs@FDA describes an
  original, so action type cannot scope ORIGs.
- **On supplements, action type is a refinement of class.** Efficacy-class supplements carry
  Efficacy-* actions 5,542 times; the 8 exceptions are 5 Efficacy-class rows with no efficacy action
  and 3 `BIOSIMILAR`-class rows with one (2 New Indication, 1 Labeling Change w/ Clinical Data). No
  Efficacy-class supplement's non-efficacy actions replace its efficacy one — Labeling-Package
  Insert, REMS-* and Medication Guide appear only alongside.
- **The subtype tags are noisy**, so fine selection among them buys little precision. From the
  approval letters: VIREAD `021356` S38 is tagged *Manufacturing Change w/ Clinical Data* but
  "provides for … pediatric patients 2 to less than 12 years"; TASIGNA `022068` S27 is tagged *New
  Patient Population* but "provides for new indications". Keytruda `125514` S4 (2015-12-18) is
  tagged *Accelerated Approval* and S5 (2015-10-02, NSCLC) *New Indication*; the first approval,
  ORIG-1 2014-09-04, has no action type. Whether FDA's tag means "is an accelerated approval" or
  "relates to an accelerated-approval commitment" is **unverified**; the approval letters
  (document type 1) would settle it.

### ORIG — by class code

| Class code | ORIG | Decision |
|---|---|---|
| TYPE 1 · TYPE 1/4 | 1,764 · 74 | in — new molecular entity, the client's stated priority |
| TYPE 2 · 2/3 · 2/4 | 172 · 17 · 8 | in |
| TYPE 4 · 3/4 · 4/5 | 318 · 29 · 6 | in |
| TYPE 3 | 1,646 | in — sometimes new trials, often bridging; P4 answers NONE where there is none |
| TYPE 5 | 1,173 | in — mostly bridging, §14 often copied from the reference product |
| TYPE 6 · 9 · 9- BLA · 10 · 10- BLA | 86 · 25 · 10 · 18 · 1 | in — a new indication by definition |
| TYPE 7 | 46 | in |
| EFFICACY | 40 | in |
| **UNKNOWN** | 188 | **in** — not legacy noise: 70 are 2020s approvals FDA has not yet classed, including 351(k) biosimilars (TYENNE, AVTOZMA, VEGZELMA …); 27 applications carry 110 current links |
| blank | 145 | in |
| BIOEQUIV · BIOSIMILAR | 0 · 0 | in — no original carries either today; listed so a future release cannot silently drop them |
| TYPE 8 (partial Rx→OTC) | 29 | **out** — 0 links today |
| MEDGAS | 82 | **out** — no label PDF, 0 links |
| S | 2 | **out** — a supplement code on an original |

351(k) biosimilar originals are classed blank 46, TYPE 2 27, TYPE 5 20, EFFICACY 8, TYPE 3 5,
TYPE 2/4 1 (94 Purple Book 351(k) BLAs) — never `BIOSIMILAR`, which exists only on supplements.

### SUPPL — by class code, then action type

| Class | SUPPL | Decision |
|---|---|---|
| EFFICACY | 5,547 | in, minus action-type exclusions below |
| BIOSIMILAR | 42 | in — carries 2 New Indications and 1 Labeling Change w/ Clinical Data |
| BIOEQUIV | 23 | in — 8 tagged Bioequivalence (PK); expected to yield no efficacy trial |
| LABELING · MANUF (CMC) · REMS · S · UNKNOWN · stray TYPE n | 75,564 | out — no efficacy action in volume |

Within the in-scope classes, by the supplement's efficacy actions:

| Efficacy action(s) | n | Treatment |
|---|---|---|
| 8 New Indication · 3 Accelerated · 4 Confirmatory · 2 Efficacy · 7 New Dosing Regimen · 9 New Patient Population · 11 Pediatric · 54 Animal Rule | 3,942 | full pipeline (2,042 of them were already in D4) |
| none, but class in scope | 67 | full pipeline |
| **only 5 Labeling Change w/ Clinical Data or 6 Manufacturing Change w/ Clinical Data** | 1,520 (1,463 + 57; 1,363 with a label PDF) | **D24 guard** — P2 + label-stated NCT only |
| only 10 New Route · 12 Rx→OTC | 83 (32 + 51) | **out** — the drug and its efficacy claim are unchanged; the new evidence is bridging or actual-use data, and an OTC Drug Facts label has no §14 |

No supplement carries 5 or 6 together with another efficacy action.

### Totals

| | Events | With a label PDF |
|---|---|---|
| ORIG in scope | 5,766 | 2,910 |
| SUPPL, full pipeline | 4,009 | 2,851 |
| SUPPL, D24 guard | 1,520 | 1,363 |
| **Trial-relevant (D23)** | **11,295** | 7,124 |
| *was, under D4* | *7,921* | |

113 originals leave scope (MEDGAS, TYPE 8, S), none of them linked. 3,487 supplements enter it (1,967 full pipeline + 1,520 D24 guard), a net change of +3,374. The set of applications barely moves (5,821 → 5,716: 107 applications whose only trial-relevant event was a MEDGAS or TYPE 8 original leave, 2 enter) and the set of active ingredients not at all (2,670 either way) — D23 adds approval events for drugs already in scope, it does not add drugs.

### Considered and not chosen

- **Action type for SUPPL, class for ORIG.** Accurate, but two rules over two fields; the class rule
  with two exclusions says the same thing.
- **Class alone, no exclusions.** Admits the 83 route/OTC supplements. Harmless but pointless.
- **Excluding UNKNOWN.** Loses 110 links and most of the recent biosimilar originals.
- **Excluding 5 and 6 outright.** Their trials would then be attributed by D16 to the next in-scope
  supplement that repeats them.

### Out of D23's reach, recorded so it is not mistaken for coverage

- **ANDA generics** (78% of Orange Book product rows) have no trials of their own; they link to
  trials only by inheriting their Reference Listed Drug's — a product-level join, not a scope rule.
- **CBER biologics** are not in Drugs@FDA (F-P0-2), whatever the scope rule says. The Purple Book
  file on disk *is* FDA's full listing (F-P0-3 corrected): 1,067 CBER product rows, with BLA
  number, names, first licensure and a Supplement column that records product changes, not an
  approval history. 82 CBER BLAs have a current openFDA label. There is no per-submission label
  or submission table for CBER, and no document links in the Purple Book or `ApplicationDocs`.
  **Open item, agreed 2026-09-24 — needed eventually, not started:** a CBER route of its own —
  openFDA current label (82) → DailyMed label history by `set_id` → CBER product pages for
  approval letters and the Summary Basis for Regulatory Action.
- **Medical devices** (client brief, 2026-09-24) are in no source this project reads.

---

## 3b. D25–D27 — letters for indications, reviews for trials

Decided 2026-09-24. Only **approved** submissions are considered; `ApplicationDocs` also holds 282
documents on NDA/BLA submissions that are not approved (144 tentative-approval letters), and those
are ignored. **Not yet implemented.**

### Which document carries what

| Doc type | In-scope submissions (D23) | Indication | Trial evidence | Used for |
|---|---|---|---|---|
| 1 Letter | 7,441 | ✅ "provides for …" in 8/9 sampled | ❌ mostly postmarketing requirements | **indication (D25)** |
| 2 Label | 7,124 | ✅ §1 | ✅ §14 by prose | the existing label route |
| 21 Summary Review | 750 | ✅ | ✅ protocol ids | **trials (D26)** |
| 3 Review | 4,206 | ✅ | ✅ protocol ids, more of them | **trials (D26)** |
| Pediatric Medical / Statistical / Clin-Pharm / CDTL / DD Summary / Medical Secondary / Addendum | ~300–480 each | ✅ | ✅ the pediatric studies | **trials (D26)** |
| Pediatric Written Request, Amendments 1–15, Reissues | — | — | ❌ requests for *future* studies | excluded |
| Medication Guide, REMS, PPI, Other | — | patient language | ❌ | excluded |

### D25 — letters

Coverage over the 11,295 in-scope submissions: 0.9% pre-1998 ORIG and 0.6% SUPPL; 85–98% after,
95.2% overall since 1998. Of the 373 missing since 1998, 17 are filed under a sibling submission's
combined letter and about half of a sampled six exist at the standard URL
(`appletter/<year>/<appl>Orig1s<nnn>ltr.pdf`) without an `ApplicationDocs` row. Letter or label
together cover 7,578 submissions. The extractor must accept both "provides for" and letters that
word it otherwise, and must not stop at a decimal point ("Gel, 1.2%") — both failures were seen in
the pilot.

### D26 — reviews

**Identity** is decided deterministically, the **role** by judges. The pipeline, per
`docs/runbooks/review-trial-role.md`:

1. Fetch; resolve a type-3 table of contents through its embedded `pdfFilenames` map or its
   `pdfBaseName` + `pdfFiles` flags. **Skip any PDF with no text layer** (no fonts, under 2,000
   characters) as `no_text_layer` — per file, not per year: 13 of 15 sampled 1998–2007 type-3
   reviews were scans (producers *Acrobat Scan Plug-in*, *eCopy*), while a 2004 review was
   born-digital and processed normally.
2. Capture the token after `Study|Trial|Protocol [No.]` with letters **and** digits, any length,
   repairing line-break splits. Bare numbers and words are dropped. **Abbreviations are not
   expanded** (`KN555`, `104a`, `A2301` are lost by design).
3. Match exactly one registry study through `orgStudyId` **or** a secondary id (`GOG-0240` is a
   secondary id of NCT00803062, issued by NRG Oncology / CTEP).
4. Guards, all required: start ≤ approval · lead sponsor matches the applicant **or** the trial names
   the application's drug (collaborators such as NCI, and ownership changes such as
   Zogenix → Recro) · the review package names the application's drug (catches
   `ApplicationDocs` mislinks: NDA208551 Triferic carries an Epclusa summary review).
5. D16 across routes: the earliest submission whose label **or** review names the trial keeps it.
6. One unit per `(submission, NCT)`; two judges, a third on disagreement; six roles.

| Role | Links? |
|---|---|
| PIVOTAL | ✅ tier A (D27) |
| SUPPORTING | ✅ tier A (D27) |
| PK_CLINPHARM · NOT_FOR_THIS_APPROVAL · FAILED · OTHER | context rows |

**Measured on the pilot** (80 summary reviews + 40 type-3 submissions):

| | Summary reviews | Type 3 |
|---|---|---|
| readable | 71 / 80 | 27 / 40 (the 2008+ ones; 13 older scans) |
| submissions with ≥1 accepted trial | 15 (19%) | 15 (all 2008+; 60% of the 25 modern) |
| distinct trials, identity correct | 30 / 30 | 40 / 40 |
| judge agreement A = B | 66 / 70 overall; 4 tiebreaks | |
| final roles | PIVOTAL 36 · SUPPORTING 2 · PK_CLINPHARM 14 · NOT_FOR_THIS_APPROVAL 5 · OTHER 13 | |
| calibration | 3 / 4 — the miss (SIMPLE `GS-US-540-5773` → OTHER, expected NOT_FOR_THIS_APPROVAL) is non-linking either way | |

A registry-date rule ("primary completion before approval") was tested and **rejected** as a role
proxy: it catches the two ongoing confirmatory trials (APHINITY, ALEX) but also drops four pivotal
trials of accelerated approvals granted on interim data (Imbruvica `PCYC-1104`, Zykadia `X2101`,
Perjeta `WO20697`, remdesivir `GS-US-540-5823`). The role comes from FDA's wording.

**Before a full run:** prompt v2 (the NOT_FOR_THIS_APPROVAL / OTHER wording, so earlier-approval
recaps land in the former), the letter-sentence fixes above, and a re-run of the calibration units
plus two possible misses (Tyvaso DPI `TIP-PH-101` → OTHER; Caplyta S6 `ITI-007-401` → OTHER).

### D27 — tier

D17 made a label-stated NCT tier A because the FDA named the trial. A review that names the trial
by its sponsor protocol number, matched exactly and guarded, is the same kind of statement, so a
PIVOTAL or SUPPORTING review link is tier A without count corroboration. The label route's
count-based tiering (D17) is unchanged for judged §14 descriptions *[superseded by D32, 2026-09-25: judged descriptions are tier B]*. On the pilot the review route
also exposed two probable D16 misattributions in the label route: Imbruvica's `PCYC-1104` and
Zykadia's `X2101` are named in the **original** approval's review but linked today to SUPPL-17 and
SUPPL-9.

---

## 3c. D28 / D29 — support checks and how the sources combine

Decided 2026-09-24. **D29 and D28 implemented in code 2026-09-25; D28 not yet piloted.**

**D28 as built.** `service/label_role.py`, modes `label-role-units` / `label-role-prepare` /
`label-role-combine`; prompt `docs/prompts/label-trial-role.md` v1, runbook
`docs/runbooks/label-trial-role.md`. Units are P5's links built with `apply_label_roles=False`
(after D16, before any support filter) whose sources include a label source; the judge reads the
passages of that submission's cached label §14 that mention the registry id (with or without a
space), the trial name or the stated count, under their `14.N` headings. Smoke test on 40 current
links: 39 units built, 1 `trial_not_found_in_section_14`. The judging core is shared with the
review route (`service/role_judging.py`). In P5 a current-version role replaces the polarity
verdict; the polarity verdict remains the fallback, counted in the report, until every label link
has a role.

**D29 as built.** *P3* (`service/candidates.py`): `review_names` reads the `accepted` rows of
`review_trial_mentions.tsv` — protocol id as written and the matched study's registry acronym —
per submission; a name pointing at two trials in one submission is dropped, and numberings
(`STUDY 1`, `TABLE 2`) never match. A description whose label trial name squashes equal to one of
them gets `review_nct`, no shortlist, and P4 never sees it. *P5* (`service/assemble.py`): such a
unit links with `evidence_source = review_name_match`, tier A, `sources =
label_description;review_protocol`, and takes part in D16 like a stated id. `conflict` is
`;`-joined. `role_disagrees` holds the row at tier B; its label side is the D28 role, or the old
application-grain polarity verdict where D28 has not judged. `indication_mismatch` compares the MeSH ids of the letter rows
(`letter`, `letter_located`) with those of `label_section_1` in `submission_indications.tsv`, and
flags without changing the tier. Every accepted mention counts for the name match, not only the
review route's earliest, because this is identity; D16 is applied in P5.

### Where each check sits

```
                 LABEL ROUTE                         REVIEW ROUTE (D26)
identify   label-stated NCT        │ protocol id → exact registry match
           or shortlist + judges   │   (guards; identity is deterministic)
           (skipped when a same-   │
            submission review id   │
            already matches, D29)  │
role       P4b role judge (D28)    │ role judge (D26)
           submission grain, own   │ submission grain, review passages
           §14 text                │
                    └──────────── P5: D16 across both routes ────────────┘
                                  sources · conflict · tier
```

P5 applies verdicts; it does not judge.

### Conflict rules (D29)

| Situation | Conflict? | Handling |
|---|---|---|
| Label and review name **different trials** for one submission | no — approvals rest on several trials | union; each trial keeps its own role |
| Same (trial, submission), **roles disagree** (e.g. label `SUPPORTING`, review `FAILED` or `NOT_FOR_THIS_APPROVAL`) | **yes** | `conflict = role_disagrees`; not linked at tier A until resolved |
| Same trial attributed to **different submissions** by the two routes (pilot: Imbruvica `PCYC-1104` named in the ORIG review, linked by the label route to SUPPL-17; Zykadia `X2101` likewise) | **yes** | D16 across routes keeps the earliest; `conflict = attribution_moved` |
| A shortlist pick contradicts a direct source for the **same description** | **yes** | the direct source wins; the judged pick is dropped and recorded `conflict = judged_contradicts_direct` |
| Letter indication not contained in the same submission's label §1 | **rare** | `conflict = indication_mismatch`; label §1 is cumulative, so containment, not equality, is the test |

`sources` lists every route that named the trial for that submission — `label_nct`, `review_protocol`,
`label_description`, `kg_abstract` — so agreement between independent routes is visible, and the
D19 cross-check (tier A with a second source) is one column, not a separate table.

**First measurement, free:** compare the 70 pilot review units with the label-route map for the same
submissions — agreements, `attribution_moved`, and role disagreements — before either route is
wired in.

### [DECIDED by D32, 2026-09-25 — judged links are tier B whatever the counts] Open item — tier A for shortlist-judged links

D17 promotes a judged link to tier A when label and registry counts agree within 15%. The P4 prompt
tells the judge the count is "the strongest single signal", so the judge largely *chooses* on the
count that D17 then *rewards* — the same evidence twice. 422 current tier-A links rest on it. The
only precision measure is the withheld-answer pilot (≈90%, 104/115, all tiers, pre-cap-100). To do
before deciding: score the withheld set by tier; if tier-A judged links fall short of the
precision-first bar, tier A should require a signal the judge did not choose on (an exact
name ↔ acronym/protocol-id match, or agreement with the review route).

---

## 4. Pipeline — eleven stages, six routes

*Redrawn 2026-10-01 for D37 and D38. The P0–P6 diagram (D35, 2026-09-29) is in git history;
the phase detail below still uses the old P-numbers — read them through D38's table.*

```
S1  SPINE          Drugs@FDA -> 87,055 approved NDA|BLA events -> 11,295 trial-relevant (D23, D24)
S2  DRUGS          per application: brand, generic, ingredient(s), moiety(ies), UNII(s)
                   (drug_names.tsv) + normalised drug id and its graph biokdeid (drug_map.tsv)
S3  REGISTRY       every CT.gov study: interventions -> drug ids (NLM MeSH, then biokde names,
                   dose stripped), conditions -> disease ids, status, why stopped, dates;
                   WITHDRAWN / NOT_YET_RECRUITING dropped (trial_drug_map, trial_condition_map)
S4  DOCUMENTS      fetched by hand at 1 req/s, never by make:
                     labels  -> §14 descriptions (NCT | name | count | subsection) + §1 text
                     letters -> "provides for ..." indication sentence (never trials)
                     reviews -> protocol id -> exactly 1 registry study (+ guards)
S5  INDICATIONS    one table, coded once: letter > located > label §1 > today's label;
                   §14 headings coded per description (submission_indications.tsv)
S6  REVIEW ROLES   agents: each review-named trial PIVOTAL / SUPPORTING / other (D26, D27)
S7  LITERATURE     graph: drug x indication edges -> abstracts -> trials they name (NCT or
                   protocol id), with PMIDs; no indication: drug's treats edges (D31);
                   drug check (every arm another FDA drug -> out) + indication check
S8  ROUTE +        each submission / §14 description -> route and candidate sets (D35-D37):
    CANDIDATES       A   an FDA document names the trial          -> no search
                     B+  §14 description  x literature trials      -> with abstracts
                     B-  §14 description  x registry shortlist     -> drug pool, indication
                     C+  indication only  x literature trials         filter, top 100
                     C-  indication only  x registry shortlist
                     D   no indication    x drug-only literature
S9  JUDGE          agents, per candidate Yes/No in tiers of 10, first Yes wins (D33):
                     A: label role (D28)        B+ C+ D: literature template v1
                     "+" before "-"             B- C-:   registry templates v2
S10 ASSEMBLE       earliest submission wins (D16, every route, same-moiety applications);
                   tiers A > B+ > {B-, C+, C-, D}; sources, conflict
                   -> trial_submission_map.tsv, unlinked_submissions.tsv
S11 VALIDATE       shortlist recall · judge precision on withheld answers · route agreement
```

### Dependency graph

Each arrow is "is read by". Boxes are `make` targets (`make <name>` builds it and everything it
needs); `[agents]` stages are run under their runbooks, and their verdict files are inputs that
make checks for staleness but never writes. Raw inputs are on the left.

```
 Drugs@FDA ──────────────> [S1 spine] ──────────┬──────────────┬──────────────────┐
 (drugsatfda.zip)              │                │              │                  │
                               v                v              v                  │
 moiety extract, UNII ───> [S2 drugs] ──┐  [S4 documents]  (labels, letters,      │
 biokde (graph names) ────────┘   │     │   reviews: cached PDFs, --offline)      │
                                  │     │      │    │    │                         │
 CT.gov archive ─────────> [S3 registry] <─────┘    │    │                         │
 (600,762 studies)            │   │     │           │    │                         │
 openFDA labels ──┐           │   │     │           │    └──> S6 REVIEW ROLES ─┐   │
 (today's §1) ────┴─────> [S5 indications] <────────┘         [agents]          │   │
 MeSH tree ───────────────────┘   │     │                                       │   │
                                  │     v                                       │   │
 biokde edges, abstracts ─> [S7 literature] <── S2 (graph biokdeids)            │   │
                                  │     │                                       │   │
                                  v     v                                       v   │
                        [S8 route + candidates] <── S2, S3, S4, S5, S6 verdicts ┘   │
                                  │                                                 │
                                  v                                                 │
                           S9 JUDGE [agents] ── identify, label-role verdicts       │
                                  │                                                 │
                                  v                                                 │
                           [S10 assemble] <── S1, S2, S4, S5, S6, S9 ───────────────┘
                                  │
                                  v
                           [S11 validate]
```

| Stage | `make` target | Writes | Reads |
|---|---|---|---|
| S1 spine | `spine` | `approval_events.tsv` | Drugs@FDA |
| S2 drugs | `drugs` | `drug_names.tsv`, `drug_map.tsv`, `indication_map.tsv` | S1, moiety extract, UNII list, biokde, `indications.tsv` (openFDA) |
| S3 registry | `registry` | `trial_drug_map.tsv`, `trial_condition_map.tsv` | S2, CT.gov archive, biokde |
| S4 documents | `documents` | `label_trials.tsv`, `submission_indication_text.tsv`, `letter_indications.tsv`, `review_trial_mentions.tsv`, review role units | S1, cached PDFs |
| S5 indications | `indications` | `submission_indications.tsv` | S3, S4, letter-span verdicts, openFDA §1, MeSH |
| S6 review roles | agents | `adjudication/review_role_verdicts.jsonl` | S4 units |
| S7 literature | `literature` | `graph_trials.tsv`, `drug_only_trials.tsv` | S2, S5, biokde edges and abstracts |
| S8 route + candidates | `candidates` | `submission_routes.tsv`, `candidates.jsonl` | S2, S3, S4, S5, S6, S7 |
| S9 judge | agents (`make judge-prepare`) | `adjudication/identify_verdicts.jsonl`, `label_role_verdicts.jsonl` | S8 |
| S10 assemble | `assemble` | `trial_submission_map.tsv`, `unlinked_submissions.tsv` | S1, S2, S4, S5, S6, S8, S9 |
| S11 validate | `validate` | report | S8, S10 |

S2 and S3 are both written by `--mode normalize` today (one pass over biokde and the registry);
their targets name the same command.

**Why this works.** §14 rarely names an NCT id but always describes the trial — design, population,
and the patient count. Structured filters cut 600,762 studies to at most 100 candidates (10 tiers since D35), and enrollment
count then separates them. The agent compares a description against ~15 fully-specified candidates
rather than searching a registry.

### Phase detail and gates

**P0 — polish the spine.** *Mostly existing code.* Restrict `service/approval_events.py` to
NDA+BLA; add `action_type`, `orphan`, `review_priority`, `era`. New reader for the two ActionTypes
tables, same shape as `source/raw_table.py`.
→ `approval_events.tsv`. **Gate:** 87,055 events in scope; 11,426 indication-changing;
**7,921 trial-relevant = 5,879 ORIG + 2,042 core SUPPL**. *(Met 2026-09-15.)*
**[SUPERSEDED by D23, 2026-09-24]** New gate, **met 2026-09-24**: 11,295 trial-relevant =
5,766 ORIG + 4,009 full-pipeline SUPPL + 1,520 D24-guard SUPPL, with a `scope_basis` column naming
the class code (and, for SUPPL, the efficacy actions) that admitted each row.

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

**P2 letters — indications (D25, D30).** *`source/approval_letter.py`, `service/letters.py`,
`service/letter_locate.py`.* `--mode letters` fetches type-1 letters of in-scope submissions
(1998+) through `source/polite_fetch.py` and takes the indication sentence by keyword;
`letter-locate-prepare` / `letter-indication-combine` send the rest to agents that point at the
sentence, which code extracts and a second agent pair verifies
(`docs/runbooks/letter-indication.md`; pilot hand-checked 2026-09-25). **Built; full run pending
the fetch.**

**P2i — one indication table (D30).** *`service/indication_normalize.py`.*
`--mode indication-normalize` codes every indication text once — letter, agent-located letter,
label §1, §14 headings, current openFDA label — to `MESH:D…` + `biokdeid`, and marks one source
per submission `chosen` by that precedence. → `submission_indications.tsv`, read by P3, P3c,
the graph links and P5 (review-only links carry the chosen indication). **Built; not yet run on
the full letter set.** *[Status 2026-09-29: rebuilt on the full letter set with D34's coder; the
today's-label fallback is now coded from `indication_text.tsv` by the same coder, not taken from
`indications.tsv` ids.]*

**P2 reviews → P4r — trials by protocol id (D26, D27).** *`source/review_docs.py`,
`service/review_trials.py`, `service/review_role.py`.* `--mode reviews` fetches types 21, 3 (via
the table-of-contents page) and pediatric reviews, skips files with no text layer, matches
protocol ids exactly to one registry study, applies the three guards and D16, and writes one unit
per (submission, trial). `review-role-prepare` / `review-role-combine` run the role judges
(`docs/runbooks/review-trial-role.md`, prompt v2). PIVOTAL/SUPPORTING → tier A,
`evidence_source = review_protocol`. **Built; fetch running 2026-09-25; v2 not yet judged.**
*[Status 2026-09-29: download ~99% — 60 documents and 71 TOC components never fetched, retry
never run; v2 roles 966 of 4,825.]*

**P3 — candidate generation.** *New: `service/candidates.py`.* Index the CT.gov archive once
(600,762 rows; the prototype took ~2 minutes across 25 shards).
→ `candidates.jsonl`, one unit per §14 trial description. **Gate:** median candidate count ≤40;
report the tail. *[D35, 2026-09-29: the cap is 100 (10 tiers) after an indication filter, plus
unfiltered literature candidates; P3 also routes every submission (`submission_routes.tsv`) and
adds one unit per case-C and case-D submission. Gate: report shortlist recall on earliest
appearances and the case counts.]*

**P4 — agent judgement.** *[D33/D35, 2026-09-29: identification is now per candidate in tiers of
ten — `docs/prompts/trial-candidate-match.md` (case B) and `trial-candidate-match-nolabel.md`
(cases C, D), runbook `docs/runbooks/trial-candidate-match.md`; the pick-one task below is kept
for reuse, not run.]* Task 4a is new and needs `docs/prompts/trial-identification.md` plus
`docs/runbooks/identify-trial.md`. Two judges, disagreement to a third. **A wrong match fabricates
an edge**, which is worse than a wrong polarity label, so `NONE` must be an easy, blameless answer.
**[PARTIALLY SUPERSEDED by D28, 2026-09-24 — "reused unchanged" was implemented as reusing the task's *old verdicts*, not re-running the task on the rebuilt units; D28 replaces it with a submission-grain role judgement]** Task 4b is already built — `docs/runbooks/adjudicate-section-14.md`, 2,679 links judged — and is
reused unchanged.
→ `adjudication/verdicts.jsonl`, append-only and git-tracked. **Gate:** 200-unit pilot,
disagreement <25%.

**P5 — assembly.** *New: `service/assemble.py`.*
→ `trial_submission_map.tsv` (2,477 links: 2,473 tier A, 4 tier B) plus
`unlinked_submissions.tsv` (16,311 rows, every one with a `reason`).
**Built 2026-09-16.** Coverage is 14.7% of trial-relevant submissions and rises as P4 completes —
5,317 of the unlinked are simply `not_yet_judged`. *[Status 2026-09-29: those figures are the
pre-D23 map; P5 has not been run on the D23 scope. Since D35 it writes tiers A–D and reads only
identifications of version 3 or later.]*

**P6 — validation.** *New: `service/validate.py`.* Two numbers, scored apart because they fail
differently: **shortlist recall** (P3's ceiling, free to compute — currently **54.1%**, 2,604 of
4,813; *since 2026-09-29 scored on earliest appearances only*) *[Judge precision since D35: runbook
`docs/runbooks/validate-precision.md` — withheld-answer units judged per candidate in
`adjudication/precision_d35/`, one judge as in production.]* and **judge precision** (P4's score, needs `--limit` to withhold answers and run judges).
**Built 2026-09-16.** **Gate:** tier-A precision ≥90%, still to measure.

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
*(This list is the original plan; the reasons `assemble.py` actually emits are in CLAUDE.md's pipeline
section. D24 adds `label_change_not_judged`.)*

---

## 6a. Next work — fix retrieval, then run P4 at scale

Shortlist recall is **54.1%**: for 2,209 of 4,813 cases where the label states the answer, that
answer is not in the candidate list. P4 cannot find what P3 did not offer, so every one of the
~6,700 remaining agent calls is spent against that ceiling. **All four fixes are in P1. None is in
P4.** Ordered by leverage, with what each is expected to move.

### 1 — Trial side reads `derivedSection` **as well as** the biokde index · **P1** · DONE 2026-09-16

**Result: shortlist recall 54.1% → 66.9%**, and 2,604 → 3,224 answers shortlisted.

Written as *derivedSection instead of* the synonym index first, which lost ground: absolute hits
fell to 2,253 while the rate appeared to rise to 62%, because units with no candidates drop out of
the denominator. CT.gov assigns MeSH ids; half the FDA side is keyed on PubChem because the graph
has no MeSH record for those drugs (F-P1-5), and those can only ever be reached through the synonym
index. Both sources now index the same study, guarded by `DESIGN_WORDS` on the inferred one.

---

### 1 (original framing) — trial side reads `derivedSection`

`source/trial_studies.py` currently parses only `protocolSection`. Add
`derivedSection.interventionBrowseModule.meshes` and `conditionBrowseModule.meshes`, and make them
the primary trial-side mapping in `service/normalize.py`.

| | studies with a drug resolved |
|---|---|
| exact whole-string against biokde (today) | 17.8% |
| token containment against biokde (considered, rejected) | 37.3% |
| **CT.gov `derivedSection.meshes`** | **46%** |

Conditions go from the current mapping to **79%**. Use `meshes` only — never `ancestors` (D22).
biokde remains the FDA-side dictionary and the trial-side fallback where `meshes` is absent.

**This supersedes the stop-list and frequency-floor work.** `Control → Chlordiazepoxide` is an NER
artefact of the biokde synonym index (`cnt=2` against `Chlordiazepoxide` at 5,419); NLM does not tag
a control arm as a chemical, so reading a curated mapping removes the class of problem rather than
patching it. Keep the biokde path guarded anyway, since it is still the fallback.

### 2 — Biosimilars get a retrieval id · **P1** · F-P1-2

158 suffixed ingredient names resolve to nothing, and 161 of the 206 unresolved BLA applications
fail for this reason alone. The reported 92.9% hides **NDA 96.2% against BLA 57.4%**.

D11 is right that `USTEKINUMAB-AEKN` must not fold into `USTEKINUMAB` — a 351(k) rests on its own
trials. But nothing carries the stem's id for *retrieval*, so a biosimilar can never receive a
shortlist. Keep the suffixed name as the row's identity and add a separate column P3 filters on.
98 stems involved.

### 3 — Re-run P1 → P2 → P3 and re-measure · **no new code**

`--mode validate` reports shortlist recall for free, from the 4,813 labels that state their own
NCT. **This number decides whether to run P4 at scale.** If it does not move off 54%, something
above is wrong and more agent calls will not fix it.

### 4 — Then P4 at scale · **P4, unchanged**

3,329 remaining tasks, two judges each. Nothing about P4 needs fixing; it needs a better shortlist.

### Not doing yet, and why

- **Judge precision is still unmeasured.** Worth 400 calls on withheld answers, but the answer
  changes once the shortlist changes, so it belongs after step 3.
- **`browseLeaves`** would give a `relevance: HIGH|LOW` tag per term. This archive kept only
  `meshes` and `ancestors`; the field split does the same job. A note for whoever maintains the
  download, not a blocker.
- **F-P0-1** — 5,322 approved submissions dropped for having no `Applications.txt` row, 41 of them
  modern. Needs a decision about typing them from another source.
- **F-P0-2** — "NDA + BLA" is really CDER-only, so ~401 Purple Book BLAs are outside the spine
  entirely. Needs a scope decision, not a fix.

---

## 7. Difficulty register

| # | Difficulty | Status | Evidence |
|---|---|---|---|
| Δ1 | Attribute an approval to a submission | **SOLVED** | Action types published |
| Δ2 | Know which submissions are trial-driven | **REVISED by D23 (2026-09-24)** | Was: same source, originals = all 5,879. Now: submission class code for both, see §3a |
| Δ3 | Accelerated + confirmatory status | **REOPENED (2026-09-24)** | Was "structured field". Originals carry no action type, so an accelerated ORIG is invisible, and the SUPPL tags disagree with Keytruda's history (§3a). Approval letters would settle it |
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
   runs. *[Superseded for P4 by `trial-candidate-match(-nolabel).md` and its runbook, D33/D35.]*
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
