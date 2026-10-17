"""Command line: migrations, labeling-queue seeding, run import, agreement vs owner labels, gate check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from judgecal.config import REPO_ROOT
from judgecal.logjson import setup_logging


def _queue_items() -> list[dict[str, Any]]:
    from judgecal.datasets import load_jsonl, load_sample

    items: list[dict[str, Any]] = []
    for r in load_jsonl(REPO_ROOT / "data" / "p1_label_queue.jsonl"):
        ref = []
        if r["gold"]:
            ref.append("Gold: " + ", ".join(r["gold"]))
        if r["gold_number"] is not None:
            ref.append(f"Gold number: {r['gold_number']}")
        if r["category"] == "out_of_scope":
            ref.append("Out of scope: the correct behavior is to refuse.")
        ref += [f"Evidence: {q}" for q in r["evidence"]]
        ctx = "\n\n".join(f"[{c['chunk_id']}] {c['snippet']}" for c in r["citations"])
        items.append(
            {
                "item_id": r["item_id"],
                "source": "p1",
                "kind": "rag",
                "task": r["question"],
                "response": r["answer"],
                "reference": "\n".join(ref),
                "context": ctx,
                "meta": {"system": r["system"], "category": r["category"], "auto_correct": r["auto_correct"]},
            }
        )
    for s in load_sample():
        items.append(
            {
                "item_id": s["item_id"],
                "source": "helpsteer2",
                "kind": "general",
                "task": s["prompt"],
                "response": s["response"],
                "meta": {"split": s["split"]},
            }
        )
    return items


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(prog="judgecal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply numbered SQL migrations")
    sub.add_parser("seed-queue", help="load P1 answers (+ HelpSteer2 sample) into the labeling queue")
    imp = sub.add_parser("import-runs", help="store eval-run records (JSON files) in Postgres")
    imp.add_argument("paths", nargs="*", type=Path)
    imp.add_argument("--kind", default="release_gate")
    ag = sub.add_parser("agreement", help="kappa of the judge vs the owner's labels + self-agreement")
    ag.add_argument("--judgments", type=Path, default=REPO_ROOT / "results" / "judgments" / "p1_answers.jsonl")
    ag.add_argument("--labeler", default=None)
    sub.add_parser("gate", help="check the committed eval evidence against the baseline")
    args = ap.parse_args(argv)

    if args.cmd == "gate":
        from judgecal.gate import main as gate_main

        return gate_main([])
    from judgecal import db

    with db.connect() as conn:
        if args.cmd == "migrate":
            print(json.dumps({"applied": db.migrate(conn)}))
        elif args.cmd == "seed-queue":
            print(json.dumps({"upserted": db.upsert_items(conn, _queue_items()), **db.label_counts(conn)}))
        elif args.cmd == "import-runs":
            paths = args.paths or sorted((REPO_ROOT / "eval_runs" / "history").glob("*.json"))
            for p in paths:
                db.insert_run(conn, json.loads(p.read_text(encoding="utf-8")), kind=args.kind)
            print(json.dumps({"imported": len(paths)}))
        elif args.cmd == "agreement":
            from judgecal.agreement import judge_vs_labels, self_agreement
            from judgecal.runs import read_jsonl

            max_scores = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}
            labels = db.all_labels(conn)
            judge = {r["item_id"]: r["scores"] for r in read_jsonl(args.judgments) if "scores" in r}
            out = {
                "labels": len(labels),
                "self_agreement": self_agreement(labels, max_scores),
                "judge_vs_labels": judge_vs_labels(labels, judge, max_scores, args.labeler),
            }
            (REPO_ROOT / "results" / "human_label_agreement.json").write_text(json.dumps(out, indent=2) + "\n")
            print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
