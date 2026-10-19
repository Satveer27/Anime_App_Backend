import glob
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.ingestion.parsers.episodes import clean_episodes


def episode(**overrides) -> dict:
    """One episode as Tenrai returns it."""
    item = {
        "mal_id": 1,
        "url": "https://myanimelist.net/anime/1/Cowboy_Bebop/episode/1",
        "title": "Asteroid Blues",
        "title_japanese": "アステロイド・ブルース",
        "title_romanji": "Asteroid Blues",
        "duration": 1440,
        "aired": "1998-10-24T00:00:00+00:00",
        "score": 4.32,
        "filler": False,
        "recap": False,
        "synopsis": "In a flashback, Spike is shown waiting near a church.\n\n(Source: Wikipedia)",
        "replies": 244,
        "forum_url": "https://myanimelist.net/forum/?topicid=29264",
        "images": {"jpg": {"image_url": "https://x/e.jpg"}},
    }
    item.update(overrides)
    return item


def pages(*lists: list[dict]) -> list[dict]:
    """The stored shape: a list of pages."""
    return [
        {"data": items, "pagination": {"has_next_page": i < len(lists) - 1}}
        for i, items in enumerate(lists)
    ]


def test_an_episode_maps_to_every_column():
    (ep,) = clean_episodes(pages([episode()]), anime_id=1)

    assert (ep.anime_id, ep.episode_number) == (1, 1)
    assert (ep.title, ep.title_romanji) == ("Asteroid Blues", "Asteroid Blues")
    assert ep.title_japanese == "アステロイド・ブルース"
    assert ep.aired == datetime(1998, 10, 24, tzinfo=UTC)
    assert ep.duration_seconds == 1440
    assert ep.synopsis.endswith("(Source: Wikipedia)")
    assert ep.synopsis_hash == hashlib.sha256(ep.synopsis.encode()).hexdigest()
    assert (ep.filler, ep.recap) == (False, False)
    assert ep.mal_score == Decimal("8.64")  # MAL's 4.32 out of 5, doubled


def test_the_anime_id_comes_from_the_caller():
    eps = clean_episodes(pages([episode(mal_id=1), episode(mal_id=2)]), anime_id=777)

    assert {e.anime_id for e in eps} == {777}


def test_episodes_from_every_page_are_returned_in_order():
    payload = pages([episode(mal_id=1), episode(mal_id=2)], [episode(mal_id=101)])

    eps = clean_episodes(payload, anime_id=1)

    assert [e.episode_number for e in eps] == [1, 2, 101]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, None),
        (0.0, None),
        (None, None),
        ("x", None),
        (True, None),
        (5, Decimal("10")),
        (4.32, Decimal("8.64")),
        (1, Decimal("2")),
    ],
)
def test_the_score_is_doubled_and_zero_means_no_score(score, expected):
    (ep,) = clean_episodes(pages([episode(score=score)]), anime_id=1)

    assert ep.mal_score == expected


def test_a_missing_field_stays_none_but_the_flags_default_to_false():
    (ep,) = clean_episodes(pages([{"mal_id": 3}]), anime_id=1)

    for column in (
        "title",
        "title_japanese",
        "title_romanji",
        "aired",
        "duration_seconds",
        "synopsis",
        "synopsis_hash",
        "mal_score",
    ):
        assert getattr(ep, column) is None, column
    assert (ep.filler, ep.recap) == (False, False)  # NOT NULL columns


def test_flags_are_read_as_booleans():
    (ep,) = clean_episodes(pages([episode(filler=True, recap=True)]), anime_id=1)

    assert (ep.filler, ep.recap) == (True, True)


def test_episode_zero_is_kept():
    eps = clean_episodes(pages([episode(mal_id=0), episode(mal_id=1)]), anime_id=1)

    assert [e.episode_number for e in eps] == [0, 1]


def test_gaps_in_the_numbering_are_kept_as_they_are():
    eps = clean_episodes(pages([episode(mal_id=44), episode(mal_id=46)]), anime_id=1)

    assert [e.episode_number for e in eps] == [44, 46]


def test_an_episode_without_a_number_is_skipped():
    payload = pages([episode(mal_id=None), {"title": "no id"}, "text", None, episode(mal_id=2)])

    eps = clean_episodes(payload, anime_id=1)

    assert [e.episode_number for e in eps] == [2]


def test_a_repeated_number_keeps_the_last_row():
    payload = pages([episode(mal_id=1, title="first")], [episode(mal_id=1, title="last")])

    (ep,) = clean_episodes(payload, anime_id=1)

    assert ep.title == "last"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "text",
        5,
        [],
        [None],
        ["x"],
        [{"data": None}],
        [{"data": "x"}],
        [{"pagination": {}}],
    ],
)
def test_unusable_payloads_give_no_episodes(payload):
    assert clean_episodes(payload, anime_id=1) == []


def test_empty_strings_become_none_and_a_blank_synopsis_has_no_hash():
    (ep,) = clean_episodes(
        pages([episode(title_romanji="", title="  ", synopsis="   ", duration=0)]), anime_id=1
    )

    assert (ep.title, ep.title_romanji) == (None, None)
    assert (ep.synopsis, ep.synopsis_hash) == (None, None)
    assert ep.duration_seconds is None


def test_other_pipelines_columns_are_never_set():
    (ep,) = clean_episodes(pages([episode()]), anime_id=1)

    for column in ("anilist_score", "imdb_score"):
        assert column not in ep.__dict__, column


def test_every_episode_in_the_probe_sample_parses():
    """Runs against the real responses saved by the Tenrai probe, when they are present."""
    paths = glob.glob("analysis/tenrai_probe/probe_results_*.jsonl")
    if not paths:
        pytest.skip("probe samples are not present")
    total_in_sample = total_parsed = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                items = record.get("anime_episodes") or []
                parsed = clean_episodes([{"data": items}], anime_id=record["anime_id"])
                total_in_sample += len(items)
                total_parsed += len(parsed)
                assert all(isinstance(e.episode_number, int) for e in parsed)
                assert all(e.mal_score is None or 0 < e.mal_score <= 10 for e in parsed)
                assert all("&quot;" not in (e.synopsis or "") for e in parsed)
    assert total_in_sample > 0
    assert total_parsed == total_in_sample  # no episode lost, none repeated in the sample


def test_html_entities_in_the_text_fields_are_decoded_before_hashing():
    item = episode(
        title="Spike &amp; Jet",
        title_romanji="Ed&rsquo;s turn",
        synopsis="He says &quot;go&quot; &ndash; and leaves.",
    )

    (ep,) = clean_episodes(pages([item]), anime_id=1)

    assert ep.title == "Spike & Jet"
    assert ep.title_romanji == "Ed\u2019s turn"
    assert ep.synopsis == 'He says "go" \u2013 and leaves.'
    assert ep.synopsis_hash == hashlib.sha256(ep.synopsis.encode()).hexdigest()


def test_entities_are_decoded_once_only():
    (ep,) = clean_episodes(pages([episode(synopsis="a &amp;lt; b")]), anime_id=1)

    assert ep.synopsis == "a &lt; b"  # not "a < b"
