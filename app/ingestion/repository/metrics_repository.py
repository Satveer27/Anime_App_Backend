from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.models import IngestionRun

ERROR_MAX_CHARS = 500


class MetricsRepository:
    """Run history for ingestion jobs.

    Give it its own session, separate from the one a job uses for its work, so a run record
    survives a rollback in the job's transaction.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def start_run(self, job: str) -> int:
        result = await self.db.execute(
            insert(IngestionRun).values(job=job).returning(IngestionRun.id)
        )
        return result.scalar_one()

    async def finish_run(
        self, run_id: int, status: RunStatus, summary: dict, error: str | None = None
    ) -> None:
        await self.db.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                status=status,
                finished_at=datetime.now(UTC),
                summary=summary,
                error=error[:ERROR_MAX_CHARS] if error else None,
            )
        )

    async def latest_run(self, job: str) -> IngestionRun | None:
        """The most recent run of a job, e.g. to read its summary["resume_page"] after a failure."""
        result = await self.db.execute(
            select(IngestionRun)
            .where(IngestionRun.job == job)
            .order_by(IngestionRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def last_succeeded_run_start(self, job: str) -> datetime | None:
        """When the newest succeeded run of a job started, or None if it has never succeeded.

        A job that only cares about what changed since it last ran uses this as its cut-off.
        """
        result = await self.db.execute(
            select(IngestionRun.started_at)
            .where(IngestionRun.job == job, IngestionRun.status == RunStatus.SUCCEEDED)
            .order_by(IngestionRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self.db.commit()
