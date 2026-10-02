# Prompt template — role of a trial an FDA document names (D46)

**Template:** role of one identified trial in one approval · **Created:** 2026-10-02 (D46) · **Runbooks:** `docs/runbooks/review-trial-role.md`, `docs/runbooks/label-trial-role.md`

One template for every trial an FDA document names by an exact id: a review's protocol id (S6, D26) and a label's
stated NCT id (S6, D46). The question, the six roles, the rules and the output format are shared; ten
`[[slot]]` lines name the document and give its examples. Each rendering keeps its own name and version, and
`tests/test_prompt_render.py` pins each rendering's SHA-256, so the review rendering stays byte-identical to
review v2 (its 4,825 verdicts stay valid) and the label rendering to label v1. Changing a slot or a shared line
changes a rendering: bump that rendering's version below and its pinned digest.

## Renderings

| name | document | version |
|---|---|---|
| review-trial-role | review | 2 |
| label-trial-role | label | 2 |

Label v2 (2026-10-02): wording unchanged (same SHA-256 as v1); the version marks new unit content — the
target is named by its NCT id, with S4's trial name only where the label writes it right before the id,
and overlapping passages are merged. No v1 label verdicts were ever recorded.

`{{…}}` fields are filled per unit: `{{approval}}`, `{{text}}`, and `{{protocol_id}}` (review) or `{{target}}` (label).

## The template

```text
[[opening]]

APPROVAL BEING JUDGED:
{{approval}}

[[target_line]]

[[text_intro]]
<text>
{{text}}
</text>

[[question]]

OUTPUT_OPTIONS:
[[pivotal]]
SUPPORTING             - contributes efficacy evidence to THIS approval but is not
[[supporting]]
PK_CLINPHARM           - a pharmacokinetic, pharmacodynamic, bioavailability,
                         bioequivalence, food-effect, drug-interaction or
                         organ-impairment study.
[[not_for_this_approval]]
FAILED                 - presented as failing its primary endpoint or as showing
[[failed]]
OTHER                  - anything else that is not one of the cases above:
[[other]]

RULES:
- Judge ONLY from the TEXT and the APPROVAL BEING JUDGED above. Do not use outside
  knowledge of this drug, study or disease. Do not open files, search, or look
  anything up.
[[target_rule]]
- If the text supports more than one option, choose the one that best describes
  the study's role in THIS approval.

Output exactly one option from OUTPUT_OPTIONS, on a single line, with no
explanation, no punctuation and no preamble.
```

## Slots

### opening

#### review

```text
You are classifying how an FDA review document uses one clinical study.
```

#### label

```text
You are classifying how an FDA drug label's CLINICAL STUDIES section (section 14)
uses one clinical study.
```

### target_line

#### review

```text
TARGET PROTOCOL ID: {{protocol_id}}
```

#### label

```text
TARGET STUDY: {{target}}
```

### text_intro

#### review

```text
TEXT (every passage of the review that mentions the target protocol id, each
with the section heading it sits under and the document it came from):
```

#### label

```text
TEXT (every passage of this approval's own label section 14 that mentions the
target study, each with the subsection it sits under):
```

### question

#### review

```text
QUESTION: what role does the study with the TARGET PROTOCOL ID play in the
APPROVAL BEING JUDGED?
```

#### label

```text
QUESTION: what role does the TARGET STUDY play in the APPROVAL BEING JUDGED?
```

### pivotal

#### review

```text
PIVOTAL                - a primary basis of THIS approval: the review presents it
                         as establishing effectiveness for what this approval
                         provides for (e.g. "pivotal", "primary evidence",
                         "adequate and well-controlled", the main efficacy trial).
```

#### label

```text
PIVOTAL                - a primary basis of THIS approval: the label presents it as
                         establishing effectiveness for what this approval provides
                         for (e.g. the randomized controlled trial whose results
                         the subsection reports for that indication).
```

### supporting

#### review

```text
                         its main basis (e.g. dose-finding, supportive phase 2,
                         extension study, additional population or endpoint).
```

#### label

```text
                         its main basis (e.g. a second or supportive trial, a
                         dose-finding or extension study, an additional population
                         or endpoint).
```

### not_for_this_approval

#### review

```text
NOT_FOR_THIS_APPROVAL  - named but not evidence for THIS approval: it supported an
                         EARLIER approval (for example the original indication,
                         when this approval adds a new one), or it is ongoing,
                         a confirmatory or postmarketing study, or required
                         after approval.
```

#### label

```text
NOT_FOR_THIS_APPROVAL  - described but not evidence for THIS approval: it supports
                         a DIFFERENT indication or an EARLIER approval that the
                         label still carries (section 14 keeps the trials of every
                         earlier indication), or it is ongoing, confirmatory or
                         postmarketing.
```

### failed

#### review

```text
                         no benefit.
```

#### label

```text
                         no benefit (e.g. under a "lack of efficacy" heading).
```

### other

#### review

```text
                         safety-only, design or background mention, another
                         product's study, or the text gives no basis to judge.
                         A study that supported an earlier approval is
                         NOT_FOR_THIS_APPROVAL, not OTHER.
```

#### label

```text
                         safety-only or long-term follow-up, design or background
                         mention, another product's study, or the text gives no
                         basis to judge. A study that supports a different or
                         earlier indication is NOT_FOR_THIS_APPROVAL, not OTHER.
```

### target_rule

#### review

```text
- Judge ONLY the TARGET PROTOCOL ID. Other studies in the text are context, not
  the answer.
```

#### label

```text
- Judge ONLY the TARGET STUDY. Other studies in the text are context, not the
  answer.
- If the approval line gives no indication ("approval letter not available"),
  judge whether the text presents the study as efficacy evidence at all:
  PIVOTAL or SUPPORTING if it does, otherwise the option that fits.
```
