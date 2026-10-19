"""JSON API (/api/v1/*) next to the Streamlit UI: queue listing, label write/read for API labelers,
eval-run history, and service status. Used by the smoke test and for programmatic access.

Controls (each has a test that fails if it is removed):
- X-API-Key required (constant-time compare, comma-separated keys for rotation); no keys = API off (503).
- Token-bucket rate limit per key, and per client IP for failed auth (429 + Retry-After).
- Request bodies capped at MAX_BODY_BYTES (413); JSON validated with Pydantic (422, input not echoed).
- The API may only write/read labels of labeler ids `api-*` / `smoke-*`; human labels come from the
  IAP-authenticated UI, so an API key cannot impersonate a labeler or read their judgments.
- LABEL_WRITES_ENABLED=false (kill switch) makes writes return 503.
- Errors are JSON with a request id; stack traces and driver messages never reach the client.
All database work runs in a worker thread so the Streamlit event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from judgecal import db
from judgecal.config import Settings, get_settings
from judgecal.rubric import load_rubric
from judgecal.security import API_KEY_HEADER, TokenBucketLimiter, check_api_key

log = logging.getLogger("judgecal.api")

API_LABELER_PATTERN = r"^(api|smoke)-[A-Za-z0-9_.\-]{1,34}$"
ITEM_ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,200}$"
SOURCES = ("p1", "helpsteer2")
RUN_KINDS = ("release_gate", "calibration", "smoke")
MAX_TEXT = 4000

Handler = Callable[[Request, str], Awaitable[Response]]


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    labeler_id: str = Field(pattern=API_LABELER_PATTERN)
    pass_no: Literal[1, 2] = 1
    scores: dict[str, int] = Field(min_length=1, max_length=10)
    note: str = Field(default="", max_length=2000)


class QueueItemOut(BaseModel):
    item_id: str
    source: str
    kind: Literal["general", "rag"]
    task: str
    response: str


def _err(status: int, error: str, rid: str, headers: dict[str, str] | None = None, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": error, "request_id": rid, **extra}, status_code=status, headers=headers)


def _ok(body: Any, rid: str, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(body, default=str)), status_code=status, headers={"x-request-id": rid})


class Api:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        s = self.settings
        self.limiter = TokenBucketLimiter(s.rate_limit_per_min, s.rate_limit_burst)
        # Failed-auth attempts are throttled per client IP with a smaller burst (key guessing).
        self.auth_fail_limiter = TokenBucketLimiter(s.rate_limit_per_min, max(1, s.rate_limit_burst // 3))

    # ------------------------------------------------------------------ wrapper
    def endpoint(self, handler: Handler) -> Callable[[Request], Awaitable[Response]]:
        async def wrapped(request: Request) -> Response:
            rid = uuid.uuid4().hex[:16]
            s = self.settings
            if not s.api_keys:
                return _err(503, "api_disabled", rid, detail="no API_KEYS configured")
            tag = check_api_key(request.headers.get(API_KEY_HEADER), s.api_keys)
            if tag is None:
                ip = request.client.host if request.client else "unknown"
                allowed, retry = self.auth_fail_limiter.take(f"ip:{ip}")
                if not allowed:
                    return _err(429, "rate_limited", rid, headers={"Retry-After": str(retry)})
                log.info("api auth rejected", extra={"request_id": rid, "path": request.url.path})
                return _err(401, "unauthorized", rid, headers={"WWW-Authenticate": "ApiKey"})
            allowed, retry = self.limiter.take(f"key:{tag}")
            if not allowed:
                return _err(429, "rate_limited", rid, headers={"Retry-After": str(retry)})
            t0 = time.perf_counter()
            try:
                resp = await handler(request, rid)
            except Exception:
                log.exception("api handler failed", extra={"request_id": rid, "path": request.url.path})
                resp = _err(500, "internal_error", rid)
            resp.headers["x-request-id"] = rid
            # One event per authenticated request: feeds the API latency SLO (log-based metric). No IPs, no bodies.
            log.info(
                "api request",
                extra={
                    "event": "api_request",
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": resp.status_code,
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "key_tag": tag,
                },
            )
            return resp

        return wrapped

    async def _read_json(self, request: Request, rid: str) -> tuple[Any, Response | None]:
        limit = self.settings.max_body_bytes
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            return None, _err(413, "body_too_large", rid, max_bytes=limit)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                return None, _err(413, "body_too_large", rid, max_bytes=limit)
        if "application/json" not in request.headers.get("content-type", ""):
            return None, _err(415, "unsupported_media_type", rid, expected="application/json")
        try:
            return json.loads(body), None
        except ValueError:
            return None, _err(422, "invalid_json", rid)

    # ------------------------------------------------------------------ handlers
    async def status(self, request: Request, rid: str) -> Response:
        def work() -> dict[str, Any]:
            with db.connect() as conn:
                return db.schema_status(conn)

        schema = await asyncio.to_thread(work)
        s = get_settings()  # kill switches are read per request
        return _ok(
            {
                "service": "kappajudge",
                "env": s.env,
                "llm_calls_enabled": s.llm_calls_enabled,
                "label_writes_enabled": s.label_writes_enabled,
                "schema": schema,
            },
            rid,
        )

    async def queue(self, request: Request, rid: str) -> Response:
        q = request.query_params
        labeler = q.get("labeler_id", "")
        source = q.get("source") or None
        limit_raw = q.get("limit", "5")
        if not db.LABELER_RE.match(labeler):
            return _err(422, "invalid_labeler_id", rid)
        if source is not None and source not in SOURCES:
            return _err(422, "invalid_source", rid, allowed=list(SOURCES))
        if not limit_raw.isdigit() or not 1 <= int(limit_raw) <= 50:
            return _err(422, "invalid_limit", rid, allowed="1..50")

        def work() -> tuple[list[dict[str, Any]], dict[str, int]]:
            with db.connect() as conn:
                return db.queue_preview(conn, labeler, source, int(limit_raw)), db.label_counts(conn)

        rows, counts = await asyncio.to_thread(work)
        items = [
            QueueItemOut(
                item_id=r["item_id"],
                source=r["source"],
                kind=r["kind"],
                task=r["task"][:MAX_TEXT],
                response=r["response"][:MAX_TEXT],
            ).model_dump()
            for r in rows
        ]
        return _ok({"labeler_id": labeler, "source": source, "items": items, "counts": counts}, rid)

    async def labels(self, request: Request, rid: str) -> Response:
        if request.method == "POST":
            return await self._write_label(request, rid)
        q = request.query_params
        item_id, labeler, pass_raw = q.get("item_id", ""), q.get("labeler_id", ""), q.get("pass_no", "1")
        if not re.match(ITEM_ID_PATTERN, item_id) or pass_raw not in ("1", "2"):
            return _err(422, "invalid_query", rid)
        if not re.match(API_LABELER_PATTERN, labeler):
            return _err(403, "labeler_not_allowed", rid, detail="the API reads only api-*/smoke-* labelers")

        def work() -> dict[str, Any] | None:
            with db.connect() as conn:
                return db.get_label(conn, item_id, labeler, int(pass_raw))

        row = await asyncio.to_thread(work)
        if row is None:
            return _err(404, "not_found", rid)
        return _ok(row, rid)

    async def _write_label(self, request: Request, rid: str) -> Response:
        data, problem = await self._read_json(request, rid)
        if problem is not None:
            return problem
        lab = data.get("labeler_id") if isinstance(data, dict) else None
        if isinstance(lab, str) and db.LABELER_RE.match(lab) and not re.match(API_LABELER_PATTERN, lab):
            return _err(403, "labeler_not_allowed", rid, detail="the API writes only api-*/smoke-* labelers")
        try:
            body = LabelIn.model_validate(data)
        except ValidationError as exc:
            # Location + message only: never echo the submitted input back.
            errs = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()][:10]
            return _err(422, "validation_error", rid, errors=errs)
        if not get_settings().label_writes_enabled:
            return _err(503, "writes_disabled", rid, detail="LABEL_WRITES_ENABLED=false (kill switch)")

        def work() -> tuple[int, dict[str, Any]]:
            with db.connect() as conn:
                item = db.get_item(conn, body.item_id)
                if item is None:
                    return 404, {"error": "unknown_item"}
                rubric = load_rubric(_release_rubric_version())
                crits = rubric.names(item["kind"])
                if sorted(body.scores) != sorted(crits):
                    return 422, {"error": "validation_error", "errors": [{"loc": ["scores"], "msg": f"need {crits}"}]}
                try:
                    db.save_label(
                        conn,
                        item_id=body.item_id,
                        labeler_id=body.labeler_id,
                        pass_no=body.pass_no,
                        scores=body.scores,
                        note=body.note,
                        valid={k: rubric.criterion(k).max_score for k in crits},
                    )
                except ValueError as exc:
                    return 422, {"error": "validation_error", "errors": [{"loc": ["scores"], "msg": str(exc)}]}
                saved = db.get_label(conn, body.item_id, body.labeler_id, body.pass_no)
                return 201, saved or {}

        status, payload = await asyncio.to_thread(work)
        if status != 201:
            return _err(status, payload.pop("error"), rid, **payload)
        return _ok(payload, rid, status=201)

    async def runs(self, request: Request, rid: str) -> Response:
        q = request.query_params
        kind, limit_raw = q.get("kind", "release_gate"), q.get("limit", "20")
        if kind not in RUN_KINDS or not limit_raw.isdigit() or not 1 <= int(limit_raw) <= 100:
            return _err(422, "invalid_query", rid, kinds=list(RUN_KINDS))

        def work() -> list[dict[str, Any]]:
            with db.connect() as conn:
                return db.list_runs(conn, kind, int(limit_raw))

        rows = await asyncio.to_thread(work)
        return _ok({"kind": kind, "runs": rows}, rid)

    async def not_found(self, request: Request, rid: str) -> Response:
        return _err(404, "not_found", rid)

    def routes(self) -> list[Route]:
        every = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        return [
            Route("/api/v1/status", self.endpoint(self.status), methods=["GET"]),
            Route("/api/v1/queue", self.endpoint(self.queue), methods=["GET"]),
            Route("/api/v1/labels", self.endpoint(self.labels), methods=["GET", "POST"]),
            Route("/api/v1/runs", self.endpoint(self.runs), methods=["GET"]),
            Route("/api/{rest:path}", self.endpoint(self.not_found), methods=every),
        ]


def _release_rubric_version() -> str:
    from judgecal.config import REPO_ROOT

    cfg = json.loads((REPO_ROOT / "judge_release.json").read_text(encoding="utf-8"))
    return str(cfg["rubric_version"])
