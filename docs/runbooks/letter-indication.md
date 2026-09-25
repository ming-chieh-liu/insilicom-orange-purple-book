# Runbook — agent-located indications in approval letters

**For:** the main agent · **Prompts:** `docs/prompts/letter-indication-locate.md` (v1),
`docs/prompts/letter-indication-verify.md` (v1) · **Written:** 2026-09-24 · **Status:** wired (`letter-locate-prepare`, `letter-indication-combine`); pilot passed, full run pending the user's hand check

## What this does and why

`service/letters.py` extracts a letter's indication with a keyword rule, tuned for precision (93%
on a hand-checked sample of 30). It finds nothing for two groups: letters with no recognisable
indication sentence (`status = no_sentence`) and letters whose sentence the rule judged not to be
an indication (`indication_bearing = false`). Some of those do state an indication in a wording the
rule does not know ("proposes a new patient population: patients 12 to less than 18 years … atopic
dermatitis"). This task finds them without letting an agent write the indication:

1. **Segment** (code) — the letter text is split at sentence ends and bullet points into numbered
   segments `[S1] … [Sn]`.
2. **Locate** (agents) — two judges name the segments that state the indication, or `NONE`.
3. **Extract** (code) — the named segments are copied verbatim. An id not in the letter is refused.
4. **Verify** (agents) — two different judges see only the extracted span and the approval, and
   answer `STATES_INDICATION` or `NOT_INDICATION`.

Only a span both locators agreed on (or a tiebreak chose) **and** both verifiers (or a tiebreak)
accepted becomes the submission's indication, recorded with `source = approval_letter_located` so it
stays distinguishable from the keyword route.

## How to run it

```bash
PY=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
$PY main.py --mode letters                        # read letters (needs FDA; throttled)
$PY main.py --mode letter-locate-prepare [--limit N]
#   → adjudication/letter_locate_units.jsonl + letter_indication_batches/locate-batch-NNN.ids
#   run locators A and B per batch (prompt: letter-indication-locate.md), save answers as
#   adjudication/letter_indication_raw/locate-batch-NNN-{A,B}.txt
$PY main.py --mode letter-indication-combine
#   → pairs answers, code extracts agreed spans, writes verify batches and any tiebreak
#   batches, records decided letters; prints exactly what to run next. Run verifiers
#   (verify-batch-NNN-{A,B}.txt) and tiebreaks (…-tiebreak-batch-NNN-C.txt), then
#   re-run combine until it reports "nothing pending".
$PY main.py --mode indication-normalize           # accepted spans become source letter_located
```

`letter-locate-prepare` writes every candidate letter to the units file but batches only letters
not already in a batch file, so re-running it never judges a letter twice. `combine` is idempotent:
it re-reads every answer file and appends a verdict only when a letter's outcome is new or changed
(last row wins).

## Work units

**Locate unit** — one letter: `{"unit_id": "<application>|<type>|<number>", "approval": "…",
"segments": "[S1] …\n[S2] …"}` in `adjudication/letter_locate_units.jsonl` (rebuilt, gitignored).

**Verify unit** — one extracted span: `{"unit_id": …, "approval": …, "text": "…"}` in
`adjudication/letter_verify_units.jsonl` (rebuilt, gitignored). Built only for letters whose
located segments were agreed.

`approval` is `<brand names> · <application> <type> <number> · approved <date>`. It never carries
the keyword route's sentence or any expected answer.

## Judging rules (as `docs/runbooks/adjudicate-section-14.md`)

1. Two independent judges per unit, launched in the same message; batches of ≤ 25 unit ids.
2. The orchestrator passes unit ids only; each judge reads its own units from the units file.
3. The prompt is copied **verbatim**, with an explicit ban on any other file read, search or lookup,
   and the output format `<unit_id> = <ANSWER>`, one line per unit.
4. **Locate combine:** identical segment sets → agreed; anything else (different sets, one `NONE`)
   → a fresh third judge on that unit alone, whose answer is final. A segment id not present in the
   letter makes that answer invalid, and the unit is re-run.
5. **Verify combine:** equal answers → final; different → a fresh third judge, final.
6. Missing or unparseable answers leave the unit undecided — never guessed.
7. Raw answers: `adjudication/letter_indication_raw/<step>-batch-NNN-{A,B,C}.txt`; decisions appended
   to `adjudication/letter_indication_verdicts.jsonl` (append-only, tracked), each stamped with both
   prompt versions.

## What the report must state

Units per step · locator agreement (identical sets) and tiebreaks · share `NONE` · invalid ids ·
verifier agreement and tiebreaks · final accepted spans · for the pilot, a hand check of every
accepted span and of a sample of `NONE`s against the letter.

## Pilot

20 letters from the local cache: 10 `no_sentence`, 10 with `indication_bearing = false`. No request
is sent to FDA. The full run waits for the pilot's hand check.
