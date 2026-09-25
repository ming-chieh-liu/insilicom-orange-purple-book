# Prompt — is any of these literature trials the evidence for this approval?

**Version:** 2 · **Created:** 2026-09-16 · **v2:** 2026-09-25

*v1 → v2 (2026-09-25, D31): units now come in two bases. `indication` units are
unchanged in meaning. `drug_only` units exist for approvals with no indication
at all; their candidates come from every disease the drug is reported to treat
(negative-correlation edges), each candidate shows the disease(s) it was found
under, and every candidate already completed its primary endpoint before the
approval. v2 adds the `{{indication}}` wording for that case, the "found under"
field and one rule. No v1 verdicts were recorded, so none are invalidated.*

The tier-C judge prompt. One judgement = one `(submission, graph edge)` unit
from `adjudication/graph_units.jsonl`. Substitute `{{drug}}`, `{{indication}}`,
`{{approval_date}}` and `{{candidates}}`; send nothing else.

| Field | `basis = indication` | `basis = drug_only` |
|---|---|---|
| `{{drug}}` | the unit's `drug` | the unit's `drug` |
| `{{indication}}` | the unit's `indication_term` | `not recorded — candidates come from the literature on every disease this drug is reported to treat` |
| `{{candidates}}` | one line per candidate: NCT id, enrollment, phases, start date, mentions, trial report yes/no, example title | the same, plus `found under: <diseases>` and the primary completion date |

Changing this file changes what the stored verdicts mean, so **bump the version
and record it in each verdict** rather than editing in place.

---

## The prompt

```text
An FDA approval was granted on the date below. No drug label we can read names
the trial behind it — the label predates the numbered Clinical Studies format,
or its Clinical Studies section names no trial at all.

Approved drug:      {{drug}}
Approved for:       {{indication}}
Date of approval:   {{approval_date}}

Below are registered studies mentioned in the published literature about this
drug and this disease. They were found by following citations, not by reading
any label, so most of them are NOT the trial that supported this approval.
Expect reviews, case reports, observational registries, studies of other drugs,
and studies run years after the approval.

<candidates>
{{candidates}}
</candidates>

Question: is any candidate the clinical trial that supported this approval?

Rules:
- Judge ONLY from what is shown above. Do not use outside knowledge about this
  drug, this trial or this disease. Do not look anything up. Do not search the
  web or read any file.
- A trial that supported an approval was an interventional efficacy study of
  this drug in this disease, completed before the approval date.
- Say NONE if the candidates are observational registries, post-marketing
  surveillance, reviews, case reports, studies of a different drug, studies in
  a different disease, or trials that could not have finished before the
  approval date.
- An enrollment in the tens of thousands is almost always a registry or an
  observational cohort, not a pivotal trial.
- "Mentioned by several papers" means well cited, not pivotal. Do not treat it
  as evidence of anything except that the trial exists.
- If two or more candidates fit equally well, answer NONE. A regulatory
  approval usually rests on one or two named trials, and if you cannot tell
  which, you do not know.
- If "Approved for" says not recorded, the approval could be for any disease a
  candidate was found under. Choose a candidate only if it is an
  interventional efficacy study of this drug that could have supported a first
  approval before the date; if candidates in different diseases fit equally,
  answer NONE.
- NONE is the expected answer for most units and is never a failure. A wrong
  match invents a link between a trial and a drug approval that does not exist.
  Nothing downstream can tell a fabricated link from a real one.

Output exactly one line, with no explanation, no punctuation and no preamble —
either the NCT id of the one candidate you chose, or the word NONE:

NCT01234567
NONE
```

---

## Why it is worded this way

**The candidate list is described honestly, up front.** Unlike P4, where the
shortlist was built to contain the answer, this one was built by following
literature citations and mostly does not. Telling the judge that is not a hint
— it is the truth about the input, and withholding it would invite the judge to
assume one candidate must be right.

**No label paragraph to compare against.** This is the structural difference
from P4 and the reason this tier exists. P4 asks "which candidate matches this
description?" and has a description. Here the question is the weaker "is any of
these plausibly the evidence?", answered from the drug, the disease, the date
and the candidates' own metadata. A weaker question deserves a lower tier.

**Enrollment in the tens of thousands flags a registry.** Measured on the first
pilot: the Turner Syndrome unit offered two candidates enrolling 21,249 and
22,960, both from one paper about observational registries. Without this rule a
judge weighing "large study, right disease, right drug, before the date" has
every reason to pick one.

**"Mentioned by several papers" is explicitly disarmed.** The unit carries a
mention count because it orders the list, and a judge shown a number will use
it. Citation frequency measures interest, not pivotality — a famous failed
trial is cited more than a quiet successful one.

**NONE is called the expected answer.** In P4 the shortlist was engineered to
contain the answer, so a high NONE rate signalled a problem. Here it signals
the tool working. Saying so prevents a judge from treating abstention as
failure and reaching for the least-bad option.

## Worked examples

The calibration set. A revision that changes any of these needs re-checking by
hand before a bulk run.

| drug, indication, date | candidates, abridged | correct answer |
|---|---|---|
| somatropin · Turner Syndrome · 2006-11-01 | `NCT00960128 n=21,249 observational registry` · `NCT01009905 n=22,960 same paper` | **NONE** — registries, not trials |
| a drug · its disease · 2004-05-01 | `NCT00000123 PHASE3 n=540 randomized, started 2001, trial report` and nothing else comparable | **NCT00000123** |
| a drug · its disease · 2001-03-01 | `NCT00300000 PHASE3 started 2009` · `NCT00400000 PHASE3 started 2011` | **NONE** — both postdate the approval |
