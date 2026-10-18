"""Print the judge-vs-human agreement and the bias numbers, recomputed from committed raw files.

    uv run python assets/demo/kappa_summary.py

Used by the demo recording. No LLM calls: QWK and the human ceiling are recomputed here from
results/judgments/calib_v1_test_reason_first.jsonl and data/calibration_sample.jsonl (HelpSteer2
per-annotator ratings); the bias lines are read from results/bias_summary.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.datasets import CRITERION_TO_HS2, load_sample  # noqa: E402
from judgecal.metrics import inter_annotator, qwk  # noqa: E402
from judgecal.runs import read_jsonl  # noqa: E402

B, D, V, R = "\033[1m", "\033[2m", "\033[38;5;141m", "\033[0m"


def main() -> None:
    sample = {s["item_id"]: s for s in load_sample()}
    rows = [r for r in read_jsonl(ROOT / "results/judgments/calib_v1_test_reason_first.jsonl") if "scores" in r]
    test_items = [s for s in sample.values() if s["split"] == "test"]
    print(f"{B}judge qwen3.8-27b, rubric v1, TEST split{R} {D}(never used for tuning){R}")
    print(f"{D}{'criterion':<14}{'n':>4}{'judge QWK':>11}{'human pairwise':>16}{'human LOO':>11}{R}")
    judge_q, pair_q = [], []
    for c, h in CRITERION_TO_HS2.items():
        j = [r["scores"][c] for r in rows]
        hu = [sample[r["item_id"]]["human"][h] for r in rows]
        k = qwk(j, hu, 4)
        ceil = inter_annotator([s["annotators"][h] for s in test_items], 4)
        judge_q.append(k)
        pair_q.append(ceil["pairwise_qwk_mean"])
        print(
            f"{c:<14}{len(rows):>4}{V}{k:>11.2f}{R}{ceil['pairwise_qwk_mean']:>16.2f}{ceil['leave_one_out_qwk']:>11.2f}"
        )
    mj, mp = sum(judge_q) / 3, sum(pair_q) / 3
    print(f"{B}{'mean':<18}{V}{mj:>11.2f}{R}{B}{mp:>16.2f}{R}  {D}(ceiling on all {len(test_items)} TEST items){R}")

    bias = json.loads((ROOT / "results/bias_summary.json").read_text(encoding="utf-8"))
    p = bias["position"]["openai-gpt-oss-20b"]
    lo, hi = p["flip_rate_ci95"]
    print(f"\n{B}bias{R} {D}(results/bias_summary.json){R}")
    print(
        f"position  gpt-oss-20b, MT-bench pairs swapped: flip rate {V}{p['flip_rate']:.0%}{R} "
        f"[{lo:.0%}, {hi:.0%}], n={p['pairs']}/{p['planned_pairs']}"
    )
    pert = bias["length"]["controlled_perturbation"]
    deltas = ", ".join(f"{c} {v['mean_delta']:+.2f}" for c, v in pert["criteria"].items())
    print(f"length    +{pert['mean_added_chars']:.0f} chars of neutral padding (n={pert['n']}): {deltas}")
    both = bias["self_preference"].get("pairs_judged_by_both", 0)
    print(f"self-pref pairs judged by both judges: {both} -> delta {'not yet measured' if not both else 'see README'}")


if __name__ == "__main__":
    main()
