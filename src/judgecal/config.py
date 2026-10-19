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


PROD_ENVS = frozenset({"prod", "production"})
MIN_API_KEY_CHARS = 24


class ConfigError(ValueError):
    """Unsafe or invalid configuration: the process refuses to start (fail closed)."""


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
    env: str = "dev"  # ENV: dev | ci | staging | prod
    # Recorded LLM mode (LLM_RECORDED_MODE=true): every model call is answered ONLY from the recorded
    # cache in LLM_RECORDED_DIR (real outputs captured earlier); a miss raises, the network is never
    # used. For CI/compose smoke tests. Refused when ENV is prod (ConfigError).
    llm_recorded_mode: bool = False
    llm_recorded_dir: Path = REPO_ROOT / "tests" / "fixtures" / "recorded_llm"
    # JSON API (/api/v1/*): X-API-Key auth, comma-separated keys for rotation. Empty = API disabled (503).
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    rate_limit_per_min: float = 120.0
    rate_limit_burst: int = 30
    max_body_bytes: int = 16384
    # Kill switch for label writes (UI form + API): LABEL_WRITES_ENABLED=false -> read-only (API 503).
    label_writes_enabled: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env.strip().lower() in PROD_ENVS


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


def _api_keys() -> tuple[str, ...]:
    keys = tuple(k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip())
    if any(len(k) < MIN_API_KEY_CHARS for k in keys):
        raise ConfigError(f"every API_KEYS entry must be at least {MIN_API_KEY_CHARS} characters")
    return keys


def get_settings() -> Settings:
    load_env()
    s = Settings(
        judge_model=os.environ.get("JUDGE_MODEL", Settings.judge_model),
        judge_provider=os.environ.get("JUDGE_PROVIDER", Settings.judge_provider),
        sut_model=os.environ.get("SUT_MODEL", Settings.sut_model),
        sut_provider=os.environ.get("SUT_PROVIDER", Settings.sut_provider),
        database_url=database_url_from_env(),
        cache_dir=Path(os.environ.get("JUDGECAL_CACHE_DIR", str(Settings.cache_dir))),
        llm_calls_enabled=_flag("LLM_CALLS_ENABLED", True),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        env=os.environ.get("ENV", "dev"),
        llm_recorded_mode=_flag("LLM_RECORDED_MODE", False),
        llm_recorded_dir=Path(os.environ.get("LLM_RECORDED_DIR", str(Settings.llm_recorded_dir))),
        api_keys=_api_keys(),
        rate_limit_per_min=float(os.environ.get("RATE_LIMIT_PER_MIN", Settings.rate_limit_per_min)),
        rate_limit_burst=int(os.environ.get("RATE_LIMIT_BURST", Settings.rate_limit_burst)),
        max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", Settings.max_body_bytes)),
        label_writes_enabled=_flag("LABEL_WRITES_ENABLED", True),
    )
    if s.llm_recorded_mode and s.is_prod:
        # Recorded mode would make the scheduled eval replay stale outputs and report them as fresh.
        raise ConfigError("LLM_RECORDED_MODE cannot be enabled when ENV is prod")
    if s.rate_limit_per_min <= 0 or s.rate_limit_burst < 1 or s.max_body_bytes < 1024:
        raise ConfigError("RATE_LIMIT_PER_MIN > 0, RATE_LIMIT_BURST >= 1 and MAX_BODY_BYTES >= 1024 are required")
    return s
