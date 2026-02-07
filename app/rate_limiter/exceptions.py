"""Custom exceptions for the rate limiter."""


class RateLimitExceeded(Exception):
    """Raised when a client exceeds the configured rate limit.

    Attributes:
        retry_after: Seconds until the client can retry.
        limit: The configured maximum requests per window.
        remaining: Number of requests remaining (always 0 when raised).
        reset: Seconds until the current window resets.
    """

    def __init__(
        self,
        retry_after: float,
        limit: int,
        reset: float,
    ) -> None:
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = 0
        self.reset = reset
        super().__init__(
            f"Rate limit exceeded. Retry after {retry_after:.1f}s."
        )
