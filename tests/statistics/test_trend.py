import pytest

from app.statistics.schemas import Verdict
from app.statistics.trend import (
    SEGMENT_SIZE,
    build_segments,
    compute_trend,
    edge_size,
    smooth,
    smoothing_window,
)


def series(scores, first=1):
    """Scored episodes numbered consecutively from `first`."""
    return [(number, score) for number, score in enumerate(scores, start=first)]


class TestWindowsAndSizes:
    @pytest.mark.parametrize(
        "count,window", [(6, 3), (12, 3), (50, 3), (100, 5), (1000, 51), (1100, 55)]
    )
    def test_smoothing_window_is_5_percent_odd_and_at_least_3(self, count, window):
        assert smoothing_window(count) == window

    @pytest.mark.parametrize("count,size", [(6, 3), (12, 3), (24, 5), (50, 10), (200, 40)])
    def test_baseline_is_20_percent_and_at_least_3(self, count, size):
        assert edge_size(count) == size


class TestSmooth:
    def test_centered_moving_average_shrinks_at_the_ends(self):
        assert smooth([1, 2, 3, 4, 5]) == pytest.approx([1.5, 2, 3, 4, 4.5])

    def test_a_flat_series_stays_flat(self):
        assert smooth([7.0] * 20) == pytest.approx([7.0] * 20)

    def test_keeps_the_length(self):
        assert len(smooth([5.0] * 37)) == 37


# (name, scores, verdict). 12 scores use a baseline of the first 3 and a window of 3.
VERDICT_CASES = [
    ("rise", [6, 6, 6] + [7.5] * 9, Verdict.GETS_GOOD),
    ("rise then fall below the baseline", [7, 7, 7, 8, 8, 8, 8, 6, 6, 6, 6, 6], Verdict.DECLINES),
    ("rise then back to the baseline", [6, 6, 6, 7.5, 7.5, 7.5, 7.5, 6, 6, 6, 6, 6], Verdict.FLAT),
    (
        "steady and good",
        [8.0, 8.1, 7.9, 8.0, 8.2, 8.0, 8.1, 7.9, 8.0, 8.1],
        Verdict.CONSISTENTLY_GOOD,
    ),
    ("steady decline", [8, 8, 8, 8, 7, 7, 6.5, 6, 6, 6, 6, 6], Verdict.DECLINES),
    ("flat and mediocre", [7.0, 7.2, 6.9, 7.1] * 3, Verdict.FLAT),
    ("good then drops by 1.0", [8, 8, 8, 8, 8, 8, 7, 7, 7, 7], Verdict.DECLINES),
    (
        "good then dips by only 0.3",
        [8, 8, 8, 8, 8, 8, 7.7, 7.7, 7.7, 7.7],
        Verdict.CONSISTENTLY_GOOD,
    ),
    ("too short, 5 scores", [6, 7, 8, 9, 9], Verdict.INSUFFICIENT_DATA),
    ("no scores", [], Verdict.INSUFFICIENT_DATA),
]


class TestVerdict:
    @pytest.mark.parametrize(
        "scores,verdict", [case[1:] for case in VERDICT_CASES], ids=[c[0] for c in VERDICT_CASES]
    )
    def test_verdict(self, scores, verdict):
        assert compute_trend(series(scores)).verdict == verdict

    def test_exactly_6_scored_episodes_is_enough(self):
        assert compute_trend(series([6, 6, 6, 8, 8, 8])).verdict != Verdict.INSUFFICIENT_DATA

    def test_too_little_data_reports_only_the_count(self):
        trend = compute_trend(series([6, 7]))

        assert trend.scored_episodes == 2
        assert trend.baseline is None
        assert trend.turning_point_episode is None


class TestGetsGood:
    def test_numbers_behind_the_verdict(self):
        trend = compute_trend(series([6, 6, 6] + [7.5] * 9))

        assert trend.baseline_size == 3
        assert trend.baseline == 6
        assert trend.threshold == 6.5
        assert trend.smoothing_window == 3
        assert trend.turning_point_episode == 4
        assert trend.mean_after_turning_point == 7.5
        assert trend.change == 1.5
        assert trend.dips_after_turning_point == 0

    def test_reaching_the_threshold_exactly_counts(self):
        # baseline 7.0, so the line is 7.5; the smoothed score first equals it at episode 5
        trend = compute_trend(series([7, 7, 7] + [7.5] * 9))

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode == 5

    def test_a_small_dip_after_the_rise_is_tolerated(self):
        # 30 episodes: baseline is the first 6; two bad episodes in a row make 2 smoothed dips,
        # within the 10% allowance of the 24 episodes that follow
        scores = [6.0] * 6 + [7.5] * 9 + [5.0, 5.0] + [7.5] * 13
        trend = compute_trend(series(scores))

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode == 7
        assert trend.dips_after_turning_point == 2

    def test_a_long_collapse_moves_the_turning_point_to_the_recovery(self):
        scores = [6.0] * 6 + [7.5] * 6 + [5.0] * 6 + [7.5] * 12
        trend = compute_trend(series(scores))

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode > 18  # not at the first rise: it did not stay

    def test_a_one_episode_finale_spike_is_not_gets_good(self):
        trend = compute_trend(series([7.0] * 11 + [9.0]))

        assert trend.verdict == Verdict.FLAT
        assert trend.turning_point_episode is None

    def test_the_turning_point_leaves_at_least_a_baseline_worth_of_episodes(self):
        # 20 episodes: the baseline is the first 4, so the turning point is episode 17 at the latest
        trend = compute_trend(series([6.0] * 4 + [6.2] * 12 + [8.5] * 4))

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode <= 17

    def test_a_show_that_peaks_and_falls_has_no_turning_point(self):
        trend = compute_trend(series([7, 7, 7, 8, 8, 8, 8, 6, 6, 6, 6, 6]))

        assert trend.turning_point_episode is None
        assert trend.mean_after_turning_point is None

    def test_gaps_in_episode_numbers_report_the_real_episode_number(self):
        # episodes 4 and 7 had no score, so they are not in the list at all
        scored = [(1, 6.0), (2, 6.0), (3, 6.0), (5, 7.5), (6, 7.5), (8, 7.5), (9, 7.5), (10, 7.5)]
        trend = compute_trend(scored)

        assert trend.verdict == Verdict.GETS_GOOD
        assert trend.turning_point_episode in {number for number, _ in scored}


class TestSegments:
    def test_none_for_a_series_up_to_200_episodes(self):
        assert build_segments(series([7.0] * SEGMENT_SIZE)) == []

    def test_none_without_scores(self):
        assert build_segments([]) == []

    def test_blocks_of_200_with_the_change_from_the_previous(self):
        scored = (
            series([6.0] * 200) + series([7.0] * 200, first=201) + series([7.5] * 50, first=401)
        )

        segments = build_segments(scored)

        assert [(s.start_episode, s.end_episode) for s in segments] == [
            (1, 200),
            (201, 400),
            (401, 450),
        ]
        assert [s.mean_score for s in segments] == [6.0, 7.0, 7.5]
        assert segments[0].change_from_previous is None
        assert segments[1].change_from_previous == 1.0
        assert segments[2].change_from_previous == 0.5

    def test_unscored_episodes_do_not_count_toward_a_blocks_scored_total(self):
        scored = [(n, 7.0) for n in range(1, 301) if n % 2 == 0]

        segments = build_segments(scored)

        assert [s.scored_episodes for s in segments] == [100, 50]
