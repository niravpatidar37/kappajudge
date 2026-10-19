"""JSON API + HTTP controls, without a database (db calls are replaced by in-memory fakes).

Each test pins one control so that removing it makes a test fail: auth, rate limit (key and failed-auth
IP buckets), body cap, validation without echo, labeler restriction, write kill switch, opaque 500s,
security headers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from judgecal import db
from judgecal.config import ConfigError, Settings, get_settings
from judgecal.security import SecurityHeadersMiddleware, TokenBucketLimiter, check_api_key

KEY = "k" * 32
OTHER = "o" * 32
RAG_SCORES = {"correctness": 3, "completeness": 2, "coherence": 4, "groundedness": 3, "safety": 2}


class FakeConn:
    def __init__(self) -> None:
        self.saved: dict[tuple[str, str, int], dict[str, Any]] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def execute(self, *_a: Any, **_k: Any) -> Any:
        return self


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeConn:
    conn = FakeConn()

    @contextmanager
    def connect(dsn: str | None = None) -> Iterator[FakeConn]:
        yield conn

    item = {"item_id": "p1-q001-hybrid", "source": "p1", "kind": "rag", "task": "t", "response": "r"}

    def save_label(c: Any, **kw: Any) -> None:
        db.save_label.__wrapped__(c, **kw)  # type: ignore[attr-defined]
        conn.saved[(kw["item_id"], kw["labeler_id"], kw["pass_no"])] = {**kw, "created_at": "now"}

    real_save = db.save_label
    save_label.__wrapped__ = real_save  # type: ignore[attr-defined]
    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(db, "schema_status", lambda c: {"applied": ["0001"], "expected": ["0001"], "pending": []})
    monkeypatch.setattr(db, "queue_preview", lambda c, lab, src, n: [item][:n])
    monkeypatch.setattr(db, "label_counts", lambda c: {"items": 1, "first_pass": 0, "second_pass": 0})
    monkeypatch.setattr(db, "get_item", lambda c, i: item if i == item["item_id"] else None)
    monkeypatch.setattr(db, "get_label", lambda c, i, lab, p: conn.saved.get((i, lab, p)))
    monkeypatch.setattr(db, "save_label", save_label)
    return conn


def make_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    base = {"API_KEYS": f"{KEY},{OTHER}", "RATE_LIMIT_PER_MIN": "60", "RATE_LIMIT_BURST": "30"}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    from judgecal.api import Api

    async def page(_: Any) -> PlainTextResponse:
        return PlainTextResponse("ui")

    app = Starlette(
        routes=[*Api().routes(), Route("/", page)],
        middleware=[Middleware(SecurityHeadersMiddleware)],
    )
    return TestClient(app, raise_server_exceptions=False)


def h(key: str = KEY) -> dict[str, str]:
    return {"X-API-Key": key}


def test_auth_required_wrong_key_rejected_and_both_rotation_keys_accepted(
    monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn
) -> None:
    c = make_client(monkeypatch)
    assert c.get("/api/v1/status").status_code == 401
    assert c.get("/api/v1/status", headers=h("x" * 32)).status_code == 401
    assert c.get("/api/v1/status", headers={"Authorization": f"Bearer {KEY}"}).status_code == 401
    assert c.get("/api/v1/status", headers=h()).status_code == 200
    assert c.get("/api/v1/status", headers=h(OTHER)).status_code == 200


def test_api_disabled_without_keys(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch, API_KEYS="")
    r = c.get("/api/v1/status", headers=h())
    assert r.status_code == 503 and r.json()["error"] == "api_disabled"


def test_short_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", "short")
    with pytest.raises(ConfigError):
        get_settings()


def test_rate_limit_429_with_retry_after_per_key(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch, RATE_LIMIT_BURST="3", RATE_LIMIT_PER_MIN="6")
    codes = [c.get("/api/v1/status", headers=h()).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.get("/api/v1/status", headers=h())
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert c.get("/api/v1/status", headers=h(OTHER)).status_code == 200  # separate bucket per key


def test_failed_auth_is_throttled_per_ip(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch, RATE_LIMIT_BURST="6", RATE_LIMIT_PER_MIN="6")
    codes = [c.get("/api/v1/status", headers=h("g" * 32)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_token_bucket_refills() -> None:
    lim = TokenBucketLimiter(per_min=60, burst=1)
    assert lim.take("a", now=0.0) == (True, 0)
    allowed, retry = lim.take("a", now=0.1)
    assert not allowed and retry == 1
    assert lim.take("a", now=1.2)[0]


def test_check_api_key_constant_time_any_match() -> None:
    assert check_api_key(None, [KEY]) is None
    assert check_api_key(KEY[:-1], [KEY]) is None
    assert check_api_key(KEY, [OTHER, KEY]) is not None


def test_oversize_body_413_declared_and_streamed(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch, MAX_BODY_BYTES="2048")
    big = b'{"note": "' + b"a" * 5000 + b'"}'
    r = c.post("/api/v1/labels", headers={**h(), "content-type": "application/json"}, content=big)
    assert r.status_code == 413

    def gen() -> Iterator[bytes]:  # chunked: no Content-Length header
        yield big[:1000]
        yield big[1000:]

    r = c.post("/api/v1/labels", headers={**h(), "content-type": "application/json"}, content=gen())
    assert r.status_code == 413


def _post(c: TestClient, body: dict[str, Any]) -> Any:
    return c.post("/api/v1/labels", headers=h(), json=body)


def test_label_validation_422_without_echoing_input(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    marker = "<script>ECHO-ME-NOT</script>"
    bad = {"item_id": "p1-q001-hybrid", "labeler_id": "smoke-x", "scores": {**RAG_SCORES, "correctness": 9}}
    r = _post(c, {**bad, "note": marker})
    assert r.status_code == 422 and marker not in r.text
    r = _post(c, {**bad, "scores": {"correctness": marker}})
    assert r.status_code == 422 and "ECHO-ME-NOT" not in r.text
    assert _post(c, {**bad, "scores": {"correctness": 3}}).status_code == 422  # missing criteria
    assert _post(c, {**bad, "labeler_id": "bad id;"}).status_code == 422
    assert _post(c, {**bad, "extra": 1}).status_code == 422
    r = c.post("/api/v1/labels", headers={**h(), "content-type": "application/json"}, content=b"{not json")
    assert r.status_code == 422 and r.json()["error"] == "invalid_json"
    assert _post(c, {**bad, "scores": RAG_SCORES, "item_id": "nope"}).status_code == 404
    assert fake_db.saved == {}


def test_api_cannot_write_or_read_human_labelers(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    r = _post(c, {"item_id": "p1-q001-hybrid", "labeler_id": "owner", "scores": RAG_SCORES})
    assert r.status_code == 403
    r = c.get("/api/v1/labels", headers=h(), params={"item_id": "p1-q001-hybrid", "labeler_id": "owner"})
    assert r.status_code == 403
    assert fake_db.saved == {}


def test_label_roundtrip_and_write_kill_switch(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    body = {"item_id": "p1-q001-hybrid", "labeler_id": "smoke-probe", "scores": RAG_SCORES, "note": "n"}
    r = _post(c, body)
    assert r.status_code == 201, r.text
    got = c.get("/api/v1/labels", headers=h(), params={"item_id": "p1-q001-hybrid", "labeler_id": "smoke-probe"})
    assert got.status_code == 200 and got.json()["scores"] == RAG_SCORES
    monkeypatch.setenv("LABEL_WRITES_ENABLED", "false")
    fake_db.saved.clear()
    r = _post(c, body)
    assert r.status_code == 503 and r.json()["error"] == "writes_disabled"
    assert fake_db.saved == {}


def test_internal_errors_are_opaque(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("connection to postgresql://u:secret@db failed")

    monkeypatch.setattr(db, "connect", boom)
    c = make_client(monkeypatch)
    r = c.get("/api/v1/status", headers=h())
    assert r.status_code == 500 and r.json()["error"] == "internal_error" and r.json()["request_id"]
    assert "secret" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text


def test_unknown_api_route_is_json_404(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    r = c.get("/api/v1/nope", headers=h())
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_queue_schema_and_query_validation(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    r = c.get("/api/v1/queue", headers=h(), params={"labeler_id": "smoke-reader", "source": "p1", "limit": "5"})
    assert r.status_code == 200 and r.json()["items"][0]["item_id"] == "p1-q001-hybrid"
    assert c.get("/api/v1/queue", headers=h(), params={"labeler_id": "x;", "limit": "5"}).status_code == 422
    assert c.get("/api/v1/queue", headers=h(), params={"labeler_id": "ab", "limit": "500"}).status_code == 422
    assert c.get("/api/v1/queue", headers=h(), params={"labeler_id": "ab", "source": "zz"}).status_code == 422


def test_security_headers_on_api_errors_and_ui(monkeypatch: pytest.MonkeyPatch, fake_db: FakeConn) -> None:
    c = make_client(monkeypatch)
    for r in (c.get("/api/v1/status", headers=h()), c.get("/api/v1/status")):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
    ui = c.get("/")
    assert ui.headers["x-frame-options"] == "DENY" and ui.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert "server" not in ui.headers


def test_settings_defaults_are_safe() -> None:
    s = Settings()
    assert s.api_keys == () and not s.llm_recorded_mode and s.label_writes_enabled
