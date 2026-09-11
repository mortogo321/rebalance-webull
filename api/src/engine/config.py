"""Runtime configuration.

Values come from the process environment, which docker-compose populates from
``api/.env.<APP_ENV>`` (committed defaults) overlaid with injected secrets.
Precedence is: real environment variable > .env file.

Everything that must be true for the service to be *safe* is asserted here, at
import time, so a misconfigured deployment fails to start rather than starting
and doing something irreversible.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "uat", "production"]
BrokerMode = Literal["mock", "webull_uat"]


class ConfigError(RuntimeError):
    """Configuration that would be unsafe to run with."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "development"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = ""
    database_pool_min: int = 1
    database_pool_max: int = 8

    # --- supabase ---
    supabase_url: str = ""
    supabase_secret_key: str = ""

    # --- encryption ---
    app_encryption_key: str = ""
    app_encryption_key_version: int = 1

    # --- internal auth ---
    engine_internal_token: str = ""

    # --- broker ---
    broker_mode: BrokerMode = "mock"
    webull_base_url: str = ""
    webull_allowed_hosts: str = "uat-api.webull.co.th,sandbox-api.webull.co.th"
    webull_request_timeout_seconds: float = 10.0

    # --- mock broker ---
    mock_starting_cash: Decimal = Decimal(100_000)
    mock_price_seed: int = 20260101
    mock_price_volatility_bps: int = 150

    # --- scheduler ---
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 15
    max_orders_per_run: int = Field(default=25, ge=1, le=200)

    @property
    def allowed_broker_hosts(self) -> tuple[str, ...]:
        return tuple(
            h.strip().lower() for h in self.webull_allowed_hosts.split(",") if h.strip()
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown LOG_LEVEL: {value}")
        return level

    @model_validator(mode="after")
    def _guard(self) -> Settings:
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", self.database_url),
                ("APP_ENCRYPTION_KEY", self.app_encryption_key),
                ("ENGINE_INTERNAL_TOKEN", self.engine_internal_token),
            )
            if not value.strip()
        ]
        if missing:
            raise ConfigError(
                "missing required configuration: "
                + ", ".join(missing)
                + ". In uat/production these are injected from CI secrets; see "
                "api/.env.uat for the variable names."
            )

        # ---------------------------------------------------------------------
        # The assignment's hard rule: no order may ever reach Webull production.
        # This is enforced here rather than in the broker client, because by the
        # time a client is constructed the process is already accepting traffic.
        # A bad host aborts startup, so the failure is a container that will not
        # boot, not a trade that cannot be unwound.
        # ---------------------------------------------------------------------
        if self.broker_mode == "webull_uat":
            if not self.webull_base_url.strip():
                raise ConfigError("BROKER_MODE=webull_uat requires WEBULL_BASE_URL")

            parsed = urlparse(self.webull_base_url)
            if parsed.scheme != "https":
                raise ConfigError(
                    f"WEBULL_BASE_URL must use https, got {parsed.scheme or 'no scheme'}"
                )
            host = (parsed.hostname or "").lower()
            if host not in self.allowed_broker_hosts:
                raise ConfigError(
                    f"refusing to start: broker host {host!r} is not in "
                    f"WEBULL_ALLOWED_HOSTS {self.allowed_broker_hosts}. "
                    "This guard is what keeps a misconfigured deploy off a live venue."
                )

        # A development stack must never be pointed at a real broker by accident.
        if self.app_env == "development" and self.broker_mode != "mock":
            raise ConfigError(
                "development runs against the mock broker only; "
                "set APP_ENV=uat to use a real venue"
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
