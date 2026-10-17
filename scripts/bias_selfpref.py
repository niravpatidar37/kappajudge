"""Self-preference: both models answer the same prompts; each model judges every pair in both orders.

Prompts: MT-bench turn-1 questions NOT used by the position-bias experiment.
Self-preference for judge J = P_J(J's own answer wins) - P_other(J's family answer wins), on the same
pairs, with ties counted as 0.5 and each pair's two orders averaged (removes position effects).
Output: results/bias/selfpref_answers.jsonl, results/bias/selfpref_verdicts.jsonl (resumable).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias_position import select_pairs  # noqa: E402

from judgecal.datasets import mtbench_pairs  # noqa: E402
from judgecal.judge import PairwiseJudge  # noqa: E402
from judgecal.llm import LLMClient, StructuredOutputError  # noqa: E402
from judgecal.logjson import setup_logging  # noqa: E402
from judgecal.runs import append_jsonl, done_ids, read_jsonl, run_resumable  # noqa: E402

MODELS = ("openai/gpt-oss-20b", "qwen/qwen3.8-27b")
GEN_SYSTEM = "You are a helpful assistant. Answer the user's request well in at most 180 words."
ANS = ROOT / "results" / "bias" / "selfpref_answers.jsonl"
VER = ROOT / "results" / "bias" / "selfpref_verdicts.jsonl"


def prompts(n: int, seed: int = 5) -> list[dict[str, object]]:
    used = {c["question_id"] for c in select_pairs(40)}
    qs: dict[int, str] = {}
    for r in mtbench_pairs():
        qs.setdefault(r["question_id"], r["question"])
    pool = sorted(q for q in qs if q not in used and len(qs[q]) <= 800)
    random.Random(seed).shuffle(pool)
    return [{"question_id": q, "question": qs[q]} for q in pool[:n]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--stage", choices=["gen", "judge", "all"], default="all")
    p.add_argument("--judges", default=",".join(MODELS))
    args = p.parse_args()
    setup_logging()
    client = LLMClient("groq", max_wait_s=1800)
    ps = prompts(args.n)
    if args.stage in ("gen", "all"):
        done = done_ids(ANS, "key")
        for q in ps:
            for m in MODELS:
                key = f"{q['question_id']}|{m}"
                if key in done:
                    continue
                res = client.complete(
                    [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": str(q["question"])}],
                    m,
                    max_tokens=900,
                    temperature=0.0,
                )
                append_jsonl(
                    ANS,
                    {
                        "key": key,
                        "question_id": q["question_id"],
                        "model": m,
                        "answer": res.text,
                        "chars": len(res.text),
                    },
                )
                print("gen", key, len(res.text), flush=True)
    if args.stage in ("judge", "all"):
        answers = {r["key"]: r for r in read_jsonl(ANS)}
        done = done_ids(VER, "key")
        g, qw = MODELS
        for judge_model in args.judges.split(","):
            pj = PairwiseJudge(client, judge_model)
            for q in ps:
                key = f"{q['question_id']}|{judge_model}"
                ag = answers.get(f"{q['question_id']}|{g}")
                aq = answers.get(f"{q['question_id']}|{qw}")
                if key in done or ag is None or aq is None or not ag["answer"].strip() or not aq["answer"].strip():
                    continue
                try:
                    v1, _ = pj.compare(str(q["question"]), ag["answer"], aq["answer"])  # A = gpt-oss, B = qwen
                    v2, _ = pj.compare(str(q["question"]), aq["answer"], ag["answer"])  # A = qwen, B = gpt-oss
                except StructuredOutputError as exc:
                    append_jsonl(VER, {"key": key, "error": str(exc)[:300]})
                    continue
                w1 = {"A": g, "B": qw, "tie": "tie"}[v1.winner]
                w2 = {"A": qw, "B": g, "tie": "tie"}[v2.winner]
                append_jsonl(
                    VER,
                    {
                        "key": key,
                        "question_id": q["question_id"],
                        "judge": judge_model,
                        "winner_gptoss_first": w1,
                        "winner_qwen_first": w2,
                        "len_gptoss": ag["chars"],
                        "len_qwen": aq["chars"],
                    },
                )
                print("judge", key, w1, w2, flush=True)


if __name__ == "__main__":
    run_resumable(main)
