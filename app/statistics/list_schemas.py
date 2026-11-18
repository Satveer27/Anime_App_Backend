from pydantic import BaseModel, Field

# Response schemas for the platform-wide lists under /api/v1/statistics (#54).
# Every row is one anime, ranked by the list's own measure.


class ListRowSchema(BaseModel):
    mal_id: int
    title: str
    type: str = Field(..., description="The filter value: tv, movie, ova, special, ona, tv_special")
    year: int | None = None
    mal_score: float | None = None
    members_mal: int | None = None


class HiddenGemRowSchema(ListRowSchema):
    scored_by_mal: int
    score_percentile: float = Field(
        ..., description="Share of this type's anime scored lower, 0-1 (higher is better)"
    )
    members_percentile: float = Field(
        ..., description="Share of this type's anime with fewer members, 0-1 (lower is rarer)"
    )


class MostDroppedRowSchema(ListRowSchema):
    dropped: int
    started: int = Field(..., description="Members who started it: total minus plan to watch")
    drop_rate: float = Field(..., description="dropped / started, 0-1")


class HiddenGemsResponse(BaseModel):
    min_ratings: int = Field(
        ..., description="Percentiles are ranked among anime of the type with this many ratings"
    )
    score_percentile: float = Field(..., description="Score must be at or above this percentile")
    members_percentile: float = Field(
        ..., description="Members must be at or below this percentile"
    )
    min_scored_by: int = Field(..., description="Anime need at least this many MAL ratings")
    total: int = Field(..., description="Anime that qualify, across all pages")
    limit: int
    offset: int
    items: list[HiddenGemRowSchema]


class MostDroppedResponse(BaseModel):
    min_members: int = Field(..., description="Anime need at least this many MAL members")
    total: int = Field(..., description="Anime that qualify, across all pages")
    limit: int
    offset: int
    items: list[MostDroppedRowSchema]
