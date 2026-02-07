"""Integration tests for the pure ASGI middleware and routes."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestRateLimitHeaders:
    """Verify that rate-limit headers are present on responses."""

    def test_successful_request_has_headers(self, client: TestClient) -> None:
        resp = client.get("/ping", headers={"X-API-Key": "hdr-user"})
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers
        assert resp.headers["X-RateLimit-Limit"] == "5"

    def test_remaining_decreases(self, client: TestClient) -> None:
        for i in range(3):
            resp = client.get("/ping", headers={"X-API-Key": "dec-user"})
            assert resp.status_code == 200
            assert resp.headers["X-RateLimit-Remaining"] == str(5 - i - 1)


class TestRateLimitEnforcement:
    """Verify that requests are denied after exceeding the limit."""

    def test_429_after_limit(self, client: TestClient) -> None:
        for _ in range(5):
            resp = client.get("/ping", headers={"X-API-Key": "block-user"})
            assert resp.status_code == 200

        resp = client.get("/ping", headers={"X-API-Key": "block-user"})
        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body
        assert "retry_after" in body
        assert "Retry-After" in resp.headers

    def test_different_keys_are_independent(self, client: TestClient) -> None:
        # Exhaust key-a
        for _ in range(5):
            client.get("/ping", headers={"X-API-Key": "key-a"})
        assert (
            client.get("/ping", headers={"X-API-Key": "key-a"}).status_code
            == 429
        )

        # key-b should still work
        resp = client.get("/ping", headers={"X-API-Key": "key-b"})
        assert resp.status_code == 200


class TestKeyExtraction:
    """Ensure the middleware extracts keys in the correct priority order."""

    def test_header_takes_priority(self, client: TestClient) -> None:
        # Exhaust via header key
        for _ in range(5):
            client.get(
                "/ping?api_key=qp-user",
                headers={"X-API-Key": "hdr-priority"},
            )
        # Header key should be exhausted
        resp = client.get("/ping", headers={"X-API-Key": "hdr-priority"})
        assert resp.status_code == 429

        # Query param key should still be fine
        resp = client.get("/ping?api_key=qp-user")
        assert resp.status_code == 200

    def test_query_param_fallback(self, client: TestClient) -> None:
        resp = client.get("/ping?api_key=my-key")
        assert resp.status_code == 200

    def test_ip_fallback(self, client: TestClient) -> None:
        # TestClient uses 'testclient' as the host by default
        resp = client.get("/ping")
        assert resp.status_code == 200


class TestExemptPaths:
    """Verify that exempt paths bypass rate limiting."""

    def test_root_is_exempt(self, client: TestClient) -> None:
        """The ``/`` endpoint should never be rate-limited."""
        for _ in range(10):
            resp = client.get("/")
            assert resp.status_code == 200
        # No rate-limit headers on exempt paths
        assert "X-RateLimit-Limit" not in resp.headers

    def test_status_is_exempt(self, client: TestClient) -> None:
        """The ``/status`` endpoint should never be rate-limited."""
        for _ in range(10):
            resp = client.get("/status", headers={"X-API-Key": "exempt-status"})
            assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers


class TestRoutes:
    """Verify demo routes return expected payloads."""

    def test_root(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_ping(self, client: TestClient) -> None:
        resp = client.get("/ping", headers={"X-API-Key": "route-ping"})
        assert resp.status_code == 200
        assert resp.json() == {"message": "pong"}

    def test_status(self, client: TestClient) -> None:
        resp = client.get("/status", headers={"X-API-Key": "route-status"})
        assert resp.status_code == 200
        body = resp.json()
        assert "limit" in body
        assert "remaining" in body
        assert "reset" in body
