from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from judgecal import llm as llm_mod
from judgecal.llm import LLMClient, LLMDisabled, QuotaExhausted, StructuredOutputError, parse_retry_after
from judgecal.spend import BudgetExceeded, SpendLedger, price_for
from tests.conftest import write_ledger

MSG = [{"role": "user", "content": "x"}]


def _ok(text: str, pin: int = 10, pout: int = 5) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}},
    )


def test_global_cap_blocks(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    write_ledger(p, [{"project": "p2-research", "usd": 24.99}])
    led = SpendLedger(p, project="p4-judge", global_cap=25.0, project_cap=5.0)
    with pytest.raises(BudgetExceeded, match="global"):
        led.check(0.02)
    led.check(0.005)


def test_project_cap_blocks(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    write_ledger(p, [{"project": "p4-judge", "usd": 4.99}])
    led = SpendLedger(p, project="p4-judge", global_cap=25.0, project_cap=5.0)
    with pytest.raises(BudgetExceeded, match="project cap"):
        led.check(0.02)


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    p.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(BudgetExceeded):
        SpendLedger(p, project="p4-judge", global_cap=25.0, project_cap=5.0).check(0.01)


def test_unknown_paid_model_refused() -> None:
    with pytest.raises(BudgetExceeded):
        price_for("groq", "openai/gpt-oss-120b")  # not assigned to this project, so not priced here


def test_budget_checked_before_any_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    p = tmp_path / "l.jsonl"
    write_ledger(p, [{"project": "p4-judge", "usd": 5.0}])
    sent: list[httpx.Request] = []
    c = LLMClient(
        "groq",
        ledger=SpendLedger(p, "p4-judge", 25.0, 5.0),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(lambda r: sent.append(r) or _ok("hi")),
    )
    with pytest.raises(BudgetExceeded):
        c.complete(MSG, "qwen/qwen3.8-27b", max_tokens=100)
    assert sent == []


def test_cache_hit_is_free(tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        return _ok("hello", 1000, 1000)

    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    a = c.complete(MSG, "qwen/qwen3.8-27b")
    b = c.complete(MSG, "qwen/qwen3.8-27b")
    assert n["calls"] == 1 and b.cached and b.usd == 0.0 and a.text == b.text
    assert len(ledger.path.read_text().splitlines()) == 1
    assert a.usd == pytest.approx(price_for("groq", "qwen/qwen3.8-27b").cost(1000, 1000))


def test_kill_switch_refuses_uncached_calls(
    tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    sent: list[httpx.Request] = []
    c = LLMClient(
        "groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(lambda r: sent.append(r) or _ok("x"))
    )
    c.complete(MSG, "qwen/qwen3.8-27b")  # cached now
    monkeypatch.setenv("LLM_CALLS_ENABLED", "false")
    assert c.complete(MSG, "qwen/qwen3.8-27b").cached  # cache still served
    with pytest.raises(LLMDisabled):
        c.complete([{"role": "user", "content": "new"}], "qwen/qwen3.8-27b")
    assert len(sent) == 1


def test_429_retry_after_honored(tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    sleeps: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: sleeps.append(s))
    seq = [httpx.Response(429, headers={"retry-after": "7"}), _ok("ok")]
    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(lambda r: seq.pop(0)))
    assert c.complete(MSG, "qwen/qwen3.8-27b").text == "ok"
    assert sleeps and sleeps[0] >= 7


def test_retry_after_beyond_budget_fails_fast(
    tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    sleeps: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: sleeps.append(s))
    c = LLMClient(
        "groq",
        ledger=ledger,
        cache_dir=tmp_path,
        max_wait_s=8,
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "60"})),
    )
    with pytest.raises(llm_mod.LLMError, match="wait budget"):
        c.complete(MSG, "qwen/qwen3.8-27b")
    assert sleeps == []


def test_parse_retry_after_from_groq_body() -> None:
    assert parse_retry_after(None, "Please try again in 7m12.5s.") == pytest.approx(432.5)
    assert parse_retry_after("3", "") == 3.0
    assert parse_retry_after(None, "nothing") is None


class Out(BaseModel):
    value: int


def test_schema_retry_then_success(tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    seq = [_ok('{"value": "nope"}'), _ok('{"value": 3}')]
    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(lambda r: seq.pop(0)))
    obj, calls = c.complete_json(MSG, "qwen/qwen3.8-27b", Out)
    assert obj.value == 3 and len(calls) == 2


def test_provider_json_failure_is_retried_and_booked(
    tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    bad = httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "Failed to validate JSON"}})
    seq = [bad, _ok('{"value": 5}')]
    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(lambda r: seq.pop(0)))
    obj, _ = c.complete_json(MSG, "openai/gpt-oss-20b", Out, max_tokens=100)
    assert obj.value == 5
    rows = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["output_tokens"] == 100  # rejected call booked at worst case


def test_schema_failure_raises(tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(lambda r: _ok("garbage")))
    with pytest.raises(StructuredOutputError):
        c.complete_json(MSG, "qwen/qwen3.8-27b", Out, attempts=2)


def test_qwen_thinking_disabled_and_gptoss_reasoning_hidden(
    tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    bodies: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return _ok("x")

    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    c.complete(MSG, "qwen/qwen3.8-27b")
    c.complete(MSG, "openai/gpt-oss-20b")
    assert bodies[0]["reasoning_effort"] == "none"
    assert bodies[1]["reasoning_effort"] == "low" and bodies[1]["include_reasoning"] is False


def test_claude_adapter_off_without_key(tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = LLMClient("anthropic", ledger=ledger, cache_dir=tmp_path)
    with pytest.raises(Exception, match=r"ANTHROPIC_API_KEY|placeholder"):
        c.complete(MSG, "CLAUDE_MODEL_ID_PLACEHOLDER")


def test_daily_quota_fails_fast_without_sleeping(
    tmp_path: Path, ledger: SpendLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test")
    sleeps: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: sleeps.append(s))
    msg = (
        "Rate limit reached for model `qwen/qwen3.8-27b` on tokens per day (TPD): Limit 200000, "
        "Used 199800, Requested 1200. Please try again in 7m12s."
    )
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": msg}}, headers={"retry-after": "432"})

    c = LLMClient("groq", ledger=ledger, cache_dir=tmp_path, max_wait_s=1800, transport=httpx.MockTransport(handler))
    with pytest.raises(QuotaExhausted) as ei:
        c.complete(MSG, "qwen/qwen3.8-27b")
    assert calls["n"] == 1 and sleeps == [] and ei.value.retry_after_s == 432
