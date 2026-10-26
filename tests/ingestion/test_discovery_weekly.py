from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.catalogue.repository import CatalogueRepository
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.jobs.discovery_job import (
    ACTIVE_CATALOGUE_STATUSES,
    TASKS_PER_STATEMENT,
    DiscoveryJob,
)
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository

RESOURCES = (Resource.DETAILS, Resource.STATISTICS)


class Adapter:
    """Two list pages for every status; fail_on = (status, page) raises there."""

    request_count = 0
    retry_count = 0
    rate_limited_count = 0

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls: list[tuple[str, int]] = []

    async def list_anime(self, page=1, **filters):
        status = filters["status"]
        self.calls.append((status, page))
        if self.fail_on == (status, page):
            raise RuntimeError("tenrai down")
        return {
            "pagination": {"has_next_page": page < 2},
            "data": [{"mal_id": page, "type": "TV"}],
        }


@pytest.fixture
def task_repo(mocker):
    repo = mocker.create_autospec(TaskRepository, instance=True)
    repo.insert_or_reset_tasks.side_effect = lambda rows: (0, len(rows))
    return repo


@pytest.fixture
def metrics_repo(mocker):
    repo = mocker.create_autospec(MetricsRepository, instance=True)
    repo.start_run.return_value = 7
    repo.latest_run.return_value = None
    return repo


@pytest.fixture
def catalogue_repo(mocker):
    repo = mocker.create_autospec(CatalogueRepository, instance=True)
    repo.anime_ids_with_status.return_value = {30, 10, 20}
    return repo


def failed_run(resume_page, age=timedelta(minutes=10)):
    return SimpleNamespace(
        status=RunStatus.FAILED,
        summary={"resume_page": resume_page},
        started_at=datetime.now(UTC) - age,
    )


async def test_it_lists_airing_then_upcoming_from_tenrai(task_repo, metrics_repo, catalogue_repo):
    adapter = Adapter()
    job = DiscoveryJob(adapter, task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    assert adapter.calls == [("airing", 1), ("airing", 2), ("upcoming", 1), ("upcoming", 2)]


async def test_each_list_is_recorded_under_its_own_job_name(
    task_repo, metrics_repo, catalogue_repo
):
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    names = [call.args[0] for call in metrics_repo.start_run.await_args_list]
    assert names == ["discovery_weekly_airing", "discovery_weekly_upcoming"]


async def test_the_catalogue_step_asks_for_the_stored_wording_not_tenrais(
    task_repo, metrics_repo, catalogue_repo
):
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    catalogue_repo.anime_ids_with_status.assert_awaited_once_with(ACTIVE_CATALOGUE_STATUSES)
    assert ACTIVE_CATALOGUE_STATUSES == ("Currently Airing", "Not yet aired")


async def test_the_catalogue_step_resets_every_resource_of_every_active_anime_once(
    task_repo, metrics_repo, catalogue_repo
):
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo, catalogue_repo)

    summary = await job.run_job_weekly(resources=RESOURCES)

    catalogue_rows = task_repo.insert_or_reset_tasks.await_args_list[-1].args[0]
    assert catalogue_rows == [
        (10, Resource.DETAILS),
        (10, Resource.STATISTICS),
        (20, Resource.DETAILS),
        (20, Resource.STATISTICS),
        (30, Resource.DETAILS),
        (30, Resource.STATISTICS),
    ]
    assert summary["catalogue"] == {"anime": 3, "tasks_inserted": 0, "tasks_reset": 6}
    task_repo.commit.assert_awaited()


async def test_a_big_catalogue_is_sent_in_chunks_that_fit_one_statement(
    task_repo, metrics_repo, catalogue_repo
):
    catalogue_repo.anime_ids_with_status.return_value = set(range(1, 3001))  # 12,000 tasks
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo, catalogue_repo)

    summary = await job.run_job_weekly()  # all four resources

    # the first four calls are the two list pages of each status
    chunk_sizes = [len(c.args[0]) for c in task_repo.insert_or_reset_tasks.await_args_list[4:]]
    assert chunk_sizes == [TASKS_PER_STATEMENT, TASKS_PER_STATEMENT, 2000]
    assert summary["catalogue"]["tasks_reset"] == 12_000


async def test_no_active_anime_in_the_catalogue_sends_nothing(
    task_repo, metrics_repo, catalogue_repo
):
    catalogue_repo.anime_ids_with_status.return_value = set()
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo, catalogue_repo)

    summary = await job.run_job_weekly(resources=RESOURCES)

    assert summary["catalogue"] == {"anime": 0, "tasks_inserted": 0, "tasks_reset": 0}
    assert task_repo.insert_or_reset_tasks.await_count == 4  # only the four list pages


async def test_each_list_resumes_from_its_own_failed_run_only(
    task_repo, metrics_repo, catalogue_repo
):
    runs = {"discovery_weekly_upcoming": failed_run(2)}  # airing has no failed run

    async def latest_run(name):
        return runs.get(name)

    metrics_repo.latest_run.side_effect = latest_run
    adapter = Adapter()
    job = DiscoveryJob(adapter, task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    # airing starts at page 1; upcoming picks up at its own saved page 2, not airing's
    assert adapter.calls == [("airing", 1), ("airing", 2), ("upcoming", 2)]


async def test_a_failed_list_stops_the_week_before_the_catalogue_step(
    task_repo, metrics_repo, catalogue_repo
):
    job = DiscoveryJob(Adapter(fail_on=("upcoming", 2)), task_repo, metrics_repo, catalogue_repo)

    with pytest.raises(RuntimeError, match="tenrai down"):
        await job.run_job_weekly(resources=RESOURCES)

    catalogue_repo.anime_ids_with_status.assert_not_awaited()
    failed = metrics_repo.finish_run.await_args_list[-1].args
    assert failed[1] == RunStatus.FAILED
    assert failed[2]["resume_page"] == 2


async def test_it_refuses_to_run_without_a_catalogue_repository(task_repo, metrics_repo):
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo)

    with pytest.raises(ValueError, match="catalogue repository"):
        await job.run_job_weekly()


async def test_the_three_argument_construction_still_works(task_repo, metrics_repo):
    job = DiscoveryJob(Adapter(), task_repo, metrics_repo)

    assert job.catalogue_repository is None


async def test_a_failure_from_an_earlier_week_is_not_resumed(
    task_repo, metrics_repo, catalogue_repo
):
    """Last Sunday's run died at page 2. This Sunday must start both lists at page 1."""
    runs = {
        "discovery_weekly_airing": failed_run(2, age=timedelta(days=7)),
        "discovery_weekly_upcoming": failed_run(2, age=timedelta(days=7)),
    }

    async def latest_run(name):
        return runs.get(name)

    metrics_repo.latest_run.side_effect = latest_run
    adapter = Adapter()
    job = DiscoveryJob(adapter, task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    assert adapter.calls == [("airing", 1), ("airing", 2), ("upcoming", 1), ("upcoming", 2)]


async def test_a_failure_from_a_few_hours_ago_is_still_resumed(
    task_repo, metrics_repo, catalogue_repo
):
    runs = {"discovery_weekly_upcoming": failed_run(2, age=timedelta(hours=5))}

    async def latest_run(name):
        return runs.get(name)

    metrics_repo.latest_run.side_effect = latest_run
    adapter = Adapter()
    job = DiscoveryJob(adapter, task_repo, metrics_repo, catalogue_repo)

    await job.run_job_weekly(resources=RESOURCES)

    assert adapter.calls[-1] == ("upcoming", 2)
