# What one drug is

**Status:** DECIDED 2026-09-10 · measured 2026-09-11 · reproducible with
`python main.py --mode census`

This is the decision record for the drug key — the thing every table in this
project joins on. Nothing downstream can be counted until it is settled, because
the same corpus is 137,804 NDC products, 51,742 approved products, 29,323
applications, 8,368 brand names, 2,790 ingredient names or 2,597 substances
depending only on what you call "a drug".

Every figure below is produced by the census, not transcribed from notes, and is
asserted by `tests/test_identity_census.py`.

---

## 1. The chain, to the unit

```
  2,790  NDA/BLA ingredient components        split on ';' only
 -    67  matched no UNII name                 LOST
 =  2,723  resolved names
 -   126  merged by name fan-in               175 names in 49 UNII groups
 =  2,597  distinct UNII  ← the drug key
```

The recipe, exactly: take `Products.ActiveIngredient`, split on `';'`, strip,
upper-case, drop empties, keep only products whose application is typed **NDA or
BLA** in `Applications.txt`, then match case-insensitively into
`UNII_Names.NAME`.

**The two subtractions are not the same kind of thing, and quoting 2,597 without
both hides a gap behind a win.** The 67 are *lost* — drugs absent from the key
space. The 126 are *merged* — several names for one substance, which is the
crosswalk doing its job.

**0 of the 2,723 resolved names map to more than one UNII.** Names collapse; they
never split. That is the property the whole key rests on, and it is asserted as
its own test.

Two exclusions, deliberately kept separate:

- **22,954 ANDAs** are excluded by design — a generic is a copy of a drug already
  counted under its NDA.
- **8 `Products.txt` application numbers have no `Applications.txt` row at all**
  (`040850 040853 071403 077089 200171 204086 205476 205581`), so they cannot be
  typed and drop out. They are counted and named rather than defaulted to NDA,
  which would silently invent a drug.

---

## 2. The key is the UNII the source names, at UNII's own grain

`ATORVASTATIN CALCIUM` resolves to `48A5M73Z4Q` (atorvastatin calcium
trihydrate) and stays **distinct** from `ATORVASTATIN` → `A0JWA85V8F`.

Chosen because it is reproducible today with nothing further to download, and
because it preserves the measured chain above. The alternative — resolving every
salt to its active moiety — needs UNII files that are not on disk (`UNIIs.zip`
contains only the names list) and would move the 2,597 to an unknown number,
requiring every published figure to be re-measured.

**Call it "the grain UNII draws", not "salt-level".** That is not pedantry: UNII
is not consistent about where it draws the line, and §3 measures exactly how
inconsistent.

> **Deferred:** active-moiety resolution. When it happens, 2,597 falls and every
> count in this document moves with it.

---

## 3. Biosimilars collapse to one UNII — and so do 25 other things

A biosimilar shares its reference product's UNII, so `DENOSUMAB` and its ten
suffixed forms are one row in `drugs.tsv`; their eleven distinct approvals hang
off it as separate rows in `approvals.tsv`. The largest groups:

| UNII | family | names |
|---|---|---|
| `4EQZ6YO2HI` | DENOSUMAB | 11 |
| `FYS6T7F842` | ADALIMUMAB | 10 |
| `3A58010674` | PEGFILGRASTIM | 9 |
| `FU77B4U5Z0` | USTEKINUMAB | 9 |
| `15C2VL427D` | AFLIBERCEPT | 8 |
| `2S9ZZM9Q9V` | BEVACIZUMAB | 8 |

**But only 24 of the 49 fan-in groups are biosimilar families. The other 25 are
collapses nobody decided on**, and they are the reason §2 is worded the way it
is. Hydrates, crystal forms, expression systems and plain synonyms all merge:

```
W34SHF8J2K  FORMOTEROL FUMARATE | FORMOTEROL FUMARATE DIHYDRATE
32HRV3E3D5  GRISEOFULVIN, MICROCRYSTALLINE | GRISEOFULVIN, ULTRAMICROSIZE
927AH8112L  NITROFURANTOIN | NITROFURANTOIN, MACROCRYSTALLINE
QQE170PANO  MANGANESE CHLORIDE | MANGANESE CHLORIDE TETRAHYDRATE
1Y17CTI5SR  INSULIN HUMAN | INSULIN RECOMBINANT HUMAN
7SFC6U2VI5  CALCITONIN SALMON | CALCITONIN SALMON RECOMBINANT
NQX9KB6PCL  SOMATROPIN | SOMATROPIN RECOMBINANT
4FT78T86XV  INSULIN DETEMIR | INSULIN DETEMIR RECOMBINANT
VL756B1K0U  NETARSUDIL DIMESYLATE | NETARSUDIL MESYLATE
A034SE7857  PHYTONADIONE | VITAMIN K
3K9958V90M  ALCOHOL | DEHYDRATED ALCOHOL
K21NZZ5Y0B  AIR | MEDICAL AIR
059QF0KO0R  PURIFIED WATER | STERILE WATER FOR INJECTION | STERILE WATER FOR IRRIGATION
ULW86O013H  GLUTATHIONE DISULFIDE | OXIGLUTATIONE
D0L9J6I13I  PORCINE SECRETIN | SECRETIN SYNTHETIC PORCINE
K0U68Q2TXA  CORTICOTROPIN | REPOSITORY CORTICOTROPIN
H192N84N1G  ALSEROXYLON | RAUWOLFIA SERPENTINA ROOT
9E8X80D2L0  ALPHA-TOCOPHEROL ACETATE | TOCOPHEROL ACETATE
6M3C89ZY6R  NICOTINE | NICOTINE POLACRILEX
743QUY4VD8  HYALURONIDASE (HUMAN RECOMBINANT) | HYALURONIDASE RECOMBINANT HUMAN | HYALURONIDASE-NVHY
494JNQ8L28  TECHNETIUM TC-99M SUCCIMER | … SUCCIMER KIT
556Q0P6PB1  TECHNETIUM TC-99M SULFUR COLLOID | … SULFUR COLLOID KIT
A0730CX801  TECHNETIUM TC-99M SODIUM PERTECHNETATE | … GENERATOR
Z8E46IA45W  TECHNETIUM TC-99M ALBUMIN AGGREGATED KIT | … ALBUMIN COLLOID KIT
E211KPY694  ABOBOTULINUMTOXINA | DAXIBOTULINUMTOXINA-LANM | INCOBOTULINUMTOXINA | ONABOTULINUMTOXINA
```

**`E211KPY694` is the one to worry about.** Four botulinum toxin products —
Dysport, Daxxify, Xeomin and Botox — share one UNII. They are separate
approvals with separate trials and are **not** clinically interchangeable, yet
under this key they are one drug. The mapping stays correct (each approval keeps
its own application number and its own trials), but any per-drug aggregate will
pool them, and nobody chose that.

The four `TECHNETIUM … KIT` pairs are the same shape of problem in miniature: a
kit and its prepared product are one substance to UNII.

> **Not deferred, because it is not a bug to fix:** this is what the chosen key
> means. It is recorded so a later reader does not mistake a merged hydrate for
> a lost drug.

---

## 4. Ingredients split on `';'` only

Reproduces 2,790/2,597 exactly, which is why it was chosen. The column also
uses `','` (433 rows), `' AND '` (43) and `'/'` (159), and those are a recorded
gap rather than an oversight: splitting on `','` would break `CARBON DIOXIDE,
USP` into two substances and would re-derive both published totals.

**The measured cost is the 67 unmatched names**, and they are not one problem
but five:

| cause | n | examples |
|---|---:|---|
| **split inside parentheses** | **10** | `LIOTRIX (T4;T3)` → `LIOTRIX (T4` + `T3)`; also `MENOTROPINS (FSH;LH)`, `PANCRELIPASE (AMYLASE;LIPASE;PROTEASE)`, `TRIPLE SULFA (…)`, `TRISULFAPYRIMIDINES (…)` |
| combinations the `';'` rule cannot split | ~20 | `DARUNAVIR AND COBICISTAT`, `AMPHETAMINE ASPARTATE/DEXTROAMPHETAMINE SULFATE`, `ADAPALENE, BENZOYL PEROXIDE`, `ELEXACAFTOR, IVACAFTOR, TEZACAFTOR` |
| biologics newer than the 4 Aug 2026 UNII list | ~14 | `ADALIMUMAB-AQVH`, `AXATILIMAB-CSFR`, `GARETOSMAB-GRTS`, `TRASTUZUMAB AND HYALURONIDASE-OYSK` |
| source typos | 3 | `AZELASTINE HYDROCHRLORIDE`, `CARBON DIOIDE`, `OMEGA-3ACID ETHYL ESTERS` |
| grade qualifiers, resin complexes, kits | rest | `CARBON DIOXIDE, USP`, `NITROGEN, NF`, `AMPHETAMINE RESIN COMPLEX`, `SOMATROPIN [RDNA ORIGIN]` |

The parenthesis case is the one worth knowing about: `';'` is used **both** as
the ingredient separator and inside a parenthesised list of an ingredient's
components, so a `';'`-only split produces fragments that are not substances at
all. 10 of the 67 losses (15%) are that artifact, and they are recoverable by a
splitter that respects parentheses — the cheapest available improvement to
coverage.

> **Deferred:** `','`, `' AND '`, `'/'` splitting, and parenthesis-aware
> splitting. Any of them moves 2,790 and 2,597.

---

## 5. Also deferred

- The **1,153 applications** with an approved `ORIG` submission but no
  `Applications.txt` row.
- The **400 Purple Book BLA numbers** absent from Drugs@FDA (CBER-only:
  vaccines, blood products, cell and gene therapies). Every count here is a
  **floor** for biologics.
- Whether the `1900-01-01` date sentinel (1,845 rows) is stripped at ingest or
  carried with its flag.

## 6. How to re-verify

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
$PY main.py --mode census       # ~3s; prints the chain, dates, conformance, row counts
$PY -m pytest                   # asserts every number above
```

The census must print `2,790 - 67 - 126 = 2,597   OK`, `names mapping to >1
UNII  0`, and `8` untypable ApplNos. Any drift is a finding about the data or a
new snapshot — **not** a test to relax.
