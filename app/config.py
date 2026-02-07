"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Rate limiter configuration.

    All values can be overridden via environment variables
    (e.g. ``RATE_LIMIT_MAX_REQUESTS=100``).
    """

    rate_limit_max_requests: int = 60
    """Maximum number of requests allowed within the sliding window."""

    rate_limit_window_seconds: int = 60
    """Size of the sliding window in seconds."""

    cleanup_interval_seconds: int = 60
    """How often (in seconds) the background thread purges stale entries."""

    rate_limit_exempt_paths: set[str] = {"/", "/status"}
    """URL paths that bypass rate limiting (e.g. health-check endpoints)."""

    model_config = {"env_prefix": ""}


def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
