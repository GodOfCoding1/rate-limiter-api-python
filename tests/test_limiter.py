"""Unit tests for the core SlidingWindowCounterLimiter."""

from __future__ import annotations

import time

import pytest

from app.rate_limiter.limiter import SlidingWindowCounterLimiter


class TestBasicAllowDeny:
    """Verify the fundamental allow / deny behaviour."""

    def test_allows_up_to_max_requests(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        for i in range(5):
            result = limiter.hit("user-1")
            assert result.allowed, f"Request {i + 1} should be allowed"
            assert result.remaining == 5 - i - 1

    def test_denies_after_max_requests(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        for _ in range(5):
            limiter.hit("user-1")

        result = limiter.hit("user-1")
        assert not result.allowed
        assert result.remaining == 0
        assert result.retry_after > 0

    def test_different_users_are_independent(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        # Exhaust user-a
        for _ in range(5):
            limiter.hit("user-a")
        assert not limiter.hit("user-a").allowed

        # user-b should still be allowed
        result = limiter.hit("user-b")
        assert result.allowed
        assert result.remaining == 4


class TestWindowExpiry:
    """Ensure counters expire correctly after the window elapses."""

    def test_requests_allowed_after_full_expiry(self) -> None:
        """After two full windows both counters are cleared."""
        limiter = SlidingWindowCounterLimiter(max_requests=2, window_seconds=1)

        limiter.hit("u")
        limiter.hit("u")
        assert not limiter.hit("u").allowed

        # Wait for both windows to fully expire (2 × window).
        time.sleep(2.1)

        result = limiter.hit("u")
        assert result.allowed
        assert result.remaining == 1

    def test_partial_expiry_allows_some_requests(self) -> None:
        """After one window the previous count decays via the weight."""
        limiter = SlidingWindowCounterLimiter(max_requests=2, window_seconds=1)

        limiter.hit("u")
        limiter.hit("u")
        assert not limiter.hit("u").allowed

        # Wait for one window to pass — previous count decays.
        time.sleep(1.1)

        # The weighted estimate is prev * weight + 0.  With weight < 1
        # the estimate should be below max_requests again.
        result = limiter.hit("u")
        assert result.allowed


class TestEdgeCases:
    """Cover boundary conditions."""

    def test_zero_max_requests(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=0, window_seconds=60)
        result = limiter.hit("u")
        assert not result.allowed

    def test_single_max_request(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=1, window_seconds=60)
        assert limiter.hit("u").allowed
        assert not limiter.hit("u").allowed

    def test_negative_max_requests_raises(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be >= 0"):
            SlidingWindowCounterLimiter(max_requests=-1, window_seconds=60)

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            SlidingWindowCounterLimiter(max_requests=5, window_seconds=0)


class TestPeek:
    """The peek method should not consume a request slot."""

    def test_peek_does_not_count(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        # Peek should report full capacity.
        result = limiter.peek("u")
        assert result.remaining == 5

        # Consume one slot.
        limiter.hit("u")
        result = limiter.peek("u")
        assert result.remaining == 4

    def test_peek_reports_denied_when_full(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        for _ in range(5):
            limiter.hit("u")
        result = limiter.peek("u")
        assert not result.allowed
        assert result.remaining == 0


class TestReset:
    """Verify that resetting a user's state works."""

    def test_reset_clears_user(
        self, limiter: SlidingWindowCounterLimiter
    ) -> None:
        for _ in range(5):
            limiter.hit("u")
        assert not limiter.hit("u").allowed

        limiter.reset("u")
        assert limiter.hit("u").allowed


class TestCleanup:
    """Test the bulk cleanup method."""

    def test_cleanup_removes_expired_entries(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=2, window_seconds=1)
        limiter.hit("a")
        limiter.hit("b")

        # Wait for both windows to fully expire.
        time.sleep(2.1)
        limiter.cleanup()

        # Both users should have been purged entirely.
        assert limiter._counters == {}

    def test_cleanup_keeps_active_entries(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=5, window_seconds=60)
        limiter.hit("a")
        limiter.cleanup()
        assert "a" in limiter._counters


class TestRetryAfterAndReset:
    """Verify the retry_after and reset values in RateLimitResult."""

    def test_retry_after_is_positive_when_denied(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=1, window_seconds=10)
        limiter.hit("u")
        result = limiter.hit("u")
        assert not result.allowed
        assert 0 < result.retry_after <= 10

    def test_reset_value_decreases_over_time(self) -> None:
        limiter = SlidingWindowCounterLimiter(max_requests=2, window_seconds=5)
        limiter.hit("u")
        r1 = limiter.peek("u")
        time.sleep(0.5)
        r2 = limiter.peek("u")
        assert r2.reset < r1.reset
