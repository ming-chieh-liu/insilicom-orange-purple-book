# Prompt — identify the registered trial a label describes

**Version:** 1 · **Created:** 2026-09-16

The judge prompt. One judgement = one `(submission, trial)` unit from
`output/candidates.jsonl`. Substitute `{{excerpt}}`, `{{acronym}}`,
`{{enrollment}}` and `{{candidates}}`; send nothing else.

Changing this file changes what the stored verdicts mean, so **bump the version
and record it in each verdict** rather than editing in place.

---

## The prompt

```text
Below is an excerpt from Section 14 (Clinical Studies) of an FDA-approved drug
label. It describes one clinical trial.

<excerpt>
{{excerpt}}
</excerpt>

The label calls this trial: {{acronym}}
The label says it enrolled: {{enrollment}} patients

Below is a numbered list of registered studies. Exactly one of them may be the
trial the excerpt describes, or none of them may be.

<candidates>
{{candidates}}
</candidates>

Question: which candidate is the trial the excerpt describes?

Rules:
- Judge ONLY from the excerpt and the candidate list above. Do not use outside
  knowledge about this drug, this trial or this disease. Do not look anything
  up. Do not search the web or read any file.
- The strongest single signal is the enrollment count. A candidate whose
  enrollment matches the number the label states is very likely the trial. A
  candidate whose enrollment is far from it is very likely not.
- The trial name is the next strongest signal: a candidate whose acronym or
  sponsor protocol id matches the name the label uses is likely the trial.
- Then compare the disease, the treatment, and the study design words
  (randomized, double-blind, placebo-controlled, open-label, single-arm).
- Counts need not match exactly. A label may report the number analysed where
  the registry reports the number enrolled, so a small difference is normal and
  a large one is not.
- Answer NONE if no candidate fits, if two or more fit equally well, or if the
  excerpt does not give you enough to tell them apart. NONE is a correct and
  expected answer. A wrong match invents a link between a trial and a drug
  approval that does not exist, which is far worse than no link at all.

Output exactly one line, with no explanation, no punctuation and no preamble —
either the NCT id of the one candidate you chose, or the word NONE:

NCT01234567
NONE
```

---

## Why it is worded this way

**"Judge ONLY from the excerpt and the candidate list."** Same reason as the
polarity prompt: the point is to read what the FDA wrote against what the
registry recorded. A judge drawing on memory of KEYNOTE-189 would answer
correctly for the famous trials and tell us nothing about the other 6,000.

**"Do not look anything up."** The subagent running this has file and shell
access, because it reads its own units from disk. That access makes the
instruction load-bearing rather than decorative.

**Enrollment named as the strongest signal.** Measured: across five drug and
condition candidate sets spanning oncology and non-oncology — pembrolizumab,
ramucirumab, semaglutide, nivolumab, apixaban — every candidate had a distinct
patient count, with no collisions anywhere. Within a shortlist already narrowed
by drug, condition and date, the count is close to an identifier. Saying so
directs the judge at the field that actually discriminates instead of letting it
weigh prose similarity, which does not.

**"Counts need not match exactly."** Section 14 often reports the efficacy or
ITT population where the registry reports everyone enrolled. Demanding an exact
match would turn the strongest signal into a source of false NONEs.

**NONE is blameless, and said so explicitly.** This is the difference between
this task and the polarity one. A wrong polarity mislabels a link that exists; a
wrong identification **fabricates an edge** — it asserts that a drug was
approved on the evidence of a trial that had nothing to do with it. The
asymmetry is real, so the prompt states it in those words rather than leaving
the judge to infer that abstaining is acceptable.

**"if two or more fit equally well".** Without this a judge picks the first
plausible one. The shortlist is deliberately generous — the median is 25
candidates and the tail runs long — so ties are expected and must resolve to
NONE rather than to a guess.

**One line, no explanation.** Parsed with a strict match by
`service/identification.py`. A judge that explains itself argues itself into a
conclusion.

## Worked examples

The calibration set. A prompt revision that changes any of these answers needs
re-checking by hand before a bulk run.

| excerpt, abridged | label says | candidates, abridged | correct answer |
|---|---|---|---|
| `The efficacy of KEYTRUDA in combination with pemetrexed and platinum chemotherapy was investigated in KEYNOTE-189, a randomized, multicenter, double-blind, active-controlled trial conducted in 616 patients with metastatic nonsquamous NSCLC…` | KEYNOTE-189, 616 | `NCT02578680 n=616 PHASE3 "Study of Pembrolizumab + Chemo in NSCLC"` · `NCT02775435 n=559 PHASE3` · 14 others | **NCT02578680** |
| `Efficacy was evaluated in a randomized, double-blind trial in patients with hypertension.` | *(no name)*, *(no count)* | eleven PHASE3 hypertension studies, enrollments 120–4,400 | **NONE** |
| `DRUGX was studied in a randomized trial of 240 patients.` | *(no name)*, 240 | `NCT00000001 n=240` · `NCT00000002 n=239` · `NCT00000003 n=241` | **NONE** — three fit equally |
