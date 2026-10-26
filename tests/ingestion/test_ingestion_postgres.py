from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.jobs.discovery_job import DiscoveryJob
from app.ingestion.models import IngestionRun, IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import MAX_ATTEMPTS, TaskRepository

pytestmark = pytest.mark.postgres


async def count_tasks(session) -> int:
    return (await session.execute(select(func.count()).select_from(IngestionTask))).scalar_one()


async def seed(session, mal_id, resource=Resource.DETAILS, **fields) -> None:
    session.add(IngestionTask(mal_id=mal_id, resource=resource, **fields))
    await session.commit()


async def status_of(session, mal_id, resource=Resource.DETAILS):
    session.expire_all()
    return (await TaskRepository(session).get_task(mal_id, resource)).status


class FakeAdapter:
    request_count = 0
    retry_count = 0
    rate_limited_count = 0

    def __init__(self, pages):
        self.pages = pages

    async def list_anime(self, page=1, **filters):
        return self.pages[page]


PAGES = {
    1: {
        "pagination": {"has_next_page": True},
        "data": [{"mal_id": 1, "type": "TV"}, {"mal_id": 2, "type": "Music"}],
    },
    2: {"pagination": {"has_next_page": False}, "data": [{"mal_id": 3, "type": "Movie"}]},
}


# --- insert_tasks -----------------------------------------------------------------------


async def test_insert_tasks_is_idempotent_and_counts_only_new_rows(pg_session):
    repo = TaskRepository(pg_session)
    rows = [(1, Resource.DETAILS), (1, Resource.EPISODES)]

    assert await repo.insert_tasks(rows) == 2
    await repo.commit()
    assert await repo.insert_tasks([*rows, (2, Resource.DETAILS)]) == 1
    await repo.commit()

    assert await count_tasks(pg_session) == 3


async def test_inserted_task_has_defaults_and_enum_round_trips(pg_session):
    repo = TaskRepository(pg_session)
    await repo.insert_tasks([(1, Resource.REVIEWS)])
    await repo.commit()

    task = await repo.get_task(1, Resource.REVIEWS)

    assert task.resource is Resource.REVIEWS
    assert task.status is TaskStatus.PENDING
    assert task.attempts == 0
    assert task.created_at is not None


# --- discovery --------------------------------------------------------------------------


async def test_discovery_twice_creates_no_duplicates_and_keeps_done_tasks(pg_sessionmaker):
    async def run():
        async with pg_sessionmaker() as tasks, pg_sessionmaker() as metrics:
            job = DiscoveryJob(
                FakeAdapter(PAGES), TaskRepository(tasks), MetricsRepository(metrics)
            )
            return await job.run_job_overall()

    first = await run()
    assert first["tasks_inserted"] == 8  # anime 1 and 3, four resources each; Music is skipped

    async with pg_sessionmaker() as s:
        repo = TaskRepository(s)
        await repo.mark_done(await repo.get_task(1, Resource.DETAILS))
        await s.commit()

    second = await run()

    assert second["tasks_inserted"] == 0
    async with pg_sessionmaker() as s:
        assert await count_tasks(s) == 8
        assert await status_of(s, 1) is TaskStatus.DONE


async def test_discovery_records_the_run_in_ingestion_run(pg_sessionmaker):
    async with pg_sessionmaker() as tasks, pg_sessionmaker() as metrics:
        job = DiscoveryJob(FakeAdapter(PAGES), TaskRepository(tasks), MetricsRepository(metrics))
        await job.run_job_overall()

    async with pg_sessionmaker() as s:
        run = (await s.execute(select(IngestionRun))).scalar_one()
    assert run.status is RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.summary["pages"] == 2
    assert run.error is None


# --- claim_next -------------------------------------------------------------------------


async def test_claim_next_picks_lowest_claimable_and_ignores_the_rest(pg_session):
    now = datetime.now(UTC)
    await seed(pg_session, 1, status=TaskStatus.DONE)
    await seed(pg_session, 2, status=TaskStatus.DEAD)
    await seed(pg_session, 3, status=TaskStatus.GONE)
    await seed(pg_session, 4, status=TaskStatus.FAILED, next_attempt_at=now + timedelta(hours=1))
    await seed(pg_session, 5, status=TaskStatus.FAILED, next_attempt_at=now - timedelta(hours=1))
    await seed(pg_session, 6)

    claimed = await TaskRepository(pg_session).claim_next()

    assert claimed.mal_id == 5  # 4 is still backing off, 1-3 are final


async def test_claim_next_returns_none_when_nothing_is_claimable(pg_session):
    await seed(pg_session, 1, status=TaskStatus.DONE)

    assert await TaskRepository(pg_session).claim_next() is None


async def test_second_claimer_skips_a_locked_row(pg_sessionmaker):
    async with pg_sessionmaker() as setup:
        await TaskRepository(setup).insert_tasks([(1, Resource.DETAILS), (2, Resource.DETAILS)])
        await setup.commit()

    async with (
        pg_sessionmaker() as worker_a,
        pg_sessionmaker() as worker_b,
        pg_sessionmaker() as worker_c,
    ):
        first = await TaskRepository(worker_a).claim_next()  # holds the row lock, no commit
        second = await TaskRepository(worker_b).claim_next()
        third = await TaskRepository(worker_c).claim_next()

    assert first.mal_id == 1
    assert second.mal_id == 2
    assert third is None  # 1 is locked by worker_a and 2 by worker_b


# --- mark_* and reset -------------------------------------------------------------------


async def test_mark_done_clears_error_and_backoff(pg_session):
    await seed(
        pg_session,
        1,
        status=TaskStatus.FAILED,
        last_error="boom",
        next_attempt_at=datetime.now(UTC),
    )
    repo = TaskRepository(pg_session)

    await repo.mark_done(await repo.get_task(1, Resource.DETAILS))
    await repo.commit()
    pg_session.expire_all()
    task = await repo.get_task(1, Resource.DETAILS)

    assert task.status is TaskStatus.DONE
    assert task.last_error is None
    assert task.next_attempt_at is None


async def test_mark_failed_backs_off_then_goes_dead(pg_session):
    await seed(pg_session, 1)
    repo = TaskRepository(pg_session)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        pg_session.expire_all()
        task = await repo.get_task(1, Resource.DETAILS)
        await repo.mark_failed(task, "x" * 1000)
        await repo.commit()
        pg_session.expire_all()
        task = await repo.get_task(1, Resource.DETAILS)
        assert task.attempts == attempt
        assert len(task.last_error) == 500
        if attempt < MAX_ATTEMPTS:
            assert task.status is TaskStatus.FAILED
            assert task.next_attempt_at > datetime.now(UTC)

    assert task.status is TaskStatus.DEAD
    assert task.next_attempt_at is None


async def test_reset_tasks_only_touches_done_and_failed(pg_session):
    statuses = [
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.DEAD,
        TaskStatus.GONE,
        TaskStatus.PENDING,
    ]
    for mal_id, status in enumerate(statuses, start=1):
        await seed(pg_session, mal_id, status=status, attempts=3)
    repo = TaskRepository(pg_session)

    changed = await repo.reset_tasks([1, 2, 3, 4, 5], [Resource.DETAILS])
    await repo.commit()

    assert changed == 2
    assert await status_of(pg_session, 1) is TaskStatus.PENDING
    assert await status_of(pg_session, 2) is TaskStatus.PENDING
    assert await status_of(pg_session, 3) is TaskStatus.DEAD
    assert await status_of(pg_session, 4) is TaskStatus.GONE
    pg_session.expire_all()
    assert (await repo.get_task(1, Resource.DETAILS)).attempts == 0


async def test_count_by_status_includes_every_state(pg_session):
    await seed(pg_session, 1, status=TaskStatus.DONE)
    await seed(pg_session, 2, status=TaskStatus.DONE)
    await seed(pg_session, 3, status=TaskStatus.FAILED)

    counts = await TaskRepository(pg_session).count_by_status()

    assert counts == {"pending": 0, "done": 2, "failed": 1, "dead": 0, "gone": 0}


async def test_last_succeeded_run_start_ignores_failed_and_running_runs(pg_session):
    repo = MetricsRepository(pg_session)
    assert await repo.last_succeeded_run_start("clean_anime") is None

    done = await repo.start_run("clean_anime")
    await repo.finish_run(done, RunStatus.SUCCEEDED, {})
    await repo.commit()
    first = (await repo.latest_run("clean_anime")).started_at
    failed = await repo.start_run("clean_anime")
    await repo.finish_run(failed, RunStatus.FAILED, {}, "boom")
    await repo.start_run("clean_anime")  # still running
    await repo.start_run("other_job")
    await repo.commit()

    assert await repo.last_succeeded_run_start("clean_anime") == first
    assert await repo.last_succeeded_run_start("other_job") is None
