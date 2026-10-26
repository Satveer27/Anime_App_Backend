import gzip
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.exceptions import RawStoreError
from app.ingestion.storage.raw_store import RawStore


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeS3:
    """The two boto3 client calls RawStore uses, backed by a dict."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, Bucket, Key):
        if self.error:
            raise self.error
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        return {"Body": FakeBody(self.objects[Key])}


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def store(s3):
    return RawStore("bucket", prefix="tenrai", client=s3)


def test_the_key_is_derived_from_the_resource_and_the_mal_id(store):
    assert store.key_for(21, Resource.DETAILS) == "tenrai/details/21.json.gz"
    assert store.key_for(5, "episodes") == "tenrai/episodes/5.json.gz"


def test_the_prefix_slashes_are_ignored():
    store = RawStore("bucket", prefix="/raw/tenrai/", client=FakeS3())

    assert store.key_for(1, Resource.REVIEWS) == "raw/tenrai/reviews/1.json.gz"


def test_a_missing_bucket_is_refused():
    with pytest.raises(ValueError, match="RAW_S3_BUCKET"):
        RawStore(None)


async def test_put_stores_gzipped_json_and_returns_its_size(store, s3):
    payload = {"data": {"mal_id": 1, "title": "x" * 500}}

    size = await store.put(1, Resource.DETAILS, payload)

    body = s3.objects["tenrai/details/1.json.gz"]
    assert size == len(body)
    assert size < len(json.dumps(payload))  # it really is compressed
    assert json.loads(gzip.decompress(body)) == payload
    assert s3.calls[0]["Bucket"] == "bucket"


async def test_get_returns_what_put_stored_including_a_list_of_pages(store):
    pages = [{"page": 1}, {"page": 2}]
    await store.put(7, Resource.EPISODES, pages)

    assert await store.get(7, Resource.EPISODES) == pages


async def test_put_overwrites_an_earlier_object(store):
    await store.put(1, Resource.DETAILS, {"v": 1})
    await store.put(1, Resource.DETAILS, {"v": 2})

    assert await store.get(1, Resource.DETAILS) == {"v": 2}


async def test_a_failing_put_raises_raw_store_error(store, s3):
    s3.error = EndpointConnectionError(endpoint_url="https://s3")

    with pytest.raises(RawStoreError, match="put tenrai/details/1.json.gz"):
        await store.put(1, Resource.DETAILS, {})


async def test_an_access_denied_put_raises_raw_store_error(store, s3):
    s3.error = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "PutObject")

    with pytest.raises(RawStoreError):
        await store.put(1, Resource.DETAILS, {})


async def test_getting_a_missing_object_raises_raw_store_error(store):
    with pytest.raises(RawStoreError, match="get tenrai/details/9.json.gz"):
        await store.get(9, Resource.DETAILS)


@pytest.mark.parametrize(
    "stored",
    [b"not gzip at all", gzip.compress(b"{truncated")[:-4], gzip.compress(b"{not json}")],
    ids=["not gzip", "truncated gzip", "gzip of invalid json"],
)
async def test_a_corrupt_object_raises_raw_store_error_not_a_bare_exception(store, s3, stored):
    s3.objects["tenrai/details/5.json.gz"] = stored

    with pytest.raises(RawStoreError, match="tenrai/details/5.json.gz"):
        await store.get(5, Resource.DETAILS)


def test_the_connection_pool_is_large_enough_for_the_clean_jobs_concurrency():
    from app.ingestion.jobs.clean_raw_job import RAW_STORE_CONCURRENCY
    from app.ingestion.storage.raw_store import RawStore as Real

    client = Real("bucket", region="eu-west-2")._client

    assert client.meta.config.max_pool_connections >= RAW_STORE_CONCURRENCY
