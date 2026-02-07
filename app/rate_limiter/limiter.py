"""Core sliding-window-counter rate limiter."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class _WindowCounter:
    """Per-user state: two fixed-window counters and its own lock.

    The sliding-window counter algorithm keeps track of request counts in
    two adjacent fixed windows (previous and current).  The estimated
    request count in the sliding window is computed as a weighted sum:

        estimated = prev_count * weight + curr_count

    where ``weight = (window_size - elapsed_in_current) / window_size``.
    """

    prev_count: int = 0
    prev_start: float = 0.0
    curr_count: int = 0
    curr_start: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RateLimitResult:
    """Outcome of a rate-limit check.

    Attributes:
        allowed: Whether the request is permitted.
        limit: Configured max requests per window.
        remaining: How many more requests the client can make in this window.
        reset: Seconds until the current fixed window ends.
        retry_after: Seconds to wait before retrying (0.0 if allowed).
    """

    allowed: bool
    limit: int
    remaining: int
    reset: float
    retry_after: float


class SlidingWindowCounterLimiter:
    """Thread-safe sliding-window-counter rate limiter.

    For every distinct ``key`` (user / API key / IP) it maintains two
    fixed-window counters (previous and current).  When :meth:`hit` is
    called the limiter:

    1. Acquires a **per-user** lock (so different users never contend).
    2. Advances the window if the current one has expired.
    3. Computes the weighted estimate of requests in the sliding window.
    4. If the estimate is below ``max_requests``, increments the current
       counter and returns an *allowed* result; otherwise returns a
       *denied* result with a ``retry_after`` hint.

    A top-level :class:`threading.Lock` guards the counter dictionary
    itself (creation / deletion of entries), but is held only briefly.

    Parameters:
        max_requests: Number of requests allowed per window.
        window_seconds: Window size in seconds.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        if max_requests < 0:
            raise ValueError("max_requests must be >= 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")

        self.max_requests = max_requests
        self.window_seconds = window_seconds

        self._counters: Dict[str, _WindowCounter] = {}
        self._global_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def hit(self, key: str) -> RateLimitResult:
        """Record a request for *key* and return the rate-limit verdict.

        This method is safe to call from multiple threads concurrently.
        """
        counter = self._get_or_create_counter(key)
        now = time.monotonic()

        with counter.lock:
            self._advance_window(counter, now)
            estimated = self._estimate_count(counter, now)

            if estimated < self.max_requests:
                counter.curr_count += 1
                new_estimated = estimated + 1
                remaining = max(0, self.max_requests - int(new_estimated))
                reset = self._compute_reset(counter, now)
                return RateLimitResult(
                    allowed=True,
                    limit=self.max_requests,
                    remaining=remaining,
                    reset=reset,
                    retry_after=0.0,
                )
            else:
                reset = self._compute_reset(counter, now)
                retry_after = self._compute_retry_after(counter, now)
                return RateLimitResult(
                    allowed=False,
                    limit=self.max_requests,
                    remaining=0,
                    reset=reset,
                    retry_after=retry_after,
                )

    def peek(self, key: str) -> RateLimitResult:
        """Check the current rate-limit state for *key* **without**
        recording a new request."""
        counter = self._get_or_create_counter(key)
        now = time.monotonic()

        with counter.lock:
            self._advance_window(counter, now)
            estimated = self._estimate_count(counter, now)
            remaining = max(0, self.max_requests - int(estimated))
            reset = self._compute_reset(counter, now)
            retry_after = (
                self._compute_retry_after(counter, now)
                if remaining == 0
                else 0.0
            )
            return RateLimitResult(
                allowed=remaining > 0,
                limit=self.max_requests,
                remaining=remaining,
                reset=reset,
                retry_after=retry_after,
            )

    def reset(self, key: str) -> None:
        """Remove all tracked state for *key*."""
        with self._global_lock:
            self._counters.pop(key, None)

    def cleanup(self) -> None:
        """Purge fully-expired counters across **all** users.

        Called periodically by the background cleanup thread.  Users
        whose counters are empty (both windows expired) are removed
        entirely to reclaim memory.
        """
        now = time.monotonic()
        empty_keys: list[str] = []

        # Snapshot under the global lock to avoid mutating while iterating.
        with self._global_lock:
            snapshot = list(self._counters.items())

        for key, counter in snapshot:
            with counter.lock:
                self._advance_window(counter, now)
                if counter.curr_count == 0 and counter.prev_count == 0:
                    empty_keys.append(key)

        # Remove empty counters under the global lock.
        if empty_keys:
            with self._global_lock:
                for key in empty_keys:
                    counter = self._counters.get(key)
                    if counter is None:
                        continue
                    # Re-check under the per-user lock to avoid racing
                    # with a concurrent hit that just arrived.
                    with counter.lock:
                        if counter.curr_count == 0 and counter.prev_count == 0:
                            del self._counters[key]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_counter(self, key: str) -> _WindowCounter:
        """Return the counter for *key*, creating it if necessary.

        Always acquires the global lock for the lookup so the code is
        safe on free-threaded Python builds (no-GIL).
        """
        with self._global_lock:
            counter = self._counters.get(key)
            if counter is None:
                counter = _WindowCounter()
                self._counters[key] = counter
            return counter

    def _advance_window(
        self, counter: _WindowCounter, now: float
    ) -> None:
        """Rotate fixed windows if the current one has expired.

        Must be called while holding ``counter.lock``.
        """
        if counter.curr_start == 0.0:
            # First request for this key.
            counter.curr_start = now
            return

        elapsed = now - counter.curr_start
        if elapsed < self.window_seconds:
            return  # Still within the current window.

        windows_passed = int(elapsed / self.window_seconds)
        if windows_passed >= 2:
            # Both windows fully expired.
            counter.prev_count = 0
            counter.prev_start = 0.0
        else:
            # Exactly one window passed; current becomes previous.
            counter.prev_count = counter.curr_count
            counter.prev_start = counter.curr_start

        counter.curr_count = 0
        counter.curr_start += windows_passed * self.window_seconds

    def _estimate_count(
        self, counter: _WindowCounter, now: float
    ) -> float:
        """Return the weighted estimate of requests in the sliding window."""
        if counter.curr_start == 0.0:
            return 0.0

        elapsed = now - counter.curr_start
        weight = max(
            0.0, (self.window_seconds - elapsed) / self.window_seconds
        )
        return counter.prev_count * weight + counter.curr_count

    def _compute_reset(
        self, counter: _WindowCounter, now: float
    ) -> float:
        """Seconds until the current fixed window ends."""
        if counter.curr_start == 0.0:
            return 0.0
        elapsed = now - counter.curr_start
        return max(0.0, self.window_seconds - elapsed)

    def _compute_retry_after(
        self, counter: _WindowCounter, now: float
    ) -> float:
        """Seconds until the estimated count drops below ``max_requests``.

        If the previous window is contributing to the denial, computes
        the exact time when the decreasing overlap weight makes the
        estimate drop below the threshold.  Otherwise falls back to the
        window reset time (time until the current window ends).
        """
        elapsed = now - counter.curr_start
        reset = max(0.0, self.window_seconds - elapsed)

        if counter.prev_count > 0 and counter.curr_count < self.max_requests:
            # Solve: prev * (W - t) / W + curr < max_requests
            # => t > W * (1 - (max_requests - curr) / prev)
            needed_elapsed = self.window_seconds * (
                1.0
                - (self.max_requests - counter.curr_count)
                / counter.prev_count
            )
            dt = needed_elapsed - elapsed
            if 0 < dt <= reset:
                return dt

        return reset
