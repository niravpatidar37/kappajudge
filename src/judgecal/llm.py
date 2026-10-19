"""Provider-agnostic LLM client: Groq (OpenAI-compatible), optional Anthropic, and a fake for tests.

Every paid call goes through the spend ledger (worst-case estimate checked before the call, actual
usage recorded after it). Responses are cached on disk by a content hash of (provider, model,
messages, params) so reruns are free and every experiment script is resumable. 429/5xx responses
are retried honoring Retry-After with exponential backoff and jitter. Structured output is
validated with Pydantic and retried with the validation error on failure. The kill switch
LLM_CALLS_ENABLED=false refuses every uncached call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from judgecal.config import get_settings, load_env
from judgecal.spend import BudgetExceeded, SpendLedger, estimate_tokens, price_for

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("judgecal.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Placeholder: verify a real model id via GET https://api.anthropic.com/v1/models before use.
CLAUDE_MODEL_ID = os.environ.get("CLAUDE_MODEL_ID", "CLAUDE_MODEL_ID_PLACEHOLDER")


class LLMError(RuntimeError):
    pass


class LLMDisabled(LLMError):
    """Kill switch is on (LLM_CALLS_ENABLED=false) and the result is not cached."""


class StructuredOutputError(LLMError):
    pass


class RecordingMissing(LLMError):
    """Recorded LLM mode is on and no recorded output exists for this exact request (never goes to the network)."""


class QuotaExhausted(LLMError):
    """Provider daily quota (tokens/requests per day) is exhausted: fail fast, checkpoint, resume later."""

    def __init__(self, model: str, retry_after_s: float | None) -> None:
        self.model = model
        self.retry_after_s = retry_after_s
        hint = f" (provider suggests retrying in ~{retry_after_s:.0f}s)" if retry_after_s else ""
        super().__init__(f"daily quota exhausted for {model}{hint}; resume later")


class ProviderJSONError(LLMError):
    """Provider-side schema validation failed (Groq 400 json_validate_failed); retryable as a bad sample."""


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    usd: float
    cached: bool
    latency_ms: float
    model: str
    provider: str


def cache_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


FakeResponder = Callable[[list[dict[str, str]], str], str]


class LLMClient:
    def __init__(
        self,
        provider: str = "groq",
        ledger: SpendLedger | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        timeout_s: float = 90.0,
        max_retries: int = 8,
        max_wait_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        fake_responder: FakeResponder | None = None,
    ) -> None:
        load_env()
        settings = get_settings()  # raises ConfigError if recorded mode is requested with ENV=prod
        self.provider = provider
        self.ledger = ledger or SpendLedger()
        self.recorded = settings.llm_recorded_mode
        if self.recorded:
            # Read-only replay of real recorded outputs; the directory may be a read-only mount.
            self.cache_dir = settings.llm_recorded_dir / "llm"
        else:
            self.cache_dir = (cache_dir or settings.cache_dir) / "llm"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self.max_retries = max_retries
        self.max_wait_s = max_wait_s
        self.fake_responder = fake_responder
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    # ------------------------------------------------------------------ public API
    def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        cache_salt: str = "",
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "schema": response_schema,
            "reasoning_effort": reasoning_effort,
            "salt": cache_salt,
        }
        key = cache_key(payload)
        cache_file = self.cache_dir / key[:2] / f"{key}.json"
        if (self.use_cache or self.recorded) and cache_file.is_file():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return LLMResult(
                data["text"], data["input_tokens"], data["output_tokens"], 0.0, True, 0.0, model, self.provider
            )
        if self.recorded:
            raise RecordingMissing(f"LLM_RECORDED_MODE: no recorded output for request {key[:12]} ({model})")

        if not get_settings().llm_calls_enabled:
            raise LLMDisabled("LLM_CALLS_ENABLED=false (kill switch); refusing uncached model call")

        price = price_for(self.provider, model)
        prompt_tokens = sum(estimate_tokens(m["content"]) + 8 for m in messages)
        worst_case = price.cost(prompt_tokens, max_tokens)
        if price.input_per_m or price.output_per_m:
            self.ledger.check(worst_case)  # raises BudgetExceeded

        t0 = time.perf_counter()
        if self.provider == "groq":
            try:
                text, in_tok, out_tok = self._groq(
                    messages, model, max_tokens, temperature, response_schema, reasoning_effort
                )
            except ProviderJSONError:
                # Usage is not returned for rejected generations: book the worst case so the cap stays safe.
                if worst_case > 0:
                    self.ledger.record(self.provider, model, prompt_tokens, max_tokens, worst_case)
                raise
        elif self.provider == "anthropic":
            text, in_tok, out_tok = self._anthropic(messages, model, max_tokens, temperature)
        elif self.provider == "fake":
            if self.fake_responder is None:
                raise LLMError("fake provider needs a fake_responder")
            text = self.fake_responder(messages, model)
            in_tok, out_tok = prompt_tokens, estimate_tokens(text)
        else:
            raise LLMError(f"unknown provider {self.provider}")
        latency_ms = (time.perf_counter() - t0) * 1000
        usd = price.cost(in_tok, out_tok)
        if usd > 0:
            self.ledger.record(self.provider, model, in_tok, out_tok, usd)
        if self.use_cache:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "text": text,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "usd": usd,
                        "model": model,
                        "ts": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            tmp.replace(cache_file)
        return LLMResult(text, in_tok, out_tok, usd, False, latency_ms, model, self.provider)

    def complete_json(
        self,
        messages: list[dict[str, str]],
        model: str,
        schema_cls: type[T],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        attempts: int = 3,
        reasoning_effort: str | None = None,
        cache_salt: str = "",
        validator: Callable[[T], None] | None = None,
    ) -> tuple[T, list[LLMResult]]:
        """Call with a JSON schema, validate with Pydantic (+ optional extra validator), retry on failure."""
        schema = schema_cls.model_json_schema()
        msgs = list(messages)
        results: list[LLMResult] = []
        last_err = ""
        for attempt in range(attempts):
            try:
                res = self.complete(
                    msgs,
                    model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_schema=schema,
                    reasoning_effort=reasoning_effort,
                    cache_salt=f"{cache_salt}|attempt{attempt}",
                )
            except ProviderJSONError as exc:
                last_err = str(exc)
                continue
            results.append(res)
            try:
                obj = schema_cls.model_validate_json(strip_fences(res.text))
                if validator is not None:
                    validator(obj)
                return obj, results
            except (ValidationError, ValueError) as exc:
                last_err = str(exc)[:800]
                msgs = [
                    *messages,
                    {"role": "assistant", "content": res.text[:4000]},
                    {
                        "role": "user",
                        "content": "That output failed validation:\n"
                        + last_err
                        + "\nReturn ONLY corrected JSON that matches the schema.",
                    },
                ]
        raise StructuredOutputError(f"validation failed after {attempts} attempts: {last_err}")

    # ------------------------------------------------------------------ providers
    def _post_with_retry(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.post(url, headers=headers, json=body)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"transport error: {type(exc).__name__}") from exc
                time.sleep(delay + random.uniform(0, 1))  # nosec B311 - jitter, not crypto
                delay = min(delay * 2, 60)
                continue
            if r.status_code == 429 and is_daily_quota(r.text):
                # Daily (TPD/RPD) quota: waiting minutes per call only burns wall time. Fail fast; the
                # experiment scripts checkpoint every finished item and are resumed later.
                retry = parse_retry_after(r.headers.get("retry-after"), r.text)
                raise QuotaExhausted(str(body.get("model", "")), retry)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.max_retries:
                    raise LLMError(f"HTTP {r.status_code} after {attempt} retries")
                retry_after = parse_retry_after(r.headers.get("retry-after"), r.text)
                if retry_after is not None and retry_after > self.max_wait_s:
                    raise LLMError(f"HTTP {r.status_code}: retry-after {retry_after:.0f}s exceeds wait budget")
                # Honor the provider's Retry-After when given (TPM windows reset in seconds); otherwise back off
                # exponentially. Jitter avoids synchronized retries across concurrent experiment processes.
                base = retry_after if retry_after is not None else delay
                wait = base + random.uniform(0.5, 2.0)  # nosec B311
                log.info(
                    "rate limited; backing off",
                    extra={"status": r.status_code, "wait_s": round(wait, 1), "daily": "per day" in r.text},
                )
                time.sleep(min(wait, self.max_wait_s))
                delay = min(delay * 2, 60)
                continue
            if r.status_code == 400 and "json_validate_failed" in r.text:
                raise ProviderJSONError("provider rejected output: json_validate_failed")
            if r.status_code >= 400:
                # Do not echo request bodies (they may contain user text); keep the provider message.
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            result: dict[str, Any] = r.json()
            return result
        raise LLMError("unreachable")

    def _groq(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        schema: dict[str, Any] | None,
        reasoning_effort: str | None,
    ) -> tuple[str, int, int]:
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise LLMError("GROQ_API_KEY not set")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = reasoning_effort or "low"
            body["include_reasoning"] = False
        elif model.startswith("qwen/"):
            # Qwen thinking is disabled so the only reasoning is the visible, schema-ordered field.
            body["reasoning_effort"] = reasoning_effort or "none"
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": str(schema.get("title", "output"))[:60], "schema": schema, "strict": False},
            }
        data = self._post_with_retry(GROQ_URL, {"Authorization": f"Bearer {key}"}, body)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    def _anthropic(
        self, messages: list[dict[str, str]], model: str, max_tokens: int, temperature: float
    ) -> tuple[str, int, int]:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY not set; Claude adapter disabled")
        if model == "CLAUDE_MODEL_ID_PLACEHOLDER":
            raise BudgetExceeded("Claude model id is a placeholder; verify via GET /v1/models first")
        import anthropic  # optional dependency, not installed by default

        client: Any = anthropic.Anthropic()
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model=model, system=system, messages=rest, max_tokens=max_tokens, temperature=temperature
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return text, resp.usage.input_tokens, resp.usage.output_tokens


_TRY_AGAIN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")


def is_daily_quota(body: str) -> bool:
    b = body.lower()
    return "per day" in b or "(tpd)" in b or "(rpd)" in b


def parse_retry_after(value: str | None, body: str = "") -> float | None:
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = _TRY_AGAIN.search(body or "")
    if m and any(m.groups()):
        h, mi, s = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + s
    return None


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
