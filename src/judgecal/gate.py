"""Evidence-based release gate.

`make eval` (needs GROQ_API_KEY) runs the system-under-test prompt on the fixed eval set, judges every
answer with the calibrated judge, and writes a run record (eval_runs/latest.json) that pins the
SHA-256 of the prompt file, the eval set, and the judge config. CI has no LLM secret, so it checks
the committed evidence instead of re-running models:

1. the record exists and its prompt hash equals the hash of the CURRENT prompt file (else: stale);
2. eval set hash and judge config hash match the committed baseline (same yardstick);
3. the record is internally consistent (every eval item present, summary recomputed from items);
4. no criterion's normalized mean drops more than its threshold vs the baseline; safety may not drop.

Residual risk: a developer can forge a record (edit scores and recompute hashes). The live job
(runs only when a GROQ_API_KEY secret exists) re-runs `make eval` and gates on fresh scores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404 - fixed argv, used only to read the git commit
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from judgecal.config import REPO_ROOT

RECORD_PATH = REPO_ROOT / "eval_runs" / "latest.json"
BASELINE_PATH = REPO_ROOT / "eval_runs" / "baseline.json"
GATE_CONFIG_PATH = REPO_ROOT / "gate_config.json"
SCHEMA_VERSION = 1


@dataclass
class GateResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    deltas: dict[str, float] = field(default_factory=dict)


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def summarize(items: list[dict[str, Any]], max_scores: dict[str, int]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for crit, mx in max_scores.items():
        vals = [it["scores"][crit] for it in items if crit in it.get("scores", {})]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        out[crit] = {"mean": round(mean, 4), "normalized": round(mean / mx, 4), "n": len(vals)}
    return out


def git_commit() -> str:
    try:
        return subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_record(
    *,
    items: list[dict[str, Any]],
    prompt_sha256: str,
    eval_set_sha256: str,
    judge_config: dict[str, Any],
    sut: dict[str, Any],
    max_scores: dict[str, int],
    usd: float,
    prompt_path: str = "prompts/rag_answer.md",
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit_at_run": git_commit(),
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "eval_set_sha256": eval_set_sha256,
        "sut": sut,
        "judge_config": judge_config,
        "judge_config_sha256": canonical_sha256(judge_config),
        "max_scores": max_scores,
        "items": items,
        "summary": summarize(items, max_scores),
        "usd": round(usd, 6),
    }
    rec["record_sha256"] = canonical_sha256({k: v for k, v in rec.items() if k != "record_sha256"})
    return rec


def load_config(path: Path = GATE_CONFIG_PATH) -> dict[str, Any]:
    cfg: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return cfg


def check(
    record: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    *,
    prompt_sha256: str,
    eval_set_sha256: str,
    eval_ids: list[str],
    config: dict[str, Any],
) -> GateResult:
    fails: list[str] = []
    if record is None:
        return GateResult(False, ["missing evidence: eval_runs/latest.json not found; run `make eval`"])
    if baseline is None:
        return GateResult(False, ["missing baseline: eval_runs/baseline.json not found"])
    if record.get("schema_version") != SCHEMA_VERSION:
        fails.append(f"record schema_version {record.get('schema_version')} != {SCHEMA_VERSION}")
    if record.get("prompt_sha256") != prompt_sha256:
        fails.append("stale evidence: record prompt_sha256 does not match the current prompt file; re-run `make eval`")
    if record.get("eval_set_sha256") != eval_set_sha256:
        fails.append("stale evidence: eval set changed since the record was produced")
    if record.get("judge_config_sha256") != baseline.get("judge_config_sha256"):
        fails.append("judge config differs from the baseline's; re-baseline deliberately (`make baseline`)")
    if record.get("judge_config_sha256") != canonical_sha256(record.get("judge_config")):
        fails.append("record judge_config_sha256 does not match its judge_config")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != canonical_sha256(body):
        fails.append("record_sha256 mismatch: the record was edited after it was produced")
    items = record.get("items") or []
    got_ids = sorted(str(it.get("question_id")) for it in items)
    if got_ids != sorted(eval_ids):
        fails.append(f"record covers {len(got_ids)} items, eval set has {len(eval_ids)} (ids differ)")
    max_scores: dict[str, int] = record.get("max_scores") or {}
    for it in items:
        for crit, val in (it.get("scores") or {}).items():
            if crit not in max_scores or not isinstance(val, int) or not 0 <= val <= max_scores[crit]:
                fails.append(f"item {it.get('question_id')}: invalid score {crit}={val!r}")
    recomputed = summarize(items, max_scores)
    if recomputed != record.get("summary"):
        fails.append("summary does not match the per-item scores")
    deltas: dict[str, float] = {}
    base_summary = baseline.get("summary") or {}
    for crit, max_drop in config["max_drop_normalized"].items():
        if crit not in base_summary:
            continue
        if crit not in recomputed:
            fails.append(f"{crit}: missing from record")
            continue
        delta = round(recomputed[crit]["normalized"] - base_summary[crit]["normalized"], 4)
        deltas[crit] = delta
        if -delta > float(max_drop) + 1e-9:
            fails.append(
                f"regression: {crit} normalized mean {recomputed[crit]['normalized']:.4f} vs baseline "
                f"{base_summary[crit]['normalized']:.4f} (drop {-delta:.4f} > allowed {float(max_drop):.4f})"
            )
    return GateResult(not fails, fails, deltas)


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def main(argv: list[str] | None = None) -> int:
    from judgecal.sut import EVAL_SET_PATH, PROMPT_PATH, file_sha256, load_eval_set

    ap = argparse.ArgumentParser(prog="judgecal-gate")
    ap.add_argument("--record", type=Path, default=RECORD_PATH)
    ap.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    ap.add_argument("--prompt", type=Path, default=PROMPT_PATH)
    ap.add_argument("--eval-set", type=Path, default=EVAL_SET_PATH)
    ap.add_argument("--config", type=Path, default=GATE_CONFIG_PATH)
    args = ap.parse_args(argv)
    res = check(
        _load(args.record),
        _load(args.baseline),
        prompt_sha256=file_sha256(args.prompt),
        eval_set_sha256=file_sha256(args.eval_set),
        eval_ids=[str(i["question_id"]) for i in load_eval_set(args.eval_set)],
        config=load_config(args.config),
    )
    print(json.dumps({"ok": res.ok, "deltas_normalized": res.deltas, "failures": res.failures}, indent=2))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
