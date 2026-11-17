from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Response schemas for GET /api/v1/anime/{mal_id}/statistics (#51).
# Every derived field is optional: it is null when its inputs are null or its denominator is
# zero, never an error. All scores are MAL's, on a 0-10 scale (ADR 0020). There is no `as_of`
# or `updated_at` (ADR 0021).


class CoreSchema(BaseModel):
    """Single-valued fields straight from the `anime` row."""

    model_config = ConfigDict(from_attributes=True)

    mal_id: int
    title: str
    title_english: str | None = None
    title_japanese: str | None = None
    type: str | None = Field(default=None, description="tv, movie, ova, ...")
    status: str | None = Field(default=None, description="Currently Airing, Finished Airing, ...")
    rating: str | None = None
    source_material: str | None = None
    season: str | None = None
    year: int | None = None
    episodes: int | None = None
    duration_minutes: int | None = None
    aired_from: datetime | None = None
    aired_to: datetime | None = None
    mal_score: float | None = Field(default=None, description="MAL score, 0-10")
    scored_by_mal: int | None = None
    members_mal: int | None = None
    favorites_mal: int | None = None
    rank_mal: int | None = Field(default=None, description="MAL score rank, 1 is best")
    popularity_mal: int | None = Field(default=None, description="MAL popularity rank, 1 is best")
    synopsis: str | None = None
    image_url: str | None = None


class TagSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mal_id: int
    name: str


class StudioSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mal_id: int
    name: str


class ListStatusSchema(BaseModel):
    """Where the members' lists put this anime.

    The three rates use `total - plan_to_watch` as the denominator: plan to watch is intent,
    not engagement. `completion_rate` is null while the anime is airing, and all three rates
    are null for an anime that has not aired.
    """

    watching: int
    completed: int
    on_hold: int
    dropped: int
    plan_to_watch: int
    total: int

    watching_share: float | None = Field(default=None, description="watching / total")
    completed_share: float | None = Field(default=None, description="completed / total")
    on_hold_share: float | None = Field(default=None, description="on_hold / total")
    dropped_share: float | None = Field(default=None, description="dropped / total")
    plan_to_watch_share: float | None = Field(default=None, description="plan_to_watch / total")

    completion_rate: float | None = Field(
        default=None, description="completed / (total - plan_to_watch); null while airing"
    )
    drop_rate: float | None = Field(default=None, description="dropped / (total - plan_to_watch)")
    watching_rate: float | None = Field(
        default=None, description="watching / (total - plan_to_watch)"
    )


class ScoreBucketSchema(BaseModel):
    score: int = Field(..., ge=1, le=10)
    votes: int | None = None
    percentage: float | None = Field(
        default=None, description="Share of all votes, 0-100; the ten buckets add up to 100"
    )


class ScoreDistributionSchema(BaseModel):
    """The vote histogram (always ten buckets, 1 to 10) and what it implies."""

    buckets: list[ScoreBucketSchema]
    total_votes: int | None = Field(default=None, description="Sum of the ten buckets")
    mean: float | None = Field(
        default=None, description="Average score computed from the histogram"
    )
    mode: int | None = Field(
        default=None, description="Most common score; the higher one if two tie"
    )


class PolarizationSchema(BaseModel):
    """How split the voters are. A high low-share and a high high-share together mean divisive."""

    standard_deviation: float | None = Field(
        default=None, description="Population standard deviation of the votes"
    )
    low_vote_share: float | None = Field(default=None, description="Share of 1-4 votes, 0-1")
    high_vote_share: float | None = Field(default=None, description="Share of 9-10 votes, 0-1")


class EngagementSchema(BaseModel):
    favorites_per_member: float | None = Field(default=None, description="favorites / members")
    scored_by_ratio: float | None = Field(
        default=None, description="scored_by_mal / members: how many members rated it"
    )
    plan_to_watch_share: float | None = Field(
        default=None, description="plan_to_watch / members: the backlog share"
    )
    low_sample: bool | None = Field(
        default=None,
        description="True when too few members rated it for the score to be reliable",
    )


class CohortMetricSchema(BaseModel):
    rank: int | None = Field(default=None, description="Position in the cohort, 1 is best")
    percentile: float | None = Field(
        default=None, description="Share of the cohort this anime beats, 0-1; higher is better"
    )


class CohortSchema(BaseModel):
    """This anime against all anime of the same `type` that started in the years it aired.

    `year_from` is the anime's start year and `year_to` the year its run ended (the current
    year while it is still airing), so a long runner is compared with everything that started
    while it was on air. For a normal one-season show both are the same year. The metrics are
    null when the cohort is smaller than 30 or when the anime has no `type` or `year`.
    `cohort_size` is always present so a null can be explained.
    """

    type: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    cohort_size: int
    score: CohortMetricSchema | None = None
    popularity: CohortMetricSchema | None = None
    favorites: CohortMetricSchema | None = Field(
        default=None, description="Favorites per member; high means a cult following"
    )
    drop_rate: CohortMetricSchema | None = Field(
        default=None,
        description="Drop rate, where a higher percentile means dropped LESS often than the "
        "cohort (rank 1 has the lowest drop rate)",
    )
    completion_rate: CohortMetricSchema | None = Field(
        default=None,
        description="Completion rate; null while airing. Higher percentile = finished more often",
    )
    hidden_gem_gap: float | None = Field(
        default=None,
        description=(
            "score percentile - popularity percentile. Large and positive means well rated "
            "but little watched; negative means popular but rated lower"
        ),
    )
    is_hidden_gem: bool | None = None


class StatisticsSchema(BaseModel):
    """Everything derived from `anime_statistics_mal` and the anime's own counters."""

    list_status: ListStatusSchema
    score_distribution: ScoreDistributionSchema
    polarization: PolarizationSchema
    engagement: EngagementSchema
    cohort: CohortSchema


class ReviewSchema(BaseModel):
    """One stored MAL review. The reviewer's username is public MAL data."""

    model_config = ConfigDict(from_attributes=True)

    mal_id: int
    score: int | None = None
    review: str
    review_url: str | None = None
    username: str | None = None
    date: datetime | None = None
    is_spoiler: bool
    is_preliminary: bool
    episodes_watched: int | None = None
    reaction_overall: int | None = None
    reaction_nice: int | None = None
    reaction_love_it: int | None = None
    reaction_funny: int | None = None
    reaction_confusing: int | None = None
    reaction_informative: int | None = None
    reaction_well_written: int | None = None
    reaction_creative: int | None = None


class ReviewsSchema(BaseModel):
    sample_note: str = Field(
        default=(
            "A sample of MAL's most helpful reviews (page 1 at each refresh), not every review. "
            "Reviews MAL later removed or that fell off page 1 keep their last-seen counts."
        ),
        description="Says what the reviews are, so a client cannot mistake them for the full set",
    )
    items: list[ReviewSchema]


class AnimeStatisticsResponse(BaseModel):
    """The endpoint's response. `statistics` is null when no statistics row is stored."""

    core: CoreSchema
    statistics: StatisticsSchema | None = None
    tags: list[TagSchema]
    studios: list[StudioSchema]
    reviews: ReviewsSchema


# --- GET /api/v1/anime/{mal_id}/statistics/episodes (#52) ----------------------------------
# One MAL entry (one season or cour) only, never a franchise (ADR 0020). Scores are MAL's
# episode scores on a 0-10 scale; an episode with no score keeps a null score and is a gap.


class Verdict(StrEnum):
    GETS_GOOD = "gets_good"
    CONSISTENTLY_GOOD = "consistently_good"
    DECLINES = "declines"
    FLAT = "flat"
    INSUFFICIENT_DATA = "insufficient_data"


class EpisodePointSchema(BaseModel):
    episode_number: int
    title: str | None = None
    title_japanese: str | None = None
    aired: datetime | None = None
    synopsis: str | None = None
    filler: bool
    recap: bool
    mal_score: float | None = Field(
        default=None, description="Raw MAL score, 0-10; null = no score"
    )
    smoothed_score: float | None = Field(
        default=None,
        description="Moving average of the scored episodes, for the 'gets good' graph. Null where "
        "the raw score is null, and for every episode when there is not enough data",
    )


class TrendSchema(BaseModel):
    """The verdict and the numbers behind it. Only `scored_episodes` is set for too little data."""

    verdict: Verdict
    scored_episodes: int
    baseline_size: int | None = Field(
        default=None, description="How many of the first scored episodes make the baseline"
    )
    baseline: float | None = Field(default=None, description="Mean score of the baseline episodes")
    threshold: float | None = Field(
        default=None, description="baseline + 0.5: the line the score has to reach and stay above"
    )
    smoothing_window: int | None = Field(
        default=None, description="Episodes in the moving average (5% of scored, at least 3)"
    )
    turning_point_episode: int | None = Field(
        default=None,
        description="gets_good only: the first episode from which the smoothed score stays above "
        "the threshold until the end",
    )
    mean_after_turning_point: float | None = None
    change: float | None = Field(
        default=None, description="mean_after_turning_point - baseline (gets_good only)"
    )
    dips_after_turning_point: int | None = Field(
        default=None, description="Smoothed points below the threshold after the turning point"
    )
    end_mean: float | None = Field(
        default=None, description="Mean score of the last scored episodes (same count as baseline)"
    )


class SegmentSchema(BaseModel):
    """A block of 200 episodes of a long series, so 'does it get good after 200' is visible."""

    start_episode: int
    end_episode: int
    scored_episodes: int
    mean_score: float
    change_from_previous: float | None = None


class YearlySchema(BaseModel):
    """Episodes grouped by the year they aired, so a very long series fits on one graph."""

    year: int
    episode_count: int
    scored_episodes: int
    mean_score: float | None = None


class ScoreExtremeSchema(BaseModel):
    mal_score: float
    episode_numbers: list[int] = Field(..., description="Every episode tied on this score")


class FillerComparisonSchema(BaseModel):
    """Scored filler against scored non-filler episodes; recap episodes count as neither."""

    filler_episodes: int
    filler_mean: float | None = None
    non_filler_episodes: int
    non_filler_mean: float | None = None


class VolatilitySchema(BaseModel):
    """How much the episode scores move. Needs at least 6 scored episodes."""

    standard_deviation: float = Field(
        ..., description="Spread of all the scores; also high for a show that simply rises"
    )
    mean_episode_change: float = Field(
        ...,
        description="Average jump between one scored episode and the next. High means a "
        "rollercoaster, low means steady, whatever the overall trend",
    )


class HookSchema(BaseModel):
    """How the season opens and closes against the rest. Needs at least 6 scored episodes."""

    first_episodes: int = Field(..., description="How many opening episodes make the hook")
    first_mean: float
    rest_mean: float = Field(..., description="Mean of every scored episode after the hook")
    hook_difference: float = Field(
        ..., description="first_mean - rest_mean; positive = a strong open"
    )
    latest_episode: int = Field(
        ..., description="The last scored episode: the finale once it has aired"
    )
    latest_score: float
    latest_vs_rest: float = Field(
        ..., description="latest_score minus the mean of all earlier scored episodes"
    )


class EpisodeBreakdownSchema(BaseModel):
    """How much of the stored episode list is filler or recap.

    Shares are 0-1 of every stored episode, scored or not. An episode flagged as both counts in
    both; `regular_episodes` are the ones that are neither. Null shares when there are none.
    """

    total_episodes: int
    filler_episodes: int
    filler_share: float | None = None
    recap_episodes: int
    recap_share: float | None = None
    regular_episodes: int
    regular_share: float | None = None


class EpisodeScoresResponse(BaseModel):
    mal_id: int
    episodes: list[EpisodePointSchema]
    trend: TrendSchema
    segments: list[SegmentSchema] = Field(
        default_factory=list, description="Only for series longer than 200 episodes"
    )
    yearly: list[YearlySchema]
    best: ScoreExtremeSchema | None = None
    worst: ScoreExtremeSchema | None = None
    filler_comparison: FillerComparisonSchema
    breakdown: EpisodeBreakdownSchema
    volatility: VolatilitySchema | None = None
    hook: HookSchema | None = None
