"""Runtime configuration from environment variables only.

Secrets (GROQ_API_KEY, HF_TOKEN, DATABASE_URL password) come from the environment or the shared
portfolio env file named by PORTFOLIO_ENV_FILE. Nothing here prints or logs secret values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(os.environ.get("JUDGECAL_ROOT") or Path(__file__).resolve().parents[2])
DEFAULT_PORTFOLIO_ENV = "F:/.secrets/portfolio.env"
PROJECT = "p4-judge"

_loaded = False


def load_env() -> None:
    """Load ./.env (local stack creds) then the shared portfolio env file. Existing env wins."""
    global _loaded
    if _loaded:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
    portfolio = os.environ.get("PORTFOLIO_ENV_FILE", DEFAULT_PORTFOLIO_ENV)
    if Path(portfolio).is_file():
        load_dotenv(portfolio, override=False)
    _loaded = True


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    judge_model: str = "qwen/qwen3.8-27b"
    judge_provider: str = "groq"
    sut_model: str = "openai/gpt-oss-20b"
    sut_provider: str = "groq"
    database_url: str = field(default="", repr=False)
    data_dir: Path = REPO_ROOT / "data"
    cache_dir: Path = REPO_ROOT / "data" / "cache"
    results_dir: Path = REPO_ROOT / "results"
    llm_calls_enabled: bool = True  # kill switch: LLM_CALLS_ENABLED=false refuses every model call
    log_level: str = "INFO"


def database_url_from_env() -> str:
    """DATABASE_URL if set; otherwise built from PG_* + POSTGRES_PASSWORD (Cloud Run injects the password secret)."""
    url = os.environ.get("DATABASE_URL", "")
    if url or not os.environ.get("PG_HOST"):
        return url
    from urllib.parse import quote

    user = quote(os.environ.get("PG_USER", "judgecal"), safe="")
    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ["PG_HOST"]
    port = os.environ.get("PG_PORT", "5432")
    db = quote(os.environ.get("PG_DB", "judgecal"), safe="")
    sslmode = os.environ.get("PG_SSLMODE", "prefer")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}?sslmode={sslmode}&connect_timeout=5"


def get_settings() -> Settings:
    load_env()
    return Settings(
        judge_model=os.environ.get("JUDGE_MODEL", Settings.judge_model),
        judge_provider=os.environ.get("JUDGE_PROVIDER", Settings.judge_provider),
        sut_model=os.environ.get("SUT_MODEL", Settings.sut_model),
        sut_provider=os.environ.get("SUT_PROVIDER", Settings.sut_provider),
        database_url=database_url_from_env(),
        cache_dir=Path(os.environ.get("JUDGECAL_CACHE_DIR", str(Settings.cache_dir))),
        llm_calls_enabled=_flag("LLM_CALLS_ENABLED", True),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
