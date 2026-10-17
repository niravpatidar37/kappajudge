from __future__ import annotations

import asyncio

import pytest


def test_healthz_ok_and_readyz_503_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none?connect_timeout=1")
    from judgecal.asgi import healthz, readyz

    assert asyncio.run(healthz(None)).status_code == 200  # type: ignore[arg-type]
    resp = asyncio.run(readyz(None))  # type: ignore[arg-type]
    assert resp.status_code == 503 and b"not_ready" in resp.body


def test_database_url_built_from_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from judgecal.config import database_url_from_env

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PG_HOST", "10.0.0.5")
    monkeypatch.setenv("PG_USER", "judgecal_app")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("PG_SSLMODE", "require")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_DB", "judgecal")
    url = database_url_from_env()
    assert url.startswith("postgresql://judgecal_app:p%40ss%2Fword@10.0.0.5:5432/judgecal?sslmode=require")
