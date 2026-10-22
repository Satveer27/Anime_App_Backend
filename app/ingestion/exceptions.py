class AdapterError(Exception):
    """Base class for adapter errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoneError(AdapterError):
    """404: the resource doesn't exist. Don't retry."""


class PermanentError(AdapterError):
    """400 or other 4xx: retrying the same request won't help."""


class AccessDeniedError(AdapterError):
    """401 or 403: the source refuses us (a block, a bad User-Agent, a changed key), for every
    request and not for this one anime. Retrying or marking the task dead would be wrong, so
    the run stops."""


class TransientError(AdapterError):
    """5xx, 429 or network errors that outlasted the quick retries."""


class NetworkError(TransientError):
    """The last attempt failed at the network level (refused, timeout)."""


class CircuitOpenError(AdapterError):
    """The breaker is open: the API is treated as down, so no request was sent."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"circuit open, retry in {retry_after_s:.0f}s")
        self.retry_after_s = retry_after_s


class RawStoreError(Exception):
    """The raw payload store (S3) failed, after the client's own retries, or refused us.

    Not a problem with one anime: the fetch job releases the task and stops the run.
    """


class RepeatedUnexpectedErrors(Exception):
    """Several tasks in a row failed in our own code, so the run stopped instead of burning more."""

    def __init__(self, count: int) -> None:
        super().__init__(f"{count} unexpected errors in a row")
        self.count = count
