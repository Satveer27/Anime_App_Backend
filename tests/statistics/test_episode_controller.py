from app.statistics.exceptions import AnimeNotFoundError
from app.statistics.schemas import (
    EpisodeBreakdownSchema,
    EpisodePointSchema,
    EpisodeScoresResponse,
    FillerComparisonSchema,
    TrendSchema,
    Verdict,
)

URL = "/api/v1/anime/{}/statistics/episodes"


def make_response(**overrides) -> EpisodeScoresResponse:
    fields = {
        "mal_id": 1,
        "episodes": [
            EpisodePointSchema(episode_number=1, filler=False, recap=False, mal_score=8.0),
            EpisodePointSchema(episode_number=2, filler=False, recap=False, mal_score=None),
        ],
        "trend": TrendSchema(verdict=Verdict.INSUFFICIENT_DATA, scored_episodes=1),
        "yearly": [],
        "filler_comparison": FillerComparisonSchema(filler_episodes=0, non_filler_episodes=1),
        "breakdown": EpisodeBreakdownSchema(
            total_episodes=2,
            filler_episodes=0,
            recap_episodes=0,
            regular_episodes=2,
            regular_share=1.0,
        ),
    }
    fields.update(overrides)
    return EpisodeScoresResponse(**fields)


class TestGetEpisodeScoresEndpoint:
    def test_success_keeps_unscored_episodes_as_null_gaps(self, client, fake_statistics_service):
        fake_statistics_service.get_episode_scores.return_value = make_response()

        response = client.get(URL.format(1))

        assert response.status_code == 200
        body = response.json()
        EpisodeScoresResponse.model_validate(body)
        assert body["episodes"][1]["mal_score"] is None
        assert body["trend"]["verdict"] == "insufficient_data"
        assert body["segments"] == []

    def test_no_episodes_is_200_with_an_empty_list(self, client, fake_statistics_service):
        fake_statistics_service.get_episode_scores.return_value = make_response(
            episodes=[], trend=TrendSchema(verdict=Verdict.INSUFFICIENT_DATA, scored_episodes=0)
        )

        response = client.get(URL.format(1))

        assert response.status_code == 200
        assert response.json()["episodes"] == []

    def test_passes_the_id_to_the_service_as_an_int(self, client, fake_statistics_service):
        fake_statistics_service.get_episode_scores.return_value = make_response()

        client.get(URL.format(21))

        fake_statistics_service.get_episode_scores.assert_awaited_once_with(21)

    def test_unknown_anime_returns_404(self, client, fake_statistics_service):
        fake_statistics_service.get_episode_scores.side_effect = AnimeNotFoundError()

        response = client.get(URL.format(999999))

        assert response.status_code == 404
        assert response.json() == {"status_code": 404, "error": "Anime not found."}

    def test_needs_no_authentication(self, client, fake_statistics_service):
        fake_statistics_service.get_episode_scores.return_value = make_response()

        assert client.get(URL.format(1)).status_code == 200

    def test_bad_ids_return_422(self, client, fake_statistics_service):
        assert client.get(URL.format("abc")).status_code == 422
        assert client.get(URL.format(0)).status_code == 422
        fake_statistics_service.get_episode_scores.assert_not_called()
