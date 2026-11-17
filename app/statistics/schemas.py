from datetime import datetime

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
    low_vote_share: float | None = Field(default=None, description="Share of 1-4 votes, 0-100")
    high_vote_share: float | None = Field(default=None, description="Share of 9-10 votes, 0-100")


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
    """This anime against all anime of the same `type` and `year`.

    The metrics are null when the cohort is smaller than 30 or when the anime has no `type`
    or `year`. `cohort_size` is always present so a null can be explained.
    """

    type: str | None = None
    year: int | None = None
    cohort_size: int
    score: CohortMetricSchema | None = None
    popularity: CohortMetricSchema | None = None
    favorites: CohortMetricSchema | None = Field(
        default=None, description="Favorites per member; high means a cult following"
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
