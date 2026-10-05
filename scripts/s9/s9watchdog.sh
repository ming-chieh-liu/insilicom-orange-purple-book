#!/bin/bash
# Keeps the S9 full run going until it reports "no new batches - done".
cd /data/mliu/insilicom-orange-purple-book
while true; do
  if grep -q "no new batches - done" logs/s9full-2026-10-03.log 2>/dev/null; then echo "$(date +%T) S9 done"; exit 0; fi
  if ! pgrep -f "scripts/s9/s9full" >/dev/null; then
    echo "$(date +%T) driver not running - restarting (resumable)"
    setsid nohup bash scripts/s9/s9full_v2.sh >> logs/s9full-2026-10-03.log 2>&1 < /dev/null &
  fi
  sleep 600
done
