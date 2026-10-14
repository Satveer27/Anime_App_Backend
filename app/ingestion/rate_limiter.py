import asyncio
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than 0")

        self.requests_per_minute = requests_per_minute
        self.interval: float = 60.0 / requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._last_request_time: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            if self._last_request_time is None:
                self._last_request_time = self._clock()
                return

            elapsed = self._clock() - self._last_request_time
            if elapsed < self.interval:
                time_to_wait = self.interval - elapsed
                await self._sleep(time_to_wait)

            self._last_request_time = self._clock()
