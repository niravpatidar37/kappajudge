"""HTTP controls for the JSON API and every response: API-key auth, token-bucket rate limit,
request-body cap, and security headers.

The labeling UI itself sits behind IAP in the cloud (Google identity before any request reaches the
container). The API adds its own key check on top, so a mis-set ingress/IAP setting alone does not
open labeling data. Keys are compared in constant time and never logged (only a short SHA-256 tag).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_KEY_HEADER = "x-api-key"  # not Authorization: IAP uses that header for its own OIDC token


def key_tag(key: str) -> str:
    """Short non-reversible tag for logs and rate-limit buckets (never the key itself)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def check_api_key(presented: str | None, valid_keys: Iterable[str]) -> str | None:
    """Return the matching key's tag, or None. Compares against every key in constant time."""
    if not presented:
        return None
    match: str | None = None
    for k in valid_keys:
        if hmac.compare_digest(presented.encode("utf-8"), k.encode("utf-8")):
            match = key_tag(k)
    return match


class TokenBucketLimiter:
    """Per-key token bucket: `burst` tokens, refilled at `per_min` / 60 tokens per second.

    Per instance (in memory); with N Cloud Run instances the effective limit is up to N x per key.
    """

    def __init__(self, per_min: float, burst: int, max_keys: int = 10000) -> None:
        self.rate = per_min / 60.0
        self.burst = float(burst)
        self.max_keys = max_keys
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, bucket: str, now: float | None = None) -> tuple[bool, int]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._state.get(bucket, (self.burst, t))
            tokens = min(self.burst, tokens + (t - last) * self.rate)
            if tokens >= 1.0:
                self._state[bucket] = (tokens - 1.0, t)
                allowed, retry = True, 0
            else:
                self._state[bucket] = (tokens, t)
                allowed, retry = False, max(1, math.ceil((1.0 - tokens) / self.rate))
            if len(self._state) > self.max_keys:  # bound memory under key/IP churn
                self._state.pop(next(iter(self._state)))
        return allowed, retry


BASE_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]
# JSON endpoints: nothing may load or frame them, and nothing is cached by intermediaries.
STRICT_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cache-control", b"no-store"),
]
# Streamlit's own HTML needs inline scripts/styles, so its CSP only forbids framing (clickjacking).
UI_HEADERS: list[tuple[bytes, bytes]] = [(b"content-security-policy", b"frame-ancestors 'none'")]
STRICT_PREFIXES = ("/api/", "/healthz", "/readyz")


class SecurityHeadersMiddleware:
    """Pure ASGI middleware: adds security headers to every HTTP response (websockets untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        extra = STRICT_HEADERS if path.startswith(STRICT_PREFIXES) else UI_HEADERS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                present = {k.lower() for k, _ in headers}
                for k, v in (*BASE_HEADERS, *extra):
                    if k not in present:
                        headers.append((k, v))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
