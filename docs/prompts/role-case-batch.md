# Prompt — several role cases in one pool job

**Version:** 1 · **Created:** 2026-10-02 · **Runbooks:** `docs/runbooks/review-trial-role.md`,
`docs/runbooks/label-trial-role.md`

A wrapper, not a new question. One pool job costs ~75 s of session start-up whatever it judges
(measured 2026-09-30), so one judge answers several role units in one job. Each case is the unit's
own prompt **verbatim** — the `review-trial-role` or `label-trial-role` rendering, byte-identical
to what a one-unit job receives — and this wrapper only says that the cases are independent and
how to answer them all. Judges A, B and C stay separate jobs; pairing and tiebreaks stay in code
(`*-combine`), never in the job.

Cases are numbered 1…n in the job; the code keeps the case → unit id map, so a judge never copies
an id. The reply must answer every case exactly once with a valid option, or nothing is recorded
for the job and every case in it stays undecided (`prompt_render.role_batch_reply_lines`).

`{{count}}` is the number of cases; `{{cases}}` is the cases, each between
`=== CASE <n> ===` and `=== END OF CASE <n> ===`.

Changing the wording bumps the version; the version is logged with every job.

## The prompt

```text
You will classify {{count}} separate cases. Each case below is a complete, independent task with
its own approval, its own target study and its own text. Judge each case only from what is
written inside that case; nothing in one case is evidence about another.

Each case ends by asking for a single option on a single line. Instead, after reading every case,
answer all of them together: output exactly {{count}} lines, one per case, in the form

CASE <n> = <OPTION>

where <OPTION> is one of that case's OUTPUT_OPTIONS, with nothing else: no explanation, no
punctuation, no preamble.

{{cases}}
```
