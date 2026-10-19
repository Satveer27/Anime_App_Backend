from app.catalogue.models import Review
from app.ingestion.parsers.common import clean_html_text, clean_text, to_datetime, to_int

REACTIONS = (
    "overall",
    "nice",
    "love_it",
    "funny",
    "confusing",
    "informative",
    "well_written",
    "creative",
)


def clean_reviews(payload: dict, anime_id: int) -> list[Review]:
    """Turn a stored `/anime/{id}/reviews` response (page 1) into Review rows for one anime.

    `payload` is `{"data": [...], "pagination": ...}`. `anime_id` comes from the caller. A review
    without an id or without text is skipped (the text column is NOT NULL), and an id that
    appears twice keeps its last row. The review's `tags` and `type` are not stored; they stay
    in the raw layer (ADR 0014). The reviewer's username is public MAL data and is kept.
    """
    items = payload.get("data") if isinstance(payload, dict) else None
    reviews: dict[int, Review] = {}
    for item in items if isinstance(items, list) else []:
        review = _clean_review(item, anime_id)
        if review is not None:
            reviews[review.mal_id] = review
    return list(reviews.values())


def _clean_review(item, anime_id: int) -> Review | None:
    if not isinstance(item, dict):
        return None
    mal_id = to_int(item.get("mal_id"))
    # MAL leaves HTML entities in the text (&rsquo; &quot; ...), which would reach the LLM
    text = clean_html_text(item.get("review"))
    if mal_id is None or text is None:
        return None

    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    reactions = item.get("reactions") if isinstance(item.get("reactions"), dict) else {}
    return Review(
        mal_id=mal_id,
        anime_id=anime_id,
        score=to_int(item.get("score")),
        review=text,
        review_url=clean_text(item.get("url")),
        username=clean_text(user.get("username")),
        username_url=clean_text(user.get("url")),
        date=to_datetime(item.get("date")),
        # NOT NULL columns, so a missing value is False, as the column default is
        is_spoiler=bool(item.get("is_spoiler")),
        is_preliminary=bool(item.get("is_preliminary")),
        episodes_watched=to_int(item.get("episodes_watched")),
        **{f"reaction_{name}": to_int(reactions.get(name)) for name in REACTIONS},
    )
