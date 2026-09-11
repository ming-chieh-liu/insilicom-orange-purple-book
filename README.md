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
drugs.tsv                  unii, name, type
approvals.tsv              application number, date, sponsor, status
links.tsv                  nct_id, application number, evidence, confidence
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

$PY main.py --mode census    # ~3s: the identity chain, date shapes, conformance, row counts
```

## The gates

All four must pass; thresholds are never lowered to make one pass.

```bash
$PY -m pytest                                                         # tests + doctests + 85% floor
$PY -m coverage report --include='utility/*' --fail-under=80           # utility tier
$PY -m coverage report --include='service/*,source/*,config/*' --fail-under=90   # core tier
$PY -m mypy .                                                         # zero errors
```

Currently: 231 tests, 100% statement and branch coverage, mypy clean over 18
files. Tests marked `realdata` read `fda_raw/` and skip when it is absent, so
the suite passes on a fresh clone.

## Status

Milestone 1 is the approval spine plus the trial links, reading only `fda_raw/` —
no knowledge graph, no database. **T0** (the drug key) and **T1** (the shared
application-number and date normalisers) are done. Next: T5 (label → application),
then T8 (label §14 → NCT ids), which is what produces the mapping.
