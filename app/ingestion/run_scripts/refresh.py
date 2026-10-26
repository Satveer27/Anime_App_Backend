"""The weekly and monthly refresh: reset the tasks of the anime in scope, fetch them, clean them.

    uv run --env-file .env.local python -m app.ingestion.run_scripts.refresh --tier weekly

`--tier weekly` is the Active tier (ADR 0004): Tenrai's airing and upcoming lists plus every anime
the catalogue holds as airing or not yet aired. `--tier monthly` is last year's and this year's
anime plus the most popular ones, without episodes. `--stage` runs one stage (one Airflow task)
or all three in order.

Unlike one_pass, the clean stage is incremental: it parses only the raw rows fetched since the last
successful clean run, which are the ones this refresh just downloaded. A failed run resumes by
itself: each discovery list from its own saved page, the clean job from its saved cursor, and the
fetch job from the task table.

Run it when no fetch is running. In Airflow the one-slot `tenrai` pool guarantees that.
The exit codes are one_pass's.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.catalogue.repository import CatalogueRepository
from app.config import settings
from app.core.database.database import asyncSessionMaker
from app.ingestion.adapters.tenrai import TenraiAdapter
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.exceptions import (
    AccessDeniedError,
    CircuitOpenError,
    PermanentError,
    RawStoreError,
    RepeatedUnexpectedErrors,
    TransientError,
)
from app.ingestion.jobs.clean_raw_job import JOB_NAME as CLEAN_JOB
from app.ingestion.jobs.clean_raw_job import CleanAnimeJob, Cursor
from app.ingestion.jobs.discovery_job import DiscoveryJob
from app.ingestion.jobs.fetch_anime_job import FetchAnimeJob
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository

# Shared with one_pass for now. They move to a common module once the backfill, which loads
# one_pass from disk for its clean task, has finished.
from app.ingestion.run_scripts.one_pass import (
    EXIT_BAD_ARGUMENTS,
    EXIT_DATABASE,
    EXIT_INTERRUPTED,
    EXIT_NEEDS_ATTENTION,
    EXIT_OK,
    EXIT_STORAGE,
    EXIT_TENRAI_DOWN,
    EXIT_TERMINATED,
    EXIT_UNEXPECTED,
    _cancel_on_sigterm,
)
from app.ingestion.storage.raw_store import RawStore
from app.ingestion.utils.rate_limiter import RateLimiter

logger = structlog.get_logger(__name__)

# A failed clean run is only resumed if it is this recent. The clean job's run history is shared with
# one_pass, so an old or full-pass failure must not decide where this week's clean starts.
CLEAN_RESUME_MAX_AGE = timedelta(hours=24)


def read_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the weekly or monthly refresh.")
    parser.add_argument("--tier", choices=("weekly", "monthly"), required=True)
    parser.add_argument(
        "--stage",
        choices=("all", "discovery", "fetch", "clean"),
        default="all",
        help="run one stage (for an Airflow task) or all three in order",
    )
    return parser.parse_args(argv)


async def clean_resume_cursor(metrics: MetricsRepository) -> Cursor | None:
    """Where the last clean run stopped, if that run is one this refresh may continue.

    Only a run that FAILED, started within CLEAN_RESUME_MAX_AGE and was incremental (it has a
    `since` cut-off) qualifies. A failed full pass (one_pass) has no cut-off: continuing it after its
    cursor with this week's cut-off would skip the refreshed rows before the cursor for good.
    """
    clean = await metrics.latest_run(CLEAN_JOB)
    if not (clean and clean.status == RunStatus.FAILED and clean.summary):
        return None
    if datetime.now(UTC) - clean.started_at > CLEAN_RESUME_MAX_AGE:
        return None
    if not clean.summary.get("since"):
        return None
    saved = clean.summary.get("resume_after")  # stored as [mal_id, resource]
    if saved:
        return saved[0], Resource(saved[1])
    return None


async def main(args: argparse.Namespace) -> int:
    _cancel_on_sigterm()
    raw_store = None
    if args.stage in ("all", "fetch", "clean"):
        try:
            raw_store = RawStore.from_settings()
        except ValueError as exc:  # no bucket configured: say so before any work starts
            logger.error("refresh_bad_configuration", error=str(exc))
            return EXIT_BAD_ARGUMENTS
    adapter = TenraiAdapter(
        RateLimiter(settings.tenrai_rate_limiter_requests_per_minute),
        settings.mal_data_base_url,
    )
    try:
        # Two sessions: the run records must survive a rollback in the work session.
        async with asyncSessionMaker() as session, asyncSessionMaker() as metrics_session:
            metrics = MetricsRepository(metrics_session)
            task_repository = TaskRepository(session)
            raw_repository = RawTenraiRepository(session)
            catalogue_repository = CatalogueRepository(session)
            logger.info("refresh_started", tier=args.tier, stage=args.stage)

            if args.stage in ("all", "discovery"):
                discovery = DiscoveryJob(adapter, task_repository, metrics, catalogue_repository)
                if args.tier == "weekly":
                    summary = await discovery.run_job_weekly()
                else:
                    summary = await discovery.run_job_monthly()
                logger.info("discovery_summary", tier=args.tier, **summary)

            if args.stage in ("all", "fetch"):
                fetch = FetchAnimeJob(adapter, task_repository, raw_repository, metrics, raw_store)
                logger.info("fetch_summary", tier=args.tier, **await fetch.fetch_from_tasks())

            if args.stage in ("all", "clean"):
                clean = CleanAnimeJob(catalogue_repository, raw_repository, metrics, raw_store)
                after = await clean_resume_cursor(metrics)
                # Incremental: only what this refresh fetched, not a full pass
                logger.info(
                    "clean_summary", tier=args.tier, **await clean.full_clean_loop(after=after)
                )
    except (CircuitOpenError, TransientError) as exc:
        logger.error("refresh_stopped_tenrai_down", error=type(exc).__name__)
        return EXIT_TENRAI_DOWN
    except (AccessDeniedError, PermanentError, RepeatedUnexpectedErrors) as exc:
        logger.error("refresh_stopped_needs_attention", error=type(exc).__name__)
        return EXIT_NEEDS_ATTENTION
    except SQLAlchemyError as exc:
        logger.error("refresh_stopped_database", error=type(exc).__name__)
        return EXIT_DATABASE
    except RawStoreError as exc:
        logger.error("refresh_stopped_raw_store", error=str(exc))
        return EXIT_STORAGE
    except Exception:
        logger.exception("refresh_failed")
        return EXIT_UNEXPECTED
    finally:
        await adapter.aclose()

    logger.info("refresh_finished", tier=args.tier, stage=args.stage)
    return EXIT_OK


def run() -> None:
    args = read_args()
    try:
        code = asyncio.run(main(args))
    except KeyboardInterrupt:
        # The jobs already recorded the interrupted run and released their task.
        logger.warning("refresh_interrupted")
        code = EXIT_INTERRUPTED
    except asyncio.CancelledError:
        # SIGTERM: the jobs recorded the run as failed with its resume position
        logger.warning("refresh_terminated")
        code = EXIT_TERMINATED
    raise SystemExit(code)


if __name__ == "__main__":
    run()
