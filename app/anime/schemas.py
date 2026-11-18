from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnimeListItemSchema(BaseModel):
    """One anime in a list or search result: enough to show a card, not the long texts."""

    model_config = ConfigDict(from_attributes=True)

    mal_id: int
    title: str
    title_english: str | None = None
    title_japanese: str | None = None
    type: str | None = Field(default=None, description="TV, Movie, OVA, ... as MAL names it")
    status: str | None = Field(
        default=None, description="Currently Airing, Finished Airing or Not yet aired"
    )
    season: str | None = None
    year: int | None = None
    episodes: int | None = None
    aired_from: datetime | None = None
    mal_score: float | None = Field(default=None, description="MAL score, 0-10")
    scored_by_mal: int | None = None
    members_mal: int | None = None
    favorites_mal: int | None = None
    rank_mal: int | None = None
    popularity_mal: int | None = None
    image_url: str | None = None


class AnimeListResponse(BaseModel):
    items: list[AnimeListItemSchema]
    total: int = Field(..., description="Anime matching the filters, across all pages")
    limit: int
    offset: int
