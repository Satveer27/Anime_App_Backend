import glob
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.ingestion.parsers.details import clean_full


def full(**overrides) -> dict:
    """A trimmed /full response in the shape Tenrai returns it."""
    data = {
        "mal_id": 1,
        "images": {
            "jpg": {"image_url": "https://x/a.jpg", "large_image_url": "https://x/al.jpg"},
            "webp": {"large_image_url": "https://x/al.webp"},
        },
        "title": "Cowboy Bebop",
        "title_english": "Cowboy Bebop",
        "title_japanese": "カウボーイビバップ",
        "type": "TV",
        "source": "Original",
        "episodes": 26,
        "status": "Finished Airing",
        "aired": {
            "from": "1998-04-03T00:00:00+00:00",
            "to": "1999-04-24T00:00:00+00:00",
            "prop": {"from": {"day": 3, "month": 4, "year": 1998}},
        },
        "duration": "24 min per ep",
        "rating": "R - 17+ (violence & profanity)",
        "score": 8.75,
        "scored_by": 1078339,
        "rank": 51,
        "popularity": 41,
        "members": 2091656,
        "favorites": 91054,
        "synopsis": "Crime is timeless.\n\n[Written by MAL Rewrite]",
        "background": "When Cowboy Bebop first aired...",
        "season": "spring",
        "year": 1998,
    }
    data.update(overrides)
    return {"data": data}


def test_a_full_response_maps_to_every_anime_column():
    anime = clean_full(full())

    assert anime.mal_id == 1
    assert anime.title == "Cowboy Bebop"
    assert anime.title_english == "Cowboy Bebop"
    assert anime.title_japanese == "カウボーイビバップ"
    assert (anime.type, anime.status, anime.source_material) == (
        "TV",
        "Finished Airing",
        "Original",
    )
    assert anime.rating == "R - 17+ (violence & profanity)"
    assert (anime.season, anime.year, anime.episodes) == ("spring", 1998, 26)
    assert anime.duration_minutes == 24
    assert anime.aired_from == datetime(1998, 4, 3, tzinfo=UTC)
    assert anime.aired_to == datetime(1999, 4, 24, tzinfo=UTC)
    assert anime.mal_score == Decimal("8.75")
    assert anime.scored_by_mal == 1078339
    assert (anime.members_mal, anime.favorites_mal) == (2091656, 91054)
    assert (anime.rank_mal, anime.popularity_mal) == (51, 41)
    assert anime.synopsis == "Crime is timeless."
    assert anime.background == "When Cowboy Bebop first aired..."
    assert anime.image_url == "https://x/al.jpg"


def test_a_missing_field_stays_none():
    anime = clean_full({"data": {"mal_id": 7, "title": "Bare"}})

    assert anime.mal_id == 7
    assert anime.title == "Bare"
    for column in (
        "title_english",
        "title_japanese",
        "type",
        "status",
        "rating",
        "source_material",
        "season",
        "year",
        "episodes",
        "duration_minutes",
        "aired_from",
        "aired_to",
        "mal_score",
        "scored_by_mal",
        "members_mal",
        "favorites_mal",
        "rank_mal",
        "popularity_mal",
        "synopsis",
        "synopsis_hash",
        "background",
        "image_url",
    ):
        assert getattr(anime, column) is None, column


def test_other_pipelines_columns_are_never_set():
    anime = clean_full(full())

    # An unset attribute is not in __dict__, so an upsert built from it cannot overwrite them
    for column in ("anilist_score", "imdb_score", "created_at", "updated_at"):
        assert column not in anime.__dict__, column
    # while a column this pipeline owns is set even when the source had no value
    assert "synopsis" in clean_full({"data": {"mal_id": 1, "title": "T"}}).__dict__


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {},
        {"data": None},
        {"data": []},
        {"data": {"title": "No id"}},
        {"data": {"mal_id": 1}},
        {"data": {"mal_id": 1, "title": "   "}},
        {"data": {"mal_id": "abc", "title": "T"}},
    ],
)
def test_unusable_responses_return_none(payload):
    assert clean_full(payload) is None


def test_the_title_falls_back_to_the_english_then_japanese_name():
    assert clean_full(full(title=None)).title == "Cowboy Bebop"
    assert clean_full(full(title=None, title_english=None)).title == "カウボーイビバップ"


def test_empty_strings_become_none():
    anime = clean_full(full(title_english="", background="  ", source=""))

    assert anime.title_english is None
    assert anime.background is None
    assert anime.source_material is None


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("24 min per ep", 24),
        ("24 min", 24),
        ("1 hr 55 min", 115),
        ("2 hr", 120),
        ("1 hr 30 min per ep", 90),
        ("45 sec", 1),
        ("10 sec per ep", 1),
        ("Unknown", None),
        ("", None),
        (None, None),
        (24, None),
    ],
)
def test_duration_is_converted_to_minutes(duration, expected):
    assert clean_full(full(duration=duration)).duration_minutes == expected


def test_the_rewrite_tag_is_stripped_and_the_hash_follows_the_cleaned_text():
    anime = clean_full(full(synopsis="Story.\n\n[Written by MAL Rewrite]"))

    assert anime.synopsis == "Story."
    assert anime.synopsis_hash == hashlib.sha256(b"Story.").hexdigest()


def test_the_same_synopsis_gives_the_same_hash_and_a_changed_one_a_different_hash():
    same = [
        clean_full(full(synopsis="Story.")),
        clean_full(full(synopsis="Story.\n[Written by MAL Rewrite]")),
    ]
    changed = clean_full(full(synopsis="Story, revised."))

    assert same[0].synopsis_hash == same[1].synopsis_hash
    assert changed.synopsis_hash != same[0].synopsis_hash


@pytest.mark.parametrize(
    "synopsis",
    [
        None,
        "",
        "   ",
        "[Written by MAL Rewrite]",
        "No synopsis information has been added to this title.",
        "NO SYNOPSIS HAS BEEN ADDED FOR THIS TITLE.",
    ],
)
def test_missing_or_placeholder_synopses_become_none_with_no_hash(synopsis):
    anime = clean_full(full(synopsis=synopsis))

    assert anime.synopsis is None
    assert anime.synopsis_hash is None


def test_the_year_falls_back_to_the_year_airing_started():
    assert clean_full(full(year=None)).year == 1998


def test_the_year_is_none_when_nothing_gives_one():
    assert clean_full(full(year=None, aired={"from": None, "to": None, "prop": {}})).year is None
    assert clean_full(full(year=None, aired=None)).year is None


def test_a_partial_air_date_keeps_the_year_and_leaves_the_date_empty():
    aired = {"from": None, "to": None, "prop": {"from": {"day": None, "month": None, "year": 2030}}}

    anime = clean_full(full(year=None, aired=aired))

    assert (anime.year, anime.aired_from, anime.aired_to) == (2030, None, None)


def test_bad_dates_become_none():
    anime = clean_full(full(aired={"from": "not a date", "to": 5}))

    assert anime.aired_from is None
    assert anime.aired_to is None


@pytest.mark.parametrize(
    ("score", "expected"),
    [(8.75, Decimal("8.75")), (7, Decimal("7")), (None, None), ("x", None), (True, None)],
)
def test_the_score_is_a_decimal(score, expected):
    assert clean_full(full(score=score)).mal_score == expected


def test_the_image_prefers_the_large_jpg_then_falls_back():
    assert clean_full(full()).image_url == "https://x/al.jpg"
    assert (
        clean_full(full(images={"jpg": {"image_url": "https://x/a.jpg"}})).image_url
        == "https://x/a.jpg"
    )
    assert (
        clean_full(full(images={"webp": {"large_image_url": "https://x/w"}})).image_url
        == "https://x/w"
    )
    assert clean_full(full(images=None)).image_url is None


def test_every_anime_in_the_probe_sample_parses():
    """Runs against the real responses saved by the Tenrai probe, when they are present."""
    paths = glob.glob("analysis/tenrai_probe/probe_results_*.jsonl")
    if not paths:
        pytest.skip("probe samples are not present")
    parsed = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                data = json.loads(line).get("anime_data")
                if not data:
                    continue
                anime = clean_full({"data": data})
                assert anime is not None
                assert anime.mal_id == data["mal_id"]
                assert anime.title
                parsed += 1
    assert parsed > 0


def test_html_entities_in_the_text_fields_are_decoded_before_hashing():
    anime = clean_full(
        full(
            title="Fate &amp; Zero",
            title_english="Ed&rsquo;s Show",
            title_japanese="&quot;x&quot;",
            synopsis="He says &quot;go&quot; &ndash; and leaves.\n\n[Written by MAL Rewrite]",
            background="Caf&eacute; scenes.",
        )
    )

    assert anime.title == "Fate & Zero"
    assert anime.title_english == "Ed\u2019s Show"
    assert anime.title_japanese == '"x"'
    assert anime.synopsis == 'He says "go" \u2013 and leaves.'
    assert anime.synopsis_hash == hashlib.sha256(anime.synopsis.encode()).hexdigest()
    assert anime.background == "Caf\u00e9 scenes."


def test_entities_are_decoded_once_only_and_urls_and_labels_are_left_alone():
    anime = clean_full(
        full(
            synopsis="a &amp;lt; b",
            images={"jpg": {"large_image_url": "https://x/a.jpg?a=1&region=eu"}},
            rating="R &amp; more",
        )
    )

    assert anime.synopsis == "a &lt; b"  # not "a < b"
    assert anime.image_url == "https://x/a.jpg?a=1&region=eu"  # a URL is never decoded
    assert anime.rating == "R &amp; more"  # a MAL label is stored as given


def test_a_synopsis_that_is_only_an_entity_or_a_placeholder_with_entities_is_none():
    assert clean_full(full(synopsis="&nbsp;")).synopsis is None
    assert (
        clean_full(full(synopsis="No synopsis information has been added to this title.")).synopsis
        is None
    )
