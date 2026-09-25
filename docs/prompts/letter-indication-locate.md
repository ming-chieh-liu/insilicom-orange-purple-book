# Prompt — locate the indication statement in an approval letter

**Version:** 1 · **Created:** 2026-09-24 · **Runbook:** `docs/runbooks/letter-indication.md`

Step 2 of the letter-indication task. One judgement = one approval letter whose indication the
keyword rule (`service/letters.py`, `INDICATION_BEARING`) could not find. The letter is shown split
into numbered segments; the judge names segments, it never writes text. Code then copies the named
segments verbatim (step 3), so a judge cannot invent an indication — a segment id that does not
exist is rejected. Substitute `{{approval}}` and `{{segments}}`; send nothing else.

Changing the wording changes what stored answers mean: **bump the version** and record it on every
answer.

---

## The prompt

```text
You are reading an FDA approval letter to find where it states what the approval is FOR.

APPROVAL BEING JUDGED:
{{approval}}

TEXT (the letter, split into numbered segments):
<text>
{{segments}}
</text>

QUESTION: which segments state the indication of THIS approval — the disease,
condition or purpose the drug is approved for as a result of this approval,
with its patient population if the letter gives one?

OUTPUT_OPTIONS:
- the ids of the segments that state it, comma-separated, in order (for example: S4,S5)
- NONE

RULES:
- Judge ONLY from the TEXT above. Do not use outside knowledge of this drug.
  Do not open files, search, or look anything up.
- Name only segment ids that appear in the TEXT. Do not write or rephrase any text.
- Include every segment the statement needs to be complete (for example a
  sentence and the bullet points that continue it), and no others.
- Answer NONE if the letter states no indication: for example it approves only
  labeling, safety or warning changes, a new strength, device, container or
  manufacturing change, a dosing change, biosimilar interchangeability, or
  editorial updates. NONE is a correct and expected answer.
- Do not choose segments that only mention an indication in passing (for
  example a postmarketing requirement "for the X indication") unless they state
  what this approval provides for.

Output exactly one line, with no explanation and no preamble: the segment ids,
or the word NONE.
```

---

## Field contents

| Field | Built from |
|---|---|
| `{{approval}}` | `<brand names> · <application> <submission type> <number> · approved <date>` — no extracted sentence, no expected answer |
| `{{segments}}` | the letter's text split at sentence ends and bullet points, one per line, `[S1] …`, `[S2] …` |

## Why it is worded this way

**Segment ids, not text.** The user's requirement: the agent locates, code extracts. An id either
exists or it does not, so a fabricated indication cannot enter the table.

**NONE is expected.** Most letters the keyword rule rejected are genuinely not indication letters
(labeling alignment, devices, interchangeability, survival-data updates — 13 of 15 in the
2026-09-24 sample), so a judge that feels obliged to find something would do harm.
