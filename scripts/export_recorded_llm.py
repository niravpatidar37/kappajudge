"""Export the recorded-LLM fixtures used by CI/compose smoke tests (no model calls, $0).

Replays the release-gate eval from the local LLM cache with the kill switch on
(LLM_CALLS_ENABLED=false: any cache miss raises instead of calling a provider) for

  1. prompts/rag_answer.md                (the release prompt; must reproduce eval_runs/baseline.json)
  2. scripts/demo/rag_answer_weakened.md  (the demo regression prompt from PR #1)

and copies exactly the cache entries those runs read into tests/fixtures/recorded_llm/llm/. These are
REAL model outputs recorded during the original runs. It also writes tests/fixtures/regressed_record.json,
the weakened prompt's eval record, which the gate CLI must reject.

  JUDGECAL_CACHE_DIR=data/cache uv run --frozen python scripts/export_recorded_llm.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "recorded_llm" / "llm"
REGRESSED = ROOT / "tests" / "fixtures" / "regressed_record.json"
WEAK_PROMPT = ROOT / "scripts" / "demo" / "rag_answer_weakened.md"

os.environ["LLM_CALLS_ENABLED"] = "false"  # kill switch: a cache miss must raise, never call a provider
os.environ["LLM_RECORDED_MODE"] = "false"
os.environ.setdefault("JUDGECAL_CACHE_DIR", str(ROOT / "data" / "cache"))

from judgecal import llm  # noqa: E402
from judgecal.evalrun import run  # noqa: E402
from judgecal.sut import PROMPT_PATH  # noqa: E402

used: set[str] = set()
_orig = llm.cache_key


def _recording_key(payload: dict[str, object]) -> str:
    k = _orig(payload)
    used.add(k)
    return k


llm.cache_key = _recording_key  # type: ignore[assignment]


def main() -> int:
    cache = Path(os.environ["JUDGECAL_CACHE_DIR"]) / "llm"
    base = json.loads((ROOT / "eval_runs" / "baseline.json").read_text(encoding="utf-8"))
    rec = run(PROMPT_PATH)
    same = {i["question_id"]: i["scores"] for i in rec["items"]} == {
        i["question_id"]: i["scores"] for i in base["items"]
    }
    print(json.dumps({"release_prompt_replay_matches_baseline": same, "summary": rec["summary"]}))
    if not same:
        return 1
    weak = run(WEAK_PROMPT)
    REGRESSED.parent.mkdir(parents=True, exist_ok=True)
    REGRESSED.write_text(json.dumps(weak, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"weakened_prompt_summary": weak["summary"]}))
    if OUT.exists():
        shutil.rmtree(OUT)
    for k in sorted(used):
        src = cache / k[:2] / f"{k}.json"
        if not src.is_file():
            continue  # keys of later validation-retry attempts that were never needed
        dst = OUT / k[:2] / f"{k}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(src.read_text(encoding="utf-8"))
        data["usd"] = 0.0  # replay is free; the original spend is in the ledger
        dst.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"recorded_entries": len(list(OUT.rglob("*.json")))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
