"""`make eval`: run the system-under-test prompt on the fixed eval set and judge every answer.

Writes eval_runs/latest.json (the evidence CI checks) and eval_runs/history/<ts>_<id>.json, and stores
the run in Postgres when --store is given (Cloud Run Job / local stack). Needs GROQ_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from judgecal.config import REPO_ROOT, get_settings
from judgecal.gate import BASELINE_PATH, GateResult, build_record, canonical_sha256
from judgecal.gate import check as gate_check
from judgecal.gate import load_config as load_gate_config
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
    load_prompt,
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


EXIT_DRIFT = 3
log = logging.getLogger("judgecal.evalrun")


def drift_check(record: dict[str, Any], prompt_path: Path) -> GateResult:
    """Scheduled-run drift: the fresh scores vs the committed baseline (same checks as the release gate,
    with the record's own prompt as the reference, so only yardstick changes and regressions count)."""
    return gate_check(
        record,
        json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.is_file() else None,
        prompt_sha256=file_sha256(prompt_path),
        eval_set_sha256=file_sha256(EVAL_SET_PATH),
        eval_ids=[str(i["question_id"]) for i in load_eval_set()],
        config=load_gate_config(),
    )


def preflight(prompt_path: Path = PROMPT_PATH) -> dict[str, Any]:
    """`--smoke`: everything the scheduled job needs, without a single model call or write.

    Config valid, prompt/eval set/baseline/gate config load, the release judge config equals the
    baseline's (else every run would report drift), Postgres reachable with all migrations applied,
    and the Groq key present unless the kill switch or recorded mode makes it unnecessary.
    """
    checks: dict[str, Any] = {}
    s = get_settings()
    checks["kill_switch_llm_calls_enabled"] = s.llm_calls_enabled
    checks["recorded_mode"] = s.llm_recorded_mode
    load_prompt(prompt_path)
    items = load_eval_set()
    checks["eval_items"] = len(items)
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    load_gate_config()
    judge = release_judge(LLMClient(s.judge_provider))
    checks["judge_config_matches_baseline"] = canonical_sha256(judge.config()) == baseline.get("judge_config_sha256")
    checks["groq_key_present"] = bool(os.environ.get("GROQ_API_KEY"))
    needs_key = s.llm_calls_enabled and not s.llm_recorded_mode
    checks["database"] = False
    if s.database_url:
        from judgecal import db

        with db.connect() as conn:
            schema = db.schema_status(conn)
        checks["database"] = True
        checks["migrations_pending"] = schema["pending"]
    ok = (
        checks["judge_config_matches_baseline"]
        and checks["database"]
        and checks.get("migrations_pending") == []
        and (checks["groq_key_present"] or not needs_key)
        and len(items) > 0
    )
    return {"ok": bool(ok), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(prog="judgecal-eval")
    ap.add_argument("--prompt", type=Path, default=PROMPT_PATH)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "eval_runs")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--store", action="store_true", help="also insert the run into Postgres (DATABASE_URL)")
    ap.add_argument("--gen-only", action="store_true", help="only generate (and cache) the SUT answers")
    ap.add_argument("--smoke", action="store_true", help="preflight only: no model calls, no writes")
    ap.add_argument("--fail-on-drift", action="store_true", help=f"exit {EXIT_DRIFT} if the gate check fails")
    args = ap.parse_args(argv)
    if args.smoke:
        res = preflight(args.prompt)
        log.info("eval preflight", extra={"event": "eval_preflight", "ok": res["ok"], "checks": res["checks"]})
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    if args.gen_only:
        s = get_settings()
        client = LLMClient(s.sut_provider, max_wait_s=120)
        for it in load_eval_set()[: args.limit or None]:
            answer, _ = generate(client, s.sut_model, it, args.prompt)
            print(f"{it['question_id']}: {answer[:80]!r}", flush=True)
        return 0
    try:
        record = run(args.prompt, limit=args.limit)
    except Exception as exc:
        # One structured event per failed run (log-based metric + alert); the exception still fails the job.
        log.error("eval run failed", extra={"event": "eval_run_failed", "error_type": type(exc).__name__})
        raise
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "history").mkdir(exist_ok=True)
    body = json.dumps(record, indent=1, ensure_ascii=False) + "\n"
    (args.out / "latest.json").write_text(body, encoding="utf-8", newline="\n")
    stamp = record["created_at"].replace(":", "").replace("-", "")
    (args.out / "history" / f"{stamp}_{record['run_id'][:8]}.json").write_text(body, encoding="utf-8", newline="\n")
    kind = "smoke" if get_settings().llm_recorded_mode else "release_gate"
    if args.store:
        from judgecal import db

        with db.connect() as conn:
            db.insert_run(conn, record, kind=kind)
    verdict = drift_check(record, args.prompt) if not args.limit else None
    if verdict is not None:
        extra = {
            "event": "eval_gate" if verdict.ok else "eval_gate_drift",
            "ok": verdict.ok,
            "run_id": record["run_id"],
            "kind": kind,
            "deltas_normalized": verdict.deltas,
            "failures": verdict.failures,
        }
        (log.info if verdict.ok else log.error)("eval gate verdict", extra=extra)
    print(
        json.dumps(
            {
                "run_id": record["run_id"],
                "kind": kind,
                "stored": args.store,
                "summary": record["summary"],
                "usd": record["usd"],
                "prompt_sha256": record["prompt_sha256"],
                "gate_ok": None if verdict is None else verdict.ok,
                "gate_failures": [] if verdict is None else verdict.failures,
            },
            indent=1,
        )
    )
    if verdict is not None and not verdict.ok and args.fail_on_drift:
        return EXIT_DRIFT
    return 0


if __name__ == "__main__":
    from judgecal.runs import run_resumable

    run_resumable(lambda: sys.exit(main()))
