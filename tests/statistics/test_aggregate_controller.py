import pytest

from app.catalogue.enums import AnimeType, GroupSort, Order
from app.exceptions import BadRequestError, ServiceUnavailableError
from app.main import app
from app.statistics.aggregate_schemas import (
    BySeasonResponse,
    ByStudioResponse,
    ByTagResponse,
    ByTypeResponse,
    ByYearResponse,
    GroupStatsSchema,
    SeasonRowSchema,
    StudioRowSchema,
    TagRowSchema,
    TypeRowSchema,
    YearRowSchema,
)
from app.statistics.aggregate_service import AggregateService
from app.statistics.deps import create_aggregate_service

BASE = "/api/v1/statistics"
STATS = {
    "anime_count": 10,
    "scored_count": 8,
    "mean_score": 7.1,
    "median_score": 7.0,
    "total_members": 99,
}


@pytest.fixture
def fake_service(mocker, client):
    fake = mocker.create_autospec(AggregateService, instance=True)
    fake.by_year.return_value = ByYearResponse(
        min_ratings=500, items=[YearRowSchema(year=2020, **STATS)]
    )
    fake.by_type.return_value = ByTypeResponse(
        min_ratings=500, items=[TypeRowSchema(type="tv", label="TV", **STATS)]
    )
    fake.by_season.return_value = BySeasonResponse(
        min_ratings=500, items=[SeasonRowSchema(season="fall", **STATS)]
    )
    fake.by_tag.return_value = ByTagResponse(
        min_ratings=500, items=[TagRowSchema(tag_id=1, name="Action", **STATS)]
    )
    fake.by_studio.return_value = ByStudioResponse(
        min_ratings=500, min_anime=10, total=1, limit=20, offset=0,
        items=[StudioRowSchema(studio_id=2, name="Studio", **STATS)],
    )  # fmt: skip
    app.dependency_overrides[create_aggregate_service] = lambda: fake
    return fake


class TestResponses:
    def test_by_year(self, client, fake_service):
        body = client.get(f"{BASE}/by-year").json()

        ByYearResponse.model_validate(body)
        assert body["min_ratings"] == 500
        assert body["items"][0] == {"year": 2020, **STATS}

    def test_by_type(self, client, fake_service):
        body = client.get(f"{BASE}/by-type").json()

        assert body["items"][0]["type"] == "tv"
        assert body["items"][0]["label"] == "TV"

    def test_by_season(self, client, fake_service):
        assert client.get(f"{BASE}/by-season").json()["items"][0]["season"] == "fall"

    def test_by_tag(self, client, fake_service):
        assert client.get(f"{BASE}/by-tag").json()["items"][0]["name"] == "Action"

    def test_by_studio_has_pagination_fields(self, client, fake_service):
        body = client.get(f"{BASE}/by-studio").json()

        assert (body["total"], body["limit"], body["offset"], body["min_anime"]) == (1, 20, 0, 10)

    def test_a_group_with_no_usable_scores_serialises_null_scores(self):
        row = GroupStatsSchema(anime_count=3, scored_count=0, total_members=5)

        assert row.model_dump()["mean_score"] is None

    @pytest.mark.parametrize("path", ["by-year", "by-type", "by-season", "by-tag", "by-studio"])
    def test_needs_no_authentication(self, client, fake_service, path):
        assert client.get(f"{BASE}/{path}").status_code == 200


class TestParameters:
    def test_by_year_passes_its_filters(self, client, fake_service):
        client.get(f"{BASE}/by-year?type=tv&year_from=2010&year_to=2020")

        fake_service.by_year.assert_awaited_once_with(AnimeType.TV, 2010, 2020)

    def test_by_type_takes_only_the_year_range(self, client, fake_service):
        client.get(f"{BASE}/by-type?year_from=2010&year_to=2020")

        fake_service.by_type.assert_awaited_once_with(2010, 2020)

    def test_by_tag_defaults_to_most_anime_first(self, client, fake_service):
        client.get(f"{BASE}/by-tag")

        fake_service.by_tag.assert_awaited_once_with(
            None, None, None, GroupSort.ANIME_COUNT, Order.DESC
        )

    def test_by_tag_sort_and_order(self, client, fake_service):
        client.get(f"{BASE}/by-tag?sort=mean_score&order=asc&type=movie")

        fake_service.by_tag.assert_awaited_once_with(
            AnimeType.MOVIE, None, None, GroupSort.MEAN_SCORE, Order.ASC
        )

    def test_by_studio_defaults(self, client, fake_service):
        client.get(f"{BASE}/by-studio")

        fake_service.by_studio.assert_awaited_once_with(
            None, None, None, 10, GroupSort.ANIME_COUNT, Order.DESC, 20, 0
        )

    def test_by_studio_passes_everything(self, client, fake_service):
        client.get(
            f"{BASE}/by-studio?type=tv&year_from=2000&min_anime=5&sort=total_members&order=asc&limit=50&offset=100"
        )

        fake_service.by_studio.assert_awaited_once_with(
            AnimeType.TV, 2000, None, 5, GroupSort.TOTAL_MEMBERS, Order.ASC, 50, 100
        )

    @pytest.mark.parametrize(
        "path",
        [
            "by-year?type=banana",
            "by-year?year_from=1899",
            "by-year?year_to=2101",
            "by-type?year_from=abc",
            "by-season?type=x",
            "by-tag?sort=popularity",
            "by-tag?order=up",
            "by-studio?min_anime=0",
            "by-studio?min_anime=101",
            "by-studio?limit=0",
            "by-studio?limit=101",
            "by-studio?offset=-1",
            "by-studio?offset=10001",
        ],
    )
    def test_invalid_parameters_return_422_and_never_reach_the_service(
        self, client, fake_service, path
    ):
        response = client.get(f"{BASE}/{path}")

        assert response.status_code == 422
        for name in ("by_year", "by_type", "by_season", "by_tag", "by_studio"):
            getattr(fake_service, name).assert_not_called()

    def test_by_type_has_no_type_filter(self, client, fake_service):
        client.get(f"{BASE}/by-type?type=tv")  # an unknown parameter is ignored, not an error

        fake_service.by_type.assert_awaited_once_with(None, None)


class TestErrors:
    def test_a_bad_request_from_the_service_is_a_400(self, client, fake_service):
        fake_service.by_year.side_effect = BadRequestError("year_from must not be after year_to.")

        response = client.get(f"{BASE}/by-year?year_from=2020&year_to=2019")

        assert response.status_code == 400

    def test_a_timeout_is_a_503(self, client, fake_service):
        fake_service.by_studio.side_effect = ServiceUnavailableError()

        assert client.get(f"{BASE}/by-studio").status_code == 503
