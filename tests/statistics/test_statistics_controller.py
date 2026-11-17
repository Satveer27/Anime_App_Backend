from app.statistics.exceptions import AnimeNotFoundError
from app.statistics.schemas import (
    AnimeStatisticsResponse,
    CoreSchema,
    ReviewSchema,
    ReviewsSchema,
    TagSchema,
)

URL = "/api/v1/anime/{}/statistics"


def make_response(**overrides) -> AnimeStatisticsResponse:
    fields = {
        "core": CoreSchema(mal_id=1, title="Some Anime", mal_score=8.1),
        "statistics": None,
        "tags": [TagSchema(mal_id=1, name="Action")],
        "studios": [],
        "reviews": ReviewsSchema(
            items=[ReviewSchema(mal_id=5, review="Great.", is_spoiler=True, is_preliminary=False)]
        ),
    }
    fields.update(overrides)
    return AnimeStatisticsResponse(**fields)


class TestGetAnimeStatisticsEndpoint:
    def test_success_returns_the_response_body(self, client, fake_statistics_service):
        fake_statistics_service.get_anime_statistics.return_value = make_response()

        response = client.get(URL.format(1))

        assert response.status_code == 200
        body = response.json()
        AnimeStatisticsResponse.model_validate(body)
        assert body["core"]["title"] == "Some Anime"
        assert body["tags"] == [{"mal_id": 1, "name": "Action"}]
        assert body["reviews"]["items"][0]["is_spoiler"] is True
        assert "page 1" in body["reviews"]["sample_note"]

    def test_missing_statistics_row_is_200_with_null_statistics(
        self, client, fake_statistics_service
    ):
        fake_statistics_service.get_anime_statistics.return_value = make_response(statistics=None)

        response = client.get(URL.format(1))

        assert response.status_code == 200
        assert response.json()["statistics"] is None

    def test_passes_the_id_to_the_service_as_an_int(self, client, fake_statistics_service):
        fake_statistics_service.get_anime_statistics.return_value = make_response()

        client.get(URL.format(31964))

        fake_statistics_service.get_anime_statistics.assert_awaited_once_with(31964)

    def test_unknown_anime_returns_404(self, client, fake_statistics_service):
        fake_statistics_service.get_anime_statistics.side_effect = AnimeNotFoundError()

        response = client.get(URL.format(999999))

        assert response.status_code == 404
        assert response.json() == {"status_code": 404, "error": "Anime not found."}

    def test_needs_no_authentication(self, client, fake_statistics_service):
        fake_statistics_service.get_anime_statistics.return_value = make_response()

        response = client.get(URL.format(1))  # no Authorization header

        assert response.status_code == 200

    def test_non_numeric_id_returns_422_not_500(self, client, fake_statistics_service):
        response = client.get(URL.format("abc"))

        assert response.status_code == 422
        fake_statistics_service.get_anime_statistics.assert_not_called()

    def test_zero_or_negative_id_returns_422(self, client, fake_statistics_service):
        assert client.get(URL.format(0)).status_code == 422
        assert client.get(URL.format(-5)).status_code == 422
        fake_statistics_service.get_anime_statistics.assert_not_called()
