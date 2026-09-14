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
source/mesh_descriptors.py     the MeSH release; the disease vocabulary
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

Currently: 363 tests, 99.93% statement and branch coverage, mypy clean over 31
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

**Not started.** T3, the Orange Book substitution data. The three Orange Book
files are read by the census but no TE code or RLD flag reaches an output.

**The next question**, and the one the indication layer was built to inform: how
to link trials to drugs where section 14 is silent. Measured against the
adjudicated answer key, matching on drug name recovers 63% of known links at
1.5% precision; stacking phase, sponsor, industry class and date reaches 14.9%
precision at 34.3% recall. Adding indication -> condition matching moves F1 only
0.207 -> 0.225, because indication is largely collinear with drug name. So
name matching is a *candidate generator*, not a mapping, and the precision has
to come from judgement — the same conclusion section 14 reached.
