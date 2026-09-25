# Prompt — role of a trial in its own submission's label (D28)

**Version:** 1 · **Created:** 2026-09-25 · **Runbook:** `docs/runbooks/label-trial-role.md`

The judge prompt. One judgement = one `(submission, nct_id)` label link — a trial the label route
(label-stated NCT, name matched to a review id, or a judged §14 description) attributes to this
submission after D16. Substitute the three `{{…}}` fields; send nothing else.

Changing the wording changes what stored verdicts mean: **bump the version** and record it on every
verdict rather than editing in place.

The six options are **the same as the review route's** (`docs/prompts/review-trial-role.md` v2), on
purpose: P5 compares the two answers for the same `(submission, trial)` and flags
`role_disagrees` when one links and the other does not. The option wording differs only where a
label differs from a review (a label has no "ongoing, required after approval" section, but does
carry trials behind earlier indications because section 14 is cumulative).

---

## The prompt

```text
You are classifying how an FDA drug label's CLINICAL STUDIES section (section 14)
uses one clinical study.

APPROVAL BEING JUDGED:
{{approval}}

TARGET STUDY: {{target}}

TEXT (every passage of this approval's own label section 14 that mentions the
target study, each with the subsection it sits under):
<text>
{{text}}
</text>

QUESTION: what role does the TARGET STUDY play in the APPROVAL BEING JUDGED?

OUTPUT_OPTIONS:
PIVOTAL                - a primary basis of THIS approval: the label presents it as
                         establishing effectiveness for what this approval provides
                         for (e.g. the randomized controlled trial whose results
                         the subsection reports for that indication).
SUPPORTING             - contributes efficacy evidence to THIS approval but is not
                         its main basis (e.g. a second or supportive trial, a
                         dose-finding or extension study, an additional population
                         or endpoint).
PK_CLINPHARM           - a pharmacokinetic, pharmacodynamic, bioavailability,
                         bioequivalence, food-effect, drug-interaction or
                         organ-impairment study.
NOT_FOR_THIS_APPROVAL  - described but not evidence for THIS approval: it supports
                         a DIFFERENT indication or an EARLIER approval that the
                         label still carries (section 14 keeps the trials of every
                         earlier indication), or it is ongoing, confirmatory or
                         postmarketing.
FAILED                 - presented as failing its primary endpoint or as showing
                         no benefit (e.g. under a "lack of efficacy" heading).
OTHER                  - anything else that is not one of the cases above:
                         safety-only or long-term follow-up, design or background
                         mention, another product's study, or the text gives no
                         basis to judge. A study that supports a different or
                         earlier indication is NOT_FOR_THIS_APPROVAL, not OTHER.

RULES:
- Judge ONLY from the TEXT and the APPROVAL BEING JUDGED above. Do not use outside
  knowledge of this drug, study or disease. Do not open files, search, or look
  anything up.
- Judge ONLY the TARGET STUDY. Other studies in the text are context, not the
  answer.
- If the approval line gives no indication ("approval letter not available"),
  judge whether the text presents the study as efficacy evidence at all:
  PIVOTAL or SUPPORTING if it does, otherwise the option that fits.
- If the text supports more than one option, choose the one that best describes
  the study's role in THIS approval.

Output exactly one option from OUTPUT_OPTIONS, on a single line, with no
explanation, no punctuation and no preamble.
```

---

## Field contents

| Field | Built from |
|---|---|
| `{{approval}}` | `<brand names> · <application> <type> <number> · approved <date> · <letter "provides for …" sentence>`, or `approval letter not available` — built by `label_role.approval_line`, the same shape as the review route |
| `{{target}}` | the label's trial name, or `the study of N patients` / `the study described under "<heading>"` for a trial the label does not name, plus `(registry id NCT…; the label may not print it)` |
| `{{text}}` | every mention in that submission's label §14 of the registry id, the trial name or the stated patient count, ±700 characters, at most 8, each prefixed `[<14.N> <subsection heading>]`; a trial never mentioned by name falls back to its whole subsection (≤3,000 characters) |

The registry record is **not** shown: P4 already used it to identify the trial, and showing it
would let a judge answer from the registry instead of from the FDA's text.

## Why it is worded this way

**Same six roles as the review route** so the two routes' answers are comparable (D29
`role_disagrees`). Only `PIVOTAL` and `SUPPORTING` link.

**NOT_FOR_THIS_APPROVAL names the cumulative-label case first.** Section 14 keeps every earlier
indication's trials, and D16 already attributes a trial to its earliest submission — but on that
earliest submission the label can still describe it under a different indication (combination
approvals, a trial first shown in a subsection about another use). This is the label analogue of
the review prompt's v1 → v2 fix.

**The fallback rule for a missing letter** keeps the task answerable before 1998, where there are
no letters: the question degrades to "is this efficacy evidence at all", which is still exactly
what the old polarity task asked, now on the right label.

## Calibration set

| Unit | Expected |
|---|---|
| BLA125514 SUPPL-35 (Keytruda, 2018-08-20) · KEYNOTE-189 `NCT02578680` | PIVOTAL |

The pilot adds cases found by hand (a fenofibrate label's ACCORD Lipid under "Lack of Efficacy"
→ FAILED is the target shape); each addition is recorded here with the date.
