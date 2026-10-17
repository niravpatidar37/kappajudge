#!/usr/bin/env bash
# Generate (and cache) the system-under-test answers for the baseline and the demo (weakened) prompt.
# Fails fast on daily quota; waits only the provider's short retry-after between attempts.
cd "$(dirname "$0")/../.." || exit 1
gen() {
  for _ in $(seq 1 30); do
    out=$("$@" 2>&1 | grep -v '"level"')
    echo "$out" | tail -1
    echo "$out" | grep -q quota_exhausted || return 0
    sleep 90
  done
  return 75
}
gen .venv/Scripts/python.exe -m judgecal.evalrun --gen-only
JUDGECAL_ROOT=F:/llm-judge-calibration-demo JUDGECAL_CACHE_DIR=F:/llm-judge-calibration/data/cache \
  gen .venv/Scripts/python.exe -m judgecal.evalrun --gen-only --prompt F:/llm-judge-calibration-demo/prompts/rag_answer.md
