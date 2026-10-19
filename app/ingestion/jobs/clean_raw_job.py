import json
import time
from datetime import datetime
from typing import NamedTuple

import structlog

from app.catalogue.repository import CatalogueRepository
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.models import RawTenrai
from app.ingestion.parsers.details import clean_full
from app.ingestion.parsers.episodes import clean_episodes
from app.ingestion.parsers.reviews import clean_reviews
from app.ingestion.parsers.statistics import clean_statistics
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.repository.raw_tenrai_repository import RawTenraiRepository

logger = structlog.get_logger(__name__)

BATCH_ROWS = 500
PROGRESS_EVERY_BATCHES = 20
# How much of a raw response to log when it cannot be parsed.
PAYLOAD_LOG_CHARS = 1000
# How many anime ids to list in the "skipped, no anime row" warning.
SKIPPED_IDS_LOGGED = 20

COUNT_KEYS = (
    "rows_read",
    "anime_changed",
    "statistics_written",
    "episodes_written",
    "reviews_written",
    "parse_failed",
    "skipped_no_anime",
    "skipped_unsupported",
)
Cursor = tuple[int, Resource]


class BatchResult(NamedTuple):
    # Pass this as `after` for the next batch, or None when nothing was left to read.
    next_after: Cursor | None
    counts: dict[str, int]


class CleanAnimeJob:
    """Parses raw Tenrai rows into the catalogue, one batch of raw rows at a time (ADR 0014)."""

    def __init__(
        self,
        catalogue_repository: CatalogueRepository,
        raw_tenrai_repository: RawTenraiRepository,
        metric_repository: MetricsRepository,
    ):
        self.catalogue_repository = catalogue_repository
        self.raw_tenrai_repository = raw_tenrai_repository
        self.metric_repository = metric_repository

    async def full_clean_loop(
        self,
        since: datetime | None = None,
        after: Cursor | None = None,
        batch_size: int = BATCH_ROWS,
    ) -> dict:
        """Clean batch after batch until no raw rows are left, and return the run summary.

        `since`: only rows fetched at or after this time (None cleans everything).
        `after`: where to resume (None starts at the beginning), e.g. the `resume_after` logged
        by a failed run. Each batch is committed on its own, so a failure loses only that batch.
        """
        totals = dict.fromkeys(COUNT_KEYS, 0)
        batches = 0
        started = time.monotonic()
        logger.info("clean_run_started", since=since, after=after, batch_size=batch_size)

        try:
            while True:
                result = await self._clean_anime(after, since, batch_size)
                if result.next_after is None:
                    break
                after = result.next_after
                batches += 1
                for key, value in result.counts.items():
                    totals[key] += value
                if batches % PROGRESS_EVERY_BATCHES == 0:
                    logger.info(
                        "clean_run_progress",
                        batches=batches,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        **totals,
                    )
        except Exception:
            # `after` is the last batch that was committed: the place to resume from
            logger.exception("clean_run_failed", resume_after=after, batches=batches, **totals)
            raise

        summary = {
            **totals,
            "batches": batches,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        logger.info("clean_run_finished", **summary)
        return summary

    async def _clean_anime(
        self, after: Cursor | None, since: datetime | None, batch_size: int = BATCH_ROWS
    ) -> BatchResult:
        """Clean the next batch of raw rows and commit it.

        `since`: only rows fetched at or after this time (None cleans everything).
        `after`: the cursor returned by the previous batch (None to start from the beginning).
        A database error rolls the batch back and is re-raised for the caller to log, so
        nothing of a failed batch is saved and `after` is still the place to resume from.
        """
        counts = dict.fromkeys(COUNT_KEYS, 0)
        rows = await self.raw_tenrai_repository.get_batch(since, after, batch_size)
        if not rows:
            return BatchResult(None, counts)
        counts["rows_read"] = len(rows)

        try:
            await self._clean_rows(rows, counts)
            await self.catalogue_repository.commit()
        except Exception:
            await self.catalogue_repository.rollback()
            raise

        last = rows[-1]
        logger.info("clean_batch_done", last_mal_id=last.mal_id, **counts)
        return BatchResult((last.mal_id, last.resource), counts)

    async def _clean_rows(self, rows: list[RawTenrai], counts: dict[str, int]) -> None:
        details = [row for row in rows if row.resource == Resource.DETAILS]
        others = [row for row in rows if row.resource != Resource.DETAILS]

        # The anime rows go first: the other tables point at them.
        animes = []
        for row in details:
            anime = clean_full(row.payload)
            if anime is None:
                self._parse_failed(row, counts)
            else:
                animes.append(anime)
        counts["anime_changed"] = await self.catalogue_repository.upsert_anime(animes)

        # One query tells us which anime exist, including the ones just upserted.
        present = await self.catalogue_repository.existing_anime_ids({r.mal_id for r in others})

        statistics, episodes, reviews = [], [], []
        skipped_ids = set()
        for row in others:
            if row.mal_id not in present:
                counts["skipped_no_anime"] += 1
                skipped_ids.add(row.mal_id)
            elif row.resource == Resource.STATISTICS:
                stat = clean_statistics(row.payload, row.mal_id)
                if stat is None:
                    self._parse_failed(row, counts)
                else:
                    statistics.append(stat)
            elif row.resource == Resource.EPISODES:
                episodes.extend(clean_episodes(row.payload, row.mal_id))
            elif row.resource == Resource.REVIEWS:
                reviews.extend(clean_reviews(row.payload, row.mal_id))
            else:
                counts["skipped_unsupported"] += 1
                logger.warning(
                    "clean_resource_unsupported", mal_id=row.mal_id, resource=row.resource.value
                )

        if skipped_ids:
            logger.warning(
                "clean_skipped_no_anime",
                anime=len(skipped_ids),
                sample=sorted(skipped_ids)[:SKIPPED_IDS_LOGGED],
            )

        counts["statistics_written"] = await self.catalogue_repository.upsert_statistics(statistics)
        counts["episodes_written"] = await self.catalogue_repository.upsert_episodes(episodes)
        counts["reviews_written"] = await self.catalogue_repository.upsert_reviews(reviews)

    @staticmethod
    def _parse_failed(row: RawTenrai, counts: dict[str, int]) -> None:
        counts["parse_failed"] += 1
        logger.warning(
            "clean_parse_failed",
            mal_id=row.mal_id,
            resource=row.resource.value,
            payload=json.dumps(row.payload, default=str)[:PAYLOAD_LOG_CHARS],
        )
