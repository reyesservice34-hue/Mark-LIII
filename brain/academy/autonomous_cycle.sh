#!/bin/sh
set -eu
ROOT=/root/Mark-LIII
LOCK=/run/mia-academy.lock
exec 9>"$LOCK"
flock -n 9 || { echo "ACADEMY_SKIP_ALREADY_RUNNING"; exit 0; }
cd "$ROOT"
PY="$ROOT/.venv/bin/python3"
export PYTHONPATH="$ROOT"
echo "ACADEMY_CYCLE_START $(date -Is)"
"$PY" brain/academy/academy_loop.py
"$PY" brain/academy/adaptive_scheduler.py
if [ -s knowledge_src/business_academy/remediation_queue.json ] && grep -q '"case_id"' knowledge_src/business_academy/remediation_queue.json; then
  echo "ACADEMY_MODE=RETEST"
  timeout 12m "$PY" brain/academy/retest.py || echo "ACADEMY_RETEST_EXIT=$?"
else
  echo "ACADEMY_MODE=NEW_CASE"
  timeout 12m "$PY" brain/academy/academy_engine.py 1 || echo "ACADEMY_CASE_EXIT=$?"
fi
"$PY" brain/academy/academy_loop.py
"$PY" brain/academy/adaptive_scheduler.py
echo "ACADEMY_CYCLE_DONE $(date -Is)"
