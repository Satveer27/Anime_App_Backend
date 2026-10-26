import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec

import pytest
from raw_store_fake import InMemoryRawStore
from sqlalchemy import func, select
from structlog.testing import capture_logs

from app.catalogue.models import (
    Anime,
    AnimeRelation,
    AnimeStatisticsMal,
    AnimeStudio,
    AnimeTag,
    Episode,
    Review,
    Studio,
    Tag,
)
from app.catalogue.repository import CatalogueRepository
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.exceptions import RawStoreError
from app.ingestion.jobs import clean_raw_job
from app.ingestion.jobs.clean_raw_job import (
    COUNT_KEYS,
    TIMING_KEYS,
    BatchResult,
    CleanAnimeJob,
)
from app.ingestion.models import RawTenrai
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository

pytestmark = pytest.mark.postgres


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def details(mal_id: int, **fields) -> dict:
    return {"data": {"mal_id": mal_id, "title": f"Title {mal_id}", **fields}}


def statistics(total: int = 10) -> dict:
    return {
        "data": {
            "watching": 1, "completed": 2, "on_hold": 3, "dropped": 4, "plan_to_watch": 5,
            "total": total, "scores": [{"score": 10, "votes": 7}],
        }
    }  # fmt: skip


def episodes(*numbers: int) -> list[dict]:
    return [{"data": [{"mal_id": n, "title": f"ep {n}"} for n in numbers]}]


def reviews(anime_id: int, count: int = 2, text: str = "great") -> dict:
    return {"data": [{"mal_id": anime_id * 100 + n, "review": text} for n in range(count)]}


# The responses live in S3 in production; here one in-memory store serves every test, emptied
# before each one. The index rows go to Postgres, as in production.
STORE = InMemoryRawStore()


@pytest.fixture(autouse=True)
def empty_store():
    STORE.objects.clear()


def put_payload(mal_id: int, resource: Resource, payload) -> None:
    STORE.objects[(mal_id, Resource(resource))] = payload


async def seed(session, rows):
    """rows: (mal_id, resource, payload) or (mal_id, resource, payload, fetched_day)"""
    for row in rows:
        mal_id, resource, payload, *day = row
        put_payload(mal_id, resource, payload)
        session.add(
            RawTenrai(mal_id=mal_id, resource=resource, size_bytes=1, fetched_at=at(*day or [1]))
        )
    await session.commit()


def make_job(session) -> CleanAnimeJob:
    return CleanAnimeJob(
        CatalogueRepository(session),
        RawTenraiRepository(session),
        MetricsRepository(session),
        STORE,
    )


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


def two_complete_anime():
    rows = []
    for anime_id in (1, 2):
        rows += [
            (anime_id, Resource.DETAILS, details(anime_id)),
            (anime_id, Resource.EPISODES, episodes(1, 2, 3)),
            (anime_id, Resource.REVIEWS, reviews(anime_id)),
            (anime_id, Resource.STATISTICS, statistics(anime_id * 100)),
        ]
    return rows


async def test_a_batch_fills_every_catalogue_table_and_returns_the_cursor(pg_session):
    await seed(pg_session, two_complete_anime())

    result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result.next_after == (2, Resource.STATISTICS)  # the last raw row of the batch
    assert result.counts["rows_read"] == 8
    assert result.counts["anime_changed"] == 2
    assert result.counts["statistics_written"] == 2
    assert result.counts["episodes_written"] == 6
    assert result.counts["reviews_written"] == 4
    assert result.counts["parse_failed"] == result.counts["skipped_no_anime"] == 0
    assert await count(pg_session, Anime) == 2
    assert await count(pg_session, Episode) == 6
    assert await count(pg_session, Review) == 4
    total = (
        await pg_session.execute(
            select(AnimeStatisticsMal.total).where(AnimeStatisticsMal.anime_id == 2)
        )
    ).scalar_one()
    assert total == 200


async def test_an_empty_batch_returns_no_cursor_and_zero_counts(pg_session):
    result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result == BatchResult(
        None, dict.fromkeys(COUNT_KEYS, 0) | dict.fromkeys(TIMING_KEYS, 0.0)
    )


async def test_running_the_same_rows_again_creates_no_duplicates_and_changes_no_anime(pg_session):
    await seed(pg_session, two_complete_anime())
    job = make_job(pg_session)
    await job._clean_anime(None, None, {}, {})

    second = await job._clean_anime(None, None, {}, {})

    assert second.counts["anime_changed"] == 0  # nothing differs, so nothing was written
    assert await count(pg_session, Anime) == 2
    assert await count(pg_session, Episode) == 6
    assert await count(pg_session, Review) == 4
    assert await count(pg_session, AnimeStatisticsMal) == 2


async def test_rows_of_an_anime_with_no_row_are_skipped_not_crashed_on(pg_session):
    await seed(
        pg_session,
        [
            (1, Resource.DETAILS, details(1)),
            (1, Resource.EPISODES, episodes(1)),
            (9, Resource.STATISTICS, statistics()),  # details never fetched or failed
            (9, Resource.EPISODES, episodes(1, 2)),
            (9, Resource.REVIEWS, reviews(9)),
        ],
    )

    with capture_logs() as logs:
        result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result.counts["skipped_no_anime"] == 3
    assert result.counts["episodes_written"] == 1
    assert await count(pg_session, Anime) == 1
    assert await count(pg_session, Episode) == 1
    assert await count(pg_session, AnimeStatisticsMal) == 0
    (warning,) = [log for log in logs if log["event"] == "clean_skipped_no_anime"]
    assert (warning["anime"], warning["sample"]) == (1, [9])


async def test_an_anime_already_in_the_catalogue_still_gets_its_other_rows_when_only_they_are_new(
    pg_session,
):
    await seed(pg_session, [(1, Resource.DETAILS, details(1), 1)])
    await make_job(pg_session)._clean_anime(None, None, {}, {})
    # a weekly run: statistics were re-fetched later, the details were not
    await seed(pg_session, [(1, Resource.STATISTICS, statistics(77), 20)])

    result = await make_job(pg_session)._clean_anime(None, at(10), {}, {})

    assert result.counts["rows_read"] == 1
    assert result.counts["statistics_written"] == 1
    assert result.counts["skipped_no_anime"] == 0


async def test_an_unparsable_response_is_logged_and_counted_and_the_batch_carries_on(pg_session):
    await seed(
        pg_session,
        [
            (1, Resource.DETAILS, {"data": {"mal_id": 1}}),  # no title
            (2, Resource.DETAILS, details(2)),
            (2, Resource.STATISTICS, {"data": {"watching": 1}}),  # counts missing
            (2, Resource.EPISODES, episodes(1)),
        ],
    )

    with capture_logs() as logs:
        result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result.counts["parse_failed"] == 2
    assert result.counts["anime_changed"] == 1
    assert result.counts["episodes_written"] == 1
    failures = [log for log in logs if log["event"] == "clean_parse_failed"]
    assert {(f["mal_id"], f["resource"]) for f in failures} == {(1, "details"), (2, "statistics")}
    assert all("payload" in f for f in failures)
    assert await count(pg_session, Anime) == 1


async def test_a_resource_with_no_parser_is_skipped_with_a_warning(pg_session):
    await seed(
        pg_session,
        [(1, Resource.CHARACTERS, {"data": []}), (1, Resource.DETAILS, details(1))],
    )

    with capture_logs() as logs:
        result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result.counts["skipped_unsupported"] == 1
    assert any(log["event"] == "clean_resource_unsupported" for log in logs)


async def test_only_rows_fetched_since_the_cut_off_are_cleaned(pg_session):
    await seed(
        pg_session,
        [
            (1, Resource.DETAILS, details(1), 1),  # old
            (2, Resource.DETAILS, details(2), 20),  # new
        ],
    )

    result = await make_job(pg_session)._clean_anime(None, at(10), {}, {})

    assert result.counts["rows_read"] == 1
    ids = (await pg_session.execute(select(Anime.mal_id))).scalars().all()
    assert ids == [2]


async def test_paging_with_small_batches_cleans_everything_even_when_an_anime_is_split(
    pg_session,
):
    await seed(pg_session, two_complete_anime())
    job = make_job(pg_session)

    after, batches = None, 0
    totals = dict.fromkeys(COUNT_KEYS, 0) | dict.fromkeys(TIMING_KEYS, 0.0)
    while True:
        result = await job._clean_anime(after, None, {}, {}, batch_size=3)  # splits every anime
        if result.next_after is None:
            break
        after = result.next_after
        batches += 1
        for key, value in result.counts.items():
            totals[key] += value

    assert batches == 3  # 8 rows, 3 at a time
    assert totals["skipped_no_anime"] == 0  # details always reached the database before the rest
    assert (totals["anime_changed"], totals["episodes_written"]) == (2, 6)
    assert (totals["reviews_written"], totals["statistics_written"]) == (4, 2)
    assert await count(pg_session, Anime) == 2
    assert await count(pg_session, Episode) == 6


async def test_the_text_is_cleaned_on_the_way_in(pg_session):
    await seed(
        pg_session,
        [
            (1, Resource.DETAILS, details(1, title="Fate &amp; Zero", synopsis="Story.\n\n[Written by MAL Rewrite]")),
            (1, Resource.REVIEWS, reviews(1, count=1, text="Ed&rsquo;s a girl")),
        ],
    )  # fmt: skip

    await make_job(pg_session)._clean_anime(None, None, {}, {})

    anime = (await pg_session.execute(select(Anime))).scalar_one()
    assert (anime.title, anime.synopsis) == ("Fate & Zero", "Story.")
    assert anime.synopsis_hash is not None
    review = (await pg_session.execute(select(Review))).scalar_one()
    assert review.review == "Ed’s a girl"


async def test_each_batch_is_logged_with_its_counts(pg_session):
    await seed(pg_session, two_complete_anime())

    with capture_logs() as logs:
        await make_job(pg_session)._clean_anime(None, None, {}, {})

    (done,) = [log for log in logs if log["event"] == "clean_batch_done"]
    assert done["rows_read"] == 8
    assert done["anime_changed"] == 2
    assert done["last_mal_id"] == 2


async def test_a_database_failure_rolls_the_batch_back_and_is_reraised(mocker):
    catalogue = create_autospec(CatalogueRepository, instance=True)
    catalogue.upsert_anime.return_value = 0
    catalogue.existing_anime_ids.return_value = {1}
    catalogue.upsert_statistics.return_value = 0
    catalogue.upsert_episodes.side_effect = RuntimeError("db exploded")
    raw = create_autospec(RawTenraiRepository, instance=True)
    raw.get_batch.return_value = [
        RawTenrai(mal_id=1, resource=Resource.EPISODES, size_bytes=1, fetched_at=at(1))
    ]
    put_payload(1, Resource.EPISODES, episodes(1))
    job = CleanAnimeJob(catalogue, raw, create_autospec(MetricsRepository, instance=True), STORE)

    with pytest.raises(RuntimeError, match="db exploded"):
        await job._clean_anime(None, None, {}, {})

    catalogue.rollback.assert_awaited_once()
    catalogue.commit.assert_not_awaited()


# --- the loop -------------------------------------------------------------------------------


async def test_the_loop_cleans_everything_and_returns_the_totals(pg_session):
    await seed(pg_session, two_complete_anime())

    summary = await make_job(pg_session).full_clean_loop(batch_size=3)

    assert summary["batches"] == 3  # 8 rows, 3 at a time
    assert summary["rows_read"] == 8
    assert (summary["anime_changed"], summary["episodes_written"]) == (2, 6)
    assert (summary["reviews_written"], summary["statistics_written"]) == (4, 2)
    assert summary["skipped_no_anime"] == summary["parse_failed"] == 0
    assert summary["elapsed_seconds"] >= 0
    assert await count(pg_session, Anime) == 2


async def test_an_index_row_whose_object_is_missing_stops_the_run_and_is_recorded(pg_session):
    await seed(pg_session, [(1, Resource.DETAILS, details(1))])
    STORE.objects.clear()  # the index row exists but the object it points at does not

    with pytest.raises(RawStoreError):
        await make_job(pg_session).full_clean_loop()

    run = await MetricsRepository(pg_session).latest_run("clean_anime")
    assert run.status.value == "failed"
    assert run.error.startswith("RawStoreError")
    assert await count(pg_session, Anime) == 0


async def test_the_payloads_of_a_batch_are_read_concurrently_but_no_more_than_the_limit(
    pg_session,
):
    await seed(pg_session, [(n, Resource.DETAILS, details(n)) for n in range(1, 41)])
    running = peak = 0
    real_get = STORE.get

    async def slow_get(mal_id, resource):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return await real_get(mal_id, resource)

    STORE.get = slow_get
    try:
        summary = await make_job(pg_session).full_clean_loop()
    finally:
        del STORE.get  # back to the class method

    assert summary["anime_changed"] == 40
    assert 1 < peak <= clean_raw_job.RAW_STORE_CONCURRENCY


async def test_the_loop_on_an_empty_table_does_nothing(pg_session):
    summary = await make_job(pg_session).full_clean_loop()

    assert summary["batches"] == 0
    assert summary["rows_read"] == 0


async def test_the_loop_honours_the_cut_off(pg_session):
    await seed(
        pg_session,
        [(1, Resource.DETAILS, details(1), 1), (2, Resource.DETAILS, details(2), 20)],
    )

    summary = await make_job(pg_session).full_clean_loop(since=at(10))

    assert summary["rows_read"] == 1
    assert (await pg_session.execute(select(Anime.mal_id))).scalars().all() == [2]


async def test_the_loop_logs_its_start_progress_and_finish(pg_session, monkeypatch):
    monkeypatch.setattr(clean_raw_job, "PROGRESS_EVERY_BATCHES", 2)
    await seed(pg_session, two_complete_anime())

    with capture_logs() as logs:
        await make_job(pg_session).full_clean_loop(since=at(1), batch_size=3)

    events = [log["event"] for log in logs]
    assert events.count("clean_run_started") == 1
    assert events.count("clean_run_progress") == 1  # after batch 2 of 3
    assert events.count("clean_run_finished") == 1
    started = next(log for log in logs if log["event"] == "clean_run_started")
    assert (started["since"], started["after"], started["batch_size"]) == (at(1), None, 3)


class FailsOnItsSecondBatch(CatalogueRepository):
    """A catalogue that loses the database partway through a run."""

    def __init__(self, db):
        super().__init__(db)
        self.batches = 0

    async def upsert_reviews(self, reviews):
        self.batches += 1
        if self.batches == 2:
            raise RuntimeError("database went away")
        return await super().upsert_reviews(reviews)


async def test_a_failed_run_keeps_earlier_batches_and_can_be_resumed(pg_session):
    await seed(pg_session, two_complete_anime())
    flaky = CleanAnimeJob(
        FailsOnItsSecondBatch(pg_session),
        RawTenraiRepository(pg_session),
        MetricsRepository(pg_session),
        STORE,
    )

    with capture_logs() as logs, pytest.raises(RuntimeError, match="database went away"):
        await flaky.full_clean_loop(batch_size=4)  # anime 1 is batch 1, anime 2 is batch 2

    (failed,) = [log for log in logs if log["event"] == "clean_run_failed"]
    assert failed["resume_after"] == [1, "statistics"]  # the last committed batch
    assert failed["batches"] == 1
    assert await count(pg_session, Anime) == 1  # batch 1 was kept, batch 2 rolled back
    assert await count(pg_session, Review) == 2

    mal_id, resource = failed["resume_after"]
    summary = await make_job(pg_session).full_clean_loop(
        after=(mal_id, Resource(resource)), batch_size=4
    )

    assert summary["rows_read"] == 4  # only the rows after the cursor
    assert await count(pg_session, Anime) == 2
    assert await count(pg_session, Review) == 4
    assert await count(pg_session, Episode) == 6


# --- tags and studios -----------------------------------------------------------------------


def named(mal_id: int, name: str) -> dict:
    return {"mal_id": mal_id, "type": "anime", "name": name, "url": f"https://x/{mal_id}"}


ACTION, SCIFI, ADULT, SHOUNEN = (
    named(1, "Action"),
    named(24, "Sci-Fi"),
    named(50, "Adult Cast"),
    named(27, "Shounen"),
)
SUNRISE, BONES = named(14, "Sunrise"), named(4, "Bones")


def tagged(mal_id: int, genres=(), themes=(), demographics=(), studios=()) -> tuple:
    payload = details(
        mal_id,
        genres=list(genres),
        themes=list(themes),
        demographics=list(demographics),
        studios=list(studios),
    )
    return (mal_id, Resource.DETAILS, payload)


async def link_pairs(session, model, column):
    rows = (await session.execute(select(model.anime_id, getattr(model, column)))).all()
    return sorted(tuple(row) for row in rows)


async def test_tags_studios_and_their_links_are_created(pg_session):
    await seed(
        pg_session,
        [
            tagged(1, genres=[ACTION, SCIFI], themes=[ADULT], studios=[SUNRISE]),
            tagged(2, genres=[ACTION], demographics=[SHOUNEN], studios=[SUNRISE, BONES]),
        ],
    )

    summary = await make_job(pg_session).full_clean_loop()

    assert summary["tags_upserted"] == 4  # each tag once, although Action is on both anime
    assert summary["studios_upserted"] == 2
    assert (summary["tag_links_changed"], summary["studio_links_changed"]) == (2, 2)
    names = {t.mal_id: t.name for t in (await pg_session.execute(select(Tag))).scalars().all()}
    assert names == {1: "Action", 24: "Sci-Fi", 50: "Adult Cast", 27: "Shounen"}
    assert await link_pairs(pg_session, AnimeTag, "tag_id") == [
        (1, 1),
        (1, 24),
        (1, 50),
        (2, 1),
        (2, 27),
    ]
    assert await link_pairs(pg_session, AnimeStudio, "studio_id") == [(1, 14), (2, 4), (2, 14)]
    assert await count(pg_session, Studio) == 2


async def test_cleaning_the_same_rows_again_changes_no_tags_studios_or_links(pg_session):
    await seed(pg_session, [tagged(1, genres=[ACTION], studios=[SUNRISE])])
    await make_job(pg_session).full_clean_loop()

    second = await make_job(pg_session).full_clean_loop(full_pass=True)

    assert second["tags_upserted"] == second["studios_upserted"] == 0
    assert second["tag_links_changed"] == second["studio_links_changed"] == 0
    assert await count(pg_session, Tag) == 1
    assert await count(pg_session, AnimeTag) == 1


async def test_a_renamed_tag_is_updated_in_place(pg_session):
    await seed(pg_session, [tagged(1, genres=[named(1, "Action")])])
    await make_job(pg_session).full_clean_loop()
    put_payload(
        1, Resource.DETAILS, tagged(1, genres=[named(1, "Action & Adventure")])[2]
    )  # MAL renamed tag 1

    summary = await make_job(pg_session).full_clean_loop(full_pass=True)

    assert summary["tags_upserted"] == 1
    assert (await pg_session.execute(select(Tag.name))).scalar_one() == "Action & Adventure"
    assert await count(pg_session, Tag) == 1


async def test_a_tag_the_source_dropped_is_unlinked_but_stays_in_the_tag_table(pg_session):
    await seed(pg_session, [tagged(1, genres=[ACTION, SCIFI])])
    await make_job(pg_session).full_clean_loop()
    put_payload(1, Resource.DETAILS, tagged(1, genres=[ACTION])[2])

    summary = await make_job(pg_session).full_clean_loop(full_pass=True)

    assert summary["tag_links_changed"] == 1
    assert await link_pairs(pg_session, AnimeTag, "tag_id") == [(1, 1)]
    assert await count(pg_session, Tag) == 2


async def test_an_anime_with_no_studio_gets_no_studio_links(pg_session):
    await seed(pg_session, [tagged(1, genres=[ACTION], studios=[])])

    summary = await make_job(pg_session).full_clean_loop()

    assert summary["studios_upserted"] == 0
    assert summary["studio_links_changed"] == 0
    assert await count(pg_session, AnimeStudio) == 0


async def test_an_anime_that_failed_to_parse_gets_no_links(pg_session):
    broken = {"data": {"mal_id": 1, "genres": [ACTION]}}  # no title
    await seed(pg_session, [(1, Resource.DETAILS, broken), tagged(2, genres=[ACTION])])

    summary = await make_job(pg_session).full_clean_loop()

    assert summary["parse_failed"] == 1
    assert await link_pairs(pg_session, AnimeTag, "tag_id") == [(2, 1)]


class CountsTheDictLoads(CatalogueRepository):
    def __init__(self, db):
        super().__init__(db)
        self.tag_loads = self.studio_loads = 0

    async def get_tags(self):
        self.tag_loads += 1
        return await super().get_tags()

    async def get_studios(self):
        self.studio_loads += 1
        return await super().get_studios()


async def test_the_tag_and_studio_dicts_are_loaded_once_per_run_not_once_per_batch(pg_session):
    await seed(pg_session, two_complete_anime())
    catalogue = CountsTheDictLoads(pg_session)
    job = CleanAnimeJob(
        catalogue, RawTenraiRepository(pg_session), MetricsRepository(pg_session), STORE
    )

    summary = await job.full_clean_loop(batch_size=2)

    assert summary["batches"] == 4
    assert (catalogue.tag_loads, catalogue.studio_loads) == (1, 1)


async def test_the_dicts_learn_new_tags_only_after_the_batch_commits(pg_session):
    await seed(pg_session, [tagged(1, genres=[ACTION], studios=[SUNRISE])])
    tags, studios = {}, {}

    await make_job(pg_session)._clean_anime(None, None, tags, studios)

    assert tags == {1: "Action"}
    assert studios == {14: "Sunrise"}


async def test_a_batch_that_fails_leaves_the_dicts_and_the_database_unchanged(pg_session):
    class FailsOnStudioLinks(CatalogueRepository):
        async def replace_anime_studios(self, studio_ids_by_anime):
            raise RuntimeError("database went away")

    await seed(pg_session, [tagged(1, genres=[ACTION], studios=[SUNRISE])])
    job = CleanAnimeJob(
        FailsOnStudioLinks(pg_session),
        RawTenraiRepository(pg_session),
        MetricsRepository(pg_session),
        STORE,
    )
    tags, studios = {}, {}

    with pytest.raises(RuntimeError):
        await job._clean_anime(None, None, tags, studios)

    assert (tags, studios) == ({}, {})  # the dicts never learned tags that were rolled back
    assert await count(pg_session, Tag) == await count(pg_session, Anime) == 0


# --- relations ------------------------------------------------------------------------------


def related(relation: str, *entries: tuple[int, str]) -> dict:
    return {
        "relation": relation,
        "entry": [{"mal_id": mal_id, "type": kind, "name": "x"} for mal_id, kind in entries],
    }


def with_relations(mal_id: int, *groups: dict) -> tuple:
    return (mal_id, Resource.DETAILS, details(mal_id, relations=list(groups)))


async def relation_rows(session):
    rows = (await session.execute(select(AnimeRelation))).scalars().all()
    return sorted((r.anime_id, r.relation, r.related_mal_id) for r in rows)


async def test_anime_to_anime_relations_are_written_with_the_anime_and_manga_is_left_out(
    pg_session,
):
    await seed(
        pg_session,
        [
            with_relations(
                1,
                related("Adaptation", (174, "manga")),
                related("Sequel", (5, "anime"), (6, "anime")),
            ),
            with_relations(2, related("Prequel", (1, "anime"))),
        ],
    )

    summary = await make_job(pg_session).full_clean_loop()

    assert summary["relation_links_changed"] == 2
    assert await relation_rows(pg_session) == [
        (1, "Sequel", 5),
        (1, "Sequel", 6),
        (2, "Prequel", 1),
    ]


async def test_a_relation_to_an_anime_that_is_not_in_the_catalogue_is_kept(pg_session):
    await seed(pg_session, [with_relations(1, related("Sequel", (99999, "anime")))])

    await make_job(pg_session).full_clean_loop()

    assert await relation_rows(pg_session) == [(1, "Sequel", 99999)]


async def test_relations_are_written_in_the_same_batch_as_the_anime_not_after_the_loop(
    pg_session,
):
    # anime 2 is related to anime 1, which is cleaned in the very same batch
    await seed(
        pg_session,
        [
            with_relations(1, related("Sequel", (2, "anime"))),
            with_relations(2, related("Prequel", (1, "anime"))),
        ],
    )

    await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert await relation_rows(pg_session) == [(1, "Sequel", 2), (2, "Prequel", 1)]


async def test_cleaning_the_same_relations_again_changes_nothing_and_a_dropped_one_is_removed(
    pg_session,
):
    await seed(
        pg_session,
        [with_relations(1, related("Sequel", (5, "anime")), related("Side Story", (7, "anime")))],
    )
    await make_job(pg_session).full_clean_loop()

    unchanged = await make_job(pg_session).full_clean_loop(full_pass=True)

    assert unchanged["relation_links_changed"] == 0
    put_payload(
        1, Resource.DETAILS, with_relations(1, related("Sequel", (5, "anime")))[2]
    )  # MAL dropped one

    changed = await make_job(pg_session).full_clean_loop(full_pass=True)

    assert changed["relation_links_changed"] == 1
    assert await relation_rows(pg_session) == [(1, "Sequel", 5)]


async def test_an_anime_that_failed_to_parse_gets_no_relations(pg_session):
    broken = {"data": {"mal_id": 1, "relations": [related("Sequel", (5, "anime"))]}}  # no title
    await seed(pg_session, [(1, Resource.DETAILS, broken)])

    summary = await make_job(pg_session).full_clean_loop()

    assert summary["parse_failed"] == 1
    assert await relation_rows(pg_session) == []


# --- the run record and the cut-off ---------------------------------------------------------


async def last_run(session):
    return await MetricsRepository(session).latest_run("clean_anime")


async def test_a_clean_run_is_recorded_with_its_summary(pg_session):
    await seed(pg_session, two_complete_anime())

    summary = await make_job(pg_session).full_clean_loop(batch_size=3)

    run = await last_run(pg_session)
    assert run.status.value == "succeeded"
    assert run.finished_at is not None
    assert run.error is None
    assert run.summary["rows_read"] == 8
    assert run.summary["batches"] == 3
    assert run.summary["resume_after"] is None
    assert run.summary == summary


async def test_the_summary_totals_the_rows_written_and_the_time_taken(pg_session):
    await seed(
        pg_session,
        [
            *two_complete_anime(),
            tagged(3, genres=[ACTION], studios=[SUNRISE]),
        ],
    )

    summary = await make_job(pg_session).full_clean_loop(batch_size=3)

    assert summary["rows_written"] == (
        summary["anime_changed"]
        + summary["tags_upserted"]
        + summary["studios_upserted"]
        + summary["statistics_written"]
        + summary["episodes_written"]
        + summary["reviews_written"]
    )
    assert (
        summary["rows_written"] == 3 + 1 + 1 + 2 + 6 + 4
    )  # anime, tag, studio, stats, eps, reviews
    assert summary["read_seconds"] >= 0
    assert summary["write_seconds"] > 0
    assert summary["elapsed_seconds"] >= summary["read_seconds"] + summary["write_seconds"] - 0.001
    assert summary["max_batch_seconds"] >= summary["avg_batch_ms"] / 1000 - 0.001  # max >= mean
    assert summary["rows_read_per_second"] > 0


async def test_every_batch_reports_how_long_it_took(pg_session):
    await seed(pg_session, two_complete_anime())

    result = await make_job(pg_session)._clean_anime(None, None, {}, {})

    assert result.counts["read_seconds"] >= 0
    assert result.counts["write_seconds"] > 0


async def test_a_failed_clean_run_is_recorded_with_where_to_resume(pg_session):
    await seed(pg_session, two_complete_anime())
    flaky = CleanAnimeJob(
        FailsOnItsSecondBatch(pg_session),
        RawTenraiRepository(pg_session),
        MetricsRepository(pg_session),
        STORE,
    )

    with pytest.raises(RuntimeError):
        await flaky.full_clean_loop(batch_size=4)

    run = await last_run(pg_session)
    assert run.status.value == "failed"
    assert run.error == "RuntimeError: database went away"
    assert run.summary["resume_after"] == [1, "statistics"]
    assert run.summary["batches"] == 1


async def test_by_default_a_run_cleans_only_what_was_fetched_since_the_last_succeeded_run(
    pg_session,
):
    future = datetime.now(UTC) + timedelta(days=1)
    await seed(pg_session, [(1, Resource.DETAILS, details(1), 1)])
    first = await make_job(pg_session).full_clean_loop()
    assert first["since"] is None  # no earlier run: everything
    put_payload(2, Resource.DETAILS, details(2))
    pg_session.add(RawTenrai(mal_id=2, resource=Resource.DETAILS, size_bytes=1, fetched_at=future))
    await pg_session.commit()

    second = await make_job(pg_session).full_clean_loop()

    assert second["since"] is not None
    assert second["rows_read"] == 1  # only the row fetched after the first run started
    assert sorted((await pg_session.execute(select(Anime.mal_id))).scalars().all()) == [1, 2]


async def test_a_full_pass_ignores_earlier_runs_and_an_explicit_since_overrides_them(pg_session):
    await seed(
        pg_session, [(1, Resource.DETAILS, details(1), 1), (2, Resource.DETAILS, details(2), 20)]
    )
    await make_job(pg_session).full_clean_loop()

    full = await make_job(pg_session).full_clean_loop(full_pass=True)
    explicit = await make_job(pg_session).full_clean_loop(since=at(10))

    assert (full["since"], full["rows_read"]) == (None, 2)
    assert explicit["rows_read"] == 1
    assert explicit["since"] == at(10).isoformat()


async def test_a_failed_run_does_not_move_the_cut_off(pg_session):
    await seed(pg_session, two_complete_anime())
    flaky = CleanAnimeJob(
        FailsOnItsSecondBatch(pg_session),
        RawTenraiRepository(pg_session),
        MetricsRepository(pg_session),
        STORE,
    )
    with pytest.raises(RuntimeError):
        await flaky.full_clean_loop(batch_size=4)

    retry = await make_job(pg_session).full_clean_loop(batch_size=4)

    assert retry["since"] is None  # the failed run is not a cut-off
    assert retry["rows_read"] == 8
    assert await count(pg_session, Anime) == 2
