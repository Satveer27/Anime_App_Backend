import pytest
from sqlalchemy import func, select

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.exceptions import CircuitOpenError, GoneError, PermanentError, TransientError
from app.ingestion.jobs.fetch_anime_job import FetchAnimeJob
from app.ingestion.models import IngestionTask, RawTenrai
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository

pytestmark = pytest.mark.postgres


class FakeAdapter:
    request_count = 0
    retry_count = 0
    rate_limited_count = 0

    def __init__(self, full=None, episodes=None):
        self.full = full
        self.episodes = episodes

    async def get_full(self, mal_id):
        if isinstance(self.full, Exception):
            raise self.full
        return self.full

    async def get_episodes(self, mal_id, page=1):
        return self.episodes[page - 1]


async def seed(sessionmaker, resource=Resource.DETAILS):
    async with sessionmaker() as s:
        s.add(IngestionTask(mal_id=5, resource=resource))
        await s.commit()


async def run_once(sessionmaker, adapter):
    """One worker pass on its own session, the way a real run would."""
    async with sessionmaker() as session, sessionmaker() as metrics:
        job = FetchAnimeJob(
            adapter,
            TaskRepository(session),
            RawTenraiRepository(session),
            MetricsRepository(metrics),
        )
        return await job.fetch_job_from_tasks()


async def state(sessionmaker, resource=Resource.DETAILS):
    async with sessionmaker() as s:
        task = await TaskRepository(s).get_task(5, resource)
        raw = await RawTenraiRepository(s).find(5, resource)
        return task, raw


async def test_success_saves_the_raw_row_and_marks_done_together(pg_sessionmaker):
    await seed(pg_sessionmaker)

    await run_once(pg_sessionmaker, FakeAdapter(full={"data": {"mal_id": 5}}))

    task, raw = await state(pg_sessionmaker)
    assert task.status is TaskStatus.DONE
    assert raw.payload == {"data": {"mal_id": 5}}


async def test_episode_pages_are_stored_as_a_jsonb_list(pg_sessionmaker):
    await seed(pg_sessionmaker, Resource.EPISODES)
    pages = [
        {"data": [1], "pagination": {"has_next_page": True}},
        {"data": [2], "pagination": {"has_next_page": False}},
    ]

    await run_once(pg_sessionmaker, FakeAdapter(episodes=pages))

    task, raw = await state(pg_sessionmaker, Resource.EPISODES)
    assert task.status is TaskStatus.DONE
    assert isinstance(raw.payload, list)
    assert raw.payload == pages


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GoneError("nope", status_code=404), TaskStatus.GONE),
        (PermanentError("bad", status_code=400), TaskStatus.DEAD),
        (TransientError("down", status_code=503), TaskStatus.FAILED),
    ],
)
async def test_adapter_errors_are_recorded_and_persisted(pg_sessionmaker, error, expected):
    await seed(pg_sessionmaker)

    await run_once(pg_sessionmaker, FakeAdapter(full=error))

    task, raw = await state(pg_sessionmaker)
    assert task.status is expected
    assert raw is None
    if expected is TaskStatus.FAILED:
        assert task.attempts == 1
        assert task.next_attempt_at is not None
        assert task.last_error == "TransientError: down"
    if expected is TaskStatus.DEAD:
        assert task.last_error == "PermanentError: bad"


async def test_circuit_open_leaves_the_task_untouched_and_claimable(pg_sessionmaker):
    await seed(pg_sessionmaker)

    with pytest.raises(CircuitOpenError):
        await run_once(pg_sessionmaker, FakeAdapter(full=CircuitOpenError(30)))

    task, raw = await state(pg_sessionmaker)
    assert task.status is TaskStatus.PENDING
    assert task.attempts == 0
    assert raw is None
    async with pg_sessionmaker() as s:  # the row lock was released
        assert await TaskRepository(s).claim_next() is not None


async def test_running_again_after_done_refetches_nothing_and_overwrites_nothing(
    pg_sessionmaker,
):
    await seed(pg_sessionmaker)
    await run_once(pg_sessionmaker, FakeAdapter(full={"v": 1}))

    result = await run_once(pg_sessionmaker, FakeAdapter(full={"v": 2}))

    assert result is None  # nothing claimable
    _, raw = await state(pg_sessionmaker)
    assert raw.payload == {"v": 1}
    async with pg_sessionmaker() as s:
        count = (await s.execute(select(func.count()).select_from(RawTenrai))).scalar_one()
    assert count == 1


async def test_a_bug_on_one_tasks_data_is_saved_as_failed(pg_sessionmaker):
    await seed(pg_sessionmaker, Resource.EPISODES)

    await run_once(pg_sessionmaker, FakeAdapter(episodes=[["not", "a", "dict"]]))

    task, raw = await state(pg_sessionmaker, Resource.EPISODES)
    assert task.status is TaskStatus.FAILED
    assert task.last_error.startswith("AttributeError")
    assert raw is None


async def test_the_loop_runs_to_the_end_and_records_the_run_and_the_queue(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        for mal_id in (1, 2, 3, 4):
            s.add(IngestionTask(mal_id=mal_id, resource=Resource.DETAILS))
        await s.commit()
    errors = {
        2: GoneError("nope", status_code=404),
        3: TransientError("down", status_code=503),
        4: PermanentError("bad", status_code=400),
    }

    class Adapter:
        request_count = 0
        retry_count = 0
        rate_limited_count = 0

        async def get_full(self, mal_id):
            if mal_id in errors:
                raise errors[mal_id]
            return {"data": {"mal_id": mal_id}}

    async with pg_sessionmaker() as session, pg_sessionmaker() as metrics:
        job = FetchAnimeJob(
            Adapter(),
            TaskRepository(session),
            RawTenraiRepository(session),
            MetricsRepository(metrics),
        )
        summary = await job.fetch_from_tasks()

    assert summary["tasks"] == 4
    assert (summary["done"], summary["gone"], summary["failed"], summary["dead"]) == (1, 1, 1, 1)
    # task 3 is backing off, so the loop ended with one task still failed
    assert summary["queue_after"] == {"pending": 0, "done": 1, "failed": 1, "dead": 1, "gone": 1}
    async with pg_sessionmaker() as s:
        run = await MetricsRepository(s).latest_run("fetch_tasks")
        raw_count = (await s.execute(select(func.count()).select_from(RawTenrai))).scalar_one()
    assert run.status is RunStatus.SUCCEEDED
    assert run.summary["tasks"] == 4
    assert run.summary["queue_after"]["failed"] == 1
    assert raw_count == 1  # only the successful fetch stored a raw row


async def test_a_loop_stopped_by_an_open_circuit_is_recorded_as_failed(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(IngestionTask(mal_id=1, resource=Resource.DETAILS))
        s.add(IngestionTask(mal_id=2, resource=Resource.DETAILS))
        await s.commit()

    class Adapter:
        request_count = 0
        retry_count = 0
        rate_limited_count = 0

        async def get_full(self, mal_id):
            if mal_id == 2:
                raise CircuitOpenError(30)
            return {"data": {}}

    async with pg_sessionmaker() as session, pg_sessionmaker() as metrics:
        job = FetchAnimeJob(
            Adapter(),
            TaskRepository(session),
            RawTenraiRepository(session),
            MetricsRepository(metrics),
        )
        with pytest.raises(CircuitOpenError):
            await job.fetch_from_tasks()

    async with pg_sessionmaker() as s:
        run = await MetricsRepository(s).latest_run("fetch_tasks")
        task_two = await TaskRepository(s).get_task(2, Resource.DETAILS)
    assert run.status is RunStatus.FAILED
    assert run.summary["stopped_by"] == "CircuitOpenError"
    assert run.summary["tasks"] == 1
    assert task_two.status is TaskStatus.PENDING  # untouched, no attempt used
    assert task_two.attempts == 0


async def test_the_loop_counts_first_try_successes_from_the_attempt_counter(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        s.add(IngestionTask(mal_id=1, resource=Resource.DETAILS))  # never failed
        s.add(IngestionTask(mal_id=2, resource=Resource.DETAILS, attempts=1))  # failed once
        await s.commit()

    class Adapter:
        request_count = 0
        retry_count = 0
        rate_limited_count = 0

        async def get_full(self, mal_id):
            return {"data": {"mal_id": mal_id}}

    async with pg_sessionmaker() as session, pg_sessionmaker() as metrics:
        job = FetchAnimeJob(
            Adapter(),
            TaskRepository(session),
            RawTenraiRepository(session),
            MetricsRepository(metrics),
        )
        summary = await job.fetch_from_tasks()

    assert (summary["done"], summary["done_first_try"]) == (2, 1)
    assert summary["first_try_tasks"] == 1  # only task 1 was on its first attempt
    async with pg_sessionmaker() as s:
        run = await MetricsRepository(s).latest_run("fetch_tasks")
    assert run.summary["done_first_try"] == 1
