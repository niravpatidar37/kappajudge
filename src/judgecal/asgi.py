"""ASGI entrypoint: the Streamlit UI plus /healthz (liveness), /readyz (dependency check) and the
JSON API (/api/v1/*), with security headers on every HTTP response.

Run: uvicorn judgecal.asgi:app --host 0.0.0.0 --port 8504 --timeout-graceful-shutdown 20
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from streamlit.starlette import App

from judgecal.api import Api
from judgecal.config import get_settings
from judgecal.logjson import setup_logging
from judgecal.security import SecurityHeadersMiddleware

# Settings are validated at import: unsafe config (short API key, recorded LLM mode in prod) stops startup.
setup_logging(get_settings().log_level)


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _db_ready() -> dict[str, Any]:
    from judgecal import db

    try:
        with db.connect() as conn:
            if not db.ping(conn):
                return {"database": False}
            schema = db.schema_status(conn)
            return {"database": True, "migrations_pending": schema["pending"]}
    except Exception:
        return {"database": False}


async def readyz(_: Request) -> JSONResponse:
    try:
        state = await asyncio.wait_for(asyncio.to_thread(_db_ready), timeout=4.0)
    except TimeoutError:
        state = {"database": False}
    ok = bool(state.get("database")) and state.get("migrations_pending") == []
    return JSONResponse({"status": "ready" if ok else "not_ready", **state}, status_code=200 if ok else 503)


app = App(
    Path(__file__).with_name("webapp") / "main.py",
    routes=[Route("/healthz", healthz), Route("/readyz", readyz), *Api().routes()],
    middleware=[Middleware(SecurityHeadersMiddleware)],
)
