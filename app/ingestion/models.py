from sqlalchemy import CheckConstraint, Column, DateTime, Enum, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database.database import Base
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.task_status_enum import TaskStatus


def _enum_values(enum_cls) -> list[str]:
    return [member.value for member in enum_cls]


def _in_list(column: str, enum_cls) -> str:
    values = ", ".join(f"'{v}'" for v in _enum_values(enum_cls))
    return f"{column} IN ({values})"


class IngestionTask(Base):
    __tablename__ = "ingestion_task"
    # Declared here, not via Enum(create_constraint=True): Alembic ignores type-bound
    # constraints on the model side, so `alembic check` would report them as removed.
    __table_args__ = (
        CheckConstraint(_in_list("resource", Resource), name="ck_ingestion_task_resource"),
        CheckConstraint(_in_list("status", TaskStatus), name="ck_ingestion_task_status"),
    )

    mal_id = Column(Integer, primary_key=True)
    resource = Column(
        Enum(Resource, native_enum=False, create_constraint=False, values_callable=_enum_values),
        primary_key=True,
    )
    status = Column(
        Enum(TaskStatus, native_enum=False, create_constraint=False, values_callable=_enum_values),
        nullable=False,
        server_default=TaskStatus.PENDING.value,
    )
    attempts = Column(Integer, nullable=False, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RawTenrai(Base):
    """A Tenrai response stored untouched, one row per (mal_id, resource)."""

    __tablename__ = "raw_tenrai"
    __table_args__ = (
        CheckConstraint(_in_list("resource", Resource), name="ck_raw_tenrai_resource"),
    )

    mal_id = Column(Integer, primary_key=True)
    resource = Column(
        Enum(Resource, native_enum=False, create_constraint=False, values_callable=_enum_values),
        primary_key=True,
    )
    # Multi-page Resources (episodes) store every page as a list, written once after the last.
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
