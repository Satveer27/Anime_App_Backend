from app.ingestion.parsers.common import clean_text, to_int

# ADR 0014: genres, themes and demographics share one id space and one `tag` table.
# `explicit_genres` is not stored, and none of the 700 anime in the probe sample had any.
TAG_LISTS = ("genres", "themes", "demographics")
STUDIO_LISTS = ("studios",)


def extract_tags(payload: dict) -> list[tuple[int, str]]:
    """The (mal_id, name) of every genre, theme and demographic in a stored `/full` response."""
    return _named_items(payload, TAG_LISTS)


def extract_studios(payload: dict) -> list[tuple[int, str]]:
    """The (mal_id, name) of every studio in a stored `/full` response. Producers and
    licensors are not stored."""
    return _named_items(payload, STUDIO_LISTS)


def _named_items(payload: dict, list_names: tuple[str, ...]) -> list[tuple[int, str]]:
    """Items like {"mal_id": 1, "name": "Action", ...} from the given lists, in order.

    An item without an id or a name is skipped, and an id seen twice (the same id in two
    lists) is kept once.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    found: dict[int, str] = {}
    for list_name in list_names:
        items = data.get(list_name)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            mal_id = to_int(item.get("mal_id"))
            name = clean_text(item.get("name"))
            if mal_id is not None and name is not None:
                found.setdefault(mal_id, name)
    return list(found.items())
