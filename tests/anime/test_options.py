import pytest
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from app.anime.limits import MAX_FILTER_VALUES, MAX_PAGE_SIZE, MAX_RELEASED_DAYS
from app.anime.option_schemas import (
    FilterOptionsResponse,
    LimitsSchema,
    OptionSchema,
    StudioListResponse,
    StudioOptionSchema,
    TagOptionSchema,
    YearRangeSchema,
)
from app.anime.option_service import fixed_options
from app.catalogue.enums import (
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    StudioSort,
    TagMatch,
)
from app.exceptions import ServiceUnavailableError


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("pg error")
        self.sqlstate = sqlstate


def values(options):
    return [option.value for option in options]


class TestFixedOptions:
    """Every value offered is one the list accepts, so a dropdown can never send a bad filter."""

    def test_types_are_the_enum_values_with_mals_labels(self):
        options = fixed_options()["types"]

        assert values(options) == [t.value for t in AnimeType]
        labels = {o.value: o.label for o in options}
        assert labels["tv"] == "TV"
        assert labels["tv_special"] == "TV Special"
        assert labels["ova"] == "OVA"

    def test_statuses_seasons_and_the_rest_cover_their_enums(self):
        options = fixed_options()

        assert values(options["statuses"]) == [s.value for s in AnimeStatus]
        assert values(options["seasons"]) == [s.value for s in AnimeSeason]
        assert values(options["release_statuses"]) == [r.value for r in ReleaseStatus]
        assert values(options["sorts"]) == [s.value for s in AnimeSort]
        assert set(values(options["orders"])) == {o.value for o in Order}
        assert set(values(options["tag_matches"])) == {m.value for m in TagMatch}

    def test_every_value_parses_back_into_its_enum(self):
        options = fixed_options()

        for value in values(options["types"]):
            AnimeType(value)
        for value in values(options["statuses"]):
            AnimeStatus(value)
        for value in values(options["seasons"]):
            AnimeSeason(value)
        for value in values(options["release_statuses"]):
            ReleaseStatus(value)
        for value in values(options["sorts"]):
            AnimeSort(value)

    def test_status_labels_are_the_names_mal_uses(self):
        labels = {o.value: o.label for o in fixed_options()["statuses"]}

        assert labels == {
            "airing": "Currently Airing",
            "finished": "Finished Airing",
            "upcoming": "Not yet aired",
        }

    def test_seasons_are_in_calendar_order(self):
        assert values(fixed_options()["seasons"]) == ["winter", "spring", "summer", "fall"]

    def test_release_statuses_say_what_they_mean(self):
        described = {o.value: o.description for o in fixed_options()["release_statuses"]}

        assert "10 days" in described["releasing"]
        assert "10 days" in described["on_break"]
        assert described["no_episode_data"]

    def test_the_sorts_that_need_care_say_so(self):
        described = {o.value: o.description for o in fixed_options()["sorts"]}

        assert "search text" in described["relevance"]
        assert "status=airing" in described["latest_episode"]


class TestFilterOptions:
    async def test_tags_years_and_limits_are_added_to_the_fixed_choices(
        self, options_service, mock_read_repository
    ):
        mock_read_repository.list_tags.return_value = [
            {"tag_id": 1, "name": "Action", "anime_count": 4647},
            {"tag_id": 2, "name": "Adventure", "anime_count": 4428},
        ]
        mock_read_repository.year_range.return_value = (1917, 2028)

        result = await options_service.filter_options()

        assert [(t.tag_id, t.name, t.anime_count) for t in result.tags] == [
            (1, "Action", 4647),
            (2, "Adventure", 4428),
        ]
        assert (result.years.min, result.years.max) == (1917, 2028)
        assert result.limits.max_page_size == MAX_PAGE_SIZE == 100
        assert result.limits.max_filter_values == MAX_FILTER_VALUES == 10
        assert result.limits.max_released_days == MAX_RELEASED_DAYS == 60
        assert values(result.types)[0] == "tv"

    async def test_an_empty_catalogue_is_not_an_error(self, options_service):
        result = await options_service.filter_options()

        assert result.tags == []
        assert result.years.min is None
        assert result.years.max is None

    async def test_a_statement_timeout_is_a_503(self, options_service, mock_read_repository):
        mock_read_repository.list_tags.side_effect = DBAPIError("S", {}, _PgError("57014"))

        with capture_logs() as logs, pytest.raises(ServiceUnavailableError):
            await options_service.filter_options()

        assert [e["event"] for e in logs].count("statement_timeout") == 1


class TestStudioList:
    async def test_rows_become_items_with_the_total(self, options_service, mock_read_repository):
        mock_read_repository.list_studios.return_value = (
            [{"studio_id": 18, "name": "Toei Animation", "anime_count": 893}],
            1165,
        )

        result = await options_service.list_studios(None, 1, StudioSort.ANIME_COUNT, None, 20, 40)

        assert result.items[0].studio_id == 18
        assert result.items[0].anime_count == 893
        assert (result.total, result.limit, result.offset) == (1165, 20, 40)

    async def test_the_arguments_are_passed_on(self, options_service, mock_read_repository):
        await options_service.list_studios("toei", 5, StudioSort.NAME, Order.ASC, 10, 30)

        mock_read_repository.list_studios.assert_awaited_once_with(
            "toei", 5, StudioSort.NAME, Order.ASC, 10, 30
        )

    async def test_the_search_text_has_its_spaces_collapsed(
        self, options_service, mock_read_repository
    ):
        await options_service.list_studios("  production   i.g ", 1, StudioSort.NAME, None, 20, 0)

        assert mock_read_repository.list_studios.call_args.args[0] == "production i.g"

    async def test_a_blank_search_is_no_search(self, options_service, mock_read_repository):
        await options_service.list_studios("   ", 1, StudioSort.NAME, None, 20, 0)

        assert mock_read_repository.list_studios.call_args.args[0] is None

    async def test_no_matches_is_an_empty_page(self, options_service):
        result = await options_service.list_studios("zzzz", 1, StudioSort.NAME, None, 20, 0)

        assert result.items == []
        assert result.total == 0

    async def test_the_search_text_is_never_logged(self, options_service):
        with capture_logs() as logs:
            await options_service.list_studios("my secret", 1, StudioSort.NAME, None, 20, 0)

        assert "my secret" not in str(logs)
        assert [e for e in logs if e["event"] == "studio_list_served"][0]["has_search"] is True

    async def test_a_statement_timeout_is_a_503(self, options_service, mock_read_repository):
        mock_read_repository.list_studios.side_effect = DBAPIError("S", {}, _PgError("57014"))

        with pytest.raises(ServiceUnavailableError):
            await options_service.list_studios(None, 1, StudioSort.NAME, None, 20, 0)


def options_response() -> FilterOptionsResponse:
    option = OptionSchema(value="tv", label="TV")
    return FilterOptionsResponse(
        types=[option],
        statuses=[option],
        seasons=[option],
        release_statuses=[option],
        sorts=[option],
        orders=[option],
        tag_matches=[option],
        years=YearRangeSchema(min=1917, max=2028),
        limits=LimitsSchema(max_page_size=100, max_filter_values=10, max_released_days=60),
        tags=[TagOptionSchema(tag_id=1, name="Action", anime_count=10)],
    )


class TestFiltersEndpoint:
    def test_success_returns_everything_in_one_call(self, client, fake_options_service):
        fake_options_service.filter_options.return_value = options_response()

        response = client.get("/api/v1/filters")

        assert response.status_code == 200
        body = response.json()
        FilterOptionsResponse.model_validate(body)
        assert body["tags"] == [{"tag_id": 1, "name": "Action", "anime_count": 10}]
        assert body["years"] == {"min": 1917, "max": 2028}

    def test_browsers_may_keep_it_for_an_hour(self, client, fake_options_service):
        fake_options_service.filter_options.return_value = options_response()

        response = client.get("/api/v1/filters")

        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_needs_no_authentication(self, client, fake_options_service):
        fake_options_service.filter_options.return_value = options_response()

        assert client.get("/api/v1/filters").status_code == 200

    def test_a_timeout_is_a_503(self, client, fake_options_service):
        fake_options_service.filter_options.side_effect = ServiceUnavailableError()

        assert client.get("/api/v1/filters").status_code == 503


def studios_response() -> StudioListResponse:
    return StudioListResponse(
        items=[StudioOptionSchema(studio_id=18, name="Toei Animation", anime_count=893)],
        total=1,
        limit=20,
        offset=0,
    )


class TestStudiosEndpoint:
    def test_success(self, client, fake_options_service):
        fake_options_service.list_studios.return_value = studios_response()

        response = client.get("/api/v1/studios")

        assert response.status_code == 200
        StudioListResponse.model_validate(response.json())
        assert response.json()["items"][0]["name"] == "Toei Animation"

    def test_defaults(self, client, fake_options_service):
        fake_options_service.list_studios.return_value = studios_response()

        client.get("/api/v1/studios")

        fake_options_service.list_studios.assert_awaited_once_with(
            None, 1, StudioSort.ANIME_COUNT, None, 20, 0
        )

    def test_every_parameter_is_parsed(self, client, fake_options_service):
        fake_options_service.list_studios.return_value = studios_response()

        client.get("/api/v1/studios?q=toei&min_anime=5&sort=name&order=asc&limit=50&offset=100")

        fake_options_service.list_studios.assert_awaited_once_with(
            "toei", 5, StudioSort.NAME, Order.ASC, 50, 100
        )

    def test_needs_no_authentication(self, client, fake_options_service):
        fake_options_service.list_studios.return_value = studios_response()

        assert client.get("/api/v1/studios").status_code == 200

    @pytest.mark.parametrize(
        "query",
        [
            "limit=0",
            "limit=101",
            "offset=-1",
            "offset=10001",
            "min_anime=0",
            "sort=popular",
            "order=sideways",
            "q=",
            "q=" + "x" * 101,
        ],
    )
    def test_invalid_parameters_return_422_and_never_reach_the_service(
        self, client, fake_options_service, query
    ):
        response = client.get("/api/v1/studios?" + query)

        assert response.status_code == 422
        fake_options_service.list_studios.assert_not_called()

    def test_the_boundary_values_are_accepted(self, client, fake_options_service):
        fake_options_service.list_studios.return_value = studios_response()

        response = client.get("/api/v1/studios?limit=100&offset=10000&min_anime=10000")

        assert response.status_code == 200
