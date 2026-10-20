from app.catalogue.models import AnimeStatisticsMal
from app.ingestion.parsers.common import to_int

# NOT NULL columns: without all of them there is no row to store
COUNTS = ("watching", "completed", "on_hold", "dropped", "plan_to_watch", "total")
SCORES = range(1, 11)


def clean_statistics(payload: dict, anime_id: int) -> AnimeStatisticsMal | None:
    """Turn a stored `/anime/{id}/statistics` response into a row, or None if it is unusable.

    `payload` is `{"data": {...}}`. `anime_id` comes from the caller, because the response does
    not contain it. A missing vote count stays None. The source's percentage is not stored
    because it is derivable from the votes.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    counts = {name: to_int(data.get(name)) for name in COUNTS}
    if any(value is None for value in counts.values()):
        return None

    return AnimeStatisticsMal(anime_id=anime_id, **counts, **_votes(data.get("scores")))


def _votes(scores) -> dict[str, int | None]:
    """[{"score": 7, "votes": 108946, ...}, ...] -> {"votes_1": ..., ..., "votes_10": ...}."""
    votes = {f"votes_{score}": None for score in SCORES}
    for item in scores if isinstance(scores, list) else []:
        score = to_int(item.get("score")) if isinstance(item, dict) else None
        if score in SCORES:
            votes[f"votes_{score}"] = to_int(item.get("votes"))
    return votes
