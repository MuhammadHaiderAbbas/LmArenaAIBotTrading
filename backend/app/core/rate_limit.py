"""Minimal in-process fixed-window rate limiter.

Used to blunt credential stuffing against the auth endpoints. In multi-instance
deployments this moves to Redis (REDIS_URL is already provisioned); the call
signature stays the same so the swap is local to this module.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_buckets: dict[str, deque[float]] = defaultdict(deque)


def _client_key(request: Request, scope: str) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    return f"{scope}:{ip}"


def enforce_rate_limit(request: Request, scope: str, limit: int, window_seconds: int = 60) -> None:
    now = time.monotonic()
    bucket = _buckets[_client_key(request, scope)]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = int(window_seconds - (now - bucket[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again shortly.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    bucket.append(now)


def reset_rate_limits() -> None:
    """Test helper."""
    _buckets.clear()
