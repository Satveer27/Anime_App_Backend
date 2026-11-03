from datetime import UTC, datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.exceptions import RawStoreError
from app.ingestion.storage.imdb_store import ImdbStore

FETCHED_AT = datetime(2026, 11, 9, 3, 0, 0, tzinfo=UTC)


class FakeS3:
    """The one boto3 client call ImdbStore uses, backed by a dict."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.calls.append({"Bucket": Bucket, "Key": Key, "ExtraArgs": ExtraArgs})
        if self.error:
            raise self.error
        with open(Filename, "rb") as f:
            self.objects[Key] = f.read()


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def store(s3):
    return ImdbStore("bucket", client=s3)


def test_the_key_is_built_from_the_time_and_the_file():
    assert (
        ImdbStore.key_for(FETCHED_AT, ImdbFile.BASICS)
        == "imdb/2026-11-09T030000Z/title.basics.tsv.gz"
    )
    assert (
        ImdbStore.key_for(FETCHED_AT, ImdbFile.EPISODE)
        == "imdb/2026-11-09T030000Z/title.episode.tsv.gz"
    )
    assert (
        ImdbStore.key_for(FETCHED_AT, ImdbFile.RATINGS)
        == "imdb/2026-11-09T030000Z/title.ratings.tsv.gz"
    )


def test_the_key_is_always_in_utc():
    plus_two = timezone(timedelta(hours=2))

    key = ImdbStore.key_for(datetime(2026, 11, 9, 5, 0, 0, tzinfo=plus_two), ImdbFile.RATINGS)

    assert key == "imdb/2026-11-09T030000Z/title.ratings.tsv.gz"


def test_a_time_without_a_timezone_is_refused():
    with pytest.raises(ValueError):
        ImdbStore.key_for(datetime(2026, 11, 9, 3, 0, 0), ImdbFile.BASICS)


def test_it_refuses_to_start_without_a_bucket():
    with pytest.raises(ValueError):
        ImdbStore(None, client=FakeS3())


async def test_put_uploads_the_unaltered_file_under_the_imdb_prefix(store, s3, tmp_path):
    path = tmp_path / "title.ratings.tsv.gz"
    path.write_bytes(b"\x1f\x8b raw gzip bytes")

    key = await store.put(FETCHED_AT, ImdbFile.RATINGS, path)

    assert key == "imdb/2026-11-09T030000Z/title.ratings.tsv.gz"
    assert s3.objects[key] == b"\x1f\x8b raw gzip bytes"
    assert s3.calls[0]["Bucket"] == "bucket"
    assert s3.calls[0]["ExtraArgs"] == {"ContentType": "application/gzip"}


async def test_putting_the_same_key_again_overwrites_it(store, s3, tmp_path):
    path = tmp_path / "f.gz"
    path.write_bytes(b"first")
    await store.put(FETCHED_AT, ImdbFile.BASICS, path)
    path.write_bytes(b"second")

    await store.put(FETCHED_AT, ImdbFile.BASICS, path)

    assert list(s3.objects.values()) == [b"second"]


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "PutObject"),
        EndpointConnectionError(endpoint_url="https://s3.test"),
    ],
)
async def test_an_s3_failure_raises_raw_store_error(store, s3, tmp_path, error):
    path = tmp_path / "f.gz"
    path.write_bytes(b"x")
    s3.error = error

    with pytest.raises(RawStoreError):
        await store.put(FETCHED_AT, ImdbFile.BASICS, path)
