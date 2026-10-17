"""Run every pending LLM experiment in priority order, failing fast on daily quotas.

Each step is resumable (finished items are checkpointed, every LLM call is cached by content hash).
When a step exits 75 (daily quota exhausted for a model), later steps that need that model are
skipped in this pass and the command to resume is printed. Re-run this script later to continue.

  python scripts/run_pending.py            # all steps
  python scripts/run_pending.py --only test
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
QWEN, GPTOSS = "qwen/qwen3.8-27b", "openai/gpt-oss-20b"
DEMO = ROOT.parent / "llm-judge-calibration-demo"  # git worktree of the demo-regression branch


def _final_rubric() -> str:
    return str(json.loads((ROOT / "judge_release.json").read_text(encoding="utf-8"))["rubric_version"])


def steps() -> list[tuple[str, set[str], list[str], dict[str, str]]]:
    r = _final_rubric()
    demo_env = {"JUDGECAL_ROOT": str(DEMO), "JUDGECAL_CACHE_DIR": str(ROOT / "data" / "cache")}
    return [
        (
            "dev-subset",
            {QWEN},
            [
                PY,
                "scripts/run_calibration.py",
                "--rubric",
                r,
                "--split",
                "dev",
                "--ids-from",
                "results/dev_subset_ids.jsonl",
            ],
            {},
        ),
        ("gate-baseline", {QWEN, GPTOSS}, [PY, "-m", "judgecal.evalrun"], {}),
        (
            "test",
            {QWEN},
            [
                PY,
                "scripts/run_calibration.py",
                "--rubric",
                r,
                "--split",
                "test",
                "--ids-from",
                "results/test_subset_ids.jsonl",
            ],
            {},
        ),
        (
            "demo",
            {QWEN, GPTOSS},
            [
                PY,
                "-m",
                "judgecal.evalrun",
                "--prompt",
                str(DEMO / "prompts" / "rag_answer.md"),
                "--out",
                str(DEMO / "eval_runs"),
            ],
            demo_env,
        ),
        ("selfpref", {QWEN, GPTOSS}, [PY, "scripts/bias_selfpref.py", "--stage", "judge", "--n", "10"], {}),
        (
            "order",
            {QWEN},
            [
                PY,
                "scripts/run_calibration.py",
                "--rubric",
                r,
                "--split",
                "dev",
                "--order",
                "score_first",
                "--ids-from",
                "results/order_experiment_ids.jsonl",
            ],
            {},
        ),
        ("length", {QWEN}, [PY, "scripts/bias_length.py", "--rubric", r], {}),
        ("position", {GPTOSS}, [PY, "scripts/bias_position.py", "--model", GPTOSS, "--n", "40"], {}),
        ("dev-rest", {QWEN}, [PY, "scripts/run_calibration.py", "--rubric", r, "--split", "dev"], {}),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    exhausted: set[str] = set()
    status: dict[str, str] = {}
    for name, models, cmd, extra_env in steps():
        if args.only and name not in args.only:
            continue
        if name == "demo" and not DEMO.is_dir():
            status[name] = "skipped (demo worktree missing)"
            continue
        if models & exhausted:
            status[name] = f"skipped (quota exhausted: {sorted(models & exhausted)})"
            continue
        env = {**os.environ, **extra_env}
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        if proc.returncode == 75:
            info = json.loads(tail[0]) if tail[0].startswith("{") else {}
            exhausted.add(str(info.get("model") or "") or next(iter(models)))
            status[name] = f"quota exhausted ({info.get('model')}); resume: {' '.join(cmd)}"
        elif proc.returncode != 0:
            status[name] = f"failed rc={proc.returncode}: {(proc.stderr or '')[-300:]}"
        else:
            status[name] = "done"
            if name == "gate-baseline" and not (ROOT / "eval_runs" / "baseline.json").is_file():
                shutil.copyfile(ROOT / "eval_runs" / "latest.json", ROOT / "eval_runs" / "baseline.json")
    print(json.dumps(status, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
