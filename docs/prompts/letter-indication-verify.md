# Prompt — verify an extracted letter indication

**Version:** 1 · **Created:** 2026-09-24 · **Runbook:** `docs/runbooks/letter-indication.md`

Step 4 of the letter-indication task. One judgement = one span of letter text that two locator
judges pointed at and code copied verbatim. The verifier sees **only** that span and which approval
it belongs to — not the rest of the letter and not the locators' answers — and says whether it
states the approval's indication. Substitute `{{approval}}` and `{{text}}`; send nothing else.

Changing the wording changes what stored answers mean: **bump the version** and record it on every
answer.

---

## The prompt

```text
You are checking whether a passage from an FDA approval letter states what the
approval is FOR.

APPROVAL BEING JUDGED:
{{approval}}

TEXT (copied verbatim from the approval letter):
<text>
{{text}}
</text>

QUESTION: does the TEXT state the indication of THIS approval — a disease,
condition or purpose the drug is approved for, with or without its patient
population?

OUTPUT_OPTIONS:
STATES_INDICATION  - the text names a disease, condition or purpose that this
                     approval covers: a new indication, an extended or new
                     patient population for an indication, or the indication(s)
                     of an original approval.
NOT_INDICATION     - the text is about something else: labeling sections,
                     warnings or safety wording, dosing, strengths, devices,
                     containers, manufacturing, pharmacokinetics,
                     interchangeability, data updates; or it names no disease,
                     condition or purpose.

RULES:
- Judge ONLY from the TEXT above. Do not use outside knowledge of this drug.
  Do not open files, search, or look anything up.
- Mentioning a disease is not enough: the text must say the approval is FOR it.

Output exactly one option from OUTPUT_OPTIONS, on a single line, with no
explanation, no punctuation and no preamble.
```

---

## Why it is worded this way

**The verifier never sees the whole letter.** It judges the extracted span on its own, so it checks
what code will actually store, and it cannot be swayed by the part of the letter the locators
ignored.

**"Mentioning a disease is not enough."** The keyword-rule sample's two errors were exactly this:
a disease named inside a data update ("… for the cutaneous squamous cell carcinoma indication") and
inside a dosing change.
