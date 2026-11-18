from pydantic import BaseModel, Field

# Response schemas for the platform-wide aggregates under /api/v1/statistics (#53).
# Every row summarises one group of anime (a year, a type, a season, a tag or a studio).


class GroupStatsSchema(BaseModel):
    """The numbers every group row has.

    The score numbers only use anime that have a score and at least `min_ratings` ratings;
    `scored_count` says how many those are. `anime_count` and `total_members` cover every
    anime in the group.
    """

    anime_count: int = Field(..., description="All anime in the group")
    scored_count: int = Field(..., description="Anime the score numbers are based on")
    mean_score: float | None = Field(
        default=None, description="Average MAL score per anime (each counts once); null if none"
    )
    median_score: float | None = None
    total_members: int = Field(..., description="Members across every anime in the group")


class YearRowSchema(GroupStatsSchema):
    year: int


class TypeRowSchema(GroupStatsSchema):
    type: str = Field(..., description="The filter value: tv, movie, ova, special, ona, tv_special")
    label: str = Field(..., description="MAL's name for the type")


class SeasonRowSchema(GroupStatsSchema):
    season: str


class TagRowSchema(GroupStatsSchema):
    tag_id: int
    name: str


class StudioRowSchema(GroupStatsSchema):
    studio_id: int
    name: str


class ByYearResponse(BaseModel):
    min_ratings: int = Field(..., description="Anime need this many ratings to count in the scores")
    items: list[YearRowSchema]


class ByTypeResponse(BaseModel):
    min_ratings: int
    items: list[TypeRowSchema]


class BySeasonResponse(BaseModel):
    min_ratings: int
    items: list[SeasonRowSchema]


class ByTagResponse(BaseModel):
    min_ratings: int
    items: list[TagRowSchema]


class ByStudioResponse(BaseModel):
    min_ratings: int
    min_anime: int = Field(..., description="A studio needs this many anime with a usable score")
    total: int = Field(..., description="Studios that qualify, across all pages")
    limit: int
    offset: int
    items: list[StudioRowSchema]
