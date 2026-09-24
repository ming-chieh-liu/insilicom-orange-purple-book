# Prompt — role of a trial named in an FDA review

**Version:** 1 · **Created:** 2026-09-24 · **Runbook:** `docs/runbooks/review-trial-role.md`

The judge prompt. One judgement = one `(submission, nct_id)` unit, built only where a protocol id
in an FDA review document matched exactly one registered trial and passed every deterministic
guard (runbook step 4). Substitute the four `{{…}}` fields; send nothing else.

Changing the wording changes what stored verdicts mean: **bump the version** and record it on every
verdict rather than editing in place.

---

## The prompt

```text
You are classifying how an FDA review document uses one clinical study.

APPROVAL BEING JUDGED:
{{approval}}

TARGET PROTOCOL ID: {{protocol_id}}

TEXT (every passage of the review that mentions the target protocol id, each
with the section heading it sits under and the document it came from):
<text>
{{text}}
</text>

QUESTION: what role does the study with the TARGET PROTOCOL ID play in the
APPROVAL BEING JUDGED?

OUTPUT_OPTIONS:
PIVOTAL                - a primary basis of THIS approval: the review presents it
                         as establishing effectiveness for what this approval
                         provides for (e.g. "pivotal", "primary evidence",
                         "adequate and well-controlled", the main efficacy trial).
SUPPORTING             - contributes efficacy evidence to THIS approval but is not
                         its main basis (e.g. dose-finding, supportive phase 2,
                         extension study, additional population or endpoint).
PK_CLINPHARM           - a pharmacokinetic, pharmacodynamic, bioavailability,
                         bioequivalence, food-effect, drug-interaction or
                         organ-impairment study.
NOT_FOR_THIS_APPROVAL  - named but not evidence for THIS approval: it supported an
                         earlier approval, is ongoing, is a confirmatory or
                         postmarketing study, or is required after approval.
FAILED                 - presented as failing its primary endpoint or as showing
                         no benefit.
OTHER                  - anything else: safety-only, design or background mention,
                         another product's study, or the text gives no basis to
                         judge.

RULES:
- Judge ONLY from the TEXT and the APPROVAL BEING JUDGED above. Do not use outside
  knowledge of this drug, study or disease. Do not open files, search, or look
  anything up.
- Judge ONLY the TARGET PROTOCOL ID. Other studies in the text are context, not
  the answer.
- If the text supports more than one option, choose the one that best describes
  the study's role in THIS approval.

Output exactly one option from OUTPUT_OPTIONS, on a single line, with no
explanation, no punctuation and no preamble.
```

---

## Field contents

| Field | Built from |
|---|---|
| `{{approval}}` | `<drug names> · <application> <submission type> <number> · approved <date>` plus the submission's approval-letter "provides for …" sentence when a letter exists; `approval letter not available` otherwise |
| `{{protocol_id}}` | the protocol id as written in the review |
| `{{text}}` | every mention of that id across the submission's review documents, ±~700 characters each, prefixed `[<doc type> · <nearest heading>]` |

The registry record (title, phase, dates) is **not** shown. The deterministic guards already
used it; showing it would let a judge answer from the registry instead of from FDA's text.

## Why it is worded this way

**Six options, two of which link.** Only `PIVOTAL` and `SUPPORTING` produce a trial → submission
row (decision 2026-09-24: keep both, report pivotal as the core answer). The other four are kept
as context rows because each is informative: `FAILED` is exactly the negative evidence the
client's prediction goal needs, and `NOT_FOR_THIS_APPROVAL` makes the D16 recap and ongoing
confirmatory classes (APHINITY, ALEX, remdesivir adult trials in a pediatric review) auditable.

**No `UNCLEAR`.** As in the section-14 prompt, "no basis to judge" folds into `OTHER`; a unit
exists only because the id was found and matched, so absence of the id is an extraction bug,
not a judgement.

**The approval sentence is the one piece of outside context allowed.** Without it the judge
cannot distinguish "evidence for this approval" from "recap of an earlier one", which is the
distinction this task exists for.

## Calibration set

Expected answers for pilot units whose role is known from the registry and the approval history.
A prompt revision that changes any of these must be re-checked by hand.

| Unit | Expected |
|---|---|
| NDA202811 ORIG-1 (Linzess) · MCP-103-302 | PIVOTAL |
| NDA214787 SUPPL-10 (Veklury, pediatric) · GS-US-540-5773 (adult trial) | NOT_FOR_THIS_APPROVAL |
| BLA125409 SUPPL-51 (Perjeta, neoadjuvant 2013) · BO25126 (APHINITY, completed 2016) | NOT_FOR_THIS_APPROVAL |
| NDA202880 ORIG-1 (Zohydro) · ZX002-0901 (alcohol interaction) | PK_CLINPHARM |
