"""Length bias, controlled: add neutral verbosity that changes no content, re-judge, compare.

Items: 30 TEST items (6 per human helpfulness level). Padding: a fixed polite preamble and closing
(~290 chars) that adds no facts, no steps and no claims. Same judge config as the TEST run, so the
original scores are the cached TEST judgments. Output: results/bias/length_perturb.jsonl (resumable).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.config import get_settings  # noqa: E402
from judgecal.datasets import CRITERION_TO_HS2, load_sample  # noqa: E402
from judgecal.judge import Judge, JudgeItem  # noqa: E402
from judgecal.llm import LLMClient, StructuredOutputError  # noqa: E402
from judgecal.logjson import setup_logging  # noqa: E402
from judgecal.rubric import load_rubric  # noqa: E402
from judgecal.runs import append_jsonl, done_ids  # noqa: E402

PRE = "Thank you for your question. I have read your request carefully, and my response follows below.\n\n"
POST = ("\n\nI hope this response is useful to you. If anything is unclear, or if you would like me to expand on "
        "any part of it, please let me know.")
OUT = ROOT / "results" / "bias" / "length_perturb.jsonl"
CRITERIA = list(CRITERION_TO_HS2)


def pick(per_level: int = 6, seed: int = 9) -> list[dict[str, object]]:
    rng = random.Random(seed)
    test = load_sample("test")
    out: list[dict[str, object]] = []
    for level in range(5):
        pool = sorted([t for t in test if t["human"]["helpfulness"] == level], key=lambda t: t["item_id"])
        rng.shuffle(pool)
        out.extend(pool[:per_level])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", required=True)
    args = ap.parse_args()
    setup_logging()
    s = get_settings()
    judge = Judge(LLMClient(s.judge_provider, max_wait_s=1800), s.judge_model, load_rubric(args.rubric))
    done = done_ids(OUT)
    for it in pick():
        iid = str(it["item_id"])
        if iid in done:
            continue
        orig = str(it["response"])
        padded = PRE + orig + POST
        try:
            r0 = judge.score(JudgeItem(iid, str(it["prompt"]), orig), CRITERIA)
            r1 = judge.score(JudgeItem(iid + "-padded", str(it["prompt"]), padded), CRITERIA)
        except StructuredOutputError as exc:
            append_jsonl(OUT, {"item_id": iid, "error": str(exc)[:300]})
            continue
        append_jsonl(OUT, {"item_id": iid, "rubric": args.rubric, "human_helpfulness": it["human"]["helpfulness"],  # type: ignore[index]
                           "original_chars": len(orig), "padded_chars": len(padded),
                           "scores_original": r0.scores, "scores_padded": r1.scores})
        print(iid, r0.scores, r1.scores, flush=True)


if __name__ == "__main__":
    main()
