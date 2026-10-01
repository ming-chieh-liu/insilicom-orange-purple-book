# Prompt — is this candidate the evidence for an approval with no trial text? (per-candidate Yes/No)

**Version:** 3 · **Created:** 2026-09-29 · **v2:** 2026-09-30 (D37) · **v3:** 2026-10-01 (D39) · **Decision:** D35 · **Runbook:** `docs/runbooks/trial-candidate-match.md`

**v3 (2026-10-01, D39):** prompt wording unchanged; fields gain our normalised codes beside the raw text — `Conditions (normalised):` / `Interventions (normalised):` lines per candidate and the approved drug's and indication's codes, each as `Name [MeSH D…]` or `PubChem:CID…`. They are NLM's and our coding and may be incomplete; a missing line means no code, not a mismatch.

The case C and case D counterpart of `docs/prompts/trial-candidate-match.md` (case B). Used where
the FDA documents give **no §14 description** of the trial:

- **Case C** — the submission's approved indication is known (the approval letter's "provides
  for …" sentence, else its label's section 1); candidates are the drug's registry trials,
  filtered on that indication, plus trials the literature names for this drug and indication.
- **Case D** — no indication is recorded; candidates are trials named in the literature on what
  the drug treats, each shown with the diseases it was found under.

One judgement = one **tier** of up to 10 candidates, each answered on its own, exactly as in D33.
A submission stops at the first tier holding a Yes; its answer is the highest-ranked Yes.
Results are tier C (case C) or tier D (case D), never promoted (`docs/TIERS.md`).

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
- If "APPROVED FOR" says not recorded, the approval could be for any disease;
  use the "Named in literature on" line to see what the trial is about, and
  answer Yes only for an interventional efficacy study of this drug that could
  have supported an approval before the date.
- Answer No for observational studies, registries, extensions and long-term
  safety follow-ups, pharmacokinetic or bioequivalence studies in healthy
  volunteers, studies of a different drug or a different disease, and trials
  that could not have produced results before the date of approval.
- An enrollment in the tens of thousands is almost always a registry or an
  observational cohort, not a pivotal trial.
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
| `{{indication}}` | case C: the submission's chosen indication text (`submission_indications.tsv`, `chosen = true`, up to 2,000 characters); when that is the drug's **current** openFDA label rather than a document of this submission, it is prefixed `(no document of this submission states its indication; this is the drug's current label, which may include indications approved later)` (2026-09-30 — field content, not prompt wording); case D: `not recorded` |
| `{{approval_date}}` | the submission's approval date |
| `{{candidates}}` | one block per candidate, in rank order: the same fields as the excerpt prompt v2 (status and why stopped included), plus `Named in literature on:` for case D |

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
