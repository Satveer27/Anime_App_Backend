from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.jobs.discovery_job import DiscoveryJob
from app.ingestion.models import IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository

pytestmark = pytest.mark.postgres


async def seed(session, mal_id, status, resource=Resource.DETAILS, **fields):
    session.add(IngestionTask(mal_id=mal_id, resource=resource, status=status, **fields))
    await session.commit()


async def fetch(session, mal_id, resource=Resource.DETAILS) -> IngestionTask:
    session.expire_all()
    return await TaskRepository(session).get_task(mal_id, resource)


async def test_insert_or_reset_handles_every_status_and_counts_each_outcome(pg_session):
    old = datetime(2020, 1, 1, tzinfo=UTC)
    for mal_id, status in enumerate(
        [
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.DEAD,
            TaskStatus.GONE,
            TaskStatus.PENDING,
        ],
        start=1,
    ):
        await seed(
            pg_session,
            mal_id,
            status,
            attempts=3,
            last_error="boom",
            next_attempt_at=old,
            updated_at=old,
        )
    repo = TaskRepository(pg_session)

    inserted, reset = await repo.insert_or_reset_tasks(
        [(mal_id, Resource.DETAILS) for mal_id in range(1, 8)]  # 6 and 7 are new
    )
    await repo.commit()

    assert (inserted, reset) == (2, 2)  # done+failed reset; dead, gone, pending untouched
    done = await fetch(pg_session, 1)
    assert done.status is TaskStatus.PENDING
    assert (done.attempts, done.last_error, done.next_attempt_at) == (0, None, None)
    assert done.updated_at > old
    assert (await fetch(pg_session, 2)).status is TaskStatus.PENDING
    dead = await fetch(pg_session, 3)
    assert dead.status is TaskStatus.DEAD and dead.attempts == 3 and dead.last_error == "boom"
    assert (await fetch(pg_session, 4)).status is TaskStatus.GONE
    pending = await fetch(pg_session, 5)
    assert pending.status is TaskStatus.PENDING and pending.attempts == 3  # not touched
    assert (await fetch(pg_session, 6)).status is TaskStatus.PENDING
    assert (await fetch(pg_session, 7)).status is TaskStatus.PENDING


async def test_insert_or_reset_only_touches_the_listed_resources(pg_session):
    await seed(pg_session, 1, TaskStatus.DONE, Resource.DETAILS)
    await seed(pg_session, 1, TaskStatus.DONE, Resource.EPISODES)
    repo = TaskRepository(pg_session)

    await repo.insert_or_reset_tasks([(1, Resource.DETAILS)])
    await repo.commit()

    assert (await fetch(pg_session, 1, Resource.DETAILS)).status is TaskStatus.PENDING
    assert (await fetch(pg_session, 1, Resource.EPISODES)).status is TaskStatus.DONE


async def test_insert_or_reset_with_no_rows_does_nothing(pg_session):
    assert await TaskRepository(pg_session).insert_or_reset_tasks([]) == (0, 0)


class FakeAdapter:
    def __init__(self, pages, fail_on=None):
        self.pages = pages
        self.fail_on = fail_on

    async def list_anime(self, page=1, **filters):
        if page == self.fail_on:
            raise RuntimeError("tenrai down")
        return self.pages[page]


PAGES = {
    1: {
        "pagination": {"has_next_page": True},
        "data": [{"mal_id": 1, "type": "TV"}, {"mal_id": 2, "type": "TV"}],
    },
    2: {
        "pagination": {"has_next_page": False},
        "data": [{"mal_id": 3, "type": "Movie"}, {"mal_id": 4, "type": "TV"}],
    },
}


async def test_refresh_run_resets_known_anime_and_adds_new_ones(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        await seed(s, 1, TaskStatus.DONE)
        await seed(s, 2, TaskStatus.DEAD)
        await seed(s, 3, TaskStatus.FAILED)

    async with pg_sessionmaker() as tasks, pg_sessionmaker() as metrics:
        job = DiscoveryJob(FakeAdapter(PAGES), TaskRepository(tasks), MetricsRepository(metrics))
        summary = await job.run_job_refresh(
            "discovery_weekly", {"status": "airing"}, resources=(Resource.DETAILS,)
        )

    assert summary["tasks_inserted"] == 1  # anime 4
    assert summary["tasks_reset"] == 2  # anime 1 (done) and 3 (failed)
    assert summary["tasks_unchanged"] == 1  # anime 2 is dead
    async with pg_sessionmaker() as s:
        assert (await fetch(s, 1)).status is TaskStatus.PENDING
        assert (await fetch(s, 2)).status is TaskStatus.DEAD
        assert (await fetch(s, 3)).status is TaskStatus.PENDING
        assert (await fetch(s, 4)).status is TaskStatus.PENDING
        count = (await s.execute(select(func.count()).select_from(IngestionTask))).scalar_one()
        assert count == 4
        run = await MetricsRepository(s).latest_run("discovery_weekly")
    assert run.status is RunStatus.SUCCEEDED
    assert run.summary["tasks_reset"] == 2


async def test_crashed_refresh_resumes_from_the_stored_page_without_repeating_work(
    pg_sessionmaker,
):
    async with pg_sessionmaker() as s:
        for mal_id in (1, 2, 3, 4):
            await seed(s, mal_id, TaskStatus.DONE)
    adapter = FakeAdapter(PAGES, fail_on=2)

    async with pg_sessionmaker() as tasks, pg_sessionmaker() as metrics:
        job = DiscoveryJob(adapter, TaskRepository(tasks), MetricsRepository(metrics))
        with pytest.raises(RuntimeError):
            await job.run_job_refresh("discovery_weekly", {}, resources=(Resource.DETAILS,))

    async with pg_sessionmaker() as s:
        failed_run = await MetricsRepository(s).latest_run("discovery_weekly")
        assert failed_run.status is RunStatus.FAILED
        assert failed_run.error.startswith("RuntimeError")
        resume_page = failed_run.summary["resume_page"]
        assert resume_page == 2
        # page 1 was committed before the crash, page 2 was not
        assert (await fetch(s, 1)).status is TaskStatus.PENDING
        assert (await fetch(s, 3)).status is TaskStatus.DONE
        # the worker finishes anime 1 before the operator resumes
        await TaskRepository(s).mark_done(await TaskRepository(s).get_task(1, Resource.DETAILS))
        await s.commit()

    adapter.fail_on = None
    async with pg_sessionmaker() as tasks, pg_sessionmaker() as metrics:
        job = DiscoveryJob(adapter, TaskRepository(tasks), MetricsRepository(metrics))
        await job.run_job_refresh(
            "discovery_weekly", {}, resources=(Resource.DETAILS,), page=resume_page
        )

    async with pg_sessionmaker() as s:
        assert (await fetch(s, 1)).status is TaskStatus.DONE  # page 1 was not repeated
        assert (await fetch(s, 3)).status is TaskStatus.PENDING
        assert (await fetch(s, 4)).status is TaskStatus.PENDING


async def test_latest_run_returns_the_newest_run_of_that_job_only(pg_session):
    repo = MetricsRepository(pg_session)
    assert await repo.latest_run("discovery_weekly") is None

    first = await repo.start_run("discovery_weekly")
    other = await repo.start_run("discovery_overall")
    second = await repo.start_run("discovery_weekly")
    await repo.commit()

    latest = await repo.latest_run("discovery_weekly")
    assert latest.id == second
    assert first < second and other not in (first, second)
