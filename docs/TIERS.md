# Evidence tiers — what each tier claims

**Decision:** D35 (2026-09-29), refining D10, D14, D27, D32. **Status:** implemented in code
2026-09-29 (`service/assemble.py`); `output/` not yet rebuilt, so the current map still has the
pre-D35 tiers.

Every row of `trial_submission_map.tsv` carries one tier. The tier says **how the trial's
identity was established** for that approval, not how confident a judge felt. A tier is never
promoted. When several routes find the same trial for the same approval, the highest tier is
kept and the other routes are listed in `sources`.

| Tier | Case | What we had | How the trial was found | What the judge was asked | Where |
|---|---|---|---|---|---|
| **A** | An FDA document **names** the trial | A label-stated NCT id; a protocol id in the FDA review matching exactly one registry study; or a label trial name matching an id the same submission's review named | No search: the document gives the id | Only its **role** — pivotal, supporting, or something else (failed, PK, safety, not for this approval). Only pivotal or supporting links | P2a / P2c → P4r (reviews), P4b / D28 (labels) |
| **B** | A **§14 description** with no identifier | The label's own paragraph about the trial: design, population, patient count | Drug + date pool → indication filter (MeSH tree) → top 100 by rank, **plus** literature candidates for this drug × indication | "Is this candidate the trial this excerpt describes?" — each candidate Yes/No, tiers of 10, first Yes wins | P3 → P4 (D33) |
| **C** | An **indication**, but no judgeable §14 description, and no pivotal/supporting review trial (D36) | The approved indication (letter sentence or label §1) and the approval date; no trial text | Same candidate list as B, built on the submission's indication | "Is this candidate the evidence for this approval?" — per candidate, from drug + indication + date + the registry record only | P3 → P4 no-label |
| **D** | **No indication** at all | Only the drug and the approval date | The drug's "treats" edges in the literature graph to any disease (D31) → trials named in those abstracts, started and completed before the approval | Same question as C, with the indication "not recorded" | P2g → P4 no-label |

## Why the order is A > B > C > D

- **A** rests on the FDA's own identifier; the only judgement is whether the trial *supported* the
  approval, not *which* trial it is.
- **B** is an agent's inference, but against a specific description the FDA wrote about that trial.
- **C** has no trial description to match — only "this drug, this disease, before this date" —
  so several plausible trials can fit.
- **D** does not even know the disease; the literature supplies it.

## What a tier does not say

- Not that the patient counts agree — counts are on every row as information (D32).
- Not that two routes agree — that is `sources`.
- Not that the link is disputed — that is `conflict`.
