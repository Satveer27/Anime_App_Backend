import asyncio
from datetime import UTC, datetime
from pathlib import Path

import boto3
import structlog
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.exceptions import RawStoreError

logger = structlog.get_logger(__name__)

# Fixed, not a setting: the pipeline's IAM policy only allows this prefix.
PREFIX = "imdb"


class ImdbStore:
    """The archived IMDb dataset files in S3.

    The key is built from the snapshot's time and the file, never found by listing the bucket:
    `imdb/2026-11-09T030000Z/title.basics.tsv.gz`. A rerun with the same time overwrites the
    same keys. Files are uploaded unaltered. boto3 is synchronous, so each call runs in a
    worker thread.
    """

    def __init__(self, bucket: str | None, region: str | None = None, client=None) -> None:
        if not bucket:
            raise ValueError("RAW_S3_BUCKET is not set")
        self._bucket = bucket
        self._client = client or boto3.client(
            "s3",
            region_name=region,
            config=Config(
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    @classmethod
    def from_settings(cls) -> "ImdbStore":
        return cls(settings.raw_s3_bucket, settings.aws_region or None)

    @staticmethod
    def key_for(finished_at: datetime, file: ImdbFile) -> str:
        if finished_at.tzinfo is None:
            raise ValueError("finished_at must be timezone-aware")
        stamp = finished_at.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
        return f"{PREFIX}/{stamp}/{ImdbFile(file).filename}"

    async def put(self, finished_at: datetime, file: ImdbFile, path: Path) -> str:
        """Upload one file, overwriting any object at the same key, and return the key.

        upload_file switches to a multipart upload for big files, so the 200+ MB basics file is
        sent in parts and a failed part is retried alone.
        """
        key = self.key_for(finished_at, file)
        try:
            await asyncio.to_thread(
                self._client.upload_file,
                Filename=str(path),
                Bucket=self._bucket,
                Key=key,
                # Not ContentEncoding: the object is a .gz file to store, not a compressed body
                # for a client to unpack.
                ExtraArgs={"ContentType": "application/gzip"},
            )
        except (BotoCoreError, ClientError, S3UploadFailedError) as exc:
            logger.error("imdb_store_put_failed", key=key, error=type(exc).__name__)
            raise RawStoreError(f"put {key} failed: {type(exc).__name__}") from exc
        return key
