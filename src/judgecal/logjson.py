"""Structured JSON logging with PII redaction.

Every log record is emitted as one JSON line. Message text and string extras pass through
`redact_pii`, which masks emails, phone numbers, SSNs, card-like numbers and bearer/API keys.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:gsk|sk|hf|ghp|gho|github_pat)_[A-Za-z0-9_\-]{10,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}"), "Bearer [REDACTED_KEY]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_NUMBER]"),
    (re.compile(r"(?<!\d)(?:\+?1[ .\-]?)?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?!\d)"), "[REDACTED_PHONE]"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@"), r"\1[REDACTED]@"),
]


def redact_pii(text: str) -> str:
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


class JsonFormatter(logging.Formatter):
    _SKIP = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "severity": record.levelname,  # Cloud Logging maps this key to LogEntry.severity
            "logger": record.name,
            "msg": redact_pii(record.getMessage()),
        }
        for k, v in record.__dict__.items():
            if k in self._SKIP or k.startswith("_"):
                continue
            payload[k] = redact_pii(v) if isinstance(v, str) else v
        if record.exc_info:
            payload["exc"] = redact_pii(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
