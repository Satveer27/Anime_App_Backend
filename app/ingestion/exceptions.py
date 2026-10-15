class AdapterError(Exception):
    """Base class for adapter errors."""


class GoneError(AdapterError):
    """404: the resource doesn't exist. Don't retry."""


class PermanentError(AdapterError):
    """400 or other 4xx: retrying the same request won't help."""


class TransientError(AdapterError):
    """5xx, 429 or network errors that outlasted the quick retries."""


class NetworkError(TransientError):
    """The last attempt failed at the network level (refused, timeout)."""


class CircuitOpenError(AdapterError):
    """The breaker is open: the API is treated as down, so no request was sent."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"circuit open, retry in {retry_after_s:.0f}s")
        self.retry_after_s = retry_after_s
