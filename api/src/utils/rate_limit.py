"""
Simple in-memory per-IP rate limiter for public endpoints.

Usage:
    from api.src.utils.rate_limit import RateLimiter

    # 20 requests per 60 seconds
    ai_demo_limiter = RateLimiter(max_requests=20, window_seconds=60)

    router = APIRouter(dependencies=[Depends(ai_demo_limiter)])
"""

import time
from collections import defaultdict
from fastapi import HTTPException, Request, status


class RateLimiter:
    """Callable FastAPI dependency that enforces per-IP rate limits."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def __call__(self, request: Request) -> None:
        ip = self._client_ip(request)
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune expired timestamps
        hits = self._hits[ip]
        self._hits[ip] = hits = [t for t in hits if t > cutoff]

        if len(hits) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
            )

        hits.append(now)
