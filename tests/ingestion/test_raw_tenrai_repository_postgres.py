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

    await repo.upsert(1, Resource.DETAILS, {"data": {"mal_id": 1}})
    await repo.commit()
    row = await repo.find(1, Resource.DETAILS)

    assert row.resource is Resource.DETAILS
    assert row.payload == {"data": {"mal_id": 1}}
    assert row.fetched_at is not None


async def test_upsert_overwrites_payload_and_fetched_at_without_duplicating(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert(1, Resource.DETAILS, {"v": 1})
    await repo.commit()
    first_fetched_at = (await repo.find(1, Resource.DETAILS)).fetched_at

    await repo.upsert(1, Resource.DETAILS, {"v": 2})
    await repo.commit()
    pg_session.expire_all()
    row = await repo.find(1, Resource.DETAILS)

    assert await count_rows(pg_session) == 1
    assert row.payload == {"v": 2}
    assert row.fetched_at > first_fetched_at


async def test_resources_of_one_anime_are_separate_rows(pg_session):
    repo = RawTenraiRepository(pg_session)

    written = await repo.upsert_many(
        [
            (1, Resource.DETAILS, {"d": 1}),
            (1, Resource.EPISODES, [{"page": 1}, {"page": 2}]),  # multi-page: a list of pages
            (2, Resource.DETAILS, {"d": 2}),
        ]
    )
    await repo.commit()

    assert written == 3
    assert (await repo.find(1, Resource.EPISODES)).payload == [{"page": 1}, {"page": 2}]
    assert await count_rows(pg_session) == 3


async def test_upsert_many_mixes_new_and_existing_rows(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert(1, Resource.DETAILS, {"v": 1})
    await repo.commit()

    await repo.upsert_many([(1, Resource.DETAILS, {"v": 2}), (2, Resource.DETAILS, {"v": 1})])
    await repo.commit()
    pg_session.expire_all()

    assert await count_rows(pg_session) == 2
    assert (await repo.find(1, Resource.DETAILS)).payload == {"v": 2}


async def test_upsert_many_with_no_rows_does_nothing(pg_session):
    assert await RawTenraiRepository(pg_session).upsert_many([]) == 0


async def test_delete_removes_only_the_matching_row(pg_session):
    repo = RawTenraiRepository(pg_session)
    await repo.upsert_many([(1, Resource.DETAILS, {}), (1, Resource.REVIEWS, {})])
    await repo.commit()

    assert await repo.delete(1, Resource.DETAILS) == 1
    assert await repo.delete(1, Resource.DETAILS) == 0  # already gone
    await repo.commit()

    assert await repo.find(1, Resource.DETAILS) is None
    assert await repo.find(1, Resource.REVIEWS) is not None
