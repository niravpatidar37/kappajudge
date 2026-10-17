from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Never load the real shared secrets file in tests; every LLM call is mocked.
os.environ["PORTFOLIO_ENV_FILE"] = str(Path(__file__).parent / "does-not-exist.env")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from judgecal.spend import SpendLedger


def write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "ledger.jsonl", project="p4-judge", global_cap=25.0, project_cap=5.0)


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CALLS_ENABLED", "true")
