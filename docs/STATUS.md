# Rebuild status and to-do

**Last updated:** 2026-09-29. Update this file whenever a stage finishes or is paused; the short
to-do list in `CLAUDE.md` ("Current status") points here.

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
| P2g | literature candidates (`graph-trials`) | **stale** — built 2026-09-25 on the pre-D34 indications; D35 adds protocol ids | `graph_trials.tsv` 2026-09-25 |
| P3 | candidates | **rebuilt 2026-09-29 but superseded by D35** (no filter, no cap rule, stale graph input) | `candidates.jsonl` 2026-09-29 |
| P4 | identify, pick-one v1 | done on the old shortlists — 3,668 units (commit da0f281); **replaced by D33/D35**, kept for comparison | |
| P4 | D33 pilot | **obsolete, archived** — built on the pre-D34 order; 27/50 decided | `adjudication/archive/d33-pilot-pre-d35/` |
| P4 | cases C and D (D35) | **code done 2026-09-29**, not run | prompt `trial-candidate-match-nolabel` v1 |
| P4b | D28 label roles | not piloted | |
| P5 | assemble | **not run on the D23 scope** — `trial_submission_map.tsv` is from 2026-09-23 | |
| P6 | validate | not run | |

**Correction:** an earlier version of this file (2026-09-28) said "P0–P3 on the D23 scope: done"
and listed no review step. The review download was never finished (131 files missing, retry step
never run) and review roles are 20% judged; both are now listed.

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
10. Case B and cases C/D, one judge each; P4r to completion.
11. P5 first pass → D28 pilot (user hand-check) → D28 full run.
12. P5 final → P6: `validate` (shortlist recall, free) and judge precision per
    `docs/runbooks/validate-precision.md` (200 withheld units, one judge) → status lines in
    CLAUDE.md / DESIGN.md → commits.

## Deck

The Friday deck (artifact `EmntLvzU8JfwLKgnNhsZEm`) still needs its estimates replaced with
measured figures; a handoff prompt for that was written in the 2026-09-28 session.
