from datetime import datetime

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.models import RawTenrai


class RawTenraiRepository:
    """Index of the stored Tenrai responses, one row per (mal_id, resource).

    The responses themselves are S3 objects (RawStore, ADR 0016). A refresh overwrites the row.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find(self, mal_id: int, resource: Resource) -> RawTenrai | None:
        result = await self.db.execute(
            select(RawTenrai).where(RawTenrai.mal_id == mal_id, RawTenrai.resource == resource)
        )
        return result.scalar_one_or_none()

    async def get_batch(
        self,
        since: datetime | None,
        after: tuple[int, Resource] | None,
        limit: int,
    ) -> list[RawTenrai]:
        """The next `limit` index rows in (mal_id, resource) order, for parsing in bulk.

        `since`: only rows fetched at or after this time (None means every row). The caller
        decides it, e.g. the start of the last successful run, or the first of the month.
        `after`: the (mal_id, resource) of the last row of the previous batch, or None to start
        from the beginning. Pass the last row you got back to read the next batch.

        Within an anime the order is details, episodes, reviews, statistics, so `details`
        always comes before the rows that depend on the anime existing.
        """
        stmt = select(RawTenrai).order_by(RawTenrai.mal_id, RawTenrai.resource).limit(limit)
        if since is not None:
            stmt = stmt.where(RawTenrai.fetched_at >= since)
        if after is not None:
            last_id, last_resource = after
            stmt = stmt.where(
                tuple_(RawTenrai.mal_id, RawTenrai.resource)
                > tuple_(last_id, Resource(last_resource).value)
            )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert(self, mal_id: int, resource: Resource, size_bytes: int) -> None:
        """Record that a response is stored, overwriting size_bytes and fetched_at if it was."""
        await self.upsert_many([(mal_id, resource, size_bytes)])

    async def upsert_many(self, rows: list[tuple[int, Resource, int]]) -> int:
        """Record several stored responses in one statement; returns how many rows were written.

        Each (mal_id, resource) may appear only once in `rows`: Postgres refuses to update the
        same row twice in one statement.
        """
        if not rows:
            return 0
        stmt = insert(RawTenrai).values(
            [
                {"mal_id": mal_id, "resource": resource, "size_bytes": size_bytes}
                for mal_id, resource, size_bytes in rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["mal_id", "resource"],
            set_={"size_bytes": stmt.excluded.size_bytes, "fetched_at": func.now()},
        )
        result = await self.db.execute(stmt)
        return result.rowcount

    async def delete(self, mal_id: int, resource: Resource) -> int:
        """Delete one row and return how many were deleted (0 if it did not exist)."""
        result = await self.db.execute(
            delete(RawTenrai).where(RawTenrai.mal_id == mal_id, RawTenrai.resource == resource)
        )
        return result.rowcount

    async def commit(self) -> None:
        await self.db.commit()
