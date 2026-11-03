from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database.database import Base
from app.ingestion.enums.match_method_enum import MatchMethod
from app.ingestion.enums.match_status_enum import MatchStatus
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
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


class ImdbSnapshot(Base):
    """One run of the IMDb download: the ETag and size of each file and how far the run got."""

    __tablename__ = "imdb_snapshot"
    __table_args__ = (
        CheckConstraint(_in_list("status", SnapshotStatus), name="ck_imdb_snapshot_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # The files themselves are S3 objects; these identify the version that was downloaded.
    basics_etag = Column(Text, nullable=True)
    basics_size_bytes = Column(BigInteger, nullable=True)
    episode_etag = Column(Text, nullable=True)
    episode_size_bytes = Column(BigInteger, nullable=True)
    ratings_etag = Column(Text, nullable=True)
    ratings_size_bytes = Column(BigInteger, nullable=True)
    status = Column(
        Enum(
            SnapshotStatus, native_enum=False, create_constraint=False, values_callable=_enum_values
        ),
        nullable=False,
        server_default=SnapshotStatus.RUNNING.value,
    )


class AnimeImdbMatch(Base):
    """The matching decision for one anime: which IMDb title (and slice) it maps to, and why."""

    __tablename__ = "anime_imdb_match"
    __table_args__ = (
        CheckConstraint(_in_list("method", MatchMethod), name="ck_anime_imdb_match_method"),
        CheckConstraint(_in_list("status", MatchStatus), name="ck_anime_imdb_match_status"),
    )

    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    # An IMDb id (for example tt0388629). There is no IMDb table to reference: the IMDb data is
    # filtered and matched in memory and never stored (ADR 0019). Null when the match is
    # unmatched, or an override confirms there is no IMDb match.
    imdb_tconst = Column(Text, nullable=True)
    season_number = Column(Integer, nullable=True)
    episode_from = Column(Integer, nullable=True)
    episode_to = Column(Integer, nullable=True)
    score = Column(Numeric(5, 4), nullable=True)
    method = Column(
        Enum(MatchMethod, native_enum=False, create_constraint=False, values_callable=_enum_values),
        nullable=False,
    )
    status = Column(
        Enum(MatchStatus, native_enum=False, create_constraint=False, values_callable=_enum_values),
        nullable=False,
    )
    # Top candidates with their feature scores, for audits of match quality.
    candidates = Column(JSONB, nullable=True)
    # The IMDb snapshot the decision was made against.
    snapshot_id = Column(Integer, ForeignKey("imdb_snapshot.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
