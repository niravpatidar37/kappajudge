"""Run the judge on the HelpSteer2 calibration sample (resumable; cached by content hash).

Example: python scripts/run_calibration.py --rubric v1 --split dev
Output: results/judgments/calib_<rubric>_<split>_<order>[_<salt>].jsonl, one row per item.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.config import get_settings  # noqa: E402
from judgecal.datasets import CRITERION_TO_HS2, load_sample  # noqa: E402
from judgecal.judge import Judge, JudgeItem  # noqa: E402
from judgecal.llm import LLMClient, StructuredOutputError  # noqa: E402
from judgecal.logjson import setup_logging  # noqa: E402
from judgecal.rubric import load_rubric  # noqa: E402
from judgecal.runs import append_jsonl, done_ids, run_resumable  # noqa: E402

CRITERIA = list(CRITERION_TO_HS2)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rubric", default="v1")
    p.add_argument("--split", choices=["dev", "test"], default="dev")
    p.add_argument("--order", choices=["reason_first", "score_first"], default="reason_first")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids-from", default="", help="only items whose ids appear in this jsonl")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--salt", default="")
    p.add_argument("--model", default="")
    args = p.parse_args()
    setup_logging()
    s = get_settings()
    model = args.model or s.judge_model
    rubric = load_rubric(args.rubric)
    client = LLMClient(s.judge_provider, max_wait_s=1800)
    judge = Judge(client, model, rubric, order=args.order, temperature=args.temperature, sample_salt=args.salt)
    items = load_sample(args.split)
    if args.ids_from:
        from judgecal.runs import read_jsonl

        keep = {r["item_id"] for r in read_jsonl(Path(args.ids_from))}
        items = [i for i in items if i["item_id"] in keep]
    # Round-robin by human helpfulness level, so a run cut short by a quota is still stratified.
    by_level: dict[int, list[dict[str, Any]]] = {}
    for it in items:
        by_level.setdefault(it["human"]["helpfulness"], []).append(it)
    items = [
        lv[k] for k in range(max(map(len, by_level.values()), default=0)) for lv in by_level.values() if k < len(lv)
    ]
    if args.limit:
        items = items[: args.limit]
    tag = f"calib_{args.rubric}_{args.split}_{args.order}" + (f"_{args.salt}" if args.salt else "")
    if args.model:
        tag += "_" + model.replace("/", "-")
    out = ROOT / "results" / "judgments" / f"{tag}.jsonl"
    done = done_ids(out)
    tin = tout = 0
    usd = 0.0
    t0 = time.time()
    for n, it in enumerate(items):
        if it["item_id"] in done:
            continue
        ji = JudgeItem(it["item_id"], it["prompt"], it["response"])
        try:
            res = judge.score(ji, CRITERIA)
        except StructuredOutputError as exc:
            append_jsonl(out, {"item_id": it["item_id"], "error": str(exc)[:300]})
            continue
        tin += sum(c.input_tokens for c in res.calls if not c.cached)
        tout += sum(c.output_tokens for c in res.calls if not c.cached)
        usd += res.usd
        append_jsonl(
            out,
            {
                "item_id": it["item_id"],
                "split": it["split"],
                "rubric": args.rubric,
                "order": args.order,
                "model": model,
                "temperature": args.temperature,
                "salt": args.salt,
                "scores": res.scores,
                "reasoning": res.reasoning,
                "human": {c: it["human"][h] for c, h in CRITERION_TO_HS2.items()},
                "response_chars": len(it["response"]),
                "attempts": len(res.calls),
                "cached": res.cached,
                "judge_config_sha256": judge.config_sha256(),
            },
        )
        if n % 10 == 0:
            print(f"[{tag}] {n + 1}/{len(items)} in={tin} out={tout} usd={usd:.4f} {time.time() - t0:.0f}s", flush=True)
    print(f"[{tag}] done in={tin} out={tout} usd={usd:.4f} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    run_resumable(main)
