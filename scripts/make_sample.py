"""Build the stratified 200-item calibration sample (100 DEV / 100 TEST) and the anchor pool."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.datasets import ANCHOR_POOL_PATH, helpsteer2_candidates, stratified_sample, write_sample  # noqa: E402


def main() -> None:
    cands = helpsteer2_candidates()
    sample = stratified_sample(cands)
    write_sample(sample)
    used_prompts = {s["prompt"] for s in sample}
    # Anchor pool: candidates whose PROMPT is not in the sample (no leakage into DEV or TEST).
    pool = [c for c in cands if c["prompt"] not in used_prompts]
    rng = random.Random(7)
    rng.shuffle(pool)
    keep = []
    per_cell: Counter[tuple[str, int]] = Counter()
    for c in pool:
        cells = [
            ("correctness", c["human"]["correctness"]),
            ("helpfulness", c["human"]["helpfulness"]),
            ("coherence", c["human"]["coherence"]),
        ]
        if len(c["response"]) > 500 or not any(per_cell[k] < 6 for k in cells):
            continue
        # Only unanimous-ish items make good anchors: annotator spread <= 1 on all three attributes.
        if any(
            max(c["annotators"][a]) - min(c["annotators"][a]) > 1 for a in ("correctness", "helpfulness", "coherence")
        ):
            continue
        keep.append(c)
        for k in cells:
            per_cell[k] += 1
    write_sample(keep, ANCHOR_POOL_PATH)
    stats = {
        "candidates": len(cands),
        "sample": len(sample),
        "by_split": Counter(s["split"] for s in sample),
        "helpfulness_by_split": {
            sp: dict(sorted(Counter(s["human"]["helpfulness"] for s in sample if s["split"] == sp).items()))
            for sp in ("dev", "test")
        },
        "correctness": dict(sorted(Counter(s["human"]["correctness"] for s in sample).items())),
        "coherence": dict(sorted(Counter(s["human"]["coherence"] for s in sample).items())),
        "anchor_pool": len(keep),
        "prompt_overlap_dev_test": len(
            {s["prompt"] for s in sample if s["split"] == "dev"} & {s["prompt"] for s in sample if s["split"] == "test"}
        ),
    }
    (ROOT / "results" / "calibration_sample_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
