from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.statistics.episode_analysis import (
    episode_breakdown,
    filler_comparison,
    hook,
    score_extremes,
    volatility,
    yearly_summary,
)
from app.statistics.schemas import EpisodePointSchema


def point(number=1, score=None, aired=None, filler=False, recap=False):
    return EpisodePointSchema(
        episode_number=number, mal_score=score, aired=aired, filler=filler, recap=recap
    )


def at(year, month=6):
    return datetime(year, month, 1, tzinfo=UTC)


class TestYearlySummary:
    def test_groups_by_year_oldest_first_with_the_mean_of_the_scored_ones(self):
        episodes = [
            point(1, 8.0, at(2000)),
            point(2, 6.0, at(2000)),
            point(3, None, at(2000)),
            point(4, 9.0, at(1999)),
        ]

        result = yearly_summary(episodes)

        assert [(y.year, y.episode_count, y.scored_episodes, y.mean_score) for y in result] == [
            (1999, 1, 1, 9.0),
            (2000, 3, 2, 7.0),
        ]

    def test_a_year_with_no_scores_has_a_null_mean(self):
        result = yearly_summary([point(1, None, at(2001))])

        assert result[0].episode_count == 1
        assert result[0].mean_score is None

    def test_episodes_without_an_air_date_are_left_out(self):
        assert yearly_summary([point(1, 8.0, None)]) == []

    def test_the_year_is_taken_in_utc(self):
        # 23:30 on 31 Dec at UTC-5 is already 2021 in UTC
        late = datetime(2020, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))

        assert yearly_summary([point(1, 8.0, late)])[0].year == 2021

    def test_no_episodes(self):
        assert yearly_summary([]) == []


class TestScoreExtremes:
    def test_best_and_worst(self):
        best, worst = score_extremes([point(1, 7.0), point(2, 9.5), point(3, 5.0)])

        assert (best.mal_score, best.episode_numbers) == (9.5, [2])
        assert (worst.mal_score, worst.episode_numbers) == (5.0, [3])

    def test_ties_list_every_episode(self):
        best, worst = score_extremes(
            [point(1, 9.0), point(2, 7.0), point(3, 9.0), point(4, 7.0), point(5, 8.0)]
        )

        assert best.episode_numbers == [1, 3]
        assert worst.episode_numbers == [2, 4]

    def test_unscored_episodes_are_ignored(self):
        best, worst = score_extremes([point(1, None), point(2, 8.0)])

        assert best.episode_numbers == [2]
        assert worst.episode_numbers == [2]

    def test_nothing_scored(self):
        assert score_extremes([point(1, None)]) == (None, None)
        assert score_extremes([]) == (None, None)


def scored_series(scores, first=1):
    return [(number, score) for number, score in enumerate(scores, start=first)]


class TestVolatility:
    def test_a_steady_show_has_no_spread_and_no_jumps(self):
        result = volatility(scored_series([8.0] * 6))

        assert result.standard_deviation == 0
        assert result.mean_episode_change == 0

    def test_a_rollercoaster_jumps_a_lot(self):
        result = volatility(scored_series([6.0, 9.0] * 3))

        assert result.standard_deviation == pytest.approx(1.5)
        assert result.mean_episode_change == pytest.approx(3.0)

    def test_a_steady_climb_has_spread_but_small_jumps(self):
        result = volatility(scored_series([6.0, 6.5, 7.0, 7.5, 8.0, 8.5]))

        assert result.mean_episode_change == pytest.approx(0.5)
        assert result.standard_deviation > result.mean_episode_change

    def test_too_few_scored_episodes_gives_none(self):
        assert volatility(scored_series([8.0] * 5)) is None
        assert volatility([]) is None

    def test_jumps_are_measured_between_consecutive_scored_episodes(self):
        # episodes 3 and 4 had no score, so 8.0 (episode 2) is followed by 9.0 (episode 5)
        scored = [(1, 8.0), (2, 8.0), (5, 9.0), (6, 9.0), (7, 9.0), (8, 9.0)]

        assert volatility(scored).mean_episode_change == pytest.approx(0.2)


class TestHook:
    def test_opening_against_the_rest_and_the_latest_episode(self):
        result = hook(scored_series([9.0, 9.0, 9.0, 7.0, 7.0, 7.0]))

        assert result.first_episodes == 3
        assert result.first_mean == 9.0
        assert result.rest_mean == 7.0
        assert result.hook_difference == 2.0
        assert result.latest_episode == 6
        assert result.latest_score == 7.0
        assert result.latest_vs_rest == pytest.approx(7.0 - 8.2)

    def test_a_weak_start_gives_a_negative_hook_difference(self):
        result = hook(scored_series([6.0, 6.0, 6.0, 8.0, 8.0, 8.0]))

        assert result.hook_difference == -2.0

    def test_a_strong_finale_is_positive_against_the_rest(self):
        result = hook(scored_series([7.0] * 5 + [9.5]))

        assert result.latest_vs_rest == pytest.approx(2.5)

    def test_the_latest_episode_is_the_last_scored_one_by_number(self):
        scored = [(1, 8.0), (2, 8.0), (3, 8.0), (4, 8.0), (5, 8.0), (9, 9.0)]

        assert hook(scored).latest_episode == 9

    def test_too_few_scored_episodes_gives_none(self):
        assert hook(scored_series([8.0] * 5)) is None
        assert hook([]) is None


class TestEpisodeBreakdown:
    def test_shares_of_all_episodes_scored_or_not(self):
        episodes = [
            point(1, 8.0),
            point(2, None, filler=True),
            point(3, 7.0, filler=True),
            point(4, None, recap=True),
        ]

        result = episode_breakdown(episodes)

        assert result.total_episodes == 4
        assert (result.filler_episodes, result.filler_share) == (2, 0.5)
        assert (result.recap_episodes, result.recap_share) == (1, 0.25)
        assert (result.regular_episodes, result.regular_share) == (1, 0.25)

    def test_an_episode_flagged_as_both_counts_in_both_but_is_not_regular(self):
        result = episode_breakdown([point(1, 8.0), point(2, 8.0, filler=True, recap=True)])

        assert result.filler_episodes == 1
        assert result.recap_episodes == 1
        assert result.regular_episodes == 1

    def test_no_filler_or_recap_is_zero_not_null(self):
        result = episode_breakdown([point(1, 8.0), point(2, 8.0)])

        assert result.filler_share == 0
        assert result.recap_share == 0
        assert result.regular_share == 1

    def test_no_episodes_gives_null_shares_not_an_error(self):
        result = episode_breakdown([])

        assert result.total_episodes == 0
        assert result.filler_share is None
        assert result.recap_share is None
        assert result.regular_share is None


class TestFillerComparison:
    def test_means_and_counts(self):
        result = filler_comparison(
            [
                point(1, 8.0),
                point(2, 9.0),
                point(3, 5.0, filler=True),
                point(4, 6.0, filler=True),
            ]
        )

        assert (result.non_filler_episodes, result.non_filler_mean) == (2, 8.5)
        assert (result.filler_episodes, result.filler_mean) == (2, 5.5)

    def test_recap_episodes_count_as_neither(self):
        result = filler_comparison([point(1, 8.0), point(2, 2.0, recap=True)])

        assert result.non_filler_episodes == 1
        assert result.non_filler_mean == 8.0
        assert result.filler_episodes == 0

    def test_unscored_episodes_do_not_count(self):
        result = filler_comparison([point(1, None, filler=True), point(2, 7.0)])

        assert result.filler_episodes == 0
        assert result.filler_mean is None

    def test_no_filler_gives_a_null_filler_mean(self):
        result = filler_comparison([point(1, 8.0)])

        assert result.filler_mean is None
        assert result.non_filler_mean == 8.0

    def test_nothing_at_all(self):
        result = filler_comparison([])

        assert result.filler_mean is None
        assert result.non_filler_mean is None
