#!/usr/bin/env bash
# gpt-oss-20b-only experiments (do not compete with the qwen judge's quota): self-preference judging by
# gpt-oss, then the remaining MT-bench position pairs. Waits only the provider's short retry-after.
cd "$(dirname "$0")/.." || exit 1
patient() {
  for _ in $(seq 1 200); do
    out=$("$@" 2>&1 | grep -v '"level"')
    last=$(echo "$out" | tail -1)
    echo "$last"
    echo "$last" | grep -q quota_exhausted || return 0
    wait_s=$(echo "$last" | sed -n 's/.*"retry_after_s": \([0-9.]*\).*/\1/p' | cut -d. -f1)
    [ -z "$wait_s" ] && wait_s=300
    [ "$wait_s" -gt 900 ] && { echo "retry-after ${wait_s}s too long; stopping"; return 75; }
    sleep $((wait_s + 5))
  done
}
patient .venv/Scripts/python.exe scripts/bias_selfpref.py --stage judge --n 10 --judges openai/gpt-oss-20b
patient .venv/Scripts/python.exe scripts/bias_position.py --model openai/gpt-oss-20b --n 40
