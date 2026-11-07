"""Environment configuration for the Telegram platform bot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_SECRET = "dev-secret-change-me"
SUPPORTED_LANGUAGES = ("ru", "en", "es", "fa", "uz", "sah")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_base_url: str
    secret_key: str
    environment: str

    telegram_bot_token: str
    telegram_webhook_secret: str
    telegram_bot_username: str
    telegram_auto_setup: bool
    telegram_drop_pending_updates: bool

    admin_api_token: str
    admin_chat_id: str
    support_email: str
    support_url: str
    terms_url: str
    privacy_url: str

    order_ttl_minutes: int
    order_dedupe_seconds: int
    allow_late_payment: bool
    late_payment_max_minutes: int
    handoff_ttl_seconds: int
    init_data_ttl_seconds: int

    workers_enabled: bool
    auto_create_tables: bool
    outbox_interval_seconds: int
    outbox_max_attempts: int
    order_expiry_interval_seconds: int
    entitlement_expiry_interval_seconds: int
    webhook_check_interval_seconds: int

    webhook_max_body_bytes: int
    webhook_rate_limit_per_minute: int
    trust_proxy: bool
    metrics_enabled: bool
    log_level: str

    cors_origins: tuple[str, ...] = field(default_factory=tuple)

    @property
    def production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"} or (
            self.public_base_url.startswith("https://")
            and self.environment.strip().lower() != "development"
        )

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url}/api/webhooks/telegram"

    def fatal_problems(self) -> list[str]:
        """Configuration that makes a production deployment unsafe to start."""
        if not self.production:
            return []
        issues: list[str] = []
        base = urlparse(self.public_base_url)
        if base.scheme != "https" or not base.netloc:
            issues.append("PUBLIC_BASE_URL must be an absolute https URL in production")
        if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
            issues.append("SECRET_KEY must be a random value of at least 32 characters")
        if self.telegram_enabled and not self.telegram_webhook_secret.strip():
            issues.append(
                "TELEGRAM_WEBHOOK_SECRET is empty but TELEGRAM_BOT_TOKEN is set: "
                "the webhook would accept unauthenticated updates"
            )
        return issues

    def warnings(self) -> list[str]:
        issues: list[str] = []
        if self.secret_key == DEFAULT_SECRET:
            issues.append("SECRET_KEY is the built-in default, set a random value")
        if not self.database_url.startswith("postgresql"):
            issues.append("DATABASE_URL is not PostgreSQL (SQLite is for development)")
        if not self.telegram_enabled:
            issues.append("TELEGRAM_BOT_TOKEN is empty: the bot and Stars are disabled")
        if self.telegram_enabled and not self.telegram_webhook_secret:
            issues.append("TELEGRAM_WEBHOOK_SECRET is empty: webhook calls will be rejected")
        if not self.admin_api_token:
            issues.append("ADMIN_API_TOKEN is empty: bootstrap admin access is disabled")
        return issues


settings = Settings(
    database_url=os.getenv("DATABASE_URL", "sqlite:///./data/paybot.db"),
    public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
    secret_key=os.getenv("SECRET_KEY", DEFAULT_SECRET),
    environment=os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")),
    telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
    telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip(),
    telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
    telegram_auto_setup=_bool("TELEGRAM_AUTO_SETUP", False),
    telegram_drop_pending_updates=_bool("TELEGRAM_DROP_PENDING_UPDATES", False),
    admin_api_token=os.getenv("ADMIN_API_TOKEN", "").strip(),
    admin_chat_id=os.getenv("ADMIN_CHAT_ID", "").strip(),
    support_email=os.getenv("SUPPORT_EMAIL", "").strip(),
    support_url=os.getenv("SUPPORT_URL", "").strip(),
    terms_url=os.getenv("TERMS_URL", "").strip(),
    privacy_url=os.getenv("PRIVACY_URL", "").strip(),
    order_ttl_minutes=_int("ORDER_TTL_MINUTES", 30),
    order_dedupe_seconds=_int("ORDER_DEDUPE_SECONDS", 30),
    allow_late_payment=_bool("ALLOW_LATE_PAYMENT", True),
    late_payment_max_minutes=_int("LATE_PAYMENT_MAX_MINUTES", 24 * 60),
    handoff_ttl_seconds=_int("HANDOFF_TTL_SECONDS", 60),
    init_data_ttl_seconds=_int("INIT_DATA_TTL_SECONDS", 300),
    workers_enabled=_bool("WORKERS_ENABLED", True),
    auto_create_tables=_bool("AUTO_CREATE_TABLES", True),
    outbox_interval_seconds=_int("OUTBOX_INTERVAL_SECONDS", 5),
    outbox_max_attempts=_int("OUTBOX_MAX_ATTEMPTS", 8),
    order_expiry_interval_seconds=_int("ORDER_EXPIRY_INTERVAL_SECONDS", 60),
    entitlement_expiry_interval_seconds=_int("ENTITLEMENT_EXPIRY_INTERVAL_SECONDS", 300),
    webhook_check_interval_seconds=_int("WEBHOOK_CHECK_INTERVAL_SECONDS", 600),
    webhook_max_body_bytes=_int("WEBHOOK_MAX_BODY_BYTES", 64 * 1024),
    webhook_rate_limit_per_minute=_int("WEBHOOK_RATE_LIMIT_PER_MINUTE", 1200),
    trust_proxy=_bool("TRUST_PROXY", False),
    metrics_enabled=_bool("METRICS_ENABLED", True),
    log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    cors_origins=tuple(
        part.strip()
        for part in os.getenv("CORS_ORIGINS", "").replace(";", ",").split(",")
        if part.strip()
    ),
)
