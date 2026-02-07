# Rate Limiter

> This project was built using **multi-step planning with agentic LLMs** — the design was iteratively refined through structured Q&A, a formal architecture plan was produced, and the implementation was executed step-by-step following that plan.

## Implementation

### Algorithm: Sliding Window Counter

We use the **Sliding Window Counter** approach, which provides a good balance between accuracy and memory efficiency. For each user/API key, the limiter maintains just two fixed-window counters (previous and current). On every incoming request it:

1. Acquires a **per-user lock** (so different users never contend).
2. Advances the window if the current fixed window has expired.
3. Computes a weighted estimate: `estimated = prev_count × weight + curr_count`, where `weight = (window_size − elapsed) / window_size`.
4. If the estimate is below `max_requests`, increments the current counter and allows the request; otherwise rejects it with HTTP 429.

This provides **O(1) memory per user** (compared to O(N) for a sliding-window log) while closely approximating the behavior of a true sliding window.

### Thread Safety

A **two-level locking** strategy ensures correctness under heavy concurrency:

- A **global lock** guards the counter dictionary (held briefly during user lookup/creation). All lookups acquire this lock for safety on free-threaded Python builds (no-GIL).
- A **per-user lock** protects each user's counters, so concurrent requests from different users never block each other.

### Pure ASGI Middleware

Rate limiting is enforced via a **pure ASGI middleware** (no `BaseHTTPMiddleware`), which avoids known performance and streaming issues. The middleware:

- Intercepts `http.response.start` messages to inject `X-RateLimit-*` headers without wrapping the entire response.
- Supports a configurable set of **exempt paths** (e.g. `/` and `/status`) that bypass rate limiting entirely.
- Forwards non-HTTP scopes (WebSocket, lifespan) unchanged.

### Background Cleanup

A **daemon thread** runs on a configurable interval (default 60s) to:

- Advance windows and check all users' counters.
- Remove users whose both windows are fully expired, reclaiming memory and preventing leaks.

The thread is managed via FastAPI's `lifespan` context manager for clean startup/shutdown.

### API Key Extraction

The middleware identifies clients using a shared `extract_client_key` function, in the following priority order:

1. `X-API-Key` request header
2. `api_key` query parameter
3. Client IP address (fallback)

### HTTP 429 Response

When a client exceeds the limit, they receive:

- **Status**: `429 Too Many Requests`
- **Body**: `{"detail": "Rate limit exceeded. Try again later.", "retry_after": <seconds>}`
- **Headers**: `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`

Rate-limit headers are also included on every successful response (except for exempt paths).

## Project Structure

```
be-uno/
  requirements.txt
  app/
    __init__.py
    main.py                  # FastAPI app, lifespan, middleware, demo routes
    config.py                # Settings via pydantic-settings (env vars)
    rate_limiter/
      __init__.py
      limiter.py             # SlidingWindowCounterLimiter (core engine)
      middleware.py           # Pure ASGI RateLimitMiddleware
      cleanup.py             # Background cleanup daemon thread
      keys.py                # Shared client key extraction utility
      exceptions.py          # RateLimitExceeded exception
  tests/
    __init__.py
    conftest.py              # Shared pytest fixtures
    test_limiter.py          # Unit tests for core limiter logic
    test_middleware.py        # Integration tests via FastAPI TestClient
    test_concurrency.py      # Multi-threaded stress tests
```

## Configuration

All settings are configurable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Max requests allowed per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window size in seconds |
| `CLEANUP_INTERVAL_SECONDS` | `60` | Background cleanup sweep interval |
| `RATE_LIMIT_EXEMPT_PATHS` | `["/", "/status"]` | URL paths that bypass rate limiting |

## Getting Started

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run the server

```bash
uvicorn app.main:app --reload
```

### Run the tests

```bash
python -m pytest tests/ -v
```

## Known Issues

### `get_settings()` is not actually cached

The `get_settings()` function in `config.py` has a docstring that says "Return a cached Settings instance", but it creates a new `Settings()` object on every call. It is missing a `@functools.lru_cache` (or `@functools.cache`) decorator. Currently this is only called once during `create_app()` so it has no runtime impact, but the misleading docstring could lead future developers to call it repeatedly expecting it to be free.

### `peek()` allocates counters for unseen keys — potential memory exhaustion

The `peek()` method in `SlidingWindowCounterLimiter` calls `_get_or_create_counter()`, which inserts a new `_WindowCounter` into the internal dictionary for every previously-unseen key. The `/status` endpoint calls `peek()` and is **exempt from rate limiting**. This means an attacker could send a large number of requests to `/status` with unique `X-API-Key` headers, each creating a new counter object in memory — completely bypassing rate limits. The background cleanup thread (default interval 60s) will eventually purge these empty entries, but in the meantime memory usage can spike. The fix would be for `peek()` to return a default result when the key doesn't exist instead of creating a counter.

## Tests

The test suite covers 31 test cases across three categories:

- **Unit tests** (`test_limiter.py`) — allow/deny logic, full and partial window expiry, multiple users, edge cases (0 limit, 1 limit), peek, reset, cleanup, retry-after values.
- **Integration tests** (`test_middleware.py`) — full HTTP round-trips via `TestClient`: 200s, 429s, correct headers, key extraction priority, exempt path bypass, route payloads.
- **Concurrency tests** (`test_concurrency.py`) — 200 threads hitting a single key (verifies exactly N allowed), multi-user concurrent access, and cleanup-under-load safety.
