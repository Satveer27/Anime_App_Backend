from datetime import UTC, datetime, timedelta

from sqlalchemy import func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.models import IngestionTask

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 6 * 3600
LAST_ERROR_MAX_CHARS = 500
# The only states a refresh may send back to pending. dead and gone are never retried
# automatically (ADR 0014); a new status stays excluded until it is added here on purpose.
REFRESHABLE_STATUSES = [TaskStatus.DONE, TaskStatus.FAILED]


class TaskRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_tasks(self, rows: list[tuple[int, Resource]]) -> int:
        if not rows:
            return 0

        stmt = (
            insert(IngestionTask)
            .values([{"mal_id": mal_id, "resource": resource} for mal_id, resource in rows])
            .on_conflict_do_nothing(index_elements=["mal_id", "resource"])
        )
        result = await self.db.execute(stmt)
        return result.rowcount

    async def insert_or_reset_tasks(self, rows: list[tuple[int, Resource]]) -> tuple[int, int]:
        """Insert missing tasks and send finished or failed ones back to pending (a refresh).

        `dead`, `gone` and tasks that are already `pending` are left untouched. Returns
        (inserted, reset); rows that were left untouched are in neither count. Each
        (mal_id, resource) may appear only once in `rows`.
        """
        if not rows:
            return 0, 0

        stmt = insert(IngestionTask).values(
            [{"mal_id": mal_id, "resource": resource} for mal_id, resource in rows]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["mal_id", "resource"],
            set_={
                "status": TaskStatus.PENDING,
                "attempts": 0,
                "next_attempt_at": None,
                "last_error": None,
                # onupdate does not apply to ON CONFLICT DO UPDATE
                "updated_at": func.now(),
            },
            where=IngestionTask.status.in_(REFRESHABLE_STATUSES),
        ).returning(literal_column("(xmax = 0)"))  # true for a fresh insert, false for an update
        result = await self.db.execute(stmt)
        was_inserted = result.scalars().all()
        inserted = sum(was_inserted)
        return inserted, len(was_inserted) - inserted

    async def count_by_status(self) -> dict[str, int]:
        """How many tasks are in each state (every state is present, 0 if empty)."""
        result = await self.db.execute(
            select(IngestionTask.status, func.count()).group_by(IngestionTask.status)
        )
        counts = {status.value: 0 for status in TaskStatus}
        counts.update({status.value: count for status, count in result.all()})
        return counts

    async def claim_next(self) -> IngestionTask | None:
        """Select the next claimable task and lock it until the caller commits or rolls back."""
        now = datetime.now(UTC)
        stmt = (
            select(IngestionTask)
            .where(
                IngestionTask.status.in_([TaskStatus.PENDING, TaskStatus.FAILED]),
                or_(
                    IngestionTask.next_attempt_at.is_(None),
                    IngestionTask.next_attempt_at <= now,
                ),
            )
            .order_by(IngestionTask.mal_id, IngestionTask.resource)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_done(self, task: IngestionTask) -> None:
        await self._set(task, status=TaskStatus.DONE, next_attempt_at=None, last_error=None)

    async def mark_gone(self, task: IngestionTask) -> None:
        await self._set(task, status=TaskStatus.GONE, next_attempt_at=None)

    async def mark_dead(self, task: IngestionTask, error: str) -> None:
        """Give up on a task whose request can never succeed (a 4xx other than 404). Never retried."""
        await self._set(
            task,
            status=TaskStatus.DEAD,
            attempts=task.attempts + 1,
            next_attempt_at=None,
            last_error=error[:LAST_ERROR_MAX_CHARS],
        )

    async def mark_failed(self, task: IngestionTask, error: str) -> None:
        attempts = task.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            status, next_attempt_at = TaskStatus.DEAD, None
        else:
            delay = min(BACKOFF_BASE_SECONDS * 2**attempts, BACKOFF_CAP_SECONDS)
            status = TaskStatus.FAILED
            next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        await self._set(
            task,
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_error=error[:LAST_ERROR_MAX_CHARS],
        )

    async def _set(self, task: IngestionTask, **values) -> None:
        await self.db.execute(
            update(IngestionTask)
            .where(IngestionTask.mal_id == task.mal_id, IngestionTask.resource == task.resource)
            .values(**values)
        )

    async def reset_tasks(self, mal_ids: list[int], resources: list[Resource]) -> int:
        """Send finished or failed tasks back to pending for a refresh."""
        result = await self.db.execute(
            update(IngestionTask)
            .where(
                IngestionTask.mal_id.in_(mal_ids),
                IngestionTask.resource.in_(resources),
                IngestionTask.status.in_(REFRESHABLE_STATUSES),
            )
            .values(
                status=TaskStatus.PENDING,
                attempts=0,
                next_attempt_at=None,
                last_error=None,
            )
        )
        return result.rowcount

    async def get_task(self, id: int, resource: Resource) -> IngestionTask | None:
        result = await self.db.execute(
            select(IngestionTask).where(
                IngestionTask.mal_id == id, IngestionTask.resource == resource
            )
        )
        return result.scalar_one_or_none()

    async def delete_task(self, task: IngestionTask) -> None:
        await self.db.delete(task)

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
