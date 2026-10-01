# Prompt — is this literature-named trial the evidence for an approval? (per-candidate Yes/No)

**Version:** 1 · **Created:** 2026-09-30 · **In code:** 2026-10-01 (`candidate_match.LITERATURE_PROMPT`) ·
**Decision:** D37 · **Runbook:** `docs/runbooks/trial-candidate-match.md` (to be extended)

The literature routes' prompt: **B+** (a §14 description), **C+** (an indication, no §14
description) and **D** (no indication). Every candidate here was found because published
abstracts name it — by NCT id, or by a sponsor protocol id matching exactly one registry study —
in literature linking this drug to this disease (or, for D, to anything it treats). Its
identity is therefore exact; the open question is whether the abstracts show it tested *this*
drug for *this* indication, which is why they are shown. B−/C− use
`docs/prompts/trial-candidate-match.md` / `-nolabel.md` (v2), whose candidates come from the
registry alone.

Before any candidate reaches the judge (D37): trials that never ran (WITHDRAWN,
NOT_YET_RECRUITING) are excluded; a trial whose every non-placebo arm resolves to another FDA
drug is excluded; the indication check (MeSH tree, id-less kept) applies to B+ and C+.

Changing this file changes what stored answers mean: **bump the version**.

---

## The prompt

```text
You are checking, one by one, whether registered clinical trials are the trial
whose results support an FDA approval. Each candidate was found because
published abstracts name it; those abstracts are shown with it.

APPROVAL:
{{approval}}

WHAT THE FDA DOCUMENTS SAY ABOUT THIS APPROVAL:
{{label_block}}

CANDIDATE TRIALS (from ClinicalTrials.gov, each with the abstracts that name it):
<candidates>
{{candidates}}
</candidates>

QUESTION: for EACH candidate, is it the trial whose results support this
approval?

RULES:
- Judge each candidate ON ITS OWN, as if it were the only one shown. Do not
  compare candidates with each other, rank them, or choose between them: two
  or more candidates may each be Yes, and all of them may be No.
- Judge ONLY from the text above. Do not use outside knowledge of this drug,
  trial or disease. Do not look anything up.
- Use the abstracts to establish what the trial tested. Answer Yes only if they
  show it tested THE APPROVED DRUG — under any of its names or code names — as
  the treatment being studied (not as a comparator or background therapy), in
  the approved disease.
- A review, meta-analysis or indirect comparison that merely mentions the trial
  does not show that; judge from what it says the trial itself tested.
- When a trial description is given above, the candidate must also match it:
  the design, the comparator, the population and the size (patients analysed
  and patients enrolled may differ a little).
- The trial must have started before the approval date. The abstracts may be
  published later.
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
| `{{approval}}` | `<brand> (<generic>) · <application> <submission type> <number> · approved <date>` — the drug from P1's `drug_names.tsv` |
| `{{label_block}}` | **B+:** `Section 14 (Clinical Studies) of this approval's label describes the trial:` + the §14 subsection (up to 3,000 characters), then `A program read its name as: <name>` and `and its size as: <count> patients`, each marked "(may be wrong)" · **C+:** `No description of the trial is available. Approved for: <indication text>` (prefixed as in `-nolabel.md` when it is today's label) · **D:** `No description of the trial is available. Approved for: not recorded.` |
| `{{candidates}}` | one block per candidate: the registry fields of `trial-candidate-match.md` v2 (status and why stopped included), then `Abstracts naming this trial (<n> found, up to 3 shown):` and for each: `PMID <pmid> — <title>` and the abstract; trial reports (publication type clinical trial / RCT) first, then by PMID. **D** adds `Named in literature on: <diseases>` |

## Why it is worded this way

**The abstract replaces the drug-identity guesswork.** A literature candidate's registry record
often names the drug only by a code (`BGB-3111`, `DS-1062a`) that no dictionary resolves; the
abstract says "zanubrutinib (BGB-3111) versus ibrutinib". Without it, half these candidates
cannot be judged; with it, a comparator-only mention (AMPLIFY in a zanubrutinib review) is a No
the judge can see.

**"Merely mentions" is called out** because the literature walk finds trials through *any*
abstract on the drug and disease, including reviews and indirect comparisons that cite other
drugs' trials.
