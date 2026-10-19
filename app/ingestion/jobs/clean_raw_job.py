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
from app.ingestion.parsers.links import extract_studios, extract_tags
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
    "tags_upserted",
    "studios_upserted",
    "tag_links_changed",
    "studio_links_changed",
    "statistics_written",
    "episodes_written",
    "reviews_written",
    "parse_failed",
    "skipped_no_anime",
    "skipped_unsupported",
)
Cursor = tuple[int, Resource]
Names = dict[int, str]  # tag or studio mal_id -> name


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
            # Loaded once per run; each batch adds the new or renamed ones after it commits
            tags = await self.catalogue_repository.get_tags()
            studios = await self.catalogue_repository.get_studios()
            while True:
                result = await self._clean_anime(after, since, tags, studios, batch_size)
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
        self,
        after: Cursor | None,
        since: datetime | None,
        tags: Names,
        studios: Names,
        batch_size: int = BATCH_ROWS,
    ) -> BatchResult:
        """Clean the next batch of raw rows and commit it.

        `since`: only rows fetched at or after this time (None cleans everything).
        `after`: the cursor returned by the previous batch (None to start from the beginning).
        `tags`, `studios`: the known {mal_id: name} dicts. The loop loads them once and passes
        the same ones to every batch; they are updated here only after the batch commits.
        A database error rolls the batch back and is re-raised for the caller to log, so
        nothing of a failed batch is saved and `after` is still the place to resume from.
        """
        counts = dict.fromkeys(COUNT_KEYS, 0)
        rows = await self.raw_tenrai_repository.get_batch(since, after, batch_size)
        if not rows:
            return BatchResult(None, counts)
        counts["rows_read"] = len(rows)

        try:
            new_tags, new_studios = await self._clean_rows(rows, counts, tags, studios)
            await self.catalogue_repository.commit()
        except Exception:
            await self.catalogue_repository.rollback()
            raise

        # Only now are these rows really in the database, so only now do the dicts learn them
        tags.update(new_tags)
        studios.update(new_studios)
        last = rows[-1]
        logger.info("clean_batch_done", last_mal_id=last.mal_id, **counts)
        return BatchResult((last.mal_id, last.resource), counts)

    async def _clean_rows(
        self, rows: list[RawTenrai], counts: dict[str, int], tags: Names, studios: Names
    ) -> tuple[Names, Names]:
        """Write the batch; returns the tags and studios that were new or renamed."""
        details = [row for row in rows if row.resource == Resource.DETAILS]
        others = [row for row in rows if row.resource != Resource.DETAILS]
        new_tags, new_studios = await self._clean_details(details, tags, studios, counts)
        await self._clean_others(others, counts)
        return new_tags, new_studios

    async def _clean_details(
        self, details: list[RawTenrai], tags: Names, studios: Names, counts: dict[str, int]
    ) -> tuple[Names, Names]:
        """The anime rows, then the tags and studios they point at, then the links."""
        animes = []
        tag_ids: dict[int, list[int]] = {}
        studio_ids: dict[int, list[int]] = {}
        new_tags: Names = {}
        new_studios: Names = {}
        for row in details:
            anime = clean_full(row.payload)
            if anime is None:
                self._parse_failed(row, counts)
                continue
            animes.append(anime)
            anime_tags = extract_tags(row.payload)
            anime_studios = extract_studios(row.payload)
            tag_ids[anime.mal_id] = [mal_id for mal_id, _ in anime_tags]
            studio_ids[anime.mal_id] = [mal_id for mal_id, _ in anime_studios]
            new_tags.update(
                {mal_id: name for mal_id, name in anime_tags if tags.get(mal_id) != name}
            )
            new_studios.update(
                {mal_id: name for mal_id, name in anime_studios if studios.get(mal_id) != name}
            )

        # Order matters: the links point at the anime, the tags and the studios
        counts["anime_changed"] = await self.catalogue_repository.upsert_anime(animes)
        if new_tags:
            counts["tags_upserted"] = await self.catalogue_repository.upsert_tags(new_tags.items())
        if new_studios:
            counts["studios_upserted"] = await self.catalogue_repository.upsert_studios(
                new_studios.items()
            )
        counts["tag_links_changed"] = await self.catalogue_repository.replace_anime_tags(tag_ids)
        counts["studio_links_changed"] = await self.catalogue_repository.replace_anime_studios(
            studio_ids
        )
        return new_tags, new_studios

    async def _clean_others(self, others: list[RawTenrai], counts: dict[str, int]) -> None:
        """Statistics, episodes and reviews, for the anime that have a row."""
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
