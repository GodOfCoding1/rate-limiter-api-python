"""Client key extraction utilities for rate limiting."""

from __future__ import annotations

from starlette.requests import Request


def extract_client_key(request: Request) -> str:
    """Extract a client identifier from the request.

    Priority:
        1. ``X-API-Key`` header
        2. ``api_key`` query parameter
        3. Client IP address (``request.client.host``)
        4. ``"unknown"`` as a last resort
    """
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key

    api_key = request.query_params.get("api_key")
    if api_key:
        return api_key

    if request.client:
        return request.client.host

    return "unknown"
