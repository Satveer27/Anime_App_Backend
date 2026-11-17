"""Small summaries over a season's episode points: by year, best and worst, filler. Pure."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC
from statistics import fmean, pstdev

from app.statistics.calculations import safe_ratio
from app.statistics.schemas import (
    EpisodeBreakdownSchema,
    EpisodePointSchema,
    FillerComparisonSchema,
    HookSchema,
    ScoreExtremeSchema,
    VolatilitySchema,
    YearlySchema,
)
from app.statistics.trend import MIN_SCORED_EPISODES, Scored

# The opening stretch that makes the hook: the first 3 scored episodes.
HOOK_EPISODES = 3


def yearly_summary(episodes: Sequence[EpisodePointSchema]) -> list[YearlySchema]:
    """Group by the year an episode aired, oldest first. Episodes with no air date are left out."""
    counts: dict[int, int] = defaultdict(int)
    scores: dict[int, list[float]] = defaultdict(list)
    for episode in episodes:
        if episode.aired is None:
            continue
        year = episode.aired.astimezone(UTC).year
        counts[year] += 1
        if episode.mal_score is not None:
            scores[year].append(episode.mal_score)
    return [
        YearlySchema(
            year=year,
            episode_count=counts[year],
            scored_episodes=len(scores[year]),
            mean_score=fmean(scores[year]) if scores[year] else None,
        )
        for year in sorted(counts)
    ]


def score_extremes(
    episodes: Sequence[EpisodePointSchema],
) -> tuple[ScoreExtremeSchema | None, ScoreExtremeSchema | None]:
    """The best and the worst score, each with every episode tied on it. (None, None) if unscored."""
    scored = [(e.episode_number, e.mal_score) for e in episodes if e.mal_score is not None]
    if not scored:
        return None, None
    top = max(score for _, score in scored)
    bottom = min(score for _, score in scored)
    return (
        ScoreExtremeSchema(
            mal_score=top, episode_numbers=[number for number, score in scored if score == top]
        ),
        ScoreExtremeSchema(
            mal_score=bottom,
            episode_numbers=[number for number, score in scored if score == bottom],
        ),
    )


def episode_breakdown(episodes: Sequence[EpisodePointSchema]) -> EpisodeBreakdownSchema:
    """The share of all stored episodes that are filler, recap, or neither."""
    total = len(episodes)
    filler = sum(1 for e in episodes if e.filler)
    recap = sum(1 for e in episodes if e.recap)
    regular = sum(1 for e in episodes if not e.filler and not e.recap)
    return EpisodeBreakdownSchema(
        total_episodes=total,
        filler_episodes=filler,
        filler_share=safe_ratio(filler, total),
        recap_episodes=recap,
        recap_share=safe_ratio(recap, total),
        regular_episodes=regular,
        regular_share=safe_ratio(regular, total),
    )


def volatility(scored: Scored) -> VolatilitySchema | None:
    """Spread of the scores and the average jump between consecutive scored episodes."""
    if len(scored) < MIN_SCORED_EPISODES:
        return None
    scores = [score for _, score in scored]
    jumps = [abs(after - before) for before, after in zip(scores, scores[1:], strict=False)]
    return VolatilitySchema(standard_deviation=pstdev(scores), mean_episode_change=fmean(jumps))


def hook(scored: Scored) -> HookSchema | None:
    """The opening episodes against the rest, and the latest episode against the ones before it."""
    if len(scored) < MIN_SCORED_EPISODES:
        return None
    scores = [score for _, score in scored]
    first_mean = fmean(scores[:HOOK_EPISODES])
    rest_mean = fmean(scores[HOOK_EPISODES:])
    return HookSchema(
        first_episodes=HOOK_EPISODES,
        first_mean=first_mean,
        rest_mean=rest_mean,
        hook_difference=first_mean - rest_mean,
        latest_episode=scored[-1][0],
        latest_score=scores[-1],
        latest_vs_rest=scores[-1] - fmean(scores[:-1]),
    )


def filler_comparison(episodes: Sequence[EpisodePointSchema]) -> FillerComparisonSchema:
    """Mean score of scored filler episodes against scored non-filler ones; recaps are neither."""
    filler = [e.mal_score for e in episodes if e.mal_score is not None and e.filler]
    non_filler = [
        e.mal_score for e in episodes if e.mal_score is not None and not e.filler and not e.recap
    ]
    return FillerComparisonSchema(
        filler_episodes=len(filler),
        filler_mean=fmean(filler) if filler else None,
        non_filler_episodes=len(non_filler),
        non_filler_mean=fmean(non_filler) if non_filler else None,
    )
