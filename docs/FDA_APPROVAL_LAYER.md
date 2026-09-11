# FDA Approval Layer — design for linking trials → approved drugs

> **Status:** PROPOSED (not yet decided, not yet emitted) · **Written:** 2026-09-04
> **Builds on:** [`CURRENT_KG_SCHEMA.md`](../schema/CURRENT_KG_SCHEMA.md) (the live IKraph
> schema) · [`DESIGN_G.md`](../schema/DESIGN_G.md) (the trial emitter) ·
> [`COMPLEX_RELATION.md`](../schema/COMPLEX_RELATION.md) (**D-71**, a relation can be a node).
> **Authority:** none yet — this is a proposal to open decisions D-140…D-146.

This is the third layer. Layer 1 is the trial graph (Design G, emitted). Layer 2 is
IKraph's existing drug/disease graph (live in `biokde`). This document specifies the
**FDA approval layer** that joins them so the question *"which trial results belong to a
drug the FDA later approved, and for what?"* becomes one traversal.

It is written to the same law as the rest of the package: **an approval is a relation,
and the relation is a node** (**D-71**) — it links a drug to an indication and carries
its own date, application number and pathway on its edges, so it can be pointed at,
qualified and compared.

---

## 1. What the two existing layers already give us

Measured 2026-09-04 against live `biokde` and the CT.gov API v2.

| asset | where | number |
|---|---|---|
| result-bearing interventional trials | CT.gov (registry) | **75,312** |
| …with ≥1 drug/biologic intervention | probe (n=6,000) | **70.3%** |
| …naming ≥1 **already-FDA-approved** drug | probe, crude match | **63.6%** of drug trials *(floor)* |
| IKraph `Chemical` nodes | `pubmedEntities` | **5.84M** (PubChem / DrugBank / MeSH keyed) |
| …keyed by `MESH:{ui}` (the trial-side bridge key) | `pubmedEntities` | **67,310** |
| drug→disease `treats` edges (literature efficacy signal) | `pubmedRelations` | **61,690** |
| FDA-sourced `Pharmacologic Class` nodes | `pubmedEntities` | **345** *(class only — no drug-level approval facts)* |

**The gap this layer fills:** IKraph has the *drugs* and the *literature*, and the CT
graph has the *trials and their results*, but **neither holds a single FDA approval
fact** — no approval date, no approved indication, no application number, and no edge
saying *this trial supported this approval*. That is exactly what a client asking to
"link trial results to FDA-approved drugs" is asking for.

---

## 2. The identity problem, and the key that solves it

A drug is named three different ways across the three layers:

| layer | how a drug is named | example (atezolizumab) |
|---|---|---|
| CT.gov intervention | free text arm/intervention label | `"Atezolizumab"`, `"MPDL3280A"`, `"Atezolizumab 1200 mg"` |
| IKraph `Chemical` | PubChem / DrugBank / **MeSH** id | `MESH:C000594389` |
| Drugs@FDA | **UNII** (FDA substance id) + active-ingredient name | `UNII: 52CMI0WC3Y`, `ATEZOLIZUMAB` |

**Decision to open — D-140 (drug identity spine): adopt `UNII` as the FDA-side key and
carry an explicit crosswalk to IKraph's `biokdeid`.** UNII is the FDA's own ingredient
identifier, present in Drugs@FDA/openFDA, and it maps cleanly through RxNorm and PubChem
to IKraph's existing ids. The crosswalk table is the one new *irreplaceable* input this
layer needs (§6).

> **Why not match on name.** The probe matched 63.6% of drug trials to an approved drug
> with crude string matching; the misses were dominated by (a) **salt forms** the string
> matcher dropped — `SUNITINIB` vs Drugs@FDA's `SUNITINIB MALATE` — and (b) **company
> compound codes** (`BI-`, `MK-`, `PF-`, `BMS-`, `TAK-`) that are *legitimately not yet
> approved*. (a) is recovered by normalizing to UNII; (b) must **stay unmatched**, and
> is itself the interesting cohort — trials of drugs that may be approved later. Name
> matching cannot tell these apart; identity keys can.

---

## 3. New nodes

Reuse `Chemical` and `Disease` unchanged. Add two node types.

### `FDAApproval` — a relation-node (per **D-71**)

One node per **(active-ingredient, application, approved-indication)** granted use. It is
the hub the drug, the indication and the trial all attach to.

```jsonc
{
  "type": "FDAApproval",
  "subtype": "NA",
  "id": "FDA:NDA761034:atezolizumab:urothelial-carcinoma",   // application# + UNII-slug + indication-slug
  "tmp_biokdeid": "6000000001",                              // minted like the trial ids (D-6 base, own range)
  "official name": "Atezolizumab — approved for urothelial carcinoma (NDA/BLA 761034)",
  "common name": "atezolizumab / urothelial carcinoma",
  "srcID_and_URL": [{ "Drugs@FDA:761034": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=761034" }]
}
```

### `FDAApplication` — the regulatory container (optional, thin)

One node per NDA/BLA number, so *"every indication under this application"* and
*"every supplement"* is one lookup. May be folded into `Organization`-style shared nodes;
kept separate here because sponsors change over an application's life.

```jsonc
{ "type": "FDAApplication", "id": "FDA:761034", "common name": "BLA 761034 (TECENTRIQ)",
  "official name": "atezolizumab — Genentech", "tmp_biokdeid": "6000500001" }
```

---

## 4. New edges

Follows the trial emitter's convention exactly: the relation carries `tmp_biokdeid` so it
can itself be qualified, reuses the IKraph `relationship_type` enum where a code fits, and
adds an `fda_meta` block mirroring the trial graph's `ct_meta` (**D-25**).

| role | from → to | rel type (enum) | carries |
|---|---|---|---|
| `approved_drug` | `FDAApproval` → `Chemical` | `associates` (28) | the drug this approval is for; the edge is the UNII↔biokdeid crosswalk in force |
| `approved_indication` | `FDAApproval` → `Disease` | `treats` (13) | **the approved therapeutic claim** — the one place the graph asserts FDA-blessed efficacy, distinct from the 61,690 text-mined `treats` edges (method `MA-FDA`, not `MP`) |
| `approval_property` | **`approved_indication`** → `TrialProperty` | `has_value_of` (21) | `approval_date`, `application_number`, `application_type` (NDA/BLA/ANDA), `marketing_status`, `approval_pathway` (accelerated / breakthrough / orphan), `first_approval` (bool) |
| `in_application` | `FDAApproval` → `FDAApplication` | `part_of` (2) | groups indications/supplements under one NDA/BLA |
| **`supported_approval`** | `ClinicalTrial` → `FDAApproval` | `associates` (28) | ⭐ **the payload edge** — this trial was cited as evidence for this approval; qualified on the edge with `role` (pivotal / supporting), `source` (label / review / curated) |
| `pharm_class` | `Chemical` → `Pharmacologic Class` | `has_property_of` (3) | reuse IKraph's existing 345 FDA/DrugCentral classes; now reachable from an approval |

`method` on every FDA edge is a **new code `MA-FDA`** (manually annotated, FDA source) —
kept distinct from `MP` (text-mined) and `CT` (trial) so a consumer can always tell an
FDA-asserted fact from a literature-inferred one. Opens **D-141**.

### The one query the layer exists to answer

```
ClinicalTrial --supported_approval--> FDAApproval --approved_indication(treats)--> Disease
                                                  --approved_drug--> Chemical <--MESH bridge-- (same trial's intervention)
```
→ *"trials whose own intervention became FDA-approved for the trial's own condition,"*
with the trial's effect size (already in Design G: `OutcomeAnalysis`) and the approval
date on the edge. The `supported_approval` edge is what turns a co-occurrence into a
provenance link.

---

## 5. Where every input comes from

| input | source | rebuildable? | notes |
|---|---|---|---|
| approval date, application #, marketing status, active ingredient (**UNII**) | **openFDA `drug/drugsfda`** bulk (`drug-drugsfda-0001-of-0001.json.zip`, ~9 MB, 29,312 applications) | ✅ yes — one download, already validated 2026-09-04 | the spine of §3–§4 |
| approved **indication** (structured) | **DrugCentral** (`omop_relationship` / `indication`) + **openFDA `drug/label`** `indications_and_usage` | ⚠️ label text needs parsing to MeSH/MedDRA | the hard normalization; start with DrugCentral's curated indication table |
| approval **pathway** (accelerated/breakthrough/orphan) | Drugs@FDA review metadata; FDA orphan/breakthrough designation lists | ✅ | flags only |
| **UNII ↔ RxNorm ↔ PubChem/MeSH** crosswalk | RxNorm (RxNav API) + FDA UNII/GSRS + PubChem | ⚠️ | **the irreplaceable join table** — build once, track ever after, like `config/mesh_terms.json` |
| **`supported_approval`** (trial → approval) | ① **Open Targets** (`drug.linkedTargets` / known-drugs has trial+phase+approval); ② FDA approval-package / label "Clinical Studies" sections (cite NCTs); ③ curated lane | ❌ partly curated | the highest-value, lowest-availability edge — model as an agent lane like `arm_decisions/` |

**No FDA data is inherited from IKraph.** The layer downloads its own regulatory substrate
and emits its own nodes/edges, exactly as the trial package downloads `output_raw/`.

---

## 6. What a first build will **NOT** have

Stated up front so the output does not look broken (mirrors DESIGN_G's discipline).

| | |
|---|---|
| **Indication normalization** | first pass keys approvals to the **verbatim** Drugs@FDA/DrugCentral indication string + a best-effort MeSH id. MedDRA/ICD resolution deferred (inherits trial-side **D-5**) |
| **`supported_approval` coverage** | seeded from Open Targets + label parsing only; a *curated* pivotal-trial link exists **only where an agent lane has ruled**. Absence of the edge ≠ the trial was not pivotal |
| **Biologics/combination products** | UNII maps cleanly for small molecules and mAbs; fixed-dose combinations and cell/gene therapies need per-product rules (opens **D-145**) |
| **Non-US / withdrawn** | Drugs@FDA only. EMA and withdrawn-approval status are out of scope for v1 (opens **D-146**) |
| **Approval ≠ efficacy for a given trial** | the graph states *that* a drug was approved and *that* a trial supported it; it does **not** assert the trial met its endpoint — that stays where Design G already puts it, on `OutcomeAnalysis` |

---

## 7. Build order (fits the existing `--mode` pipeline)

1. **`--mode fda-ingest`** — parse the openFDA drugsfda bulk into `FDAApproval` /
   `FDAApplication` nodes + `approved_drug` / `approval_property` edges. Self-contained,
   rebuildable, ~1 pass. *(No trial or IKraph dependency — ship this first.)*
2. **`--mode fda-crosswalk`** — build UNII↔biokdeid via RxNorm/PubChem; emit
   `approved_drug` edges onto existing `Chemical` nodes. Report unmatched UNIIs.
3. **`--mode fda-indication`** — attach DrugCentral indications as `approved_indication`
   (`treats`, `MA-FDA`); MeSH id where resolvable, verbatim otherwise.
4. **`supported_approval` lane** — agent lane (two subagents + adjudicator, per
   [`ORCHESTRATION.md`](../ORCHESTRATION.md)) seeded by Open Targets + label parsing,
   curated per drug. This is the deliverable's centre of gravity.
5. **Merge** — resolve the shared open questions with the IKraph team **before** loading:
   `tmp_biokdeid` allocation (**D-6/D-18**), `fda_meta`/`ct_meta` extension (**D-25**),
   relation-row ids (**D-98**). The FDA layer inherits all three; do not solve them twice.

**Fastest client-demo path:** run steps 1–3 for **one therapeutic area** (oncology —
best Open Targets coverage) over the already-emitted result-bearing trials, hand-curate
`supported_approval` for ~20 drugs, and show the §4 traversal end-to-end before committing
to the full 79,892-trial emit (~16 h, ~210 GB per `RUN.md`).
