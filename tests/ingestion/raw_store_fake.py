import json

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.exceptions import RawStoreError


class InMemoryRawStore:
    """Stands in for RawStore (S3): same put/get, objects kept in a dict."""

    def __init__(self) -> None:
        self.objects: dict[tuple[int, Resource], dict | list] = {}
        self.fail_put: Exception | None = None

    async def put(self, mal_id: int, resource: Resource, payload: dict | list) -> int:
        if self.fail_put:
            raise self.fail_put
        self.objects[(mal_id, Resource(resource))] = payload
        return len(json.dumps(payload))

    async def get(self, mal_id: int, resource: Resource) -> dict | list:
        try:
            return self.objects[(mal_id, Resource(resource))]
        except KeyError:
            raise RawStoreError(f"get {resource}/{mal_id} failed: NoSuchKey") from None
