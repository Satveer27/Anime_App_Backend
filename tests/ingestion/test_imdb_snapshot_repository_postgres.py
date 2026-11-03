from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ingestion.adapters.imdb import DownloadedFile
from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.models import ImdbSnapshot
from app.ingestion.repository.imdb_snapshot_repository import ImdbSnapshotRepository

pytestmark = pytest.mark.postgres

FETCHED_AT = datetime(2026, 11, 9, 3, 0, 0, tzinfo=UTC)


def downloaded(file: ImdbFile, etag: str, size: int) -> DownloadedFile:
    return DownloadedFile(
        file=file,
        path=Path(file.filename),
        etag=etag,
        size_bytes=size,
        download_seconds=1.0,
    )


def three_files(tag: str = "v1") -> list[DownloadedFile]:
    return [
        downloaded(ImdbFile.BASICS, f'"basics-{tag}"', 300),
        downloaded(ImdbFile.EPISODE, f'"episode-{tag}"', 200),
        downloaded(ImdbFile.RATINGS, f'"ratings-{tag}"', 100),
    ]


async def row(session, snapshot_id: int) -> ImdbSnapshot:
    session.expire_all()
    return (
        await session.execute(select(ImdbSnapshot).where(ImdbSnapshot.id == snapshot_id))
    ).scalar_one()


async def test_a_new_run_is_a_running_row_with_nothing_filled_in(pg_session):
    repo = ImdbSnapshotRepository(pg_session)

    snapshot_id = await repo.start_run()

    snapshot = await row(pg_session, snapshot_id)
    assert snapshot.status == SnapshotStatus.RUNNING
    assert snapshot.finished_at is None
    assert snapshot.basics_etag is None


async def test_every_run_gets_its_own_row(pg_session):
    repo = ImdbSnapshotRepository(pg_session)

    first = await repo.start_run()
    second = await repo.start_run()

    assert first != second


async def test_mark_archived_stores_etag_and_size_of_each_file(pg_session):
    repo = ImdbSnapshotRepository(pg_session)
    snapshot_id = await repo.start_run()

    await repo.mark_archived(snapshot_id, FETCHED_AT, three_files())

    s = await row(pg_session, snapshot_id)
    assert s.status == SnapshotStatus.ARCHIVED
    assert s.finished_at == FETCHED_AT
    assert (s.basics_etag, s.basics_size_bytes) == ('"basics-v1"', 300)
    assert (s.episode_etag, s.episode_size_bytes) == ('"episode-v1"', 200)
    assert (s.ratings_etag, s.ratings_size_bytes) == ('"ratings-v1"', 100)


async def test_mark_skipped_and_mark_failed_set_status_and_finish_time(pg_session):
    repo = ImdbSnapshotRepository(pg_session)
    skipped = await repo.start_run()
    failed = await repo.start_run()

    await repo.mark_skipped(skipped)
    await repo.mark_failed(failed)

    s = await row(pg_session, skipped)
    assert s.status == SnapshotStatus.SKIPPED and s.finished_at is not None
    f = await row(pg_session, failed)
    assert f.status == SnapshotStatus.FAILED and f.finished_at is not None
    assert f.basics_etag is None


async def test_latest_archived_is_none_when_nothing_was_archived(pg_session):
    repo = ImdbSnapshotRepository(pg_session)
    await repo.start_run()

    assert await repo.latest_archived() is None


async def test_latest_archived_ignores_failed_skipped_and_running_runs(pg_session):
    repo = ImdbSnapshotRepository(pg_session)
    old = await repo.start_run()
    await repo.mark_archived(old, FETCHED_AT, three_files("old"))
    newer_failed = await repo.start_run()
    await repo.mark_failed(newer_failed)
    newer_skipped = await repo.start_run()
    await repo.mark_skipped(newer_skipped)
    await repo.start_run()  # still running

    latest = await repo.latest_archived()

    assert latest.id == old
    assert latest.basics_etag == '"basics-old"'


async def test_latest_archived_is_the_newest_archived_one(pg_session):
    repo = ImdbSnapshotRepository(pg_session)
    first = await repo.start_run()
    await repo.mark_archived(first, FETCHED_AT, three_files("v1"))
    second = await repo.start_run()
    await repo.mark_archived(second, FETCHED_AT, three_files("v2"))

    latest = await repo.latest_archived()

    assert latest.id == second
    assert latest.ratings_etag == '"ratings-v2"'
