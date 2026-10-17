"""Judge Project 1's original benchmark answers (64 questions x hybrid/vector) with the release judge.

Output: results/judgments/p1_answers.jsonl (resumable; consumed by the UI Agreement page, where the
owner's labels on the same items are compared with these judge scores).
Context = the answer's validated citation snippets (the full retrieved passages are not stored by P1).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.config import get_settings  # noqa: E402
from judgecal.datasets import load_jsonl  # noqa: E402
from judgecal.evalrun import RAG_CRITERIA, release_judge  # noqa: E402
from judgecal.judge import JudgeItem  # noqa: E402
from judgecal.llm import LLMClient, StructuredOutputError  # noqa: E402
from judgecal.logjson import setup_logging  # noqa: E402
from judgecal.runs import append_jsonl, done_ids, run_resumable  # noqa: E402

OUT = ROOT / "results" / "judgments" / "p1_answers.jsonl"


def main() -> None:
    setup_logging()
    judge = release_judge(LLMClient(get_settings().judge_provider, max_wait_s=120))
    done = done_ids(OUT)
    for r in load_jsonl(ROOT / "data" / "p1_label_queue.jsonl"):
        if r["item_id"] in done:
            continue
        ref = []
        if r["category"] == "out_of_scope":
            ref.append("This question is out of scope for the filings: the correct behavior is to refuse.")
        if r["gold"]:
            ref.append("Gold answer entities: " + ", ".join(r["gold"]))
        if r["gold_number"] is not None:
            ref.append(f"Gold number: {r['gold_number']}")
        if r["evidence"]:
            ref.append("Evidence quotes from the filings: " + " | ".join(r["evidence"]))
        ctx = "\n\n".join(f"[{c['chunk_id']}] {c['snippet']}" for c in r["citations"]) or "(no passages cited)"
        try:
            res = judge.score(
                JudgeItem(r["item_id"], r["question"], r["answer"], reference="\n".join(ref), context=ctx, kind="rag"),
                RAG_CRITERIA,
            )
        except StructuredOutputError as exc:
            append_jsonl(OUT, {"item_id": r["item_id"], "error": str(exc)[:300]})
            continue
        append_jsonl(
            OUT,
            {
                "item_id": r["item_id"],
                "system": r["system"],
                "category": r["category"],
                "auto_correct": r["auto_correct"],
                "scores": res.scores,
                "reasoning": res.reasoning,
                "judge_config_sha256": judge.config_sha256(),
            },
        )
        print(r["item_id"], res.scores, flush=True)


if __name__ == "__main__":
    run_resumable(main)
