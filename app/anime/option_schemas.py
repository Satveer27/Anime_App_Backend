from pydantic import BaseModel, Field

# Response schemas for the endpoints that fill the frontend's filter controls.


class OptionSchema(BaseModel):
    """One choice in a fixed list: send `value` as the filter, show `label`."""

    value: str
    label: str
    description: str | None = None


class TagOptionSchema(BaseModel):
    tag_id: int = Field(..., description="The value for the `tag_id` filter")
    name: str
    anime_count: int = Field(..., description="Anime with this tag, of any type")


class YearRangeSchema(BaseModel):
    min: int | None = Field(default=None, description="The earliest start year in the catalogue")
    max: int | None = Field(default=None, description="The latest, which may be an announced year")


class LimitsSchema(BaseModel):
    max_page_size: int
    max_filter_values: int = Field(..., description="How many times a filter can be repeated")
    max_released_days: int = Field(..., description="The most `released_days` looks back")


class FilterOptionsResponse(BaseModel):
    """Everything the anime list's filter controls offer, in one call.

    Genres, themes and demographics share one tag list: MAL does not say which is which.
    """

    types: list[OptionSchema]
    statuses: list[OptionSchema]
    seasons: list[OptionSchema]
    release_statuses: list[OptionSchema]
    sorts: list[OptionSchema]
    orders: list[OptionSchema]
    tag_matches: list[OptionSchema]
    years: YearRangeSchema
    limits: LimitsSchema
    tags: list[TagOptionSchema]


class StudioOptionSchema(BaseModel):
    studio_id: int = Field(..., description="The value for the `studio_id` filter")
    name: str
    anime_count: int


class StudioListResponse(BaseModel):
    items: list[StudioOptionSchema]
    total: int = Field(..., description="Studios matching, across all pages")
    limit: int
    offset: int
