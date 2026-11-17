from datetime import UTC, datetime

import structlog

from app.catalogue.models import Anime, AnimeStatisticsMal
from app.catalogue.repository import CatalogueRepository, CohortStanding
from app.statistics.calculations import (
    HIGH_SCORES,
    LOW_SCORES,
    STATUS_AIRING,
    build_list_status,
    build_score_distribution,
    safe_ratio,
    standard_deviation,
    vote_share,
)
from app.statistics.episode_analysis import (
    episode_breakdown,
    filler_comparison,
    hook,
    score_extremes,
    volatility,
    yearly_summary,
)
from app.statistics.exceptions import AnimeNotFoundError
from app.statistics.schemas import (
    AnimeStatisticsResponse,
    CohortMetricSchema,
    CohortSchema,
    CoreSchema,
    EngagementSchema,
    EpisodePointSchema,
    EpisodeScoresResponse,
    PolarizationSchema,
    ReviewSchema,
    ReviewsSchema,
    StatisticsSchema,
    StudioSchema,
    TagSchema,
    Verdict,
)
from app.statistics.trend import build_segments, compute_trend, smooth

logger = structlog.get_logger()

# A cohort smaller than this gives no percentile: the ranks would mean too little.
MIN_COHORT_SIZE = 30
# Fewer members than this rated the anime, so its score is not reliable.
LOW_SAMPLE_VOTES = 500
# A hidden gem scores in the top fifth of its cohort but sits in the least popular 30%.
GEM_MIN_SCORE_PERCENTILE = 0.80
GEM_MAX_POPULARITY_PERCENTILE = 0.30


class StatisticsService:
    def __init__(self, catalogue_repository: CatalogueRepository):
        self.catalogue_repository = catalogue_repository

    async def get_anime_statistics(self, mal_id: int) -> AnimeStatisticsResponse:
        """Everything the per-anime statistics page shows, or AnimeNotFoundError.

        `statistics` is None when the anime has no statistics row; the core fields, tags,
        studios and reviews are still returned.
        """
        found = await self.catalogue_repository.get_anime_with_statistics(mal_id)
        if found is None:
            raise AnimeNotFoundError()
        anime, stats_row = found

        tags = await self.catalogue_repository.get_tags_of_anime(mal_id)
        studios = await self.catalogue_repository.get_studios_of_anime(mal_id)
        reviews = await self.catalogue_repository.get_reviews_of_anime(mal_id)
        statistics = None if stats_row is None else await self._build_statistics(anime, stats_row)

        logger.info(
            "anime_statistics_served",
            mal_id=mal_id,
            has_statistics=statistics is not None,
            review_count=len(reviews),
        )
        return AnimeStatisticsResponse(
            core=CoreSchema.model_validate(anime),
            statistics=statistics,
            tags=[TagSchema.model_validate(tag) for tag in tags],
            studios=[StudioSchema.model_validate(studio) for studio in studios],
            reviews=ReviewsSchema(items=[ReviewSchema.model_validate(r) for r in reviews]),
        )

    async def get_episode_scores(self, mal_id: int) -> EpisodeScoresResponse:
        """The episode graph data and the per-season verdict, or AnimeNotFoundError.

        An anime with no episodes is a normal answer: 200 with an empty list and
        `insufficient_data`.
        """
        if not await self.catalogue_repository.anime_exists(mal_id):
            raise AnimeNotFoundError()
        episodes = await self.catalogue_repository.get_episodes_of_anime(mal_id)

        scored = [
            (episode.episode_number, float(episode.mal_score))
            for episode in episodes
            if episode.mal_score is not None
        ]
        trend = compute_trend(scored)
        smoothed = {}
        if trend.verdict != Verdict.INSUFFICIENT_DATA:
            smoothed = dict(
                zip((n for n, _ in scored), smooth([s for _, s in scored]), strict=True)
            )

        points = [
            EpisodePointSchema(
                episode_number=episode.episode_number,
                title=episode.title,
                title_japanese=episode.title_japanese,
                aired=episode.aired,
                synopsis=episode.synopsis,
                filler=episode.filler,
                recap=episode.recap,
                mal_score=None if episode.mal_score is None else float(episode.mal_score),
                smoothed_score=smoothed.get(episode.episode_number),
            )
            for episode in episodes
        ]
        best, worst = score_extremes(points)

        logger.info(
            "episode_scores_served",
            mal_id=mal_id,
            episode_count=len(points),
            scored_episodes=len(scored),
            verdict=trend.verdict.value,
        )
        return EpisodeScoresResponse(
            mal_id=mal_id,
            episodes=points,
            trend=trend,
            segments=build_segments(scored),
            yearly=yearly_summary(points),
            best=best,
            worst=worst,
            filler_comparison=filler_comparison(points),
            breakdown=episode_breakdown(points),
            volatility=volatility(scored),
            hook=hook(scored),
        )

    async def _build_statistics(
        self, anime: Anime, stats_row: AnimeStatisticsMal
    ) -> StatisticsSchema:
        votes = [getattr(stats_row, f"votes_{score}") for score in range(1, 11)]
        year_from, year_to = cohort_years(anime, _current_year())
        standing = await self.catalogue_repository.get_cohort_standing(
            anime.mal_id, anime.type, year_from, year_to
        )
        low_sample = _low_sample(anime)
        return StatisticsSchema(
            list_status=build_list_status(
                watching=stats_row.watching,
                completed=stats_row.completed,
                on_hold=stats_row.on_hold,
                dropped=stats_row.dropped,
                plan_to_watch=stats_row.plan_to_watch,
                total=stats_row.total,
                anime_status=anime.status,
            ),
            score_distribution=build_score_distribution(votes),
            polarization=PolarizationSchema(
                standard_deviation=standard_deviation(votes),
                low_vote_share=vote_share(votes, LOW_SCORES),
                high_vote_share=vote_share(votes, HIGH_SCORES),
            ),
            engagement=EngagementSchema(
                favorites_per_member=safe_ratio(anime.favorites_mal, anime.members_mal),
                scored_by_ratio=safe_ratio(anime.scored_by_mal, anime.members_mal),
                plan_to_watch_share=safe_ratio(stats_row.plan_to_watch, anime.members_mal),
                low_sample=low_sample,
            ),
            cohort=_build_cohort(anime, standing, low_sample),
        )


def _current_year() -> int:
    return datetime.now(UTC).year


def cohort_years(anime: Anime, current_year: int) -> tuple[int | None, int | None]:
    """The years an anime aired: its start year to the year its run ended.

    The end is the year of `aired_to`; for an anime that is still airing it is the current
    year; with neither it is the start year. (None, None) when the start year is unknown.
    """
    if anime.year is None:
        return None, None
    if anime.aired_to is not None:
        end = anime.aired_to.year
    elif anime.status == STATUS_AIRING:
        end = current_year
    else:
        end = anime.year
    return anime.year, max(end, anime.year)


def _low_sample(anime: Anime) -> bool | None:
    if anime.scored_by_mal is None:
        return None
    return anime.scored_by_mal < LOW_SAMPLE_VOTES


def _cohort_metric(
    count: int, rank: int | None, percentile: float | None
) -> CohortMetricSchema | None:
    """A rank and percentile, or None if the cohort is too small or the anime has no value."""
    if count < MIN_COHORT_SIZE or rank is None or percentile is None:
        return None
    return CohortMetricSchema(rank=rank, percentile=percentile)


def _build_cohort(
    anime: Anime, standing: CohortStanding | None, low_sample: bool | None
) -> CohortSchema:
    year_from, year_to = cohort_years(anime, _current_year())
    if standing is None:
        return CohortSchema(type=anime.type, year_from=year_from, year_to=year_to, cohort_size=0)

    score = _cohort_metric(standing.score_count, standing.score_rank, standing.score_percentile)
    popularity = _cohort_metric(
        standing.popularity_count, standing.popularity_rank, standing.popularity_percentile
    )
    favorites = _cohort_metric(
        standing.favorites_count, standing.favorites_rank, standing.favorites_percentile
    )
    drop_rate = _cohort_metric(
        standing.drop_rate_count, standing.drop_rate_rank, standing.drop_rate_percentile
    )
    completion_rate = _cohort_metric(
        standing.completion_rate_count,
        standing.completion_rate_rank,
        standing.completion_rate_percentile,
    )

    gap = None
    is_hidden_gem = None
    if score is not None and popularity is not None:
        gap = score.percentile - popularity.percentile
        # Few votes make a score noisy, and an obscure anime with a handful of high votes would
        # otherwise always look like a gem. Unknown vote counts get no benefit of the doubt.
        is_hidden_gem = (
            low_sample is False
            and score.percentile >= GEM_MIN_SCORE_PERCENTILE
            and popularity.percentile <= GEM_MAX_POPULARITY_PERCENTILE
        )

    return CohortSchema(
        type=anime.type,
        year_from=year_from,
        year_to=year_to,
        cohort_size=standing.cohort_size,
        score=score,
        popularity=popularity,
        favorites=favorites,
        drop_rate=drop_rate,
        completion_rate=completion_rate,
        hidden_gem_gap=gap,
        is_hidden_gem=is_hidden_gem,
    )
