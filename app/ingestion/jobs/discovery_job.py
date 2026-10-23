import time

import structlog

from app.catalogue.repository import CatalogueRepository
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
WEEKLY_JOB_NAME = "discovery_weekly"
# The Active tier (CONTEXT.md) is asked of Tenrai with these two words, one list each...
WEEKLY_LIST_STATUSES = ("airing", "upcoming")
# ...but the catalogue stores MAL's own wording, which is what the database step looks up.
ACTIVE_CATALOGUE_STATUSES = ("Currently Airing", "Not yet aired")
# One insert statement binds two values per task and Postgres allows 32,767 bound values.
TASKS_PER_STATEMENT = 5000


class DiscoveryJob:
    def __init__(
        self,
        tenrai_adapter: TenraiAdapter,
        task_repository: TaskRepository,
        metrics_repository: MetricsRepository,
        catalogue_repository: CatalogueRepository | None = None,
    ):
        self.tenrai_adapter = tenrai_adapter
        self.task_repository = task_repository
        # Must use its own session so the run record survives a failure in the task session.
        self.metrics_repository = metrics_repository
        # Only the weekly run reads the catalogue; it shares the task repository's session.
        self.catalogue_repository = catalogue_repository

    async def run_job_overall(self, page: int = 1) -> dict:
        """Page through Tenrai's whole anime list and create the missing tasks.

        Existing tasks are left untouched, so re-running (even from page 1) is always safe.
        """
        return await self._run(JOB_NAME, page, {}, DISCOVERY_RESOURCES, reset_existing=False)

    async def run_job_refresh(
        self,
        job_name: str,
        filters: dict,
        resources: tuple[Resource, ...] = DISCOVERY_RESOURCES,
        page: int = 1,
    ) -> dict:
        """Page through a filtered anime list for a refresh.

        Anime we have never seen get their tasks created. Anime we already have get their
        `done` and `failed` tasks sent back to `pending`; `dead` and `gone` stay for inspection.
        `job_name` is the name the run is recorded under (e.g. "discovery_weekly").

        Run it before the worker starts, not in the middle of a worker run: it would send tasks
        the worker has already finished back to pending.
        """
        return await self._run(job_name, page, filters, resources, reset_existing=True)

    async def run_job_weekly(
        self,
        job_name: str = WEEKLY_JOB_NAME,
        resources: tuple[Resource, ...] = DISCOVERY_RESOURCES,
    ) -> dict:
        """The weekly refresh of the Active tier: reset the tasks of every anime that is live.

        1. Tenrai's `airing` list, then its `upcoming` list: new anime get their tasks, known
           ones have their `done` and `failed` tasks sent back to `pending`.
        2. Every anime the catalogue holds as airing or not yet aired, which includes the ones
           that finished since the last run and so no longer show up in Tenrai's lists. Their
           final episodes and score are fetched once more.

        Each list is recorded under its own name (`{job_name}_airing`, `{job_name}_upcoming`)
        and resumes from its own last failed run, so a retry never starts one list at the
        other's page. Run it before the fetch worker starts, not during a worker run.
        Returns the summary of each step.
        """
        if self.catalogue_repository is None:
            raise ValueError("run_job_weekly needs a catalogue repository")

        summary = {}
        for status in WEEKLY_LIST_STATUSES:
            name = f"{job_name}_{status}"
            summary[status] = await self._run(
                name,
                await self._resume_page(name),
                {"status": status},
                resources,
                reset_existing=True,
            )
        summary["catalogue"] = await self._reset_active_from_catalogue(resources)
        return summary

    async def _resume_page(self, job_name: str) -> int:
        """Where this job's last run stopped, or 1 if it did not fail."""
        last = await self.metrics_repository.latest_run(job_name)
        if last and last.status == RunStatus.FAILED and last.summary:
            return last.summary.get("resume_page") or 1
        return 1

    async def _reset_active_from_catalogue(self, resources: tuple[Resource, ...]) -> dict:
        """Send the tasks of every anime stored as airing or not yet aired back to pending."""
        ids = await self.catalogue_repository.anime_ids_with_status(ACTIVE_CATALOGUE_STATUSES)
        rows = [(mal_id, resource) for mal_id in sorted(ids) for resource in resources]
        inserted = reset = 0
        for start in range(0, len(rows), TASKS_PER_STATEMENT):
            new, again = await self.task_repository.insert_or_reset_tasks(
                rows[start : start + TASKS_PER_STATEMENT]
            )
            inserted += new
            reset += again
        await self.task_repository.commit()
        result = {"anime": len(ids), "tasks_inserted": inserted, "tasks_reset": reset}
        logger.info("discovery_weekly_catalogue_reset", **result)
        return result

    async def _run(
        self,
        job_name: str,
        page: int,
        filters: dict,
        resources: tuple[Resource, ...],
        reset_existing: bool,
    ) -> dict:
        """The paging loop shared by the overall and refresh runs.

        Commits once per page. A failed page stops the job and re-raises: everything before it
        is already saved. The run and its metrics are recorded in ingestion_run, whether it
        succeeds or fails; on failure summary["resume_page"] is the page to pass next.
        """
        params = {"type": LIST_TYPES, **filters}
        has_next = True
        summary = {
            "start_page": page,
            "pages": 0,
            "anime_seen": 0,
            "anime_skipped": 0,
            "anime_skipped_type": 0,
            "tasks_inserted": 0,
            "tasks_reset": 0,
            "tasks_unchanged": 0,
            "list_seconds": 0.0,
            "db_seconds": 0.0,
            "max_list_seconds": 0.0,
        }
        started = time.monotonic()
        # the adapter's counters are cumulative, so report the difference over this run
        rate_limited_at_start = self.tenrai_adapter.rate_limited_count
        requests_at_start = self.tenrai_adapter.request_count
        retries_at_start = self.tenrai_adapter.retry_count
        run_id = await self.metrics_repository.start_run(job_name)
        await self.metrics_repository.commit()
        logger.info(
            "discovery_started",
            run_id=run_id,
            job=job_name,
            start_page=page,
            reset_existing=reset_existing,
            filters=filters,
        )

        try:
            while has_next:
                list_started = time.monotonic()
                result = await self.tenrai_adapter.list_anime(page=page, **params)
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

                    for resource in resources:
                        rows.append((mal_id, resource))
                # an anime listed twice on one page would make the upsert hit a row twice
                rows = list(dict.fromkeys(rows))

                db_started = time.monotonic()
                if reset_existing:
                    inserted, reset = await self.task_repository.insert_or_reset_tasks(rows)
                    summary["tasks_inserted"] += inserted
                    summary["tasks_reset"] += reset
                    summary["tasks_unchanged"] += len(rows) - inserted - reset
                else:
                    summary["tasks_inserted"] += await self.task_repository.insert_tasks(rows)
                await self.task_repository.commit()
                summary["db_seconds"] += time.monotonic() - db_started
                summary["pages"] += 1
                summary["rate_limited_amount"] = (
                    self.tenrai_adapter.rate_limited_count - rate_limited_at_start
                )
                summary["requests"] = self.tenrai_adapter.request_count - requests_at_start
                summary["retries"] = self.tenrai_adapter.retry_count - retries_at_start

                if summary["pages"] % PROGRESS_EVERY_PAGES == 0:
                    logger.info(
                        "discovery_progress", run_id=run_id, **self._metrics(summary, started)
                    )
                page += 1
        except BaseException as exc:  # includes Ctrl-C and cancellation; re-raised below
            failed = self._metrics(summary, started)
            failed["failed_page"] = page
            failed["resume_page"] = page  # nothing of this page was committed
            failed["rate_limited_amount"] = (
                self.tenrai_adapter.rate_limited_count - rate_limited_at_start
            )
            failed["requests"] = self.tenrai_adapter.request_count - requests_at_start
            failed["retries"] = self.tenrai_adapter.retry_count - retries_at_start
            logger.exception("discovery_failed", run_id=run_id, job=job_name, **failed)
            await self._record_run(run_id, RunStatus.FAILED, failed, f"{type(exc).__name__}: {exc}")
            raise

        if summary["anime_skipped_type"]:
            logger.warning("discovery_type_filter_leaked", count=summary["anime_skipped_type"])
        summary = self._metrics(summary, started)
        summary["resume_page"] = None
        summary["rate_limited_amount"] = (
            self.tenrai_adapter.rate_limited_count - rate_limited_at_start
        )
        summary["requests"] = self.tenrai_adapter.request_count - requests_at_start
        summary["retries"] = self.tenrai_adapter.retry_count - retries_at_start
        await self._record_run(run_id, RunStatus.SUCCEEDED, summary)
        logger.info("discovery_finished", run_id=run_id, job=job_name, **summary)
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
    def _metrics(summary: dict, started: float) -> dict:
        """Add wall time and derived throughput/latency figures for docs/METRICS.md."""
        elapsed = time.monotonic() - started
        pages = summary["pages"]
        metrics = dict(summary, elapsed_seconds=elapsed)
        if pages:
            metrics["requests_per_minute"] = summary["requests"] / elapsed * 60
            metrics["avg_list_ms"] = summary["list_seconds"] / pages * 1000
            metrics["avg_db_ms"] = summary["db_seconds"] / pages * 1000
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()}
