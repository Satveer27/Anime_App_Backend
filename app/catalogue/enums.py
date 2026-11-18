"""The fixed values the public list and aggregate endpoints accept.

MAL stores its own texts (`TV`, `Currently Airing`, ...). The API takes clean lowercase names
instead, validated as enums, and maps them to the stored text here, so a request value never
reaches SQL as free text.
"""

from enum import StrEnum

from app.catalogue.models import STATUS_AIRING, STATUS_NOT_YET_AIRED


class AnimeType(StrEnum):
    TV = "tv"
    MOVIE = "movie"
    OVA = "ova"
    SPECIAL = "special"
    ONA = "ona"
    TV_SPECIAL = "tv_special"


class AnimeStatus(StrEnum):
    AIRING = "airing"
    FINISHED = "finished"
    UPCOMING = "upcoming"


class AnimeSeason(StrEnum):
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    FALL = "fall"


class ReleaseStatus(StrEnum):
    """Whether an anime is really putting episodes out, judged from its episode air dates.

    MAL's status says "Currently Airing" for a show on a long break (One Piece between arcs), so
    this looks at the episodes themselves.
    """

    RELEASING = "releasing"  # an episode aired in the last RELEASING_WITHIN_DAYS days
    ON_BREAK = "on_break"  # airing, has aired episodes, but none in that window
    NO_EPISODE_DATA = "no_episode_data"  # airing, but we hold no episodes for it


class TagMatch(StrEnum):
    """With several tags: the anime must have `all` of them, or `any` one."""

    ALL = "all"
    ANY = "any"


class AnimeSort(StrEnum):
    """How the anime list is ordered. `relevance` needs a search text."""

    RELEVANCE = "relevance"
    MEMBERS = "members"
    SCORE = "score"
    FAVORITES = "favorites"
    NEWEST = "newest"
    LATEST_EPISODE = "latest_episode"
    TITLE = "title"


class StudioSort(StrEnum):
    ANIME_COUNT = "anime_count"
    NAME = "name"


class GroupSort(StrEnum):
    """How aggregate groups (tags, studios) are ordered."""

    ANIME_COUNT = "anime_count"
    MEAN_SCORE = "mean_score"
    TOTAL_MEMBERS = "total_members"


class Order(StrEnum):
    ASC = "asc"
    DESC = "desc"


STORED_TYPE = {
    AnimeType.TV: "TV",
    AnimeType.MOVIE: "Movie",
    AnimeType.OVA: "OVA",
    AnimeType.SPECIAL: "Special",
    AnimeType.ONA: "ONA",
    AnimeType.TV_SPECIAL: "TV Special",
}
STORED_STATUS = {
    AnimeStatus.AIRING: STATUS_AIRING,
    AnimeStatus.FINISHED: "Finished Airing",
    AnimeStatus.UPCOMING: STATUS_NOT_YET_AIRED,
}
# Seasons are stored as the same lowercase words the API uses.
STORED_SEASON = {season: season.value for season in AnimeSeason}

# The reverse of STORED_TYPE, to give a response row the API name of a stored type.
API_TYPE = {stored: api.value for api, stored in STORED_TYPE.items()}
