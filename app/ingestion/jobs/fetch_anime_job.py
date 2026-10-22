import json
import time
from typing import NamedTuple

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.ingestion.adapters.tenrai import TenraiAdapter
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.enums.task_status_enum import TaskStatus
from app.ingestion.exceptions import (
    AccessDeniedError,
    CircuitOpenError,
    GoneError,
    PermanentError,
    RawStoreError,
    RepeatedUnexpectedErrors,
    TransientError,
)
from app.ingestion.models import IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository
from app.ingestion.storage.raw_store import RawStore

logger = structlog.get_logger(__name__)

JOB_NAME = "fetch_tasks"
# ADR 0014 says at most 12 episode pages exist; this only stops a runaway loop.
MAX_EPISODE_PAGES = 100
# This many unexpected failures in a row means a bug in our code, not one bad anime.
MAX_CONSECUTIVE_UNEXPECTED = 10
# This many 401/403 answers in a row means the source is blocking us, not one odd response.
MAX_CONSECUTIVE_DENIED = 5
# A review page can be 240 KB; cap what one log line carries.
PAYLOAD_LOG_CHARS = 1000
PROGRESS_EVERY_TASKS = 500


class TaskOutcome(NamedTuple):
    resource: Resource
    status: TaskStatus  # done, gone, dead or failed (failed may become dead after enough attempts)
    first_try: bool  # the task had no failed attempt before this one


class FetchAnimeJob:
    """Claims tasks, fetches each Resource from Tenrai and stores the raw response.

    The response goes to S3 first (`raw_store`), then its index row and the task's new state are
    committed together. `task_repository` and `raw_tenrai_repo` must share one session. A crash
    after the upload and before the commit leaves an object nobody points at, which the task's
    next attempt overwrites (ADR 0016).
    `metrics_repository` needs its own session so the run record survives a rollback.
    """

    def __init__(
        self,
        tenrai_adapter: TenraiAdapter,
        task_repository: TaskRepository,
        raw_tenrai_repo: RawTenraiRepository,
        metrics_repository: MetricsRepository,
        raw_store: RawStore,
    ):
        self.tenrai_adapter = tenrai_adapter
        self.task_repository = task_repository
        self.raw_tenrai_repo = raw_tenrai_repo
        self.metrics_repository = metrics_repository
        self.raw_store = raw_store
        self._raw_bytes = 0
        self._unexpected_in_a_row = 0
        self._denied_in_a_row = 0
        self._rate_limited_at_start = 0
        self._requests_at_start = 0
        self._retries_at_start = 0

    async def fetch_from_tasks(self) -> dict:
        """Process tasks until none is claimable, recording the run in ingestion_run.

        Returns the run summary. Raises (after recording a failed run) when the run has to
        stop: the circuit is open, the database failed, or too many unexpected errors in a row.
        "None claimable" can also mean the remaining tasks are backing off, so the summary
        ends with the number of tasks in each state (`queue_after`).
        """
        self._unexpected_in_a_row = 0
        self._denied_in_a_row = 0
        self._raw_bytes = 0
        # The adapter's counter is cumulative; count only this run's 429s.
        self._rate_limited_at_start = self.tenrai_adapter.rate_limited_count
        self._requests_at_start = self.tenrai_adapter.request_count
        self._retries_at_start = self.tenrai_adapter.retry_count
        summary = {
            "tasks": 0,
            "done": 0,
            "first_try_tasks": 0,
            "done_first_try": 0,
            "failed": 0,
            "dead": 0,
            "gone": 0,
            "task_seconds": 0.0,
            "max_task_seconds": 0.0,
            "by_resource": {},
        }
        started = time.monotonic()
        run_id = await self.metrics_repository.start_run(JOB_NAME)
        await self.metrics_repository.commit()
        logger.info("fetch_run_started", run_id=run_id)

        try:
            while True:
                task_started = time.monotonic()
                outcome = await self.fetch_job_from_tasks()
                if outcome is None:
                    break
                self._record(summary, outcome, time.monotonic() - task_started)
                summary["rate_limited_amount"] = self._rate_limited()
                summary["requests"] = self._requests()
                summary["retries"] = self._retries()
                summary["raw_bytes"] = self._raw_bytes

                if summary["tasks"] % PROGRESS_EVERY_TASKS == 0:
                    logger.info(
                        "fetch_run_progress", run_id=run_id, **self._metrics(summary, started)
                    )
                if self._unexpected_in_a_row >= MAX_CONSECUTIVE_UNEXPECTED:
                    raise RepeatedUnexpectedErrors(self._unexpected_in_a_row)
                if self._denied_in_a_row >= MAX_CONSECUTIVE_DENIED:
                    raise AccessDeniedError(
                        f"{self._denied_in_a_row} access denied answers in a row"
                    )
        except BaseException as exc:
            # Includes Ctrl-C and cancellation. The cause was already logged where it happened;
            # this logs how far the run got.
            stopped = self._metrics(summary, started)
            stopped["stopped_by"] = type(exc).__name__
            stopped["rate_limited_amount"] = self._rate_limited()
            stopped["requests"] = self._requests()
            stopped["retries"] = self._retries()
            stopped["raw_bytes"] = self._raw_bytes
            stopped["queue_after"] = await self._queue_counts()
            logger.error("fetch_run_stopped", run_id=run_id, **stopped)
            await self._record_run(run_id, RunStatus.FAILED, stopped, self._describe(exc))
            raise

        summary = self._metrics(summary, started)
        summary["stopped_by"] = None
        summary["queue_after"] = await self._queue_counts()
        summary["rate_limited_amount"] = self._rate_limited()
        summary["requests"] = self._requests()
        summary["retries"] = self._retries()
        summary["raw_bytes"] = self._raw_bytes
        await self._record_run(run_id, RunStatus.SUCCEEDED, summary)
        logger.info("fetch_run_finished", run_id=run_id, **summary)
        return summary

    async def fetch_job_from_tasks(self) -> TaskOutcome | None:
        """Process the next claimable task and return what happened to it.

        Returns None when no task is claimable. Raises CircuitOpenError or RawStoreError (the
        task stays claimable, no attempt is used) or a database error (after a rollback).
        """
        task = await self.task_repository.claim_next()
        if not task:
            return None

        first_try = task.attempts == 0
        key = {"mal_id": task.mal_id, "resource": task.resource.value}
        seen: list = []  # raw responses fetched for this task, logged if it breaks
        stored_bytes = 0
        try:
            payload = await self._fetch(task, seen)
            stored_bytes = await self.raw_store.put(task.mal_id, task.resource, payload)
            await self.raw_tenrai_repo.upsert(
                mal_id=task.mal_id, resource=task.resource, size_bytes=stored_bytes
            )
            await self.task_repository.mark_done(task)
            status = TaskStatus.DONE
            self._unexpected_in_a_row = 0
            self._denied_in_a_row = 0
        except GoneError:
            logger.info("task_gone", **key)
            self._denied_in_a_row = 0
            await self.task_repository.mark_gone(task)
            status = TaskStatus.GONE
        except PermanentError as exc:
            logger.error("task_dead", status_code=exc.status_code, **key)
            self._denied_in_a_row = 0
            await self.task_repository.mark_dead(task, self._describe(exc))
            status = TaskStatus.DEAD
        except CircuitOpenError:
            # No request was sent, so this is not an attempt: release the lock and stop.
            logger.warning("task_released_circuit_open", **key)
            await self.task_repository.rollback()
            raise
        except RawStoreError:
            # The response was fetched but could not be stored. That says nothing about this
            # anime (bad credentials, S3 down), so release the task without an attempt and stop.
            logger.error("task_released_raw_store_failed", **key)
            await self.task_repository.rollback()
            raise
        except AccessDeniedError as exc:
            # One 401/403 can be a blip, so the task takes an attempt and backs off. The run
            # stops after MAX_CONSECUTIVE_DENIED in a row (checked by the loop).
            self._denied_in_a_row += 1
            logger.error(
                "task_access_denied",
                status_code=exc.status_code,
                consecutive=self._denied_in_a_row,
                **key,
            )
            await self.task_repository.mark_failed(task, self._describe(exc))
            status = TaskStatus.FAILED
        except TransientError as exc:
            logger.warning("task_failed", status_code=exc.status_code, **key)
            await self.task_repository.mark_failed(task, self._describe(exc))
            status = TaskStatus.FAILED
        except Exception as exc:
            if isinstance(exc, SQLAlchemyError):
                # The transaction is unusable: mark nothing, keep the task pending, stop.
                logger.exception("task_aborted_database", **key)
                await self.task_repository.rollback()
                raise
            self._unexpected_in_a_row += 1
            logger.exception(
                "task_failed_unexpected",
                consecutive=self._unexpected_in_a_row,
                response=json.dumps(seen[-1], default=str)[:PAYLOAD_LOG_CHARS] if seen else None,
                **key,
            )
            await self.task_repository.mark_failed(task, self._describe(exc))
            status = TaskStatus.FAILED

        except BaseException:
            # Ctrl-C or cancellation: not an attempt, so release the task and let it be re-claimed.
            logger.exception("task_interrupted", **key)
            await self.task_repository.rollback()
            raise

        await self.task_repository.commit()
        if status is TaskStatus.DONE:
            self._raw_bytes += stored_bytes  # counted only once committed
        return TaskOutcome(task.resource, status, first_try)

    async def _fetch(self, task: IngestionTask, seen: list) -> dict | list[dict]:
        """The payload to store: one response, or the list of pages for episodes."""
        mal_id = task.mal_id
        if task.resource == Resource.EPISODES:
            return await self._fetch_episodes(mal_id, seen)
        if task.resource == Resource.DETAILS:
            response = await self.tenrai_adapter.get_full(mal_id)
        elif task.resource == Resource.STATISTICS:
            response = await self.tenrai_adapter.get_statistics(mal_id)
        elif task.resource == Resource.REVIEWS:
            response = await self.tenrai_adapter.get_reviews(mal_id)  # page 1 only (ADR 0014)
        else:
            raise PermanentError(f"no fetcher for resource {task.resource.value}")
        seen.append(response)
        return response

    async def _fetch_episodes(self, mal_id: int, seen: list) -> list[dict]:
        """Every episodes page. Nothing is returned (so nothing is stored) if any page fails."""
        pages = []
        page = 1
        while True:
            response = await self.tenrai_adapter.get_episodes(mal_id, page=page)
            seen.append(response)
            pages.append(response)
            if not (response.get("pagination") or {}).get("has_next_page"):
                return pages
            page += 1
            if page > MAX_EPISODE_PAGES:
                raise PermanentError(f"episodes still paging after {MAX_EPISODE_PAGES} pages")

    def _retries(self) -> int:
        return self.tenrai_adapter.retry_count - self._retries_at_start

    def _requests(self) -> int:
        return self.tenrai_adapter.request_count - self._requests_at_start

    def _rate_limited(self) -> int:
        return self.tenrai_adapter.rate_limited_count - self._rate_limited_at_start

    async def _queue_counts(self) -> dict[str, int]:
        """Tasks per state; empty if the database cannot be read."""
        try:
            return await self.task_repository.count_by_status()
        except Exception:
            logger.warning("queue_count_failed")
            return {}

    async def _record_run(
        self, run_id: int, status: RunStatus, summary: dict, error: str | None = None
    ) -> None:
        """Save the run outcome. A failure here is logged, never allowed to mask the job result."""
        try:
            await self.metrics_repository.finish_run(run_id, status, summary, error)
            await self.metrics_repository.commit()
        except Exception:
            logger.exception("run_record_failed", run_id=run_id)

    @staticmethod
    def _record(summary: dict, outcome: TaskOutcome, seconds: float) -> None:
        summary["tasks"] += 1
        summary[outcome.status.value] += 1
        if outcome.first_try:
            summary["first_try_tasks"] += 1
        if outcome.status is TaskStatus.DONE and outcome.first_try:
            summary["done_first_try"] += 1
        summary["task_seconds"] += seconds
        summary["max_task_seconds"] = max(summary["max_task_seconds"], seconds)
        bucket = summary["by_resource"].setdefault(
            outcome.resource.value, {"count": 0, "seconds": 0.0}
        )
        bucket["count"] += 1
        bucket["seconds"] += seconds

    @staticmethod
    def _metrics(summary: dict, started: float) -> dict:
        """Add wall time and per-task throughput/latency figures for docs/METRICS.md."""
        elapsed = time.monotonic() - started
        tasks = summary["tasks"]
        metrics = dict(summary, elapsed_seconds=elapsed)
        if tasks:
            metrics["tasks_per_minute"] = tasks / elapsed * 60
            metrics["requests_per_minute"] = summary["requests"] / elapsed * 60
            metrics["avg_task_ms"] = summary["task_seconds"] / tasks * 1000
        metrics["by_resource"] = {
            name: {
                "count": bucket["count"],
                "seconds": round(bucket["seconds"], 3),
                "avg_ms": round(bucket["seconds"] / bucket["count"] * 1000, 3),
            }
            for name, bucket in summary["by_resource"].items()
        }
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()}

    @staticmethod
    def _describe(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"
