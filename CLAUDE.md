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

- **D2 — the edge is trial → submission**, keyed `(ApplNo, SubmissionType, SubmissionNo)`.
  Keytruda melanoma was approved 2014-09-04 (accelerated, KEYNOTE-001) and again 2015-12-18
  (converted to regular, KEYNOTE-002/006). Same drug, same indication, two events, two trial sets.
  Application-grain linking destroys this.
- **D3 — scope NDA + BLA, all years** (87,055 approved events). Era is a column, not a filter.
- **D4 — trial-relevant set = 5,879 ORIG + 2,042 core SUPPL = 7,921.**
- **D5 — which submissions are trial-driven is published data.** `ActionTypes_Lookup` +
  `Join_Submission_ActionTypes_Lookup`: New Indication (1,894), Accelerated Approval (120),
  Accelerated Approval Confirmatory Study (28). **Accelerated status and confirmatory conversion
  are structured fields — no PDF parsing.**
- **D6 — point-in-time labels come from `ApplicationDocs` type 2**, 29,788 per-submission label
  PDFs (`…/2009/017881s010lbl.pdf`).
- **D7 — trials are identified from §14 prose, not NCT ids.** Only 1/6 originals carry an NCT;
  0.9% corpus-wide. This is the core problem the project exists to solve.
- **D8 — enrollment count is the disambiguating key.** Across 5 drug+condition candidate sets,
  every candidate had a distinct enrollment — zero collisions.
- **D9 — MeSH descriptor is the shared id space**; biokde `pubmedEntitiesFlat` is the dictionary.
- **D10 — every row carries an evidence tier** (A/B/C).
- **D11 — normalise to active moiety**, but keep biosimilar 4-letter suffixes distinct.
- **D13 — no OCR.** Scanned labels are all pre-2006, which is pre-PLR with no `14.N` structure.
- **D14 — the Insilicom KG is a cross-check and recall backstop**, not a required path.
- **D16 — a trial is attributed to the EARLIEST submission whose label carries it.** Section 14 is
  cumulative: a subsection is added at the approval it supported and then never removed.
  KEYNOTE-087 appears on **37** separate Keytruda submissions, 2017-05-10 through 2026-07-10; it
  supported the first (classical Hodgkin lymphoma). Attributing it to all 37 would make the
  deliverable's central claim 36 times wrongly. Later appearances are context, not evidence.

## Hard constraints

- Trial registration did not exist before ~2005 (CT.gov 2000, ICMJE 2005, FDAAA 2007). Pre-2005
  approvals have no NCT to link to. Low match rates there are reality, not failure.
- openFDA labels are a **current-state snapshot** — 262,757 records, 262,757 distinct `set_id`s.
  §14 is cumulative *and pruned*: withdrawn indications are deleted.
- Approval letters begin in 1998 (37,283 documents, only 2 before that).
- Combination regimens are legitimately many-to-many: KEYNOTE-189 approved pembrolizumab *with*
  chemo owned by other applications.

## Pipeline — six phases

```
P0 spine        Submissions + Applications + ActionTypes -> 87,055 approved NDA|BLA events
                (11,426 indication-changing; the coarse old filter)
                trial-relevant = 5,879 ORIG + 2,042 core SUPPL = 7,921
P1 normalize    biokde synonym dictionary -> MeSH descriptor, four mappings
P2 labels       ApplicationDocs type 2 -> label PDF; anchor ^14 CLINICAL STUDIES;
                extract acronym, NCT if present, ENROLLMENT COUNT, phase, design
P3 candidates   CT.gov (600,762) filtered by drug-MeSH + condition-MeSH + phase
                + start < approval_date  -> 10-40 candidates
P4 agents       4a identify (new) · 4b polarity (ALREADY BUILT)
P5 assemble     tier A/B/C -> trial_submission_map.tsv + unlinked_submissions.tsv
P6 validate     against the 2,209 label-cited NCT links
```

**Why it works:** §14 rarely names an NCT but always describes the trial — design, population, and
the patient count. Structured filters cut 600,762 studies to 10–40 candidates; enrollment count
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
- **A dose is not a trial name.** `DRUGX 10 mg/kg` and `LUTATHERA 7.4 GBq` read as trials
  "DRUGX 10" and "LUTATHERA 7" — the largest source of false acronyms on a real label.
- **Never regex section numbers over flat PDF text** — it matches table data and TOC entries.
  Anchor on `^14 CLINICAL STUDIES` to the next top-level section.
- Purple Book: two header rows; `351(k) Biosimilar` not `351(k)`; single-space nulls.
- Insulin and hGH moved NDA → BLA in March 2020 by statute. Not a data error.
- Label `version` values are dirty; order by `effective_time`.
- ANDA labels cite NCT ids because generics copy the RLD label — attribute those to the reference
  application, not the ANDA.

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
