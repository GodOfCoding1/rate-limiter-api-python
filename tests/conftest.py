"""Shared pytest fixtures for rate-limiter tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rate_limiter.limiter import SlidingWindowCounterLimiter


@pytest.fixture()
def limiter() -> SlidingWindowCounterLimiter:
    """A fresh limiter allowing 5 requests per 60-second window."""
    return SlidingWindowCounterLimiter(max_requests=5, window_seconds=60)


@pytest.fixture()
def client() -> TestClient:
    """A ``TestClient`` backed by a low-limit app (5 req / 60 s)."""
    settings = Settings(
        rate_limit_max_requests=5,
        rate_limit_window_seconds=60,
        cleanup_interval_seconds=300,
    )
    app = create_app(settings=settings)
    return TestClient(app)
