"""ASGI entrypoint: the Streamlit UI plus /healthz (liveness) and /readyz (dependency check).

Run: uvicorn judgecal.asgi:app --host 0.0.0.0 --port 8504 --timeout-graceful-shutdown 20
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from streamlit.starlette import App

from judgecal.config import get_settings
from judgecal.logjson import setup_logging

setup_logging(get_settings().log_level)


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _db_ready() -> bool:
    from judgecal import db

    try:
        with db.connect() as conn:
            if not db.ping(conn):
                return False
            row = conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()
            return bool(row and int(row["n"]) >= 1)
    except Exception:
        return False


async def readyz(_: Request) -> JSONResponse:
    try:
        ok = await asyncio.wait_for(asyncio.to_thread(_db_ready), timeout=4.0)
    except TimeoutError:
        ok = False
    return JSONResponse({"status": "ready" if ok else "not_ready", "database": ok}, status_code=200 if ok else 503)


app = App(Path(__file__).with_name("webapp") / "main.py", routes=[Route("/healthz", healthz), Route("/readyz", readyz)])
