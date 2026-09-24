# Pipeline audit — phase by phase

**Started:** 2026-09-16 · **Status:** P0 and P1 audited; P2–P6 pending.

Purpose: check each phase's code against what it claims, and each phase's output
against what the next phase needs. Findings are numbered so they can be argued
with individually. Nothing here has been fixed — this is the review list.

Severity is about the deliverable, not the code:

- **critical** — silently produces or permits a wrong edge, or caps the
  deliverable's recall well below what the data allows.
- **high** — measurable loss of coverage or precision, or an undocumented gap
  between what a gate claims and what it measured.
- **medium** — correct today but load-bearing by accident, or a stale claim.
- **low** — worth knowing, not worth stopping for.

---

## P0 — the approval-event spine

`service/approval_events.py` → `output/approval_events.tsv` (87,055 rows)

### What is right

Every gate in `docs/DESIGN.md` is met exactly, and reproduces from the file:

| Gate | Claimed | Measured in the TSV |
|---|---|---|
| events in scope | 87,055 | 87,055 |
| indication-changing | 11,426 | 11,426 |
| trial-relevant | 7,921 | 7,921 |
| — originals | 5,879 | 5,879 |
| — core supplements | 2,042 | 2,042 |

*These gates are D4's. D23 (2026-09-24) replaces the trial-relevant scope; its gate is in
`docs/DESIGN.md` §4 P0 and has not been measured against a rebuilt TSV yet.*

Also checked and clean: **zero duplicate `(application, submission_type,
submission_no)` keys**; `submission_no` is numeric on every row; no
trial-relevant event has a blank `approval_date`; the action-type join carries
multi-action submissions correctly (13 trial-relevant rows carry a second action
such as `Efficacy-New Indication;REMS`, and are matched on set intersection
rather than string equality, which is right).

### F-P0-1 — 5,322 approved submissions are dropped silently · **high**

`build()` does `application = canonical_of.get(digits)` and `continue`s when it
misses. 1,398 application numbers carrying **5,322 approved submissions** have no
row in `Applications.txt`, so they never enter the spine and are counted
nowhere — `EventsReport` reports `submissions_seen` and `approved`, but not
"dropped for unknown application".

Most look like old NDAs and ANDAs (610 below 50,000; 747 in the 50k–99k ANDA
range), but **41 carry modern numbers**, including BLA-range `103145`, `103692`,
`125011`, `125029`, `125036`, `125075`, `125083`, `125290`, `125359` and
NDA-range `200171`, `203733`, `204053` — several with an `ORIG` submission. Those
are in-scope applications that vanish without a trace.

*Impact:* unknown, because their type cannot be determined from Drugs@FDA alone —
which is exactly why the count needs to be reported instead of swallowed. P5's
`unlinked_submissions.tsv` has no `reason` value that covers them.

*Suggested:* count them in `EventsReport`, print them in `render()`, and type the
modern ones from the Orange/Purple Book or openFDA rather than dropping them.

### F-P0-2 — "NDA + BLA" is really "NDA + CDER BLA" · **high**

The spine holds **481 BLA applications**. The Purple Book file on disk lists 861
distinct BLA numbers, of which **401 are absent from the spine**, and 1,067 of
its 2,273 product rows are `Center = CBER`. Drugs@FDA covers CDER-transferred
biologics only; CBER biologics (vaccines, blood products, most cell and gene
therapies) are not in it.

D3 says "scope NDA + BLA, all years". That is true of the data source, not of
the biologics universe, and nothing in `DESIGN.md` or `CLAUDE.md` says so. Half
the project's title — the Purple Book — is roughly half-covered at the
application level.

*Suggested:* state the CDER-only boundary as a hard constraint, and decide
explicitly whether CBER BLAs are in scope. If they are, the Purple Book has to
become an input rather than a documented-but-unread file.

### F-P0-3 — the Purple Book file is the wrong file, and unread · **medium**

> **[CORRECTED 2026-09-24 — the "wrong file" half is false; "unread" still holds.]** The file is
> byte-identical to FDA's own full download,
> `accessdata.fda.gov/drugsatfda_docs/PurpleBook/2026/purplebook-search-August-data-download.csv`,
> the only bulk Purple Book file FDA publishes (one per month). Its title names only section 1:
> section 1 is the month's 29 changed rows (19 BLAs), and **section 2 is the full listing, 2,242
> rows over 860 BLAs** — CDER 1,204 rows, CBER 1,067. It contains Keytruda, Humira, Rituxan,
> Comirnaty, Kymriah and Recombivax, and 460 of Drugs@FDA's 482 BLAs. There is no fuller extract
> to download. The text below is kept as written.

`fda_raw/purplebook/purplebook-2026-08.csv` is the **"Purple Book Monthly
Historical Data Changes Report — August 2026"**: one month of new, added and
updated products, not the Purple Book database. 2,273 rows in **two sections
with two separate header rows** (file lines 4 and 35), so a single `DictReader`
over the whole file reads the second header as data — visible as the literal
values `Licensure` and `License Type` appearing as column values.

Its licence column is `License Type` — `351(a)` 2,020, `351(k) Interchangeable`
145, `351(k) Biosimilar` 106 — and there is no `BLA Type` column at all.
`CLAUDE.md`'s Purple Book trap ("`351(k) Biosimilar` not `351(k)`") describes a
column name this file does not have.

No module reads the Purple Book. The Orange Book is read only by
`service/identity_census.py`, for date-shape profiling.

*Suggested:* download the full Purple Book extract, or delete the file and the
trap note so nobody codes against a change report.

### F-P0-4 — 5,879 originals span 5,821 applications · **low**

56 applications carry two `ORIG` events (`ORIG-1` and `ORIG-2`, the second
usually class `Efficacy`) — e.g. `BLA125522` 2015-08-27 and 2021-02-26. Keying is
correct because `submission_no` is in the key. The risk is only in prose: "5,879
originals" is 5,879 *events*, not 5,879 distinct drugs, and P1's per-application
coverage denominator is correctly 5,821.

### F-P0-5 — 46 applications have supplements but no original · **low**

Their `ORIG` was approved before the data begins or is missing. Their
supplements can still be trial-relevant. Worth a named `reason` in P5 rather
than an empty result.

### F-P0-6 — 8 events have no approval date · **low**

`era` is blank for them. None is trial-relevant, so nothing downstream is
affected today.

### F-P0-7 — `submission_no` is a string, and the file is in lexical order · **needs checking in P4/P5**

Rows run `SUPPL 1, 100, 101, … 2`. D16 attributes a trial to the **earliest**
submission carrying it. If any consumer takes "earliest" by row order or string
comparison rather than by `approval_date` or `int(submission_no)`, it will
choose `SUPPL-100` over `SUPPL-2`. Not yet verified — flagged for the P4/P5 pass.

---

## P1 — normalisation to one id space

`service/normalize.py`, `dao/biokde.py`, `source/moiety.py`,
`source/mesh_descriptors.py` → `drug_map.tsv` (2,305), `indication_map.tsv`
(1,257), `trial_drug_map.tsv` (7,349), `trial_condition_map.tsv` (13,804)

### What is right

- **The two sides really are in one id space.** Of 1,391 drug names present on
  both the FDA and trial sides, **1,389 resolve to the same id** (the two
  exceptions are `dextromethorphan hydrobromide` and `ephedrine hydrochloride`).
  On the condition side, 1,170 of 1,176. The id space is not the problem.
- `biokdeid` is present on every row of all four tables, and no `biokdeid` maps
  to more than one external id.
- The trial-side index is deliberately restricted to ids the FDA side uses, so
  it never carries drugs the project cannot use — `trial_ids ⊆ fda_ids` holds
  exactly.
- The M→D rollup, the non-MeSH fallback and the `PLACEHOLDER_IDS` guard all
  behave as documented: 21 rolled-up rows, no `NA` keys, four vocabularies kept
  under their own prefixes.

### F-P1-1 — intervention matching is whole-string exact, and loses two thirds of the registry · **critical**

`_match_trial_strings` does `index.get(value.casefold())` — the *entire*
intervention string must equal a known synonym. Registry interventions are not
written that way.

Measured over 75,000 studies (shards 000, 007, 019 — about 12% of the archive):

| | studies with ≥1 drug resolved |
|---|---|
| exact whole-string (today) | 11,955 — **15.9%** |
| + containment on synonyms ≥6 chars | 25,946 — **34.6%** |

Containment more than doubles it (**+117%**). Only 15.0% of the 126,981
intervention strings match exactly. What is lost:

```
'Secukinumab 300 mg'                                    contains 'secukinumab'
'Ezetimibe + Simvastatin'                               contains 'simvastatin'
'bevacizumab, cisplatin, cetuximab, radiation therapy'  contains 'bevacizumab'
'Progesterone vaginal insert'                           contains 'progesterone'
'The ticagrelor'                                        contains 'ticagrelor'
```

*Impact:* this is the direct cause of **1,193 of the 3,076 P3 shortlist recall
misses** — trials whose NCT the label states outright, which the drug filter
drops because the registry wrote a dose after the name.

*Suggested:* token-aware containment, not naive substring (see F-P1-3 for why
naive containment is dangerous), then re-measure recall on the P3 gold set.

### F-P1-2 — every biosimilar resolves to nothing · **critical**

333 distinct FDA ingredient names fail to resolve. **151 of them carry a
biosimilar 4-letter suffix** — `ADALIMUMAB-AACF`, `AFLIBERCEPT-ABZV`,
`ADUCANUMAB-AVWA`, `AMIVANTAMAB AND HYALURONIDASE-LPUJ`. The graph has never
heard of a suffixed name, so the lookup returns nothing.

**161 of the 206 unresolved BLA applications (78%) fail for this reason alone**,
and all 161 are BLAs. That is what the headline number hides:

| | drug resolved |
|---|---|
| NDA | 5,133 / 5,340 = **96.1%** |
| BLA | 275 / 481 = **57.2%** |
| combined (the reported gate) | 5,408 / 5,821 = 92.9% |

D11 and `CLAUDE.md` are right that the suffix must not be folded — merging
`USTEKINUMAB-AEKN` into `USTEKINUMAB` would collapse a biosimilar into its
reference product, and a 351(k) approval rests on its own trials. But the code
does not do the other half: nothing puts the *reference moiety's* id on the row
for retrieval. The result is neither distinct nor joinable — a 351(k)
application can never receive a candidate shortlist, so biosimilars are
structurally unlinkable.

*Suggested:* keep the suffixed name as the row's identity and carry the stem's
id in a separate column (`retrieval_id` or similar) that P3 filters on. 98
distinct stems are involved.

### F-P1-3 — junk synonyms put ~2,300 unrelated studies under one drug · **high**

The graph has drug entities whose synonym list contains ordinary English words.
`MIN_SYNONYM_LENGTH = 4` does not catch them (they are long enough) and the
"drop synonyms claimed by more than one id" rule does not either (only one
entity claims each):

| intervention string | resolves to |
|---|---|
| `Control`, `CONTROL`, `CONtrol` | `MESH:D002707` **Chlordiazepoxide** |
| `Standard`, `STANDARD` | `MESH:D013455` **Sulfur** |
| `Test`, `TEST`, `Salt`, `SALT` | `MESH:D013739` **Testosterone** |
| `Normal` | `MESH:D009584` **Nitrogen** |
| `Urine` | `PubChem:CID146171046` (a cephalosporin) |
| `Carrier` | `PubChem:CID10917` (a carnitine) |
| `Alcohol` | `MESH:D000431` Ethanol *(correct)* |

Condition side: `Control`, `CONTROL` → `MESH:D006331` **Heart Diseases**.

In the 75,000-study sample, 281 studies join to a drug through one of these
(269 of them `control` → chlordiazepoxide) and 5 to a condition. Extrapolated:
**~2,300 false drug postings archive-wide**, almost all landing on one drug.

*Impact:* a chlordiazepoxide submission gets a shortlist of thousands of
unrelated trials; this also makes naive substring matching (F-P1-1) actively
unsafe — `'Durolane(r) as control device'` already matches `control`.

*Suggested:* a stop-list of study-design words applied to the synonym index, and
a rule that a synonym which is a common English word needs a `Drug`-typed entity
and a second corroborating synonym before it is indexed.

### F-P1-4 — the documented `placebo` exclusion is not implemented · **medium**

`CLAUDE.md` says "`placebo` → `MESH:D010919`; exclude from intervention
matching." No such exclusion exists in `normalize.py` or `candidates.py`.

It is harmless *today* only by accident: the synonym index is built from ids the
FDA side resolved, no FDA product has `PLACEBO` as an active ingredient, so
`MESH:D010919` never enters the index — `trial_drug_map.tsv` contains zero
placebo rows. The moment an application resolves to it, every placebo-controlled
trial in the registry joins to that drug.

*Suggested:* implement the documented exclusion rather than relying on the
accident.

### F-P1-5 — half the FDA drugs are keyed on PubChem, which joins less well · **medium**

1,145 of 2,305 `drug_map` rows carry a non-MeSH id (938 exact, 207 via the
moiety path). Joining rate to the trial side:

| vocabulary | FDA ids | also used by a trial |
|---|---|---|
| `MESH:` | 970 | 873 — **90%** |
| `PubChem:` | 1,112 | 807 — **73%** |
| `DrugBank` | 15 | 8 |
| `TTD` | 24 | **1** |

432 of 2,121 FDA drug ids are used by no trial at all. The mapping is
internally consistent — only one salt/moiety pair (`CALCIUM ACETATE` vs
`CALCIUM`) disagrees — so this is a coverage property of the graph, not a bug.
Worth recording as the ceiling it is: a PubChem-keyed drug is materially less
likely to retrieve its trials, and a TTD-keyed one essentially never does.

Cosmetic but confusing: moiety-path rows show an IUPAC string as
`preferred_name` (`ABACAVIR SULFATE` → a 40-word alcohol name), which makes the
table hard to read and hard to spot-check.

### F-P1-6 — `indications.tsv` is 81% out of scope and codes to generic descriptors · **medium**

56,665 rows: **46,043 ANDA**, 8,005 NDA, 1,524 BLA, and **1,093 with a blank
`app_type`** (the F-P0-1 orphans). The spine is NDA+BLA, so four fifths of the
table cannot be used by it.

The most common codes are the ones that discriminate least:

```
Disease 1,819 · Infections 1,408 · Hypertension 782 · Neoplasms 738
Heart Failure 700 · Pain 647
```

`Disease`, `Infections` and `Neoplasms` are top-level descriptors — as an
indication key they select almost the whole registry. One ANDA carries 80
distinct indications.

*Impact:* P3 now *ranks* on condition rather than filtering (commit c87d7d2),
which makes this much less dangerous than it was, but a generic descriptor still
ranks nothing usefully.

*Suggested:* record a specificity signal (MeSH tree depth) per indication so P3
can decline to rank on a top-level descriptor.

### F-P1-7 — the DESIGN gate numbers are stale · **low**

`DESIGN.md` P1 records `drug_map` 2,270, `indication_map` 1,259, `trial_drug_map`
7,585, `trial_condition_map` 13,824. The files now hold 2,305 / 1,257 / 7,349 /
13,804. The changes are almost certainly the moiety and placeholder work, but no
note says so, so the gate can no longer be checked against the document.

---

## P2–P6

Not yet audited. Next: P2 (`service/section14.py` → `label_trials.tsv`), which
currently has uncommitted changes in the working tree.
