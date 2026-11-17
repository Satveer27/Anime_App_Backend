import pytest

from app.statistics.calculations import (
    HIGH_SCORES,
    LOW_SCORES,
    build_list_status,
    build_score_distribution,
    safe_ratio,
    standard_deviation,
    vote_share,
)

ALL_ZERO = [0] * 10
NO_VOTES = [None] * 10


class TestSafeRatio:
    def test_divides(self):
        assert safe_ratio(1, 4) == 0.25

    @pytest.mark.parametrize("numerator,denominator", [(None, 4), (1, None), (1, 0), (0, 0)])
    def test_missing_input_or_zero_denominator_gives_none(self, numerator, denominator):
        assert safe_ratio(numerator, denominator) is None


def list_status(**overrides):
    fields = {
        "watching": 100,
        "completed": 600,
        "on_hold": 50,
        "dropped": 50,
        "plan_to_watch": 200,
        "total": 1000,
        "anime_status": "Finished Airing",
    }
    fields.update(overrides)
    return build_list_status(**fields)


class TestBuildListStatus:
    def test_shares_divide_by_total(self):
        result = list_status()

        assert result.completed_share == 0.6
        assert result.plan_to_watch_share == 0.2

    def test_rates_exclude_plan_to_watch(self):
        # 800 members started it: 1000 total - 200 plan to watch
        result = list_status()

        assert result.completion_rate == 600 / 800
        assert result.drop_rate == 50 / 800
        assert result.watching_rate == 100 / 800

    def test_airing_has_no_completion_rate_but_keeps_the_others(self):
        result = list_status(anime_status="Currently Airing")

        assert result.completion_rate is None
        assert result.drop_rate == 50 / 800
        assert result.watching_rate == 100 / 800

    def test_not_yet_aired_has_no_rates(self):
        result = list_status(anime_status="Not yet aired", plan_to_watch=1000, total=1000)

        assert result.completion_rate is None
        assert result.drop_rate is None
        assert result.watching_rate is None

    def test_everyone_on_plan_to_watch_gives_no_rates_instead_of_a_zero_division(self):
        result = list_status(
            watching=0, completed=0, on_hold=0, dropped=0, plan_to_watch=500, total=500
        )

        assert result.completion_rate is None
        assert result.drop_rate is None

    def test_empty_lists_give_no_shares(self):
        result = list_status(
            watching=0, completed=0, on_hold=0, dropped=0, plan_to_watch=0, total=0
        )

        assert result.watching_share is None
        assert result.completion_rate is None

    def test_unknown_status_still_gets_rates(self):
        result = list_status(anime_status=None)

        assert result.completion_rate == 600 / 800


class TestScoreDistribution:
    def test_percentages_add_up_to_100(self):
        result = build_score_distribution([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])

        assert [bucket.score for bucket in result.buckets] == list(range(1, 11))
        assert sum(bucket.percentage for bucket in result.buckets) == pytest.approx(100)
        assert result.total_votes == 550

    def test_mean_is_the_vote_weighted_average(self):
        # 10 votes of 1 and 10 votes of 10
        result = build_score_distribution([10, 0, 0, 0, 0, 0, 0, 0, 0, 10])

        assert result.mean == 5.5

    def test_mode_is_the_most_common_score(self):
        result = build_score_distribution([1, 2, 3, 4, 50, 6, 7, 8, 9, 10])

        assert result.mode == 5

    def test_tied_modes_pick_the_higher_score(self):
        result = build_score_distribution([5, 0, 0, 0, 0, 0, 0, 0, 0, 5])

        assert result.mode == 10

    @pytest.mark.parametrize("votes", [NO_VOTES, ALL_ZERO, [1, 2, None, 4, 5, 6, 7, 8, 9, 10]])
    def test_unusable_votes_give_no_derived_fields_and_no_error(self, votes):
        result = build_score_distribution(votes)

        assert result.mean is None
        assert result.mode is None
        assert result.total_votes is None
        assert all(bucket.percentage is None for bucket in result.buckets)

    def test_missing_buckets_keep_their_raw_counts(self):
        result = build_score_distribution([1, 2, None, 4, 5, 6, 7, 8, 9, 10])

        assert [bucket.votes for bucket in result.buckets][:4] == [1, 2, None, 4]


class TestStandardDeviation:
    def test_known_value(self):
        # half 1s and half 10s: mean 5.5, every vote is 4.5 away
        assert standard_deviation([10, 0, 0, 0, 0, 0, 0, 0, 0, 10]) == pytest.approx(4.5)

    def test_all_votes_on_one_score_is_zero(self):
        assert standard_deviation([0, 0, 0, 0, 100, 0, 0, 0, 0, 0]) == 0

    @pytest.mark.parametrize("votes", [NO_VOTES, ALL_ZERO])
    def test_unusable_votes_give_none(self, votes):
        assert standard_deviation(votes) is None


class TestVoteShare:
    def test_low_and_high_shares(self):
        votes = [10, 10, 10, 10, 0, 0, 0, 0, 30, 30]

        assert vote_share(votes, LOW_SCORES) == pytest.approx(0.4)
        assert vote_share(votes, HIGH_SCORES) == pytest.approx(0.6)

    @pytest.mark.parametrize("votes", [NO_VOTES, ALL_ZERO])
    def test_unusable_votes_give_none(self, votes):
        assert vote_share(votes, LOW_SCORES) is None
