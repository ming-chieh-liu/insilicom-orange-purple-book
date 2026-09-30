# Prompt — is this candidate the evidence for an approval with no trial text? (per-candidate Yes/No)

**Version:** 1 · **Created:** 2026-09-29 · **Decision:** D35 · **Runbook:** `docs/runbooks/trial-candidate-match.md`

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
  drug, in the approved disease and population, that finished (primary
  completion) before the date of approval.
- If "APPROVED FOR" says not recorded, the approval could be for any disease;
  use the "Named in literature on" line to see what the trial is about, and
  answer Yes only for an interventional efficacy study of this drug that could
  have supported an approval before the date.
- Answer No for observational studies, registries, extensions and long-term
  safety follow-ups, pharmacokinetic or bioequivalence studies in healthy
  volunteers, studies of a different drug or a different disease, and trials
  that could not have finished before the date of approval.
- An enrollment in the tens of thousands is almost always a registry or an
  observational cohort, not a pivotal trial.
- Answer No if the text does not give you enough to tell. A wrong Yes invents
  a link between a trial and an approval that does not exist, which is far
  worse than a missed one.

Output exactly one line per candidate, in the order shown, and nothing else:
<NCT id> = Yes
<NCT id> = No
```

---

## Field contents

| Field | Built from |
|---|---|
| `{{drug}}` | the application's approved ingredient names, `; `-joined |
| `{{indication}}` | case C: the submission's chosen indication text (`submission_indications.tsv`, `chosen = true`); case D: `not recorded` |
| `{{approval_date}}` | the submission's approval date |
| `{{candidates}}` | one block per candidate, in rank order: the same fields as the excerpt prompt, plus `Named in literature on:` for case D |

## Why it is worded this way

**The question is weaker than case B's, and says so.** With no description of the trial, a judge
cannot tell *which* of several fitting trials the FDA relied on; it can only rule out trials that
could not have been the evidence. That is why a Yes here is tier C or D, below an excerpt match.

**Primary completion before the date is a rule, not a hint.** The candidate block shows it, and a
trial still collecting its primary endpoint cannot have supported the approval.
