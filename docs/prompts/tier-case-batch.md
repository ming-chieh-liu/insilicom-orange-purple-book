# Prompt — several S9 tiers in one pool job

**Version:** 1 · **Created:** 2026-10-02 · **Runbook:** `docs/runbooks/trial-candidate-match.md`

A wrapper, not a new question. A pool job costs ~75 s of session start-up whatever it judges, so
one judge answers several **tiers** in one job. Each case is one tier's own prompt **verbatim** —
the `trial-candidate-match`, `-nolabel` or `trial-literature-match` rendering, byte-identical to
what a one-tier job receives — and this wrapper only says the cases are independent and how to
answer them all.

**Why several tiers can share a job.** The stop rule ("tier 2 only if tier 1 was all No") is a rule
about one unit's tiers; different units never depend on each other. S9 runs in rounds and each
round batches at most one tier per unit, so every tier in a round is independent of the others.
A job never holds two tiers of the same unit.

Jobs are packed by size: up to 5 tiers and about 100,000 characters
(`prompt_render.group_tier_requests`); a tier longer than that goes alone. Cases are numbered
1…n; the code keeps the case → tier map, so a judge never copies a tier id. The reply must answer
every candidate of every case exactly once, `Yes` or `No`, or nothing in the job is recorded and
it is sent again (`pool-judge --retries`) — `prompt_render.tier_batch_reply_lines`. Judges A, B and
C stay separate jobs; pairing, the third judge and the rounds stay in code (`match-combine`,
`match-record`).

`{{count}}` is the number of cases; `{{cases}}` is the cases, each between `=== CASE <n> ===` and
`=== END OF CASE <n> ===`.

Changing the wording bumps the version; the version is logged with every job.

## The prompt

```text
You will judge {{count}} separate cases. Each case below is a complete, independent task with its
own approval, its own description of the evidence and its own candidate trials. Judge each case
only from what is written inside that case; nothing in one case is evidence about another, even
when the drug or the trials look alike.

Each case ends by asking for one "<NCT id> = Yes" or "<NCT id> = No" line per candidate. Instead,
after reading every case, answer all of them together: for every candidate of every case, output
exactly one line in the form

CASE <n> | <NCT id> = Yes
or
CASE <n> | <NCT id> = No

using that case's own rule for Yes and No, with nothing else: no explanation, no headings, no
preamble.

{{cases}}
```
