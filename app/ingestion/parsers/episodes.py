from decimal import Decimal

from app.catalogue.models import Episode
from app.ingestion.parsers.common import (
    clean_html_text,
    sha256_hex,
    to_datetime,
    to_decimal,
    to_int,
)


def clean_episodes(payload: list[dict], anime_id: int) -> list[Episode]:
    """Turn a stored episodes response into Episode rows for one anime.

    `payload` is what the fetch job stored: a list of pages, each `{"data": [...], ...}`.
    The episode's own `mal_id` is its episode number. An item without one is skipped, and a
    number that appears twice keeps its last row. `anilist_score` and `imdb_score` are never
    set: they belong to other pipelines, so an upsert leaves them alone.
    """
    episodes: dict[int, Episode] = {}
    for page in payload if isinstance(payload, list) else []:
        items = page.get("data") if isinstance(page, dict) else None
        for item in items if isinstance(items, list) else []:
            episode = _clean_episode(item, anime_id)
            if episode is not None:
                episodes[episode.episode_number] = episode
    return list(episodes.values())


def _clean_episode(item, anime_id: int) -> Episode | None:
    if not isinstance(item, dict):
        return None
    number = to_int(item.get("mal_id"))
    if number is None:  # 0 is a real episode number, so test for None, not for falsy
        return None

    synopsis = clean_html_text(item.get("synopsis"))
    return Episode(
        anime_id=anime_id,
        episode_number=number,
        title=clean_html_text(item.get("title")),
        title_japanese=clean_html_text(item.get("title_japanese")),
        title_romanji=clean_html_text(item.get("title_romanji")),
        aired=to_datetime(item.get("aired")),
        duration_seconds=to_int(item.get("duration")) or None,
        synopsis=synopsis,
        synopsis_hash=sha256_hex(synopsis) if synopsis else None,
        # NOT NULL columns, so a missing value is False, as the column default is
        filler=bool(item.get("filler")),
        recap=bool(item.get("recap")),
        mal_score=_score(item.get("score")),
    )


def _score(value) -> Decimal | None:
    """MAL scores episodes 0-5 and uses 0 for "no score": stored 0-10, so doubled, 0 -> None."""
    score = to_decimal(value)
    return score * 2 if score else None
