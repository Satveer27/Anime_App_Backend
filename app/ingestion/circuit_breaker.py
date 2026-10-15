import time
from collections.abc import Callable

from app.ingestion.exceptions import CircuitOpenError


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be greater than 0")
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def check(self) -> None:
        if self._opened_at is None:
            return
        remaining = self._cooldown_s - (self._clock() - self._opened_at)
        if remaining > 0:
            raise CircuitOpenError(retry_after_s=remaining)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._opened_at = self._clock()
