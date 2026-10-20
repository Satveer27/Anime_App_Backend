import httpx
import pytest

from app.ingestion.adapters.tenrai import TenraiAdapter
from app.ingestion.exceptions import (
    CircuitOpenError,
    GoneError,
    NetworkError,
    PermanentError,
    TransientError,
)
from app.ingestion.utils.circuit_breaker import CircuitBreaker

OK_BODY = {"data": {"mal_id": 1}}


class FakeLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1


class Harness:
    """Adapter wired to a scripted MockTransport, a fake limiter, a fake clock and sleep."""

    def __init__(self, script: list, max_attempts: int = 5) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.now = 1000.0
        self.limiter = FakeLimiter()
        self.breaker = CircuitBreaker(clock=lambda: self.now)
        self.adapter = TenraiAdapter(
            self.limiter,
            "https://tenrai.test/v1",
            max_attempts=max_attempts,
            transport=httpx.MockTransport(self._handle),
            breaker=self.breaker,
            sleep=self._sleep,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def ok() -> httpx.Response:
    return httpx.Response(200, json=OK_BODY)


def status(code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(code, headers=headers)


async def test_success_returns_json_and_sends_request_details():
    h = Harness([ok()])

    assert await h.adapter.get_full(1) == OK_BODY

    request = h.requests[0]
    assert str(request.url) == "https://tenrai.test/v1/anime/1/full"
    assert "AnimePlanet" in request.headers["User-Agent"]
    assert h.sleeps == []


async def test_404_is_gone_and_not_retried():
    h = Harness([status(404)])

    with pytest.raises(GoneError):
        await h.adapter.get_full(1)

    assert len(h.requests) == 1


@pytest.mark.parametrize("code", [400, 403, 422])
async def test_other_4xx_is_permanent_and_not_retried(code):
    h = Harness([status(code)])

    with pytest.raises(PermanentError):
        await h.adapter.get_full(1)

    assert len(h.requests) == 1
    assert h.sleeps == []


async def test_invalid_json_is_permanent():
    h = Harness([httpx.Response(200, content=b"not json")])

    with pytest.raises(PermanentError):
        await h.adapter.get_full(1)


async def test_429_waits_retry_after_then_succeeds():
    h = Harness([status(429, {"Retry-After": "7"}), ok()])

    assert await h.adapter.get_full(1) == OK_BODY

    assert h.sleeps == [7]


async def test_429_without_retry_after_waits_30s():
    h = Harness([status(429), ok()])

    await h.adapter.get_full(1)

    assert h.sleeps == [30]


async def test_429_with_unparsable_retry_after_waits_30s():
    h = Harness([status(429, {"Retry-After": "soon"}), ok()])

    await h.adapter.get_full(1)

    assert h.sleeps == [30]


@pytest.mark.parametrize("code", [500, 502, 503, 504])
async def test_5xx_is_retried_with_backoff(code):
    h = Harness([status(code), ok()])

    assert await h.adapter.get_full(1) == OK_BODY

    assert h.sleeps == [1]
    assert len(h.requests) == 2


async def test_transport_error_is_retried():
    h = Harness([httpx.ConnectError("boom"), ok()])

    assert await h.adapter.get_full(1) == OK_BODY

    assert h.sleeps == [1]


async def test_retries_run_out_with_exponential_backoff():
    h = Harness([status(503)] * 5)

    with pytest.raises(TransientError):
        await h.adapter.get_full(1)

    assert len(h.requests) == 5
    assert h.sleeps == [1, 2, 4, 8]  # no sleep after the last attempt


async def test_transport_errors_running_out_raise_transient():
    h = Harness([httpx.ReadTimeout("slow")] * 5)

    with pytest.raises(TransientError):
        await h.adapter.get_full(1)

    assert len(h.requests) == 5


async def test_rate_limiter_is_acquired_before_every_attempt():
    h = Harness([status(503), status(503), ok()])

    await h.adapter.get_full(1)

    assert h.limiter.acquired == 3


async def test_endpoints_and_params():
    h = Harness([ok(), ok(), ok()])

    await h.adapter.get_episodes(7, page=2)
    await h.adapter.get_reviews(7)
    await h.adapter.list_anime(page=3, limit=50)

    assert h.requests[0].url.path == "/v1/anime/7/episodes"
    assert h.requests[0].url.params["page"] == "2"
    assert h.requests[1].url.path == "/v1/anime/7/reviews"
    assert h.requests[2].url.path == "/v1/anime"
    assert h.requests[2].url.params["limit"] == "50"
    assert "rating" in h.requests[2].url.params


async def fail_tasks(h: Harness, count: int, response=None) -> None:
    """Run `count` anime-id fetches that each exhaust all 5 attempts."""
    h.script = [(response or status(503)) for _ in range(count * 5)]
    for anime_id in range(count):
        with pytest.raises(TransientError):
            await h.adapter.get_full(anime_id)


async def test_breaker_stays_closed_after_4_failed_tasks():
    h = Harness([])

    await fail_tasks(h, 4)

    assert not h.breaker.is_open


async def test_breaker_opens_after_5_failed_tasks_and_blocks_requests():
    h = Harness([])
    await fail_tasks(h, 5)
    assert h.breaker.is_open
    assert len(h.requests) == 25  # 5 tasks x 5 attempts

    with pytest.raises(CircuitOpenError):
        await h.adapter.get_full(99)

    assert len(h.requests) == 25  # the rejected call sent nothing


async def test_breaker_counts_429s_too():
    h = Harness([])
    await fail_tasks(h, 5, response=status(429, {"Retry-After": "1"}))

    assert h.breaker.is_open


async def test_tasks_that_run_out_of_network_retries_count_toward_the_breaker():
    h = Harness([httpx.ConnectError("connection refused")] * 20 + [httpx.ReadTimeout("slow")] * 5)

    for anime_id in range(5):
        with pytest.raises(NetworkError):
            await h.adapter.get_full(anime_id)

    assert h.breaker.is_open


async def test_retries_that_recover_from_network_errors_do_not_count_as_failed_tasks():
    h = Harness([httpx.ConnectError("connection refused"), httpx.ReadTimeout("slow"), ok()] * 5)

    for anime_id in range(5):
        await h.adapter.get_full(anime_id)

    assert not h.breaker.is_open


async def test_success_resets_the_failed_task_count():
    h = Harness([])
    await fail_tasks(h, 4)
    h.script = [ok()]
    await h.adapter.get_full(50)

    await fail_tasks(h, 4)

    assert not h.breaker.is_open


async def test_404_counts_as_success_for_the_breaker():
    h = Harness([])
    await fail_tasks(h, 4)
    h.script = [status(404)]
    with pytest.raises(GoneError):
        await h.adapter.get_full(50)

    await fail_tasks(h, 4)

    assert not h.breaker.is_open


async def test_breaker_closes_after_a_successful_probe():
    h = Harness([])
    await fail_tasks(h, 5)
    h.now += 300
    h.script = [ok(), ok()]

    assert await h.adapter.get_full(50) == OK_BODY

    assert not h.breaker.is_open
    assert await h.adapter.get_full(51) == OK_BODY


async def test_failed_probe_reopens_the_breaker():
    h = Harness([])
    await fail_tasks(h, 5)
    h.now += 300
    h.script = [status(503)] * 5

    with pytest.raises(TransientError):
        await h.adapter.get_full(50)

    assert len(h.requests) == 30  # the probe task got its full 5 attempts
    with pytest.raises(CircuitOpenError):
        await h.adapter.get_full(51)


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_rejects_invalid_max_attempts(max_attempts):
    with pytest.raises(ValueError):
        TenraiAdapter(FakeLimiter(), "https://tenrai.test/v1", max_attempts=max_attempts)


async def test_list_anime_passes_extra_filters_and_drops_none():
    h = Harness([ok()])

    await h.adapter.list_anime(page=2, type="tv", status=None)

    params = h.requests[0].url.params
    assert params["type"] == "tv"
    assert params["page"] == "2"
    assert "status" not in params
    assert "rating" in params


async def test_get_statistics_hits_the_statistics_endpoint():
    h = Harness([ok()])

    await h.adapter.get_statistics(7)

    assert h.requests[0].url.path == "/v1/anime/7/statistics"
