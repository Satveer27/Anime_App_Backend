import pytest

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.jobs.discovery_job import LIST_TYPES, DiscoveryJob
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository


class FakeAdapter:
    def __init__(self, pages, fail_on=None):
        self.pages = pages
        self.fail_on = fail_on
        self.calls = []
        self.rate_limited_count = 0
        self.request_count = 0
        self.retry_count = 0

    async def list_anime(self, page=1, **filters):
        self.calls.append((page, filters))
        if page == self.fail_on:
            raise RuntimeError("tenrai down")
        return self.pages[page]


@pytest.fixture
def task_repo(mocker):
    repo = mocker.create_autospec(TaskRepository, instance=True)
    repo.insert_tasks.side_effect = lambda rows: len(rows)
    repo.insert_or_reset_tasks.side_effect = lambda rows: (len(rows) - 1, 1)
    return repo


@pytest.fixture
def metrics_repo(mocker):
    repo = mocker.create_autospec(MetricsRepository, instance=True)
    repo.start_run.return_value = 7
    return repo


PAGES = {
    1: {
        "pagination": {"has_next_page": True},
        "data": [{"mal_id": 1, "type": "TV"}, {"mal_id": 2, "type": "Movie"}],
    },
    2: {"pagination": {"has_next_page": False}, "data": [{"mal_id": 3, "type": "OVA"}]},
}


async def test_refresh_resets_existing_tasks_and_never_uses_do_nothing_insert(
    task_repo, metrics_repo
):
    adapter = FakeAdapter(PAGES)

    summary = await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_refresh(
        "discovery_weekly", {"status": "airing"}, resources=(Resource.DETAILS, Resource.STATISTICS)
    )

    task_repo.insert_tasks.assert_not_awaited()
    assert task_repo.insert_or_reset_tasks.await_count == 2
    rows = task_repo.insert_or_reset_tasks.await_args_list[0].args[0]
    assert rows == [
        (1, Resource.DETAILS),
        (1, Resource.STATISTICS),
        (2, Resource.DETAILS),
        (2, Resource.STATISTICS),
    ]
    # the stub reports 1 reset per page and the rest inserted: (3, 1) then (1, 1)
    assert summary["tasks_inserted"] == 4
    assert summary["tasks_reset"] == 2
    assert summary["tasks_unchanged"] == 0
    metrics_repo.start_run.assert_awaited_once_with("discovery_weekly")


async def test_refresh_sends_caller_filters_alongside_the_type_filter(task_repo, metrics_repo):
    adapter = FakeAdapter(PAGES)

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_refresh(
        "discovery_weekly", {"status": "airing"}
    )

    assert adapter.calls[0] == (1, {"type": LIST_TYPES, "status": "airing"})


async def test_overall_run_keeps_do_nothing_insert(task_repo, metrics_repo):
    await DiscoveryJob(FakeAdapter(PAGES), task_repo, metrics_repo).run_job_overall()

    task_repo.insert_or_reset_tasks.assert_not_awaited()
    assert task_repo.insert_tasks.await_count == 2


async def test_unchanged_rows_are_counted(mocker, task_repo, metrics_repo):
    task_repo.insert_or_reset_tasks.side_effect = lambda rows: (0, 0)  # e.g. all dead/gone

    summary = await DiscoveryJob(FakeAdapter(PAGES), task_repo, metrics_repo).run_job_refresh(
        "discovery_weekly", {}, resources=(Resource.DETAILS,)
    )

    assert summary["tasks_unchanged"] == 3


async def test_anime_listed_twice_on_a_page_is_sent_once(task_repo, metrics_repo):
    pages = {
        1: {
            "pagination": {"has_next_page": False},
            "data": [{"mal_id": 1, "type": "TV"}, {"mal_id": 1, "type": "TV"}],
        }
    }

    await DiscoveryJob(FakeAdapter(pages), task_repo, metrics_repo).run_job_refresh(
        "discovery_weekly", {}, resources=(Resource.DETAILS,)
    )

    assert task_repo.insert_or_reset_tasks.await_args.args[0] == [(1, Resource.DETAILS)]


async def test_failed_run_stores_the_page_to_resume_from(task_repo, metrics_repo):
    adapter = FakeAdapter(PAGES, fail_on=2)

    with pytest.raises(RuntimeError):
        await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall()

    _, status, recorded, _ = metrics_repo.finish_run.await_args.args
    assert status is RunStatus.FAILED
    assert recorded["start_page"] == 1
    assert recorded["pages"] == 1
    assert recorded["failed_page"] == 2
    assert recorded["resume_page"] == 2


async def test_resume_page_works_from_a_later_start_page(task_repo, metrics_repo):
    adapter = FakeAdapter(PAGES, fail_on=2)

    with pytest.raises(RuntimeError):
        await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall(page=1)
    resume = metrics_repo.finish_run.await_args.args[2]["resume_page"]

    adapter.fail_on = None
    summary = await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall(page=resume)

    assert [page for page, _ in adapter.calls] == [1, 2, 2]
    assert summary["start_page"] == 2
    assert summary["pages"] == 1
    assert summary["resume_page"] is None


async def test_successful_run_has_no_resume_page(task_repo, metrics_repo):
    summary = await DiscoveryJob(FakeAdapter(PAGES), task_repo, metrics_repo).run_job_overall()

    assert summary["resume_page"] is None
    assert summary["start_page"] == 1
    assert metrics_repo.finish_run.await_args.args[2]["resume_page"] is None
