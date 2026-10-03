# Prompt — is this candidate the evidence for an approval with no trial text? (per-candidate Yes/No)

**Version:** 5 · **Created:** 2026-09-29 · **v2:** 2026-09-30 (D37) · **v3:** 2026-10-01 (D39) · **v4:** 2026-10-01 · **v5:** 2026-10-03 · **Decision:** D35 · **Runbook:** `docs/runbooks/trial-candidate-match.md`

**v4 (2026-10-01):** the "APPROVED FOR says not recorded" rule is removed. It served case D, which since D37 is judged with `trial-literature-match.md`; this prompt now serves only C−, which always has an indication.
**v3 (2026-10-01, D39):** prompt wording unchanged; fields gain our normalised codes beside the raw text — `Conditions (normalised):` / `Interventions (normalised):` lines per candidate and the approved drug's and indication's codes, each as `Name [MeSH D…]` or `PubChem:CID…`. They are NLM's and our coding and may be incomplete; a missing line means no code, not a mismatch.

The route C− counterpart of `docs/prompts/trial-candidate-match.md` (B−). Used where the FDA
documents give **no §14 description** of the trial but the submission's approved indication is
known (the approval letter's "provides for …" sentence, else its label's section 1); candidates
are the drug's registry trials, filtered on that indication. Since D37 the literature's trials for
this drug and indication (C+) and the no-indication case (D) are judged with
`docs/prompts/trial-literature-match.md` instead.

One judgement = one **tier** of up to 10 candidates, each answered on its own, exactly as in D33.
A submission stops at the first tier holding a Yes; its answer is the highest-ranked Yes.
Results are tier C−, never promoted (`docs/TIERS.md`).

Changing this file changes what stored answers mean: **bump the version** and stamp it on every
row rather than editing in place (`candidate_match.NOLABEL_PROMPT_VERSION`).

---

## The prompt

```text
You are checking, one by one, whether registered clinical trials could be the
trial whose results supported an FDA approval. No FDA document we can read
describes that trial, so you have only the approval itself to go on.

APPROVED DRUG: {{drug}}
APPROVED FOR: {{indication}}
DATE OF APPROVAL: {{approval_date}}
THIS PRODUCT: {{product}}

CANDIDATE TRIALS (from ClinicalTrials.gov):
<candidates>
{{candidates}}
</candidates>

QUESTION: for EACH candidate, could it be the clinical trial whose results
supported this approval?

RULES:
- Judge each candidate ON ITS OWN, as if it were the only one shown. Do not
  compare candidates with each other, rank them, or choose between them: two
  or more candidates may each be Yes, and all of them may be No.
- Judge ONLY from the text above. Do not use outside knowledge of this drug,
  trial or disease. Do not look anything up.
- Answer Yes only if the candidate is an interventional efficacy study of this
  drug, in the approved disease and population, whose primary results could
  have been available by the date of approval.
- Answer No for observational studies, registries, extensions and long-term
  safety follow-ups, pharmacokinetic or bioequivalence studies in healthy
  volunteers, studies of a different drug or a different disease, and trials
  that could not have produced results before the date of approval.
- An enrollment in the tens of thousands is almost always a registry or an
  observational cohort, not a pivotal trial.
- If THIS PRODUCT says it is a new form or version of a drug approved earlier,
  answer Yes only for a trial that tested this product — its new form, route
  or formulation, or a new use. A trial of the drug as it was first approved
  is No: that drug's trials are not this product's evidence.
- Each candidate's Status and Why stopped are the registry's today, not as of
  the approval. A trial still running today may have reported its primary
  results before the approval. A trial stopped early because it met its goal,
  or after its primary analysis, can support an approval; one stopped for
  safety, futility, poor enrollment, or before it produced results cannot.
- Answer No if the text does not give you enough to tell. A wrong Yes invents
  a link between a trial and an approval that does not exist, which is far
  worse than a missed one.

Output exactly one line per candidate, in the order shown, and nothing else:
<NCT id> = Yes
<NCT id> = No
```

---

## Field contents

v3 additions (D39): `{{approval}}` gains `Drug (normalised): …` and `Indication (normalised): …` lines where codes exist; `{{drug}}` and `{{indication}}` (no-label prompt) gain a `(normalised: …)` line; every candidate block gains `Conditions (normalised):` and `Interventions (normalised):` after the raw lines. Candidates also pass the D39 phase rule (phase 2, 3, 2/3, 1/2, 4, or phase 1 with ≥100 enrolled) and are ranked with the applicant's own trials first after the start date.

| Field | Built from |
|---|---|
| `{{drug}}` | the approved drug as `<brand> (<generic>)` from P1's `drug_names.tsv` (v2); the ingredient names where the table has none |
| `{{indication}}` | case C: the submission's chosen indication text (`submission_indications.tsv`, `chosen = true`, up to 2,000 characters); when that is the drug's **current** openFDA label rather than a document of this submission, it is prefixed `(no document of this submission states its indication; this is the drug's current label, which may include indications approved later)` (2026-09-30 — field content, not prompt wording); case D no longer uses this prompt (D37) |
| `{{approval_date}}` | the submission's approval date |
| `{{candidates}}` | one block per candidate, in rank order: the same fields as the excerpt prompt v2 (status and why stopped included), (case C− only since D37; no `Named in literature on:` line) |

## Changes in v2 (2026-09-30, D37)

- `{{drug}}` is the brand and generic name; candidates show **status** and **why stopped**, with
  the status rule above.
- "Finished (primary completion) before the date" became "primary results could have been
  available by the date": registry completion dates are revised later, and 9% of the trials FDA
  labels name show a primary completion after their approval.

## Why it is worded this way

**The question is weaker than case B's, and says so.** With no description of the trial, a judge
cannot tell *which* of several fitting trials the FDA relied on; it can only rule out trials that
could not have been the evidence. That is why a Yes here is tier C or D, below an excerpt match.

**Primary completion is shown, not enforced (v2).** A trial still collecting its primary endpoint
cannot have supported the approval, but the registry's dates are revised after the fact, so the
judge weighs the date with the status instead of a hard cut.

## v5 (2026-10-03): the product line

`{{product}}` says whether this approval is a new dosage form (ORIG class `TYPE 3`) or a new
formulation / new manufacturer (`TYPE 5`) of a molecule an earlier application already brought to
market (same moiety group, `drug_names.moiety_key`), and when and as what that molecule was first
approved (`candidate_match.attach_products`); otherwise "not a new form of an earlier-approved
drug, as far as Drugs@FDA's records show". Why: in the 2026-10-02 pilot the judges said Yes to old
trials of the molecule for products that have no trials of their own (a premixed diltiazem bag,
2021 → a 2011 rate-control study; zidovudine 60 mg tablets, 2009 → the Tshepo study). 2,264 of
6,181 S9 units sit on such applications, but 259 of those applications have their own FDA-named
trial that started after the molecule's first approval, so they are told, not routed out.
