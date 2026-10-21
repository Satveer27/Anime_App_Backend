from unittest.mock import create_autospec

import pytest
from sqlalchemy.exc import OperationalError
from structlog.testing import capture_logs

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.exceptions import (
    AccessDeniedError,
    CircuitOpenError,
    GoneError,
    PermanentError,
    RepeatedUnexpectedErrors,
    TransientError,
)
from app.ingestion.jobs.fetch_anime_job import (
    MAX_CONSECUTIVE_DENIED,
    MAX_CONSECUTIVE_UNEXPECTED,
    MAX_EPISODE_PAGES,
    PROGRESS_EVERY_TASKS,
    FetchAnimeJob,
    TaskOutcome,
)
from app.ingestion.models import IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository


class FakeAdapter:
    """Returns canned responses; a value that is an exception is raised instead."""

    def __init__(self, **responses):
        self.responses = responses
        self.episode_pages_requested = []
        self.rate_limited_count = 0
        self.request_count = 0
        self.retry_count = 0

    def _answer(self, name):
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_full(self, mal_id):
        return self._answer("full")

    async def get_statistics(self, mal_id):
        return self._answer("statistics")

    async def get_reviews(self, mal_id, page=1):
        return self._answer("reviews")

    async def get_episodes(self, mal_id, page=1):
        self.episode_pages_requested.append(page)
        value = self.responses["episodes"]
        if isinstance(value, Exception):
            raise value
        return value[page - 1] if isinstance(value, list) else value


@pytest.fixture
def task_repo(mocker):
    return mocker.create_autospec(TaskRepository, instance=True)


@pytest.fixture
def raw_repo(mocker):
    return mocker.create_autospec(RawTenraiRepository, instance=True)


@pytest.fixture
def metrics_repo(mocker):
    repo = mocker.create_autospec(MetricsRepository, instance=True)
    repo.start_run.return_value = 7
    return repo


def make_job(adapter, task_repo, raw_repo, resource=Resource.DETAILS, claim=True):
    task_repo.claim_next.return_value = (
        IngestionTask(mal_id=5, resource=resource, attempts=0) if claim else None
    )
    metrics = create_autospec(MetricsRepository, instance=True)
    return FetchAnimeJob(adapter, task_repo, raw_repo, metrics)


@pytest.mark.parametrize(
    ("resource", "response_key"),
    [
        (Resource.DETAILS, "full"),
        (Resource.STATISTICS, "statistics"),
        (Resource.REVIEWS, "reviews"),
    ],
)
async def test_single_page_resources_store_the_response_and_mark_done(
    task_repo, raw_repo, resource, response_key
):
    body = {"data": {"mal_id": 5}}
    job = make_job(FakeAdapter(**{response_key: body}), task_repo, raw_repo, resource)

    await job.fetch_job_from_tasks()

    raw_repo.upsert.assert_awaited_once_with(mal_id=5, resource=resource, payload=body)
    task_repo.mark_done.assert_awaited_once()
    task_repo.commit.assert_awaited_once()


async def test_episodes_are_stored_as_a_list_of_pages_following_has_next_page(task_repo, raw_repo):
    pages = [
        {"data": [1], "pagination": {"has_next_page": True}},
        {"data": [2], "pagination": {"has_next_page": True}},
        {"data": [3], "pagination": {"has_next_page": False}},
    ]
    adapter = FakeAdapter(episodes=pages)

    await make_job(adapter, task_repo, raw_repo, Resource.EPISODES).fetch_job_from_tasks()

    assert adapter.episode_pages_requested == [1, 2, 3]
    assert raw_repo.upsert.await_args.kwargs["payload"] == pages  # a list, not a JSON string
    task_repo.mark_done.assert_awaited_once()


async def test_episodes_without_pagination_are_a_single_page(task_repo, raw_repo):
    adapter = FakeAdapter(episodes={"data": []})

    await make_job(adapter, task_repo, raw_repo, Resource.EPISODES).fetch_job_from_tasks()

    assert adapter.episode_pages_requested == [1]
    assert raw_repo.upsert.await_args.kwargs["payload"] == [{"data": []}]


async def test_a_failing_episodes_page_stores_nothing_and_marks_failed(task_repo, raw_repo):
    class FailsOnPageTwo(FakeAdapter):
        async def get_episodes(self, mal_id, page=1):
            if page == 2:
                raise TransientError("boom", status_code=503)
            return {"data": [], "pagination": {"has_next_page": True}}

    await make_job(FailsOnPageTwo(), task_repo, raw_repo, Resource.EPISODES).fetch_job_from_tasks()

    raw_repo.upsert.assert_not_awaited()
    task_repo.mark_failed.assert_awaited_once()
    task_repo.commit.assert_awaited_once()


async def test_runaway_episode_paging_is_stopped_and_marked_dead(task_repo, raw_repo):
    adapter = FakeAdapter(episodes={"data": [], "pagination": {"has_next_page": True}})

    await make_job(adapter, task_repo, raw_repo, Resource.EPISODES).fetch_job_from_tasks()

    assert len(adapter.episode_pages_requested) == MAX_EPISODE_PAGES
    raw_repo.upsert.assert_not_awaited()
    task_repo.mark_dead.assert_awaited_once()


async def test_404_marks_gone_and_commits(task_repo, raw_repo):
    adapter = FakeAdapter(full=GoneError("nope", status_code=404))

    await make_job(adapter, task_repo, raw_repo).fetch_job_from_tasks()

    task_repo.mark_gone.assert_awaited_once()
    raw_repo.upsert.assert_not_awaited()
    task_repo.commit.assert_awaited_once()


async def test_other_4xx_marks_dead_with_a_string_error_not_gone(task_repo, raw_repo):
    adapter = FakeAdapter(full=PermanentError("bad request", status_code=400))

    await make_job(adapter, task_repo, raw_repo).fetch_job_from_tasks()

    task_repo.mark_gone.assert_not_awaited()
    task = task_repo.mark_dead.await_args.args[0]
    error = task_repo.mark_dead.await_args.args[1]
    assert task.mal_id == 5
    assert error == "PermanentError: bad request"
    task_repo.commit.assert_awaited_once()


async def test_transient_error_marks_failed_with_a_string_error(task_repo, raw_repo):
    adapter = FakeAdapter(full=TransientError("503 after retries", status_code=503))

    await make_job(adapter, task_repo, raw_repo).fetch_job_from_tasks()

    error = task_repo.mark_failed.await_args.args[1]
    assert isinstance(error, str)
    assert error == "TransientError: 503 after retries"
    task_repo.commit.assert_awaited_once()


async def test_circuit_open_releases_the_task_and_stops_without_using_an_attempt(
    task_repo, raw_repo
):
    adapter = FakeAdapter(full=CircuitOpenError(30))

    with pytest.raises(CircuitOpenError):
        await make_job(adapter, task_repo, raw_repo).fetch_job_from_tasks()

    task_repo.rollback.assert_awaited_once()
    task_repo.mark_failed.assert_not_awaited()
    task_repo.mark_dead.assert_not_awaited()
    task_repo.commit.assert_not_awaited()


async def test_access_denied_marks_the_task_failed_and_continues(task_repo, raw_repo):
    adapter = FakeAdapter(full=AccessDeniedError("403 /anime/1/full", status_code=403))

    outcome = await make_job(adapter, task_repo, raw_repo).fetch_job_from_tasks()

    assert outcome.status is TaskStatus.FAILED
    task_repo.mark_failed.assert_awaited_once()
    task_repo.mark_dead.assert_not_awaited()
    task_repo.commit.assert_awaited_once()


async def test_repeated_access_denied_stops_the_loop(task_repo, raw_repo, metrics_repo):
    tasks = [task(i) for i in range(MAX_CONSECUTIVE_DENIED + 5)]
    adapter = FakeAdapter(full=AccessDeniedError("403", status_code=403))
    job = loop_job(adapter, task_repo, raw_repo, metrics_repo, tasks)

    with pytest.raises(AccessDeniedError):
        await job.fetch_from_tasks()

    assert task_repo.mark_failed.await_count == MAX_CONSECUTIVE_DENIED
    recorded = metrics_repo.finish_run.await_args.args[2]
    assert recorded["tasks"] == MAX_CONSECUTIVE_DENIED
    assert recorded["stopped_by"] == "AccessDeniedError"


async def test_a_success_resets_the_access_denied_count(task_repo, raw_repo, metrics_repo):
    class Adapter(FakeAdapter):
        async def get_full(self, mal_id):
            if mal_id % MAX_CONSECUTIVE_DENIED == 0:
                return {"data": {}}
            raise AccessDeniedError("403", status_code=403)

    tasks = [task(i) for i in range(1, MAX_CONSECUTIVE_DENIED * 3)]
    job = loop_job(Adapter(), task_repo, raw_repo, metrics_repo, tasks)

    summary = await job.fetch_from_tasks()

    assert summary["tasks"] == len(tasks)
    assert summary["done"] == 2


async def test_database_failure_rolls_back_and_reraises_without_marking(task_repo, raw_repo):
    raw_repo.upsert.side_effect = OperationalError("INSERT", {}, Exception("connection lost"))
    job = make_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo)

    with pytest.raises(OperationalError):
        await job.fetch_job_from_tasks()

    task_repo.rollback.assert_awaited_once()
    task_repo.mark_failed.assert_not_awaited()
    task_repo.mark_done.assert_not_awaited()
    task_repo.commit.assert_not_awaited()


async def test_a_bug_on_one_tasks_data_fails_that_task_and_the_run_continues(task_repo, raw_repo):
    # episodes come back as a list, not a dict: our code breaks on .get
    job = make_job(
        FakeAdapter(episodes=["not", "a", "dict"]), task_repo, raw_repo, Resource.EPISODES
    )

    await job.fetch_job_from_tasks()

    raw_repo.upsert.assert_not_awaited()
    error = task_repo.mark_failed.await_args.args[1]
    assert error.startswith("AttributeError")
    task_repo.commit.assert_awaited_once()
    task_repo.rollback.assert_not_awaited()


async def test_a_success_resets_the_unexpected_error_count(task_repo, raw_repo):
    job = make_job(FakeAdapter(episodes="broken"), task_repo, raw_repo, Resource.EPISODES)
    for _ in range(MAX_CONSECUTIVE_UNEXPECTED - 1):
        await job.fetch_job_from_tasks()
    assert job._unexpected_in_a_row == MAX_CONSECUTIVE_UNEXPECTED - 1

    job.tenrai_adapter = FakeAdapter(full={"data": {}})
    task_repo.claim_next.return_value = IngestionTask(
        mal_id=6, resource=Resource.DETAILS, attempts=0
    )
    await job.fetch_job_from_tasks()

    assert job._unexpected_in_a_row == 0


async def test_unexpected_failure_logs_the_key_the_count_and_the_response(task_repo, raw_repo):
    raw_repo.upsert.side_effect = ValueError("boom")
    body = {"data": {"title": "Some Title"}}

    with capture_logs() as logs:
        await make_job(FakeAdapter(full=body), task_repo, raw_repo).fetch_job_from_tasks()

    (log,) = [log for log in logs if log["event"] == "task_failed_unexpected"]
    assert (log["mal_id"], log["resource"], log["consecutive"]) == (5, "details", 1)
    assert "Some Title" in log["response"]


async def test_a_resource_with_no_fetcher_is_marked_dead(task_repo, raw_repo):
    job = make_job(FakeAdapter(), task_repo, raw_repo, Resource.CHARACTERS)

    await job.fetch_job_from_tasks()

    task_repo.mark_dead.assert_awaited_once()
    assert "no fetcher" in task_repo.mark_dead.await_args.args[1]
    raw_repo.upsert.assert_not_awaited()


async def test_no_claimable_task_returns_none_and_touches_nothing(task_repo, raw_repo):
    job = make_job(FakeAdapter(), task_repo, raw_repo, claim=False)

    assert await job.fetch_job_from_tasks() is None

    raw_repo.upsert.assert_not_awaited()
    task_repo.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"data": {}}, TaskStatus.DONE),
        (GoneError("x", status_code=404), TaskStatus.GONE),
        (PermanentError("x", status_code=400), TaskStatus.DEAD),
        (TransientError("x", status_code=503), TaskStatus.FAILED),
    ],
)
async def test_each_task_returns_its_resource_and_outcome(task_repo, raw_repo, response, expected):
    job = make_job(FakeAdapter(full=response), task_repo, raw_repo)

    assert await job.fetch_job_from_tasks() == TaskOutcome(Resource.DETAILS, expected, True)


# --- the loop and its run record ----------------------------------------------------------


def queue(**counts):
    return {status.value: counts.get(status.value, 0) for status in TaskStatus}


def loop_job(adapter, task_repo, raw_repo, metrics_repo, tasks):
    """tasks: what claim_next hands out in turn; the list running out means an empty queue."""
    task_repo.claim_next.side_effect = [*tasks, None]
    task_repo.count_by_status.return_value = queue(failed=2, done=len(tasks))
    return FetchAnimeJob(adapter, task_repo, raw_repo, metrics_repo)


def task(mal_id, resource=Resource.DETAILS):
    return IngestionTask(mal_id=mal_id, resource=resource, attempts=0)


async def test_loop_drains_the_queue_and_records_a_succeeded_run(task_repo, raw_repo, metrics_repo):
    adapter = FakeAdapter(full={"data": {}}, statistics={"data": {}})
    job = loop_job(
        adapter,
        task_repo,
        raw_repo,
        metrics_repo,
        [task(1), task(2, Resource.STATISTICS), task(3)],
    )

    summary = await job.fetch_from_tasks()

    assert summary["tasks"] == 3
    assert summary["done"] == 3
    assert summary["failed"] == summary["dead"] == summary["gone"] == 0
    assert summary["by_resource"]["details"]["count"] == 2
    assert summary["by_resource"]["statistics"]["count"] == 1
    assert summary["stopped_by"] is None
    assert summary["queue_after"] == queue(failed=2, done=3)
    for key in ("elapsed_seconds", "tasks_per_minute", "avg_task_ms", "max_task_seconds"):
        assert key in summary
    metrics_repo.start_run.assert_awaited_once_with("fetch_tasks")
    run_id, status, recorded, error = metrics_repo.finish_run.await_args.args
    assert (run_id, status, error) == (7, RunStatus.SUCCEEDED, None)
    assert recorded["tasks"] == 3


async def test_loop_counts_every_kind_of_outcome(task_repo, raw_repo, metrics_repo):
    errors = {
        1: GoneError("x", status_code=404),
        2: PermanentError("x", status_code=400),
        3: TransientError("x", status_code=503),
    }

    class Adapter(FakeAdapter):
        async def get_full(self, mal_id):
            if mal_id in errors:
                raise errors[mal_id]
            return {"data": {}}

    job = loop_job(
        Adapter(), task_repo, raw_repo, metrics_repo, [task(1), task(2), task(3), task(4)]
    )

    summary = await job.fetch_from_tasks()

    assert (summary["gone"], summary["dead"], summary["failed"], summary["done"]) == (1, 1, 1, 1)
    assert summary["tasks"] == 4


async def test_the_final_log_says_how_many_tasks_are_still_failed(
    task_repo, raw_repo, metrics_repo
):
    job = loop_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo, metrics_repo, [task(1)])

    with capture_logs() as logs:
        await job.fetch_from_tasks()

    (finished,) = [log for log in logs if log["event"] == "fetch_run_finished"]
    assert finished["queue_after"]["failed"] == 2


async def test_circuit_open_stops_the_loop_and_records_a_failed_run(
    task_repo, raw_repo, metrics_repo
):
    class Adapter(FakeAdapter):
        async def get_full(self, mal_id):
            if mal_id == 2:
                raise CircuitOpenError(30)
            return {"data": {}}

    job = loop_job(Adapter(), task_repo, raw_repo, metrics_repo, [task(1), task(2), task(3)])

    with pytest.raises(CircuitOpenError):
        await job.fetch_from_tasks()

    _, status, recorded, error = metrics_repo.finish_run.await_args.args
    assert status is RunStatus.FAILED
    assert recorded["tasks"] == 1  # only anime 1 finished
    assert recorded["stopped_by"] == "CircuitOpenError"
    assert recorded["queue_after"] == queue(failed=2, done=3)
    assert error.startswith("CircuitOpenError")


async def test_repeated_unexpected_errors_stop_the_loop(task_repo, raw_repo, metrics_repo):
    tasks = [task(i, Resource.EPISODES) for i in range(MAX_CONSECUTIVE_UNEXPECTED + 5)]
    job = loop_job(FakeAdapter(episodes="broken"), task_repo, raw_repo, metrics_repo, tasks)

    with pytest.raises(RepeatedUnexpectedErrors):
        await job.fetch_from_tasks()

    assert task_repo.mark_failed.await_count == MAX_CONSECUTIVE_UNEXPECTED
    recorded = metrics_repo.finish_run.await_args.args[2]
    assert recorded["tasks"] == MAX_CONSECUTIVE_UNEXPECTED
    assert recorded["failed"] == MAX_CONSECUTIVE_UNEXPECTED
    assert recorded["stopped_by"] == "RepeatedUnexpectedErrors"


async def test_failing_to_record_the_run_does_not_mask_the_result(
    task_repo, raw_repo, metrics_repo
):
    metrics_repo.finish_run.side_effect = RuntimeError("db gone")
    job = loop_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo, metrics_repo, [task(1)])

    summary = await job.fetch_from_tasks()

    assert summary["tasks"] == 1


async def test_an_unreadable_queue_does_not_break_the_run(task_repo, raw_repo, metrics_repo):
    job = loop_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo, metrics_repo, [task(1)])
    task_repo.count_by_status.side_effect = RuntimeError("db gone")

    summary = await job.fetch_from_tasks()

    assert summary["queue_after"] == {}


async def test_the_loop_resets_the_unexpected_count_at_the_start_of_each_run(
    task_repo, raw_repo, metrics_repo
):
    job = loop_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo, metrics_repo, [task(1)])
    job._unexpected_in_a_row = MAX_CONSECUTIVE_UNEXPECTED  # left over from an earlier run

    summary = await job.fetch_from_tasks()

    assert summary["tasks"] == 1


async def test_progress_is_logged_every_so_many_tasks(task_repo, raw_repo, metrics_repo):
    tasks = [task(i) for i in range(PROGRESS_EVERY_TASKS + 1)]
    job = loop_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo, metrics_repo, tasks)

    with capture_logs() as logs:
        await job.fetch_from_tasks()

    assert len([log for log in logs if log["event"] == "fetch_run_progress"]) == 1


# --- first-try metric -----------------------------------------------------------------------


async def test_an_outcome_says_whether_it_was_the_tasks_first_try(task_repo, raw_repo):
    first = make_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo)
    assert (await first.fetch_job_from_tasks()).first_try is True

    retried = make_job(FakeAdapter(full={"data": {}}), task_repo, raw_repo)
    task_repo.claim_next.return_value = IngestionTask(
        mal_id=5, resource=Resource.DETAILS, attempts=2
    )
    assert (await retried.fetch_job_from_tasks()).first_try is False


async def test_the_summary_counts_the_tasks_that_succeeded_on_the_first_try(
    task_repo, raw_repo, metrics_repo
):
    def attempt(mal_id, attempts):
        return IngestionTask(mal_id=mal_id, resource=Resource.DETAILS, attempts=attempts)

    class Adapter(FakeAdapter):
        async def get_full(self, mal_id):
            if mal_id == 4:
                raise TransientError("503", status_code=503)  # a first-try failure
            return {"data": {}}

    tasks = [attempt(1, 0), attempt(2, 0), attempt(3, 2), attempt(4, 0)]
    task_repo.claim_next.side_effect = [*tasks, None]
    task_repo.count_by_status.return_value = queue()
    job = FetchAnimeJob(Adapter(), task_repo, raw_repo, metrics_repo)

    summary = await job.fetch_from_tasks()

    assert summary["done"] == 3
    assert summary["done_first_try"] == 2  # task 3 needed earlier attempts, task 4 failed
    assert summary["first_try_tasks"] == 3  # tasks 1, 2 and 4 had never failed before
    assert summary["failed"] == 1
