from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from redis.exceptions import RedisError, ResponseError
from structlog.testing import capture_logs

from app.anime.option_service import OptionsService
from app.anime.service import AnimeListService, current_season
from app.catalogue.enums import (
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    GroupSort,
    Order,
    StudioSort,
    TagMatch,
)
from app.catalogue.read_repository import CatalogueReadRepository
from app.core.cache import ResponseCache, cached, make_key
from app.statistics.aggregate_service import AggregateService
from app.statistics.list_service import ListService
from app.statistics.service import _is_popular


class Payload(BaseModel):
    value: int


@pytest.fixture
def redis(mocker):
    fake = mocker.AsyncMock()
    fake.get.return_value = None
    return fake


@pytest.fixture
def cache(redis):
    return ResponseCache(redis, enabled=True, ttl_seconds=3600, max_value_bytes=1000)


def loader_returning(value, mocker):
    return mocker.AsyncMock(return_value=Payload(value=value))


class TestMakeKey:
    def test_enums_ints_bools_and_none_are_allowed(self):
        key = make_key("by_tag", type=AnimeType.TV, sort=GroupSort.MEAN_SCORE, year=None, n=5)

        assert key == "cache:v1:by_tag:n=5:sort=mean_score:type=tv:year=None"

    def test_the_order_of_the_parts_does_not_change_the_key(self):
        assert make_key("x", a=1, b=2) == make_key("x", b=2, a=1)

    @pytest.mark.parametrize("value", ["free text", 1.5, ["a"], ("a",), {"a": 1}, [1, "a"]])
    def test_free_text_floats_and_collections_are_refused(self, value):
        with pytest.raises(TypeError):
            make_key("x", q=value)

    def test_a_list_of_enums_or_ints_becomes_one_sorted_part(self):
        key = make_key("x", status=[AnimeStatus.UPCOMING, AnimeStatus.AIRING], tag=[3, 1])

        assert key == "cache:v1:x:status=airing+upcoming:tag=1+3"

    def test_the_order_a_client_sends_a_list_in_does_not_make_a_second_key(self):
        assert make_key("x", s=[AnimeSeason.FALL, AnimeSeason.SPRING]) == make_key(
            "x", s=[AnimeSeason.SPRING, AnimeSeason.FALL]
        )


class TestGetOrSet:
    async def test_a_miss_runs_the_loader_and_stores_the_result_with_a_ttl(
        self, cache, redis, mocker
    ):
        loader = loader_returning(7, mocker)

        result = await cache.get_or_set("k", Payload, loader)

        assert result == Payload(value=7)
        loader.assert_awaited_once()
        redis.set.assert_awaited_once_with("k", '{"value":7}', ex=3600)

    async def test_a_hit_skips_the_loader(self, cache, redis, mocker):
        redis.get.return_value = '{"value":9}'
        loader = loader_returning(7, mocker)

        result = await cache.get_or_set("k", Payload, loader)

        assert result == Payload(value=9)
        loader.assert_not_awaited()
        redis.set.assert_not_awaited()

    async def test_a_disabled_cache_never_touches_redis(self, redis, mocker):
        off = ResponseCache(redis, enabled=False, ttl_seconds=60, max_value_bytes=1000)
        loader = loader_returning(7, mocker)

        assert await off.get_or_set("k", Payload, loader) == Payload(value=7)
        redis.get.assert_not_awaited()
        redis.set.assert_not_awaited()

    async def test_a_redis_error_on_read_falls_through_to_the_loader(self, cache, redis, mocker):
        redis.get.side_effect = RedisError("down")

        result = await cache.get_or_set("k", Payload, loader_returning(7, mocker))

        assert result == Payload(value=7)

    async def test_a_full_redis_on_write_still_returns_the_response(self, cache, redis, mocker):
        # `noeviction`: Redis refuses the write when memory is full.
        redis.set.side_effect = ResponseError(
            "OOM command not allowed when used memory > 'maxmemory'"
        )

        result = await cache.get_or_set("k", Payload, loader_returning(7, mocker))

        assert result == Payload(value=7)

    async def test_a_value_that_no_longer_parses_is_a_miss(self, cache, redis, mocker):
        redis.get.return_value = '{"value":"not a number"}'
        loader = loader_returning(7, mocker)

        result = await cache.get_or_set("k", Payload, loader)

        assert result == Payload(value=7)
        loader.assert_awaited_once()

    async def test_a_response_over_the_size_cap_is_not_stored(self, redis, mocker):
        small = ResponseCache(redis, enabled=True, ttl_seconds=60, max_value_bytes=5)

        result = await small.get_or_set("k", Payload, loader_returning(123456, mocker))

        assert result == Payload(value=123456)
        redis.set.assert_not_awaited()

    async def test_store_if_false_returns_the_response_without_storing(self, cache, redis, mocker):
        result = await cache.get_or_set(
            "k", Payload, loader_returning(7, mocker), store_if=lambda response: False
        )

        assert result == Payload(value=7)
        redis.set.assert_not_awaited()

    async def test_a_loader_error_is_raised_and_nothing_is_stored(self, cache, redis, mocker):
        loader = mocker.AsyncMock(side_effect=LookupError("no such anime"))

        with pytest.raises(LookupError):
            await cache.get_or_set("k", Payload, loader)

        redis.set.assert_not_awaited()

    async def test_an_outage_is_logged_once_not_on_every_request(self, cache, redis, mocker):
        redis.get.side_effect = RedisError("down")
        redis.set.side_effect = RedisError("down")

        with capture_logs() as logs:
            for _ in range(5):
                await cache.get_or_set("k", Payload, loader_returning(1, mocker))

        assert len([entry for entry in logs if entry["event"] == "cache_unavailable"]) == 1


class _Service:
    def __init__(self, cache):
        self.cache = cache
        self.calls = 0

    @cached("thing", Payload, cacheable=lambda args: args["page"] == 0)
    async def get(self, kind: AnimeSort, page: int = 0):
        self.calls += 1
        return Payload(value=self.calls)


class TestCachedDecorator:
    async def test_without_a_cache_the_method_just_runs(self):
        service = _Service(cache=None)

        assert await service.get(AnimeSort.SCORE) == Payload(value=1)

    async def test_the_key_is_built_from_the_arguments_with_defaults_applied(self, cache, redis):
        service = _Service(cache)

        await service.get(AnimeSort.SCORE)

        assert redis.get.await_args.args[0] == "cache:v1:thing:kind=score:page=0"

    async def test_a_request_that_is_not_cacheable_skips_redis(self, cache, redis):
        service = _Service(cache)

        assert await service.get(AnimeSort.SCORE, page=3) == Payload(value=1)
        redis.get.assert_not_awaited()

    async def test_the_second_identical_call_is_served_from_redis(self, cache, redis):
        service = _Service(cache)
        stored = {}

        async def fake_get(key):
            return stored.get(key)

        async def fake_set(key, value, ex):
            stored[key] = value

        redis.get.side_effect = fake_get
        redis.set.side_effect = fake_set

        first = await service.get(AnimeSort.SCORE)
        second = await service.get(AnimeSort.SCORE)

        assert first == second == Payload(value=1)
        assert service.calls == 1


@pytest.fixture
def spy_cache(mocker):
    """A cache that records the key and runs the loader, to check what each service caches."""
    fake = mocker.create_autospec(ResponseCache, instance=True)
    fake.enabled = True

    async def run(key, model, loader, store_if=None):
        return await loader()

    fake.get_or_set.side_effect = run
    return fake


@pytest.fixture
def read_repository(mocker):
    repo = mocker.create_autospec(CatalogueReadRepository, instance=True)
    repo.stats_by_year.return_value = []
    repo.stats_by_tag.return_value = []
    repo.stats_by_studio.return_value = ([], 0)
    repo.hidden_gems.return_value = ([], 0)
    repo.most_dropped.return_value = ([], 0)
    repo.list_studios.return_value = ([], 0)
    repo.search_anime.return_value = ([], 0)
    return repo


LIST_BASE = dict(
    q=None, type=None, status=None, season=None, year=None, year_from=None, year_to=None,
    tag_id=None, tag_match=TagMatch.ALL, studio_id=None, min_score=None, min_ratings=None,
    sort=AnimeSort.SCORE, order=None, limit=20, offset=0,
)  # fmt: skip


class TestWhatIsCached:
    async def test_the_unfiltered_aggregates_are_cached(self, spy_cache, read_repository):
        service = AggregateService(read_repository, spy_cache)

        await service.by_year(None, None, None)
        await service.by_tag(AnimeType.TV, None, None, GroupSort.ANIME_COUNT, Order.DESC)

        keys = [call.args[0] for call in spy_cache.get_or_set.await_args_list]
        assert keys == [
            "cache:v1:aggregate_by_year:min_ratings=50:type=None:year_from=None:year_to=None",
            "cache:v1:aggregate_by_tag:min_ratings=50:order=desc:sort=anime_count:type=tv"
            ":year_from=None:year_to=None",
        ]

    async def test_a_year_range_or_another_minimum_is_not_cached(self, spy_cache, read_repository):
        service = AggregateService(read_repository, spy_cache)

        await service.by_year(None, 2000, None)
        await service.by_year(None, None, None, min_ratings=500)
        await service.by_tag(None, None, 2010, GroupSort.ANIME_COUNT, Order.DESC)

        spy_cache.get_or_set.assert_not_awaited()
        assert read_repository.stats_by_year.await_count == 2

    async def test_only_the_default_studio_page_is_cached(self, spy_cache, read_repository):
        service = AggregateService(read_repository, spy_cache)

        await service.by_studio(None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 0)
        assert spy_cache.get_or_set.await_count == 1

        await service.by_studio(None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 20)
        await service.by_studio(None, None, None, 5, GroupSort.ANIME_COUNT, Order.DESC, 20, 0)
        await service.by_studio(None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 100, 0)
        assert spy_cache.get_or_set.await_count == 1

    async def test_the_lists_cache_only_the_default_first_page(self, spy_cache, read_repository):
        service = ListService(read_repository, spy_cache)

        await service.hidden_gems(AnimeType.TV, None, None, 20, 0)
        await service.most_dropped(None, None, None, 20, 0)
        assert spy_cache.get_or_set.await_count == 2

        await service.hidden_gems(None, None, None, 20, 20)  # page 2
        await service.hidden_gems(None, 2015, None, 20, 0)  # a year range
        await service.hidden_gems(None, None, None, 20, 0, min_ratings=500)
        await service.most_dropped(None, None, None, 20, 0, min_members=100_000)
        await service.most_dropped(None, None, None, 50, 0)
        assert spy_cache.get_or_set.await_count == 2

    @pytest.fixture(autouse=True)
    def fixed_today(self, mocker):
        """Today is in fall 2026, so the presets for this year and this season are fixed."""
        mocker.patch("app.anime.service.current_season", return_value=(AnimeSeason.FALL, 2026))

    async def test_the_plain_list_is_cached_for_its_first_ten_pages_only(
        self, spy_cache, read_repository
    ):
        service = AnimeListService(read_repository, spy_cache)

        for offset in range(0, 200, 20):  # pages 1 to 10 at 20 per page
            await service.list_anime(**{**LIST_BASE, "offset": offset})
        assert spy_cache.get_or_set.await_count == 10

        await service.list_anime(**{**LIST_BASE, "offset": 200})  # page 11
        assert spy_cache.get_or_set.await_count == 10

    @pytest.mark.parametrize(
        ("limit", "offset", "cached"),
        [
            (50, 450, True),  # page 10 at 50 per page
            (50, 500, False),  # page 11
            (100, 900, True),
            (20, 30, False),  # an offset that is not a page boundary
            (30, 0, False),  # a page size the frontend does not use
            (1, 0, False),
        ],
    )
    async def test_only_the_usual_page_sizes_and_page_boundaries_are_cached(
        self, spy_cache, read_repository, limit, offset, cached
    ):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, "limit": limit, "offset": offset})

        assert spy_cache.get_or_set.await_count == (1 if cached else 0)

    @pytest.mark.parametrize(
        "change",
        [
            {"q": "titan"},
            {"type": [AnimeType.TV]},
            {"tag_id": [1]},
            {"year": 2020},
            {"min_score": 7.5},
            {"year_from": 2020},
            {"studio_id": [3]},
            {"tag_match": TagMatch.ANY},
        ],
    )
    async def test_a_search_or_any_other_filter_is_not_cached(
        self, spy_cache, read_repository, change
    ):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, **change})

        spy_cache.get_or_set.assert_not_awaited()

    @pytest.mark.parametrize("status", [AnimeStatus.AIRING, AnimeStatus.UPCOMING])
    async def test_every_page_of_the_airing_and_upcoming_presets_is_cached(
        self, spy_cache, read_repository, status
    ):
        service = AnimeListService(read_repository, spy_cache)

        for offset in range(0, 1020, 20):  # up to the cap, far past the last real page
            await service.list_anime(**{**LIST_BASE, "status": [status], "offset": offset})
        assert spy_cache.get_or_set.await_count == 51

        await service.list_anime(**{**LIST_BASE, "status": [status], "offset": 1020})
        assert spy_cache.get_or_set.await_count == 51  # over the cap

    async def test_the_preset_key_names_the_status_the_sort_and_the_page(
        self, spy_cache, read_repository
    ):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(
            **{**LIST_BASE, "status": [AnimeStatus.AIRING], "sort": AnimeSort.MEMBERS, "offset": 40}
        )

        key = spy_cache.get_or_set.await_args.args[0]
        assert key.startswith("cache:v1:anime_list:")
        assert "status=airing" in key and "sort=members" in key and "offset=40" in key

    @pytest.mark.parametrize(
        "change",
        [
            {"status": [AnimeStatus.FINISHED]},  # 20,000 anime, not a preset
            {"status": [AnimeStatus.AIRING, AnimeStatus.UPCOMING]},
            {"status": [AnimeStatus.AIRING, AnimeStatus.AIRING]},
            {"status": [AnimeStatus.AIRING], "min_score": 7.0},
            {"status": [AnimeStatus.AIRING], "tag_id": [1]},
            {"status": [AnimeStatus.AIRING], "type": [AnimeType.TV]},
            {"status": [AnimeStatus.AIRING], "q": "naruto"},
            {"status": [AnimeStatus.AIRING], "year_to": 2026},
            {"status": [AnimeStatus.AIRING], "season": [AnimeSeason.FALL]},
            {"status": [AnimeStatus.AIRING], "limit": 30},
        ],
    )
    async def test_other_status_filters_are_not_a_preset(self, spy_cache, read_repository, change):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, **change})

        spy_cache.get_or_set.assert_not_awaited()

    @pytest.mark.parametrize("status", [AnimeStatus.AIRING, AnimeStatus.UPCOMING])
    async def test_a_status_that_started_this_year_is_cached(
        self, spy_cache, read_repository, status
    ):
        """The main page's "top airing": a show that premiered in winter and still airs."""
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, "status": [status], "year": 2026})
        await service.list_anime(**{**LIST_BASE, "status": [status], "year": 2026, "offset": 60})
        await service.list_anime(**{**LIST_BASE, "status": [status], "year_from": 2025})
        assert spy_cache.get_or_set.await_count == 3

    async def test_airing_this_year_and_airing_since_last_year_are_two_different_keys(
        self, spy_cache, read_repository
    ):
        service = AnimeListService(read_repository, spy_cache)
        airing = {**LIST_BASE, "status": [AnimeStatus.AIRING]}

        await service.list_anime(**{**airing, "year": 2026})
        await service.list_anime(**{**airing, "year_from": 2025})

        keys = [call.args[0] for call in spy_cache.get_or_set.await_args_list]
        assert "year=2026" in keys[0] and "year_from=None" in keys[0]
        assert "year=None" in keys[1] and "year_from=2025" in keys[1]

    @pytest.mark.parametrize(
        "change",
        [
            {"year": 2025},  # not this year
            {"year": 2027},
            {"year_from": 2020},  # not last year
            {"year_from": 2026},
            {"year_from": 2025, "year_to": 2026},
            {"year": 2026, "season": [AnimeSeason.WINTER]},  # a status and a season
            {"year": 2026, "min_score": 7.0},
            {"year": 2026, "tag_id": [1]},
            {"year": 2026, "offset": 1020},
        ],
    )
    async def test_airing_with_any_other_year_filter_is_not_a_preset(
        self, spy_cache, read_repository, change
    ):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, "status": [AnimeStatus.AIRING], **change})

        spy_cache.get_or_set.assert_not_awaited()

    @pytest.mark.parametrize(
        "change",
        [
            {"status": [AnimeStatus.FINISHED], "year": 2026},
            {"status": [AnimeStatus.FINISHED], "year_from": 2025},
            {"year": 2026},  # a year without a status or season is not a preset
            {"year_from": 2025},
        ],
    )
    async def test_a_year_alone_or_with_finished_is_not_a_preset(
        self, spy_cache, read_repository, change
    ):
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, **change})

        spy_cache.get_or_set.assert_not_awaited()

    async def test_every_page_of_the_current_season_is_cached(
        self, spy_cache, read_repository, mocker
    ):
        mocker.patch("app.anime.service.current_season", return_value=(AnimeSeason.FALL, 2026))
        service = AnimeListService(read_repository, spy_cache)
        season = {"season": [AnimeSeason.FALL], "year": 2026}

        await service.list_anime(**{**LIST_BASE, **season, "offset": 0})
        await service.list_anime(**{**LIST_BASE, **season, "offset": 60})
        await service.list_anime(**{**LIST_BASE, **season, "limit": 50, "offset": 100})
        assert spy_cache.get_or_set.await_count == 3

    @pytest.mark.parametrize(
        "change",
        [
            {"season": [AnimeSeason.SUMMER], "year": 2026},  # last season
            {"season": [AnimeSeason.FALL], "year": 2025},  # same season, another year
            {"season": [AnimeSeason.FALL]},  # no year
            {"year": 2026},  # no season
            {"season": [AnimeSeason.FALL, AnimeSeason.SPRING], "year": 2026},
            {"season": [AnimeSeason.FALL], "year": 2026, "min_ratings": 500},
            {"season": [AnimeSeason.FALL], "year": 2026, "offset": 1020},
        ],
    )
    async def test_any_other_season_is_not_a_preset(
        self, spy_cache, read_repository, mocker, change
    ):
        mocker.patch("app.anime.service.current_season", return_value=(AnimeSeason.FALL, 2026))
        service = AnimeListService(read_repository, spy_cache)

        await service.list_anime(**{**LIST_BASE, **change})

        spy_cache.get_or_set.assert_not_awaited()

    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 1, 1), (AnimeSeason.WINTER, 2026)),
            (date(2026, 3, 31), (AnimeSeason.WINTER, 2026)),
            (date(2026, 4, 1), (AnimeSeason.SPRING, 2026)),
            (date(2026, 7, 15), (AnimeSeason.SUMMER, 2026)),
            (date(2026, 10, 1), (AnimeSeason.FALL, 2026)),
            (date(2026, 11, 18), (AnimeSeason.FALL, 2026)),
            (date(2026, 12, 31), (AnimeSeason.FALL, 2026)),
        ],
    )
    def test_the_current_season_follows_mals_calendar(self, today, expected):
        assert current_season(today) == expected

    async def test_filter_options_and_the_default_studio_list_are_cached(
        self, spy_cache, read_repository
    ):
        read_repository.list_tags.return_value = []
        read_repository.year_range.return_value = (1960, 2026)
        service = OptionsService(read_repository, spy_cache)

        await service.filter_options()
        await service.list_studios(None, 1, StudioSort.ANIME_COUNT, None, 20, 0)
        assert spy_cache.get_or_set.await_count == 2

        await service.list_studios("toei", 1, StudioSort.ANIME_COUNT, None, 20, 0)
        await service.list_studios(None, 1, StudioSort.ANIME_COUNT, None, 20, 40)
        assert spy_cache.get_or_set.await_count == 2


class TestPerAnimeStatisticsGate:
    @pytest.mark.parametrize(
        ("popularity_rank", "stored"),
        [(1, True), (1000, True), (1001, False), (23_000, False), (None, False)],
    )
    def test_only_the_top_anime_by_popularity_are_stored(self, popularity_rank, stored):
        response = SimpleNamespace(core=SimpleNamespace(popularity_mal=popularity_rank))

        assert _is_popular(response) is stored
