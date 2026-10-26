from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from app.ingestion.enums.resource_enum import Resource
from app.ingestion.enums.run_status_enum import RunStatus
from app.ingestion.repository.metrics_repository import MetricsRepository
from app.ingestion.run_scripts.one_pass import (
    DISCOVERY_JOB,
    REFRESH_JOB,
    discovery_job_name,
    read_args,
    saved_positions,
)


def test_the_defaults_resume_and_do_not_refresh():
    args = read_args([])

    assert (args.stage, args.fresh, args.refresh) == ("all", False, False)
    assert discovery_job_name(args) == DISCOVERY_JOB


@pytest.mark.parametrize("stage", ["all", "discovery"])
def test_refresh_is_accepted_for_the_stages_that_run_discovery(stage):
    args = read_args(["--stage", stage, "--refresh"])

    assert args.refresh is True
    assert discovery_job_name(args) == REFRESH_JOB


@pytest.mark.parametrize("stage", ["fetch", "clean"])
def test_refresh_is_refused_for_stages_that_do_not_run_discovery(stage, capsys):
    with pytest.raises(SystemExit) as exit_info:
        read_args(["--stage", stage, "--refresh"])

    assert exit_info.value.code == 2
    assert "--refresh only applies to the discovery stage" in capsys.readouterr().err


def test_fresh_and_refresh_are_independent():
    args = read_args(["--fresh", "--refresh"])

    assert (args.fresh, args.refresh) == (True, True)


def run(status, summary):
    return SimpleNamespace(status=status, summary=summary)


def metrics_with(runs: dict):
    metrics = create_autospec(MetricsRepository, instance=True)

    async def latest_run(job):
        return runs.get(job)

    metrics.latest_run.side_effect = latest_run
    return metrics


async def test_a_refresh_resumes_from_its_own_failed_run_not_the_first_passes():
    metrics = metrics_with(
        {
            DISCOVERY_JOB: run(RunStatus.FAILED, {"resume_page": 40}),
            REFRESH_JOB: run(RunStatus.FAILED, {"resume_page": 7}),
        }
    )

    page, _ = await saved_positions(metrics, REFRESH_JOB)

    assert page == 7


async def test_the_first_pass_ignores_a_failed_refresh_run():
    metrics = metrics_with({REFRESH_JOB: run(RunStatus.FAILED, {"resume_page": 7})})

    page, _ = await saved_positions(metrics)

    assert page is None


async def test_a_succeeded_discovery_run_leaves_nothing_to_resume():
    metrics = metrics_with({REFRESH_JOB: run(RunStatus.SUCCEEDED, {"resume_page": None})})

    assert (await saved_positions(metrics, REFRESH_JOB))[0] is None


async def test_the_clean_position_is_read_whichever_discovery_job_is_used():
    metrics = metrics_with({"clean_anime": run(RunStatus.FAILED, {"resume_after": [9, "reviews"]})})

    _, after = await saved_positions(metrics, REFRESH_JOB)

    assert after == (9, Resource.REVIEWS)
