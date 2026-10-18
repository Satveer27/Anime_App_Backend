import time

import structlog

from app.ingestion.adapters.tenrai import TenraiAdapter
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.task_repository import TaskRepository

logger = structlog.get_logger(__name__)

# Characters get their own pass later (ADR 0014).
DISCOVERY_RESOURCES = (
    Resource.DETAILS,
    Resource.STATISTICS,
    Resource.REVIEWS,
    Resource.EPISODES,
)
# Sent to Tenrai so Music, CM and PV are never listed (docs/METRICS.md).
LIST_TYPES = "tv,movie,ova,special,ona,tv_special"
# Safety net in case Tenrai ignores or mis-parses the type filter.
EXCLUDED_TYPES = {"Music", "CM", "PV"}
PROGRESS_EVERY_PAGES = 25
JOB_NAME = "discovery_overall"


class DiscoveryJob:
    def __init__(
        self,
        tenrai_adapter: TenraiAdapter,
        task_repository: TaskRepository,
        metrics_repository: MetricsRepository,
    ):
        self.tenrai_adapter = tenrai_adapter
        self.task_repository = task_repository
        # Must use its own session so the run record survives a failure in the task session.
        self.metrics_repository = metrics_repository

    async def run_job_overall(self, page: int = 1) -> dict[str, float]:
        """Page through Tenrai's anime list and create the missing tasks.

        Commits once per page. A failed page stops the job and re-raises: everything before it
        is already saved, and re-running is safe because existing tasks are left untouched.
        The run and its metrics are recorded in ingestion_run, whether it succeeds or fails.
        """

        has_next = True
        summary = {
            "pages": 0,
            "anime_seen": 0,
            "anime_skipped": 0,
            "anime_skipped_type": 0,
            "tasks_inserted": 0,
            "list_seconds": 0.0,
            "db_seconds": 0.0,
            "max_list_seconds": 0.0,
        }
        started = time.monotonic()
        run_id = await self.metrics_repository.start_run(JOB_NAME)
        await self.metrics_repository.commit()
        logger.info("discovery_started", run_id=run_id, start_page=page)

        try:
            while has_next:
                list_started = time.monotonic()
                result = await self.tenrai_adapter.list_anime(page=page, type=LIST_TYPES)
                list_elapsed = time.monotonic() - list_started
                summary["list_seconds"] += list_elapsed
                summary["max_list_seconds"] = max(summary["max_list_seconds"], list_elapsed)
                pagination = result.get("pagination") or {}
                has_next = bool(pagination.get("has_next_page"))

                rows = []
                for anime in result.get("data") or []:
                    summary["anime_seen"] += 1
                    mal_id = anime.get("mal_id")
                    if not mal_id:
                        logger.warning("anime_missing_mal_id", anime=anime)
                        summary["anime_skipped"] += 1
                        continue

                    if anime.get("type") in EXCLUDED_TYPES:
                        summary["anime_skipped_type"] += 1
                        continue

                    for resource in DISCOVERY_RESOURCES:
                        rows.append((mal_id, resource))

                db_started = time.monotonic()
                summary["tasks_inserted"] += await self.task_repository.insert_tasks(rows)
                await self.task_repository.commit()
                summary["db_seconds"] += time.monotonic() - db_started
                summary["pages"] += 1

                if summary["pages"] % PROGRESS_EVERY_PAGES == 0:
                    logger.info("discovery_progress", **self._metrics(summary, started))
                page += 1
        except Exception as exc:
            failed = self._metrics(summary, started)
            logger.exception("discovery_failed", run_id=run_id, failed_page=page, **failed)
            await self._record_run(run_id, RunStatus.FAILED, failed, f"{type(exc).__name__}: {exc}")
            raise

        if summary["anime_skipped_type"]:
            logger.warning("discovery_type_filter_leaked", count=summary["anime_skipped_type"])
        summary = self._metrics(summary, started)
        await self._record_run(run_id, RunStatus.SUCCEEDED, summary)
        logger.info("discovery_finished", run_id=run_id, **summary)
        return summary

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
    def _metrics(summary: dict[str, float], started: float) -> dict[str, float]:
        """Add wall time and derived throughput/latency figures for docs/METRICS.md."""
        elapsed = time.monotonic() - started
        pages = summary["pages"]
        metrics = dict(summary, elapsed_seconds=elapsed)
        if pages:
            metrics["requests_per_minute"] = pages / elapsed * 60
            metrics["avg_list_ms"] = summary["list_seconds"] / pages * 1000
            metrics["avg_db_ms"] = summary["db_seconds"] / pages * 1000
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()}
