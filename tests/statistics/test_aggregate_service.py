import pytest
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from app.catalogue.enums import AnimeType, GroupSort, Order
from app.catalogue.read_repository import AggregateFilters, CatalogueReadRepository
from app.exceptions import BadRequestError, ServiceUnavailableError
from app.statistics.aggregate_service import MIN_RATINGS, AggregateService
from app.statistics.service import LOW_SAMPLE_VOTES


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("pg error")
        self.sqlstate = sqlstate


def stats(**overrides):
    row = {
        "anime_count": 100,
        "scored_count": 60,
        "mean_score": 6.787462568951931,
        "median_score": 6.82,
        "total_members": 250_000,
    }
    row.update(overrides)
    return row


@pytest.fixture
def repository(mocker):
    repo = mocker.create_autospec(CatalogueReadRepository, instance=True)
    repo.stats_by_year.return_value = []
    repo.stats_by_type.return_value = []
    repo.stats_by_season.return_value = []
    repo.stats_by_tag.return_value = []
    repo.stats_by_studio.return_value = ([], 0)
    return repo


@pytest.fixture
def service(repository):
    return AggregateService(repository)


class TestThreshold:
    def test_the_minimum_ratings_match_the_low_sample_bar(self):
        assert MIN_RATINGS == LOW_SAMPLE_VOTES == 500

    async def test_every_response_says_which_minimum_it_used(self, service):
        assert (await service.by_year(None, None, None)).min_ratings == 500
        assert (await service.by_type(None, None)).min_ratings == 500
        assert (await service.by_season(None, None, None)).min_ratings == 500
        assert (
            await service.by_tag(None, None, None, GroupSort.ANIME_COUNT, Order.DESC)
        ).min_ratings == 500

    async def test_the_minimum_is_passed_to_the_queries(self, service, repository):
        await service.by_year(None, None, None)

        assert repository.stats_by_year.call_args.args[1] == 500


class TestByYear:
    async def test_rows_are_returned_with_rounded_scores(self, service, repository):
        repository.stats_by_year.return_value = [{"year": 2019, **stats()}]

        result = await service.by_year(None, None, None)

        row = result.items[0]
        assert row.year == 2019
        assert row.mean_score == 6.787
        assert row.median_score == 6.82
        assert (row.anime_count, row.scored_count, row.total_members) == (100, 60, 250_000)

    async def test_a_year_with_no_usable_scores_has_null_scores_not_an_error(
        self, service, repository
    ):
        repository.stats_by_year.return_value = [
            {"year": 2027, **stats(scored_count=0, mean_score=None, median_score=None)}
        ]

        row = (await service.by_year(None, None, None)).items[0]

        assert row.mean_score is None
        assert row.median_score is None
        assert row.scored_count == 0

    async def test_the_type_filter_becomes_the_stored_text(self, service, repository):
        await service.by_year(AnimeType.TV_SPECIAL, 2010, 2020)

        assert repository.stats_by_year.call_args.args[0] == AggregateFilters(
            type="TV Special", year_from=2010, year_to=2020
        )

    async def test_a_backwards_range_is_a_bad_request(self, service, repository):
        with pytest.raises(BadRequestError):
            await service.by_year(None, 2020, 2019)

        repository.stats_by_year.assert_not_called()

    async def test_no_rows_is_an_empty_list(self, service):
        assert (await service.by_year(None, None, None)).items == []


class TestByType:
    async def test_the_stored_type_becomes_the_filter_value_and_a_label(self, service, repository):
        repository.stats_by_type.return_value = [
            {"type": "TV", **stats()},
            {"type": "TV Special", **stats()},
        ]

        items = (await service.by_type(None, None)).items

        assert [(i.type, i.label) for i in items] == [("tv", "TV"), ("tv_special", "TV Special")]

    async def test_a_type_mal_adds_later_is_kept_under_a_lowercase_name(self, service, repository):
        repository.stats_by_type.return_value = [
            {"type": "CM", **stats()},
            {"type": "Music Video", **stats()},
        ]

        items = (await service.by_type(None, None)).items

        assert [(i.type, i.label) for i in items] == [("cm", "CM"), ("music_video", "Music Video")]

    async def test_the_year_range_is_passed_on(self, service, repository):
        await service.by_type(2000, 2010)

        assert repository.stats_by_type.call_args.args[0] == AggregateFilters(
            year_from=2000, year_to=2010
        )


class TestBySeason:
    async def test_four_rows_come_back_as_the_query_returned_them(self, service, repository):
        repository.stats_by_season.return_value = [
            {"season": s, **stats()} for s in ("winter", "spring", "summer", "fall")
        ]

        items = (await service.by_season(AnimeType.TV, None, None)).items

        assert [i.season for i in items] == ["winter", "spring", "summer", "fall"]
        assert repository.stats_by_season.call_args.args[0].type == "TV"


class TestByTag:
    async def test_sort_and_order_are_passed_on(self, service, repository):
        repository.stats_by_tag.return_value = [{"tag_id": 1, "name": "Action", **stats()}]

        result = await service.by_tag(None, None, None, GroupSort.MEAN_SCORE, Order.ASC)

        args = repository.stats_by_tag.call_args.args
        assert (args[2], args[3]) == (GroupSort.MEAN_SCORE, Order.ASC)
        assert (result.items[0].tag_id, result.items[0].name) == (1, "Action")


class TestByStudio:
    async def test_pagination_and_the_minimum_are_reported(self, service, repository):
        repository.stats_by_studio.return_value = (
            [{"studio_id": 5, "name": "Studio", **stats()}],
            194,
        )

        result = await service.by_studio(
            None, None, None, 10, GroupSort.MEAN_SCORE, Order.DESC, 20, 40
        )

        assert (result.total, result.limit, result.offset, result.min_anime) == (194, 20, 40, 10)
        assert result.items[0].studio_id == 5
        args = repository.stats_by_studio.call_args.args
        assert args[1:] == (500, 10, GroupSort.MEAN_SCORE, Order.DESC, 20, 40)

    async def test_an_empty_page_past_the_end_keeps_the_total(self, service, repository):
        repository.stats_by_studio.return_value = ([], 194)

        result = await service.by_studio(
            None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 1000
        )

        assert result.items == []
        assert result.total == 194


class TestTimeouts:
    @pytest.mark.parametrize(
        "call",
        [
            lambda s: s.by_year(None, None, None),
            lambda s: s.by_type(None, None),
            lambda s: s.by_season(None, None, None),
            lambda s: s.by_tag(None, None, None, GroupSort.ANIME_COUNT, Order.DESC),
            lambda s: s.by_studio(None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 0),
        ],
        ids=["by_year", "by_type", "by_season", "by_tag", "by_studio"],
    )
    async def test_a_statement_timeout_is_a_503_logged_once(self, service, repository, call):
        error = DBAPIError("SELECT", {}, _PgError("57014"))
        for name in (
            "stats_by_year",
            "stats_by_type",
            "stats_by_season",
            "stats_by_tag",
            "stats_by_studio",
        ):
            getattr(repository, name).side_effect = error

        with capture_logs() as logs, pytest.raises(ServiceUnavailableError):
            await call(service)

        assert [e["event"] for e in logs].count("statement_timeout") == 1
