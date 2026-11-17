"""The per-season "does it get good?" verdict, smoothing and long-series segments.

Pure functions, no database and no I/O. They take the scored episodes of ONE MAL entry as
(episode_number, score) pairs in episode order; unscored episodes are left out by the caller.
Every threshold is a named constant so the check against real series (docs/METRICS.md) can
tune it in one place.
"""

from collections.abc import Sequence
from statistics import fmean

from app.statistics.schemas import SegmentSchema, TrendSchema, Verdict

Scored = Sequence[tuple[int, float]]

# Fewer scored episodes than this: no verdict.
MIN_SCORED_EPISODES = 6
# The baseline is how the season starts: the first 20% of scored episodes, at least 3.
BASELINE_FRACTION = 0.20
MIN_BASELINE_EPISODES = 3
# gets_good: the smoothed score reaches baseline + RISE_THRESHOLD and stays there to the end.
RISE_THRESHOLD = 0.5
# After the turning point, this share of the remaining smoothed points may dip below the line.
DIP_ALLOWANCE = 0.10
# consistently_good: baseline at least GOOD_BASELINE, and every smoothed point within STAY_WITHIN.
GOOD_BASELINE = 7.5
STAY_WITHIN = 0.5
# declines: the last EDGE episodes average at least DECLINE_THRESHOLD below the baseline.
DECLINE_THRESHOLD = 0.5
# The moving average spans 5% of the scored episodes, at least 3, and an odd number.
SMOOTHING_FRACTION = 0.05
MIN_SMOOTHING_WINDOW = 3
# Series longer than this many episodes also get one summary per block of this size.
SEGMENT_SIZE = 200


def edge_size(count: int) -> int:
    """How many episodes make the baseline (from the start) and the end mean (from the end)."""
    return max(MIN_BASELINE_EPISODES, round(BASELINE_FRACTION * count))


def smoothing_window(count: int) -> int:
    window = max(MIN_SMOOTHING_WINDOW, round(SMOOTHING_FRACTION * count))
    return window if window % 2 == 1 else window + 1


def smooth(scores: Sequence[float]) -> list[float]:
    """A centered moving average. Near the ends it averages the episodes that exist."""
    count = len(scores)
    half = smoothing_window(count) // 2
    prefix = [0.0]
    for score in scores:
        prefix.append(prefix[-1] + score)
    smoothed = []
    for index in range(count):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        smoothed.append((prefix[high] - prefix[low]) / (high - low))
    return smoothed


def _turning_index(
    smoothed: Sequence[float], start: int, threshold: float, min_after: int
) -> int | None:
    """The first index from `start` where the smoothed score stays above the threshold.

    "Stays" means it is above the threshold at the very last episode, and at most
    DIP_ALLOWANCE of the points from there on are below it. A show that rises, peaks and falls
    back below the line has no turning point. At least `min_after` episodes (as many as the
    baseline has) must follow it, so a spike in the finale does not count as "gets good".
    """
    count = len(smoothed)
    if smoothed[-1] < threshold:
        return None
    below_from = [0] * (count + 1)  # below_from[i]: points at or after i under the threshold
    for index in range(count - 1, -1, -1):
        below_from[index] = below_from[index + 1] + (smoothed[index] < threshold)
    for index in range(start, count - min_after + 1):
        if smoothed[index] >= threshold and below_from[index] <= DIP_ALLOWANCE * (count - index):
            return index
    return None


def compute_trend(scored: Scored) -> TrendSchema:
    """The verdict for one season, with the numbers behind it.

    Checked in this order: too little data, gets_good (a turning point exists),
    consistently_good, declines, otherwise flat.
    """
    count = len(scored)
    if count < MIN_SCORED_EPISODES:
        return TrendSchema(verdict=Verdict.INSUFFICIENT_DATA, scored_episodes=count)

    numbers = [number for number, _ in scored]
    scores = [score for _, score in scored]
    smoothed = smooth(scores)
    size = edge_size(count)
    baseline = fmean(scores[:size])
    threshold = baseline + RISE_THRESHOLD
    end_mean = fmean(scores[-size:])
    common = {
        "scored_episodes": count,
        "baseline_size": size,
        "baseline": baseline,
        "threshold": threshold,
        "smoothing_window": smoothing_window(count),
        "end_mean": end_mean,
    }

    turning = _turning_index(smoothed, size, threshold, min_after=size)
    if turning is not None:
        mean_after = fmean(scores[turning:])
        return TrendSchema(
            verdict=Verdict.GETS_GOOD,
            turning_point_episode=numbers[turning],
            mean_after_turning_point=mean_after,
            change=mean_after - baseline,
            dips_after_turning_point=sum(1 for value in smoothed[turning:] if value < threshold),
            **common,
        )
    if baseline >= GOOD_BASELINE and all(
        abs(value - baseline) <= STAY_WITHIN for value in smoothed
    ):
        return TrendSchema(verdict=Verdict.CONSISTENTLY_GOOD, **common)
    if end_mean <= baseline - DECLINE_THRESHOLD:
        return TrendSchema(verdict=Verdict.DECLINES, **common)
    return TrendSchema(verdict=Verdict.FLAT, **common)


def build_segments(scored: Scored) -> list[SegmentSchema]:
    """One summary per block of SEGMENT_SIZE episodes, for series longer than one block."""
    if not scored or scored[-1][0] <= SEGMENT_SIZE:
        return []
    blocks: dict[int, list[float]] = {}
    for number, score in scored:
        blocks.setdefault((number - 1) // SEGMENT_SIZE, []).append(score)
    segments = []
    previous = None
    for block in sorted(blocks):
        mean = fmean(blocks[block])
        segments.append(
            SegmentSchema(
                start_episode=block * SEGMENT_SIZE + 1,
                end_episode=min((block + 1) * SEGMENT_SIZE, scored[-1][0]),
                scored_episodes=len(blocks[block]),
                mean_score=mean,
                change_from_previous=None if previous is None else mean - previous,
            )
        )
        previous = mean
    return segments
