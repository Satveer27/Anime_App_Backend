from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import OperationalError

from app.ingestion.exceptions import (
    CorruptDownloadError,
    NetworkError,
    RawStoreError,
    TransientError,
)
from app.ingestion.run_scripts import imdb_download as script


class FakeAdapter:
    closed = False

    async def aclose(self):
        FakeAdapter.closed = True


@pytest.fixture
def wired(monkeypatch):
    """The script with its store, adapter, session and job replaced; set `outcome` per test."""
    state = {"outcome": {"status": "archived", "files": {}}}

    class FakeJob:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            if isinstance(state["outcome"], BaseException):
                raise state["outcome"]
            return state["outcome"]

    @asynccontextmanager
    async def fake_session():
        yield object()

    FakeAdapter.closed = False
    monkeypatch.setattr(script.ImdbStore, "from_settings", classmethod(lambda cls: object()))
    monkeypatch.setattr(script, "ImdbAdapter", FakeAdapter)
    monkeypatch.setattr(script, "ImdbDownloadJob", FakeJob)
    monkeypatch.setattr(script, "asyncSessionMaker", fake_session)
    return state


async def test_archived_exits_zero(wired):
    assert await script.main() == script.EXIT_OK
    assert FakeAdapter.closed


async def test_skipped_exits_99_so_airflow_skips_the_task(wired):
    wired["outcome"] = {"status": "skipped", "files": {}}

    assert await script.main() == 99


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NetworkError("down"), script.EXIT_IMDB_DOWN),
        (TransientError("503"), script.EXIT_IMDB_DOWN),
        (CorruptDownloadError("bad gzip"), script.EXIT_NEEDS_ATTENTION),
        (OperationalError("select 1", {}, Exception("x")), script.EXIT_DATABASE),
        (RawStoreError("put failed"), script.EXIT_STORAGE),
        (RuntimeError("bug"), script.EXIT_UNEXPECTED),
    ],
)
async def test_errors_map_to_their_exit_codes_and_close_the_adapter(wired, error, code):
    wired["outcome"] = error

    assert await script.main() == code
    assert FakeAdapter.closed


async def test_a_missing_bucket_exits_with_bad_configuration(wired, monkeypatch):
    def no_bucket(cls):
        raise ValueError("RAW_S3_BUCKET is not set")

    monkeypatch.setattr(script.ImdbStore, "from_settings", classmethod(no_bucket))

    assert await script.main() == script.EXIT_BAD_CONFIGURATION
