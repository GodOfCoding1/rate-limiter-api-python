"""Background daemon thread that periodically purges stale rate-limit entries."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rate_limiter.limiter import SlidingWindowCounterLimiter

logger = logging.getLogger(__name__)


class CleanupThread:
    """Periodically calls :meth:`SlidingWindowCounterLimiter.cleanup`.

    The thread is created as a **daemon** so it will not prevent the
    process from exiting.  Use :meth:`start` / :meth:`stop` (or the
    context-manager protocol) for explicit lifecycle control.

    Parameters:
        limiter: The rate limiter instance to clean up.
        interval: Seconds between cleanup sweeps.
    """

    def __init__(
        self,
        limiter: SlidingWindowCounterLimiter,
        interval: float = 60.0,
    ) -> None:
        self._limiter = limiter
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background cleanup thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Cleanup thread is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rate-limiter-cleanup",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Cleanup thread started (interval=%ss).", self._interval
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the thread to stop and wait up to *timeout* seconds."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "Cleanup thread did not stop within %ss.", timeout
                )
            else:
                logger.info("Cleanup thread stopped.")
            self._thread = None

    # Context-manager support
    def __enter__(self) -> "CleanupThread":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Thread target: loop until the stop event is set."""
        while not self._stop_event.is_set():
            # Wait for the interval or until stop is requested.
            if self._stop_event.wait(timeout=self._interval):
                break
            try:
                self._limiter.cleanup()
                logger.debug("Cleanup sweep completed.")
            except Exception:
                logger.exception("Error during rate-limiter cleanup.")
