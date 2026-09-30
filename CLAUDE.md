# Orange Book, Purple Book → clinical trials

## Goal

Answer **which drug was approved by FDA on the evidence of which trial**.
Output is **flat TSV**, not a knowledge graph — one edge type repeated a few thousand times.

Full design, measurements and rejected approaches: **`docs/DESIGN.md`**.

## The domain model that matters

FDA data is four levels, and "drug" names none of them:

| Level | Key | Note |
|---|---|---|
| Application | `ApplNo` | A marketing licence, **not** an approval. A folder. |
| **Submission** | `ApplNo` + `SubmissionType` + `SubmissionNo` | **The approval event.** `ORIG-1`, then `SUPPL-1..n` |
| Product | `ApplNo` + `ProductNo` | dosage form + strength |
| Substance | UNII / RxNorm | active moiety |

Pathways: NDA (§505(b), small molecule, Orange Book) · ANDA (§505(j), generic, Orange Book) ·
BLA 351(a) (biologic, Purple Book) · BLA 351(k) (biosimilar, Purple Book).

"New Drug Application" does **not** mean new substance — novelty is the ORIG submission class
(Type 1 = New Molecular Entity, Type 3 = New Dosage Form, Type 4 = New Combination).

## Standing decisions

**Decisions are append-only.** Never delete or renumber one. A new decision takes the next free
number and a date; when it changes an older one, the older bullet keeps its text and gains a marker
at the front — `[SUPERSEDED by Dn, date]`, `[PARTIALLY SUPERSEDED by Dn, date — which part]`, or
`[CLARIFIED date]` for a wording fix. No marker means in force. `docs/DESIGN.md` §3 is the full
record and carries the same markers; the two must never disagree. A decision agreed but not yet in
code says `not yet implemented` until it lands.

- **D2 — the edge is trial → submission**, keyed `(ApplNo, SubmissionType, SubmissionNo)`.
  Keytruda melanoma was approved 2014-09-04 (accelerated, KEYNOTE-001) and again 2015-12-18
  (converted to regular, KEYNOTE-002/006). Same drug, same indication, two events, two trial sets.
  Application-grain linking destroys this.
- **D3 — scope NDA + BLA, all years** (87,055 approved events). Era is a column, not a filter.
- **[SUPERSEDED by D23, 2026-09-24]** **D4 — trial-relevant set = 5,879 ORIG + 2,042 core SUPPL
  = 7,921.**
- **[PARTIALLY SUPERSEDED by D23, 2026-09-24 — scope is chosen by submission class, not action
  type; and the last sentence does not hold: originals carry no action type, so an accelerated
  ORIG is invisible, and the subtype tags are noisy (DESIGN §3a)]** **D5 — which submissions are
  trial-driven is published data.** `ActionTypes_Lookup` + `Join_Submission_ActionTypes_Lookup`:
  New Indication (1,894), Accelerated Approval (120), Accelerated Approval Confirmatory Study (28).
  **Accelerated status and confirmatory conversion are structured fields — no PDF parsing.**
- **[CLARIFIED 2026-09-24 — "type 2" is `ApplicationDocsTypeID = 2` = `Label`; unrelated to
  submission class id 2 or action type id 2, which both mean Efficacy]** **D6 — point-in-time
  labels come from `ApplicationDocs` type 2**, 29,788 per-submission label PDFs
  (`…/2009/017881s010lbl.pdf`).
- **D7 — trials are identified from §14 prose, not NCT ids.** Only 1/6 originals carry an NCT;
  0.9% corpus-wide. This is the core problem the project exists to solve.
- **D8 — enrollment count is the disambiguating key.** Across 5 drug+condition candidate sets,
  every candidate had a distinct enrollment — zero collisions.
- **D9 — MeSH descriptor is the shared id space**; biokde `pubmedEntitiesFlat` is the dictionary.
- **[CLARIFIED 2026-09-29 — four tiers since D35: A/B/C/D, defined in `docs/TIERS.md`]** **D10 — every row carries an evidence tier** (A/B/C).
- **D11 — normalise to active moiety**, but keep biosimilar 4-letter suffixes distinct.
- **D13 — no OCR.** Scanned labels are all pre-2006, which is pre-PLR with no `14.N` structure.
- **D21 — the trial side normalises from ClinicalTrials.gov's own `derivedSection`, not from
  biokde.** `derivedSection.interventionBrowseModule.meshes` and `conditionBrowseModule.meshes`
  carry NLM-curated MeSH ids: 46% and 79% coverage against my exact-string matching's 17.8%, and
  with none of the NER noise — NLM does not tag a control arm as a chemical. biokde stays the
  **FDA-side** dictionary (an ingredient name has no derivedSection) and the trial-side fallback
  where `meshes` is absent.
- **D22 — use `meshes`, NEVER `ancestors`.** `ancestors` is the MeSH hierarchy walked to the root:
  an epinephrine trial carries `Hydrocarbons`, and a polycystic-ovary trial carries `Neoplasms`.
  Matching on it would join a drug to everything sharing a benzene ring. Its legitimate use is the
  opposite — as a **specificity signal**, since a term with many ancestors sits deep in the tree and
  discriminates, while one with none is a root descriptor P3 should decline to rank on.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — the fallback is judged in the D33 format with one judge on the full run, not "2 calls per unit"; its links are tier D]** **D19 — the graph route is PARALLEL to the label route, and serves two purposes.** As a
  *fallback* it needs a judge (2 calls per unit) and runs only where the label route is exhausted.
  As a *cross-check* it needs no agent at all — it is a set-membership test against links that
  already exist, and it is the cheaper and more valuable of the two because it gives tier A an
  independent second source instead of resting on the label alone.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — literature trials found for a drug + indication become P3 candidates for tier B/C; the stand-alone literature route serves only submissions with no indication, and its links are tier D, not C]** **D14 — the Insilicom KG is tier C, and it is NOT optional.** It was scoped as "not a required
  path" and therefore skipped, which was wrong: **4,539 trial-relevant approvals — every one before
  2008, 57% of the set — have zero links from any other source.** Measured on 150 pre-2008
  submissions: 95% resolve a drug id, only **31%** a disease id (the bottleneck is indication
  coverage, not the graph), 29% have an edge and 16% yield a registry id, median 2 candidates.
  A tier-C link is never promoted above C however confident the judges are — the question they
  answer ("is this plausibly the evidence") is weaker than P4's.
- **[CLARIFIED 2026-09-29 — recall is scored on each trial's earliest appearance only; the 54.1% below counted later appearances]** **D18 — shortlist recall is the ceiling, and is measured separately from judge precision.**
  Currently **54.1%**: 2,209 of 4,813 known answers are not in their own shortlist, so no judge can
  ever find them. A shortlist failure looks exactly like a judging failure in the output —
  CHECKMATE-648 hid as one for two commits — so they are scored apart. Matching the label's trial
  name against the registry's acronym recovers only 170 of the 2,209, so the gap is elsewhere.
- **[SUPERSEDED by D32, 2026-09-25 — a judged link is tier B whatever the counts; counts are information only]** **[PARTIALLY SUPERSEDED by D27, 2026-09-24 — a second outright tier-A source is added: a review-route link judged PIVOTAL or SUPPORTING; count tiering still governs judged §14 descriptions]** **D17 — the tier is the count.** A judged link reaches tier A only when the label's patient count
  and the registry's agree within 15%; otherwise tier B, with both counts in the row. A label that
  states its own NCT id is tier A outright — the FDA said it, there is nothing to corroborate. This
  exists because a unit keyed `TABLE 3` carried a label "count" of 3 (a table number the extractor
  read as an enrollment) and its row was indistinguishable from CHECKMATE-648's 970 against 970.
- **[PARTIALLY SUPERSEDED by D26, 2026-09-24 — "whose label carries it" becomes "whose label OR review names it"; the rule itself is unchanged]** **D16 — a trial is attributed to the EARLIEST submission whose label carries it.** Section 14 is
  cumulative: a subsection is added at the approval it supported and then never removed.
  KEYNOTE-087 appears on **37** separate Keytruda submissions, 2017-05-10 through 2026-07-10; it
  supported the first (classical Hodgkin lymphoma). Attributing it to all 37 would make the
  deliverable's central claim 36 times wrongly. Later appearances are context, not evidence.
  **This applies to label-stated ids too, not only judged ones** — missing that branch linked
  KEYNOTE-189 to supplements four years after the one it supported, 3,123 rows of it.
- **D23 — scope by submission class CODE, for both ORIG and SUPPL** (2026-09-24, **implemented
  in code 2026-09-24; P0 gate met; `output/` not yet rebuilt**). Action type is empty on every original, so it cannot scope them; class is
  populated on both. Key on `SubmissionClassCode` (`TYPE 1`), never the id or the description —
  `BIOSIMILAR` and `TYPE 10- BLA` have empty descriptions. **ORIG in:** `EFFICACY, BIOEQUIV,
  BIOSIMILAR, TYPE 1, 1/4, 2, 2/3, 2/4, 3, 3/4, 4, 4/5, 5, 6, 7, 9, 9- BLA, 10, 10- BLA, UNKNOWN`,
  blank. **ORIG out:** `TYPE 8`, `MEDGAS`, `S`. **SUPPL in:** `EFFICACY, BIOSIMILAR, BIOEQUIV`, minus
  supplements whose only efficacy action is 10 New Route or 12 Rx→OTC. `UNKNOWN` is in because it
  is mostly 2020s approvals FDA has not yet classed, recent biosimilars among them — 110 links sit
  on it. **11,295 events** (5,766 ORIG + 4,009 SUPPL + 1,520 D24), was 7,921. D4 dropped 3,506
  efficacy supplements whose letters name trials, and D16 then gave those trials to the next
  in-scope supplement — a false edge. Detail: `docs/DESIGN.md` §3a.
- **D24 — "with clinical data" supplements are a D16 guard, not a judging target** (2026-09-24,
  **implemented in code 2026-09-24; `output/` not yet rebuilt**). Supplements whose only efficacy action is 5 Labeling Change With
  Clinical Data or 6 Manufacturing Change With Clinical Data (1,520) go through P2 and link their
  label-stated NCT ids, but P4 does not judge their prose. Unjudged units get reason
  `label_change_not_judged`. They must be read so a trial first described there is not attributed
  by D16 to a later supplement.
- **[PARTIALLY SUPERSEDED by D30, 2026-09-24 — coding and the source merge move into the single indication step]** **D25 — approval letters are an indication source** (2026-09-24, **letter reader implemented 2026-09-24; full download in progress**).
  `ApplicationDocsTypeID = 1`; the "provides for …" sentence states the approved indication of that
  exact submission. Coverage 95.2% of in-scope submissions since 1998, ~0% before; adds 454 with no
  label. **Not a trial source** — their study mentions are postmarketing requirements. The
  extractor must handle other wordings and not stop at decimals ("Gel, 1.2%").
- **D26 — FDA reviews are a trial source, parallel to the label route** (2026-09-24, **implemented
  in code 2026-09-25; as of 2026-09-29 the review download is ~99% — 131 files never fetched, retry
  step never run — and roles are judged for 966 of 4,825 units**). Summary Review (21), Review (3, via its table of contents) and pediatric reviews;
  never Pediatric Written Requests. Protocol id after `Study/Trial/Protocol` (letters + digits, any
  length, **no abbreviation expansion**) → exactly one registry study by `orgStudyId` **or**
  secondary id → guards (start ≤ approval · sponsor = applicant or trial names the drug · package
  names the drug) → D16 across label and review → two judges assign a role
  (`docs/prompts/review-trial-role.md`, `docs/runbooks/review-trial-role.md`). Skip a PDF with no
  text layer, **per file, never by year**. Pilot: identity 70/70, judges agree 66/70.
- **D27 — a review link judged PIVOTAL or SUPPORTING is tier A outright** (2026-09-24, **implemented
  in code 2026-09-25; `output/` not yet rebuilt**), `evidence_source = review_protocol`. The FDA named the trial by its sponsor
  protocol number and the match is exact, so as with a label-stated NCT there is nothing to
  corroborate. Tier C stays below it.
- **D28 — every label link gets a role judgement at submission grain** (2026-09-24, **implemented
  in code 2026-09-25; not yet piloted**). A §14 mention is not support. The old polarity task ran once on *current* labels at
  *application* grain and was never re-run: 1,055 of 3,361 links (31%) have no support check, and P4
  identification never asks about support. New P4b step, before assembly, on the linked
  submission's own §14 text, same six roles as D26; replaces `adjudication/verdicts.jsonl`. Only
  `PIVOTAL`/`SUPPORTING` link. As built: units from P5's links with no support filter
  (`label-role-units`, cached labels only), prompt `docs/prompts/label-trial-role.md` v1, runbook
  `docs/runbooks/label-trial-role.md`; a non-linking role leaves the link out as
  `label_role_<role>`; where no role exists yet the old polarity verdict still filters, and P5
  counts those links — D28 is complete when that count is 0.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — "the graph route (tier C) serves what has no usable document" becomes: a submission with an indication but no judgeable §14 description is case C; one with no indication is case D]** **D29 — how the sources combine** (2026-09-24, **implemented in code 2026-09-25; `output/` not yet rebuilt**). Direct sources first
  (label-stated NCT, review protocol id). A §14 description whose trial name matches a protocol id or
  acronym the same submission's review matched skips the shortlist. The shortlist serves only
  descriptions no FDA document identifies; the graph route (tier C) serves what has no usable
  document. Rows carry `sources` and `conflict` (`role_disagrees`, `attribution_moved`,
  `judged_contradicts_direct`, `indication_mismatch`). Different trials from different routes for one
  approval is normal, not a conflict. As built: a description resolved by name gets `evidence_source =
  review_name_match`, tier A; a `role_disagrees` row is held at tier B; the label side of a role
  disagreement is the D28 role, falling back to the old polarity verdict where D28 has not judged. Detail: `docs/DESIGN.md` §3c; its open question on D17 tier A for judged links was settled by D32.
- **D30 — indications: read once, normalise once, one table** (2026-09-24, **implemented in
  code 2026-09-24; `output/` not yet rebuilt**). Readers emit raw text (label §1 + §14 headings, letter sentence, reviews later);
  one step codes all of it with one coder (exact disease name, then MeSH sweep) into `MESH:D…` +
  `biokdeid` and writes `submission_indications.tsv` (source · source_key · text · ids · `chosen`).
  P3 reads per-trial heading indications from it; the graph route reads chosen submission
  indications instead of application-level `indications.tsv`. Replaces three separate coding paths;
  `submission_indication_text.tsv` had no reader.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — its links are tier D, judged in the D33 per-candidate format, not pick-one]** **D31 — no indication: search the drug's treatment edges** (2026-09-25, **implemented in
  code 2026-09-25; not yet run**). The graph route (D14, tier C) keeps walking drug ↔ *this
  submission's indication*, unchanged, and that stays one of P3/P4's candidate sources. Only where a
  submission has **no indication at all** does it fall back to the drug alone: every
  `pubmedRelations` edge with `relationship_type = 3` (negative correlation: the drug treats the
  disease) from the drug to a `Disease` node, direction drug → disease or unrecorded, best-scored
  first up to the 400-abstract budget → PMIDs → NCT ids → start ≤ approval **and** primary
  completion ≤ approval → the same P4c judges (prompt v2), tier C. Units carry
  `basis = drug_only` and each candidate the disease(s) it was found under. Measured on 150 such
  submissions: 21 (14%) reach candidates, median 2; a trial with no registry record is dropped here.
  **Tier C joins P5 as of 2026-09-25** (it never did before — `TIER_GRAPH` was unused): judging via
  `graph-prepare`/`graph-combine` (`service/graph_judging.py`, prompt v2, answers checked against
  the unit's own candidates), then a pick becomes a tier-C `kg_abstract` link only where no other
  route links that trial for the application; where one does for the same submission, it only adds
  `kg_abstract` to `sources`.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — four tiers: C is now an agent pick from drug + indication with no §14 text; literature on the drug alone is D]** **D32 — the tier says how the trial's identity was established** (2026-09-25, **implemented in
  code 2026-09-25; `output/` not yet rebuilt**). **A**: an FDA document names the trial by an
  identifier — label-stated NCT, review protocol id (D27), or a label trial name matching an id the
  same submission's review named (D29). **B**: an agent inferred which registry trial the §14 prose
  describes (P4 shortlist pick), **whatever the patient counts say**. **C**: literature graph,
  never promoted (D14). Agents still judge a trial's *role* inside tier A (D26, D28); a role check
  decides whether a link exists, not its tier. The counts stay on every row as information. Why:
  the P4 prompt tells the judge the count is its strongest signal, so D17 rewarded the evidence
  the judge chose on; and a ±15% match was unique in only 45 of 2,363 known cases. On the rebuilt
  scope 366 judged links move A → B; submissions with a tier-A link 1,709 → 1,448.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — the drug pool is indication-filtered and capped at 10 tiers (100); literature candidates are added beyond the cap; the format also judges cases C and D]** **D33 — P4 judges each candidate Yes/No, in ranked tiers of 10, with one judge** (2026-09-28,
  **implemented in code 2026-09-28; pilot not yet run**). P3 returns every shortlist in rank order,
  with no cap for literature-injected candidates (commit 308887d); P4 works down it in tiers of 10
  (ranks 1–10, 11–20, …) and a description stops at the first tier holding a Yes, taking its
  highest-ranked Yes; every tier No is NONE. Ten candidates share one call, each answered on its
  own (prompt `docs/prompts/trial-candidate-match.md` v1, runbook
  `docs/runbooks/trial-candidate-match.md`). **The full run uses one judge** — a recorded
  exception to the two-judge convention, taken for cost; the pilot runs two judges plus a third
  on disagreement to measure agreement. Decisions go to `identify_verdicts.jsonl` stamped
  `prompt_version 2`, superseding the pick-one answer (the pick-one prompt, runbook and
  `identify-*` modes stay for reuse). Why: manager's request; measured on 11,792 known answers
  the true trial sits at median rank 2 and 74% of shortlisted answers are in tier 1.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — "condition still ranks, never filters": condition now filters, by MeSH tree match keeping trials where either side has no id]** **D34 — each §14 description is ranked on every disease it names, coded from the heading as
  printed** (2026-09-29, **implemented in code 2026-09-29; P2 → P2i → P3 rebuilt 2026-09-29;
  P4+ not yet re-run on it**). Wrapped headings are joined (`label_trials.tsv` `heading_full`; `heading`
  unchanged, unit ids depend on it). The coder drops never-an-indication descriptors (`Disease`,
  `Disease Progression`, `Recurrence`, `Death`, biomarkers…), lets `Neoplasms`/`Infections`/`Carcinoma`
  give way to a specific disease, and also sweeps registry condition strings NLM codes consistently
  (≥3 single-condition studies, ≥60% one id — "Urothelial Carcinoma" → D002295) plus P1's
  biokde-resolved names, never overruling MeSH. P3 ranks on `indication_ids`: all heading ids, else
  every id of the submission's own letter + label §1, instead of refusing on two. Condition still
  ranks, never filters. Why: 36% of judged descriptions had no id and an exact filter lost 51% of
  answers; detail `docs/DESIGN.md` §3.
- **[PARTIALLY SUPERSEDED by D36, 2026-09-30 — a submission whose FDA review names a PIVOTAL or SUPPORTING trial is case A and gets no C/D unit]** **D35 — route by what the FDA documents give; four tiers; indication filters before the cap**
  (2026-09-29, **implemented in code 2026-09-29; not yet run**). Tiers are defined in `docs/TIERS.md`.
  **A**: an FDA document names the trial (label NCT, review protocol id, label name = review id) →
  role judge (P4r, P4b) keeps pivotal/supporting. **B**: a §14 description with no id → drug + date
  pool → **indication filter** (MeSH tree: same, ancestor or descendant; kept when either side has
  no id; no siblings) → rank → **top 100 (10 tiers)** → **plus literature candidates** for drug ×
  indication, unfiltered, beyond the 100 → P4 per-candidate with excerpt. A B description judged
  NONE stays `judged_none`. **C**: an indication but no judgeable §14 description → one unit per
  submission, same candidates, per-candidate judge on drug + indication sentence + date (new
  prompt). **D** (formerly C): no indication → drug's treats edges (D31) → literature candidates →
  same no-label prompt. **Literature candidates** (new P2g, no agents): NCT ids in the evidence
  abstracts plus full protocol ids matching exactly one registry study; `graph-trials` rebuilt on
  the D34 indications. Pilots 2 judges + third, full runs 1. Never promoted; highest tier wins.
  Why: the user's routing — the tier-C trigger was a missing label, the real gap a missing
  indication. Measured: the filter nets −28 of 2,412 label-stated answers (115 dropped, 87
  recovered from below the cap) and cuts judge calls 17,662 → 13,983 (−21%).
- **D36 — a submission whose review names a linking trial is not searched as case C/D**
  (2026-09-30, **implemented in code 2026-09-30; P3 rebuilt**). P3 routes it `review` (tier A via
  P4r) when the FDA review names a trial judged PIVOTAL or SUPPORTING; one whose review trials are
  all non-linking (PK, other, failed, not for this approval) stays C/D. A §14 description (case B)
  is judged regardless. Why: the evidence is already identified, and a drug + indication search
  would only add a weaker link. Measured: 279 submissions routed `review`; C/D units with
  candidates 2,734 → 2,483 (−251).

## Current status and to-do

Full detail, evidence and commands: **`docs/STATUS.md`** (audited 2026-09-29) — read it at the
start of a session and update it when a stage finishes or pauses. As of 2026-09-29:

1. **P2c review route done** — download as complete as FDA allows (118 of 131 missing files are
   404 on FDA); **P4r review roles 4,825 / 4,825 decided (2026-09-30)**.
2. **D34 implemented and rebuilt through P3; D35 implemented in code (Phase 1 done); both uncommitted.**
3. Next (Phase 2): finish the review download → rebuild P2g (`graph-trials`, `graph-links`) → P3 → pilots B and C/D (two
   judges) → full runs (one judge) with P4r alongside → P5 → D28 → P5 → P6.
4. The D33 pilot so far is obsolete (built on the pre-D34 order); `trial_submission_map.tsv` is
   still the 2026-09-23 pre-D23 map.

## Hard constraints

- Trial registration did not exist before ~2005 (CT.gov 2000, ICMJE 2005, FDAAA 2007). Pre-2005
  approvals have no NCT to link to. Low match rates there are reality, not failure.
- openFDA labels are a **current-state snapshot** — 262,757 records, 262,757 distinct `set_id`s.
  §14 is cumulative *and pruned*: withdrawn indications are deleted.
- Approval letters begin in 1998 (37,283 documents, only 2 before that).
- Combination regimens are legitimately many-to-many: KEYNOTE-189 approved pembrolizumab *with*
  chemo owned by other applications.

## Pipeline — six phases, four sources, four cases (D35)

```
P0 spine      Submissions + Applications + ActionTypes -> 87,055 approved NDA|BLA events
              -> D23: 5,766 ORIG + 4,009 SUPPL + 1,520 D24-guard SUPPL = 11,295 trial-relevant
                              |
P1 normalize  FDA side  : ingredient -> MeSH/PubChem via biokde synonyms (+ moiety fold)
              TRIAL side: derivedSection.*BrowseModule.meshes (NLM) + biokde name matches
                              |
P2 read       P2a LABELS  (ApplicationDocs 2)  §14: NCT | trial name | count | heading (+wrap, D34); §1
              P2b LETTERS (ApplicationDocs 1)  "provides for …" -> indication only, never trials
              P2c REVIEWS (ApplicationDocs 21, 3 TOC, pediatric) -> protocol id -> exactly 1 registry
                          study (orgStudyId | secondary id) + guards (date, sponsor|drug, package)
              P2i INDICATION TABLE (D30, D34): letter > letter_located > label §1 > openfda_current,
                          coded once to MeSH + biokdeid -> submission_indications.tsv
              P2g LITERATURE (no agents, D35): graph edges drug x indication -> abstracts ->
                          NCT ids + full protocol ids (exactly 1 registry study) -> graph_trials.tsv;
                          no indication: drug's treats edges (D31) -> drug_only_trials.tsv
                              |
P3 route      every trial-relevant submission / §14 description -> submission_routes.tsv
      +-----------------------+---------------------------+-------------------------+-------------+
      | CASE A                | CASE B                    | CASE C                  | CASE D      |
      | an FDA document names | a §14 description, no id  | indication known, no    | no          |
      | the trial: label NCT, |                           | §14 description of its  | indication  |
      | review protocol id,   |                           | own (no label, pre-PLR, |             |
      | label name = review id|                           | §14 names no trial)     |             |
      |                       | drug + start <= approval  | same candidate build as | drug-only   |
      | no search             | -> indication filter      | B, on the submission's  | literature  |
      |                       |    (MeSH tree; id-less    | chosen indication       | candidates, |
      |                       |    trials kept)           |                         | start and   |
      |                       | -> rank -> top 100        |                         | completion  |
      |                       |    (10 tiers)             |                         | <= approval |
      |                       | + literature candidates,  |                         |             |
      |                       |   unfiltered, beyond 100  |                         |             |
      v                       v                           v                         v             |
P4    P4r review role (D26)   P4 identify (D33): per candidate Yes/No, tiers of 10, first Yes wins
      P4b label role (D28)        B: excerpt prompt       C, D: no-label prompt (D35)
      2 judges + 3rd; only        pilots 2 judges + 3rd, full runs 1 judge
      PIVOTAL|SUPPORTING link
      -> TIER A               -> TIER B                   -> TIER C                 -> TIER D
      +-----------------------+---------------------------+-------------------------+-------------+
                              |
P5 assemble   D16 earliest-wins across label AND review (A, B); C and D never displace a stronger
              link, only add to `sources`; highest tier wins -> trial_submission_map.tsv
              columns sources, conflict; + review_trial_context.tsv + unlinked_submissions.tsv
                              |
P6 validate   shortlist recall · judge precision (docs/runbooks/validate-precision.md) · route
              agreement by `sources` · graph cross-check
```

**Letters give indications, never trials; reviews give trials by the sponsor's own protocol id**
(D25, D26). Both are fetched at 1 request/s with a breaker after 5 consecutive 403s — FDA banned
this host's IP after an unthrottled run. A review link and a label link to the same trial meet in
P5's D16; a later mention moves nothing, an earlier one moves the link and sets
`conflict = attribution_moved`.

**[PARTIALLY SUPERSEDED by D35, 2026-09-29 — the fallback no longer reads `unlinked_submissions.tsv`: P3 routes a submission with no indication to case D directly, and literature trials for a drug + indication are case B/C candidates; the zero-cost cross-check (2) is unchanged]** **The label and graph routes are parallel and independent, not sequential.** The graph route
branches at P1 and skips P2/P3 entirely, because the approvals it serves have no
readable section 14 — that is the whole reason it exists. It does two jobs:

1. **Fallback.** For approvals the label route exhausted, it is the only source.
   Runs on `unlinked_submissions.tsv` reasons that mean *exhausted*
   (`no_label`, `judged_none`, `no_candidates`,
   `no_drug_resolved`) — **not** `not_yet_judged`, which means P4 has not looked
   yet, and **not** `later_appearance_of_the_same_trial`, which is D16 working.
   Nor `label_change_not_judged` (D24): P4 skipped it by decision, it is not exhausted.
2. **Cross-check, and this costs no agent calls.** For links the label route
   already produced, ask whether the graph's literature for that drug and
   disease mentions the same trial. Agreement between two independent sources is
   stronger than either alone; silence is worth flagging. Populates `conflict`.

**Why it works:** §14 rarely names an NCT but always describes the trial — design, population, and
the patient count. Structured filters cut 600,762 studies to at most 100 candidates (10 tiers); enrollment count
then separates them. The agent compares against ~15 fully-specified candidates, it does not search.

## Outputs

`approval_events.tsv` (spine) · four P1 mapping tables · `label_trials.tsv` ·
**`trial_submission_map.tsv`** (deliverable) · `unlinked_submissions.tsv` (a `reason` for every
unlinked event — this is what keeps recall auditable).

## Data locations

| What | Where |
|---|---|
| Drugs@FDA | `fda_raw/drugsfda/drugsatfda.zip` |
| openFDA labels | `fda_raw/label/drug-label-*.json.zip` (14 shards) |
| Purple Book | `fda_raw/purplebook/purplebook-2026-08.csv` |
| Orange Book | `fda_raw/orangebook/EOBZIP.zip` |
| UNII | `fda_raw/unii/UNIIs.zip` |
| MeSH | `/data/mliu/ontology_normalization_tp/external_data/MeSH/desc2026.xml` |
| Moiety extract | `/space/feng/share/drug_label/drug_indication_market.csv.gz` (another team's; current labels only) |
| CT.gov archive | `/data/mliu/insilicom-clinical_trials/final/output_raw` (manifest + 25 shards) |
| Insilicom KG | `mongodb://galaxy3:27017`, db `biokde`, creds in `creds.json` (gitignored) |

KG schema: `pubmedEntitiesFlat` = nodes (`biokdeid`, `id` like `MESH:D002289`, synonyms) ·
`pubmedRelations` = edges · **evidence PMIDs are in `locations`**, formatted
`"<pmid>.<node_two>.<node_one>"`, *not* in `context` or `contList` · `pubmedDocs` = abstracts.

## Known traps

- **`biokdeIDTest` is a different id space** from `pubmedRelations` — always resolve through
  `pubmedEntitiesFlat`.
- **`pubmedRelations` stores relation types as bare codes, in two look-alike fields.** Filter on
  `relationship_type`: `3` = negative correlation (treats — NSCLC for pembrolizumab, score 3,007),
  `2` = positive correlation (adverse events), `1` = association. `correlation_type` is a
  different field whose `0` happens to coincide with `relationship_type = 3`; reading it once
  produced the opposite conclusion. `direction` `12` = node one → node two. See D31.
- **Keep the `biokdeid`.** The prefixed `id` (`MESH:C582435`) is the *external* vocabulary id and
  does not appear in `pubmedRelations` at all; edges are keyed on the internal `biokdeid`
  (`6308113`). P1 carries it on every mapping row so P5 can walk drug → disease edges without
  re-resolving. Entities that arrive already coded — indications, which come out of
  `indications.tsv` as bare MeSH ids — need `BiokdeRepository.internal_ids()`, since they never
  pass through a name lookup.
- **MeSH `M` ids vs `D` ids:** `Stomach Neoplasms` → `MESH:D013274` but `Gastric Cancer` →
  `MESH:M0333444`. Same disease. Roll `M*` up to its descriptor or the join silently fails.
- **Code names are separate concepts:** `MK-3475` → `MESH:M0586474`, not pembrolizumab's
  `MESH:C582435`. Fold code names in explicitly.
- **Not everything has a MeSH descriptor:** semaglutide/Ozempic → `PubChem:CID56843331` only.
- **The FDA names the salt, a trial names the moiety.** 723 of 2,270 resolved drugs are salts of a
  different moiety, and where the graph gives the two different ids the drug matches *no* trials —
  silently, because zero candidates looks like a drug whose trials were never registered.
  `source/moiety.py` folds them. **Use only single-ingredient rows of the extract:** the
  `active_ingredients` and `moiety_names` lists are semicolon-joined and do **not** correspond
  positionally — zipping them pairs `.ALPHA.-LIPOIC ACID` with `.ALPHA.-KETOGLUTARIC ACID` and
  makes 1,350 salts claim several moieties. **Never fold a biosimilar suffix** (the extract maps
  `USTEKINUMAB-AEKN` → `USTEKINUMAB`, which would merge a biosimilar into its reference product).
- **The MeSH term sweep does not understand negation.** `Non-Small Cell Lung Cancer` coded to
  `D055752 Small Cell Lung Carcinoma` — the opposite disease — because `[a-z0-9]+` drops the `non-`
  and MeSH writes *carcinoma* where labels write *cancer*. `indications.NEGATORS` guards it.
- **This archive has no `browseLeaves`.** The CT.gov API returns one with a `relevance: HIGH|LOW`
  tag per term; whoever built `/data/mliu/insilicom-clinical_trials/final/output_raw` kept only
  `meshes` and `ancestors`. The field split does the same job, so this is a note for whoever
  maintains that download, not a blocker.
- **`meshes` is NLM's indexing, which is generous.** A fertility trial lists `Leuprolide` in
  `meshes` because the protocol mentions it, not because it was an arm. That is acceptable and even
  useful here: this is a retrieval filter, not the answer. A false candidate is rejected by the
  judge; a trial that never enters the shortlist is lost forever, and recall is what the project is
  short of.
- **`NA` is a literal placeholder id in the graph, not an identifier.** 90 FDA ingredients
  (acoltremon, acoramidis hydrochloride, anacaulase-bcdb…) all "resolve" to it. Keyed on that,
  every unidentified drug is the same drug — it inflated trial-intervention matches from 7,585 to
  44,874 before it was caught. Newer drugs are exactly the ones lacking an id, so this is not a
  rare corner. `service.normalize.PLACEHOLDER_IDS`.
- **The graph's `name` lookups are case-sensitive by default** — `keytruda` returns nothing while
  `Keytruda` returns the drug. Every query must pass the `{en, strength 2}` collation, which is
  also what makes it use the `name_1_type_1_ci` index.
- **`indications.tsv` stores bare MeSH ids** (`D002137`); the graph stores them prefixed
  (`MESH:D002137`). Asking with the bare form matches nothing and reports honest-looking zeros.
- **`Products.txt` carries no `ApplType`**, so its rows cannot be keyed on their own — only
  `Applications.txt` says whether 000001 is an NDA or an ANDA.
- `placebo` → `MESH:D010919`; exclude from intervention matching.
- **Label PDFs: use `https://` and follow redirects.** The `http://` URLs in `ApplicationDocs.txt`
  return nothing.
- **[PARTIALLY SUPERSEDED by D35, 2026-09-29 — condition now filters by MeSH tree match, keeping trials where either side has no id; the CHECKMATE-648 lesson is why id-less trials are kept]** **The registry's condition and the label's indication disagree, so condition must RANK and never
  FILTER.** CHECKMATE-648 is `NCT03143153`, enrollment 970 — exactly what the Opdivo label states —
  and it registers its condition as *Various Advanced Cancer*, which maps to no indication at all.
  Drug and date left a pool of 1,055 containing it; intersecting on Esophageal Neoplasms left 2 and
  discarded it. Drug and date are hard facts; condition is two vocabularies describing one disease
  differently. When a pool exceeds the cap it is ordered — condition matches first, then nearest to
  the patient count the label states — and truncated, never intersected.
- **Section 14 is cumulative, so a trial recurs on every later label.** 772 of 3,007
  (application, trial) groups span more than one submission, covering 2,728 units. Judge the
  earliest only — see D16 — or spend 39% of the agent budget re-deriving answers and then assert
  links that do not exist.
- **Not every label has a section 14, and that is not a bug.** 90.5% of labels for 2008+ approvals
  carry one; 59.0% for 2000-2007 and 42.3% for pre-2000. The rest head `CLINICAL PHARMACOLOGY` or
  `NONCLINICAL TOXICOLOGY` instead — pharmacology, not efficacy trials. Corpus-wide the rate is
  84.0%.
- **Section boundaries need three capital letters, not four.** Section 16 is `HOW SUPPLIED/STORAGE
  AND HANDLING` and section 8 is `USE IN SPECIFIC POPULATIONS`; requiring four let section 14 run
  on into 15, 16 and 17.
- **Some `ApplicationDocsURL` values contain a literal space** — one PDF covering two applications
  is named after both. An unencoded space raises `http.client.InvalidURL`, which is not an
  `OSError`, so it escaped the handler and killed a whole run.
- **A dose is not a trial name, and neither is a table column.** `DRUGX 10 mg/kg` and
  `LUTATHERA 7.4 GBq` read as trials "DRUGX 10" and "LUTATHERA 7"; a unit lookahead catches those.
  It does **not** catch `ABILIFY 15` scraped from a dose-arm table with another number after it —
  measured at **51% of all extracted trial names** before `section14.is_own_drug_name` rejected any
  acronym starting with the application's own brand or ingredient name, which halved it to 35%.
  The remainder are condition abbreviations (`ABSSSI 81`), endpoint scales (`EASI-75`) and table
  captions (`TABLE 2`); a judge answers NONE to all of them, correctly, so they cost calls but do
  not corrupt data.
- **Never regex section numbers over flat PDF text** — it matches table data and TOC entries.
  Anchor on `^14 CLINICAL STUDIES` to the next top-level section.
- Purple Book: two header rows; `351(k) Biosimilar` not `351(k)`; single-space nulls.
- Insulin and hGH moved NDA → BLA in March 2020 by statute. Not a data error.
- Label `version` values are dirty; order by `effective_time`.
- ANDA labels cite NCT ids because generics copy the RLD label — attribute those to the reference
  application, not the ANDA.
- **FDA approves several supplements of one application on the same day, and D16 compares dates.**
  140 (trial, application) pairs spanned more than one submission and 138 were exact date ties.
  The label PDF's digest breaks them, measured 70/70: Temodar's `NDA021029` SUPPL 36, 37 and 38
  share one `021029s036_037_038lbl.pdf`, so the trial is evidence for one event filed three ways
  and only SUPPL 36 keeps the link; `NDA208419` SUPPL 2, 3 and 4 have three different PDFs and are
  three real claims. **An absent digest must count as distinct, never as shared** — grouping on the
  empty string collapses every tie into one link and reports nothing.
- **[SUPERSEDED by D28, 2026-09-24 — to be replaced by a submission-grain role judgement on every label link; measured 2026-09-24: 1,055 of 3,361 links (31%) have no polarity verdict at all]** **Section-14 polarity was judged on the *current* openFDA labels, at application grain, but
  filters links built from *per-submission* labels.** §14 is cumulative and pruned, so a trial
  pivotal in 2015 can sit under a "lack of efficacy" heading today, and the filter would drop a
  correct 2015 link on a 2026 reading. Coverage is 2,005 of 2,293 pairs; the disagreement rate
  between the two readings is **not measured** and would need the era labels judged separately.
- **Submission numbers are strings in Drugs@FDA**, so `"10"` sorts before `"9"`. Anything choosing
  "the lowest supplement" must compare numerically — `assemble._submission_sort`.

## CONVENTION — agent-delegated work requires a runbook

**General rule, all projects.** Before any task is fanned out to subagents there must exist:

1. A **versioned prompt** in `docs/prompts/<task>.md`, copied into each subagent message
   **verbatim, never paraphrased**. Changing wording bumps the version; the version is stamped on
   every stored result.
2. A **runbook** in `docs/runbooks/<task>.md` naming: what a work unit is, how units are built, how
   many judges, how answers combine, how disagreement resolves, where results are written, and what
   the final report must state.
3. **Two independent judges per unit** minimum; agreement required for the positive verdict. One
   sampled answer is not a measurement.
4. **Disagreement escalates to a fresh third judge** whose answer is final. Do not average, do not
   re-run the original judges, do not decide it in the orchestrator.
5. **Minimal context per judge** — no hints, no expected answer, no batch context that leaks the
   target. If the judge has file or web access, forbid lookups explicitly.
6. **Never record a result that was not returned.** Missing or unparseable leaves the unit
   undecided for re-run. A guessed verdict is worse than a missing one.
7. **The orchestrator must not read the payload** — subagents read their own inputs from disk; the
   main agent passes ids only.
8. **Results append-only and tracked in git**, outside any directory the pipeline truncates.
9. **Resumable by construction** — decided units are skipped.
10. **Pilot before the full run.** A mis-worded prompt should cost 200 calls, not 5,000.

Reference implementation: `docs/runbooks/adjudicate-section-14.md`.

## Environment and gates

Conda env `clinical-trials-claude` (`environment.yml`), Python 3.11.
`mypy` must pass with zero errors. `pytest` configured in `pyproject.toml` only.
Coverage: overall ≥85%, core ≥90%, utility ≥80%.
Project root holds `main.py` only — no business logic, no utilities, no DB access.
Database access is pooled, constructed once, injected via constructor.
