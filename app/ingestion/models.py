from sqlalchemy import CheckConstraint, Column, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database.database import Base
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
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


class IngestionRun(Base):
    """One execution of an ingestion job, with the metrics it measured."""

    __tablename__ = "ingestion_run"
    __table_args__ = (
        CheckConstraint(_in_list("status", RunStatus), name="ck_ingestion_run_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job = Column(String(50), nullable=False)
    status = Column(
        Enum(RunStatus, native_enum=False, create_constraint=False, values_callable=_enum_values),
        nullable=False,
        server_default=RunStatus.RUNNING.value,
    )
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # The job's summary dict (counts, timings, throughput), written when the run ends.
    summary = Column(JSONB, nullable=True)
    # Exception type and trimmed message of a failed run. Never personal data.
    error = Column(Text, nullable=True)


class RawTenrai(Base):
    """Index of a stored Tenrai response, one row per (mal_id, resource); the payload is in S3."""

    __tablename__ = "raw_tenrai"
    __table_args__ = (
        CheckConstraint(_in_list("resource", Resource), name="ck_raw_tenrai_resource"),
    )

    mal_id = Column(Integer, primary_key=True)
    resource = Column(
        Enum(Resource, native_enum=False, create_constraint=False, values_callable=_enum_values),
        primary_key=True,
    )
    # The response itself is an S3 object keyed by (resource, mal_id) (ADR 0016); this row is its
    # index. Multi-page Resources (episodes) store every page as a list in that one object.
    size_bytes = Column(Integer, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
