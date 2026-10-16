import asyncio
from itertools import pairwise

import pytest

from app.ingestion.rate_limiter import RateLimiter


class FakeTime:
    """Fake clock whose sleep advances the clock and records each wait."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


def make_limiter(fake_time: FakeTime, requests_per_minute: float = 100) -> RateLimiter:
    return RateLimiter(requests_per_minute, clock=fake_time.clock, sleep=fake_time.sleep)


async def acquire_times(limiter: RateLimiter, fake_time: FakeTime, count: int) -> list[float]:
    starts = []
    for _ in range(count):
        await limiter.acquire()
        starts.append(fake_time.now)
    return starts


def test_interval_is_sixty_over_rate(fake_time):
    assert make_limiter(fake_time, 100)._interval == pytest.approx(0.6)


@pytest.mark.parametrize("rate", [0, -1])
def test_non_positive_rate_is_rejected(fake_time, rate):
    with pytest.raises(ValueError):
        make_limiter(fake_time, rate)


async def test_first_call_does_not_wait(fake_time):
    limiter = make_limiter(fake_time)

    await limiter.acquire()

    assert fake_time.sleeps == []


async def test_consecutive_calls_start_at_least_interval_apart(fake_time):
    limiter = make_limiter(fake_time, 100)

    starts = await acquire_times(limiter, fake_time, 5)

    gaps = [b - a for a, b in pairwise(starts)]
    assert all(gap >= limiter._interval - 1e-9 for gap in gaps)


async def test_call_after_partial_wait_sleeps_only_the_remainder(fake_time):
    limiter = make_limiter(fake_time, 100)
    await limiter.acquire()
    fake_time.now += 0.25

    await limiter.acquire()

    assert fake_time.sleeps == [pytest.approx(0.35)]


async def test_no_burst_after_long_idle_period(fake_time):
    limiter = make_limiter(fake_time, 100)
    await limiter.acquire()
    fake_time.now += 3600

    starts = await acquire_times(limiter, fake_time, 4)

    gaps = [b - a for a, b in pairwise(starts)]
    assert all(gap >= limiter._interval - 1e-9 for gap in gaps)
    assert len(fake_time.sleeps) == 3


@pytest.mark.parametrize("rate", [60, 100, 110])
async def test_rate_setting_changes_spacing(fake_time, rate):
    limiter = make_limiter(fake_time, rate)

    starts = await acquire_times(limiter, fake_time, 3)

    assert starts[1] - starts[0] == pytest.approx(60 / rate)
    assert starts[2] - starts[1] == pytest.approx(60 / rate)


async def test_concurrent_callers_are_spaced_out(fake_time):
    limiter = make_limiter(fake_time, 100)
    starts: list[float] = []

    async def worker():
        await limiter.acquire()
        starts.append(fake_time.now)

    await asyncio.gather(*(worker() for _ in range(4)))

    gaps = [b - a for a, b in pairwise(starts)]
    assert all(gap >= limiter._interval - 1e-9 for gap in gaps)
