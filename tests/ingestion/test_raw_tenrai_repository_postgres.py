from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.models import RawTenrai
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository

pytestmark = pytest.mark.postgres


async def count_rows(session) -> int:
    return (await session.execute(select(func.count()).select_from(RawTenrai))).scalar_one()


async def test_find_returns_none_when_missing_and_the_row_when_present(pg_session):
    repo = RawTenraiRepository(pg_session)
    assert await repo.find(1, Resource.DETAILS) is None

    await repo.upsert(1, Resource.DETAILS, 120)
    await repo.commit()
    row = await repo.find(1, Resource.DETAILS)

    assert row.resource is Resource.DETAILS
    assert row.size_bytes == 120
    assert row.fetched_at is not None


async def test_upsert_overwrites_size_and_fetched_at_without_duplicating(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert(1, Resource.DETAILS, 10)
    await repo.commit()
    first_fetched_at = (await repo.find(1, Resource.DETAILS)).fetched_at

    await repo.upsert(1, Resource.DETAILS, 20)
    await repo.commit()
    pg_session.expire_all()
    row = await repo.find(1, Resource.DETAILS)

    assert await count_rows(pg_session) == 1
    assert row.size_bytes == 20
    assert row.fetched_at > first_fetched_at


async def test_resources_of_one_anime_are_separate_rows(pg_session):
    repo = RawTenraiRepository(pg_session)

    written = await repo.upsert_many(
        [
            (1, Resource.DETAILS, 1),
            (1, Resource.EPISODES, 2),
            (2, Resource.DETAILS, 3),
        ]
    )
    await repo.commit()

    assert written == 3
    assert (await repo.find(1, Resource.EPISODES)).size_bytes == 2
    assert await count_rows(pg_session) == 3


async def test_upsert_many_mixes_new_and_existing_rows(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert(1, Resource.DETAILS, 1)
    await repo.commit()

    await repo.upsert_many([(1, Resource.DETAILS, 2), (2, Resource.DETAILS, 1)])
    await repo.commit()
    pg_session.expire_all()

    assert await count_rows(pg_session) == 2
    assert (await repo.find(1, Resource.DETAILS)).size_bytes == 2


async def test_upsert_many_with_no_rows_does_nothing(pg_session):
    assert await RawTenraiRepository(pg_session).upsert_many([]) == 0


async def test_delete_removes_only_the_matching_row(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert_many([(1, Resource.DETAILS, 1), (1, Resource.REVIEWS, 1)])
    await repo.commit()

    assert await repo.delete(1, Resource.DETAILS) == 1
    assert await repo.delete(1, Resource.DETAILS) == 0  # already gone
    await repo.commit()

    assert await repo.find(1, Resource.DETAILS) is None
    assert await repo.find(1, Resource.REVIEWS) is not None


# --- get_batch -----------------------------------------------------------------------------


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


async def seed_raw(session, rows):
    """rows: (mal_id, resource, fetched_day)"""
    for mal_id, resource, day in rows:
        session.add(RawTenrai(mal_id=mal_id, resource=resource, size_bytes=1, fetched_at=at(day)))
    await session.commit()


def keys(rows):
    return [(row.mal_id, row.resource) for row in rows]


ALL_ROWS = [
    (2, Resource.STATISTICS, 1),
    (1, Resource.REVIEWS, 1),
    (1, Resource.DETAILS, 1),
    (2, Resource.DETAILS, 1),
    (1, Resource.STATISTICS, 1),
    (1, Resource.EPISODES, 1),
]
IN_ORDER = [
    (1, Resource.DETAILS),
    (1, Resource.EPISODES),
    (1, Resource.REVIEWS),
    (1, Resource.STATISTICS),
    (2, Resource.DETAILS),
    (2, Resource.STATISTICS),
]


async def test_get_batch_returns_rows_in_anime_then_resource_order_up_to_the_limit(pg_session):
    await seed_raw(pg_session, ALL_ROWS)
    repo = RawTenraiRepository(pg_session)

    assert keys(await repo.get_batch(None, None, 100)) == IN_ORDER
    assert keys(await repo.get_batch(None, None, 4)) == IN_ORDER[:4]


async def test_get_batch_on_an_empty_table_returns_nothing(pg_session):
    assert await RawTenraiRepository(pg_session).get_batch(None, None, 10) == []


async def test_paging_with_the_last_row_as_the_cursor_reads_every_row_once(pg_session):
    await seed_raw(pg_session, ALL_ROWS)
    repo = RawTenraiRepository(pg_session)

    seen, after = [], None
    while batch := await repo.get_batch(None, after, 4):
        seen += keys(batch)
        after = (batch[-1].mal_id, batch[-1].resource)

    assert seen == IN_ORDER  # no gaps, no repeats, across a boundary that splits anime 1


async def test_details_comes_before_the_other_resources_even_with_one_row_per_batch(pg_session):
    await seed_raw(pg_session, ALL_ROWS)
    repo = RawTenraiRepository(pg_session)

    seen, after = [], None
    while batch := await repo.get_batch(None, after, 1):
        seen.append(batch[0].resource)
        after = (batch[0].mal_id, batch[0].resource)

    assert seen[:4] == [Resource.DETAILS, Resource.EPISODES, Resource.REVIEWS, Resource.STATISTICS]


async def test_since_keeps_only_rows_fetched_at_or_after_it(pg_session):
    await seed_raw(
        pg_session,
        [
            (1, Resource.DETAILS, 1),  # old
            (1, Resource.STATISTICS, 10),  # exactly at the cut-off
            (2, Resource.DETAILS, 20),  # newer
        ],
    )

    rows = await RawTenraiRepository(pg_session).get_batch(at(10), None, 100)

    assert keys(rows) == [(1, Resource.STATISTICS), (2, Resource.DETAILS)]


async def test_since_and_cursor_work_together(pg_session):
    await seed_raw(
        pg_session,
        [
            (1, Resource.DETAILS, 20),
            (1, Resource.EPISODES, 1),  # too old
            (1, Resource.REVIEWS, 20),
            (2, Resource.DETAILS, 20),
        ],
    )
    repo = RawTenraiRepository(pg_session)

    first = await repo.get_batch(at(10), None, 2)
    second = await repo.get_batch(at(10), (first[-1].mal_id, first[-1].resource), 2)

    assert keys(first) == [(1, Resource.DETAILS), (1, Resource.REVIEWS)]
    assert keys(second) == [(2, Resource.DETAILS)]


async def test_get_batch_returns_the_index_rows_with_their_sizes(pg_session):
    pg_session.add(RawTenrai(mal_id=1, resource=Resource.DETAILS, size_bytes=77))
    await pg_session.commit()

    (row,) = await RawTenraiRepository(pg_session).get_batch(None, None, 10)

    assert (row.mal_id, row.resource, row.size_bytes) == (1, Resource.DETAILS, 77)
