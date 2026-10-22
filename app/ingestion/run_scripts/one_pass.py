"""One full pass: discover every anime, fetch every task, then clean the raw rows.

    uv run --env-file .env.local python -m app.ingestion.run_scripts.one_pass

A rerun resumes by itself: discovery continues from the `resume_page` of its last failed run,
the clean job from that run's `resume_after`, and the fetch job takes whatever is still
claimable. `--fresh` ignores the saved positions; `--page` and `--after` set them by hand.

Exit codes: 0 done, 1 unexpected error, 2 bad arguments or missing configuration (such as
RAW_S3_BUCKET), 3 Tenrai unreachable (retry later), 4 blocked or a bug (look at it), 5 database
error, 6 raw store (S3) error, 130 Ctrl-C, 143 stopped by SIGTERM (what Airflow sends when it
stops a task).
"""

import argparse
import asyncio
import signal

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
from app.ingestion.jobs.discovery_job import JOB_NAME as DISCOVERY_JOB
from app.ingestion.jobs.discovery_job import DiscoveryJob
from app.ingestion.jobs.fetch_anime_job import FetchAnimeJob
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository
from app.ingestion.storage.raw_store import RawStore
from app.ingestion.utils.rate_limiter import RateLimiter

logger = structlog.get_logger(__name__)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_BAD_ARGUMENTS = 2  # bad arguments (argparse exits with this itself) or missing config
EXIT_TENRAI_DOWN = 3
EXIT_NEEDS_ATTENTION = 4
EXIT_DATABASE = 5
EXIT_STORAGE = 6
EXIT_INTERRUPTED = 130
EXIT_TERMINATED = 143


def read_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one full ingestion pass.")
    parser.add_argument(
        "--stage",
        choices=("all", "discovery", "fetch", "clean"),
        default="all",
        help="run one stage (for an Airflow task) or all three in order",
    )
    parser.add_argument("--page", type=int, help="discovery page to start from")
    parser.add_argument("--after", help="clean job cursor to resume after, as MAL_ID:RESOURCE")
    parser.add_argument(
        "--fresh", action="store_true", help="ignore the positions saved by failed runs"
    )
    args = parser.parse_args(argv)
    if args.page is not None and args.page < 1:
        parser.error("--page must be at least 1")
    args.after = _parse_cursor(parser, args.after) if args.after else None
    return args


def _parse_cursor(parser: argparse.ArgumentParser, value: str) -> Cursor:
    try:
        mal_id, resource = value.split(":")
        return int(mal_id), Resource(resource)
    except ValueError:
        parser.error(f"--after must look like 123:statistics, got {value!r}")


async def saved_positions(metrics: MetricsRepository) -> tuple[int | None, Cursor | None]:
    """Where the last failed discovery and clean runs stopped (None if they did not fail)."""
    page = after = None

    discovery = await metrics.latest_run(DISCOVERY_JOB)
    if discovery and discovery.status == RunStatus.FAILED and discovery.summary:
        page = discovery.summary.get("resume_page")

    clean = await metrics.latest_run(CLEAN_JOB)
    if clean and clean.status == RunStatus.FAILED and clean.summary:
        saved = clean.summary.get("resume_after")  # stored as [mal_id, resource]
        if saved:
            after = (saved[0], Resource(saved[1]))
    return page, after


def _cancel_on_sigterm() -> None:
    """Make SIGTERM cancel the running task, so the jobs record the run before the process ends.

    Without this the process dies at once and the run row stays `running` with no resume position.
    Ctrl-C needs nothing: asyncio.run already cancels the task on SIGINT.
    """
    task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    except NotImplementedError:
        pass  # Windows has no loop signal handlers; Airflow runs this on Linux


async def main(args: argparse.Namespace) -> int:
    _cancel_on_sigterm()
    raw_store = None
    if args.stage in ("all", "fetch", "clean"):
        try:
            raw_store = RawStore.from_settings()
        except ValueError as exc:  # no bucket configured: say so before any work starts
            logger.error("one_pass_bad_configuration", error=str(exc))
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

            page, after = (None, None) if args.fresh else await saved_positions(metrics)
            page = args.page or page or 1
            after = args.after or after
            logger.info(
                "one_pass_started", stage=args.stage, page=page, after=after, fresh=args.fresh
            )

            if args.stage in ("all", "discovery"):
                discovery = DiscoveryJob(adapter, task_repository, metrics)
                logger.info("discovery_summary", **await discovery.run_job_overall(page))

            if args.stage in ("all", "fetch"):
                fetch = FetchAnimeJob(adapter, task_repository, raw_repository, metrics, raw_store)
                logger.info("fetch_summary", **await fetch.fetch_from_tasks())

            if args.stage in ("all", "clean"):
                clean = CleanAnimeJob(
                    CatalogueRepository(session), raw_repository, metrics, raw_store
                )
                logger.info(
                    "clean_summary", **await clean.full_clean_loop(after=after, full_pass=True)
                )
    except (CircuitOpenError, TransientError) as exc:
        logger.error("one_pass_stopped_tenrai_down", error=type(exc).__name__)
        return EXIT_TENRAI_DOWN
    except (AccessDeniedError, PermanentError, RepeatedUnexpectedErrors) as exc:
        logger.error("one_pass_stopped_needs_attention", error=type(exc).__name__)
        return EXIT_NEEDS_ATTENTION
    except SQLAlchemyError as exc:
        logger.error("one_pass_stopped_database", error=type(exc).__name__)
        return EXIT_DATABASE
    except RawStoreError as exc:
        logger.error("one_pass_stopped_raw_store", error=str(exc))
        return EXIT_STORAGE
    except Exception:
        logger.exception("one_pass_failed")
        return EXIT_UNEXPECTED
    finally:
        await adapter.aclose()

    logger.info("one_pass_finished")
    return EXIT_OK


def run() -> None:
    args = read_args()
    try:
        code = asyncio.run(main(args))
    except KeyboardInterrupt:
        # The jobs already recorded the interrupted run and released their task.
        logger.warning("one_pass_interrupted")
        code = EXIT_INTERRUPTED
    except asyncio.CancelledError:
        # SIGTERM: the jobs recorded the run as failed with its resume position
        logger.warning("one_pass_terminated")
        code = EXIT_TERMINATED
    raise SystemExit(code)


if __name__ == "__main__":
    run()
