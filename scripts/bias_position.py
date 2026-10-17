"""Position bias: judge MT-bench pairs in both orders; flip rate + agreement with human verdicts.

Pairs: lmsys/mt_bench_human_judgments, turn 1, one (question, model pair) per question, human
majority verdict non-tie, both answers <= 1200 chars (sized to the free-tier token quota).
Output: results/bias/position_<model>.jsonl (resumable) and summarized by scripts/report.py.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.datasets import mtbench_pairs  # noqa: E402
from judgecal.judge import PairwiseJudge  # noqa: E402
from judgecal.llm import LLMClient, StructuredOutputError  # noqa: E402
from judgecal.logjson import setup_logging  # noqa: E402
from judgecal.runs import append_jsonl, done_ids, run_resumable  # noqa: E402

MAX_ANS = 1200


def select_pairs(n: int, seed: int = 11) -> list[dict[str, Any]]:
    rows = mtbench_pairs()
    agg: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        x, y = sorted([r["model_a"], r["model_b"]])
        agg[(r["question_id"], x, y)].append(r)
    cands: list[dict[str, Any]] = []
    for (qid, x, y), rs in agg.items():
        votes: Counter[str] = Counter()
        answers: dict[str, str] = {}
        for r in rs:
            answers[r["model_a"]] = r["answer_a"]
            answers[r["model_b"]] = r["answer_b"]
            if r["winner"] == "tie":
                votes["tie"] += 1
            else:
                votes[r["model_a"] if r["winner"] == "model_a" else r["model_b"]] += 1
        top = votes.most_common(2)
        if top[0][0] == "tie" or (len(top) > 1 and top[0][1] == top[1][1]):
            continue
        if max(len(answers[x]), len(answers[y])) > MAX_ANS or len(rs[0]["question"]) > 800:
            continue
        cands.append(
            {
                "pair_id": f"q{qid}-{x}-vs-{y}",
                "question_id": qid,
                "question": rs[0]["question"],
                "model_x": x,
                "model_y": y,
                "answer_x": answers[x],
                "answer_y": answers[y],
                "human_winner": top[0][0],
                "human_votes": dict(votes),
            }
        )
    rng = random.Random(seed)
    rng.shuffle(cands)
    chosen: list[dict[str, Any]] = []
    seen_q: set[int] = set()
    for c in cands:
        if c["question_id"] in seen_q:
            continue
        chosen.append(c)
        seen_q.add(c["question_id"])
        if len(chosen) >= n:
            break
    return chosen


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="openai/gpt-oss-20b")
    p.add_argument("--n", type=int, default=40)
    args = p.parse_args()
    setup_logging()
    pj = PairwiseJudge(LLMClient("groq", max_wait_s=1800), args.model)
    out = ROOT / "results" / "bias" / f"position_{args.model.replace('/', '-')}.jsonl"
    done = done_ids(out, "pair_id")
    for i, c in enumerate(select_pairs(args.n)):
        if c["pair_id"] in done:
            continue
        verdicts: dict[str, str] = {}
        try:
            for order in ("xy", "yx"):
                a, b = (c["answer_x"], c["answer_y"]) if order == "xy" else (c["answer_y"], c["answer_x"])
                v, _ = pj.compare(c["question"], a, b)
                first, second = (c["model_x"], c["model_y"]) if order == "xy" else (c["model_y"], c["model_x"])
                verdicts[order] = "tie" if v.winner == "tie" else (first if v.winner == "A" else second)
                verdicts[order + "_slot"] = v.winner
        except StructuredOutputError as exc:
            append_jsonl(out, {"pair_id": c["pair_id"], "error": str(exc)[:300]})
            continue
        append_jsonl(
            out,
            {
                **{k: c[k] for k in ("pair_id", "question_id", "model_x", "model_y", "human_winner", "human_votes")},
                "len_x": len(c["answer_x"]),
                "len_y": len(c["answer_y"]),
                "judge": args.model,
                "verdict_xy": verdicts["xy"],
                "verdict_yx": verdicts["yx"],
                "slot_xy": verdicts["xy_slot"],
                "slot_yx": verdicts["yx_slot"],
            },
        )
        print(f"{i + 1}: {c['pair_id']} {verdicts}", flush=True)


if __name__ == "__main__":
    run_resumable(main)
