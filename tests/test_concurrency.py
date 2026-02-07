"""Multi-threaded stress tests for the rate limiter."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.rate_limiter.limiter import SlidingWindowCounterLimiter


class TestConcurrentHits:
    """Spawn many threads hammering the same key simultaneously."""

    def test_exactly_max_requests_allowed(self) -> None:
        """Regardless of concurrency, exactly ``max_requests`` should be
        allowed within a single window (no previous-window contribution)."""
        max_requests = 50
        num_threads = 200
        limiter = SlidingWindowCounterLimiter(
            max_requests=max_requests, window_seconds=60
        )

        allowed_count = 0
        denied_count = 0
        lock = threading.Lock()

        def _hit() -> bool:
            return limiter.hit("shared-key").allowed

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_hit) for _ in range(num_threads)]
            for future in as_completed(futures):
                if future.result():
                    with lock:
                        allowed_count += 1
                else:
                    with lock:
                        denied_count += 1

        assert allowed_count == max_requests
        assert denied_count == num_threads - max_requests

    def test_multiple_users_concurrent(self) -> None:
        """Different users should not interfere with each other."""
        max_requests = 10
        num_users = 5
        hits_per_user = 20
        limiter = SlidingWindowCounterLimiter(
            max_requests=max_requests, window_seconds=60
        )

        results: dict[str, list[bool]] = {
            f"user-{i}": [] for i in range(num_users)
        }
        results_lock = threading.Lock()

        def _hit(user: str) -> None:
            allowed = limiter.hit(user).allowed
            with results_lock:
                results[user].append(allowed)

        with ThreadPoolExecutor(max_workers=num_users * hits_per_user) as pool:
            futures = []
            for uid in range(num_users):
                user = f"user-{uid}"
                for _ in range(hits_per_user):
                    futures.append(pool.submit(_hit, user))
            for f in as_completed(futures):
                f.result()  # propagate exceptions

        for user, outcomes in results.items():
            allowed = sum(outcomes)
            assert allowed == max_requests, (
                f"{user}: expected {max_requests} allowed, got {allowed}"
            )

    def test_cleanup_under_concurrent_hits(self) -> None:
        """Running cleanup while hits are in-flight should not corrupt state."""
        max_requests = 20
        limiter = SlidingWindowCounterLimiter(
            max_requests=max_requests, window_seconds=60
        )
        errors: list[Exception] = []

        def _hit_loop() -> None:
            try:
                for _ in range(50):
                    limiter.hit("concurrent-cleanup-user")
            except Exception as exc:
                errors.append(exc)

        def _cleanup_loop() -> None:
            try:
                for _ in range(50):
                    limiter.cleanup()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_hit_loop) for _ in range(10)
        ] + [
            threading.Thread(target=_cleanup_loop) for _ in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"Unexpected errors: {errors}"
