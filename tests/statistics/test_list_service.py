import pytest
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from app.catalogue.enums import AnimeType
from app.catalogue.read_repository import AggregateFilters, CatalogueReadRepository
from app.exceptions import BadRequestError, ServiceUnavailableError
from app.statistics.list_service import (
    DROPPED_MIN_MEMBERS,
    HIDDEN_GEM_COHORT_MIN_RATINGS,
    HIDDEN_GEM_MEMBERS_PERCENTILE,
    HIDDEN_GEM_MIN_SCORED_BY,
    HIDDEN_GEM_SCORE_PERCENTILE,
    ListService,
)


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("pg error")
        self.sqlstate = sqlstate


def gem(**overrides):
    row = {
        "mal_id": 1,
        "title": "A Gem",
        "type": "TV",
        "year": 2011,
        "mal_score": 8.71,
        "scored_by_mal": 4200,
        "members_mal": 15000,
        "score_percentile": 0.987654321,
        "members_percentile": 0.2468013579,
    }
    row.update(overrides)
    return row


def dropped(**overrides):
    row = {
        "mal_id": 2,
        "title": "Dropped Often",
        "type": "TV Special",
        "year": 2015,
        "mal_score": 5.5,
        "members_mal": 40000,
        "dropped": 12345,
        "started": 30000,
        "drop_rate": 0.41150000001,
    }
    row.update(overrides)
    return row


@pytest.fixture
def repository(mocker):
    repo = mocker.create_autospec(CatalogueReadRepository, instance=True)
    repo.hidden_gems.return_value = ([], 0)
    repo.most_dropped.return_value = ([], 0)
    return repo


@pytest.fixture
def service(repository):
    return ListService(repository)


class TestThresholds:
    def test_the_documented_defaults(self):
        assert HIDDEN_GEM_SCORE_PERCENTILE == 0.80
        assert HIDDEN_GEM_MEMBERS_PERCENTILE == 0.50
        assert HIDDEN_GEM_MIN_SCORED_BY == 2_000
        assert HIDDEN_GEM_COHORT_MIN_RATINGS == 50
        assert DROPPED_MIN_MEMBERS == 10_000

    async def test_hidden_gems_pass_the_defaults_to_the_query(self, service, repository):
        await service.hidden_gems(None, None, None, 20, 0)

        assert repository.hidden_gems.call_args.args == (
            AggregateFilters(),
            50,
            0.80,
            0.50,
            2_000,
            20,
            0,
        )

    async def test_the_cohort_minimum_can_be_changed_and_is_echoed(self, service, repository):
        result = await service.hidden_gems(None, None, None, 20, 0, min_ratings=300)

        assert repository.hidden_gems.call_args.args[1] == 300
        assert result.min_ratings == 300

    async def test_most_dropped_passes_the_default_floor(self, service, repository):
        result = await service.most_dropped(None, None, None, 20, 0)

        assert repository.most_dropped.call_args.args == (AggregateFilters(), 10_000, 20, 0)
        assert result.min_members == 10_000

    async def test_the_members_floor_can_be_changed_and_is_echoed(self, service, repository):
        result = await service.most_dropped(None, None, None, 20, 0, min_members=500)

        assert repository.most_dropped.call_args.args[1] == 500
        assert result.min_members == 500


class TestHiddenGems:
    async def test_rows_are_rounded_and_the_type_gets_its_api_name(self, service, repository):
        repository.hidden_gems.return_value = ([gem(type="TV Special")], 41)

        result = await service.hidden_gems(None, None, None, 20, 0)

        assert result.total == 41
        assert (result.limit, result.offset) == (20, 0)
        assert result.items[0].type == "tv_special"
        assert result.items[0].score_percentile == 0.988
        assert result.items[0].members_percentile == 0.247
        assert result.items[0].mal_score == 8.71

    async def test_a_type_mal_adds_later_is_returned_not_dropped(self, service, repository):
        repository.hidden_gems.return_value = ([gem(type="Music Video")], 1)

        result = await service.hidden_gems(None, None, None, 20, 0)

        assert result.items[0].type == "music_video"

    async def test_filters_are_turned_into_stored_values(self, service, repository):
        await service.hidden_gems(AnimeType.MOVIE, 2000, 2010, 5, 10)

        args = repository.hidden_gems.call_args.args
        assert args[0] == AggregateFilters(type="Movie", year_from=2000, year_to=2010)
        assert args[-2:] == (5, 10)

    async def test_year_from_after_year_to_is_a_bad_request(self, service, repository):
        with pytest.raises(BadRequestError):
            await service.hidden_gems(None, 2020, 2010, 20, 0)

        repository.hidden_gems.assert_not_called()


class TestMostDropped:
    async def test_rows_are_rounded_and_the_type_gets_its_api_name(self, service, repository):
        repository.most_dropped.return_value = ([dropped()], 7)

        result = await service.most_dropped(None, None, None, 20, 0)

        assert result.total == 7
        assert result.items[0].type == "tv_special"
        assert result.items[0].drop_rate == 0.4115
        assert (result.items[0].dropped, result.items[0].started) == (12345, 30000)

    async def test_year_from_after_year_to_is_a_bad_request(self, service, repository):
        with pytest.raises(BadRequestError):
            await service.most_dropped(None, 2020, 2010, 20, 0)

        repository.most_dropped.assert_not_called()


class TestTimeouts:
    @pytest.mark.parametrize(
        ("name", "call"),
        [
            ("hidden_gems", lambda s: s.hidden_gems(None, None, None, 20, 0)),
            ("most_dropped", lambda s: s.most_dropped(None, None, None, 20, 0)),
        ],
    )
    async def test_a_statement_timeout_is_a_503_logged_once(self, service, repository, name, call):
        getattr(repository, name).side_effect = DBAPIError("SELECT", {}, _PgError("57014"))

        with capture_logs() as logs, pytest.raises(ServiceUnavailableError):
            await call(service)

        timeouts = [entry for entry in logs if entry["event"] == "statement_timeout"]
        assert len(timeouts) == 1

    async def test_another_database_error_is_not_swallowed(self, service, repository):
        repository.hidden_gems.side_effect = DBAPIError("SELECT", {}, _PgError("42P01"))

        with pytest.raises(DBAPIError):
            await service.hidden_gems(None, None, None, 20, 0)
