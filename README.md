# Orange Book, Purple Book → clinical trials

Map FDA drug approvals to the clinical trials that supported them, from FDA bulk
data. The question this answers: **which drug was approved on the evidence of
which trial?**

The FDA states it itself, in label section 14 — 2,124 NCT numbers across 871
applications, 1,838 of which are already in our local ClinicalTrials.gov
archive with posted results. That is the payload; everything else here is the
scaffolding needed to join it up.

**The output is flat TSV tables, not a knowledge graph** — the deliverable is one
edge type repeated a few thousand times, which is a table.

```
trial_approval_map.tsv     the deliverable: one row per trial -> approval claim
drugs.tsv                  unii, name
approvals.tsv              application number, type, sponsor, first approval
links.tsv                  nct_id, application, evidence, confidence, polarity
indications.tsv            what each approval is FOR: application -> MeSH disease
indication_text.tsv        the section-1 prose behind those codes, one per application
approval_events.tsv        one row per FDA approval, not per application
trial_approval_dated.tsv   the map re-checked against those events
```

## Layout

```
main.py                        the single entry script; wiring only
config/settings.py             where fda_raw/ is and how each file is shaped
utility/application_number.py  canonicalise + classify FDA application numbers
utility/fda_date.py            every date shape in the corpus, and the four non-dates
utility/logging.py             one logging setup
source/raw_table.py            delimited-table reader (newlines, encodings, ragged rows)
service/identity_census.py     measures the drug-identity chain; proves the parsers
service/approval_map.py        T5 + T8: label -> application, and section 14 -> trials
service/adjudication.py        why a trial is cited, judged by paired agents
service/indications.py         label section 1 -> MeSH disease, the drug->disease edge
service/approval_events.py     the approval-event spine, from Submissions.txt
service/attribution.py         which approval a trial supported, not just which application
source/mesh_descriptors.py     the MeSH release; the disease vocabulary
source/trial_studies.py        study start dates, read only when attribution needs them
docs/drug-identity.md          the drug-key decision record
docs/FDA_APPROVAL_LAYER.md     earlier design proposal, kept for reference only
```

`fda_raw/` (2.9 GB) and `scratch/` are gitignored. `fda_raw/STATS.md` holds the
dual-verified measurements and **wins wherever `fda_raw/README.md` disagrees**.

## Running it

The environment is the existing conda env `clinical-trials-claude`
(Python 3.11). T0/T1 add no dependencies — stdlib only.

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python

$PY main.py --mode census        # ~3s: identity chain, date shapes, conformance, row counts
$PY main.py --mode map           # ~1m: the four mapping tables
$PY main.py --mode indications   # ~4m: indications.tsv and indication_text.tsv
$PY main.py --mode approval-events  # ~5s: the approval-event spine
$PY main.py --mode attribute        # ~3m: re-checks the map (run --mode map first)
```

`--mode indications` also needs the MeSH descriptor release, which lives outside
this repo like the trial archive; override its location with `--mesh`.

## The gates

All four must pass; thresholds are never lowered to make one pass.

```bash
$PY -m pytest                                                         # tests + doctests + 85% floor
$PY -m coverage report --include='utility/*' --fail-under=80           # utility tier
$PY -m coverage report --include='service/*,source/*,config/*' --fail-under=90   # core tier
$PY -m mypy .                                                         # zero errors
```

Currently: 406 tests, 99.94% statement and branch coverage, mypy clean over 37
files. Tests marked `realdata` read `fda_raw/` and skip when it is absent, so
the suite passes on a fresh clone.

## Status

Milestone 1 is the approval spine plus the trial links, reading only `fda_raw/` —
no knowledge graph, no database.

**Done.** T0 (the drug key), T1 (the shared application-number and date
normalisers), T2 (Drugs@FDA into approvals), T5 (label -> application) and T8
(label §14 -> NCT ids), plus the adjudication layer that judges *why* each trial
is cited. 2,679 supporting trial -> approval rows over 804 applications, every
one of the 3,050 extracted links judged by paired independent agents at 97.0%
inter-judge agreement.

**Done beyond the milestone.** The indication layer: 56,685 drug -> disease rows
over 11,525 applications, coded from label section 1 to MeSH. It reaches 15x as
many applications as section 14 does, because section 1 is on 96.5% of labels
against section 14's 17.8%.

**An application is not one approval.** `approval_events.tsv` holds 187,155
approval events over 25,548 applications, of which 31,079 are
indication-changing (an original, or an Efficacy supplement — Labeling and CMC
supplements change the document or the plant, not what the drug treats).
Keytruda's `BLA125514` alone has 109. `trial_approval_dated.tsv` re-checks every
mapping row against them: **534 of 2,679 (19.9%) are refuted**, because the
trial began after the approval the row was stamped with. 52 resolve to exactly
one later approval and are corrected; 477 are marked `ambiguous` with the
window rather than a guess.

**Not started.** T3, the Orange Book substitution data — though `TE.txt` inside
`drugsatfda.zip` now looks like the better source, and it joins on the same
application key the spine uses.

**The known gap.** Only the label filed *with* a submission names the trials
behind it, and those are 29,838 PDFs in Drugs@FDA's `ApplicationDocs`. Reading
them is what would turn `ambiguous` into `resolved` — and would also date the
indication layer, which today reports the current label's accumulated
indications against the original approval date.

**The other open question**: how to link trials to drugs where section 14 is
silent. Measured against the adjudicated answer key, matching on drug name
recovers 63% of known links at 1.5% precision; stacking phase, sponsor,
industry class and date reaches 14.9% precision at 34.3% recall. Adding
indication -> condition matching moves F1 only 0.207 -> 0.225, because
indication is largely collinear with drug name. So name matching is a
*candidate generator*, not a mapping, and the precision has to come from
judgement — the same conclusion section 14 reached.
