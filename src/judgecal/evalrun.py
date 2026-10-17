"""`make eval`: run the system-under-test prompt on the fixed eval set and judge every answer.

Writes eval_runs/latest.json (the evidence CI checks) and eval_runs/history/<ts>_<id>.json, and stores
the run in Postgres when --store is given (Cloud Run Job / local stack). Needs GROQ_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from judgecal.config import REPO_ROOT, get_settings
from judgecal.gate import build_record
from judgecal.judge import Judge, JudgeItem
from judgecal.llm import LLMClient
from judgecal.logjson import setup_logging
from judgecal.rubric import load_rubric
from judgecal.sut import (
    EVAL_SET_PATH,
    PROMPT_PATH,
    file_sha256,
    format_contexts,
    generate,
    load_eval_set,
    reference_text,
)

RELEASE_JUDGE_PATH = REPO_ROOT / "judge_release.json"
RAG_CRITERIA = ["correctness", "completeness", "coherence", "groundedness", "safety"]


def release_judge(client: LLMClient) -> Judge:
    cfg = json.loads(RELEASE_JUDGE_PATH.read_text(encoding="utf-8"))
    return Judge(
        client,
        cfg["model"],
        load_rubric(cfg["rubric_version"]),
        order=cfg.get("order", "reason_first"),
        temperature=float(cfg.get("temperature", 0.0)),
        max_tokens=int(cfg.get("max_tokens", 1000)),
    )


def run(
    prompt_path: Path = PROMPT_PATH, eval_set_path: Path = EVAL_SET_PATH, limit: int = 0, max_wait_s: float = 1800
) -> dict[str, Any]:
    s = get_settings()
    judge_client = LLMClient(s.judge_provider, max_wait_s=max_wait_s)
    sut_client = LLMClient(s.sut_provider, max_wait_s=max_wait_s)
    judge = release_judge(judge_client)
    items = load_eval_set(eval_set_path)
    if limit:
        items = items[:limit]
    out_items: list[dict[str, Any]] = []
    usd = 0.0
    for it in items:
        answer, gen_usd = generate(sut_client, s.sut_model, it, prompt_path)
        res = judge.score(
            JudgeItem(
                str(it["question_id"]),
                it["question"],
                answer,
                reference=reference_text(it),
                context=format_contexts(it["contexts"]),
                kind="rag",
            ),
            RAG_CRITERIA,
        )
        usd += gen_usd + res.usd
        out_items.append(
            {
                "question_id": str(it["question_id"]),
                "category": it["category"],
                "answer": answer,
                "scores": res.scores,
                "reasoning": res.reasoning,
            }
        )
        print(f"{it['question_id']}: {res.scores}", flush=True)
    rel = (
        prompt_path.resolve().relative_to(REPO_ROOT.resolve())
        if prompt_path.resolve().is_relative_to(REPO_ROOT.resolve())
        else prompt_path
    )
    return build_record(
        items=out_items,
        prompt_sha256=file_sha256(prompt_path),
        eval_set_sha256=file_sha256(eval_set_path),
        judge_config=judge.config(),
        sut={"provider": s.sut_provider, "model": s.sut_model, "temperature": 0.0},
        max_scores={c: judge.rubric.criterion(c).max_score for c in RAG_CRITERIA},
        usd=usd,
        prompt_path=str(rel).replace("\\", "/"),
    )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(prog="judgecal-eval")
    ap.add_argument("--prompt", type=Path, default=PROMPT_PATH)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "eval_runs")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--store", action="store_true", help="also insert the run into Postgres (DATABASE_URL)")
    args = ap.parse_args(argv)
    record = run(args.prompt, limit=args.limit)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "history").mkdir(exist_ok=True)
    body = json.dumps(record, indent=1, ensure_ascii=False) + "\n"
    (args.out / "latest.json").write_text(body, encoding="utf-8", newline="\n")
    stamp = record["created_at"].replace(":", "").replace("-", "")
    (args.out / "history" / f"{stamp}_{record['run_id'][:8]}.json").write_text(body, encoding="utf-8", newline="\n")
    if args.store:
        from judgecal import db

        with db.connect() as conn:
            db.insert_run(conn, record, kind="release_gate")
    print(
        json.dumps(
            {"summary": record["summary"], "usd": record["usd"], "prompt_sha256": record["prompt_sha256"]}, indent=1
        )
    )
    return 0


if __name__ == "__main__":
    from judgecal.runs import run_resumable

    run_resumable(lambda: sys.exit(main()))
