import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.ingestion.adapters.imdb import FileInfo, ImdbAdapter
from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.models import ImdbSnapshot
from app.ingestion.repository.imdb_snapshot_repository import ImdbSnapshotRepository
from app.ingestion.storage.imdb_store import ImdbStore

logger = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImdbDownloadJob:
    """Downloads the three IMDb files, archives them in S3 and records the run.

    One run is one `imdb_snapshot` row; a retry is a new run and a new row. The row is inserted
    as `running` first, so a run that fails or is killed still leaves a record, and ends as
    `archived`, `skipped` or `failed`. `snapshot_repository` should have its own session.

    `finished_at` is taken once at the start. It names the S3 keys and is stored on the row, so
    the keys can be rebuilt from the row later without listing the bucket.
    """

    def __init__(
        self,
        adapter: ImdbAdapter,
        store: ImdbStore,
        snapshot_repository: ImdbSnapshotRepository,
        temp_root: Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.snapshot_repository = snapshot_repository
        self.temp_root = temp_root
        self.clock = clock

    async def run(self) -> dict:
        """Run once and return the summary. Raises after recording a failed run."""
        started = time.monotonic()
        # Whole seconds: the key only has seconds, so the key and the stored value agree.
        finished_at = self.clock().replace(microsecond=0)
        snapshot_id = await self.snapshot_repository.start_run()
        await self.snapshot_repository.commit()
        logger.info("imdb_download_started", snapshot_id=snapshot_id, finished_at=finished_at)

        try:
            summary = await self._run(snapshot_id, finished_at)
        except BaseException as exc:
            await self._record_failure(snapshot_id, exc)
            raise
        summary["seconds"] = round(time.monotonic() - started, 2)
        logger.info("imdb_download_finished", snapshot_id=snapshot_id, **summary)
        return summary

    async def _run(self, snapshot_id: int, finished_at: datetime) -> dict:
        latest = await self.snapshot_repository.latest_archived()
        infos = {file: await self.adapter.head(file) for file in ImdbFile}
        if latest is not None and self._unchanged(latest, infos):
            await self.snapshot_repository.mark_skipped(snapshot_id)
            await self.snapshot_repository.commit()
            return {"status": SnapshotStatus.SKIPPED.value, "files": {}}

        files: dict[str, dict] = {}
        # The directory and everything in it is removed when the block ends, on success and on
        # failure alike. A file that fails a check is never uploaded.
        with tempfile.TemporaryDirectory(prefix="imdb_", dir=self.temp_root) as tmp:
            downloaded = []
            for file in ImdbFile:
                result = await self.adapter.download(file, Path(tmp))
                downloaded.append(result)
                files[file.value] = {
                    "bytes": result.size_bytes,
                    "download_seconds": round(result.download_seconds, 2),
                }
                logger.info(
                    "imdb_file_downloaded",
                    snapshot_id=snapshot_id,
                    file=file.value,
                    bytes=result.size_bytes,
                    seconds=files[file.value]["download_seconds"],
                )

            for result in downloaded:
                uploading = time.monotonic()
                key = await self.store.put(finished_at, result.file, result.path)
                upload_seconds = round(time.monotonic() - uploading, 2)
                files[result.file.value]["upload_seconds"] = upload_seconds
                logger.info(
                    "imdb_file_archived",
                    snapshot_id=snapshot_id,
                    file=result.file.value,
                    key=key,
                    seconds=upload_seconds,
                )

        await self.snapshot_repository.mark_archived(snapshot_id, finished_at, downloaded)
        await self.snapshot_repository.commit()
        return {
            "status": SnapshotStatus.ARCHIVED.value,
            "files": files,
            "total_bytes": sum(f["bytes"] for f in files.values()),
        }

    @staticmethod
    def _unchanged(latest: ImdbSnapshot, infos: dict[ImdbFile, FileInfo]) -> bool:
        """True when every file has the same ETag as in the latest archived run."""
        return all(
            getattr(latest, f"{file.name.lower()}_etag") == info.etag
            for file, info in infos.items()
        )

    async def _record_failure(self, snapshot_id: int, exc: BaseException) -> None:
        """Mark the run failed. Never raises, so the original error is the one that surfaces."""
        logger.error("imdb_download_failed", snapshot_id=snapshot_id, error=type(exc).__name__)
        try:
            # After a database error the session is unusable until it is rolled back.
            await self.snapshot_repository.rollback()
            await self.snapshot_repository.mark_failed(snapshot_id)
            await self.snapshot_repository.commit()
        except Exception:
            logger.exception("imdb_failure_not_recorded", snapshot_id=snapshot_id)
