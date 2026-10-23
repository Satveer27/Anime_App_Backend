import pytest
from sqlalchemy import select

from app.catalogue.models import Anime
from app.catalogue.repository import CatalogueRepository
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.jobs.discovery_job import DiscoveryJob
from app.ingestion.models import IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository

pytestmark = pytest.mark.postgres

RESOURCES = (Resource.DETAILS, Resource.EPISODES)


class EmptyLists:
    """Tenrai's airing and upcoming lists are empty, so only the catalogue step has work."""

    request_count = 0
    retry_count = 0
    rate_limited_count = 0

    async def list_anime(self, page=1, **filters):
        return {"pagination": {"has_next_page": False}, "data": []}


async def statuses(sessionmaker) -> dict[tuple[int, Resource], TaskStatus]:
    async with sessionmaker() as s:
        rows = (await s.execute(select(IngestionTask))).scalars().all()
        return {(row.mal_id, row.resource): row.status for row in rows}


async def test_the_catalogue_step_resets_only_the_tasks_of_airing_and_upcoming_anime(
    pg_sessionmaker,
):
    async with pg_sessionmaker() as s:
        s.add_all(
            [
                Anime(mal_id=1, title="airing", status="Currently Airing"),
                Anime(mal_id=2, title="upcoming", status="Not yet aired"),
                Anime(mal_id=3, title="finished", status="Finished Airing"),
                Anime(mal_id=4, title="airing but dead", status="Currently Airing"),
            ]
        )
        for mal_id in (1, 2, 3):
            for resource in RESOURCES:
                s.add(IngestionTask(mal_id=mal_id, resource=resource, status=TaskStatus.DONE))
        for resource in RESOURCES:
            s.add(IngestionTask(mal_id=4, resource=resource, status=TaskStatus.DEAD))
        await s.commit()

    async with pg_sessionmaker() as session, pg_sessionmaker() as metrics:
        job = DiscoveryJob(
            EmptyLists(),
            TaskRepository(session),
            MetricsRepository(metrics),
            CatalogueRepository(session),
        )
        summary = await job.run_job_weekly(resources=RESOURCES)

    after = await statuses(pg_sessionmaker)
    for resource in RESOURCES:
        assert after[(1, resource)] is TaskStatus.PENDING  # airing: fetched again
        assert after[(2, resource)] is TaskStatus.PENDING  # not yet aired: fetched again
        assert after[(3, resource)] is TaskStatus.DONE  # finished long ago: left alone
        assert after[(4, resource)] is TaskStatus.DEAD  # dead stays dead for inspection
    assert summary["catalogue"] == {"anime": 3, "tasks_inserted": 0, "tasks_reset": 4}


async def test_both_lists_are_recorded_as_succeeded_runs(pg_sessionmaker):
    async with pg_sessionmaker() as session, pg_sessionmaker() as metrics:
        job = DiscoveryJob(
            EmptyLists(),
            TaskRepository(session),
            MetricsRepository(metrics),
            CatalogueRepository(session),
        )
        await job.run_job_weekly(resources=RESOURCES)

    async with pg_sessionmaker() as s:
        repo = MetricsRepository(s)
        airing = await repo.latest_run("discovery_weekly_airing")
        upcoming = await repo.latest_run("discovery_weekly_upcoming")
    assert airing.status.value == upcoming.status.value == "succeeded"
