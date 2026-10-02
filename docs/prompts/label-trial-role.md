# Prompt — role of a trial in its own submission's label (S6, D46)

**Version:** 2 (2026-10-02; v1 2026-09-25, never used to judge) · **Created:** 2026-09-25 · **Runbook:** `docs/runbooks/label-trial-role.md`

The judge prompt. One judgement = one `(application, label-stated NCT)` at the earliest submission
whose label states it (D16, D46), built from S4's `label_trials.tsv` and `label_section14.jsonl.gz`.
Substitute the three `{{…}}` fields; send nothing else.

**v2 (2026-10-02)** changes the unit content, not the wording (same SHA-256): the target is named by
its NCT id, S4's trial name is shown and searched only where the label writes it right before the
id (a figure caption had given NCT00087711, Study JMDB, the name KEYNOTE-189), the patient count is
searched only where the id's own sentence writes it, and overlapping passages are merged.

Changing the wording changes what stored verdicts mean: **bump the version** and record it on every
verdict rather than editing in place.

The six options are **the same as the review route's** (`docs/prompts/review-trial-role.md` v2), on
purpose: P5 compares the two answers for the same `(submission, trial)` and flags
`role_disagrees` when one links and the other does not. The option wording differs only where a
label differs from a review (a label has no "ongoing, required after approval" section, but does
carry trials behind earlier indications because section 14 is cumulative).

---

## The prompt

**Rendered from `fda-document-trial-role.md`** (document = `label`, D46, 2026-10-02). The text
is no longer kept here, so the two role prompts cannot drift apart; the rendering is pinned
by SHA-256 in `tests/test_prompt_render.py` and is byte-identical to the text this version was
judged with.


---

## Field contents

| Field | Built from |
|---|---|
| `{{approval}}` | `<brand names> · <application> <type> <number> · approved <date> · <letter "provides for …" sentence>`, or `approval letter not available` — built by `label_role.approval_line`, the same shape as the review route |
| `{{target}}` | `<name> (registry id NCT…)` where the label writes the name right before the id (`label_role.name_beside_id`), else `the study registered as NCT…` |
| `{{text}}` | every mention in that submission's label §14 of the registry id, the verified name, or the patient count where the id's sentence states it; ±700 characters, overlapping windows in one subsection merged, at most 8 (those holding the id first), in reading order, each prefixed `[<14.N> <subsection heading>]` |

The registry record is **not** shown: the label states the id, and showing the record would let a
judge answer from the registry instead of from the FDA's text.

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
