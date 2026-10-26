from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.exceptions import (
    AccessDeniedError,
    CircuitOpenError,
    RawStoreError,
    TransientError,
)
from app.ingestion.run_scripts import refresh
from app.ingestion.run_scripts.one_pass import (
    EXIT_BAD_ARGUMENTS,
    EXIT_DATABASE,
    EXIT_NEEDS_ATTENTION,
    EXIT_OK,
    EXIT_STORAGE,
    EXIT_TENRAI_DOWN,
)


def test_a_tier_is_required_and_the_stage_defaults_to_all(capsys):
    with pytest.raises(SystemExit) as exit_info:
        refresh.read_args([])
    assert exit_info.value.code == 2

    args = refresh.read_args(["--tier", "weekly"])
    assert (args.tier, args.stage) == ("weekly", "all")


def test_unknown_tiers_and_stages_are_refused():
    for bad in (["--tier", "daily"], ["--tier", "weekly", "--stage", "nope"]):
        with pytest.raises(SystemExit):
            refresh.read_args(bad)


# --- main(): the jobs are replaced by fakes that record what they were asked to do ------------


class Recorder:
    """Collects the calls the three jobs and the adapter receive, in order."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.raise_in: dict[str, Exception] = {}
        self.adapter_closed = False

    def step(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.raise_in:
            raise self.raise_in[name]
        return {"done": True}


@pytest.fixture
def rec(monkeypatch):
    recorder = Recorder()

    class FakeDiscovery:
        def __init__(self, adapter, task_repository, metrics, catalogue_repository):
            recorder.calls.append(("discovery_built", (catalogue_repository,), {}))

        async def run_job_weekly(self):
            return recorder.step("run_job_weekly")

        async def run_job_monthly(self):
            return recorder.step("run_job_monthly")

    class FakeFetch:
        def __init__(self, *args):
            recorder.calls.append(("fetch_built", args[-1:], {}))

        async def fetch_from_tasks(self):
            return recorder.step("fetch_from_tasks")

    class FakeClean:
        def __init__(self, *args):
            recorder.calls.append(("clean_built", args[-1:], {}))

        async def full_clean_loop(self, **kwargs):
            return recorder.step("full_clean_loop", **kwargs)

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        async def aclose(self):
            recorder.adapter_closed = True

    class FakeMetrics:
        def __init__(self, session):
            pass

        async def latest_run(self, job):
            return None

    @asynccontextmanager
    async def session_maker():
        yield object()

    monkeypatch.setattr(refresh, "DiscoveryJob", FakeDiscovery)
    monkeypatch.setattr(refresh, "FetchAnimeJob", FakeFetch)
    monkeypatch.setattr(refresh, "CleanAnimeJob", FakeClean)
    monkeypatch.setattr(refresh, "TenraiAdapter", FakeAdapter)
    monkeypatch.setattr(refresh, "MetricsRepository", FakeMetrics)
    monkeypatch.setattr(refresh, "asyncSessionMaker", session_maker)
    monkeypatch.setattr(refresh, "_cancel_on_sigterm", lambda: None)
    monkeypatch.setattr(refresh.RawStore, "from_settings", classmethod(lambda cls: "store"))
    return recorder


def names(rec):
    return [name for name, _, _ in rec.calls if not name.endswith("_built")]


async def test_weekly_runs_the_weekly_discovery_then_fetch_then_an_incremental_clean(rec):
    code = await refresh.main(refresh.read_args(["--tier", "weekly"]))

    assert code == EXIT_OK
    assert names(rec) == ["run_job_weekly", "fetch_from_tasks", "full_clean_loop"]
    (clean_call,) = [c for c in rec.calls if c[0] == "full_clean_loop"]
    assert clean_call[2] == {"after": None}  # no full_pass: only what was just fetched
    assert rec.adapter_closed


async def test_monthly_runs_the_monthly_discovery(rec):
    code = await refresh.main(refresh.read_args(["--tier", "monthly"]))

    assert code == EXIT_OK
    assert names(rec) == ["run_job_monthly", "fetch_from_tasks", "full_clean_loop"]


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("discovery", ["run_job_weekly"]),
        ("fetch", ["fetch_from_tasks"]),
        ("clean", ["full_clean_loop"]),
    ],
)
async def test_one_stage_runs_only_that_stage(rec, stage, expected):
    await refresh.main(refresh.read_args(["--tier", "weekly", "--stage", stage]))

    assert names(rec) == expected


async def test_discovery_alone_does_not_need_an_s3_bucket(rec, monkeypatch):
    def no_bucket(cls):
        raise ValueError("RAW_S3_BUCKET is not set")

    monkeypatch.setattr(refresh.RawStore, "from_settings", classmethod(no_bucket))

    code = await refresh.main(refresh.read_args(["--tier", "weekly", "--stage", "discovery"]))

    assert code == EXIT_OK


@pytest.mark.parametrize("stage", ["all", "fetch", "clean"])
async def test_a_missing_bucket_stops_before_any_work(rec, monkeypatch, stage):
    def no_bucket(cls):
        raise ValueError("RAW_S3_BUCKET is not set")

    monkeypatch.setattr(refresh.RawStore, "from_settings", classmethod(no_bucket))

    code = await refresh.main(refresh.read_args(["--tier", "weekly", "--stage", stage]))

    assert code == EXIT_BAD_ARGUMENTS
    assert rec.calls == []


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (CircuitOpenError(30), EXIT_TENRAI_DOWN),
        (TransientError("503", status_code=503), EXIT_TENRAI_DOWN),
        (AccessDeniedError("403", status_code=403), EXIT_NEEDS_ATTENTION),
        (OperationalError("SELECT", {}, Exception("gone")), EXIT_DATABASE),
        (RawStoreError("put failed"), EXIT_STORAGE),
    ],
)
async def test_failures_map_to_the_exit_codes_and_the_adapter_is_still_closed(
    rec, error, expected_code
):
    rec.raise_in["fetch_from_tasks"] = error

    code = await refresh.main(refresh.read_args(["--tier", "weekly", "--stage", "fetch"]))

    assert code == expected_code
    assert rec.adapter_closed


async def test_a_failed_stage_stops_the_later_ones(rec):
    rec.raise_in["run_job_weekly"] = CircuitOpenError(30)

    code = await refresh.main(refresh.read_args(["--tier", "weekly"]))

    assert code == EXIT_TENRAI_DOWN
    assert names(rec) == ["run_job_weekly"]  # fetch and clean never started


# --- the clean stage's resume position --------------------------------------------------------


class Metrics:
    def __init__(self, run):
        self.run = run

    async def latest_run(self, job):
        return self.run


def clean_run(status, resume_after=None, since="2026-10-22T20:50:28", age=timedelta(minutes=10)):
    return SimpleNamespace(
        status=status,
        summary={"resume_after": resume_after, "since": since},
        started_at=datetime.now(UTC) - age,
    )


async def test_a_recent_failed_incremental_clean_run_gives_its_cursor():
    run = clean_run(RunStatus.FAILED, resume_after=[9, "reviews"])

    assert await refresh.clean_resume_cursor(Metrics(run)) == (9, Resource.REVIEWS)


@pytest.mark.parametrize(
    "run",
    [
        None,
        clean_run(RunStatus.SUCCEEDED, resume_after=[9, "reviews"]),
        clean_run(RunStatus.FAILED, resume_after=None),
        clean_run(RunStatus.RUNNING),
        SimpleNamespace(status=RunStatus.RUNNING, summary=None, started_at=datetime.now(UTC)),
    ],
    ids=["no run", "succeeded", "no cursor", "running", "running without summary"],
)
async def test_without_a_failed_clean_run_there_is_nothing_to_resume(run):
    assert await refresh.clean_resume_cursor(Metrics(run)) is None


async def test_a_failed_full_pass_is_not_continued_by_an_incremental_refresh():
    """one_pass cleans everything (no `since`). Resuming it after its cursor with this week's
    cut-off would skip the refreshed rows before the cursor for good."""
    run = clean_run(RunStatus.FAILED, resume_after=[30000, "details"], since=None)

    assert await refresh.clean_resume_cursor(Metrics(run)) is None


async def test_a_clean_failure_from_days_ago_is_not_continued():
    run = clean_run(RunStatus.FAILED, resume_after=[9, "reviews"], age=timedelta(days=3))

    assert await refresh.clean_resume_cursor(Metrics(run)) is None
