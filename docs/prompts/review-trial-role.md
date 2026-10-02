# Prompt — role of a trial named in an FDA review

**Version:** 2 · **Created:** 2026-09-24 · **v2:** 2026-09-25 · **Runbook:** `docs/runbooks/review-trial-role.md`

*v1 → v2 (2026-09-25): the pilot's one calibration miss was the SIMPLE trial (`GS-US-540-5773`),
evidence for Veklury's 2020 approval named in the review of a 2022 supplement — expected
NOT_FOR_THIS_APPROVAL, answered OTHER, because v1's OTHER ("anything else … background mention")
read as a fit for a recap. v2 puts "supported an earlier approval" first in NOT_FOR_THIS_APPROVAL
with an example, and says in OTHER that it does not cover those cases. Nothing else changed.
Verdicts record `prompt_version`; v1 and v2 answers are kept in separate folders.*

The judge prompt. One judgement = one `(submission, nct_id)` unit, built only where a protocol id
in an FDA review document matched exactly one registered trial and passed every deterministic
guard (runbook step 4). Substitute the four `{{…}}` fields; send nothing else.

Changing the wording changes what stored verdicts mean: **bump the version** and record it on every
verdict rather than editing in place.

---

## The prompt

**Rendered from `fda-document-trial-role.md`** (document = `review`, D46, 2026-10-02). The text
is no longer kept here, so the two role prompts cannot drift apart; the rendering is pinned
by SHA-256 in `tests/test_prompt_render.py` and is byte-identical to the text this version was
judged with.


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
