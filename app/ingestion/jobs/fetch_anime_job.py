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
    CircuitOpenError,
    GoneError,
    PermanentError,
    RepeatedUnexpectedErrors,
    TransientError,
)
from app.ingestion.models import IngestionTask
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository
from app.ingestion.repository.task_repository import TaskRepository

logger = structlog.get_logger(__name__)

JOB_NAME = "fetch_tasks"
# ADR 0014 says at most 12 episode pages exist; this only stops a runaway loop.
MAX_EPISODE_PAGES = 100
# This many unexpected failures in a row means a bug in our code, not one bad anime.
MAX_CONSECUTIVE_UNEXPECTED = 10
# A review page can be 240 KB; cap what one log line carries.
PAYLOAD_LOG_CHARS = 1000
PROGRESS_EVERY_TASKS = 500


class TaskOutcome(NamedTuple):
    resource: Resource
    status: TaskStatus  # done, gone, dead or failed (failed may become dead after enough attempts)


class FetchAnimeJob:
    """Claims tasks, fetches each Resource from Tenrai and stores the raw response.

    `task_repository` and `raw_tenrai_repo` must share one session, so the raw row and the
    task's new state are committed together: a crash between them saves neither.
    `metrics_repository` needs its own session so the run record survives a rollback.
    """

    def __init__(
        self,
        tenrai_adapter: TenraiAdapter,
        task_repository: TaskRepository,
        raw_tenrai_repo: RawTenraiRepository,
        metrics_repository: MetricsRepository,
    ):
        self.tenrai_adapter = tenrai_adapter
        self.task_repository = task_repository
        self.raw_tenrai_repo = raw_tenrai_repo
        self.metrics_repository = metrics_repository
        self._unexpected_in_a_row = 0

    async def fetch_from_tasks(self) -> dict:
        """Process tasks until none is claimable, recording the run in ingestion_run.

        Returns the run summary. Raises (after recording a failed run) when the run has to
        stop: the circuit is open, the database failed, or too many unexpected errors in a row.
        "None claimable" can also mean the remaining tasks are backing off, so the summary
        ends with the number of tasks in each state (`queue_after`).
        """
        self._unexpected_in_a_row = 0
        summary = {
            "tasks": 0,
            "done": 0,
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

                if summary["tasks"] % PROGRESS_EVERY_TASKS == 0:
                    logger.info(
                        "fetch_run_progress", run_id=run_id, **self._metrics(summary, started)
                    )
                if self._unexpected_in_a_row >= MAX_CONSECUTIVE_UNEXPECTED:
                    raise RepeatedUnexpectedErrors(self._unexpected_in_a_row)
        except Exception as exc:
            # The cause was already logged where it happened; this logs how far the run got.
            stopped = self._metrics(summary, started)
            stopped["stopped_by"] = type(exc).__name__
            stopped["queue_after"] = await self._queue_counts()
            logger.error("fetch_run_stopped", run_id=run_id, **stopped)
            await self._record_run(run_id, RunStatus.FAILED, stopped, self._describe(exc))
            raise

        summary = self._metrics(summary, started)
        summary["stopped_by"] = None
        summary["queue_after"] = await self._queue_counts()
        await self._record_run(run_id, RunStatus.SUCCEEDED, summary)
        logger.info("fetch_run_finished", run_id=run_id, **summary)
        return summary

    async def fetch_job_from_tasks(self) -> TaskOutcome | None:
        """Process the next claimable task and return what happened to it.

        Returns None when no task is claimable. Raises CircuitOpenError (the task stays
        claimable, no attempt is used) or a database error (after a rollback).
        """
        task = await self.task_repository.claim_next()
        if not task:
            return None

        key = {"mal_id": task.mal_id, "resource": task.resource.value}
        seen: list = []  # raw responses fetched for this task, logged if it breaks
        try:
            payload = await self._fetch(task, seen)
            await self.raw_tenrai_repo.upsert(
                mal_id=task.mal_id, resource=task.resource, payload=payload
            )
            await self.task_repository.mark_done(task)
            status = TaskStatus.DONE
            self._unexpected_in_a_row = 0
        except GoneError:
            logger.info("task_gone", **key)
            await self.task_repository.mark_gone(task)
            status = TaskStatus.GONE
        except PermanentError as exc:
            logger.error("task_dead", status_code=exc.status_code, **key)
            await self.task_repository.mark_dead(task, self._describe(exc))
            status = TaskStatus.DEAD
        except CircuitOpenError:
            # No request was sent, so this is not an attempt: release the lock and stop.
            logger.warning("task_released_circuit_open", **key)
            await self.task_repository.rollback()
            raise
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

        await self.task_repository.commit()
        return TaskOutcome(task.resource, status)

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
