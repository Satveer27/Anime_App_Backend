import threading
from unittest.mock import MagicMock

import pytest
from kombu.exceptions import OperationalError

from app.core.workers.enqueue import enqueue
from app.exceptions import ServiceUnavailableError


def make_task(side_effect=None):
    task = MagicMock()
    task.name = "some_task"
    task.delay.side_effect = side_effect
    return task


async def test_delay_runs_off_the_event_loop_thread():
    seen: list[int] = []
    task = make_task(side_effect=lambda *a: seen.append(threading.get_ident()))

    await enqueue(task, "to@example.com", "code")

    assert seen and seen[0] != threading.get_ident()
    task.delay.assert_called_once_with("to@example.com", "code")


async def test_broker_failure_becomes_503():
    task = make_task(side_effect=OperationalError("broker down"))

    with pytest.raises(ServiceUnavailableError):
        await enqueue(task, "to@example.com", "code")


async def test_other_errors_are_not_swallowed():
    task = make_task(side_effect=ValueError("bug"))

    with pytest.raises(ValueError):
        await enqueue(task, "to@example.com", "code")


def test_task_results_are_ignored_so_delay_never_touches_the_result_backend():
    from app.core.workers.celery_worker import celery_app

    assert celery_app.conf.task_ignore_result is True
