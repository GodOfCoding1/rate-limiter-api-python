"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request

from app.config import Settings, get_settings
from app.rate_limiter.cleanup import CleanupThread
from app.rate_limiter.keys import extract_client_key
from app.rate_limiter.limiter import SlidingWindowCounterLimiter
from app.rate_limiter.middleware import RateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def _create_limiter(settings: Settings) -> SlidingWindowCounterLimiter:
    """Instantiate the rate limiter from application settings."""
    return SlidingWindowCounterLimiter(
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup / shutdown of long-lived resources.

    The single limiter instance is created by :func:`create_app` and
    stored on ``app.state``.  The lifespan only manages the background
    cleanup thread, ensuring it operates on the **same** limiter that
    the middleware uses.
    """
    limiter: SlidingWindowCounterLimiter = app.state.limiter
    settings: Settings = app.state.settings

    cleanup = CleanupThread(
        limiter=limiter,
        interval=settings.cleanup_interval_seconds,
    )
    cleanup.start()
    app.state.cleanup = cleanup

    logger.info(
        "Rate limiter ready: %d requests / %ds window.",
        settings.rate_limit_max_requests,
        settings.rate_limit_window_seconds,
    )

    yield

    cleanup.stop()
    logger.info("Application shutdown complete.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    Parameters:
        settings: Optional override; useful for testing.  When *None*
            the default :func:`get_settings` is used.
    """
    if settings is None:
        settings = get_settings()

    limiter = _create_limiter(settings)

    app = FastAPI(
        title="Rate Limiter API",
        description="Sliding-window-counter rate limiter demo.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store settings and the single limiter on app.state so the lifespan,
    # middleware, and routes all share the exact same instance.
    app.state.settings = settings
    app.state.limiter = limiter

    # --- Middleware ---------------------------------------------------
    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        exempt_paths=settings.rate_limit_exempt_paths,
    )

    # --- Routes -------------------------------------------------------
    @app.get("/")
    async def root() -> dict:
        """Health-check / landing page."""
        return {"message": "Rate Limiter API is running."}

    @app.get("/ping")
    async def ping() -> dict:
        """Lightweight endpoint for rate-limit testing."""
        return {"message": "pong"}

    @app.get("/status")
    async def status(request: Request) -> dict:
        """Return current rate-limit status for the calling client."""
        key = extract_client_key(request)
        result = limiter.peek(key)
        return {
            "limit": result.limit,
            "remaining": result.remaining,
            "reset": round(result.reset, 2),
        }

    return app


# Default app instance used by ``uvicorn app.main:app``
app = create_app()
