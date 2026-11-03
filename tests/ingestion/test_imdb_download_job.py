import gzip
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ingestion.adapters.imdb import DownloadedFile, FileInfo
from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.enums.snapshot_status_enum import SnapshotStatus
from app.ingestion.exceptions import CorruptDownloadError, NetworkError, RawStoreError
from app.ingestion.jobs.imdb.download_file_job import ImdbDownloadJob
from app.ingestion.models import ImdbSnapshot
from app.ingestion.repository.imdb_snapshot_repository import ImdbSnapshotRepository
from app.ingestion.storage.imdb_store import ImdbStore

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 11, 9, 3, 0, 0, 123456, tzinfo=UTC)
KEYS = {
    ImdbFile.BASICS: "imdb/2026-11-09T030000Z/title.basics.tsv.gz",
    ImdbFile.EPISODE: "imdb/2026-11-09T030000Z/title.episode.tsv.gz",
    ImdbFile.RATINGS: "imdb/2026-11-09T030000Z/title.ratings.tsv.gz",
}


class FakeAdapter:
    """Stands in for ImdbAdapter: serves a version per file and writes real gzip files."""

    def __init__(self, version: str = "v1") -> None:
        self.version = version
        self.heads: list[ImdbFile] = []
        self.downloads: list[ImdbFile] = []
        self.fail_head: Exception | None = None
        self.fail_download: dict[ImdbFile, Exception] = {}

    def _etag(self, file: ImdbFile) -> str:
        return f'"{file.name}-{self.version}"'

    async def head(self, file: ImdbFile) -> FileInfo:
        self.heads.append(file)
        if self.fail_head:
            raise self.fail_head
        return FileInfo(self._etag(file), 10)

    async def download(self, file: ImdbFile, dest_dir) -> DownloadedFile:
        self.downloads.append(file)
        if file in self.fail_download:
            raise self.fail_download[file]
        body = gzip.compress(f"{file.value} {self.version}".encode())
        path = dest_dir / file.filename
        path.write_bytes(body)
        return DownloadedFile(file, path, self._etag(file), len(body), 0.5)


class FakeStore:
    """Stands in for ImdbStore: records each upload with the bytes found on disk at that time."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail: Exception | None = None

    async def put(self, finished_at, file, path) -> str:
        if self.fail:
            raise self.fail
        key = ImdbStore.key_for(finished_at, file)
        self.objects[key] = path.read_bytes()
        return key


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def repo(pg_session):
    return ImdbSnapshotRepository(pg_session)


@pytest.fixture
def make_job(adapter, store, repo, tmp_path):
    def build() -> ImdbDownloadJob:
        return ImdbDownloadJob(adapter, store, repo, temp_root=tmp_path, clock=lambda: NOW)

    return build


async def snapshots(session) -> list[ImdbSnapshot]:
    session.expire_all()
    return list((await session.execute(select(ImdbSnapshot).order_by(ImdbSnapshot.id))).scalars())


async def test_new_files_are_uploaded_under_the_finished_at_keys_and_archived(
    make_job, adapter, store, pg_session, tmp_path
):
    summary = await make_job().run()

    assert sorted(store.objects) == sorted(KEYS.values())
    assert summary["status"] == "archived"
    assert set(summary["files"]) == {"title.basics", "title.episode", "title.ratings"}
    [snapshot] = await snapshots(pg_session)
    assert snapshot.status == SnapshotStatus.ARCHIVED
    assert snapshot.finished_at == NOW.replace(microsecond=0)
    assert snapshot.basics_etag == '"BASICS-v1"'
    assert snapshot.episode_etag == '"EPISODE-v1"'
    assert snapshot.basics_size_bytes == len(store.objects[KEYS[ImdbFile.BASICS]])
    assert list(tmp_path.iterdir()) == []


async def test_unchanged_etags_are_skipped_without_download_or_upload(
    make_job, adapter, store, pg_session
):
    await make_job().run()
    store.objects.clear()
    adapter.downloads.clear()

    summary = await make_job().run()

    assert summary["status"] == "skipped"
    assert adapter.downloads == []
    assert store.objects == {}
    statuses = [s.status for s in await snapshots(pg_session)]
    assert statuses == [SnapshotStatus.ARCHIVED, SnapshotStatus.SKIPPED]


async def test_one_changed_file_is_enough_to_download_everything(
    make_job, adapter, store, pg_session
):
    await make_job().run()
    adapter.downloads.clear()
    original_etag = adapter._etag
    adapter._etag = lambda file: '"BASICS-new"' if file == ImdbFile.BASICS else original_etag(file)

    summary = await make_job().run()

    assert summary["status"] == "archived"
    assert sorted(adapter.downloads) == sorted(ImdbFile)


async def test_a_corrupt_download_fails_the_run_and_uploads_nothing(
    make_job, adapter, store, pg_session, tmp_path
):
    adapter.fail_download[ImdbFile.EPISODE] = CorruptDownloadError("bad gzip")

    with pytest.raises(CorruptDownloadError):
        await make_job().run()

    assert store.objects == {}
    [snapshot] = await snapshots(pg_session)
    assert snapshot.status == SnapshotStatus.FAILED
    assert snapshot.basics_etag is None
    assert list(tmp_path.iterdir()) == []


async def test_an_s3_failure_fails_the_run_and_cleans_the_temp_files(
    make_job, store, pg_session, tmp_path
):
    store.fail = RawStoreError("put failed")

    with pytest.raises(RawStoreError):
        await make_job().run()

    [snapshot] = await snapshots(pg_session)
    assert snapshot.status == SnapshotStatus.FAILED
    assert list(tmp_path.iterdir()) == []


async def test_a_failed_head_fails_the_run(make_job, adapter, store, pg_session):
    adapter.fail_head = NetworkError("down")

    with pytest.raises(NetworkError):
        await make_job().run()

    [snapshot] = await snapshots(pg_session)
    assert snapshot.status == SnapshotStatus.FAILED
    assert adapter.downloads == []


async def test_a_retry_is_a_new_row_and_a_failed_run_is_never_the_skip_baseline(
    make_job, adapter, store, pg_session
):
    store.fail = RawStoreError("put failed")
    with pytest.raises(RawStoreError):
        await make_job().run()
    store.fail = None

    summary = await make_job().run()

    assert summary["status"] == "archived"  # not skipped: the failed run archived nothing
    statuses = [s.status for s in await snapshots(pg_session)]
    assert statuses == [SnapshotStatus.FAILED, SnapshotStatus.ARCHIVED]
