# Prompt — is this candidate the trial the label describes? (per-candidate Yes/No)

**Version:** 2 · **Created:** 2026-09-28 · **v2:** 2026-09-30 (D37) · **Decision:** D33 · **Runbook:** `docs/runbooks/trial-candidate-match.md`

Replaces `docs/prompts/trial-identification.md` (v1, "pick one of the list") for P4. That prompt
and its two-judge runbook stay in the repo, unchanged, for reuse.

One judgement = one **tier**: one section-14 description and up to 10 of its ranked candidates
(ranks 1–10, then 11–20, …). The judge answers Yes or No for **each candidate separately**. P3
returns every shortlist in rank order (commit 308887d), so tiers are worked top-down and a
description stops at the first tier holding a Yes; the answer is the highest-ranked Yes.

Changing this file changes what stored verdicts mean: **bump the version** and stamp it on every
row rather than editing in place.

---

## The prompt

```text
You are checking, one by one, whether registered clinical trials are the trial
that an FDA drug label describes.

APPROVAL:
{{approval}}

LABEL EXCERPT (Section 14, Clinical Studies — it describes ONE trial):
<excerpt>
{{excerpt}}
</excerpt>
The label calls this trial: {{trial_name}}
The label says it enrolled: {{enrollment}} patients

CANDIDATE TRIALS (from ClinicalTrials.gov):
<candidates>
{{candidates}}
</candidates>

QUESTION: for EACH candidate, is it the trial the label excerpt describes,
i.e. the trial whose results support this approval?

RULES:
- Judge each candidate ON ITS OWN, as if it were the only one shown. Do not
  compare candidates with each other, rank them, or choose between them: two
  or more candidates may each be Yes, and all of them may be No.
- Judge ONLY from the text above. Do not use outside knowledge of this drug,
  trial or disease. Do not look anything up.
- Answer Yes only if the candidate matches the excerpt on the things the
  excerpt states: the drug and comparator, the disease and population, the
  design (randomized, placebo- or active-controlled, open-label, single-arm)
  and the size. A label may report patients analysed where the registry
  reports patients enrolled, so a small difference in size is normal and a
  large one is not.
- A matching trial name or protocol id is strong evidence; a trial of the same
  drug in the same disease that differs in design, size or population is No.
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

| Field | Built from |
|---|---|
| `{{approval}}` | `<brand> (<generic>) · <application> <submission type> <number> · approved <date> · section 14 heading: <heading>` — the drug from P1's `drug_names.tsv` (v2) |
| `{{excerpt}}` | the section-14 excerpt P2 extracted for this description |
| `{{trial_name}}` | the label's trial name, or `(the label gives no name)` |
| `{{enrollment}}` | the label's patient count, or `(the label gives no count)` |
| `{{candidates}}` | one block per candidate, in rank order: NCT id, title, acronym, protocol id (`orgStudyId`), lead sponsor, phase, conditions, interventions (each up to 1,000 characters, v2), enrolled, start date, primary completion, **status**, and **why stopped** when the registry gives one (v2). Trials that never ran (WITHDRAWN, NOT_YET_RECRUITING) are never candidates |

## Changes in v2 (2026-09-30, D37)

- `APPROVAL` names the drug: brand and generic name (P1 `drug_names.tsv`).
- Each candidate shows its registry **status** and **why stopped**, with the rule above. A
  hard filter on COMPLETED was measured and rejected: 14% of the trials FDA labels name are not
  COMPLETED in the registry (11% ACTIVE_NOT_RECRUITING, 1.4% TERMINATED — some stopped *for*
  efficacy).
- Conditions and interventions are cut at 1,000 characters instead of 300.

## Why it is worded this way

**Each candidate on its own.** The previous prompt asked the judge to pick one of ~60, which
leaned on comparison ("the closest count wins"). D33 asks whether each candidate *is* the trial,
so a near-miss is judged against the excerpt, not against its neighbours. Ten candidates share one
call to keep the run within the account's usage budget (mode B); the rule above tells the judge
not to let them influence each other.

**The excerpt stays in.** Without it the question becomes "does this trial support this
approval?", and every same-drug, same-disease phase 3 answers Yes.

**No is the safe answer.** As before, a wrong Yes fabricates an edge that nothing downstream can
tell from a real one.
