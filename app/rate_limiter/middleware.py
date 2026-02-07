"""Pure ASGI middleware that enforces per-client rate limits."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Callable, Optional, Set

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.rate_limiter.keys import extract_client_key
from app.rate_limiter.limiter import RateLimitResult, SlidingWindowCounterLimiter


class RateLimitMiddleware:
    """Pure ASGI middleware that enforces rate limits.

    For every incoming HTTP request the middleware:

    1. Checks if the request path is exempt.
    2. Extracts a client identifier (API key or IP address).
    3. Calls :meth:`SlidingWindowCounterLimiter.hit` to record the request.
    4. Injects ``X-RateLimit-*`` headers into the response.
    5. Returns **HTTP 429** if the limit has been exceeded.

    Non-HTTP scopes (e.g. WebSocket, lifespan) are forwarded unchanged.

    Parameters:
        app: The ASGI application to wrap.
        limiter: A configured :class:`SlidingWindowCounterLimiter`.
        key_func: Optional callable ``(Request) -> str`` that extracts the
            client identifier.  When *None* the default extraction order
            is used (``X-API-Key`` header → ``api_key`` query param → client IP).
        exempt_paths: Optional set of URL paths that bypass rate limiting
            (e.g. health-check endpoints).
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: SlidingWindowCounterLimiter,
        key_func: Optional[Callable[[Request], str]] = None,
        exempt_paths: Optional[Set[str]] = None,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._key_func = key_func or extract_client_key
        self._exempt_paths = exempt_paths or set()

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        client_key = self._key_func(request)
        # Offload the blocking hit() call (which acquires threading locks)
        # to a worker thread so the async event loop is never blocked.
        result: RateLimitResult = await asyncio.to_thread(
            self._limiter.hit, client_key
        )

        if not result.allowed:
            await self._send_429(send, result)
            return

        # Wrap ``send`` to inject rate-limit headers into the response.
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._rate_limit_headers(result))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rate_limit_headers(
        result: RateLimitResult,
    ) -> list[tuple[bytes, bytes]]:
        """Build the ``X-RateLimit-*`` header tuples."""
        return [
            (b"x-ratelimit-limit", str(result.limit).encode()),
            (b"x-ratelimit-remaining", str(result.remaining).encode()),
            (b"x-ratelimit-reset", str(math.ceil(result.reset)).encode()),
        ]

    @staticmethod
    async def _send_429(send: Send, result: RateLimitResult) -> None:
        """Send a JSON 429 response with appropriate headers."""
        retry_after = math.ceil(result.retry_after)
        body = json.dumps(
            {
                "detail": "Rate limit exceeded. Try again later.",
                "retry_after": retry_after,
            }
        ).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode()),
                    (b"x-ratelimit-limit", str(result.limit).encode()),
                    (b"x-ratelimit-remaining", b"0"),
                    (
                        b"x-ratelimit-reset",
                        str(math.ceil(result.reset)).encode(),
                    ),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
