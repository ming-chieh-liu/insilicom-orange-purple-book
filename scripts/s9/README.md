# S9 run drivers (agent pool)

Shell drivers used for the S9 full run (2026-10-03 → 10-05). Each round:
`match-prepare` (B, C, D) → `render-prompts --per-job 5` → `pool-judge` A and B in
parallel → `match-combine` → third judge on disagreements → `match-record`, until
`match-prepare` writes no batch. Failed jobs record nothing and are re-batched.
See `docs/runbooks/trial-candidate-match.md`.

- `s9full.sh` — the driver as first run (rounds `full-NN`).
- `s9full_v2.sh` — restart driver: 200 rounds, waits 20 min when most jobs fail
  for lack of pool seats, round folders `fullr-<tag>-NN`.
- `s9watchdog.sh` — restarts the driver if it stops before "no new batches - done".

Run from the repository root; logs go to `logs/` (gitignored).
