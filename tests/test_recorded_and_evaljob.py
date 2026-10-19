"""Recorded LLM mode (CI/compose smoke) and the scheduled eval job's preflight, drift verdict and failure events.

The recorded fixtures in tests/fixtures/recorded_llm are real model outputs captured from the original
release-gate runs (scripts/export_recorded_llm.py); replaying them is deterministic and costs $0.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from judgecal.config import REPO_ROOT, ConfigError, get_settings

FIXTURES = REPO_ROOT / "tests" / "fixtures"
WEAK_PROMPT = REPO_ROOT / "scripts" / "demo" / "rag_answer_weakened.md"


def _last_json(text: str) -> dict[str, object]:
    """The job prints one progress line per item, then a JSON summary."""
    start = text.index("\n{") + 1 if not text.startswith("{") else 0
    out: dict[str, object] = json.loads(text[start:])
    return out


def _no_network(_: httpx.Request) -> httpx.Response:
    raise AssertionError("recorded mode must never reach the network")


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LLM_RECORDED_MODE", "true")
    monkeypatch.setenv("LLM_RECORDED_DIR", str(FIXTURES / "recorded_llm"))
    monkeypatch.setenv("ENV", "ci")
    monkeypatch.setenv("LLM_SPEND_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PG_HOST", raising=False)
    return tmp_path


@pytest.mark.parametrize("env", ["prod", "production", "PROD"])
def test_recorded_mode_is_refused_in_prod(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("LLM_RECORDED_MODE", "true")
    monkeypatch.setenv("ENV", env)
    with pytest.raises(ConfigError):
        get_settings()
    from judgecal.llm import LLMClient

    with pytest.raises(ConfigError):
        LLMClient("groq")


def test_recorded_miss_raises_without_network(recorded: Path) -> None:
    from judgecal.llm import LLMClient, RecordingMissing

    client = LLMClient("groq", transport=httpx.MockTransport(_no_network))
    with pytest.raises(RecordingMissing):
        client.complete([{"role": "user", "content": "never recorded"}], "qwen/qwen3.8-27b")


def test_recorded_eval_replays_baseline_exactly_and_passes_gate(
    recorded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from judgecal.evalrun import main

    assert main(["--out", str(recorded / "runs")]) == 0
    out = _last_json(capsys.readouterr().out)
    base = json.loads((REPO_ROOT / "eval_runs" / "baseline.json").read_text(encoding="utf-8"))
    assert out["kind"] == "smoke" and out["usd"] == 0.0
    assert out["summary"] == base["summary"] and out["gate_ok"] is True


def test_drift_is_logged_and_fails_with_flag(
    recorded: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    from judgecal.evalrun import EXIT_DRIFT, main

    with caplog.at_level(logging.INFO, logger="judgecal.evalrun"):
        rc = main(["--out", str(recorded / "runs"), "--prompt", str(WEAK_PROMPT), "--fail-on-drift"])
    assert rc == EXIT_DRIFT
    out = _last_json(capsys.readouterr().out)
    assert out["gate_ok"] is False and all(f.startswith("regression:") for f in out["gate_failures"])
    events = [getattr(r, "event", None) for r in caplog.records]
    assert "eval_gate_drift" in events


def test_kill_switch_stops_eval_job_and_logs_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from judgecal.evalrun import main
    from judgecal.llm import LLMDisabled

    monkeypatch.setenv("LLM_CALLS_ENABLED", "false")
    monkeypatch.setenv("LLM_RECORDED_MODE", "false")
    monkeypatch.setenv("JUDGECAL_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("LLM_SPEND_LEDGER", str(tmp_path / "ledger.jsonl"))
    with caplog.at_level(logging.INFO, logger="judgecal.evalrun"), pytest.raises(LLMDisabled):
        main(["--out", str(tmp_path / "runs")])
    assert any(getattr(r, "event", None) == "eval_run_failed" for r in caplog.records)


def test_preflight_needs_database_and_reports_checks(recorded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from judgecal.evalrun import main

    assert main(["--smoke"]) == 1  # no DATABASE_URL: the job could not store its run
    out = _last_json(capsys.readouterr().out)
    assert out["ok"] is False and out["checks"]["judge_config_matches_baseline"] is True
    assert out["checks"]["database"] is False and out["checks"]["eval_items"] == 8
