import pytest
from pydantic import BaseModel
from redis.exceptions import RedisError

from app.core.cache import CACHE_REQUESTS, ResponseCache, _family


class Payload(BaseModel):
    value: int


def count(family: str, result: str) -> float:
    return CACHE_REQUESTS.labels(family, result)._value.get()


@pytest.fixture
def redis(mocker):
    fake = mocker.AsyncMock()
    fake.get.return_value = None
    return fake


@pytest.fixture
def cache(redis):
    return ResponseCache(redis, enabled=True, ttl_seconds=60, max_value_bytes=1000)


def loader(mocker, value=1):
    return mocker.AsyncMock(return_value=Payload(value=value))


class TestFamily:
    def test_it_is_the_endpoint_name_in_the_key(self):
        assert _family("cache:v1:aggregate_by_tag:type=tv") == "aggregate_by_tag"

    def test_a_key_in_another_shape_does_not_break_the_counter(self):
        assert _family("odd") == "unknown"


class TestCounters:
    async def test_a_miss_then_a_hit(self, cache, redis, mocker):
        before_miss, before_hit = count("fam_a", "miss"), count("fam_a", "hit")

        await cache.get_or_set("cache:v1:fam_a:k=1", Payload, loader(mocker))
        redis.get.return_value = '{"value":1}'
        await cache.get_or_set("cache:v1:fam_a:k=1", Payload, loader(mocker))

        assert count("fam_a", "miss") == before_miss + 1
        assert count("fam_a", "hit") == before_hit + 1

    async def test_a_redis_error_counts_as_an_error(self, cache, redis, mocker):
        redis.get.side_effect = RedisError("down")
        before = count("fam_b", "error")

        await cache.get_or_set("cache:v1:fam_b:k=1", Payload, loader(mocker))

        assert count("fam_b", "error") == before + 1

    async def test_a_value_that_does_not_parse_counts_as_an_error(self, cache, redis, mocker):
        redis.get.return_value = '{"value":"x"}'
        before = count("fam_c", "error")

        await cache.get_or_set("cache:v1:fam_c:k=1", Payload, loader(mocker))

        assert count("fam_c", "error") == before + 1

    async def test_a_response_that_is_not_stored_counts_as_skipped(self, cache, mocker):
        before = count("fam_d", "skipped")

        await cache.get_or_set(
            "cache:v1:fam_d:k=1", Payload, loader(mocker), store_if=lambda response: False
        )

        assert count("fam_d", "skipped") == before + 1

    async def test_a_response_over_the_size_cap_counts_as_skipped(self, redis, mocker):
        small = ResponseCache(redis, enabled=True, ttl_seconds=60, max_value_bytes=3)
        before = count("fam_e", "skipped")

        await small.get_or_set("cache:v1:fam_e:k=1", Payload, loader(mocker, 123456))

        assert count("fam_e", "skipped") == before + 1
