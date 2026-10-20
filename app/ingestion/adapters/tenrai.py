import asyncio
from collections.abc import Awaitable, Callable

import httpx
import structlog

from app.ingestion.exceptions import GoneError, NetworkError, PermanentError, TransientError
from app.ingestion.utils.circuit_breaker import CircuitBreaker
from app.ingestion.utils.rate_limiter import RateLimiter

log = structlog.get_logger(__name__)
RETRYABLE_STATUSES = {500, 502, 503, 504}
DEFAULT_RETRY_AFTER_S = 30.0
USER_AGENT = "AnimePlanet https://github.com/Satveer27/Anime_App_Backend"


class TenraiAdapter:
    def __init__(
        self,
        rate_limiter: RateLimiter,
        base_url: str,
        max_attempts: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._rate_limiter = rate_limiter
        self._max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(30, connect=5),
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )
        self._breaker = breaker or CircuitBreaker()
        self._sleep = sleep
        # Cumulative over the adapter's life; jobs report the difference over their run.
        self.request_count = 0  # HTTP requests sent, retries included
        self.rate_limited_count = 0

    @staticmethod
    def _retry_after(r: httpx.Response) -> float:
        try:
            return float(r.headers.get("Retry-After", DEFAULT_RETRY_AFTER_S))
        except ValueError:
            return DEFAULT_RETRY_AFTER_S

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, endpoint: str, params: dict | None = None) -> dict:
        for attempt in range(1, self._max_attempts + 1):
            backoff = 2 ** (attempt - 1)

            try:
                await self._rate_limiter.acquire()
                self.request_count += 1
                r = await self._client.get(endpoint, params=params)
            except httpx.TransportError as e:
                last_error = type(e).__name__
                last_status = None
                last_was_network = True
                wait = backoff
                log.warning(
                    "tenrai_network_error",
                    endpoint=endpoint,
                    error=last_error,
                    attempt=attempt,
                    wait_s=wait,
                )
            else:
                if r.status_code == 429:
                    last_error = "429"
                    last_status = 429
                    last_was_network = False
                    wait = self._retry_after(r)
                    self.rate_limited_count += 1
                    log.warning(
                        "tenrai_rate_limited", endpoint=endpoint, wait_s=wait, attempt=attempt
                    )
                elif r.status_code in RETRYABLE_STATUSES:
                    last_error = str(r.status_code)
                    last_status = r.status_code
                    last_was_network = False
                    wait = backoff
                    log.warning(
                        "tenrai_server_error",
                        endpoint=endpoint,
                        status=r.status_code,
                        attempt=attempt,
                        wait_s=wait,
                    )
                else:
                    if r.status_code == 404:
                        raise GoneError(f"404 {endpoint}", status_code=404)
                    if r.status_code != 200:
                        raise PermanentError(
                            f"{r.status_code} {endpoint}", status_code=r.status_code
                        )
                    try:
                        return r.json()
                    except ValueError as e:
                        raise PermanentError(f"invalid JSON {endpoint}", status_code=200) from e

            if attempt < self._max_attempts:
                await self._sleep(wait)

        message = f"{endpoint} failed after {self._max_attempts} attempts: {last_error}"
        raise (NetworkError if last_was_network else TransientError)(
            message, status_code=last_status
        )

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        self._breaker.check()
        try:
            data = await self._fetch(endpoint, params)
        except TransientError:
            self._breaker.record_failure()
            if self._breaker.is_open:
                log.error("tenrai_circuit_open", endpoint=endpoint)
            raise
        except (GoneError, PermanentError):
            # Tenrai answered, so it is up.
            was_open = self._breaker.is_open
            self._breaker.record_success()
            if was_open:
                log.info("tenrai_circuit_closed", endpoint=endpoint)
            raise

        was_open = self._breaker.is_open
        self._breaker.record_success()
        if was_open:
            log.info("tenrai_circuit_closed", endpoint=endpoint)

        return data

    async def get_full(self, anime_id: int) -> dict:
        return await self._get(f"/anime/{anime_id}/full")

    async def get_statistics(self, anime_id: int) -> dict:
        return await self._get(f"/anime/{anime_id}/statistics")

    async def get_episodes(self, anime_id: int, page: int = 1) -> dict:
        return await self._get(f"/anime/{anime_id}/episodes", {"page": page})

    async def get_reviews(self, anime_id: int, page: int = 1) -> dict:
        return await self._get(f"/anime/{anime_id}/reviews", {"page": page})

    async def list_anime(
        self,
        page: int = 1,
        limit: int = 100,
        rating: str = "g,pg,pg13,r17,r",
        **filters: str | int | None,
    ) -> dict:
        """List anime. Extra keyword filters (e.g. type, status) go straight into the query."""
        params = {"page": page, "limit": limit, "rating": rating}
        params.update({k: v for k, v in filters.items() if v is not None})
        return await self._get("/anime", params)
