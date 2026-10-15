import pytest

from app.ingestion.circuit_breaker import CircuitBreaker
from app.ingestion.exceptions import CircuitOpenError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=5, cooldown_s=300, clock=clock)


def fail(breaker: CircuitBreaker, times: int) -> None:
    for _ in range(times):
        breaker.record_failure()


def test_stays_closed_below_threshold(breaker):
    fail(breaker, 4)

    assert not breaker.is_open
    breaker.check()


def test_opens_at_threshold_and_rejects(breaker):
    fail(breaker, 5)

    assert breaker.is_open
    with pytest.raises(CircuitOpenError) as exc:
        breaker.check()
    assert exc.value.retry_after_s == 300


def test_success_resets_the_failure_count(breaker):
    fail(breaker, 4)
    breaker.record_success()
    fail(breaker, 4)

    assert not breaker.is_open


def test_allows_a_probe_after_cooldown(breaker, clock):
    fail(breaker, 5)
    clock.now += 300

    breaker.check()


def test_successful_probe_closes_the_breaker(breaker, clock):
    fail(breaker, 5)
    clock.now += 300
    breaker.check()
    breaker.record_success()

    assert not breaker.is_open
    breaker.check()


def test_failed_probe_reopens_for_a_full_cooldown(breaker, clock):
    fail(breaker, 5)
    clock.now += 300
    breaker.check()
    breaker.record_failure()

    with pytest.raises(CircuitOpenError) as exc:
        breaker.check()
    assert exc.value.retry_after_s == 300


@pytest.mark.parametrize("kwargs", [{"failure_threshold": 0}, {"cooldown_s": 0}])
def test_rejects_invalid_config(kwargs):
    with pytest.raises(ValueError):
        CircuitBreaker(**kwargs)
