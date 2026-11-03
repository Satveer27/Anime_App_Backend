from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.adapters.imdb import DownloadedFile
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.models import ImdbSnapshot


class ImdbSnapshotRepository:
    """One row per run of the IMDb download (ADR 0018). A retry is a new run and a new row.

    Give it its own session, separate from any other work, so the record of a failed run
    survives a rollback.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def start_run(self) -> int:
        """Insert a `running` snapshot and return its id."""
        result = await self.db.execute(insert(ImdbSnapshot).returning(ImdbSnapshot.id))
        return result.scalar_one()

    async def latest_archived(self) -> ImdbSnapshot | None:
        """The newest snapshot whose files are in S3. Failed, skipped and running ones are
        ignored, so the skip rule always compares against files that really exist."""
        result = await self.db.execute(
            select(ImdbSnapshot)
            .where(ImdbSnapshot.status == SnapshotStatus.ARCHIVED)
            .order_by(ImdbSnapshot.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_archived(
        self, snapshot_id: int, finished_at: datetime, files: Iterable[DownloadedFile]
    ) -> None:
        """Record the three archived files. `finished_at` is the time their S3 keys were built
        from, so the keys can be rebuilt from the row."""
        values: dict = {"status": SnapshotStatus.ARCHIVED, "finished_at": finished_at}
        for f in files:
            prefix = f.file.name.lower()  # BASICS -> basics_etag, basics_size_bytes, ...
            values[f"{prefix}_etag"] = f.etag
            values[f"{prefix}_size_bytes"] = f.size_bytes
        await self._update(snapshot_id, values)

    async def mark_skipped(self, snapshot_id: int) -> None:
        await self._update(
            snapshot_id, {"status": SnapshotStatus.SKIPPED, "finished_at": datetime.now(UTC)}
        )

    async def mark_failed(self, snapshot_id: int) -> None:
        await self._update(
            snapshot_id, {"status": SnapshotStatus.FAILED, "finished_at": datetime.now(UTC)}
        )

    async def _update(self, snapshot_id: int, values: dict) -> None:
        await self.db.execute(
            update(ImdbSnapshot).where(ImdbSnapshot.id == snapshot_id).values(**values)
        )

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
