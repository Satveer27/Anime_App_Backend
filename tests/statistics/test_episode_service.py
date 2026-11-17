from datetime import UTC, datetime

import pytest

from app.statistics.exceptions import AnimeNotFoundError
from app.statistics.schemas import Verdict


class TestGetEpisodeScores:
    async def test_unknown_anime_raises_not_found_and_reads_no_episodes(
        self, statistics_service, mock_catalogue_repository
    ):
        mock_catalogue_repository.anime_exists.return_value = False

        with pytest.raises(AnimeNotFoundError):
            await statistics_service.get_episode_scores(999)

        mock_catalogue_repository.get_episodes_of_anime.assert_not_called()

    async def test_no_episodes_is_a_normal_answer(self, statistics_service):
        result = await statistics_service.get_episode_scores(1)

        assert result.episodes == []
        assert result.trend.verdict == Verdict.INSUFFICIENT_DATA
        assert result.trend.scored_episodes == 0
        assert result.yearly == []
        assert result.segments == []
        assert result.best is None
        assert result.worst is None

    async def test_one_or_two_scored_episodes_are_returned_but_not_analysed(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes([8.0, 9.0])

        result = await statistics_service.get_episode_scores(1)

        assert [e.mal_score for e in result.episodes] == [8.0, 9.0]
        assert all(e.smoothed_score is None for e in result.episodes)
        assert result.trend.verdict == Verdict.INSUFFICIENT_DATA
        assert result.trend.scored_episodes == 2
        assert result.best.mal_score == 9.0  # the simple stats still work

    async def test_episode_fields_are_passed_through(
        self, statistics_service, mock_catalogue_repository, make_episode
    ):
        aired = datetime(2020, 5, 1, tzinfo=UTC)
        mock_catalogue_repository.get_episodes_of_anime.return_value = [
            make_episode(
                1, 8.5, title="Pilot", synopsis="It begins.", aired=aired, filler=True, recap=False
            )
        ]

        point = (await statistics_service.get_episode_scores(1)).episodes[0]

        assert point.title == "Pilot"
        assert point.synopsis == "It begins."
        assert point.aired == aired
        assert point.mal_score == 8.5
        assert point.filler is True

    async def test_unscored_episodes_stay_in_the_list_as_gaps(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        scores = [6.0, 6.0, 6.0, None, 7.5, 7.5, None, 7.5, 7.5, 7.5]
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(scores)

        result = await statistics_service.get_episode_scores(1)

        assert len(result.episodes) == 10
        assert result.episodes[3].mal_score is None
        assert result.episodes[3].smoothed_score is None
        assert result.episodes[4].smoothed_score is not None
        assert result.trend.scored_episodes == 8

    async def test_a_rising_season_gets_good_and_reports_the_turning_point(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(
            [6, 6, 6] + [7.5] * 9
        )

        result = await statistics_service.get_episode_scores(1)

        assert result.trend.verdict == Verdict.GETS_GOOD
        assert result.trend.turning_point_episode == 4
        assert all(e.smoothed_score is not None for e in result.episodes)

    async def test_turning_point_is_the_real_episode_number_when_scores_have_gaps(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        scores = [6.0, 6.0, None, 6.0, 7.5, 7.5, 7.5, 7.5, 7.5, 7.5]
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(scores)

        trend = (await statistics_service.get_episode_scores(1)).trend

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode == 5  # episode 3 has no score, so 5 is the 4th scored

    async def test_a_long_series_gets_segments(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(
            [7.0] * 200 + [8.0] * 100
        )

        result = await statistics_service.get_episode_scores(1)

        assert [(s.start_episode, s.end_episode) for s in result.segments] == [(1, 200), (201, 300)]
        assert result.segments[1].change_from_previous == pytest.approx(1.0)

    async def test_breakdown_counts_filler_and_recap_over_every_episode(
        self, statistics_service, mock_catalogue_repository, make_episode
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = [
            make_episode(1, 8.0),
            make_episode(2, None, filler=True),
            make_episode(3, 7.0, recap=True),
            make_episode(4, 9.0),
        ]

        breakdown = (await statistics_service.get_episode_scores(1)).breakdown

        assert breakdown.total_episodes == 4
        assert (breakdown.filler_episodes, breakdown.filler_share) == (1, 0.25)
        assert (breakdown.recap_episodes, breakdown.recap_share) == (1, 0.25)
        assert breakdown.regular_share == 0.5

    async def test_volatility_and_hook_are_included_with_enough_scores(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(
            [9.0, 9.0, 9.0, 7.0, 7.0, 7.0]
        )

        result = await statistics_service.get_episode_scores(1)

        assert result.hook.hook_difference == 2.0
        assert result.hook.latest_episode == 6
        assert result.volatility.mean_episode_change == pytest.approx(0.4)

    async def test_volatility_and_hook_are_null_below_6_scored_episodes(
        self, statistics_service, mock_catalogue_repository, make_episodes
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = make_episodes(
            [9.0, 9.0, None, 7.0, 7.0, None, None]
        )

        result = await statistics_service.get_episode_scores(1)

        assert result.volatility is None
        assert result.hook is None

    async def test_no_episodes_gives_an_empty_breakdown(self, statistics_service):
        breakdown = (await statistics_service.get_episode_scores(1)).breakdown

        assert breakdown.total_episodes == 0
        assert breakdown.filler_share is None

    async def test_episodes_are_grouped_by_year_filler_and_extremes(
        self, statistics_service, mock_catalogue_repository, make_episode
    ):
        mock_catalogue_repository.get_episodes_of_anime.return_value = [
            make_episode(1, 9.0, aired=datetime(1999, 10, 1, tzinfo=UTC)),
            make_episode(2, 9.0, aired=datetime(1999, 11, 1, tzinfo=UTC)),
            make_episode(3, 5.0, aired=datetime(2000, 1, 1, tzinfo=UTC), filler=True),
        ]

        result = await statistics_service.get_episode_scores(1)

        assert [(y.year, y.episode_count) for y in result.yearly] == [(1999, 2), (2000, 1)]
        assert result.best.episode_numbers == [1, 2]
        assert result.worst.episode_numbers == [3]
        assert result.filler_comparison.filler_mean == 5.0
        assert result.filler_comparison.non_filler_mean == 9.0
