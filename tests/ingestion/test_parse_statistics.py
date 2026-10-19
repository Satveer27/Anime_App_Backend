import glob
import json

import pytest

from app.ingestion.parsers.statistics import clean_statistics


def stats_payload(**overrides) -> dict:
    data = {
        "watching": 192285,
        "completed": 1215374,
        "on_hold": 118148,
        "dropped": 50487,
        "plan_to_watch": 515397,
        "total": 2091691,
        "scores": [
            {"score": 1, "votes": 2493, "percentage": 0.2},
            {"score": 2, "votes": 1229, "percentage": 0.1},
            {"score": 3, "votes": 2206, "percentage": 0.2},
            {"score": 4, "votes": 5577, "percentage": 0.5},
            {"score": 5, "votes": 15129, "percentage": 1.4},
            {"score": 6, "votes": 36528, "percentage": 3.4},
            {"score": 7, "votes": 108946, "percentage": 10.1},
            {"score": 8, "votes": 229334, "percentage": 21.3},
            {"score": 9, "votes": 304195, "percentage": 28.2},
            {"score": 10, "votes": 372985, "percentage": 34.6},
        ],
    }
    data.update(overrides)
    return {"data": data}


def test_statistics_map_to_every_column():
    row = clean_statistics(stats_payload(), anime_id=1)

    assert row.anime_id == 1
    assert (row.watching, row.completed, row.on_hold) == (192285, 1215374, 118148)
    assert (row.dropped, row.plan_to_watch, row.total) == (50487, 515397, 2091691)
    assert (row.votes_1, row.votes_2, row.votes_7, row.votes_10) == (2493, 1229, 108946, 372985)
    assert [getattr(row, f"votes_{n}") for n in range(1, 11)] == [
        2493, 1229, 2206, 5577, 15129, 36528, 108946, 229334, 304195, 372985,
    ]  # fmt: skip


def test_the_anime_id_comes_from_the_caller():
    assert clean_statistics(stats_payload(), anime_id=777).anime_id == 777


@pytest.mark.parametrize("scores", [None, [], "x", 5, {}])
def test_a_missing_score_distribution_leaves_every_vote_none(scores):
    row = clean_statistics(stats_payload(scores=scores), anime_id=1)

    assert row is not None
    assert all(getattr(row, f"votes_{n}") is None for n in range(1, 11))


def test_a_missing_score_entry_stays_none_and_the_others_are_kept():
    scores = [{"score": 8, "votes": 5}, {"score": 9}, {"score": 10, "votes": None}]

    row = clean_statistics(stats_payload(scores=scores), anime_id=1)

    assert (row.votes_8, row.votes_9, row.votes_10) == (5, None, None)
    assert row.votes_1 is None


def test_odd_score_entries_are_ignored():
    scores = [
        None,
        "x",
        {"score": 0, "votes": 9},
        {"score": 11, "votes": 9},
        {"score": "a"},
        {"score": 5, "votes": 3},
    ]

    row = clean_statistics(stats_payload(scores=scores), anime_id=1)

    assert row.votes_5 == 3
    assert sum(getattr(row, f"votes_{n}") is not None for n in range(1, 11)) == 1


@pytest.mark.parametrize(
    "missing", ["watching", "completed", "on_hold", "dropped", "plan_to_watch", "total"]
)
def test_a_missing_count_makes_the_response_unusable(missing):
    assert clean_statistics(stats_payload(**{missing: None}), anime_id=1) is None


@pytest.mark.parametrize(
    "payload", [None, [], "text", {}, {"data": None}, {"data": []}, {"data": "x"}]
)
def test_unusable_payloads_return_none(payload):
    assert clean_statistics(payload, anime_id=1) is None


def test_zero_counts_are_real_values_not_missing():
    row = clean_statistics(stats_payload(watching=0, total=0), anime_id=1)

    assert (row.watching, row.total) == (0, 0)


def test_every_statistics_response_in_the_probe_sample_parses():
    paths = glob.glob("analysis/tenrai_probe/probe_results_*.jsonl")
    if not paths:
        pytest.skip("probe samples are not present")
    parsed = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                row = clean_statistics({"data": record["anime_statistics"]}, record["anime_id"])
                assert row is not None
                assert all(getattr(row, f"votes_{n}") is not None for n in range(1, 11))
                parsed += 1
    assert parsed > 0
