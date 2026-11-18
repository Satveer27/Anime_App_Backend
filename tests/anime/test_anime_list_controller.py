from datetime import UTC, datetime

import pytest

from app.anime.schemas import AnimeListItemSchema, AnimeListResponse
from app.catalogue.enums import (
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    TagMatch,
)
from app.exceptions import BadRequestError, ServiceUnavailableError

URL = "/api/v1/anime"


def make_response(**overrides) -> AnimeListResponse:
    fields = {
        "items": [AnimeListItemSchema(mal_id=1, title="Some Anime", mal_score=8.1)],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    fields.update(overrides)
    return AnimeListResponse(**fields)


class TestListAnimeEndpoint:
    def test_success_returns_items_and_the_total(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        response = client.get(URL)

        assert response.status_code == 200
        body = response.json()
        AnimeListResponse.model_validate(body)
        assert body["items"][0]["title"] == "Some Anime"
        assert (body["total"], body["limit"], body["offset"]) == (1, 20, 0)

    def test_with_no_parameters_the_defaults_are_used(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(URL)

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert kwargs["limit"] == 20
        assert kwargs["offset"] == 0
        assert kwargs["q"] is None
        assert kwargs["sort"] is None

    def test_every_filter_is_parsed_and_passed_on(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(
            URL
            + "?q=titan&type=tv_special&status=airing&season=fall&year=2020&tag_id=3&studio_id=4"
            "&min_score=7.5&min_ratings=500&sort=score&order=asc&limit=50&offset=100"
        )

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert kwargs["q"] == "titan"
        assert kwargs["type"] == [AnimeType.TV_SPECIAL]
        assert kwargs["status"] == [AnimeStatus.AIRING]
        assert kwargs["season"] == [AnimeSeason.FALL]
        assert kwargs["year"] == 2020
        assert kwargs["tag_id"] == [3]
        assert kwargs["studio_id"] == [4]
        assert kwargs["tag_match"] == TagMatch.ALL
        assert kwargs["release_status"] is None
        assert kwargs["min_score"] == 7.5
        assert kwargs["min_ratings"] == 500
        assert kwargs["sort"] == AnimeSort.SCORE
        assert kwargs["order"] == Order.ASC
        assert (kwargs["limit"], kwargs["offset"]) == (50, 100)

    def test_a_filter_can_be_repeated_to_take_several_values(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(
            URL + "?status=airing&status=upcoming&type=tv&type=movie&season=fall&season=winter"
            "&tag_id=1&tag_id=4&studio_id=7&studio_id=8"
        )

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert kwargs["status"] == [AnimeStatus.AIRING, AnimeStatus.UPCOMING]
        assert kwargs["type"] == [AnimeType.TV, AnimeType.MOVIE]
        assert kwargs["season"] == [AnimeSeason.FALL, AnimeSeason.WINTER]
        assert kwargs["tag_id"] == [1, 4]
        assert kwargs["studio_id"] == [7, 8]

    def test_tag_match_and_release_status_are_parsed(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(URL + "?tag_id=1&tag_id=2&tag_match=any&release_status=on_break")

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert kwargs["tag_match"] == TagMatch.ANY
        assert kwargs["release_status"] == ReleaseStatus.ON_BREAK

    def test_the_top_releasing_airing_query_is_accepted(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        response = client.get(URL + "?status=airing&release_status=releasing&sort=members&limit=20")

        assert response.status_code == 200
        assert fake_anime_list_service.list_anime.call_args.kwargs["release_status"] == (
            ReleaseStatus.RELEASING
        )

    def test_released_days_is_parsed_as_a_number(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(URL + "?released_days=7&sort=latest_episode")

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert kwargs["released_days"] == 7
        assert kwargs["sort"] == AnimeSort.LATEST_EPISODE

    def test_released_days_is_off_by_default(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(URL)

        assert fake_anime_list_service.list_anime.call_args.kwargs["released_days"] is None

    @pytest.mark.parametrize("days", [1, 60])
    def test_released_days_boundaries_are_accepted(self, client, fake_anime_list_service, days):
        fake_anime_list_service.list_anime.return_value = make_response()

        assert client.get(URL + f"?released_days={days}").status_code == 200

    @pytest.mark.parametrize("value", ["0", "-1", "61", "abc", "7.5"])
    def test_released_days_outside_1_to_60_is_422(self, client, fake_anime_list_service, value):
        response = client.get(URL + f"?released_days={value}")

        assert response.status_code == 422
        fake_anime_list_service.list_anime.assert_not_called()

    def test_the_latest_episode_sort_is_accepted(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        response = client.get(URL + "?sort=latest_episode")

        assert response.status_code == 200
        assert fake_anime_list_service.list_anime.call_args.kwargs["sort"] == (
            AnimeSort.LATEST_EPISODE
        )

    def test_the_item_shows_when_its_latest_episode_aired(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response(
            items=[
                AnimeListItemSchema(
                    mal_id=1,
                    title="A",
                    last_episode_aired=datetime(2026, 11, 13, tzinfo=UTC),
                )
            ]
        )

        body = client.get(URL).json()

        assert body["items"][0]["last_episode_aired"].startswith("2026-11-13")

    def test_ten_values_of_a_filter_are_allowed_and_eleven_are_not(
        self, client, fake_anime_list_service
    ):
        fake_anime_list_service.list_anime.return_value = make_response()

        assert (
            client.get(URL + "?" + "&".join(f"tag_id={n}" for n in range(1, 11))).status_code == 200
        )
        assert (
            client.get(URL + "?" + "&".join(f"tag_id={n}" for n in range(1, 12))).status_code == 422
        )
        assert client.get(URL + "?" + "&".join(["status=airing"] * 11)).status_code == 422

    def test_year_range_is_parsed(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        client.get(URL + "?year_from=2010&year_to=2019")

        kwargs = fake_anime_list_service.list_anime.call_args.kwargs
        assert (kwargs["year_from"], kwargs["year_to"]) == (2010, 2019)

    def test_the_top_airing_query_from_the_docs_works(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        response = client.get(URL + "?status=airing&sort=members")

        assert response.status_code == 200

    def test_needs_no_authentication(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        assert client.get(URL).status_code == 200  # no Authorization header

    @pytest.mark.parametrize(
        "query",
        [
            "type=banana",
            "status=live",
            "season=monsoon",
            "sort=popularity",
            "order=sideways",
            "limit=0",
            "limit=101",
            "offset=-1",
            "offset=50001",
            "year=1899",
            "year=2101",
            "year_from=abc",
            "tag_id=0",
            "tag_id=1&tag_id=0",
            "tag_id=abc",
            "studio_id=-3",
            "tag_match=either",
            "release_status=paused",
            "status=airing&status=live",
            "min_score=10.5",
            "min_score=-1",
            "min_ratings=-1",
            "q=",
            "q=" + "x" * 101,
        ],
    )
    def test_invalid_parameters_return_422_and_never_reach_the_service(
        self, client, fake_anime_list_service, query
    ):
        response = client.get(URL + "?" + query)

        assert response.status_code == 422
        fake_anime_list_service.list_anime.assert_not_called()

    def test_the_boundary_values_are_accepted(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.return_value = make_response()

        response = client.get(URL + "?limit=100&offset=50000&min_score=10&year=1900&min_ratings=0")

        assert response.status_code == 200

    def test_a_bad_request_from_the_service_is_a_400(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.side_effect = BadRequestError("nope")

        response = client.get(URL)

        assert response.status_code == 400
        assert response.json() == {"status_code": 400, "error": "nope"}

    def test_a_timeout_is_a_503(self, client, fake_anime_list_service):
        fake_anime_list_service.list_anime.side_effect = ServiceUnavailableError()

        assert client.get(URL).status_code == 503
