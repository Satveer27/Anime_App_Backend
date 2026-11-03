"""Download the IMDb dataset files and archive them in S3 (ADR 0018). The first task of imdb_weekly.

    uv run --env-file .env.local python -m app.ingestion.run_scripts.imdb_download

Exit codes: 0 archived, 1 unexpected error, 2 missing configuration (RAW_S3_BUCKET), 3 IMDb
unreachable (retry later), 4 refused or corrupt (a redirect, a file over the size cap, a failed
gzip check, missing headers: look at it), 5 database error, 6 raw store (S3) error, 99 skipped
(the files are unchanged since the last archived run; Airflow marks the task and the tasks after
it as skipped), 130 Ctrl-C, 143 stopped by SIGTERM (what Airflow sends when it stops a task).
"""

import asyncio
import signal

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.core.database.database import asyncSessionMaker
from app.ingestion.adapters.imdb import ImdbAdapter
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.exceptions import (
    AccessDeniedError,
    GoneError,
    PermanentError,
    RawStoreError,
    TransientError,
)
from app.ingestion.jobs.imdb.download_file_job import ImdbDownloadJob
from app.ingestion.repository.imdb_snapshot_repository import ImdbSnapshotRepository
from app.ingestion.storage.imdb_store import ImdbStore

logger = structlog.get_logger(__name__)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_BAD_CONFIGURATION = 2
EXIT_IMDB_DOWN = 3
EXIT_NEEDS_ATTENTION = 4
EXIT_DATABASE = 5
EXIT_STORAGE = 6
# Airflow's BashOperator treats 99 as "skipped" by default.
EXIT_SKIPPED = 99
EXIT_INTERRUPTED = 130
EXIT_TERMINATED = 143


def _cancel_on_sigterm() -> None:
    """Make SIGTERM cancel the running task, so the job marks its snapshot failed before the
    process ends. Ctrl-C needs nothing: asyncio.run already cancels the task on SIGINT."""
    task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    except NotImplementedError:
        pass  # Windows has no loop signal handlers; Airflow runs this on Linux


async def main() -> int:
    _cancel_on_sigterm()
    try:
        store = ImdbStore.from_settings()
    except ValueError as exc:  # no bucket configured: say so before any work starts
        logger.error("imdb_download_bad_configuration", error=str(exc))
        return EXIT_BAD_CONFIGURATION

    adapter = ImdbAdapter()
    try:
        async with asyncSessionMaker() as session:
            summary = await ImdbDownloadJob(adapter, store, ImdbSnapshotRepository(session)).run()
    except TransientError as exc:  # includes NetworkError
        logger.error("imdb_download_stopped_imdb_down", error=type(exc).__name__)
        return EXIT_IMDB_DOWN
    except (AccessDeniedError, GoneError, PermanentError) as exc:
        logger.error("imdb_download_stopped_needs_attention", error=type(exc).__name__)
        return EXIT_NEEDS_ATTENTION
    except SQLAlchemyError as exc:
        logger.error("imdb_download_stopped_database", error=type(exc).__name__)
        return EXIT_DATABASE
    except RawStoreError as exc:
        logger.error("imdb_download_stopped_raw_store", error=str(exc))
        return EXIT_STORAGE
    except Exception:
        logger.exception("imdb_download_failed")
        return EXIT_UNEXPECTED
    finally:
        await adapter.aclose()

    return EXIT_SKIPPED if summary["status"] == SnapshotStatus.SKIPPED.value else EXIT_OK


def run() -> None:
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        # The job already marked its snapshot failed.
        logger.warning("imdb_download_interrupted")
        code = EXIT_INTERRUPTED
    except asyncio.CancelledError:
        # SIGTERM: the job already marked its snapshot failed.
        logger.warning("imdb_download_terminated")
        code = EXIT_TERMINATED
    raise SystemExit(code)


if __name__ == "__main__":
    run()
