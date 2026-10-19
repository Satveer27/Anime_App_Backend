import glob
import json

import pytest

from app.ingestion.parsers.links import extract_studios, extract_tags


def tag(mal_id, name) -> dict:
    return {"mal_id": mal_id, "type": "anime", "name": name, "url": f"https://x/genre/{mal_id}"}


def full(**lists) -> dict:
    data = {
        "genres": [tag(1, "Action"), tag(24, "Sci-Fi")],
        "themes": [tag(50, "Adult Cast")],
        "demographics": [],
        "studios": [tag(14, "Sunrise")],
        "producers": [tag(23, "Bandai Visual")],
        "licensors": [tag(102, "Funimation")],
    }
    data.update(lists)
    return {"data": data}


def test_tags_come_from_genres_themes_and_demographics_in_order():
    payload = full(demographics=[tag(27, "Shounen")])

    assert extract_tags(payload) == [
        (1, "Action"),
        (24, "Sci-Fi"),
        (50, "Adult Cast"),
        (27, "Shounen"),
    ]


def test_producers_licensors_and_explicit_genres_are_not_tags():
    payload = full(explicit_genres=[tag(9, "Ecchi")])

    ids = [mal_id for mal_id, _ in extract_tags(payload)]

    assert 9 not in ids
    assert 23 not in ids
    assert 102 not in ids


def test_studios_come_from_the_studios_list_only():
    assert extract_studios(full()) == [(14, "Sunrise")]


def test_an_anime_with_no_studio_has_an_empty_list():
    assert extract_studios(full(studios=[])) == []


def test_an_item_without_an_id_or_a_name_is_skipped():
    payload = full(
        genres=[
            tag(None, "No id"),
            {"mal_id": 2},
            {"mal_id": 3, "name": "  "},
            "x",
            None,
            tag(4, "Ok"),
        ]
    )

    assert [t for t in extract_tags(payload) if t[0] in (2, 3, 4)] == [(4, "Ok")]


def test_an_id_that_appears_in_two_lists_is_kept_once():
    payload = full(genres=[tag(1, "Action")], themes=[tag(1, "Action again")])

    assert extract_tags(payload).count((1, "Action")) == 1
    assert [name for mal_id, name in extract_tags(payload) if mal_id == 1] == ["Action"]


def test_names_are_stripped_and_left_as_given():
    payload = full(genres=[tag(1, "  Slice of Life  "), tag(2, "R &amp; B")])

    assert extract_tags(payload)[:2] == [(1, "Slice of Life"), (2, "R &amp; B")]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {},
        {"data": None},
        {"data": []},
        {"data": {}},
        {"data": {"genres": None}},
        {"data": {"genres": "x"}},
    ],
)
def test_unusable_payloads_give_nothing(payload):
    assert extract_tags(payload) == []
    assert extract_studios(payload) == []


def test_every_anime_in_the_probe_sample_has_tags_and_consistent_names():
    paths = glob.glob("analysis/tenrai_probe/probe_results_*.jsonl")
    if not paths:
        pytest.skip("probe samples are not present")
    tag_names: dict[int, set[str]] = {}
    studio_names: dict[int, set[str]] = {}  # a separate id space from the tags
    anime = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                payload = {"data": json.loads(line)["anime_data"]}
                tags = extract_tags(payload)
                assert tags, payload["data"]["mal_id"]
                assert len({mal_id for mal_id, _ in tags}) == len(tags)
                for mal_id, name in tags:
                    tag_names.setdefault(mal_id, set()).add(name)
                for mal_id, name in extract_studios(payload):
                    studio_names.setdefault(mal_id, set()).add(name)
                anime += 1
    assert anime > 0
    assert all(len(found) == 1 for found in tag_names.values())  # an id never has two names
    assert all(len(found) == 1 for found in studio_names.values())
