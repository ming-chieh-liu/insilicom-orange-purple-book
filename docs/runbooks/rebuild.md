# Runbook — full rebuild, P0 → P6, after the review download

**Written:** 2026-09-25 · **For:** the main agent, with the user present for the pilots.

Rebuilds `output/` under D23–D31. Every step is resumable; a step that fails is
re-run, not worked around. **Only one process may talk to FDA at a time** — the
steps marked **FDA** are sequential and polite (1 request/s, breaker after 5
refusals, `source/polite_fetch.py`); never start one while another is running.

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
cd /data/mliu/insilicom-orange-purple-book
```

## Stage 0 — can run now (no FDA requests)

| # | Command | Needs | Writes | Agents |
|---|---|---|---|---|
| 0.1 | `$PY main.py --mode letters --offline` | letter cache (downloaded) | `output/letter_indications.tsv` | — |
| 0.2 | `$PY main.py --mode letter-locate-prepare` | 0.1 | `adjudication/letter_locate_units.jsonl`, batches | — |
| 0.3 | judges per `docs/runbooks/letter-indication.md` (locate A/B, then verify A/B) | 0.2 | `adjudication/letter_indication_raw/` | **yes** |
| 0.4 | `$PY main.py --mode letter-indication-combine` (repeat until nothing pending) | 0.3 | `adjudication/letter_indication_verdicts.jsonl` | tiebreaks |

## Stage 1 — after the review download finishes

The download (`--mode reviews`, started 2026-09-25) ends by parsing and writing
its units itself; wait for that process to exit.

| # | Command | FDA | Writes | Notes |
|---|---|---|---|---|
| 1.1 | `$PY main.py --mode reviews` | **FDA** | `output/review_trial_mentions.tsv` (+`acronym`), `adjudication/review_role_units.jsonl` | fetches only what is missing: the 4 `&amp;` components and a retry of earlier 404s; then re-parses from the cache |
| 1.2 | `$PY main.py --mode labels` | **FDA** (18 PDFs) | `output/label_trials.tsv`, `submission_indication_text.tsv` | ~25 min of parsing. **Decide first** on the other session's uncommitted `service/section14.py` regex (see below) |
| 1.3 | `$PY main.py --mode indication-normalize` | — | `output/submission_indications.tsv` (D30) | needs 0.4, 1.2; Mongo |
| 1.4 | `$PY main.py --mode graph-trials` | — | `output/graph_trials.tsv` | Mongo; P3 ranking input and the D19 cross-check |
| 1.5 | `$PY main.py --mode candidates` | — | `output/candidates.jsonl` | P3 incl. D29 review-name skip and the stated-id pairing fix |
| 1.6 | `$PY main.py --mode validate` | — | report | shortlist recall on the rebuilt units (answer key caveat below) |

## Stage 2 — judging (agents; pilots with the user)

| # | Step | Runbook | Pilot first |
|---|---|---|---|
| 2.1 | P4 identify: `identify-prepare` → judges → `identify-combine` → `identify-record` | `identify-trial.md` | coordinate with the other session's run: its ~5,800 verdicts are keyed on pre-rebuild unit ids; units that kept their id are skipped, re-keyed ones are judged again |
| 2.2 | Review roles: `review-role-prepare` → judges → `review-role-combine` | `review-trial-role.md` | yes — calibration units first, v2 never run |
| 2.3 | `$PY main.py --mode assemble` | — | first map, with review links (D27) and old polarity fallback |
| 2.4 | Label roles (D28): `label-role-units` → `label-role-prepare --limit 200` → judges → `label-role-combine` | `label-trial-role.md` | **yes — the user hand-checks non-linking answers before the full run** |
| 2.5 | `$PY main.py --mode assemble` | — | map with D28 roles; `label links without a D28 role yet` must reach 0 |
| 2.6 | Tier C: `graph-links` → `graph-prepare --limit 60` → judges → `graph-combine` | `select-graph-trial.md` | **yes — never judged before; both bases (indication, D31 drug-only)** |

## Stage 3 — final

| # | Command | Writes |
|---|---|---|
| 3.1 | `$PY main.py --mode assemble` | `output/trial_submission_map.tsv`, `unlinked_submissions.tsv`, `review_trial_context.tsv` |
| 3.2 | `$PY main.py --mode validate` | recall, route agreement (`sources`), conflicts |
| 3.3 | before/after report against the 09-23 map; update D23–D31 status lines in CLAUDE.md and DESIGN.md §3 to "`output/` rebuilt" | — |

## Open decisions before Stage 1

1. **`service/section14.py` has uncommitted edits from another session** — a
   wider patient-count pattern (`a total of N patients`, a qualified cohort
   before the verb). 1.2 extracts with whatever is on disk. Review and commit
   it, or set it aside, before 1.2.
2. **P6's answer key is noisy.** Label-stated ids are the only free truth, but
   14 of the 21 cases the precision test scored as judge errors are units whose
   stored excerpt describes another trial (the excerpt is cut at 600
   characters). Recall (1.6, 3.2) is therefore a floor. A cleaner key uses only
   units whose excerpt shows the id beside its name, with the id blanked before
   judging — not yet built.

## Gates (report each)

- P2: ≥ 90% of non-scanned labels yield a section 14.
- P3: median shortlist ≤ 100 (cap); report the D29 skip count.
- Each judged task: A/B disagreement < 25% per batch, else stop.
- mypy clean and coverage thresholds after any code change made during the run.
