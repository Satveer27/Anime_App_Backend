from unittest.mock import AsyncMock

import pytest

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.jobs.discovery_job import JOB_NAME, DiscoveryJob
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository


class FakeAdapter:
    def __init__(self, pages: dict[int, dict] | None = None, fail_on: int | None = None):
        self.pages = pages or {}
        self.fail_on = fail_on
        self.calls: list[tuple[int, dict]] = []
        self.rate_limited_count = 0
        self.request_count = 0

    async def list_anime(self, page: int = 1, **filters):
        self.calls.append((page, filters))
        if page == self.fail_on:
            raise RuntimeError("tenrai down")
        return self.pages[page]


@pytest.fixture
def task_repo(mocker):
    repo = mocker.create_autospec(TaskRepository, instance=True)
    repo.insert_tasks.side_effect = lambda rows: len(rows)
    return repo


@pytest.fixture
def metrics_repo(mocker):
    repo = mocker.create_autospec(MetricsRepository, instance=True)
    repo.start_run.return_value = 7
    return repo


TWO_PAGES = {
    1: {
        "pagination": {"has_next_page": True},
        "data": [{"mal_id": 1, "type": "TV"}, {"type": "TV"}, {"mal_id": 2, "type": "Music"}],
    },
    2: {"pagination": {"has_next_page": False}, "data": [{"mal_id": 3, "type": "Movie"}]},
}


async def test_run_creates_four_tasks_per_kept_anime_and_records_success(task_repo, metrics_repo):
    adapter = FakeAdapter(TWO_PAGES)

    summary = await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall()

    assert summary["pages"] == 2
    assert summary["anime_seen"] == 4
    assert summary["anime_skipped"] == 1
    assert summary["anime_skipped_type"] == 1
    assert summary["tasks_inserted"] == 8
    inserted = [row for call in task_repo.insert_tasks.await_args_list for row in call.args[0]]
    assert {resource for _, resource in inserted} == {
        Resource.DETAILS,
        Resource.STATISTICS,
        Resource.REVIEWS,
        Resource.EPISODES,
    }
    assert task_repo.commit.await_count == 2

    metrics_repo.start_run.assert_awaited_once_with(JOB_NAME)
    run_id, status, recorded, error = metrics_repo.finish_run.await_args.args
    assert (run_id, status, error) == (7, RunStatus.SUCCEEDED, None)
    assert recorded["pages"] == 2
    assert "elapsed_seconds" in recorded and "requests_per_minute" in recorded


async def test_failed_page_records_failed_run_and_reraises(task_repo, metrics_repo):
    adapter = FakeAdapter(TWO_PAGES, fail_on=2)

    with pytest.raises(RuntimeError, match="tenrai down"):
        await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall()

    assert task_repo.commit.await_count == 1  # page 1 stays committed
    run_id, status, recorded = metrics_repo.finish_run.await_args.args[:3]
    assert (run_id, status) == (7, RunStatus.FAILED)
    assert recorded["pages"] == 1
    assert "tenrai down" in metrics_repo.finish_run.await_args.args[3]


async def test_failure_to_record_run_does_not_mask_job_result(task_repo, metrics_repo):
    metrics_repo.finish_run = AsyncMock(side_effect=RuntimeError("db gone"))

    summary = await DiscoveryJob(FakeAdapter(TWO_PAGES), task_repo, metrics_repo).run_job_overall()

    assert summary["pages"] == 2


async def test_resume_from_page_counts_pages_run(task_repo, metrics_repo):
    adapter = FakeAdapter(TWO_PAGES)

    summary = await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_overall(page=2)

    assert [page for page, _ in adapter.calls] == [2]
    assert summary["pages"] == 1
