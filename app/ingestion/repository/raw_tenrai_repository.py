from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.models import RawTenrai


class RawTenraiRepository:
    """Raw Tenrai responses, one row per (mal_id, resource). A refresh overwrites the row (ADR 0014)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find(self, mal_id: int, resource: Resource) -> RawTenrai | None:
        result = await self.db.execute(
            select(RawTenrai).where(RawTenrai.mal_id == mal_id, RawTenrai.resource == resource)
        )
        return result.scalar_one_or_none()

    async def upsert(self, mal_id: int, resource: Resource, payload: dict | list) -> None:
        """Store one response, overwriting the payload and fetched_at if the row exists."""
        await self.upsert_many([(mal_id, resource, payload)])

    async def upsert_many(self, rows: list[tuple[int, Resource, dict | list]]) -> int:
        """Store several responses in one statement and return how many rows were written.

        Each (mal_id, resource) may appear only once in `rows`: Postgres refuses to update the
        same row twice in one statement.
        """
        if not rows:
            return 0
        stmt = insert(RawTenrai).values(
            [
                {"mal_id": mal_id, "resource": resource, "payload": payload}
                for mal_id, resource, payload in rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["mal_id", "resource"],
            set_={"payload": stmt.excluded.payload, "fetched_at": func.now()},
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
