import re

from app.catalogue.models import Anime
from app.ingestion.parsers.common import (
    clean_html_text,
    clean_text,
    sha256_hex,
    to_datetime,
    to_decimal,
    to_int,
)

REWRITE_TAG = "[Written by MAL Rewrite]"
# What MAL shows when a title has no synopsis (ADR 0014). None of these were in the probe sample,
# where a missing synopsis arrives as null.
PLACEHOLDER_SYNOPSES = {
    "no synopsis information has been added to this title.",
    "no synopsis has been added for this title.",
}
DURATION_PART = re.compile(r"(\d+)\s*(hr|min|sec)")
SECONDS_PER_UNIT = {"hr": 3600, "min": 60, "sec": 1}


def clean_full(payload: dict) -> Anime | None:
    """Turn a stored `/anime/{id}/full` response into an Anime, or None if it has no usable id/title.

    Every column this pipeline owns is set, to None when the source has no value, so an upsert
    mirrors the source. `anilist_score` and `imdb_score` are never set: they belong to other
    pipelines, and an unset attribute is left out of an upsert (see `Anime.__dict__`).
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    mal_id = to_int(data.get("mal_id"))
    if not mal_id:
        return None

    title = (
        clean_html_text(data.get("title"))
        or clean_html_text(data.get("title_english"))
        or clean_html_text(data.get("title_japanese"))
    )
    if mal_id is None or title is None:
        return None

    aired = data.get("aired") or {}
    synopsis = _clean_synopsis(data.get("synopsis"))
    return Anime(
        mal_id=mal_id,
        title=title,
        title_english=clean_html_text(data.get("title_english")),
        title_japanese=clean_html_text(data.get("title_japanese")),
        type=clean_text(data.get("type")),
        status=clean_text(data.get("status")),
        rating=clean_text(data.get("rating")),
        source_material=clean_text(data.get("source")),
        season=clean_text(data.get("season")),
        year=_year(data, aired),
        episodes=to_int(data.get("episodes")),
        duration_minutes=_duration_minutes(data.get("duration")),
        aired_from=to_datetime(aired.get("from")),
        aired_to=to_datetime(aired.get("to")),
        mal_score=to_decimal(data.get("score")),
        scored_by_mal=to_int(data.get("scored_by")),
        members_mal=to_int(data.get("members")),
        favorites_mal=to_int(data.get("favorites")),
        rank_mal=to_int(data.get("rank")),
        popularity_mal=to_int(data.get("popularity")),
        synopsis=synopsis,
        synopsis_hash=sha256_hex(synopsis) if synopsis else None,
        background=clean_html_text(data.get("background")),
        image_url=_image_url(data.get("images")),
    )


def _year(data: dict, aired: dict) -> int | None:
    """The season year, or the year the show started airing when MAL has no season year."""
    year = to_int(data.get("year"))
    if year is not None:
        return year
    return to_int(((aired.get("prop") or {}).get("from") or {}).get("year"))


def _duration_minutes(value) -> int | None:
    """'24 min per ep' -> 24, '1 hr 55 min' -> 115, '45 sec' -> 1, 'Unknown' -> None."""
    if not isinstance(value, str):
        return None
    seconds = sum(int(n) * SECONDS_PER_UNIT[unit] for n, unit in DURATION_PART.findall(value))
    return max(1, round(seconds / 60)) if seconds else None


def _clean_synopsis(value) -> str | None:
    text = clean_html_text(value)
    if text is None:
        return None
    text = text.replace(REWRITE_TAG, "").strip()
    if not text or text.lower() in PLACEHOLDER_SYNOPSES:
        return None
    return text


def _image_url(images) -> str | None:
    if not isinstance(images, dict):
        return None
    jpg = images.get("jpg") or {}
    webp = images.get("webp") or {}
    return (
        clean_text(jpg.get("large_image_url"))
        or clean_text(jpg.get("image_url"))
        or clean_text(webp.get("large_image_url"))
    )
