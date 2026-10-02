# Rebuild status and to-do

**Last updated:** 2026-10-02. Stage names S1–S11 since D38; the sections below that say P0–P6 predate it. Update this file whenever a stage finishes or is paused; the short
to-do list in `CLAUDE.md` ("Current status") points here.

## Progress by stage (D38) — 2026-10-01

**Next (user, 2026-10-02): re-run the whole pipeline from S5 on D42–D45** (S2/S3 rebuilt on D45 2026-10-02). S5→S11 outputs on disk predate the S5 descriptor fix and the S8 disease-only rule. Last S11 shortlist recall: 84.5% (2,479/2,933), against 81.6% on iKraph1.

`make status` is the authority on what is stale; this table says what has been *done*.

| Stage | State | Notes |
|---|---|---|
| S1 spine | done | 11,295 trial-relevant events |
| S2 drugs | **D45 rebuilt 2026-10-02** | `substance_ids.tsv` (133,467 UNIIs); rule R, one entity per ingredient (`drug_identity.tsv` route column): TP 2,508, Chemical 45, none 117; 2,561 `drug_map.tsv` rows, applications with a drug id 98.2%; `id_bridge.tsv` 16,129 |
| S3 registry | **D45 rebuilt 2026-10-02** | `trial_drug_map.tsv` 108,343 strings (103,115 with an entity, 0 with two), drug MeSH vs NLM 95.5%; `trial_condition_map.tsv` 51,211 (42,382 with an entity, 0 with two); `nlm_id_map.tsv` 20,599 NLM ids (7,498 drug ids with an entity); FDA vs trial same-name disagreement 0/1,624 |
| S4 documents | **done, re-run 2026-10-02** | re-fetch: all remaining labels (28), letters (66) and ~130 review files are 404 at FDA; 98 old-style review index pages not followed (future). Labels re-parse byte-identical. Fixes: review protocol ids no longer swallow table-of-contents dot leaders (+2,179 accepted mentions, +8 role units for S6); letters flag 1,332 `indication_candidate` sentences → 979 letter-agent verify units (batches 019–058) |
| S5 indications | **code changed, not rebuilt** | registry aliases must share a word with their descriptor |
| S6 review roles | **done** | 4,825/4,825 (2026-09-30) |
| S7 literature | **code changed, not rebuilt** | PMIDs, found-under diseases and abstracts files; case D start ≤ approval only |
| S8 route + candidates | **D39 code changed, not rebuilt** | phase rule (c); sponsor rank key after start date; bridged drug ids; normalised codes on candidates and units; literature set (drug + indication checked) separate from the registry shortlist; never-ran excluded; drug name and §14 subsection on every unit |
| S9 judge | **code changed**: three prompts (registry v3 ×2, literature v2 — D39 codes), literature tiers first, route on every decision | D35 pilots superseded by v2 — every answer re-judged; digest-keyed tiers; agent pool verified (cap 32) |
| S10 assemble | **code changed**, not run on the current scope | route tiers; D16 across routes and same-moiety applications |
| S11 validate | **code changed**, not run | first appearances by moiety and trial name; recall counts both routes |

D37 was committed (9aa3db5) and rebuilt (recall 80.0%, 2,206/2,756 first appearances). D39 is in code since.

Biosimilar copied labels stay in the S11 ground truth (user, 2026-10-01).

**D39 rebuilt 2026-10-01** (`make candidates`, d62f9c5; only S10 stale): `id_bridge.tsv` 14,462
bridges; 159,691 studies indexed after the phase rule; median 44 candidates per unit; units
needing judgement 18,445, C units with candidates 2,236, D 153. Shortlist recall (S11, units
with any candidate) 2,236 / 2,739 = **81.6%** (was 80.0%, 2,206 / 2,756); over all 3,093
first-appearance known answers 72.3%. Of 692 missed trials, the phase rule accounts for 32
(11 blank, 4 NA, 17 phase 1 under 100), 8 are not in the archive, 653 are drug, date,
indication or cap.

**File-independent prompts 2026-10-01:** `service/prompt_render.py` and
`--mode render-prompts --task match|review-role|label-role --batch <ids>` render every unit as a
complete prompt (`<batch>.requests.jsonl`); APPROVAL line no longer repeats the §14 heading;
no-label prompt v4 (dead case-D rule removed). The pool client that sends them is not built.

**D41 rebuilt 2026-10-01** (S8 + S10): 30,574 candidate slots removed over all units (most on
later appearances no judge sees); on the units S9 judges — B 3,660 units (1,644 lose 5,491
trials), C 2,234 (392 lose 1,746, all named on an earlier same-moiety submission), D 153 (5 lose
7). 73 units left with no candidate; S10 `candidates_already_linked` 108 rows. Shortlist recall
unchanged at 81.6%. Map: 4,110 links on 2,039 submissions (no judging yet under v3/v4).

**Agent-pool client 2026-10-01:** `--mode pool-judge` (`config/agent_pool.py`, `dao/agent_pool.py`,
`service/pool_judging.py`) sends rendered prompts, at most min(32, free job slots) at a time,
records only valid replies. Live: 1 of 2 test jobs answered correctly, the other hit "no seats
available". **Capacity is the constraint:** on 2026-10-01 afternoon 1 of 19 seats was eligible
(2 job slots); most were at weekly or 5-hour quota walls.

Next: commit D39 → `make candidates` → pilots per route (two
judges + third) → full runs (two judges + third, D40) → S10 → D28 label roles → S10 → S11.

## Future improvements — S2 drug mapping (recorded 2026-10-02, skipped for now)

Under D45 rule R, 71 FDA ingredients have no `drug_map.tsv` row: no iKraph2 entity by any id or
exact name, and no MeSH id of their own. 60 applications lose every drug key (applications with a
drug id 99.2% → 98.2%), so their registry routes (B−, C−) build no shortlist (`no_drug_resolved`);
30 combination products lose one ingredient. The user's decision: leave them out and move on.
Fixable parts, measured 2026-10-02:

1. **`ingredients_of` splits inside parentheses.** Drugs@FDA writes
   `PANCRELIPASE (AMYLASE; LIPASE; PROTEASE)`; splitting on `;` yields fragments
   (`PANCRELIPASE (AMYLASE`, `PROTEASE)`, `SULFAMETHAZINE)`, `SULFATHIAZOLE)`) that can match
   nothing. Split only on top-level `;`. 4 fragments.
2. **Combination products are iKraph2 aliases of RxCUI-keyed TherapyPreventions entities**
   (`DARUNAVIR AND COBICISTAT`, `OMBITASVIR, PARITAPREVIR, RITONAVIR`,
   `CONJUGATED ESTROGENS/MEDROXYPROGESTERONE ACETATE`). Rule R accepts a TP alias only on a
   UNII-keyed entity (to keep ChEBI classes out); accepting RxCUI-keyed entities too would match
   them. 3 ingredients.
3. **The other 61 have nothing to match**: their UNII (55 also with an RxCUI or PubChem CID) is on
   no iKraph2 entity and their exact name is not in iKraph2 — recent salts (BEROTRALSTAT
   DIHYDROCHLORIDE, ELTROMBOPAG CHOLINE), radiolabelled agents, kits. Only a moiety fold would
   reach them, and D45 removed folding on purpose; a UNII-only row would join just 71 trials
   (20 registry strings name these salts). Left out.

## Where the rebuild stands (audited 2026-09-29 against the files on disk)

"Done" below means the step ran to completion on the D23 scope and its output is on disk; each
row says what was checked.

| Phase | Step | State | Evidence |
|---|---|---|---|
| P0 | spine | **done** | `approval_events.tsv` 2026-09-24; 11,295 trial-relevant |
| P1 | normalize | **done** | `drug_map`, `trial_drug_map`, `trial_condition_map` 2026-09-24 18:13 |
| P2a | labels | **done, 28 PDFs never fetched** | 6,423 of 6,451 PDFs cached, 1 unreadable, 114 scanned; re-read offline 2026-09-29 (D34 `heading_full`) |
| P2b | letters | **done, 66 letters never fetched** | 6,596 of 6,662 in-scope letter URLs cached (99.0%); `letter_indications.tsv` 2026-09-25; letter agent 653 units located and verified (commit 3b61ff1) |
| P2c | reviews — download + protocol-id parse | **download ~99% complete, not 100%**: 60 review documents (20 PDFs, 40 table-of-contents pages) and 71 TOC component PDFs never fetched; runbook step 1.1 (retry the `&amp;` components and earlier 404s) **was never run**. Parse done on what is cached | 3,045 + 3,353 documents and 10,418 components cached; `review_trial_mentions.tsv` and 4,825 role units written 2026-09-25 11:31, after the last download |
| P2c | reviews — **role judging (P4r)** | **done 2026-09-30**: 4,825 of 4,825 decided under prompt v2 (PIVOTAL 1,469 · SUPPORTING 723 · PK_CLINPHARM 1,275 · OTHER 946 · NOT_FOR_THIS_APPROVAL 320 · FAILED 92) | commits ed91306, 283e378, 730e040, e6a2e11 |
| P2i | indication table | **done, rebuilt 2026-09-29 (D34)** | `submission_indications.tsv` |
| P2g | literature candidates | **done 2026-09-30**: `graph-trials` 2,147 applications (116 pairs via protocol id); `graph-links` 179 case-D submissions | `graph_trials.tsv`, `drug_only_trials.tsv` |
| P3 | candidates | **rebuilt 2026-09-30 under D35 + D36** — routes: label 3,572 · C 3,034 · D 3,168 · review 279 · guard 1,043 · biosimilar 199; units with candidates B 3,737 · C 2,358 · D 125; recall 78.1% (earliest) | `candidates.jsonl`, `submission_routes.tsv` 2026-09-30 |
| P4 | identify, pick-one v1 | done on the old shortlists — 3,668 units (commit da0f281); **replaced by D33/D35**, kept for comparison | |
| P4 | D33 pilot | **obsolete, archived** — built on the pre-D34 order; 27/50 decided | `adjudication/archive/d33-pilot-pre-d35/` |
| P4 | case B pilot (D35) | **done 2026-09-30** — 50/50 decided, 2 judges + third: 16 identified (T1 13 · T2 1 · T4 1 · T5 1), 34 NONE; A=B 1,750/1,751 candidates | see "D35 pilots" below |
| P4 | cases C and D pilot (D35) | **done 2026-09-30** — 50/50 decided (48 C, 2 D): 23 identified (T1 17 · T2 5 · T10 1), all case C; 27 NONE; A=B 820/827 | prompt `trial-candidate-match-nolabel` v1 |
| P4 | full runs B, C/D | **not started** — waits on the user's review of the pilots | |
| P4b | D28 label roles | not piloted | |
| P5 | assemble | **not run on the D23 scope** — `trial_submission_map.tsv` is from 2026-09-23 | |
| P6 | validate | not run | |

**Correction:** an earlier version of this file (2026-09-28) said "P0–P3 on the D23 scope: done"
and listed no review step. The review download was never finished (131 files missing, retry step
never run) and review roles are 20% judged; both are now listed.

## Stale-input rebuild (2026-09-30)

`submission_indications.tsv` (P2i) was written 09-29 16:02, before its code changed at 23:50, and
P2g and P3 were then re-run on it: every `openfda_current` row had ids but no text, so case-C
judges were told "not recorded", and the ids were the old coder's. `trial_condition_map.tsv` (P1)
was stale the same way (its coder in `indications.py` changed 09-29). **Fix:** a `Makefile` now
declares every step's inputs and code (code dependencies generated from imports,
`utility/code_deps.py`); `make status` lists stale outputs, and `main.py` refuses to run a guarded
mode on stale inputs (`utility/freshness.py`, `--allow-stale` overrides). Rebuilt in order:
`indications` → `normalize` → `indication-normalize` → `graph-trials` → `graph-links` →
`candidates`. Routing now C 3,130 · D 3,072 (was 3,034 · 3,168); units with candidates C 2,369 ·
D 125; shortlist recall 78.2%. Pilot units whose judged candidate tiers changed: B 5 of 50, C 9 of
48; and every C unit whose indication text changed is stale regardless (see below).

## D35 pilots (2026-09-30) — two judges + third, `--limit 50` each

Answers `adjudication/candidate_match_answers.jsonl`; decisions `identify_verdicts.jsonl`
`prompt_version 3`; raw `candidate_match_raw/round-01..23`, tiebreaks `raw/tiebreak/`.

| | Case B (excerpt prompt v1) | Cases C/D (no-label prompt v1) |
|---|---|---|
| units | 50 descriptions | 50 submissions (48 C, 2 D) |
| candidates judged | 1,751 | 827 |
| judge A = B | 1,750 (99.94%), 1 tiebreak | 820 (99.2%), 7 tiebreaks |
| identified | 16 (T1 13 · T2 1 · T4 1 · T5 1) | 23 (T1 17 · T2 5 · T10 1), all C; D 0/2 |
| NONE | 34 | 27 |
| tier calls per judge | 192 (3.8 / unit; 24 stop at T1, 6 run all 10 tiers, 3 reach literature tiers 11–12) | 99 (2.0 / unit; 33 stop at T1) |
| units with > 1 Yes in the deciding tier | 1 | 11 (up to 5) |

Case B against the old pick-one P4 on the same 49 descriptions that have an old answer: same
trial 12, same NONE 31, **new identification where the old said NONE 4** (NDA206111 S38 →
NCT03057951 · NDA211371 n=38 → NCT02942004 · NDA211616 S24 → NCT02666664 · NDA218785 IWWM-6 →
NCT03053440), **old identification now NONE 2**: NDA022509 PGTC Seizures (old NCT00043901 sits at
rank 6 of 16; both new judges said No) and NDA213082 "ACR20 33" (an endpoint read as a trial name;
the old pick is no longer on the filtered shortlist). **Hand-check these six.**

## D33 pilot (50 descriptions, `--limit 50`, two judges) — OBSOLETE, archived 2026-09-29

Files moved to `adjudication/archive/d33-pilot-pre-d35/` (see its README). Its 27 decisions stay in
`identify_verdicts.jsonl` as version 2; D35 decisions are version 3 and P5 reads only version 3+,
so neither those nor the 3,668 pick-one v1 answers are linked until re-decided. Kept below for the
record.

Runbook: `docs/runbooks/trial-candidate-match.md`.

- Rounds 1–3 (ranks 1–30) judged and combined. Per-candidate A/B agreement: 445/445, 257/257,
  250/250 — **100%, no third judge needed yet**.
- Decided 27 of 50: **15 identified** (tier 1: 14, tier 3: 1), **12 NONE**. **23 still judging.**
- **Round 4 (ranks 31–40) is prepared, not launched:**
  `adjudication/candidate_match_batches/round-04/batch-001..003.ids` (10 + 10 + 3 tiers). Judges
  write `adjudication/candidate_match_raw/round-04/batch-NNN-{A,B}.txt`.
- Compared with pick-one P4 on the same descriptions:
  - Every decided identification matches the old answer.
  - 4 new identifications where the old P4 said NONE — **hand-check these**:
    NDA021641 n=159 → NCT00203034 · NDA211371 n=38 → NCT02942004 ·
    NDA211616 Primary Hypercholesterolemia → NCT02666664 · NDA218785 IWWM-6 → NCT03053440.
  - Pending descriptions that had an old identification, at old rank: BLA761069 Bladder Cancer
    (59), NDA020634 CAP (26), NDA208843 n=120 (37), **NDA022509 PGTC Seizures (6)** — the last
    means both new judges said No to the old pick in tier 1; a real disagreement to inspect.

Loop to finish the pilot (repeat until prepare writes 0 batch files):

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
# launch judges A and B on every batch of the round (message template in the runbook)
$PY main.py --mode match-combine --judges 2   # tiebreaks -> candidate_match_tiebreak_needed.txt
$PY main.py --mode match-record --limit 50
$PY main.py --mode match-prepare --limit 50
```

## Consistency audit, 2026-09-29 (code, workflow, docs)

Fixed in code: the today's-label indication fallback is coded by the one coder (was: old
`indications.tsv` ids, 343 rows coded `Disease`); P6 shortlist recall scores earliest appearances
only; P3 reports the D35 routing and warns when `graph_trials.tsv` / `drug_only_trials.tsv` are
missing. Fixed in docs: `DESIGN.md` §3 lacked D16–D18 (present in `CLAUDE.md`); D18/D20 clarified;
`DESIGN.md` §4 phase text annotated with current status.

Resolved with the user, 2026-09-29:
1. **P6 judge precision** — new runbook `docs/runbooks/validate-precision.md`: withheld-answer
   units (earliest appearances only) judged per candidate by the production configuration, all
   under `adjudication/precision_d35/`, never in production verdicts (`validate --limit`,
   `match-* --units`).
2. **Superseded modes print a DEPRECATED warning every time they run** and are tagged in `--help`:
   `map`, `attribute`, `adjudicate-*`, `identify-*`, `graph-prepare/-combine`,
   `precision-prepare/-combine`; `graph-links` logs a warning if its old indication path runs.
3. Old polarity verdicts stay as D28's fallback until D28 is complete — accepted.
4. The Week-1 files `approvals.tsv`, `links.tsv`, `drugs.tsv`, `trial_approval_map.tsv`,
   `trial_approval_dated.tsv` were **removed from `output/`** (gitignored; `--mode map`
   regenerates them).

## Run order (D35, agreed 2026-09-29): all code first, then data, then agents

**Phase 1 — code only, nothing run** — **DONE 2026-09-29** (uncommitted): 1,097 tests pass, coverage 97.7%, mypy clean. Items 1–5 below are implemented; the old pick-one graph route (`graph-prepare/-combine`, `select-graph-trial.md`) is marked superseded
1. P2g: protocol ids in abstracts (full ids only, exactly one registry match) beside NCT ids;
   `graph-trials` walks drug × every indication, and drug-only treats edges where there is none.
2. P3: route each submission / description into case A–D; tree indication filter (keep id-less);
   top 100 = 10 tiers; literature candidates unfiltered beyond the 100; case C and D units.
3. P4: no-label prompt `trial-candidate-match-nolabel` v1 + runbook; `match-*` modes take C/D units.
4. P5: tiers A–D (`docs/TIERS.md`), highest tier wins, `sources`; old tier-C code path → D.
5. Tests, mypy, coverage; docs status lines.

**Phase 2 — data rebuild, no agents**
6. `--mode reviews` — **FDA, alone, 1 req/s**: fetch the 131 missing review files, re-parse →
   `review_trial_mentions.tsv`, role units (existing P4r verdicts keep their unit ids).
7. `--mode graph-trials` (Mongo) → `--mode candidates` → `--mode validate` (shortlist recall).

**Phase 3 — pilots, two judges + third; the user reviews each**
8. Case B pilot (50 descriptions) and case C/D pilot (50 units), in parallel.
9. P4r: combine the uncombined raw files, then resume (two judges + third).

**Phase 4 — full runs**
10. Case B and cases C/D, two judges + third each (D40); P4r to completion.
11. P5 first pass → D28 pilot (user hand-check) → D28 full run.
12. P5 final → P6: `validate` (shortlist recall, free) and judge precision per
    `docs/runbooks/validate-precision.md` (200 withheld units, two judges + third) → status lines in
    CLAUDE.md / DESIGN.md → commits.

## Deck

The Friday deck (artifact `EmntLvzU8JfwLKgnNhsZEm`) still needs its estimates replaced with
measured figures; a handoff prompt for that was written in the 2026-09-28 session.
