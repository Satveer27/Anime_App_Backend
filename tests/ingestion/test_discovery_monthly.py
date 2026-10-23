from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.jobs.discovery_job import (
    MONTHLY_RESOURCES,
    POPULAR_ANIME,
    POPULAR_PAGES,
    DiscoveryJob,
)
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository


class Adapter:
    """The year list ends after `year_pages` pages; the members-ordered list never ends."""

    request_count = 0
    retry_count = 0
    rate_limited_count = 0

    def __init__(self, year_pages=2, popular_ends_at=None):
        self.year_pages = year_pages
        self.popular_ends_at = popular_ends_at
        self.calls: list[tuple[str, int, dict]] = []

    async def list_anime(self, page=1, **filters):
        popular = filters.get("order_by") == "members"
        self.calls.append(("popular" if popular else "years", page, filters))
        if popular:
            more = page != self.popular_ends_at
        else:
            more = page < self.year_pages
        return {"pagination": {"has_next_page": more}, "data": [{"mal_id": page, "type": "TV"}]}

    def pages(self, which):
        return [page for name, page, _ in self.calls if name == which]


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


def failed_run(resume_page):
    return SimpleNamespace(status=RunStatus.FAILED, summary={"resume_page": resume_page})


async def test_it_asks_for_last_year_onwards_then_the_most_followed(task_repo, metrics_repo):
    adapter = Adapter()

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_monthly()

    last_year = str(datetime.now(UTC).year - 1)
    year_filters = adapter.calls[0][2]
    popular_filters = [f for name, _, f in adapter.calls if name == "popular"][0]
    assert year_filters["start_date"] == last_year
    assert (popular_filters["order_by"], popular_filters["sort"]) == ("members", "desc")


async def test_the_popular_list_stops_after_the_page_cap_though_tenrai_has_more(
    task_repo, metrics_repo
):
    adapter = Adapter()  # the members list claims a next page forever

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_monthly()

    assert adapter.pages("popular") == list(range(1, POPULAR_PAGES + 1))
    assert POPULAR_PAGES * 100 == POPULAR_ANIME == 500


async def test_a_list_shorter_than_the_cap_stops_where_tenrai_says_it_ends(task_repo, metrics_repo):
    adapter = Adapter(popular_ends_at=2)

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_monthly()

    assert adapter.pages("popular") == [1, 2]


async def test_the_year_list_follows_tenrai_with_no_cap(task_repo, metrics_repo):
    adapter = Adapter(year_pages=12)

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_monthly()

    assert adapter.pages("years") == list(range(1, 13))


async def test_monthly_skips_episodes_by_default(task_repo, metrics_repo):
    await DiscoveryJob(Adapter(), task_repo, metrics_repo).run_job_monthly()

    sent = {
        resource
        for call in task_repo.insert_or_reset_tasks.await_args_list
        for _, resource in call.args[0]
    }
    assert sent == set(MONTHLY_RESOURCES)
    assert Resource.EPISODES not in sent


async def test_each_list_is_recorded_under_its_own_job_name(task_repo, metrics_repo):
    await DiscoveryJob(Adapter(), task_repo, metrics_repo).run_job_monthly()

    names = [call.args[0] for call in metrics_repo.start_run.await_args_list]
    assert names == ["discovery_monthly_recent_years", "discovery_monthly_popular"]


async def test_a_capped_list_resumes_from_its_saved_page_and_still_ends_at_the_cap(
    task_repo, metrics_repo
):
    runs = {"discovery_monthly_popular": failed_run(4)}

    async def latest_run(name):
        return runs.get(name)

    metrics_repo.latest_run.side_effect = latest_run
    adapter = Adapter()

    await DiscoveryJob(adapter, task_repo, metrics_repo).run_job_monthly()

    assert adapter.pages("popular") == [4, 5]  # not 4 .. 8: the cap counts page numbers
    assert adapter.pages("years")[0] == 1  # the other list is not affected


async def test_it_returns_a_summary_per_list(task_repo, metrics_repo):
    summary = await DiscoveryJob(Adapter(), task_repo, metrics_repo).run_job_monthly()

    assert set(summary) == {"recent_years", "popular"}
    assert summary["popular"]["pages"] == POPULAR_PAGES
