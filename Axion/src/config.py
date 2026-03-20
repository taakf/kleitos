"""
Configuration loader for Axion (by 4Labs).

Loads settings from config/settings.yaml and environment variables.
Checks ~/.axion.env first, then falls back to ~/.kleitos.env for backward compatibility.
Provides a validated Settings pydantic model and a get_settings() singleton.

NOTE: All KLEITOS_* environment variable names are preserved for backward compatibility.
A future release may add AXION_* aliases.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project root is two levels up from this file: src/config.py -> project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
DEFAULT_ENV_PATH = Path.home() / ".axion.env"
LEGACY_ENV_PATH = Path.home() / ".kleitos.env"
DEFAULT_DATA_DIR = Path.home() / "kleitos-data"  # kept for backward compat


def _resolve_path(raw: str | Path) -> Path:
    """Expand ~ and environment variables, then resolve to absolute."""
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return the top-level dict."""
    if not path.exists():
        logger.warning("Settings file not found at %s — using defaults", path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Pydantic sub-models — mirrors the settings.yaml structure
# ---------------------------------------------------------------------------


class SystemSettings(BaseModel):
    name: str = "axion"
    version: str = "1.0.0"
    environment: str = "production"


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7777
    workers: int = 1
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:7777", "http://127.0.0.1:7777"]
    )
    auth_enabled: bool = True
    api_key: str = ""  # Set via KLEITOS_API_KEY env var (kept for backward compat)
    rate_limit_rpm: int = 100


class DatabaseSettings(BaseModel):
    path: Path = Field(default_factory=lambda: DEFAULT_DATA_DIR / "db" / "kleitos.db")
    wal_mode: bool = True
    journal_size_limit: int = 67_108_864
    busy_timeout: int = 5000

    @model_validator(mode="after")
    def resolve_db_path(self) -> "DatabaseSettings":
        self.path = _resolve_path(self.path)
        return self


class SchedulerCollectionSettings(BaseModel):
    interval_minutes: int = 30
    max_concurrent: int = 1


def _validate_hhmm_time(v: str) -> str:
    """Validate that a string is in HH:MM format (00:00–23:59)."""
    import re
    if not re.fullmatch(r"[0-2]\d:[0-5]\d", v):
        raise ValueError(f"time must be in HH:MM format (00:00–23:59), got {v!r}")
    hour = int(v[:2])
    if hour > 23:
        raise ValueError(f"hour must be 00–23, got {hour}")
    return v


class SchedulerDigestSettings(BaseModel):
    time: str = "07:00"
    timezone: str = "local"

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        return _validate_hhmm_time(v)


class SchedulerHealthCheckSettings(BaseModel):
    interval_minutes: int = 5


class SchedulerBackupSettings(BaseModel):
    time: str = "02:00"
    retention_days: int = 7

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        return _validate_hhmm_time(v)


class SchedulerClassificationSettings(BaseModel):
    interval_hours: int = 6


class SchedulerCoverageQASettings(BaseModel):
    interval_hours: int = 4


class SchedulerRiskCheckSettings(BaseModel):
    interval_hours: int = 1


class SchedulerAnalysisSettings(BaseModel):
    interval_minutes: int = 30


class SchedulerSettings(BaseModel):
    collection: SchedulerCollectionSettings = Field(
        default_factory=SchedulerCollectionSettings
    )
    analysis: SchedulerAnalysisSettings = Field(
        default_factory=SchedulerAnalysisSettings
    )
    digest: SchedulerDigestSettings = Field(default_factory=SchedulerDigestSettings)
    health_check: SchedulerHealthCheckSettings = Field(
        default_factory=SchedulerHealthCheckSettings
    )
    backup: SchedulerBackupSettings = Field(default_factory=SchedulerBackupSettings)
    classification: SchedulerClassificationSettings = Field(
        default_factory=SchedulerClassificationSettings
    )
    coverage_qa: SchedulerCoverageQASettings = Field(
        default_factory=SchedulerCoverageQASettings
    )
    risk_check: SchedulerRiskCheckSettings = Field(
        default_factory=SchedulerRiskCheckSettings
    )


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    analysis_model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_seconds: int = 60
    max_retries: int = 3
    retry_backoff_seconds: list[int] = Field(default_factory=lambda: [2, 5, 15])


class MacroScreeningSettings(BaseModel):
    enabled: bool = True
    batch_size: int = 20
    max_batches_per_run: int = 5
    min_confidence: float = 0.2


class ImpactMappingSettings(BaseModel):
    min_relevance_threshold: float = 0.3
    materiality_levels: list[str] = Field(
        default_factory=lambda: ["immaterial", "watch", "important", "critical"]
    )
    confidence_levels: list[str] = Field(
        default_factory=lambda: ["low", "medium", "high"]
    )
    max_llm_scoring_batch: int = 10


class ConcentrationSettings(BaseModel):
    max_single_name_pct: float = 10.0
    max_sector_pct: float = 30.0
    max_geography_pct: float = 40.0
    max_currency_pct: float = 50.0
    max_theme_pct: float = 25.0


class DividendConcentrationSettings(BaseModel):
    max_same_month_pct: float = 30.0


class CorrelationSettings(BaseModel):
    max_same_sector_geo_pct: float = 50.0
    max_same_sector_pct: float = 60.0


class CalendarSettings(BaseModel):
    cluster_threshold_days: int = 5
    cluster_min_events: int = 3


class StaleDataSettings(BaseModel):
    max_days_without_event: int = 14
    max_days_without_price: int = 3


class RiskSettings(BaseModel):
    concentration: ConcentrationSettings = Field(
        default_factory=ConcentrationSettings
    )
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    stale_data: StaleDataSettings = Field(default_factory=StaleDataSettings)
    dividend_concentration: DividendConcentrationSettings = Field(
        default_factory=DividendConcentrationSettings
    )
    correlation: CorrelationSettings = Field(
        default_factory=CorrelationSettings
    )


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "json"
    max_file_size_mb: int = 50
    backup_count: int = 10
    log_dir: Path = Field(default_factory=lambda: DEFAULT_DATA_DIR / "logs")

    @model_validator(mode="after")
    def resolve_log_dir(self) -> "LoggingSettings":
        self.log_dir = _resolve_path(self.log_dir)
        return self


class TelegramSettings(BaseModel):
    enabled: bool = False
    token: str = ""  # BotFather token — set via KLEITOS_TELEGRAM_TOKEN (kept for backward compat)
    chat_ids: list[int] = Field(default_factory=list)  # Authorized chat IDs
    push_severities: list[str] = Field(default_factory=lambda: ["critical", "high"])
    push_digests: bool = True


class EmailSettings(BaseModel):
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    from_address: str = ""
    to_addresses: list[str] = Field(default_factory=list)
    use_tls: bool = True


class OpenClawSettings(BaseModel):
    enabled: bool = True
    config_path: str = "openclaw/openclaw-config.json"


class DashboardSettings(BaseModel):
    enabled: bool = True
    theme: str = "light"
    refresh_interval_seconds: int = 60


# ---------------------------------------------------------------------------
# Top-level Settings model
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """Validated, immutable application configuration for Axion."""

    system: SystemSettings = Field(default_factory=SystemSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    macro_screening: MacroScreeningSettings = Field(
        default_factory=MacroScreeningSettings
    )
    impact_mapping: ImpactMappingSettings = Field(
        default_factory=ImpactMappingSettings
    )
    risk: RiskSettings = Field(default_factory=RiskSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    openclaw: OpenClawSettings = Field(default_factory=OpenClawSettings)

    # Env-var overrides (loaded from ~/.axion.env or ~/.kleitos.env fallback)
    anthropic_api_key: SecretStr = SecretStr("")
    data_dir: Path = Field(default_factory=lambda: DEFAULT_DATA_DIR)

    @model_validator(mode="after")
    def resolve_data_dir(self) -> "Settings":
        self.data_dir = _resolve_path(self.data_dir)
        return self


def _build_settings(
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
) -> Settings:
    """
    Build a Settings instance by merging YAML config with env vars.

    Priority (highest wins):
      1. Environment variables
      2. ~/.axion.env (or ~/.kleitos.env fallback)
      3. config/settings.yaml
      4. Pydantic defaults
    """
    # Load .env files — check ~/.axion.env first, then ~/.kleitos.env as
    # backward-compat fallback, then project root .env.
    if env_path.exists():
        load_dotenv(env_path, override=False)
        logger.info("Loaded environment from %s", env_path)
    elif LEGACY_ENV_PATH.exists():
        load_dotenv(LEGACY_ENV_PATH, override=False)
        logger.info("Loaded environment from %s (legacy fallback)", LEGACY_ENV_PATH)
    else:
        logger.debug("No env file at %s or %s — skipping", env_path, LEGACY_ENV_PATH)

    # Also load project-root .env as fallback (common developer convention)
    project_env = PROJECT_ROOT / ".env"
    if project_env.exists() and project_env != env_path:
        load_dotenv(project_env, override=False)
        logger.info("Loaded environment from %s (project root fallback)", project_env)

    raw = _load_yaml(settings_path)

    # Overlay select env vars onto the raw dict
    env_overrides: dict[str, Any] = {}
    if api_key := os.environ.get("ANTHROPIC_API_KEY", ""):
        env_overrides["anthropic_api_key"] = SecretStr(api_key)
    if data_dir := os.environ.get("KLEITOS_DATA_DIR", ""):
        env_overrides["data_dir"] = data_dir
    if db_path := os.environ.get("KLEITOS_DB_PATH", ""):
        raw.setdefault("database", {})["path"] = db_path
    if log_level := os.environ.get("KLEITOS_LOG_LEVEL", ""):
        raw.setdefault("logging", {})["level"] = log_level
    if llm_model := os.environ.get("KLEITOS_LLM_MODEL", ""):
        raw.setdefault("llm", {})["model"] = llm_model
    if env_name := os.environ.get("KLEITOS_ENVIRONMENT", ""):
        raw.setdefault("system", {})["environment"] = env_name
    if kleitos_api_key := os.environ.get("KLEITOS_API_KEY", ""):
        raw.setdefault("api", {})["api_key"] = kleitos_api_key
    if cors_origins := os.environ.get("KLEITOS_CORS_ORIGINS", ""):
        raw.setdefault("api", {})["cors_origins"] = [o.strip() for o in cors_origins.split(",")]

    # Telegram settings from env vars
    if tg_token := os.environ.get("KLEITOS_TELEGRAM_TOKEN", ""):
        raw.setdefault("telegram", {})["token"] = tg_token
        raw["telegram"]["enabled"] = True
    if tg_chats := os.environ.get("KLEITOS_TELEGRAM_CHAT_ID", ""):
        chat_ids = [int(c.strip()) for c in tg_chats.split(",") if c.strip().lstrip("-").isdigit()]
        raw.setdefault("telegram", {})["chat_ids"] = chat_ids

    # Email settings from env vars
    if smtp_user := os.environ.get("KLEITOS_SMTP_USER", ""):
        raw.setdefault("email", {})["smtp_user"] = smtp_user
        raw["email"]["enabled"] = True
    if smtp_pass := os.environ.get("KLEITOS_SMTP_PASSWORD", ""):
        raw.setdefault("email", {})["smtp_password"] = smtp_pass
    if smtp_host := os.environ.get("KLEITOS_SMTP_HOST", ""):
        raw.setdefault("email", {})["smtp_host"] = smtp_host
    if email_to := os.environ.get("KLEITOS_EMAIL_TO", ""):
        raw.setdefault("email", {})["to_addresses"] = [e.strip() for e in email_to.split(",")]
    if email_from := os.environ.get("KLEITOS_EMAIL_FROM", ""):
        raw.setdefault("email", {})["from_address"] = email_from

    merged = {**raw, **env_overrides}
    return Settings(**merged)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a singleton Settings instance.

    The result is cached for the lifetime of the process.  Call
    ``get_settings.cache_clear()`` if you need to force a reload (e.g. in tests).
    """
    settings = _build_settings()
    logger.info(
        "Settings loaded — env=%s, db=%s",
        settings.system.environment,
        settings.database.path,
    )
    return settings
