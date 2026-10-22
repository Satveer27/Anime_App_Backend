import asyncio
import gzip
import json

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.ingestion.enums.resource_enum import Resource
from app.ingestion.exceptions import RawStoreError

logger = structlog.get_logger(__name__)


class RawStore:
    """Raw Tenrai responses as S3 objects (ADR 0016).

    The key is derived from (resource, mal_id), never generated, so a refresh overwrites the
    object and the `raw_tenrai` row only has to say that it exists. Payloads are gzipped JSON.
    boto3 is synchronous, so each call runs in a worker thread.
    """

    def __init__(
        self, bucket: str | None, prefix: str = "tenrai", region: str | None = None, client=None
    ) -> None:
        if not bucket:
            raise ValueError("RAW_S3_BUCKET is not set")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = client or boto3.client(
            "s3",
            region_name=region,
            config=Config(
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    @classmethod
    def from_settings(cls) -> "RawStore":
        return cls(settings.raw_s3_bucket, settings.raw_s3_prefix, settings.aws_region or None)

    def key_for(self, mal_id: int, resource: Resource) -> str:
        return f"{self._prefix}/{Resource(resource).value}/{mal_id}.json.gz"

    async def put(self, mal_id: int, resource: Resource, payload: dict | list) -> int:
        """Store one response, overwriting any earlier one, and return the stored size in bytes."""
        key = self.key_for(mal_id, resource)
        body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                ContentEncoding="gzip",
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("raw_store_put_failed", key=key, error=type(exc).__name__)
            raise RawStoreError(f"put {key} failed: {type(exc).__name__}") from exc
        return len(body)

    async def get(self, mal_id: int, resource: Resource) -> dict | list:
        key = self.key_for(mal_id, resource)
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=key
            )
            body = await asyncio.to_thread(response["Body"].read)
        except (BotoCoreError, ClientError) as exc:
            logger.error("raw_store_get_failed", key=key, error=type(exc).__name__)
            raise RawStoreError(f"get {key} failed: {type(exc).__name__}") from exc
        return json.loads(gzip.decompress(body))
