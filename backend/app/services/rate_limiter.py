import time
from collections import defaultdict
from typing import Dict, List, Callable
from fastapi import Request
from backend.app.config import get_settings, Settings


class SlidingWindowRateLimiter:
    def __init__(self):
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def reset(self):
        self._requests.clear()

    def check_rate_limit(self, client_ip: str, action_key: str, limit_per_min: int) -> tuple[bool, int]:
        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return True, 0

        now = time.time()
        window_start = now - 60.0
        key = f"{client_ip}:{action_key}"

        timestamps = [t for t in self._requests[key] if t > window_start]
        self._requests[key] = timestamps

        if len(timestamps) >= limit_per_min:
            oldest = timestamps[0]
            retry_after = max(1, int(60.0 - (now - oldest)))
            return False, retry_after

        self._requests[key].append(now)
        return True, 0


limiter = SlidingWindowRateLimiter()


def rate_limit(action_key: str, limit_getter: Callable[[Settings], int]):
    async def dependency(request: Request):
        from backend.app.main import PipelineException

        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return

        limit = limit_getter(settings)
        client_ip = request.client.host if request.client else "127.0.0.1"

        allowed, retry_after = limiter.check_rate_limit(client_ip, action_key, limit)
        if not allowed:
            raise PipelineException(
                code="RATE_LIMITED",
                message="Rate limit exceeded. Try again later.",
                stage=None,
                status_code=429,
                retry_after=retry_after,
            )

    return dependency
