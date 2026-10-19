import glob
import json
from datetime import UTC, datetime

import pytest

from app.ingestion.parsers.reviews import clean_reviews


def review(**overrides) -> dict:
    item = {
        "mal_id": 7406,
        "url": "https://myanimelist.net/reviews.php?id=7406",
        "type": "anime",
        "reactions": {
            "overall": 2283,
            "nice": 2225,
            "love_it": 25,
            "funny": 7,
            "confusing": 2,
            "informative": 7,
            "well_written": 15,
            "creative": 2,
        },
        "date": "2008-08-24T12:46:00+00:00",
        "review": "Cowboy Bebop is that exception.",
        "score": 10,
        "tags": ["Recommended"],
        "is_spoiler": False,
        "is_preliminary": False,
        "episodes_watched": None,
        "user": {
            "username": "TheLlama",
            "url": "https://myanimelist.net/profile/TheLlama",
            "images": {"jpg": {"image_url": "https://x/u.jpg"}},
        },
    }
    item.update(overrides)
    return item


def payload(*items: dict) -> dict:
    return {"data": list(items), "pagination": {"has_next_page": True}}


def test_a_review_maps_to_every_column():
    (r,) = clean_reviews(payload(review()), anime_id=1)

    assert (r.mal_id, r.anime_id, r.score) == (7406, 1, 10)
    assert r.review == "Cowboy Bebop is that exception."
    assert r.review_url == "https://myanimelist.net/reviews.php?id=7406"
    assert (r.username, r.username_url) == ("TheLlama", "https://myanimelist.net/profile/TheLlama")
    assert r.date == datetime(2008, 8, 24, 12, 46, tzinfo=UTC)
    assert (r.is_spoiler, r.is_preliminary, r.episodes_watched) == (False, False, None)
    assert (r.reaction_overall, r.reaction_nice, r.reaction_love_it) == (2283, 2225, 25)
    assert (r.reaction_funny, r.reaction_confusing, r.reaction_informative) == (7, 2, 7)
    assert (r.reaction_well_written, r.reaction_creative) == (15, 2)


def test_the_anime_id_comes_from_the_caller():
    reviews = clean_reviews(payload(review(mal_id=1), review(mal_id=2)), anime_id=777)

    assert {r.anime_id for r in reviews} == {777}


def test_html_entities_in_the_text_are_decoded():
    text = "Ed&rsquo;s a girl &ndash; &quot;perfect&quot; &amp; more"

    (r,) = clean_reviews(payload(review(review=text)), anime_id=1)

    assert r.review == 'Ed’s a girl – "perfect" & more'


def test_the_flags_and_episodes_watched_are_read():
    item = review(is_spoiler=True, is_preliminary=True, episodes_watched=12)

    (r,) = clean_reviews(payload(item), anime_id=1)

    assert (r.is_spoiler, r.is_preliminary, r.episodes_watched) == (True, True, 12)


def test_missing_flags_default_to_false_for_the_not_null_columns():
    item = review()
    del item["is_spoiler"], item["is_preliminary"]

    (r,) = clean_reviews(payload(item), anime_id=1)

    assert (r.is_spoiler, r.is_preliminary) == (False, False)


def test_a_missing_user_or_reactions_block_leaves_those_columns_none():
    (r,) = clean_reviews(
        payload(review(user=None, reactions=None, score=None, date=None)), anime_id=1
    )

    assert (r.username, r.username_url, r.score, r.date) == (None, None, None, None)
    for name in (
        "overall",
        "nice",
        "love_it",
        "funny",
        "confusing",
        "informative",
        "well_written",
        "creative",
    ):
        assert getattr(r, f"reaction_{name}") is None, name


def test_a_review_without_an_id_or_text_is_skipped():
    items = [
        review(mal_id=None),
        review(mal_id=2, review=None),
        review(mal_id=3, review="   "),
        review(mal_id=4, review="&nbsp;"),
        "text",
        None,
        review(mal_id=5),
    ]

    reviews = clean_reviews(payload(*items), anime_id=1)

    assert [r.mal_id for r in reviews] == [5]


def test_a_repeated_review_id_keeps_the_last_row():
    reviews = clean_reviews(
        payload(review(mal_id=1, review="first"), review(mal_id=1, review="last")), anime_id=1
    )

    assert [r.review for r in reviews] == ["last"]


def test_tags_and_type_are_not_stored():
    (r,) = clean_reviews(payload(review()), anime_id=1)

    assert not hasattr(r, "tags")
    assert not hasattr(r, "type")


@pytest.mark.parametrize(
    "data", [None, {}, "text", 5, [], [None], {"data": None}, {"data": "x"}, {"pagination": {}}]
)
def test_unusable_payloads_give_no_reviews(data):
    assert clean_reviews(data, anime_id=1) == []


def test_every_review_in_the_probe_sample_parses():
    paths = glob.glob("analysis/tenrai_probe/probe_results_*.jsonl")
    if not paths:
        pytest.skip("probe samples are not present")
    in_sample = parsed = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                items = record.get("anime_reviews") or []
                reviews = clean_reviews({"data": items}, record["anime_id"])
                in_sample += len(items)
                parsed += len(reviews)
                assert all(r.review and "&rsquo;" not in r.review for r in reviews)
    assert in_sample > 0
    assert parsed == in_sample  # every sample review has an id and text, none repeated per anime


def test_entities_in_a_review_are_decoded_once_only():
    (r,) = clean_reviews(payload(review(review="a &amp;lt; b")), anime_id=1)

    assert r.review == "a &lt; b"  # not "a < b"


def test_urls_and_usernames_are_never_entity_decoded():
    item = review(
        url="https://myanimelist.net/reviews.php?id=1&region=eu",
        user={"username": "a&copy;b", "url": "https://x/profile?a=1&reg=2"},
    )

    (r,) = clean_reviews(payload(item), anime_id=1)

    assert r.review_url == "https://myanimelist.net/reviews.php?id=1&region=eu"
    assert (r.username, r.username_url) == ("a&copy;b", "https://x/profile?a=1&reg=2")
