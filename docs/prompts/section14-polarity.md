# Prompt — section 14 trial polarity

**Version:** 2 · **Created:** 2026-09-11 · *(v1 asked for four answers; v2 is binary)*

The judge prompt. One judgement = one `(nct_id, application, segment)` unit.
Substitute `{{text}}` and `{{nct_id}}`; send nothing else.

Changing this file changes what the stored verdicts mean, so **bump the version
and record it in each verdict** rather than editing in place — otherwise a
verdict written under v1 and one written under v2 are indistinguishable in
`adjudication/verdicts.jsonl`.

---

## The prompt

```text
Below is an excerpt from Section 14 (Clinical Studies) of an FDA-approved drug
label.

<text>
{{text}}
</text>

Target trial id: {{nct_id}}

Question: in this text, is the target trial presented as evidence SUPPORTING
this drug's effectiveness or approval?

Rules:
- Judge ONLY from the text above. Do not use any outside knowledge about this
  drug, this trial, or this disease. If the text does not say it, it is not
  evidence.
- Judge ONLY the target trial id. The text usually describes several trials;
  ignore what it says about the others.
- Answer SUPPORTING if the text presents this trial as demonstrating efficacy,
  establishing effectiveness, or supporting an indication.
- Answer NOT_SUPPORTING for anything else — the trial failed to show a benefit,
  did not meet its endpoint, is cited as evidence of lack of efficacy, is
  mentioned only for safety, pharmacokinetics, dosing or study design, or the
  text gives no basis to judge.

Output exactly one word on a single line, with no explanation, no punctuation
and no preamble:

SUPPORTING
NOT_SUPPORTING
```

---

## Why it is worded this way

**"Judge ONLY from the text above."** The whole point is to read what the FDA
wrote, not what a model remembers about the drug. A judge that knows
pembrolizumab works will say SUPPORTING regardless of what the excerpt says,
and we would learn nothing. This sentence is the instruction the user asked for
explicitly.

**"Judge ONLY the target trial id."** The median excerpt is 5,519 characters and
describes several trials. The failure this prevents is reading a neighbouring
trial's result and attributing it to the target — which is exactly how the
regex-based attempt at this went wrong before an agent was involved.

**Two answers, by decision (2026-09-11).** The deliverable asks one question —
did this trial support this approval — so the judge answers that and nothing
else. v1 also offered `NEGATIVE` and `UNCLEAR`; both meant "not supporting" for
every downstream purpose, and a judge given more options spends effort choosing
between them. v1 verdicts remain readable and are mapped to `not_supporting`.

`UNCLEAR` is gone because it was never the agent's job: a unit exists only
*because* the regex found the target id in that segment, so "the id is not in
the text" is checkable in Python and is a bug in extraction, not a judgement.

**One word, no explanation.** The output is parsed by `service/adjudication.py`
with a strict match. Reasoning text would have to be parsed out, and a judge
that explains itself tends to argue itself into a conclusion.

## Worked examples

These three are the calibration set. A prompt revision that changes any of these
answers needs re-checking by hand before a bulk run.

| excerpt, abridged | target | correct answer |
|---|---|---|
| `14.4 Lack of Efficacy in Cardiovascular Outcomes Trials — Fenofibrate did not reduce cardiovascular disease morbidity or mortality… The ACCORD Lipid (NCT00000620) trial was a randomized placebo-controlled trial of 5,518 patients…` | NCT00000620 | **NOT_SUPPORTING** |
| `Use of PHESGO for this indication is supported by evidence from adequate and well-controlled studies… (NCT00545688, NCT00976989, NCT02132949, NCT01358877)` | NCT00545688 | **SUPPORTING** |
| `The efficacy of KEYTRUDA was investigated in KEYNOTE-006 (NCT01866319), a randomized (1:1:1), open-label, multicenter, active-controlled trial in 834 patients…` | NCT01866319 | **SUPPORTING** |
