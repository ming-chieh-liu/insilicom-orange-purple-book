#!/bin/bash
# S9 pilot: rounds until every pilot unit is decided. Specs: case:limit
cd /data/mliu/insilicom-orange-purple-book
P=/data/mliu/miniforge3/envs/clinical-trials-claude/bin/python
ADJ=adjudication; BR=$ADJ/candidate_match_batches; RAW=$ADJ/candidate_match_raw
SPECS="B C D"
for iter in $(seq 1 40); do
  echo "=== iteration $iter $(date +%T)"
  before=$(ls -d $BR/round-* 2>/dev/null | sort)
  for spec in $SPECS; do $P main.py --mode match-prepare --case $spec | grep -E "needing|awaiting|batch files"; done
  after=$(ls -d $BR/round-* 2>/dev/null | sort)
  new=$(comm -13 <(echo "$before") <(echo "$after"))
  [ -z "$new" ] && { echo "no new batches - done"; break; }
  R=$BR/full-$(printf %02d $iter); mkdir -p $R $RAW/full-$(printf %02d $iter)
  cat $(for d in $new; do ls $d/*.ids; done) > $R/all.ids
  echo "tiers this round: $(wc -l < $R/all.ids)"
  rm -f $R/all.requests-x5.jsonl
  $P main.py --mode render-prompts --task match --per-job 5 --batch $R/all.ids | tail -1
  [ -s $R/all.requests-x5.jsonl ] || { echo RENDER-FAILED; exit 1; }
  for J in A B; do
    $P main.py --mode pool-judge --judge $J --requests $R/all.requests-x5.jsonl \
       --raw-file $RAW/full-$(printf %02d $iter)/all-$J.txt --max-in-flight 16 > $R/pool-$J.log 2>&1 &
  done; wait
  grep -hE "sent this run|answered \(|again|still|cost" $R/pool-A.log $R/pool-B.log
  if grep -q "not sent" $R/pool-A.log $R/pool-B.log; then echo "POOL BUSY - waiting 10 min"; sleep 600; fi
  $P main.py --mode match-combine | grep -vE "^INFO"
  if [ -s $ADJ/candidate_match_tiebreak_needed.txt ]; then
    cp $ADJ/candidate_match_tiebreak_needed.txt $R/tiebreak.ids
    rm -f $R/tiebreak.requests-x5.jsonl
    $P main.py --mode render-prompts --task match --per-job 5 --batch $R/tiebreak.ids | tail -1
    [ -s $R/tiebreak.requests-x5.jsonl ] || { echo RENDER-FAILED; exit 1; }
    $P main.py --mode pool-judge --judge C --requests $R/tiebreak.requests-x5.jsonl \
       --raw-file $RAW/full-$(printf %02d $iter)/tiebreak-C.txt --max-in-flight 16 | grep -E "answered \(|still"
    $P main.py --mode match-combine | grep -vE "^INFO"
  fi
  for spec in $SPECS; do $P main.py --mode match-record --case $spec | grep -E "decided|identified|NONE|still|refused"; done
done
echo PILOT-DONE $(date +%T)
