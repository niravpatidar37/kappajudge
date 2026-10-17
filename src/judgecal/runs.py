"""Resumable experiment I/O: append-only JSONL result files keyed by item id."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from judgecal.llm import QuotaExhausted

EXIT_QUOTA = 75  # EX_TEMPFAIL: resume later


def run_resumable(main: Callable[[], None]) -> None:
    """Run an experiment entrypoint; on a daily-quota 429, print a clear status and exit 75 (resume later)."""
    try:
        main()
    except QuotaExhausted as exc:
        cmd = " ".join(["python", *sys.argv])
        print(
            json.dumps(
                {
                    "status": "quota_exhausted",
                    "model": exc.model,
                    "retry_after_s": exc.retry_after_s,
                    "resume": cmd,
                    "note": "finished items are checkpointed; rerun the same command later",
                }
            ),
            flush=True,
        )
        sys.exit(EXIT_QUOTA)


def done_ids(path: Path, key: str = "item_id") -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if key in row and "error" not in row:  # failed items are retried on the next run
                out.add(str(row[key]))
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
